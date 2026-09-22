"""Limited intrabar ATR/EMA check; does not replay setups or request contexts."""
import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path

from backend.engine.interpreter import Execution, tf_seconds
from backend.engine.syntax import load_program
from backend.engine.runtime import ENGINE_VERSION, PINE_HASH
from backend.marketdata import BybitAdapter
from .compare import percentile
from .native import dump
from .replay import normalize_bar
from .trace import messages, unpack

FIELDS={'atr':'atr','ema_fast':'emaFast','ema_slow':'emaSlow'}


def check(session, warmup):
    metadata=session['metadata'];rows=session['rows'];tf=metadata['timeframe']
    symbol=metadata['symbol'].removesuffix('.P');step=tf_seconds(tf)*1000
    origin=metadata['history_start'];start=rows[0]['bar_start']
    bars=[normalize_bar(b,symbol,tf) for b in warmup]
    if not bars or bars[0]['start']!=origin or bars[-1]['end']!=start:
        raise ValueError('Warmup must cover exactly the recorded origin through the first open candle')
    if any(not b['confirmed'] for b in bars) or any(a['end']!=b['start'] for a,b in zip(bars,bars[1:])):
        raise ValueError('Warmup has unconfirmed bars or gaps')
    capture=session['report']
    if (not capture.get('received_sequence_contiguous',capture['sequence_contiguous'])
            or capture.get('reported_dropped_updates',0)
            or any(b['seq']!=a['seq']+1 for a,b in zip(rows,rows[1:]))):
        raise ValueError('Cannot check a discontinuous trace')
    program=load_program()
    statements=[s for s in program.statements if s.kind=='assign' and s.meta['name'] in FIELDS.values()]
    ex=Execution(program,metadata['parameters'],f'BYBIT:{metadata["symbol"]}',tf,metadata['tick_size'])
    for b in bars:
        ex.begin(b);ex.execute(statements);ex.commit()
    actual=[];previous=None
    for row in rows:
        if row['bar_end']-row['bar_start']!=step or row['bar_start']-origin!=row['bar_index']*step:
            raise ValueError('Trace chart origin/index/timeframe mismatch')
        if previous and row['bar_start']!=previous['bar_start']:
            if not previous['confirmed'] or row['bar_start']!=previous['bar_end']:
                raise ValueError('Cannot advance past an unclosed or missing candle')
            # Commit the last execution once. Repeated closing executions of the
            # same candle all use the prior candle's history, without deduping seq.
            ex.commit()
        bar=dict(start=row['bar_start'],end=row['bar_end'],received_at=row['event_time'],
                 confirmed=row['confirmed'],volume=row['volume'],
                 **{k:row['PARITY_'+k] for k in ('open','high','low','close')})
        ex.begin(bar,realtime=True);ex.execute(statements)
        actual.append(dict(seq=row['seq'],event_time=row['event_time'],bar_start=row['bar_start'],
                           **{'PARITY_'+k:ex.lookup(v) for k,v in FIELDS.items()}))
        previous=row
    metrics=[]
    for field in FIELDS:
        errors=[];relative=[];invalid=0
        for ref,py in zip(rows,actual):
            a,b=ref['PARITY_'+field],py['PARITY_'+field]
            if a is None or b is None or not math.isfinite(a) or not math.isfinite(b):invalid+=1;continue
            errors.append(abs(a-b));relative.append(abs(a-b)/max(abs(a),1e-12))
        passed=not invalid and bool(relative) and max(relative)<=.0005
        metrics.append(dict(metric=field,status='PASS' if passed else 'FAIL',pairs=len(errors),
                            invalid_pairs=invalid,error_median=percentile(errors,.5),error_p95=percentile(errors,.95),
                            relative_error_max=max(relative) if relative else None))
    return actual,dict(status='PASS' if all(m['status']=='PASS' for m in metrics) else 'FAIL',
                       scope='Only chart ATR and EMA calculations on supplied realtime OHLCV',
                       full_intrabar_status='UNVERIFIED',engine_version=ENGINE_VERSION,pine_source_hash=PINE_HASH,
                       session_id=session['session_id'],matched_updates=len(actual),warmup_bars=len(bars),
                       missing_prefix_updates=rows[0]['seq']-1,
                       history_start=origin,metrics=metrics,
                       limitations=['Native exchange warmup; captured chart OHLCV is supplied input.',
                                    'Missing updates before the first supplied row are not reconstructed; this check uses only closed-bar TA history.',
                                    'No setup state, scores, request contexts or signal parity is established.'])


async def prepare(session, cached):
    metadata=session['metadata'];start=session['rows'][0]['bar_start'];origin=metadata['history_start']
    tf=metadata['timeframe'];step=tf_seconds(tf)*1000
    fixture=json.loads(cached.read_text())
    key=f'BYBIT:{metadata["symbol"]}|{tf}'
    bars=[b for b in fixture['contexts'].get(key,[]) if origin<=b['start']<start]
    if not bars or bars[0]['start']!=origin:raise ValueError('Cached native stream does not cover capture origin')
    if bars[-1]['end']<start:
        adapter=BybitAdapter()
        try:
            tail=await adapter.backfill(metadata['symbol'].removesuffix('.P'),tf,
                                        math.ceil((start-bars[-1]['end'])/step)+2,end=start-1)
            end=bars[-1]['end']
            bars.extend(b.to_dict() for b in tail if b.confirmed and b.start>=end and b.end<=start)
        finally:await adapter.close()
    return bars


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--cached-fixture',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    sessions,_=unpack(messages(a.input))
    if len(sessions)!=1:p.error('Expected exactly one session')
    if a.output_dir.exists():p.error('Choose a new output directory to preserve evidence')
    session=sessions[0];warmup=asyncio.run(prepare(session,a.cached_fixture))
    actual,report=check(session,warmup)
    report.update(input_file=str(a.input),input_sha256=hashlib.sha256(a.input.read_bytes()).hexdigest(),
                  warmup_sha256=hashlib.sha256(json.dumps(warmup,sort_keys=True).encode()).hexdigest())
    a.output_dir.mkdir(parents=True)
    dump(a.output_dir/'warmup.json',warmup);dump(a.output_dir/'report.json',report)
    (a.output_dir/'python.jsonl').write_text(''.join(json.dumps(r,allow_nan=False)+'\n' for r in actual))
    print(json.dumps(report));return 0 if report['status']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
