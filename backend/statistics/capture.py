"""Freeze signal events; never count periodic state updates."""
import hashlib,json
from sqlalchemy import select
from backend.models.schema import Signal,WTEvent
from .models import Evaluation,CaptureCursor
from .outcomes import POLICY,evaluate
def candidates(row,wt=False):
    p=row.payload
    if wt:
        if p.get('duplicate_of') or not p.get('signals') or not p.get('signal_setups'):return []
        source=row.signal_source
        if source=='tradingview' and 'READY TO ENTER' in p.get('signals',[]):return []
        if source=='tradingview' and p.get('raw_body','').startswith('WT READY TO ENTER'):return []
        if source=='engine' and 'READY TO ENTER' in p.get('signals',[]) and not any(p.get('metrics',{}).get(k) for k in ('fireLongAlert','fireShortAlert')):return []
        families=[v for v in p['signal_setups'] if v in ('T1','T2','T3','T4')]
        identity=[source,row.exchange,row.symbol,row.timeframe,p.get('plan_bar_start') or p.get('setup_generation_id') or str(row.id),p.get('direction'),p.get('parameter_hash'),bool(p.get('replay'))]
        if source=='tradingview':identity=[source,row.exchange,p.get('source_event_id') or row.dedupe_key]
        strategy='WT_SETUPS'
    else:
        families={'LONG WATCH ENTRY':['WE'],'SHORT WATCH ENTRY':['WE'],'PINE READY LONG':['PINE READY'],'PINE READY SHORT':['PINE READY']}.get(row.name,[])
        source='engine';strategy='BROKE';identity=['signal',row.id]
    items=[]
    for family in set(families):
        event_time=row.received_at if source=='tradingview' else p.get('event_time') or (row.received_at if wt else row.event_time)
        plan=dict(event_id=row.id,event_time=event_time,entry=p.get('price') if p.get('price') is not None else p.get('entry'),sl=p.get('sl'),
            direction=p.get('direction'),t1=p.get('tp1',p.get('t1')),
            parameter_version=p.get('parameter_hash',getattr(row,'parameter_set_id',None)),
            engine_version=p.get('engine_version'),generation=p.get('setup_generation_id'),
            data_health=p.get('data_health'),path=p.get('trigger_path') or p.get('candidate_path'),
            time_basis='received_at_only' if source=='tradingview' else 'detector_event',
            original_plan_entry=p.get('entry'),signal_setups=p.get('signal_setups',[]))
        key=hashlib.sha256(json.dumps([POLICY,strategy,*identity,family],sort_keys=True).encode()).hexdigest()
        outcome=evaluate(plan,[],event_time)
        exchange=(row.exchange if wt else p.get('exchange','BYBIT')).upper()
        if exchange not in ('BYBIT','BINANCE'):outcome=dict(status='UNSUPPORTED',reason='No exchange adapter')
        items.append(Evaluation(id=key,policy=POLICY,strategy=strategy,source=source,mode='replay' if p.get('replay') else 'live',
            family=family,exchange=exchange,symbol=row.symbol,timeframe=row.timeframe,direction=p.get('direction','NONE'),
            event_time=event_time,updated_at=0,plan=plan,status=outcome['status'],outcome=outcome))
    return items
def capture(repo):
    counts={}
    for model,name,wt in ((Signal,'broke',False),(WTEvent,'wt',True)):
        with repo.session.begin() as s:
            cursor=s.get(CaptureCursor,name)
            if cursor is None:cursor=CaptureCursor(name=name,last_id=0);s.add(cursor)
            query=select(model).where(model.id>cursor.last_id)
            if not wt:query=query.where(Signal.name.in_(['LONG WATCH ENTRY','SHORT WATCH ENTRY','PINE READY LONG','PINE READY SHORT']))
            else:query=query.where(WTEvent.payload['signal_setups'] != [])
            rows=s.scalars(query.order_by(model.id).limit(25)).all()
            count=0
            for row in rows:
                for item in candidates(row,wt):
                    if s.get(Evaluation,item.id) is None:s.add(item);s.flush();count+=1
                cursor.last_id=row.id
            counts[name]=count
    return counts
