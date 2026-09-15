"""Source-derived, versionable configuration: every Pine input is retained."""
from __future__ import annotations
import hashlib
import json
from functools import lru_cache
from .syntax import load_program
from .interpreter import Execution, qualified, tf_seconds
from .values import encode

@lru_cache(maxsize=1)
def input_schema():
    p=load_program(); runtime=Execution(p)
    runtime.begin(dict(start=0,end=60000,open=0.,high=0.,low=0.,close=0.,volume=0.))
    schema=[]
    for st in p.statements:
        if st.line>=1200:break
        runtime.statement(st)
        if st.expr and st.expr.kind=='call' and str(qualified(st.expr.args[0])).startswith('input.'):
            call=st.expr; kind=qualified(call.args[0]).split('.')[-1]
            args=[n for n in call.args[1:] if n.kind!='kw']; kwargs={n.value:runtime.eval(n.args[0]) for n in call.args[1:] if n.kind=='kw'}
            default=runtime.eval(args[0]) if args else kwargs['defval']
            if kind=='source':default=args[0].value
            item={'name':st.meta['name'],'type':kind,'default':encode(default),'title':runtime.eval(args[1]) if len(args)>1 else kwargs.get('title',st.meta['name']),'line':st.line}
            item.update({key:encode(kwargs[key]) for key in ('minval','maxval','step','options','group','tooltip') if key in kwargs});schema.append(item)
    return schema

def defaults():return {s['name']:s['default'] for s in input_schema()}

def validate_parameters(values):
    specs={s['name']:s for s in input_schema()}; unknown=set(values)-set(specs)-{'snapshot_interval_sec'}
    if unknown:raise ValueError(f'Unknown Pine parameters: {sorted(unknown)}')
    result=defaults()
    for name,value in values.items():
        if name=='snapshot_interval_sec':
            if isinstance(value,bool) or not isinstance(value,(int,float)) or value<1:raise ValueError('snapshot_interval_sec must be >= 1')
            result[name]=value;continue
        s=specs[name];typ=s['type']
        if typ=='bool' and not isinstance(value,bool):raise ValueError(f'{name} must be bool')
        if typ=='int' and (not isinstance(value,int) or isinstance(value,bool)):raise ValueError(f'{name} must be integer')
        if typ=='float' and (not isinstance(value,(int,float)) or isinstance(value,bool)):raise ValueError(f'{name} must be number')
        if typ in ('color','string','timeframe','symbol','source') and not isinstance(value,str):raise ValueError(f'{name} must be string')
        if typ in ('int','float'):
            import math
            if not math.isfinite(value):raise ValueError(f'{name} must be finite')
            if 'minval' in s and value<s['minval'] or 'maxval' in s and value>s['maxval']:raise ValueError(f'{name} out of bounds')
        if 'options' in s and value not in s['options']:raise ValueError(f'{name} invalid option')
        if typ=='timeframe':tf_seconds(value)
        if typ=='source' and value not in ('open','high','low','close','volume','hl2','hlc3','ohlc4'):raise ValueError(f'{name}: external series must be supplied by an explicit adapter; supported native series only')
        result[name]=value
    return result

def parameter_hash(values):return hashlib.sha256(json.dumps(validate_parameters(values),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def required_history(parameters=None,timeframe='15'):
    """Conservative bound from *all* integer source inputs plus recursive seeds.

    Finite backfill cannot prove equality of indefinitely persistent zones. The
    exact starting timestamp is therefore part of parity provenance.
    """
    p=validate_parameters(parameters or {})
    lengths=[v for k,v in p.items() if isinstance(v,int) and not isinstance(v,bool) and any(x in k.lower() for x in ('len','lookback','bars','history'))]
    return max(max(lengths,default=0)*2+50,max(p['emaSlowLen'],p['btcSlowLen'])*10,500)
