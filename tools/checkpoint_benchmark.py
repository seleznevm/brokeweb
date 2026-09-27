"""Read one checkpoint; compare serialization paths without writing to the DB.

Run in an isolated process (not in a running worker): this patches local encoder
bindings while timing the pre-optimization implementation on the same state.
"""
import argparse
import dataclasses
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import select
from backend.engine import contexts, interpreter
from backend.engine.runtime import PineEngine, ENGINE_VERSION
from backend.engine.values import Record, Namespace
from backend.models.repository import Repository, clean
from backend.models.schema import Current
from backend.models.checkpoints import PackedCheckpoint, pack_checkpoint, unpack_checkpoint


def legacy_encode(value):
    if isinstance(value,Record):return {'__pine_record__':value.type_name,'fields':legacy_encode(value.fields)}
    if isinstance(value,Namespace):return {'__pine_namespace__':value.name}
    if isinstance(value,dict):return {str(k):legacy_encode(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [legacy_encode(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def legacy_clean(value):
    if dataclasses.is_dataclass(value):value=dataclasses.asdict(value)
    if isinstance(value,dict):return {str(k):legacy_clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [legacy_clean(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def measure(engine,old):
    started=time.perf_counter()
    if old:
        with patch.object(interpreter,'encode',legacy_encode),patch.object(contexts,'encode',legacy_encode):
            state=engine.export_state()
    else:state=engine.export_state()
    exported=time.perf_counter()
    blob=pack_checkpoint((legacy_clean if old else clean)(state))
    finished=time.perf_counter()
    return blob,{'export_ms':(exported-started)*1000,'clean_pack_ms':(finished-exported)*1000,'total_ms':(finished-started)*1000}


def measure_prepared(engine,old):
    started=time.perf_counter()
    if old:
        # Export before the canonical native-field boundary was introduced.
        from backend.engine.parameters import parameter_hash
        from backend.engine.runtime import PINE_HASH
        state={'version':ENGINE_VERSION,'pine_source_hash':PINE_HASH,
               'parameter_hash':parameter_hash(engine.parameters),'currency_rates':engine.currency_rates.records,
               'first_bar_start':engine.first_bar_start,'runtime':engine.runtime.export_state(),
               'contexts':engine.provider.export_state(),'chart_bars':engine.chart_bars,'snapshot':engine.snapshot}
    else:state=engine.export_state()
    exported=time.perf_counter()
    blob=pack_checkpoint(clean(state)) if old else PackedCheckpoint.from_state(state).blob
    finished=time.perf_counter()
    return blob,{'export_ms':(exported-started)*1000,'clean_pack_ms':(finished-exported)*1000,'total_ms':(finished-started)*1000}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--prepared',action='store_true',help='Compare previous export+clean with canonical export+prepared bytes')
    args=parser.parse_args()
    if not 1<=args.repeats<=20:parser.error('repeats must be 1..20')
    repo=Repository();parameters=repo.parameters()
    with repo.session() as session:
        row=session.scalar(select(Current).where(Current.payload['engine_version'].as_string()==ENGINE_VERSION,
            Current.payload['parameter_set_id'].as_string()==parameters['id'],Current.checkpoint_blob.is_not(None))
            .order_by(Current.updated_at.desc()).limit(1))
        if row is None:raise RuntimeError('No compatible current checkpoint available')
        symbol,tf=row.symbol,row.timeframe
        state=repo.read_checkpoint(row)
    engine=PineEngine(symbol,tf,parameters=parameters['values']);engine.restore_state(state)
    labels=('previous','prepared') if args.prepared else ('legacy','optimized')
    measure_run=measure_prepared if args.prepared else measure
    runs={label:[] for label in labels};reference=None
    for iteration in range(args.repeats):
        # Alternate order to reduce one-sided warm-cache bias.
        for old in ((True,False) if iteration%2==0 else (False,True)):
            blob,timing=measure_run(engine,old)
            if reference is None:reference=blob
            if blob!=reference:raise AssertionError('Serialized checkpoint bytes changed')
            runs[labels[0] if old else labels[1]].append(timing)
    restored=PineEngine(symbol,tf,parameters=parameters['values'])
    restored.restore_state(unpack_checkpoint(reference))
    if pack_checkpoint(clean(restored.export_state()))!=reference:raise AssertionError('Restored runtime state changed')
    medians={name:{metric:statistics.median(run[metric] for run in rows) for metric in rows[0]} for name,rows in runs.items()}
    result={'measured_at':datetime.now(timezone.utc).isoformat(),'symbol':symbol,'timeframe':tf,
        'engine_version':ENGINE_VERSION,'checkpoint_bytes':len(reference),'lossless_status':'PASS',
        'scope':'single saved state, no SQL writes, no market throughput measurement',
        'mode':'prepared' if args.prepared else 'scalar traversal',
        'median':medians,'speedup':medians[labels[0]]['total_ms']/medians[labels[1]]['total_ms'],'runs':runs}
    rendered=json.dumps(result,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(rendered+'\n')
    print(rendered)


if __name__=='__main__':main()
