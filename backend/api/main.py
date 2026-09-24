from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
import os
import uuid
from fastapi import FastAPI,HTTPException,Query,Request,WebSocket,WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel,Field
from sqlalchemy import select,text
from backend.models.repository import Repository,now_ms,SCORES
from backend.models.schema import Current,Snapshot,Signal,Instrument,Event,MarketBar,Rule,RuleVersion,Delivery,ResearchSample,ParityResult,ServiceHealth
from backend.models.schema import ArchiveBatch
from backend.alerts.rules import AlertRuleInput,matches,validate_condition

class ParametersInput(BaseModel): values:dict
class SettingsInput(BaseModel):
    snapshot_interval_sec:float=Field(default=15,ge=1,le=86400)
    timezone_offset_minutes:int=Field(default=420,ge=-720,le=840,multiple_of=15,strict=True)
class RuleTestInput(BaseModel): conditions:dict

def create_app(repository:Repository|None=None):
    repo=repository or Repository()
    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(repo.initialize)
        yield
    app=FastAPI(title='Scalping SMA standalone',version='1.15.2',lifespan=lifespan)
    app.state.repo=repo

    @app.get('/api/health')
    def health():
        try:
            with repo.session() as s:
                s.execute(text('SELECT 1'))
                rows=s.scalars(select(ServiceHealth).where(ServiceHealth.name!='settings')).all()
            services={row.name:{**row.payload,'updated_at':row.updated_at,'stale':now_ms()-row.updated_at>90000} for row in rows}
            status='HEALTHY' if services and all(not row['stale'] and row.get('status')=='HEALTHY' for row in services.values()) else 'RECOVERING'
            return {'status':status,'services':{'database':{'status':'HEALTHY'},**services},'parity_status':'UNVERIFIED','time':now_ms()}
        except Exception:
            raise HTTPException(503,'Database unavailable')

    @app.get('/api/setups')
    def setups(request:Request,active_only:bool=True,search:str='',direction:str|None=None,timeframe:str|None=None,action:str|None=None,fsm:str|None=None,candidate_path:str|None=None,signal:str|None=None,exchange:str|None=None,sort:str='avg_setup:desc',limit:int=Query(500,ge=1,le=10000),offset:int=Query(0,ge=0)):
        with repo.session() as s: items=[row.payload for row in s.scalars(select(Current))]
        if active_only: items=[r for r in items if r.get('action') and r['action']!='WAIT SETUP']
        if search: items=[r for r in items if search.upper() in r['symbol'].upper()]
        for field,value in [('direction',direction),('timeframe',timeframe),('action',action),('fsm',fsm),('candidate_path',candidate_path),('exchange',exchange)]:
            if value is not None: items=[r for r in items if str(r.get(field))==value]
        if signal: items=[r for r in items if signal in r.get('signals',[])]
        for field in SCORES:
            for suffix,compare in [('min',lambda a,b:a>=b),('max',lambda a,b:a<=b)]:
                value=request.query_params.get(f'{field}_{suffix}')
                if value is not None:
                    try: number=float(value)
                    except ValueError: raise HTTPException(422,f'Invalid {field}_{suffix}')
                    items=[r for r in items if isinstance(r.get(field),(int,float)) and compare(r[field],number)]
        for part in reversed(sort.split(',')):
            field,_,order=part.partition(':')
            present=[r for r in items if r.get(field) is not None]; missing=[r for r in items if r.get(field) is None]
            try: present.sort(key=lambda r:r[field],reverse=order=='desc')
            except TypeError: present.sort(key=lambda r:str(r[field]),reverse=order=='desc')
            items=present+missing
        return {'items':items[offset:offset+limit],'total':len(items)}

    @app.get('/api/setups/{symbol}/{timeframe}')
    def detail(symbol:str,timeframe:str,exchange:str='BYBIT'):
        with repo.session() as s: row=s.get(Current,(exchange,symbol,timeframe))
        if row is None: raise HTTPException(404,'Setup not found')
        return row.payload

    @app.get('/api/setups/{symbol}/{timeframe}/history')
    def history(symbol:str,timeframe:str,exchange:str='BYBIT',since:int|None=None,until:int|None=None,limit:int=Query(1000,ge=1,le=10000)):
        query=select(Snapshot).where(Snapshot.exchange==exchange,Snapshot.symbol==symbol,Snapshot.timeframe==timeframe)
        if since is not None: query=query.where(Snapshot.event_time>=since)
        if until is not None: query=query.where(Snapshot.event_time<=until)
        with repo.session() as s: rows=s.scalars(query.order_by(Snapshot.event_time.desc(),Snapshot.id.desc()).limit(limit)).all()
        return {'items':[r.payload for r in reversed(rows)]}

    @app.get('/api/setups/{symbol}/{timeframe}/bars')
    def bars(symbol:str,timeframe:str,exchange:str='BYBIT',since:int|None=None,until:int|None=None,limit:int=Query(1000,ge=1,le=10000)):
        query=select(MarketBar).where(MarketBar.exchange==exchange,MarketBar.symbol==symbol,MarketBar.timeframe==timeframe)
        if since is not None: query=query.where(MarketBar.start>=since)
        if until is not None: query=query.where(MarketBar.start<=until)
        with repo.session() as s: rows=s.scalars(query.order_by(MarketBar.start.desc()).limit(limit)).all()
        items=[{c.name:getattr(row,c.name) for c in MarketBar.__table__.columns} for row in reversed(rows)]
        with repo.session() as session:current=session.get(Current,(exchange,symbol,timeframe))
        live=current.payload.get('bar') if current else None
        if live and (since is None or live['start']>=since) and (until is None or live['start']<=until):
            if items and items[-1]['start']==live['start'] and not items[-1]['confirmed']:items[-1]=live
            elif not items or live['start']>items[-1]['start']:items.append(live)
        return {'items':items[-limit:]}

    @app.get('/api/setups/{symbol}/{timeframe}/events')
    def events(symbol:str,timeframe:str,exchange:str='BYBIT',since:int|None=None,until:int|None=None,limit:int=Query(500,ge=1,le=10000)):
        query=select(Event).where(Event.exchange==exchange,Event.symbol==symbol,Event.timeframe==timeframe)
        if since is not None:query=query.where(Event.event_time>=since)
        if until is not None:query=query.where(Event.event_time<=until)
        with repo.session() as s: rows=s.scalars(query.order_by(Event.event_time.desc(),Event.id.desc()).limit(limit)).all()
        return {'items':[{'id':r.id,'kind':r.kind,'event_time':r.event_time,**r.payload} for r in rows]}

    @app.get('/api/signals')
    def signals(symbol:str|None=None,timeframe:str|None=None,since:int|None=None,limit:int=Query(500,ge=1,le=10000)):
        query=select(Signal)
        if symbol: query=query.where(Signal.symbol==symbol)
        if timeframe: query=query.where(Signal.timeframe==timeframe)
        if since is not None: query=query.where(Signal.event_time>=since)
        with repo.session() as s: rows=s.scalars(query.order_by(Signal.event_time.desc(),Signal.id.desc()).limit(limit)).all()
        return {'items':[{'id':r.id,'name':r.name,**r.payload} for r in rows]}

    @app.get('/api/instruments')
    def instruments():
        with repo.session() as s: rows=s.scalars(select(Instrument).order_by(Instrument.symbol)).all()
        return {'items':[r.payload for r in rows]}

    @app.get('/api/parameters')
    def parameters(): return repo.parameters()
    @app.put('/api/parameters')
    def set_parameters(body:ParametersInput):
        try: return repo.set_parameters(body.values)
        except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc))
    @app.get('/api/settings')
    def settings(): return repo.settings()
    @app.put('/api/settings')
    def set_settings(body:SettingsInput): return repo.set_settings(body.model_dump(exclude_unset=True))

    @app.get('/api/alerts/fields')
    def alert_fields():
        from backend.alerts.fields import field_catalog
        return field_catalog()

    @app.get('/api/alerts/rules')
    def rules():
        with repo.session() as s: rows=s.scalars(select(Rule)).all()
        return {'items':[{'id':r.id,'version':r.version,**r.payload} for r in rows]}
    @app.post('/api/alerts/rules',status_code=201)
    def create_rule(body:AlertRuleInput):
        key=str(uuid.uuid4()); payload=body.model_dump()
        with repo.session.begin() as s:
            s.add(Rule(id=key,version=1,enabled=body.enabled,payload=payload))
            s.add(RuleVersion(rule_id=key,version=1,created_at=now_ms(),payload=payload))
        return {'id':key,'version':1,**payload}
    @app.post('/api/alerts/rules/test')
    def test_rule(body:RuleTestInput):
        try: validate_condition(body.conditions)
        except ValueError as exc: raise HTTPException(422,str(exc))
        with repo.session() as s: items=[r.payload for r in s.scalars(select(Current))]
        matched=[r for r in items if matches(body.conditions,r) or any(matches(body.conditions,{**r,'event':event}) for event in r.get('signals',[]))]
        return {'items':matched,'total':len(matched),'note':'Crossing/change rules require prior state; this preview evaluates current snapshots only.'}
    @app.put('/api/alerts/rules/{rule_id}')
    def update_rule(rule_id:str,body:AlertRuleInput):
        with repo.session.begin() as s:
            row=s.get(Rule,rule_id,with_for_update=True)
            if row is None: raise HTTPException(404,'Rule not found')
            row.version+=1; row.enabled=body.enabled; row.payload=body.model_dump()
            s.add(RuleVersion(rule_id=rule_id,version=row.version,created_at=now_ms(),payload=row.payload))
            result={'id':row.id,'version':row.version,**row.payload}
        return result
    @app.delete('/api/alerts/rules/{rule_id}')
    def delete_rule(rule_id:str):
        with repo.session.begin() as s:
            row=s.get(Rule,rule_id,with_for_update=True)
            if row is None: raise HTTPException(404,'Rule not found')
            s.add(RuleVersion(rule_id=rule_id,version=row.version+1,created_at=now_ms(),payload={**row.payload,'deleted':True}))
            s.delete(row)
        return {'deleted':rule_id}
    @app.get('/api/alerts/deliveries')
    def deliveries(limit:int=Query(100,ge=1,le=1000)):
        with repo.session() as s: rows=s.scalars(select(Delivery).order_by(Delivery.created_at.desc()).limit(limit)).all()
        return {'items':[{'id':r.id,'rule_id':r.rule_id,'status':r.status,'created_at':r.created_at,'attempts':r.attempts,'error':r.error,'payload':r.payload} for r in rows]}
    @app.post('/api/alerts/test-telegram')
    def test_telegram():
        if not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID'): raise HTTPException(409,'Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first')
        from backend.alerts.outbox import digest
        now=now_ms()
        with repo.session.begin() as s:
            s.add(Delivery(dedupe_key=digest(['test',str(uuid.uuid4())]),rule_id='manual-test',rule_version=1,created_at=now,updated_at=now,next_attempt=now,status='pending',payload={'text':'Scalping SMA: connection test requested in the web interface.'}))
        return {'status':'queued'}

    @app.get('/api/research')
    def research(symbol:str|None=None,family:str|None=None,limit:int=Query(1000,ge=1,le=10000)):
        query=select(ResearchSample)
        if symbol: query=query.where(ResearchSample.symbol==symbol)
        if family: query=query.where(ResearchSample.family==family)
        with repo.session() as s: rows=s.scalars(query.order_by(ResearchSample.event_time.desc()).limit(limit)).all()
        with repo.session() as s: current=s.scalars(select(Current)).all()
        stats=[{'exchange':r.exchange,'symbol':r.symbol,'timeframe':r.timeframe,'stats':r.payload.get('research',{}).get('stats',[]),'dropped':r.payload.get('research',{}).get('dropped',0)} for r in current if not symbol or r.symbol==symbol]
        return {'items':[{'id':r.id,'symbol':r.symbol,'timeframe':r.timeframe,'family':r.family,'parameter_set_id':r.parameter_set_id,**r.payload} for r in rows],'pine_stats':stats,'source':'Pine engine research records','parity_status':'UNVERIFIED'}
    @app.get('/api/parity')
    def parity():
        with repo.session() as s: row=s.scalar(select(ParityResult).order_by(ParityResult.created_at.desc()).limit(1))
        if row: return row.payload
        default_report=next((p for p in ('reports/context-parity.json','reports/full-parity.json','reports/parity.json') if os.path.isfile(p)),'reports/parity.json')
        path=os.getenv('PARITY_REPORT_PATH',default_report)
        try:
            with open(path) as f: return json.load(f)
        except FileNotFoundError: pass
        return {'status':'UNVERIFIED','metrics':[],'signals':[],'reason':'No external TradingView reference dataset has been validated.'}
    @app.get('/api/parity/intrabar')
    def intrabar_parity(include_sessions:bool=False):
        path=os.getenv('INTRABAR_REPORT_PATH','reports/intrabar-pool-ad4e4.json')
        try:
            with open(path) as f:report=json.load(f)
        except FileNotFoundError:
            return {'status':'UNVERIFIED','parity_status':'UNVERIFIED','sessions':0,'reason':'No intrabar pool report available.'}
        return report if include_sessions else {k:v for k,v in report.items() if k!='items'}
    @app.get('/api/storage')
    def storage(limit:int=Query(50,ge=1,le=500)):
        from backend.partitions import inventory
        with repo.session() as session:
            service=session.get(ServiceHealth,'retention')
            archives=session.scalars(select(ArchiveBatch).order_by(ArchiveBatch.created_at.desc(),ArchiveBatch.id).limit(limit)).all()
            return {'retention':service.payload if service else {'status':'NOT_STARTED'},'partitioning':inventory(repo),
                    'archives':[{column.name:getattr(row,column.name) for column in ArchiveBatch.__table__.columns} for row in archives]}

    @app.get('/metrics',response_class=PlainTextResponse)
    def metrics():
        with repo.session() as s:
            current=s.scalars(select(Current)).all(); services=s.scalars(select(ServiceHealth)).all()
            failed=len(s.scalars(select(Delivery.id).where(Delivery.status.in_(['failed','uncertain']))).all())
        lines=['# TYPE scalping_active_setups gauge',f'scalping_active_setups {sum(r.payload.get("action")!="WAIT SETUP" for r in current)}',f'scalping_telegram_failures {failed}']
        names=('processed_market_messages','market_data_lag_ms','calculations','calculations_per_second','calculation_latency_ms','checkpoint_export_latency_ms','websocket_reconnects','db_write_latency_ms','stale_instruments','parity_failures')
        for name in names:
            for service in services:
                value=service.payload.get(name)
                if isinstance(value,(int,float)): lines.append(f'scalping_{name}{{service="{service.name}"}} {value}')
        return '\n'.join(lines)+'\n'

    @app.websocket('/ws/setups')
    async def websocket(ws:WebSocket):
        await ws.accept()
        def initial():
            with repo.session() as s: return [r.payload for r in s.scalars(select(Current))]
        client=None; pubsub=None
        try:
            from redis.asyncio import Redis
            client=Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'),decode_responses=True,socket_connect_timeout=3,socket_timeout=5)
            pubsub=client.pubsub(); await pubsub.subscribe('setups')
            for item in await asyncio.to_thread(initial): await ws.send_json({'type':'snapshot','data':item})
            while True:
                message=await pubsub.get_message(ignore_subscribe_messages=True,timeout=15)
                if message:
                    payload=json.loads(message['data'])
                    await ws.send_json(payload if payload.get('type') else {'type':'snapshot','data':payload})
                else: await ws.send_json({'type':'heartbeat','time':now_ms()})
        except WebSocketDisconnect: pass
        except Exception:
            try: await ws.send_json({'type':'health','status':'DEGRADED','reason':'Realtime connection unavailable'}); await ws.close(code=1013)
            except Exception: pass
        finally:
            if pubsub: await pubsub.aclose()
            if client: await client.aclose()
    return app

app=create_app()
