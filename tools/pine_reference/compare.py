"""Honest parity comparator: missing references/columns/rows fail acceptance."""
from __future__ import annotations
import argparse,csv,json,math,statistics
from pathlib import Path
from .catalog import METRICS,PATHS
from .exporter import action_codes
from backend.engine.runtime import SIGNALS,PINE_HASH,ENGINE_VERSION

def number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError):return None

def percentile(values,q):
    values=sorted(values)
    if not values:return None
    i=(len(values)-1)*q;lo=int(i);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(i-lo)

def metric_value(row,name):
    if 'PARITY_'+name in row:return number(row['PARITY_'+name])
    if name in row and (row[name] is None or number(row[name]) is not None):return number(row[name])
    metrics=row.get('metrics',{})
    if name=='action':return action_codes().get(metrics.get('actionText',row.get('action')))
    if name in ('candidate_path','trigger_path'):return PATHS.get(metrics.get('candidateEntryPath' if name=='candidate_path' else 'entryPath',row.get(name)))
    pine=METRICS[name][0]
    return number(metrics.get(pine))

def timestamp(row,realtime=False):
    value=row.get('event_time') if realtime else row.get('bar_start',row.get('time'))
    numeric=number(value)
    if numeric is not None:return int(numeric*1000) if abs(numeric)<100000000000 else int(numeric)
    if isinstance(value,str):
        from datetime import datetime
        return int(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000)
    raise ValueError('Record missing UTC timestamp')

def compare(reference,actual,tick_size,realtime=False,max_timestamp_delta_ms=1500):
    report={'status':'UNVERIFIED','engine_version':ENGINE_VERSION,'pine_source_hash':PINE_HASH,'reference_records':len(reference),'python_records':len(actual),'metrics':[],'signals':[],'realtime':realtime,'reasons':[]}
    if not reference or not actual:
        report['reasons']=['TradingView reference and Python replay are both required; no fabricated parity percentages.']
        report['metrics']=[{'metric':name,'pine':None,'python':None,'error_median':None,'error_p95':None,'status':'MISSING_REFERENCE'} for name in METRICS]
        report['signals']=[{'signal':name,'pine_events':None,'python_events':None,'match_percent':None,'status':'MISSING_REFERENCE'} for name in SIGNALS.values()]
        return report
    # Identity is required for mixed exports, single-chart files may omit it.
    def key(r):return (r.get('exchange'),r.get('symbol'),r.get('timeframe'),timestamp(r,realtime))
    indexes={};seen=set()
    for row in actual:
        k=key(row)
        if k in seen:raise ValueError('Duplicate Python comparison key')
        seen.add(k);indexes[k]=row
    paired=[];missing_rows=0;used=set()
    for ref in reference:
        k=key(ref);match=indexes.get(k)
        if match is None and k[:3]==(None,None,None):
            candidates=[(key(row),row) for row in actual if timestamp(row,realtime)==k[3]]
            if len(candidates)==1:k,match=candidates[0]
        if match is None and realtime:
            candidates=[(abs(ak[3]-k[3]),ak,row) for ak,row in indexes.items() if ak[:3]==k[:3] and ak not in used]
            if candidates:
                distance,k2,row=min(candidates,key=lambda x:x[0])
                if distance<=max_timestamp_delta_ms:k,match=k2,row
        if match is None or k in used:missing_rows+=1;continue
        used.add(k);paired.append((ref,match))
    report['matched_records']=len(paired);report['missing_records']=missing_rows;failed=missing_rows>0
    for name,(_,kind) in METRICS.items():
        differences=[];relative=[];mismatches=0;missing_columns=0;both_na=0
        for ref,actual_row in paired:
            has_ref=name in ref or 'PARITY_'+name in ref or METRICS[name][0] in ref.get('metrics',{})
            if not has_ref:missing_columns+=1;continue
            a,b=metric_value(ref,name),metric_value(actual_row,name)
            if a is None and b is None:both_na+=1;continue
            if a is None or b is None:mismatches+=1;continue
            delta=abs(a-b);differences.append(delta);relative.append(delta/max(abs(a),1e-12));mismatches+=int(delta!=0)
        median=percentile(differences,.5);p95=percentile(differences,.95)
        valid=len(differences);agreement=100*(valid-mismatches)/valid if valid else None
        passed=bool(valid) and missing_columns==0 and not missing_rows
        if kind in ('discrete','bool'):passed=passed and mismatches==0
        elif kind=='ohlc':passed=passed and max(differences,default=math.inf)<=tick_size*1e-8
        elif kind=='price':passed=passed and max(differences,default=math.inf)<=tick_size*(2 if name in ('sl','t1') else 1)
        elif kind=='indicator':passed=passed and max(relative,default=math.inf)<=.0005
        else:passed=passed and median<=.5 and p95<=(2 if realtime else 1.5)
        # NA mismatch is never hidden by discarding records.
        missing_values=sum((metric_value(a,name) is None)!=(metric_value(b,name) is None) for a,b in paired)
        passed=passed and not missing_values
        failed|=not passed
        report['metrics'].append({'metric':name,'pine':valid+both_na,'python':valid,'error_median':median,'error_p95':p95,'relative_error_max':max(relative) if relative else None,'agreement_percent':agreement,'missing_columns':missing_columns,'missing_values':missing_values,'status':'PASS' if passed else 'FAIL'})
    for pine,name in SIGNALS.items():
        pe=ae=matches=0;missing=0
        for ref,row in paired:
            raw=ref.get('PARITY_'+name,ref.get(name,ref.get('metrics',{}).get(pine)))
            if raw is None:missing+=1;continue
            r=bool(number(raw));a=name in row.get('signals',[]) if 'signals' in row else bool(number(row.get('PARITY_'+name,row.get(name,row.get('metrics',{}).get(pine)))))
            pe+=r;ae+=a;matches+=int(r==a)
        passed=not missing and not missing_rows and matches==len(paired)
        failed|=not passed
        report['signals'].append({'signal':name,'pine_events':pe,'python_events':ae,'match_percent':100*matches/len(paired) if paired else None,'missing_columns':missing,'status':'PASS' if passed else 'FAIL'})
    report['status']='FAIL' if failed else 'PASS'
    return report

def load(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as handle:return list(csv.DictReader(handle))
    content=path.read_text();return json.loads(content) if content.lstrip().startswith('[') else [json.loads(s) for s in content.splitlines() if s.strip()]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reference');parser.add_argument('--python');parser.add_argument('--tick-size',type=float,default=.01);parser.add_argument('--realtime',action='store_true');parser.add_argument('--output',default='reports/parity.json');args=parser.parse_args()
    report=compare(load(args.reference) if args.reference else [],load(args.python) if args.python else [],args.tick_size,args.realtime)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(report['status'])
    return 0 if report['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
