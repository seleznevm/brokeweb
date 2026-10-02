"""Bounded, read-only health sampling; never certifies full-universe capacity."""
import argparse
import json
import math
import time
from pathlib import Path
from urllib.request import urlopen


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def assess(samples,max_pipeline_lag_ms=5000):
    reasons=set();counts=[]
    if len(samples)<2:reasons.add('At least two samples required')
    for sample in samples:
        health=sample.get('health',{});now=sample['captured_at']
        engines={key:value for key,value in health.get('services',{}).items() if key=='engine' or key.startswith('engine:')}
        if sample.get('error'):reasons.add('Health request failed')
        if health.get('status')!='HEALTHY':reasons.add('Application is not HEALTHY')
        if not engines:reasons.add('No engine heartbeat')
        if engines:
            universes={e.get('universe') for e in engines.values()}
            excluded={e.get('liquidity_excluded',0) for e in engines.values()}
            valid_universe=len(universes)==1 and all(finite(n) and n>0 for n in universes)
            valid_excluded=len(excluded)==1 and all(finite(n) and n>=0 for n in excluded)
            eligible=next(iter(universes))-next(iter(excluded)) if valid_universe and valid_excluded else None
            if eligible is None or eligible<=0 or sum(e.get('selected',0) for e in engines.values())!=eligible:
                reasons.add('Selected shards do not cover the liquidity-eligible universe')
            if {e.get('shard_index') for e in engines.values()}!=set(range(len(engines))):
                reasons.add('Missing or duplicate shard indices')
        total=0
        for name,engine in engines.items():
            def reject(message):reasons.add(f'{name}: {message}')
            if engine.get('shard_count')!=len(engines):reject('Incomplete shard coverage')
            updated=engine.get('updated_at')
            if not finite(updated) or now-updated>90000 or engine.get('stale'):reject('Stale heartbeat')
            if engine.get('max_symbols')!=0:reject('Limited universe')
            selected=engine.get('selected',0);tfs=engine.get('timeframes',[])
            if not selected or not tfs or engine.get('initialized')!=selected:reject('Universe initialization incomplete')
            if not selected or engine.get('healthy_engines')!=selected*len(tfs):reject('Engine freshness coverage incomplete')
            if engine.get('status')!='HEALTHY' or engine.get('errors') or engine.get('reconciliation_errors'):reject('Engine is recovering or has errors')
            lag=engine.get('market_data_lag_ms')
            if not finite(lag) or not 0<=lag<=90000:reject('Market lag unavailable or above 90s')
            pipeline=engine.get('pipeline_lag_ms_p95')
            if not finite(pipeline) or not 0<=pipeline<=max_pipeline_lag_ms:reject('Pipeline p95 unavailable or above budget')
            if not finite(engine.get('pending_calculations')) or engine['pending_calculations']<0:reject('Pending calculation count unavailable')
            if engine.get('recovering_symbols')!=0:reject('Recovery incomplete')
            streams=engine.get('streams',[])
            if not streams or any(not s.get('connected') or not finite(s.get('last_market_event')) or now-s['last_market_event']>90000 for s in streams):reject('Market streams are not fresh')
            if engine.get('btc_recovering') or not engine.get('btc_stream',{}).get('connected'):reject('BTC stream unavailable')
            btc_events=engine.get('btc_timeframe_events',{})
            if not btc_events or any(not finite(ts) or now-ts>90000 for ts in btc_events.values()):reject('BTC contexts are not fresh')
            calculated=engine.get('calculations')
            if not finite(calculated):reject('Calculation counter unavailable')
            else:total+=calculated
        counts.append(total)
    elapsed=(samples[-1]['captured_at']-samples[0]['captured_at'])/1000 if len(samples)>1 else 0
    if elapsed<=0 or not counts or counts[-1]<=counts[0]:reasons.add('No observed calculation progress')
    if any(b<a for a,b in zip(counts,counts[1:])):reasons.add('Calculation counters reset during probe')
    return {'readiness':'NOT_READY' if reasons else 'READY_FOR_SOAK','capacity_status':'UNVERIFIED',
            'scope':'short health probe; sustained throughput, storage growth and restart acceptance remain required',
            'observed_seconds':elapsed,'calculation_delta':counts[-1]-counts[0] if counts else 0,
            'reasons':sorted(reasons)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://localhost:8080/api/health')
    parser.add_argument('--samples',type=int,default=3)
    parser.add_argument('--interval',type=float,default=15)
    parser.add_argument('--output',type=Path,default=Path('artifacts/local/capacity-probe.json'))
    parser.add_argument('--max-pipeline-lag-ms',type=float,default=5000)
    args=parser.parse_args()
    if not 2<=args.samples<=100 or not 0<args.interval<=60:parser.error('Use 2..100 samples and interval (0,60] seconds')
    samples=[]
    for index in range(args.samples):
        if index:time.sleep(args.interval)
        sample={}
        try:
            with urlopen(args.url,timeout=10) as response:sample['health']=json.load(response)
        except (OSError,ValueError) as exc:sample['error']=str(exc)
        sample['captured_at']=time.time_ns()//1_000_000;samples.append(sample)
        print(f'Captured {index+1}/{args.samples}',flush=True)
    result={**assess(samples,args.max_pipeline_lag_ms),'samples':samples,'max_pipeline_lag_ms':args.max_pipeline_lag_ms}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='samples'},indent=2))


if __name__=='__main__':main()
