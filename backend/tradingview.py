"""TradingView reference inbox. Delivery time is never treated as bar time."""
from __future__ import annotations
import hashlib
import json
import math
import re
import secrets
import os
from collections import Counter
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import delete, select, func
from backend.models.repository import now_ms
from backend.models.schema import TradingViewAlert, Signal

RETENTION_MS=30*24*60*60*1000
MAX_BODY=65536
HEADER=re.compile(r'^(.*?)\s*\|\s*(LONG|SHORT)\s*\|\s*([A-Za-z0-9_]+):([A-Za-z0-9_.!-]+)\s*\|\s*TF=(\w+)\s*(?:\||$)')

def signal_name(event,side):
    for fragment,name in [('AVG SETUP >= 70','AVG SETUP >= 70'),('EXECUTION QUALITY >= 65','EXECUTION QUALITY >= 65'),('SETUP QUALITY BRONZE','BRONZE'),('SETUP QUALITY STRONG','STRONG')]:
        if fragment in event:return name
    fixed={'WATCH ENTRY | EARLY':f'{side} WATCH ENTRY','ARMED':f'{side} ARMED','PINE READY':f'PINE READY {side}',
           'REVERSAL RISK SIGNAL':f'{side} REVERSAL RISK','ACTIVE PLAN EXIT / ABORT':'ACTIVE PLAN EXIT',
           'ACTIVE PLAN DEGRADED / NO ADD':'ACTIVE PLAN DEGRADED','ADD-ON CONFIRMED':'ADD-ON',
           'TP HIT':f'{side} TP HIT','ARMED LOST':f'{side} ARMED LOST','BOUNCE WATCH | OBSERVATION':f'{side} BOUNCE WATCH'}
    if event in fixed:return fixed[event]
    return event if event in {'L WATCH','S WATCH','BREAKOUT','BREAKDOWN','MATURED PRE-BREAK ENTRY'} else None

def parse_body(raw):
    """Accept native dynamic text or {message, bar_start?} JSON envelope."""
    envelope={}
    try:
        decoded=json.loads(raw)
        if isinstance(decoded,dict):envelope=decoded
        message=decoded if isinstance(decoded,str) else envelope.get('message',raw)
    except ValueError:message=raw
    if not isinstance(message,str):message=raw
    match=HEADER.match(message)
    result={'parse_status':'unparsed','time_basis':'received_at_only','measurements':{}}
    if not match:return result
    event,side,exchange,symbol,timeframe=match.groups()
    if timeframe!='30':raise ValueError('Only the 30-minute chart timeframe is accepted')
    if len(exchange)>30 or len(symbol)>60:raise ValueError('Invalid market identifier')
    result.update(exchange=exchange.upper(),symbol=symbol.upper().removesuffix('.P'),timeframe=timeframe,
                  event=event.strip(),direction=side,name=signal_name(event.strip(),side),parse_status='parsed')
    if 'bar_start' in envelope:
        bar=envelope['bar_start']
        if type(bar) is not int or not 946684800000<=bar<=now_ms()+1800000 or bar%1800000:
            raise ValueError('bar_start must be a UTC millisecond timestamp aligned to a 30m bar')
        result.update(bar_start=bar,time_basis='explicit_bar_start')
    fields={part.split('=',1)[0].strip():part.split('=',1)[1].strip() for part in message[match.end():].split('|') if '=' in part}
    for source,target in [('Close','price'),('AVG','avg_setup'),('SL','sl'),('TP','t1'),('RR','rr')]:
        try:
            value=float(fields[source])
            if math.isfinite(value):result['measurements'][target]=value
        except (KeyError,ValueError):pass
    for source,targets in [('F/E',('formation','execution')),('Ex/MAE',('exhaustion','mae'))]:
        try:
            values=[float(v) for v in fields[source].split('/')]
            if len(values)==2 and all(math.isfinite(v) for v in values):result['measurements'].update(zip(targets,values))
        except (KeyError,ValueError):pass
    result['fields']=fields
    return result

def save_alert(repo,raw):
    parsed=parse_body(raw)
    with repo.session.begin() as session:
        row=TradingViewAlert(received_at=now_ms(),raw_body=raw,body_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            **{key:parsed.get(key) for key in ('exchange','symbol','timeframe','bar_start','name')},payload=parsed)
        session.add(row);session.flush()
        return {'status':'stored','id':row.id,'parse_status':parsed['parse_status']}

def cleanup(repo,now=None):
    cutoff=(now_ms() if now is None else now)-RETENTION_MS
    with repo.session.begin() as session:
        # Bound each transaction; the next hourly run continues a large backlog.
        ids=select(TradingViewAlert.id).where(TradingViewAlert.received_at<cutoff).order_by(TradingViewAlert.received_at).limit(10000)
        return session.execute(delete(TradingViewAlert).where(TradingViewAlert.id.in_(ids))).rowcount

def serialize(row):
    return {'id':row.id,'received_at':row.received_at,'raw_body':row.raw_body,'body_sha256':row.body_sha256,**row.payload}

def compare(repo,limit):
    with repo.session() as session:
        rows=session.scalars(select(TradingViewAlert).where(TradingViewAlert.received_at>=now_ms()-RETENTION_MS).order_by(TradingViewAlert.id.desc()).limit(limit)).all()
        items=[]
        for row in rows:
            item={'id':row.id,'received_at':row.received_at,'symbol':row.symbol,'name':row.name,'time_basis':row.payload['time_basis'],'status':'UNPARSED','candidates':[]}
            if row.name:
                query=select(Signal).where(Signal.symbol==row.symbol,Signal.timeframe==row.timeframe,Signal.name==row.name)
                if row.bar_start is None:
                    query=query.where(Signal.event_time.between(row.received_at-90000,row.received_at+90000))
                else:
                    # SQL JSON extraction works on PostgreSQL and SQLite; indexed market/name bounds keep this narrow.
                    query=query.where(Signal.payload['bar_start'].as_integer()==row.bar_start)
                candidates=session.scalars(query.order_by(Signal.event_time).limit(100)).all()
                candidates=[s for s in candidates if s.payload.get('exchange')==row.exchange and s.payload.get('direction')==row.payload.get('direction')]
                item['candidates']=[{'id':s.id,'event_time':s.event_time,'bar_start':s.payload.get('bar_start'),'parameter_set_id':s.parameter_set_id,
                    'measurement_deltas':{k:s.payload[k]-v for k,v in row.payload['measurements'].items() if type(s.payload.get(k)) in (int,float)}} for s in candidates]
                item['status']=('SIGNAL_MATCH' if candidates else 'SIGNAL_NOT_FOUND') if row.bar_start is not None else ('TIME_CANDIDATE' if candidates else 'NO_TIME_CANDIDATE')
            items.append(item)
    return {'status':'UNVERIFIED','counts':dict(Counter(i['status'] for i in items)),'items':items,
            'reason':'Native alert text has no bar timestamp or parameter identity. ±90s delivery-time candidates are diagnostic only. Explicit bar matches check signal presence only; rounded metric deltas do not establish full parity. Repeated deliveries remain separate records.'}

def router(repo):
    routes=APIRouter()
    @routes.post('/api/webhooks/tradingview',status_code=201)
    async def receive(request:Request):
        expected=os.getenv('TRADINGVIEW_WEBHOOK_KEY','')
        if not expected:raise HTTPException(503,'TradingView webhook is not configured')
        supplied=request.query_params.get('key','')
        if not secrets.compare_digest(supplied.encode(),expected.encode()):raise HTTPException(401,'Invalid webhook key')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>MAX_BODY:raise HTTPException(413,'Webhook body exceeds 64 KiB')
        try:
            raw=body.decode('utf-8')
            if not raw.strip():raise ValueError('Empty webhook body')
            import asyncio
            return await asyncio.to_thread(save_alert,repo,raw)
        except (UnicodeError,ValueError) as exc:raise HTTPException(422,str(exc))

    @routes.get('/api/tradingview/alerts')
    def alerts(limit:int=Query(100,ge=1,le=1000),offset:int=Query(0,ge=0),symbol:str|None=None):
        query=select(TradingViewAlert).where(TradingViewAlert.received_at>=now_ms()-RETENTION_MS)
        if symbol:query=query.where(TradingViewAlert.symbol==symbol.upper().removesuffix('.P'))
        with repo.session() as session:
            total=session.scalar(select(func.count()).select_from(query.subquery()))
            rows=session.scalars(query.order_by(TradingViewAlert.id.desc()).offset(offset).limit(limit)).all()
        return {'items':[serialize(row) for row in rows],'total':total,'retention_days':30}

    @routes.get('/api/parity/tradingview')
    def parity(limit:int=Query(100,ge=1,le=1000)):return compare(repo,limit)
    return routes
