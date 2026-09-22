"""Validate a contexts+metrics CSV and replay with explicitly observed FX inputs."""
import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path

from backend.engine.runtime import PINE_HASH, ENGINE_VERSION
from backend.engine.parameters import validate_parameters
from backend.engine.interpreter import tf_seconds
from backend.marketdata import BybitAdapter, BinanceBtcContextAdapter
from .catalog import METRICS
from .context_probe import FIELDS
from .compare import compare, number
from .native import read_export, dump
from .replay import replay_fixture, normalized_contexts


def inspect_contexts(path):
    symbol, tf, rows = read_export(path)
    required = {'PARITY_'+k for k in METRICS} | {'PARITY_CTX_'+k for k in FIELDS}
    required |= {'PARITY_META_'+g+'_'+k for g in ('contexts','metrics')
                 for k in ('history_start','bar_index','tick_size','confirmed','volume','timeframe_seconds')}
    if required - rows[0].keys(): raise ValueError('Missing context/metrics columns')
    closed = []
    identity = None
    for i, row in enumerate(rows):
        flags = [row['PARITY_META_'+g+'_confirmed'] for g in ('contexts','metrics')]
        if i == len(rows)-1 and '0' in flags: continue
        if flags != ['1','1']: raise ValueError('Unconfirmed row inside history')
        for k in ('history_start','bar_index','tick_size','confirmed','volume','timeframe_seconds'):
            if row['PARITY_META_contexts_'+k] != row['PARITY_META_metrics_'+k]:
                raise ValueError('Context/metrics metadata mismatch: '+k)
        origin = int(row['PARITY_META_contexts_history_start'])
        tick = float(row['PARITY_META_contexts_tick_size'])
        if identity is not None and identity != (origin,tick): raise ValueError('Changed origin/tick')
        identity = origin,tick
        start = int(row['time'])*1000; step=tf_seconds(tf)*1000
        if int(row['PARITY_META_contexts_timeframe_seconds']) != step//1000 or start-origin != int(row['PARITY_META_contexts_bar_index'])*step:
            raise ValueError('Context history/timeframe mismatch')
        if int(row['PARITY_CTX_chart_start']) != start or int(row['PARITY_CTX_chart_end']) != start+step:
            raise ValueError('Context chart timestamps mismatch')
        if row['PARITY_CTX_realtime'] != '0': raise ValueError('Historical replay requires historical reference rows')
        if row['PARITY_CTX_probe_revision'] != '1': raise ValueError('Unknown context probe revision')
        for field in required:
            value=row[field]
            if value.strip() and number(value) is None: raise ValueError('Nonfinite context value: '+field)
        if not math.isfinite(tick) or tick <= 0: raise ValueError('Invalid tick')
        if any(float(row['PARITY_'+k])!=float(row[k]) for k in ('open','high','low','close')) or float(row['Volume'])!=float(row['PARITY_META_contexts_volume']):
            raise ValueError('Chart/metrics OHLCV mismatch')
        closed.append(row)
    if not closed: raise ValueError('No historical closed rows')
    return symbol,tf,rows,closed,*identity


def currency_observations(rows, timeframe):
    # Use the independently exported request result, NOT volume ratios or scores.
    intervals=[]; step=tf_seconds(timeframe)*1000
    for row in rows:
        start=int(row['time'])*1000
        rate=number(row['PARITY_CTX_quote_usd_requested'])
        if intervals and intervals[-1]['expires_at']==start and intervals[-1]['rate']==rate:
            intervals[-1]['expires_at']=start+step
        else: intervals.append(dict(available_at=start,expires_at=start+step,rate=rate))
    return {'USDT|USD':intervals}


def capture_evidence(rows, old_rows=()):
    """Independent observations from the probe, without shifting acceptance rows."""
    def equal(a,b):
        a,b=number(a),number(b)
        return a is not None and b is not None and math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-10)
    old={r['time']:r for r in old_rows if r.get('PARITY_META_metrics_confirmed')=='1'}
    overlaps=[r for r in rows if r['time'] in old]
    distinct=[r for r in overlaps if not equal(r['PARITY_CTX_return_1h_off'],r['PARITY_CTX_return_1h_on'])]
    rates=[number(r['PARITY_CTX_quote_usd_requested']) for r in rows]
    known=[rate for rate in rates if rate is not None]
    products=[abs(float(r['PARITY_CTX_volume_24h_usd'])-float(r['PARITY_CTX_volume_24h_quote'])*float(r['PARITY_CTX_quote_usd_used']))
              for r in rows if all(number(r[k]) is not None for k in ('PARITY_CTX_volume_24h_usd','PARITY_CTX_volume_24h_quote','PARITY_CTX_quote_usd_used'))]
    return dict(status='OBSERVED_CONTEXT_EVIDENCE',rows=len(rows),
                original_matches_off=sum(equal(r['PARITY_CTX_return_1h_original'],r['PARITY_CTX_return_1h_off']) for r in rows),
                metrics_matches_off=sum(equal(r['PARITY_return_1h'],r['PARITY_CTX_return_1h_off']) for r in rows),
                future_off_timestamps=sum(int(r['PARITY_CTX_hour_end_off'])>int(r['PARITY_CTX_chart_end']) for r in rows),
                old_capture_overlap=len(overlaps),old_capture_discriminating_rows=len(distinct),
                old_matches_on_only=sum(equal(old[r['time']]['PARITY_return_1h'],r['PARITY_CTX_return_1h_on']) for r in distinct),
                old_matches_off_only=sum(equal(old[r['time']]['PARITY_return_1h'],r['PARITY_CTX_return_1h_off']) for r in distinct),
                currency_known_rows=len(known),currency_rate_min=min(known) if known else None,currency_rate_max=max(known) if known else None,
                currency_product_pairs=len(products),currency_product_max_error=max(products) if products else None,
                limitations=['The former CSV behaves like lookahead_on on the measured hourly series; its source/settings are not authenticated by this comparison.'])


async def prepare(path, cached, *, parameters_confirmed=False):
    symbol,tf,all_rows,rows,origin,tick=inspect_contexts(path)
    if not symbol.endswith('USDT'): raise ValueError('Only the pinned USDT quote context is supported')
    fixture=json.loads(cached.read_text())
    if (fixture['symbol'],fixture['timeframe'],fixture['bars'][0]['start'])!=(symbol,tf,origin):
        raise ValueError('Cached chart identity/origin mismatch')
    if fixture['parameters']!=validate_parameters({}): raise ValueError('Cached inputs are not defaults')
    if fixture['provenance']['pine_source_hash']!=PINE_HASH: raise ValueError('Cached Pine source mismatch')
    start=int(rows[0]['time'])*1000; step=tf_seconds(tf)*1000; end=int(rows[-1]['time'])*1000+step
    prefix=[b for b in fixture['bars'] if b['start']<start]
    if prefix and prefix[-1]['end']!=start: raise ValueError('Cached warmup does not reach reference')
    fixture['bars']=prefix+[dict(start=int(r['time'])*1000,end=int(r['time'])*1000+step,
                               **{k:float(r['Volume' if k=='volume' else k]) for k in ('open','high','low','close','volume')},confirmed=True)
                            for r in rows]
    bybit,btc=BybitAdapter(),BinanceBtcContextAdapter()
    try:
        for key,stream in fixture['contexts'].items():
            last_end=stream[-1]['end']; context_tf=key.rsplit('|',1)[1]
            if last_end>=end: continue
            adapter,ticker=(btc,'BTCUSDT') if key.startswith('BINANCE:') else (bybit,symbol)
            count=math.ceil((end-last_end)/(tf_seconds(context_tf)*1000))+2
            tail=await adapter.backfill(ticker,context_tf,count,end=end-1)
            stream.extend(b.to_dict() for b in tail if b.confirmed and b.start>=last_end and b.end<=end)
        normalized_contexts(fixture['contexts'])
    finally:
        await bybit.close(); await btc.close()
    fixture['currency_rates']=currency_observations(rows,tf)
    fixture['tick_size']=tick
    previous_provenance=fixture['provenance']
    fixture['provenance']={'kind':'contexts_capture_with_observed_currency_rates',
                           'csv_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'file':str(path),
                           'engine_version':ENGINE_VERSION,'pine_source_hash':PINE_HASH,'parameters_confirmed':parameters_confirmed,
                           'cached_capture_provenance':previous_provenance,
                           'cached_fixture_sha256':hashlib.sha256(cached.read_bytes()).hexdigest(),
                           'reference_records':len(all_rows),'compared_records':len(rows),'warmup_bars':len(prefix),
                           'excluded_last_time':int(all_rows[-1]['time'])*1000 if len(all_rows)>len(rows) else None,
                           'excluded_last_reason':'Unconfirmed reference candle' if len(all_rows)>len(rows) else None,
                           'currency_source':'PARITY_CTX_quote_usd_requested; unavailable before reference window',
                           'signal_reference_available':False}
    return fixture


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path);p.add_argument('--cached-fixture',required=True,type=Path)
    p.add_argument('--output-dir',required=True,type=Path);p.add_argument('--report',required=True,type=Path)
    p.add_argument('--parameters-confirmed',action='store_true');a=p.parse_args()
    symbol,tf,_,refs,origin,tick=inspect_contexts(a.input)
    a.output_dir.mkdir(parents=True,exist_ok=True); fixture_path=a.output_dir/'fixture.json'
    if a.report.resolve() in (a.input.resolve(),a.cached_fixture.resolve(),fixture_path.resolve()): p.error('Report cannot overwrite input/fixture')
    if fixture_path.exists():
        fixture=json.loads(fixture_path.read_text())
        if fixture['provenance']['csv_sha256']!=hashlib.sha256(a.input.read_bytes()).hexdigest(): raise ValueError('Cached output belongs to another CSV')
        if fixture['parameters']!=validate_parameters({}) or fixture['currency_rates']!=currency_observations(refs,tf): raise ValueError('Cached inputs differ from capture')
    else:
        fixture=asyncio.run(prepare(a.input,a.cached_fixture,parameters_confirmed=a.parameters_confirmed));dump(fixture_path,fixture)
    actual=[];first=int(refs[0]['time'])*1000;out=a.output_dir/'python.jsonl'
    with out.with_suffix('.partial').open('w') as f:
        for i,r in enumerate(replay_fixture(fixture,incremental_contexts=True)):
            if r['bar_start']>=first:
                compact={k:v for k,v in r.items() if k.startswith('PARITY_') or k in ('bar_start','symbol','timeframe','exchange','engine_version','pine_source_hash','replay_input_sha256')}
                compact.update({'PY_CTX_'+k:r['metrics'].get(expr) for k,expr in FIELDS.items() if expr.isidentifier() and expr in r['metrics']})
                actual.append(compact);f.write(json.dumps(compact,allow_nan=False)+'\n')
            if (i+1)%500==0: print('REPLAY',i+1,'/',len(fixture['bars']),flush=True)
    out.with_suffix('.partial').replace(out)
    report=compare(refs,actual,tick)
    # This capture intentionally has no signals script: classify that coverage
    # explicitly, keeping all metric failures and missing-row failures intact.
    for signal in report['signals']:
        signal.update(status='MISSING_REFERENCE',pine_events=None,match_percent=None,positive_event_coverage=False)
    metric_failure=any(m['status']=='FAIL' for m in report['metrics']) or report['missing_records'] or report['unexpected_python_records']
    report.update(status='FAIL' if metric_failure else 'UNVERIFIED',metrics_status='FAIL' if metric_failure else 'PASS',
                  scope='Historical metrics; signal reference absent',symbol=symbol,timeframe=tf,history_start=origin,
                  csv_sha256=fixture['provenance']['csv_sha256'],parameters_confirmed=a.parameters_confirmed,
                  intrabar_status='UNVERIFIED',currency_source=fixture['provenance']['currency_source'],
                  limitations=['No signal columns in this capture; old signals are not merged across incompatible HTF observations.',
                               'Native exchange warmup/contexts can differ from TradingView.',
                               'Observed currency rates cover the reference window only; warmup and live worker still use the source fallback where rates are unavailable.'])
    dump(a.report,report)
    print(json.dumps(dict(status=report['status'],metrics_pass=sum(m['status']=='PASS' for m in report['metrics']),rows=len(refs))),flush=True)


if __name__=='__main__': main()
