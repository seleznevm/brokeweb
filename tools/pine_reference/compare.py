"""Honest parity comparator: missing references/columns/rows fail acceptance."""
from __future__ import annotations
import argparse,csv,json,math,tempfile
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
    return number(metric_raw(row,name))

def timestamp(row,realtime=False):
    field='event_time' if realtime else 'bar_start' if 'bar_start' in row else 'time'
    value=row.get(field)
    if isinstance(value,bool):raise ValueError('Boolean timestamp is invalid')
    numeric=number(value)
    if numeric is not None:
        stamp=numeric*1000 if field=='time' and abs(numeric)<100000000000 else numeric
        if stamp<0 or not stamp.is_integer():raise ValueError('Timestamp must resolve to nonnegative integer UTC milliseconds')
        return int(stamp)
    if isinstance(value,str):
        from datetime import datetime
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError('ISO timestamp requires an explicit timezone')
        stamp=parsed.timestamp()*1000
        if stamp<0 or not stamp.is_integer():raise ValueError('Invalid timestamp precision')
        return int(stamp)
    raise ValueError('Record missing UTC timestamp')


MISSING=object()


def metric_raw(row,name):
    if 'PARITY_'+name in row:return row['PARITY_'+name]
    metrics=row.get('metrics',{})
    pine=METRICS[name][0]
    if pine in metrics:return metrics[pine]
    if name=='action':
        value=metrics.get('actionText',row.get('action',MISSING))
        if isinstance(value,str):return action_codes().get(value,value)
        return value
    if name in ('candidate_path','trigger_path'):
        value=metrics.get('candidateEntryPath' if name=='candidate_path' else 'entryPath',row.get(name,MISSING))
        return PATHS.get(value,value) if isinstance(value,str) else value
    return row.get(name,MISSING)


def signal_raw(row,pine,name):
    # An explicit exported value must not be hidden by an empty signals list.
    for key in ('PARITY_'+name,name):
        if key in row:return row[key]
    if pine in row.get('metrics',{}):return row['metrics'][pine]
    if 'signals' in row:
        values=row['signals']
        if not isinstance(values,list) or any(not isinstance(v,str) or v not in SIGNALS.values() for v in values):
            raise ValueError('signals must be a list of known signal names')
        return int(name in values)
    return MISSING


def cell(raw,boolean=False):
    """Return presence, numeric value, validity; absent is distinct from Pine NA."""
    if raw is MISSING:return False,None,True
    if raw is None or isinstance(raw,str) and raw.strip().lower() in ('','na','nan'):
        return True,None,not boolean
    value=number(raw)
    return True,value,value is not None and (not boolean or value in (0,1))


def pair_rows(reference,actual,realtime,tolerance):
    def identity(row):
        values=tuple(row.get(k) for k in ('exchange','symbol','timeframe'))
        if any(v is not None for v in values) and not all(v is not None and str(v) for v in values):
            raise ValueError('Comparison identity requires exchange, symbol and timeframe together')
        return tuple(str(v) if v is not None else None for v in values)
    anonymous=(None,None,None)
    actual_ids={identity(r) for r in actual}
    ref_ids={identity(r) for r in reference}
    if anonymous in ref_ids and len(actual_ids)!=1:
        raise ValueError('Anonymous reference is ambiguous across multiple Python instruments')
    if anonymous in actual_ids and (len(actual_ids)!=1 or ref_ids!={anonymous}):
        raise ValueError('Python observations lack the reference instrument identity')
    def key(row,is_reference=False):
        ident=identity(row)
        if is_reference and ident==anonymous:ident=next(iter(actual_ids))
        return (*ident,timestamp(row,realtime))
    refs={};observations={}
    for rows,target,label,is_ref in ((reference,refs,'reference',True),(actual,observations,'Python',False)):
        for row in rows:
            k=key(row,is_ref)
            if k in target:raise ValueError(f'Duplicate {label} comparison key')
            target[k]=row
    # Reserve exact timestamps before bounded nearest matching. A nearby row
    # cannot consume another reference's exact observation.
    assignments={k:k for k in refs if k in observations};used=set(assignments.values())
    if realtime:
        for k in sorted(refs,key=lambda k:k[3]):
            if k in assignments:continue
            candidates=[ak for ak in observations if ak[:3]==k[:3] and ak not in used and abs(ak[3]-k[3])<=tolerance]
            if candidates:
                match=min(candidates,key=lambda ak:(abs(ak[3]-k[3]),ak[3]))
                assignments[k]=match;used.add(match)
    windows={}
    for k in refs:
        lo,hi=windows.get(k[:3],(k[3],k[3]));windows[k[:3]]=(min(lo,k[3]),max(hi,k[3]))
    unexpected=[k for k in observations if k not in used and k[:3] in windows and windows[k[:3]][0]<=k[3]<=windows[k[:3]][1]]
    pairs=[(refs[k],observations[assignments[k]]) for k in sorted(assignments,key=lambda k:k[3])]
    return pairs,len(refs)-len(pairs),len(unexpected)


def compare(reference,actual,tick_size,realtime=False,max_timestamp_delta_ms=1500):
    if isinstance(tick_size,bool) or number(tick_size) is None or float(tick_size)<=0:raise ValueError('tick_size must be positive and finite')
    tick_size=float(tick_size)
    if isinstance(max_timestamp_delta_ms,bool) or number(max_timestamp_delta_ms) is None or not 0<=float(max_timestamp_delta_ms)<=1500:
        raise ValueError('Timestamp tolerance must be between 0 and 1500 ms')
    report={'status':'UNVERIFIED','engine_version':ENGINE_VERSION,'pine_source_hash':PINE_HASH,'reference_records':len(reference),'python_records':len(actual),'metrics':[],'signals':[],'realtime':realtime,'reasons':[]}
    if not reference or not actual:
        report['reasons']=['TradingView reference and Python replay are both required; no fabricated parity percentages.']
        report['metrics']=[{'metric':name,'pine':None,'python':None,'error_median':None,'error_p95':None,'status':'MISSING_REFERENCE'} for name in METRICS]
        report['signals']=[{'signal':name,'pine_events':None,'python_events':None,'match_percent':None,'status':'MISSING_REFERENCE'} for name in SIGNALS.values()]
        return report
    paired,missing_rows,unexpected=pair_rows(reference,actual,realtime,float(max_timestamp_delta_ms))
    report.update(matched_records=len(paired),missing_records=missing_rows,unexpected_python_records=unexpected,
                  timestamp_error_max_ms=max((abs(timestamp(a,realtime)-timestamp(b,realtime)) for a,b in paired),default=None))
    row_failure=bool(missing_rows or unexpected)
    if missing_rows:report['reasons'].append('Reference observations have no unique Python match.')
    if unexpected:report['reasons'].append('Extra Python observations exist inside the reference time window.')
    for name,(_,kind) in METRICS.items():
        differences=[];relative=[];both_na=na_mismatch=exact=numeric_ref=numeric_actual=invalid=missing_ref=missing_actual=missing_columns=0
        examples=[]
        for ref,python in paired:
            ap,a,av=cell(metric_raw(ref,name),kind=='bool');bp,b,bv=cell(metric_raw(python,name),kind=='bool')
            missing_ref+=not ap;missing_actual+=not bp;missing_columns+=not ap or not bp
            numeric_ref+=ap and av and a is not None;numeric_actual+=bp and bv and b is not None
            reason=None
            if not ap or not bp:reason='MISSING_COLUMN'
            elif not av or not bv:invalid+=1;reason='INVALID_VALUE'
            elif a is None and b is None:both_na+=1;exact+=1
            elif a is None or b is None:na_mismatch+=1;reason='NA_MISMATCH'
            else:
                delta=abs(a-b);differences.append(delta);relative.append(delta/max(abs(a),1e-12));exact+=delta==0
                if delta:reason='NUMERIC_DIFFERENCE'
            if reason and len(examples)<5:examples.append({'time':timestamp(ref,realtime),'python_time':timestamp(python,realtime),'pine':a,'python':b,'reason':reason})
        median=percentile(differences,.5);p95=percentile(differences,.95)
        passed=bool(differences) and not(row_failure or missing_columns or invalid or na_mismatch)
        if kind in ('discrete','bool'):passed=passed and max(differences,default=math.inf)==0
        elif kind=='ohlc':passed=passed and max(differences,default=math.inf)<=tick_size*1e-8
        elif kind=='price':passed=passed and max(differences,default=math.inf)<=tick_size*(2 if name in ('sl','t1') else 1)
        elif kind=='indicator':passed=passed and max(relative,default=math.inf)<=.0005
        else:passed=passed and median<=.5 and p95<=(2 if realtime else 1.5)
        unobserved=bool(paired) and both_na==len(paired) and not row_failure
        status='PASS' if passed else 'NOT_OBSERVED' if unobserved else 'FAIL'
        report['metrics'].append({'metric':name,'pine':numeric_ref,'python':numeric_actual,'both_na':both_na,'error_median':median,'error_p95':p95,'relative_error_max':max(relative) if relative else None,
                                 'agreement_percent':100*exact/len(paired) if paired and not unobserved else None,
                                 'missing_columns':missing_columns,'missing_reference_columns':missing_ref,'missing_python_columns':missing_actual,'missing_values':na_mismatch,'invalid_values':invalid,
                                 'first_differences':examples,'status':status})
    for pine,name in SIGNALS.items():
        pe=ae=matches=missing_ref=missing_actual=missing_columns=invalid=0;examples=[]
        for ref,python in paired:
            ap,a,av=cell(signal_raw(ref,pine,name),True);bp,b,bv=cell(signal_raw(python,pine,name),True)
            missing_ref+=not ap;missing_actual+=not bp;missing_columns+=not ap or not bp
            if ap and av:pe+=int(a==1)
            if bp and bv:ae+=int(b==1)
            valid=ap and bp and av and bv
            invalid+=ap and bp and not(av and bv)
            matches+=valid and a==b
            if (not valid or a!=b) and len(examples)<5:examples.append({'time':timestamp(ref,realtime),'python_time':timestamp(python,realtime),'pine':a,'python':b,'reason':'MISSING_COLUMN' if not ap or not bp else 'INVALID_VALUE' if not av or not bv else 'SIGNAL_MISMATCH'})
        passed=bool(paired) and not row_failure and matches==len(paired)
        report['signals'].append({'signal':name,'pine_events':pe,'python_events':ae,'match_percent':100*matches/len(paired) if paired else None,
                                 'positive_event_coverage':pe>0,'missing_columns':missing_columns,'missing_reference_columns':missing_ref,'missing_python_columns':missing_actual,'invalid_values':invalid,'first_differences':examples,'status':'PASS' if passed else 'FAIL'})
    statuses={r['status'] for r in report['metrics']+report['signals']}
    report['status']='FAIL' if row_failure or 'FAIL' in statuses else 'UNVERIFIED' if 'NOT_OBSERVED' in statuses else 'PASS'
    if 'NOT_OBSERVED' in statuses:report['reasons'].append('All-NA metrics do not provide numerical parity coverage.')
    return report

def load(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as handle:
            reader=csv.DictReader(handle);columns=reader.fieldnames or []
            if not columns or len(columns)!=len(set(columns)):raise ValueError('Missing or duplicate CSV headers')
            rows=list(reader)
            if any(None in row or any(v is None for v in row.values()) for row in rows):raise ValueError('CSV row width differs from its header')
    else:
        content=path.read_text(encoding='utf-8-sig')
        rows=json.loads(content) if content.lstrip().startswith('[') else [json.loads(s) for s in content.splitlines() if s.strip()]
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError('Observations must be objects')
    if any('metrics' in r and not isinstance(r['metrics'],dict) for r in rows):raise ValueError('metrics must be an object')
    return rows

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--reference');parser.add_argument('--python');parser.add_argument('--tick-size',type=float,default=.01);parser.add_argument('--realtime',action='store_true');parser.add_argument('--output',default='reports/full-parity.json');args=parser.parse_args(argv)
    path=Path(args.output)
    if any(Path(p).resolve()==path.resolve() for p in (args.reference,args.python) if p):parser.error('Output must differ from reference and Python inputs')
    try:
        report=compare(load(args.reference) if args.reference else [],load(args.python) if args.python else [],args.tick_size,args.realtime)
    except (ValueError,OSError) as exc:
        report={'status':'INVALID_INPUT','engine_version':ENGINE_VERSION,'pine_source_hash':PINE_HASH,'reasons':[str(exc)],'metrics':[],'signals':[]}
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix=path.name+'.',suffix='.partial',delete=False) as handle:
            temporary=Path(handle.name)
            json.dump(report,handle,ensure_ascii=False,allow_nan=False,indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    print(report['status'])
    return 0 if report['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
