"""Compare ordinary TradingView chart exports with source-exact visible plots.

No title aliases: unrelated indicators (including a column named `direction`)
are not treated as observations of this Pine source. Missing internal metrics
keep full parity UNVERIFIED even when every observed plot matches.
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from backend.engine.syntax import load_program
from backend.engine.interpreter import qualified, tf_seconds
from backend.engine.parameters import validate_parameters, required_history, parameter_hash
from backend.engine.runtime import PINE_HASH, ENGINE_VERSION
from backend.engine.values import encode
from backend.marketdata import BybitAdapter, BinanceBtcContextAdapter
from .compare import number, percentile


@lru_cache(maxsize=1)
def plot_specs():
    """Read title and gated series expression from the pinned original AST."""
    result = {}
    for st in load_program().statements:
        call = st.expr
        if not call or call.kind != 'call' or qualified(call.args[0]) not in ('plot', 'plotshape'):
            continue
        positional = [n for n in call.args[1:] if n.kind != 'kw']
        kw = {n.value: n.args[0] for n in call.args[1:] if n.kind == 'kw'}
        title = kw.get('title', positional[1] if len(positional) > 1 else None)
        if title is None or title.kind != 'literal':
            raise ValueError(f'Nonliteral plot title at source line {st.line}')
        absolute = qualified(kw.get('location')) == 'location.absolute' if 'location' in kw else False
        kind = 'signal' if qualified(call.args[0]) == 'plotshape' and not absolute else 'price'
        if str(title.value).startswith('EMA '): kind = 'indicator'
        result[title.value] = {'expr': positional[0], 'line': st.line, 'kind': kind}
    return result


def plot_values(runtime):
    # These source expressions are pure scalar reads/conditions. Fail closed if
    # a future source introduces history or a mutating/stateful call here.
    def safe(node):
        if node.kind == 'history' or node.kind == 'call' and qualified(node.args[0]) != 'na':
            raise ValueError('Plot requires capture at its original execution point')
        for child in node.args: safe(child)
    result = {}
    for title, spec in plot_specs().items():
        safe(spec['expr'])
        value = encode(runtime.eval(spec['expr']))
        result[title] = int(value) if isinstance(value, bool) else value
    return result


def read_export(path):
    path = Path(path)
    match = re.fullmatch(r'BYBIT_([A-Z0-9]+)\.P, ([0-9]+)_[^.]+\.csv', path.name)
    if not match: raise ValueError(f'Unsupported chart identity filename: {path.name}')
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if len(reader.fieldnames or []) != len(set(reader.fieldnames or [])):
            raise ValueError('Duplicate CSV columns are ambiguous')
        rows = list(reader)
    if not rows: raise ValueError('Empty chart export')
    symbol, tf = match.groups()
    step = tf_seconds(tf)*1000
    previous = None
    for row in rows:
        stamp = number(row.get('time'))
        if stamp is None or stamp != int(stamp): raise ValueError('Invalid export time')
        start = int(stamp)*1000
        if start % step or previous is not None and start != previous+step:
            raise ValueError('Duplicate, unordered, unaligned or missing CSV candle')
        previous = start
        for field in ('open', 'high', 'low', 'close', 'Volume'):
            if number(row.get(field)) is None: raise ValueError(f'Missing finite {field}')
        for title in plot_specs().keys() & row.keys():
            if row[title].strip() and number(row[title]) is None:
                raise ValueError(f'Invalid plot value: {title}')
    return symbol, tf, rows


def compare_plots(reference, actual, tick_size):
    index = {}
    for row in actual:
        stamp = row['bar_start']
        if stamp in index: raise ValueError('Duplicate Python candle')
        index[stamp] = row
    missing = sum(int(r['time'])*1000 not in index for r in reference)
    result = []
    for title, spec in plot_specs().items():
        if not any(title in row for row in reference): continue
        errors = []; bad = []; both_na = na_mismatch = missing_columns = 0
        pine_events = python_events = 0
        for ref in reference:
            start = int(ref['time'])*1000
            row = index.get(start)
            if row is None: continue
            if title not in ref or title not in row.get('source_plots', {}):
                missing_columns += 1; continue
            a, b = number(ref[title]), number(row['source_plots'][title])
            if spec['kind'] == 'signal':
                pine_events += int(a is not None and a != 0)
                python_events += int(b is not None and b != 0)
            if a is None and b is None:
                both_na += 1; continue
            if a is None or b is None:
                na_mismatch += 1; mismatch = True
            else:
                delta = abs(a-b); errors.append(delta)
                tolerance = 0 if spec['kind'] == 'signal' else abs(a)*.0005 if spec['kind'] == 'indicator' else tick_size*(2 if title in ('Forecast T1', 'Estimated Structural SL', 'LONG T1 label', 'SHORT T1 label') else 1)
                mismatch = delta > tolerance + (tick_size*1e-9 if spec['kind']=='price' else 0)
            if mismatch:
                bad.append({'time': start, 'pine': a, 'python': b})
        observed = len(reference)-missing-missing_columns
        status = 'FAIL' if bad or missing or missing_columns else 'NOT_OBSERVED' if not errors else 'PASS'
        result.append({'metric': title, 'source_line': spec['line'], 'kind': spec['kind'], 'status': status,
                       'observations': observed, 'numeric_pairs': len(errors), 'both_na': both_na,
                       'na_mismatches': na_mismatch, 'missing_columns': missing_columns,
                       'mismatches': len(bad), 'match_percent': 100*(observed-len(bad))/observed if observed and (errors or bad) else None,
                       'error_median': percentile(errors,.5), 'error_p95': percentile(errors,.95),
                       'error_max': max(errors) if errors else None,
                       'pine_events': pine_events if spec['kind']=='signal' else None,
                       'python_events': python_events if spec['kind']=='signal' else None,
                       'positive_event_coverage': pine_events > 0 if spec['kind']=='signal' else None,
                       'first_mismatches': bad[:5]})
    return {'status': 'UNVERIFIED', 'observed_status': 'FAIL' if missing or any(r['status']=='FAIL' for r in result) else 'PASS' if any(r['status']=='PASS' for r in result) else 'NOT_OBSERVED',
            'reference_records': len(reference), 'python_records': len(actual), 'missing_records': missing,
            'metrics': [r for r in result if r['kind']!='signal'], 'signals': [r for r in result if r['kind']=='signal']}


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.partial')
    temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    temporary.replace(path)


async def capture_export(path, output, parameters=None, warmup=None):
    from backend.worker import context_requirements
    symbol, tf, rows = read_export(path)
    parameters = validate_parameters(parameters or {})
    warmup = required_history(parameters, tf) if warmup is None else warmup
    if warmup < 0: raise ValueError('warmup cannot be negative')
    start = int(rows[0]['time'])*1000; end = int(rows[-1]['time'])*1000+tf_seconds(tf)*1000
    bybit = BybitAdapter(); btc = BinanceBtcContextAdapter()
    try:
        instruments = {i.symbol:i for i in await bybit.instruments()}
        tick = instruments[symbol].tick_size
        chart = await bybit.backfill(symbol, tf, len(rows)+warmup, end=end-1)
        chart = [b for b in chart if b.confirmed and b.end <= end]
        if not chart or chart[0].start > start: raise ValueError('Insufficient chart history')
        native = {b.start:b.to_dict() for b in chart}
        # The final exported candle may still have been open; filesystem mtime
        # is not an authoritative capture timestamp. Exclude the final row in
        # every export conservatively and retain the reason in provenance.
        reference = rows[:-1]
        feed = []
        for ref in rows:
            stamp = int(ref['time'])*1000
            if stamp not in native: raise ValueError(f'Missing native candle {stamp}')
            differences = {f: {'tradingview':number(ref[c]), 'bybit':native[stamp][f]} for c,f in [('open','open'),('high','high'),('low','low'),('close','close'),('Volume','volume')] if not math.isclose(number(ref[c]),native[stamp][f],rel_tol=1e-12,abs_tol=tick*1e-8)}
            if differences: feed.append({'time': stamp, 'excluded_last_row': ref is rows[-1], 'differences': differences})
        contexts = {}; own, bitcoin = context_requirements(parameters,[tf])
        span = end-chart[0].start
        for exchange, tfs, adapter, ticker in [('BYBIT',own,bybit,symbol),('BINANCE',bitcoin,btc,'BTCUSDT')]:
            for context_tf in sorted(tfs, key=tf_seconds):
                if context_tf == '30S': raise ValueError('Historical 30S requires recorded trades')
                depth = max(required_history(parameters,context_tf),math.ceil(span/(tf_seconds(context_tf)*1000))+500)
                bars = await adapter.backfill(ticker,context_tf,depth,end=end-1)
                contexts[f'{exchange}:{ticker}.P|{context_tf}'] = [b.to_dict() for b in bars if b.confirmed and b.end<=end]
        # Use the exported OHLCV on the comparison window, exchange history
        # before it. Feed mismatches remain separately visible in the report.
        export_bars = {int(r['time'])*1000: r for r in reference}
        chart_rows = []
        for bar in chart:
            if bar.start >= int(rows[-1]['time'])*1000: break
            value = bar.to_dict()
            if bar.start in export_bars:
                ref = export_bars[bar.start]
                value.update({f:number(ref[c]) for c,f in [('open','open'),('high','high'),('low','low'),('close','close'),('Volume','volume')]})
                value['turnover'] = None # not present in CSV; never infer native turnover
            chart_rows.append(value)
        fixture = {'symbol':symbol,'timeframe':tf,'tick_size':tick,'parameters':parameters,'bars':chart_rows,'contexts':contexts,
                   'provenance':{'kind':'tradingview_chart_with_native_exchange_warmup_and_contexts','file':str(path),'csv_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                   'pine_source_hash':PINE_HASH,'engine_version':ENGINE_VERSION,'parameter_set_id':parameter_hash(parameters),'parameters_confirmed':False,
                   'captured_at':int(time.time()*1000),'reference_records':len(rows),'compared_records':len(reference),
                   'warmup_bars':sum(b['start']<start for b in chart_rows),'history_start':chart_rows[0]['start'],
                   'excluded_last_time':int(rows[-1]['time'])*1000,'excluded_last_reason':'Potential open candle; export timestamp/confirmed flag absent',
                   'feed_differences':feed,'unmapped_columns':sorted(set(rows[0])-set(plot_specs())-{'time','open','high','low','close','Volume'})}}
        dump(output, fixture)
        return fixture
    finally:
        await bybit.close(); await btc.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path('tradingview_data'))
    parser.add_argument('--output-dir',type=Path,default=Path('artifacts/local/tradingview'))
    parser.add_argument('--capture-only',action='store_true')
    parser.add_argument('--parameters',type=Path)
    parser.add_argument('--warmup',type=int)
    parser.add_argument('--symbols',nargs='+',help='Optional subset for diagnostics')
    parser.add_argument('--compare-only',action='store_true',help='Reuse saved Python observations')
    parser.add_argument('--report',type=Path,help='Publish an aggregate report after all selected datasets finish')
    args=parser.parse_args()
    parameters=json.loads(args.parameters.read_text()) if args.parameters else {}
    from .replay import replay_fixture
    summaries=[]; reports=[]
    for path in sorted(args.directory.glob('*.csv')):
        symbol,tf,rows=read_export(path)
        if args.symbols and symbol not in args.symbols: continue
        target=args.output_dir/symbol
        fixture_path=target/'fixture.json'
        if fixture_path.exists():
            fixture=json.loads(fixture_path.read_text())
            if fixture['provenance']['csv_sha256']!=hashlib.sha256(path.read_bytes()).hexdigest() or fixture['parameters']!=validate_parameters(parameters) or fixture['provenance']['pine_source_hash']!=PINE_HASH:
                raise ValueError('Cached fixture does not match CSV/parameters; use a new output directory')
            if args.warmup is not None and fixture['provenance']['warmup_bars']!=args.warmup:
                raise ValueError('Cached fixture has another warmup; use a new output directory')
        else:
            print(f'CAPTURE {symbol}',flush=True)
            fixture=asyncio.run(capture_export(path,fixture_path,parameters,args.warmup))
        if args.capture_only: continue
        actual=[]; reference_start=int(rows[0]['time'])*1000
        out=target/'python.jsonl'
        if args.compare_only:
            actual=[json.loads(line) for line in out.read_text().splitlines()]
            fingerprint=hashlib.sha256(json.dumps(fixture,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            if any(row['replay_input_sha256']!=fingerprint or row.get('engine_version')!=ENGINE_VERSION or row.get('pine_source_hash')!=PINE_HASH for row in actual):
                raise ValueError('Saved observations belong to another fixture or engine/source version')
        else:
            print(f'REPLAY {symbol} {len(fixture["bars"])} bars',flush=True)
            with out.with_suffix('.partial').open('w') as handle:
                for i,row in enumerate(replay_fixture(fixture)):
                    if row['bar_start']>=reference_start:
                        compact={k:row[k] for k in ('bar_start','symbol','timeframe','source_plots','data_health','missing_contexts','replay_input_sha256','history_start','parameter_set_id','engine_version','pine_source_hash')}
                        # Keep internal observations for first-divergence diagnostics.
                        compact['metrics']={k:v for k,v in row['metrics'].items() if k!='source_locals'}
                        handle.write(json.dumps(compact,allow_nan=False,separators=(',',':'))+'\n');actual.append(compact)
                    if (i+1)%250==0: print(f'{symbol} {i+1}/{len(fixture["bars"])}',flush=True)
            out.with_suffix('.partial').replace(out)
        report=compare_plots(rows[:-1],actual,fixture['tick_size'])
        report.update(symbol=symbol,timeframe=tf,tick_size=fixture['tick_size'],provenance=fixture['provenance'],
                      engine_version=ENGINE_VERSION,pine_source_hash=PINE_HASH,
                      reasons=['Ordinary chart export does not contain internal scores/FSM or intrabar observations.',
                               'TradingView parameters and loaded history origin are not recorded in CSV.',
                               'Unowned column direction is not the hidden source plot ALERT_DIR.'])
        dump(target/'report.json',report)
        reports.append(report)
        summary={'symbol':symbol,'timeframe':tf,'rows':len(rows),'compared':len(rows)-1,'observed_status':report['observed_status'],
                 'failed_plots':sum(r['status']=='FAIL' for r in report['metrics']+report['signals']),
                 'closed_feed_mismatches':sum(not f['excluded_last_row'] for f in fixture['provenance']['feed_differences']),
                 'report':str(target/'report.json')}
        summaries.append(summary);print(json.dumps(summary),flush=True)
        dump(args.output_dir/'summary.json',{'status':'UNVERIFIED','datasets':summaries})
    if args.report and reports:
        aggregate={'status':'UNVERIFIED','observed_status':'FAIL' if any(r['observed_status']=='FAIL' for r in reports) else 'PASS',
                   'engine_version':ENGINE_VERSION,'pine_source_hash':PINE_HASH,'generated_at':datetime.now(timezone.utc).isoformat(),
                   'reference_records':sum(s['rows'] for s in summaries),'matched_records':sum(s['compared'] for s in summaries),
                   'excluded_potential_open_candles':len(reports),'realtime':False,
                   'reasons':reports[0]['reasons'], 'datasets':[{**s,'report':str(args.report.parent/'tradingview'/f'{s["symbol"]}.json')} for s in summaries],
                   'metrics':[{'symbol':r['symbol'],**metric} for r in reports for metric in r['metrics']],
                   'signals':[{'symbol':r['symbol'],**metric} for r in reports for metric in r['signals']]}
        for r in reports:dump(args.report.parent/'tradingview'/f'{r["symbol"]}.json',r)
        dump(args.report,aggregate)


if __name__=='__main__':main()
