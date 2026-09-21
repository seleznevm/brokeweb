"""Compare diagnostic chart CSV with an origin-aligned historical replay."""
import argparse,asyncio,hashlib,json
from pathlib import Path
from .native import read_export,capture_export,dump
from .replay import replay_fixture
from .compare import compare
from .catalog import METRICS
from backend.engine.runtime import SIGNALS,PINE_HASH,ENGINE_VERSION
from backend.engine.parameters import validate_parameters


def inspect(path):
    symbol,tf,rows=read_export(path)
    required={'PARITY_'+k for k in [*METRICS,*SIGNALS.values()]}
    if required-set(rows[0]):raise ValueError('Missing diagnostic columns')
    origin=None;tick=None;closed=[]
    for r in rows:
        if r is rows[-1] and any(r['PARITY_META_'+g+'_confirmed']=='0' for g in ('metrics','signals')):continue
        for name in ('history_start','bar_index','tick_size','confirmed','volume','timeframe_seconds'):
            a=r['PARITY_META_metrics_'+name];b=r['PARITY_META_signals_'+name]
            if a!=b:raise ValueError('Diagnostic groups have different metadata: '+name)
        o=int(r['PARITY_META_metrics_history_start']);t=float(r['PARITY_META_metrics_tick_size'])
        if origin is not None and (origin!=o or tick!=t):raise ValueError('Origin/tick changed')
        origin,tick=o,t
        if int(r['PARITY_META_metrics_timeframe_seconds'])!=int(tf)*60:raise ValueError('Timeframe mismatch')
        if int(r['time'])*1000-origin!=int(r['PARITY_META_metrics_bar_index'])*int(tf)*60000:raise ValueError('History index mismatch')
        if r['PARITY_META_metrics_confirmed']=='1':closed.append(r)
        elif r is not rows[-1]:raise ValueError('Unconfirmed candle inside history')
    return symbol,tf,rows,closed,origin,tick


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True,type=Path);p.add_argument('--output-dir',required=True,type=Path);p.add_argument('--report',required=True,type=Path);p.add_argument('--parameters',type=Path);p.add_argument('--parameters-confirmed',action='store_true');a=p.parse_args()
    symbol,tf,rows,closed,origin,tick=inspect(a.input)
    params=validate_parameters(json.loads(a.parameters.read_text()) if a.parameters else {})
    sha=hashlib.sha256(a.input.read_bytes()).hexdigest();a.output_dir.mkdir(parents=True,exist_ok=True)
    fixture_path=a.output_dir/'fixture.json'
    if fixture_path.exists():
        fixture=json.loads(fixture_path.read_text())
        if fixture['provenance']['csv_sha256']!=sha or fixture['parameters']!=params:raise ValueError('Fixture mismatch')
    else:
        print('CAPTURE from recorded TradingView origin',origin,flush=True)
        fixture=asyncio.run(capture_export(a.input,fixture_path,params,int(rows[0]['PARITY_META_metrics_bar_index'])))
    if fixture['bars'][0]['start']!=origin:raise ValueError('Native history did not reach TradingView origin')
    fixture['tick_size']=tick
    # capture_export conservatively excludes the last row, even if confirmed.
    refs=[r for r in closed if int(r['time'])*1000<=fixture['bars'][-1]['start']]
    out=a.output_dir/'python.jsonl';actual=[]
    with out.with_suffix('.partial').open('w') as f:
        for i,r in enumerate(replay_fixture(fixture,incremental_contexts=True)):
            if r['bar_start']>=int(refs[0]['time'])*1000:
                compact={k:v for k,v in r.items() if k.startswith('PARITY_') or k in ('bar_start','symbol','timeframe','exchange','data_health','missing_contexts')}
                actual.append(compact);f.write(json.dumps(compact,allow_nan=False)+'\n')
            if (i+1)%500==0:print('REPLAY',i+1,'/',len(fixture['bars']),flush=True)
    out.with_suffix('.partial').replace(out)
    report=compare(refs,actual,tick)
    report.update(symbol=symbol,timeframe=tf,pine_source_hash=PINE_HASH,engine_version=ENGINE_VERSION,csv_sha256=sha,history_start=origin,parameters_confirmed=a.parameters_confirmed,reference_records=len(rows),compared_closed_records=len(refs),excluded_records=len(rows)-len(refs),intrabar_status='UNVERIFIED',feed_differences=fixture['provenance']['feed_differences'],limitations=['Native exchange warmup and request contexts can differ from TradingView feeds.','Historical CSV does not verify intrabar execution.'])
    report['observed_status']=report['status']
    if not a.parameters_confirmed and report['status']=='PASS':report['status']='UNVERIFIED'
    dump(a.report,report)
    print(json.dumps({k:report[k] for k in ('status','observed_status','compared_closed_records')}),flush=True)

if __name__=='__main__':main()
