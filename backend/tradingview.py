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
from sqlalchemy import BigInteger, cast, delete, select, func
from backend.models.repository import now_ms
from backend.models.schema import TradingViewAlert, Signal
from backend.engine.level_campaign import is_campaign_event, normalize_symbol, process_campaign_snapshot

RETENTION_MS=30*24*60*60*1000
MAX_BODY=65536
HEADER=re.compile(r'^(.*?)\s*\|\s*(LONG|SHORT)\s*\|\s*([A-Za-z0-9_]+):([A-Za-z0-9_.!-]+)\s*\|\s*TF=(\w+)\s*(?:\||$)')

def signal_name(event,side):
    event=re.sub(r'^(?:REALTIME|BAR CLOSE|CONFIRMED CLOSE)\s*\|\s*','',event).strip()
    if is_campaign_event(event):return event
    for fragment,name in [('AVG SETUP >= 70','AVG SETUP >= 70'),('EXECUTION QUALITY >= 65','EXECUTION QUALITY >= 65'),('SETUP QUALITY BRONZE','BRONZE'),('SETUP QUALITY STRONG','STRONG')]:
        if fragment in event:return name
    fixed={'WATCH ENTRY':f'{side} WATCH ENTRY','WATCH ENTRY | EARLY':f'{side} WATCH ENTRY','ARMED':f'{side} ARMED','PINE READY':f'PINE READY {side}',
           'REVERSAL RISK':f'{side} REVERSAL RISK',
           'REVERSAL RISK SIGNAL':f'{side} REVERSAL RISK','ACTIVE PLAN EXIT / ABORT':'ACTIVE PLAN EXIT',
           'ACTIVE PLAN DEGRADED / NO ADD':'ACTIVE PLAN DEGRADED','ADD-ON CONFIRMED':'ADD-ON',
           'TP HIT':f'{side} TP HIT','ARMED LOST':f'{side} ARMED LOST','BOUNCE WATCH | OBSERVATION':f'{side} BOUNCE WATCH'}
    if event in fixed:return fixed[event]
    return event if event in {'L WATCH','S WATCH','BREAKOUT','BREAKDOWN','MATURED PRE-BREAK ENTRY'} else None


def structured_alert(envelope):
    """Versioned Pine reference message; it is not an execution envelope.

    v1 contains no confirmed 30m bias, reclaim geometry or order quantity.
    In particular, a campaign-named reference must not execute orders.
    """
    market=envelope.get('symbol')
    if not isinstance(market,str) or not re.fullmatch(r'[A-Za-z0-9_]{1,30}:[A-Za-z0-9_.!-]{1,60}',market):
        raise ValueError('Invalid structured alert market identifier')
    exchange,symbol=market.upper().split(':',1)
    timeframe=str(envelope.get('tf',''))
    if timeframe not in ('5','30'):raise ValueError('Structured alert timeframe must be 5 or 30')
    event=envelope.get('event')
    if not isinstance(event,str) or not 1<=len(event)<=200:raise ValueError('Invalid structured alert event')
    side=envelope.get('side')
    if side not in ('LONG','SHORT','SUPPORT','RESISTANCE'):raise ValueError('Invalid structured alert side')
    source_mode=envelope.get('mode')
    mode='BAR_CLOSE' if source_mode=='CONFIRMED' else source_mode
    if mode not in ('REALTIME','BAR_CLOSE'):raise ValueError('Invalid structured alert mode')
    direction=side if side in ('LONG','SHORT') else None
    name=signal_name(event,direction) if direction else event
    result={'parse_status':'parsed','time_basis':'received_at_only','measurements':{},
            'schema':envelope['schema'],'script':envelope.get('script'),'event_id':envelope.get('event_id'),
            'exchange':exchange,'symbol':symbol.removesuffix('.P'),'timeframe':timeframe,
            'event':event,'direction':direction,'side':side,'mode':mode,'source_mode':source_mode,'name':name or event,
            'reference_only':True}
    for source,target in [('close','price'),('avg','avg_setup'),('formation','formation'),
                          ('execution','execution'),('geometry','geometry'),('context','context'),
                          ('exhaustion','exhaustion'),('mae','mae'),('sl','sl'),('t1','t1'),('rr','rr')]:
        value=envelope.get(source)
        if type(value) in (int,float) and math.isfinite(value):result['measurements'][target]=value
    # Source bar time is encoded by Pine in the stable event ID, not in delivery time.
    prefix=f'{market}_{timeframe}_'
    event_id=envelope.get('event_id')
    if isinstance(event_id,str) and event_id.startswith(prefix):
        suffix=event_id[len(prefix):]
        match=re.fullmatch(r'(\d{13})_'+re.escape(event)+'_'+re.escape(side),suffix)
        if match:
            bar=int(match[1]);interval=int(timeframe)*60000
            if 946684800000<=bar<=now_ms()+interval and bar%interval==0:
                result.update(bar_start=bar,time_basis='source_event_id')
    return result

def campaign_payload(envelope):
    """Execution envelope. event_id and source bar time are never inferred from delivery."""
    if not isinstance(envelope.get('snapshot', {}), dict):raise ValueError('snapshot must be an object')
    s = {**envelope.get('snapshot', {}), **{k:v for k,v in envelope.items() if k not in ('snapshot','message')}}
    if not isinstance(s.get('bar',{}),dict):raise ValueError('bar must be an object')
    for field in ('supports','resistances'):
        if field in s and not isinstance(s[field],list):raise ValueError(f'{field} must be an array')
    if not isinstance(s.get('event_id'), str) or not 1 <= len(s['event_id']) <= 200:
        raise ValueError('Campaign execution requires a stable event_id (1..200 characters)')
    s['symbol'] = normalize_symbol(s.get('symbol',''))
    s['exchange'] = str(s.get('exchange','BYBIT')).upper()
    if not re.fullmatch(r'[A-Z0-9_]{1,30}',s['exchange']):raise ValueError('Invalid exchange')
    s['timeframe'] = str(s.get('timeframe',''))
    if s['timeframe'] not in ('5','30'):raise ValueError('Campaign timeframe must be 5 or 30')
    interval = int(s['timeframe'])*60000
    start = s.get('bar_start')
    if type(start) is not int or not 946684800000 <= start <= now_ms()+interval or start % interval:
        raise ValueError('Campaign bar_start must be an aligned UTC millisecond timestamp')
    price = s.get('price')
    if type(price) not in (int,float) or not math.isfinite(price) or price <= 0:
        raise ValueError('Campaign price must be a finite positive number')
    if 'mode' in s and s['mode'] not in ('REALTIME','BAR_CLOSE'):raise ValueError('Invalid campaign mode')
    for field in ('confirmed','direction_30m_confirmed'):
        if field in s and type(s[field]) is not bool:raise ValueError(f'{field} must be boolean')
    s.setdefault('event_time',start)
    if type(s['event_time']) is not int or not (start <= s['event_time'] < start+interval or s.get('confirmed') is True and s['event_time']==start+interval):
        raise ValueError('event_time must belong to the source bar')
    s['signal_source']='tradingview'
    return {'parse_status':'parsed','time_basis':'explicit_bar_start','measurements':{'price':price},
            **{k:s.get(k) for k in ('symbol','exchange','timeframe','bar_start','event','direction')},
            'name':s['event'],'campaign_snapshot':s}


def parse_body(raw):
    """Accept native dynamic text or {message, bar_start?} JSON envelope."""
    envelope={}
    try:
        decoded=json.loads(raw)
        if isinstance(decoded,dict):envelope=decoded
        message=decoded if isinstance(decoded,str) else envelope.get('message',raw)
    except ValueError:message=raw
    if envelope.get('schema')=='scalping_sma.alert.v1':return structured_alert(envelope)
    if is_campaign_event(envelope.get('event')) or envelope.get('event') == 'DIRECTION_30M':
        return campaign_payload(envelope)
    if not isinstance(message,str):message=raw
    match=HEADER.match(message)
    result={'parse_status':'unparsed','time_basis':'received_at_only','measurements':{}}
    if not match:
        result['parse_reason']=('Missing timeframe in OPEN/CLOSE alert; use TF=30 native text or versioned JSON'
            if re.match(r'^(?:OPEN|CLOSE)\s+(?:LONG|SHORT)\s*\|',message)
            else 'Unsupported alert format; expected native text or scalping_sma.alert.v1 JSON')
        return result
    event,side,exchange,symbol,timeframe=match.groups()
    if is_campaign_event(event.strip()):
        return campaign_payload({**envelope, 'event':event.strip(),'direction':side,
                                 'exchange':exchange,'symbol':symbol,'timeframe':timeframe})
    if timeframe!='30':raise ValueError('Only the 30-minute chart timeframe is accepted for reference alerts')
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
        result={'status':'stored','id':row.id,'parse_status':parsed['parse_status']}
        if parsed.get('campaign_snapshot'):
            result['execution']=process_campaign_snapshot(session,parsed['campaign_snapshot'],repo.settings(session),row.received_at)
        return result

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
                    query=query.where(cast(Signal.payload['bar_start'].as_string(),BigInteger)==row.bar_start)
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
