"""WT persistence, REST and authenticated TradingView input."""
import asyncio
import json
import math
import os
import re
import secrets
import uuid
from itertools import combinations
from fastapi import APIRouter,HTTPException,Query,Request
from pydantic import BaseModel
from sqlalchemy import select,delete,text,func
from backend.models.repository import now_ms
from backend.models.schema import WTCurrent,WTEvent,Delivery,ServiceHealth
from backend.alerts.outbox import digest,enqueue_matching
from backend.engine.wt import parameters,input_schema

RETENTION_MS=30*86400000

def config(repo):
    with repo.session() as s:row=s.get(ServiceHealth,'wt-settings')
    values=parameters(row.payload['values'] if row else {})
    return {'id':digest(values),'values':values,'schema':input_schema()}

def save_current(session,current,now):
    key=tuple(current[k] for k in ('exchange','symbol','timeframe','signal_source'))
    old=session.get(WTCurrent,key,with_for_update=True);previous=old.payload if old else None
    enqueue_matching(session,current,previous,now)
    if old:old.payload=current;old.updated_at=now
    else:session.add(WTCurrent(**dict(zip(('exchange','symbol','timeframe','signal_source'),key)),updated_at=now,payload=current))
    return previous

def save_engine(session,current,now):
    previous=save_current(session,current,now)
    # WT stores its own attributes, never another copy of candles.
    significant=not previous or current.get('signals') or current['confirmed'] or current.get('action')!=previous.get('action')
    if not significant:return
    key=digest(['wt-engine',current['symbol'],current['timeframe'],current['bar_start'],current['confirmed'],current.get('setup_generation_id'),current.get('action'),current['signals'],current['parameter_hash']])
    if session.scalar(select(WTEvent.id).where(WTEvent.dedupe_key==key)) is None:
        session.add(WTEvent(dedupe_key=key,received_at=now,**{k:current[k] for k in ('exchange','symbol','timeframe','signal_source')},payload=current))

def parse_message(raw,exchange):
    envelope={}
    try:
        decoded=json.loads(raw)
        if isinstance(decoded,dict):envelope=decoded
        message=decoded if isinstance(decoded,str) else envelope.get('message',raw)
    except ValueError:message=raw
    if not isinstance(message,str):raise ValueError('message must be text')
    match=re.match(r'^WT (READY TO ENTER )?(LONG|SHORT)\s*\|\s*([A-Za-z0-9_.:!-]+)\s*\|\s*TF=(\w+)\s*\|\s*((?:T[1-4][ +]*)+)\|',message.strip())
    if not match:raise ValueError('Expected WT LONG/SHORT or WT READY TO ENTER message with T1–T4 IDs; use Any alert() function call')
    ready,side,symbol,tf,ids=match.groups()
    if tf!='30':raise ValueError('WT webhook requires a 30-minute chart')
    if ':' in symbol:
        explicit,symbol=symbol.split(':',1)
        if explicit.upper()!=exchange:raise ValueError('Message exchange differs from webhook exchange')
    symbol=symbol.upper().removesuffix('.P')
    if not re.fullmatch(r'[A-Z0-9_]{1,60}',symbol):raise ValueError('Invalid symbol')
    setups=sorted(set(re.findall(r'T[1-4]',ids)));fields={p.split('=',1)[0].strip():p.split('=',1)[1].strip() for p in message[match.end():].split('|') if '=' in p}
    signals=['+'.join(c) for n in range(1,len(setups)+1) for c in combinations(setups,n)]
    if ready:signals.append('READY TO ENTER')
    now=now_ms();bar=envelope.get('bar_start')
    if bar is not None and (type(bar) is not int or bar%1800000 or not 946684800000<=bar<=now+1800000):raise ValueError('bar_start must be an aligned UTC millisecond timestamp')
    event_id=envelope.get('event_id')
    if event_id is not None and (not isinstance(event_id,str) or not 1<=len(event_id)<=160):raise ValueError('Invalid event_id')
    result={'strategy':'WT_SETUPS','signal_source':'tradingview','exchange':exchange,'symbol':symbol,'timeframe':tf,'direction':side,
        'setups':setups,'signal_setups':setups,'setup_combination':'+'.join(setups),'signals':signals,'action':'ENTER NOW' if ready else fields.get('Action','SIGNAL'),
        'event_time':now,'received_at':now,'bar_start':bar,'time_basis':'explicit_bar_start' if bar is not None else 'received_at_only',
        'confirmed':False,'data_health':'HEALTHY','parity_status':'UNVERIFIED','raw_body':raw,'fields':fields,'source_event_id':event_id,
        'setup_generation_id':event_id or str(uuid.uuid4())}
    for source,target in [('Score','score'),('EQ','entry_quality'),('Entry','entry'),('Price','price'),('PlanEntry','entry'),('SL','sl'),('TP1','tp1'),('LIQ','liquidity_target'),('RRliq','rr_liquidity'),('Pos','position_usdt'),('Risk','risk_usdt')]:
        try:
            value=float(fields[source].split()[0])
            if math.isfinite(value):result[target]=value
        except (KeyError,ValueError,IndexError):pass
    result.setdefault('price',result.get('entry'))
    return result

def receive(repo,raw,exchange):
    current=parse_message(raw,exchange);now=current['received_at'];identity=digest([exchange,raw]);event_id=current['source_event_id']
    with repo.session.begin() as s:
        if repo.engine.dialect.name=='postgresql':s.execute(text('SELECT pg_advisory_xact_lock(431581)'))
        if event_id:
            key=digest(['wt-tv',exchange,event_id]);old=s.scalar(select(WTEvent).where(WTEvent.dedupe_key==key))
        else:
            key=digest(['wt-tv',str(uuid.uuid4())]);old=s.scalar(select(WTEvent).where(WTEvent.signal_source=='tradingview',WTEvent.received_at>=now-15000,WTEvent.payload['body_hash'].as_string()==identity).order_by(WTEvent.id.desc()).limit(1))
        if old and event_id:
            if old.payload['body_hash']!=identity:raise ValueError('event_id reused with a different message')
            return {'status':'duplicate','id':old.id,'telegram':'not_requeued'}
        current['body_hash']=identity
        if old:current['duplicate_of']=old.id
        row=WTEvent(dedupe_key=key,received_at=now,**{k:current[k] for k in ('exchange','symbol','timeframe','signal_source')},payload=current)
        s.add(row);s.flush()
        if not old:
            current['reference_id']=row.id;row.payload=dict(current)
            save_current(s,current,now)
            if os.getenv('WT_TRADINGVIEW_TELEGRAM_ENABLED','true').lower()=='true':
                message=f"WT · TradingView · {current['setup_combination']} · {current['direction']}\n{current['exchange']}:{current['symbol']} · TF {current['timeframe']}\nACTION: {current['action']}\n"
                message+='\n'.join(f'{label}: {current.get(key,"n/a")}' for label,key in [('Score','score'),('EQ','entry_quality'),('Price','price'),('Entry','entry'),('SL','sl'),('TP1','tp1'),('LIQ','liquidity_target'),('Pos USDT','position_usdt'),('Risk USDT','risk_usdt')])
                s.add(Delivery(dedupe_key=digest(['wt-tv-delivery',row.id]),rule_id='wt-tradingview',rule_version=1,created_at=now,updated_at=now,next_attempt=now,status='pending',payload={'text':message,'wt_reference_id':row.id,'snapshot':current}))
        return {'status':'duplicate' if old else 'stored','id':row.id,'telegram':'not_requeued' if old else 'queued' if os.getenv('WT_TRADINGVIEW_TELEGRAM_ENABLED','true').lower()=='true' else 'disabled'}

def cleanup(repo,now=None):
    cutoff=(now or now_ms())-RETENTION_MS
    with repo.session.begin() as s:
        ids=select(WTEvent.id).where(WTEvent.received_at<cutoff).order_by(WTEvent.received_at).limit(10000)
        count=s.execute(delete(WTEvent).where(WTEvent.id.in_(ids))).rowcount
        s.execute(delete(WTCurrent).where(WTCurrent.signal_source=='tradingview',WTCurrent.updated_at<cutoff))
        # Remove the webhook message copies in the outbox under the same retention policy.
        s.execute(delete(Delivery).where(Delivery.rule_id=='wt-tradingview',Delivery.created_at<cutoff))
        return count

class WTParametersInput(BaseModel):values:dict

def router(repo):
    routes=APIRouter()
    @routes.get('/api/wt/parameters')
    def get_parameters():return config(repo)
    @routes.put('/api/wt/parameters')
    def put_parameters(body:WTParametersInput):
        try:values=parameters(body.values)
        except ValueError as exc:raise HTTPException(422,str(exc))
        with repo.session.begin() as s:
            row=s.get(ServiceHealth,'wt-settings')
            if row:row.payload={'values':values};row.updated_at=now_ms()
            else:s.add(ServiceHealth(name='wt-settings',updated_at=now_ms(),payload={'values':values}))
        return config(repo)
    @routes.get('/api/wt/setups')
    def setups(signal_source:str='engine',active_only:bool=False):
        with repo.session() as s:rows=s.scalars(select(WTCurrent).where(WTCurrent.signal_source==signal_source)).all()
        items=[{**r.payload,'updated_at':r.updated_at,'data_health':'STALE' if now_ms()-r.updated_at>90000 and signal_source=='engine' else r.payload['data_health']} for r in rows]
        if active_only:items=[r for r in items if r.get('action') not in ('WAIT SIGNAL','SKIP','TOO LATE')]
        return {'items':items,'total':len(items)}
    @routes.get('/api/wt/events')
    def events(symbol:str|None=None,signal_source:str|None=None,limit:int=Query(100,ge=1,le=1000),offset:int=Query(0,ge=0)):
        query=select(WTEvent).where(WTEvent.received_at>=now_ms()-RETENTION_MS)
        if symbol:query=query.where(WTEvent.symbol==symbol)
        if signal_source:query=query.where(WTEvent.signal_source==signal_source)
        with repo.session() as s:
            total=s.scalar(select(func.count()).select_from(query.subquery()))
            rows=s.scalars(query.order_by(WTEvent.id.desc()).offset(offset).limit(limit)).all()
        return {'items':[{'id':r.id,**r.payload} for r in rows],'total':total}
    @routes.post('/api/webhooks/tradingview/wt',status_code=201)
    async def webhook(request:Request,exchange:str='BYBIT'):
        expected=os.getenv('WT_TRADINGVIEW_WEBHOOK_KEY','')
        if not expected:raise HTTPException(503,'WT webhook is not configured')
        if not secrets.compare_digest(request.query_params.get('key','').encode(),expected.encode()):raise HTTPException(401,'Invalid webhook key')
        exchange=exchange.upper()
        if exchange not in ('BYBIT','BINANCE'):raise HTTPException(422,'Supported exchange: BYBIT or BINANCE')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>65536:raise HTTPException(413,'Webhook body exceeds 64 KiB')
        try:return await asyncio.to_thread(receive,repo,body.decode('utf-8'),exchange)
        except (ValueError,UnicodeError) as exc:raise HTTPException(422,str(exc))
    return routes
