"""Offline linear/indexed context comparison on the same saved native fixture.

Compares every snapshot (except wall-clock telemetry) and final checkpoint.
Does not certify TradingView parity or live full-universe capacity.
"""
import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from backend.engine.context_bars import ContextBars
from backend.engine.runtime import PineEngine, ENGINE_VERSION, PINE_HASH
from backend.models.repository import clean


def canonical(value):
    return json.dumps(clean(value), sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(',', ':')).encode()


def measure(fixture, count, indexed):
    engine=PineEngine(fixture['symbol'],fixture['timeframe'],fixture.get('tick_size',.01),
                      fixture.get('parameters'),fixture.get('currency_rates'))
    contexts=fixture.get('contexts',{})
    started=time.perf_counter()
    if indexed:contexts={k:ContextBars(v) for k,v in contexts.items()}
    index_ms=(time.perf_counter()-started)*1000
    elapsed=0.;digest=hashlib.sha256()
    for bar in fixture['bars'][:count]:
        if 'bar' in bar or not bar.get('confirmed',True):
            raise ValueError('Benchmark requires plain confirmed historical bars')
        started=time.perf_counter()
        snapshot=engine.update(bar,contexts,False)
        elapsed+=time.perf_counter()-started
        # Only nondeterministic performance telemetry is excluded, not scores,
        # signals, gates, missing contexts or the captured source locals.
        snapshot.pop('calculation_ms',None);snapshot.pop('calculation_timestamp',None)
        digest.update(canonical(snapshot))
    state_digest=hashlib.sha256(canonical(engine.export_state())).hexdigest()
    return {'index_ms':index_ms,'calculation_ms':elapsed*1000,
            'total_ms':index_ms+elapsed*1000,'snapshot_sha256':digest.hexdigest(),
            'checkpoint_sha256':state_digest}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--bars',type=int,default=100)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    fixture=json.loads(args.fixture.read_text())
    if not 1<=args.bars<=len(fixture['bars']) or not 1<=args.repeats<=10:
        parser.error('bars must fit fixture; repeats must be 1..10')
    runs={'linear':[],'indexed':[]};reference=None
    for iteration in range(args.repeats):
        for indexed in ((False,True) if iteration%2==0 else (True,False)):
            row=measure(fixture,args.bars,indexed)
            hashes=(row['snapshot_sha256'],row['checkpoint_sha256'])
            if reference is None:reference=hashes
            if hashes!=reference:raise AssertionError('Indexed replay changed snapshot or checkpoint')
            runs['indexed' if indexed else 'linear'].append(row)
            print(f'{iteration+1}: {"indexed" if indexed else "linear"} {row["total_ms"]:.1f} ms',flush=True)
    medians={name:statistics.median(r['total_ms'] for r in rows) for name,rows in runs.items()}
    result={'measured_at':datetime.now(timezone.utc).isoformat(),'status':'PASS',
            'scope':'offline context traversal equivalence; not TradingView parity or live capacity',
            'fixture_sha256':hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
            'symbol':fixture['symbol'],'timeframe':fixture['timeframe'],'bars':args.bars,
            'context_bars':{k:len(v) for k,v in fixture.get('contexts',{}).items()},
            'engine_version':ENGINE_VERSION,'pine_source_hash':PINE_HASH,
            'median_total_ms':medians,'speedup':medians['linear']/medians['indexed'],'runs':runs}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='runs'},indent=2))


if __name__=='__main__':main()
