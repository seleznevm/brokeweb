from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
import os
import uuid
import logging
from typing import Literal
from fastapi import FastAPI,HTTPException,Query,Request,WebSocket,WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel,Field,ConfigDict,model_validator
from sqlalchemy import select,text,func,cast,Float,BigInteger
from backend.models.repository import Repository,now_ms,SCORES
from backend.models.schema import Current,Snapshot,Signal,Instrument,Event,MarketBar,Rule,RuleVersion,Delivery,ResearchSample,ParityResult,ServiceHealth,BrokePBPosition
from backend.models.schema import ArchiveBatch
from backend.alerts.rules import AlertRuleInput,matches,validate_condition
from backend.redaction import redact_telegram_tokens

class ParametersInput(BaseModel): values:dict
class SettingsInput(BaseModel):
    model_config=ConfigDict(extra='forbid', strict=True)
    universe_min_turnover24h_usdt:float|None=Field(default=None,ge=0,le=1e12,allow_inf_nan=False)
    snapshot_interval_sec:float|None=Field(default=None,ge=1,le=86400)
    timezone_offset_minutes:int|None=Field(default=None,ge=-720,le=840,multiple_of=15)
    telegram_bot_token:str|None=None
    telegram_chat_id:str|None=None
    telegram_topic_id:str|None=None
    broke_pb_position_usdt:float|None=Field(default=None,ge=1,le=1000000,allow_inf_nan=False)
    campaign_enabled:bool|None=None
    campaign_signal_mode:Literal['REALTIME','BAR_CLOSE']|None=None
    campaign_exit_policy:Literal['CONTEXT_30M','STRUCTURAL']|None=None
    campaign_execution_mode:Literal['PAPER','DEMO','LIVE']|None=None
    campaign_risk_usdt:float|None=Field(default=None,gt=0,le=1000000,allow_inf_nan=False)
    campaign_portfolio_risk_usdt:float|None=Field(default=None,gt=0,le=10000000,allow_inf_nan=False)
    campaign_fee_rate:float|None=Field(default=None,ge=0,le=.02,allow_inf_nan=False)
    campaign_slippage_bps:float|None=Field(default=None,ge=0,le=500,allow_inf_nan=False)
    campaign_spread_bps:float|None=Field(default=None,ge=0,le=500,allow_inf_nan=False)
    campaign_be_buffer_bps:float|None=Field(default=None,ge=0,le=500,allow_inf_nan=False)
    campaign_bias_max_age_sec:int|None=Field(default=None,ge=1800,le=86400)
    campaign_price_max_age_sec:int|None=Field(default=None,ge=1,le=3600)
    campaign_tp1_fraction:float|None=Field(default=None,gt=0,le=1,allow_inf_nan=False)
    campaign_max_tranches:Literal[1,3]|None=None
    broke_pb_telegram_bot_token:str|None=None
    broke_pb_telegram_chat_id:str|None=None
    broke_pb_telegram_topic_id:str|None=None
    broke_pb_pm_telegram_enabled:bool|None=None
    broke_pb_pm_telegram_bot_token:str|None=None
    broke_pb_pm_telegram_chat_id:str|None=None
    broke_pb_pm_telegram_topic_id:str|None=None
    broke_execution_gate_enabled:bool|None=None
    broke_execution_notional_usdt:float|None=Field(default=None,gt=0,le=1000000,allow_inf_nan=False)
    broke_execution_max_spread_bps:float|None=Field(default=None,ge=0,le=500,allow_inf_nan=False)
    broke_execution_max_slippage_bps:float|None=Field(default=None,ge=0,le=500,allow_inf_nan=False)
    broke_execution_max_age_sec:int|None=Field(default=None,ge=1,le=60)
    broke_execution_max_symbols:int|None=Field(default=None,ge=1,le=100)
    broke_fee_rate:float|None=Field(default=None,ge=0,le=.02,allow_inf_nan=False)
    broke_slippage_bps:float|None=Field(default=None,ge=0,le=500,allow_inf_nan=False)
    broke_spread_bps:float|None=Field(default=None,ge=0,le=500,allow_inf_nan=False)
    broke_late_watch_enabled:bool|None=None
    broke_late_watch_window_bars:int|None=Field(default=None,ge=1,le=12)

    @model_validator(mode='before')
    @classmethod
    def check_not_null(cls, data):
        if isinstance(data, dict):
            from backend.setups_config import DEFAULTS as broke_defaults
            if any(k in data and data[k] is None for k in broke_defaults):
                raise ValueError('BROKE settings cannot be null')
            for k in ('universe_min_turnover24h_usdt', 'snapshot_interval_sec', 'timezone_offset_minutes', 'broke_pb_position_usdt',
                      'campaign_fee_rate','campaign_slippage_bps','campaign_spread_bps','campaign_be_buffer_bps','campaign_bias_max_age_sec',
                      'campaign_price_max_age_sec','campaign_tp1_fraction','campaign_max_tranches',
                      'campaign_execution_mode','campaign_signal_mode','campaign_exit_policy','campaign_enabled'):
                if k in data and data[k] is None:
                    raise ValueError(f'{k} cannot be null')
            if data.get('campaign_execution_mode')=='LIVE':
                raise ValueError('LIVE_EXECUTOR_UNAVAILABLE: use PAPER or DEMO')
        return data

class SettingsFileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    format:Literal['brokeweb-settings']
    version:Literal[1]
    exported_at:int|None=None
    settings:dict=Field(default_factory=dict)
    parameters:dict|None=None

class TestTelegramInput(BaseModel):
    target:Literal['broke_we', 'broke_pb', 'broke_pb_pm']='broke_we'

class RuleTestInput(BaseModel):
    conditions:dict
    strategy:Literal['BROKE_SETUPS']='BROKE_SETUPS'
class RuleFileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    format:Literal['brokeweb-alert-rules']
    version:Literal[1]
    rules:list[AlertRuleInput]=Field(min_length=1,max_length=500)

class WebhookAccessFilter(logging.Filter):
    def filter(self,record):
        if isinstance(record.args,tuple) and len(record.args)>=3 and str(record.args[2]).startswith('/api/webhooks/tradingview'):
            args=list(record.args);args[2]='/api/webhooks/tradingview';record.args=tuple(args)
        return True

logging.getLogger('uvicorn.access').addFilter(WebhookAccessFilter())

def create_app(repository:Repository|None=None):
    repo=repository or Repository()
    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(repo.initialize)
        from backend.tradingview import cleanup
        stop=asyncio.Event()
        async def retention():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(cleanup,repo)
                except Exception:logging.getLogger(__name__).exception('TradingView retention failed')
                try:await asyncio.wait_for(stop.wait(),timeout=3600)
                except TimeoutError:pass
        task=asyncio.create_task(retention())
        try:yield
        finally:
            stop.set();await task
    app=FastAPI(title='Scalping SMA standalone',version='1.15.2',lifespan=lifespan)
    app.state.repo=repo
    from backend.tradingview import router
    app.include_router(router(repo))
    from backend.statistics.api import router as statistics_router
    app.include_router(statistics_router(repo))
    from backend.setup_diagnostics_api import router as diagnostics_router
    app.include_router(diagnostics_router(repo))
    from backend.backtest.api import router as backtest_router
    app.include_router(backtest_router(repo))

    @app.get('/api/health')
    def health():
        try:
            with repo.session() as s:
                s.execute(text('SELECT 1'))
                rows=s.scalars(select(ServiceHealth).where(ServiceHealth.name!='settings')).all()
            services={row.name:{**redact_telegram_tokens(row.payload),'updated_at':row.updated_at,'stale':now_ms()-row.updated_at>90000} for row in rows}
            status='HEALTHY' if services and all(not row['stale'] and row.get('status')=='HEALTHY' for row in services.values()) else 'RECOVERING'
            return {'status':status,'services':{'database':{'status':'HEALTHY'},**services},'parity_status':'UNVERIFIED','time':now_ms()}
        except Exception:
            raise HTTPException(503,'Database unavailable')

    @app.get('/api/setups')
    def setups(request:Request,active_only:bool=True,search:str='',direction:str|None=None,timeframe:str|None=None,action:str|None=None,fsm:str|None=None,candidate_path:str|None=None,signal:str|None=None,exchange:str|None=None,readiness:Literal['READY','OBSERVE','MANAGE']|None=None,sort:str='avg_setup:desc',limit:int=Query(500,ge=1,le=10000),offset:int=Query(0,ge=0),compact:bool=False):
        from backend.setup_diagnostics import current_readiness
        stamp=now_ms()
        with repo.session() as s: items=[current_readiness(payload,stamp) for payload in s.scalars(select(Current.payload))]
        if active_only: items=[r for r in items if r.get('action') and r['action']!='WAIT SETUP']
        if search: items=[r for r in items if search.upper() in r['symbol'].upper()]
        for field,value in [('direction',direction),('timeframe',timeframe),('action',action),('fsm',fsm),('candidate_path',candidate_path),('exchange',exchange),('readiness',readiness)]:
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
        page=items[offset:offset+limit]
        if compact:
            from backend.setup_summary import setup_summary
            page=[setup_summary(item) for item in page]
        return {'items':page,'total':len(items)}

    @app.get('/api/setups/{symbol}/{timeframe}')
    def detail(symbol:str,timeframe:str,exchange:str='BYBIT'):
        with repo.session() as s: payload=s.scalar(select(Current.payload).where(Current.exchange==exchange,Current.symbol==symbol,Current.timeframe==timeframe))
        if payload is None: raise HTTPException(404,'Setup not found')
        from backend.setup_diagnostics import current_readiness
        return current_readiness(payload,now_ms())

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
    @app.get('/api/settings/export')
    def export_settings():
        return {
            'format': 'brokeweb-settings',
            'version': 1,
            'exported_at': now_ms(),
            'settings': repo.settings(),
            'parameters': repo.parameters()['values']
        }
    @app.post('/api/settings/import')
    def import_settings(body:SettingsFileInput):
        try:
            validated=SettingsInput.model_validate(body.settings).model_dump(exclude_unset=True)
            if body.parameters:
                from backend.models.repository import validate_parameters
                validate_parameters(body.parameters)
        except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc))
        if body.settings:
            repo.set_settings(validated)
        if body.parameters:
            try: repo.set_parameters(body.parameters)
            except (ValueError,TypeError) as exc: raise HTTPException(422,f'Invalid parameters: {exc}')
        return {
            'status': 'imported',
            'settings': repo.settings(),
            'parameters': repo.parameters()['values']
        }

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
    @app.get('/api/alerts/rules/export')
    def export_rules():
        with repo.session() as s:rows=s.scalars(select(Rule).order_by(Rule.id)).all()
        return {'format':'brokeweb-alert-rules','version':1,'rules':[AlertRuleInput.model_validate(r.payload).model_dump() for r in rows]}
    @app.post('/api/alerts/rules/import',status_code=201)
    def import_rules(body:RuleFileInput):
        # Validate the complete file before opening a transaction: all or nothing.
        items=[]
        with repo.session.begin() as s:
            for rule in body.rules:
                key=str(uuid.uuid4());payload=rule.model_dump()
                s.add(Rule(id=key,version=1,enabled=rule.enabled,payload=payload))
                s.add(RuleVersion(rule_id=key,version=1,created_at=now_ms(),payload=payload))
                items.append({'id':key,'version':1,**payload})
        return {'items':items,'total':len(items)}
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
    def test_telegram(body: TestTelegramInput | None = None):
        st=repo.settings()
        target = body.target if body else 'broke_we'
        if target == 'broke_pb':
            token=(st.get('broke_pb_telegram_bot_token') or st.get('telegram_bot_token') or os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()
            chat=(st.get('broke_pb_telegram_chat_id') or st.get('telegram_chat_id') or os.getenv('TELEGRAM_CHAT_ID') or '').strip()
            topic=(st.get('broke_pb_telegram_topic_id') or '').strip()
            label='BROKE-PB Setups'
        elif target == 'broke_pb_pm':
            token=(st.get('broke_pb_pm_telegram_bot_token') or st.get('telegram_bot_token') or os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()
            chat=(st.get('broke_pb_pm_telegram_chat_id') or st.get('telegram_chat_id') or os.getenv('TELEGRAM_CHAT_ID') or '').strip()
            topic=(st.get('broke_pb_pm_telegram_topic_id') or '').strip()
            label='BROKE-PB Position Manager'
        else:
            token=(st.get('telegram_bot_token') or os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()
            chat=(st.get('telegram_chat_id') or os.getenv('TELEGRAM_CHAT_ID') or '').strip()
            topic=(st.get('telegram_topic_id') or os.getenv('TELEGRAM_TOPIC_ID') or '').strip()
            label='BROKE WE Setups'

        if not token or not chat: raise HTTPException(409, f'Configure Telegram Bot Token and Chat ID for {label} first')
        from backend.alerts.outbox import digest
        now=now_ms()
        thread_id=int(topic) if topic.lstrip('-').isdigit() else None
        with repo.session.begin() as s:
            s.add(Delivery(
                dedupe_key=digest(['test',str(uuid.uuid4())]),
                rule_id='manual-test',
                rule_version=1,
                created_at=now,
                updated_at=now,
                next_attempt=now,
                status='pending',
                payload={
                    'text': f'Scalping SMA: connection test for {label} requested in the web interface.',
                    'telegram_bot_token': token,
                    'telegram_chat_id': chat,
                    'telegram_topic_id': topic,
                    'message_thread_id': thread_id
                }
            ))
        return {'status':'queued', 'target': target, 'chat': chat, 'topic': topic or 'general'}

    @app.get('/api/setup-campaigns')
    def setup_campaigns(limit:int=Query(20,ge=1,le=1000),offset:int=Query(0,ge=0),
            sort_by:Literal['symbol','direction','mode','opened_at','closed_at','entry_price','sl','t1','current_price','price_change_pct','close_reason']='closed_at',
            sort_direction:Literal['asc','desc']='desc'):
        from backend.setup_campaigns import campaigns
        with repo.session() as s:return campaigns(s,limit,offset,sort_by,sort_direction)

    @app.get('/api/level-campaign')
    def level_campaigns(limit:int=Query(200,ge=1,le=1000),offset:int=Query(0,ge=0),
            sort_by:Literal['symbol','side','opened_at','closed_at','tranches','realized_pnl','net_realized_pnl','close_reason','last_execution']='closed_at',
            sort_direction:Literal['asc','desc']='desc'):
        from backend.models.schema import LevelCampaign, CampaignSymbolState, CampaignTranche
        from backend.engine.level_campaign import serialize_campaign, COUNTERS
        with repo.session() as s:
            active = s.scalars(select(LevelCampaign).where(LevelCampaign.state != 'CLOSED').order_by(LevelCampaign.opened_at.desc())).all()
            closed = select(LevelCampaign).where(LevelCampaign.state == 'CLOSED')
            history_total = s.scalar(select(func.count()).select_from(closed.subquery()))
            sort_fields={'symbol':LevelCampaign.symbol,'side':LevelCampaign.side,'opened_at':LevelCampaign.opened_at,
                'closed_at':func.coalesce(cast(LevelCampaign.payload['closed_at'].as_string(),BigInteger),LevelCampaign.updated_at),
                'tranches':select(func.count()).select_from(CampaignTranche).where(CampaignTranche.campaign_id==LevelCampaign.id).correlate(LevelCampaign).scalar_subquery(),
                'realized_pnl':cast(LevelCampaign.payload['realized_pnl'].as_string(),Float),
                'net_realized_pnl':cast(LevelCampaign.payload['net_realized_pnl'].as_string(),Float),
                'close_reason':LevelCampaign.payload['close_reason'].as_string(),
                'last_execution':cast(LevelCampaign.payload['last_execution']['at'].as_string(),BigInteger)}
            field=sort_fields[sort_by]
            order=(field.asc() if sort_direction=='asc' else field.desc()).nulls_last()
            history = s.scalars(closed.order_by(order,LevelCampaign.id.asc()).offset(offset).limit(limit)).all()
            diagnostics = [{'key':r.key,'updated_at':r.updated_at,**r.payload} for r in s.scalars(select(CampaignSymbolState))]
            counts = {k:sum(d.get('counters',{}).get(k,0) for d in diagnostics) for k in COUNTERS}
            return {'active':[serialize_campaign(s,c) for c in active],
                    'history':[serialize_campaign(s,c) for c in history],
                    'history_total':history_total,
                    'diagnostics':diagnostics,'counters':counts,
                    'execution_mode':repo.settings(s).get('campaign_execution_mode','PAPER')}

    @app.get('/api/level-campaign/{campaign_id}/orders')
    def campaign_orders(campaign_id:str):
        from backend.models.schema import LevelCampaign, CampaignOrder
        with repo.session() as s:
            if s.get(LevelCampaign,campaign_id) is None:raise HTTPException(404,'Campaign not found')
            rows=s.scalars(select(CampaignOrder).where(CampaignOrder.campaign_id==campaign_id).order_by(CampaignOrder.created_at,cast(CampaignOrder.payload['sequence'].as_string(),BigInteger).asc().nulls_last(),CampaignOrder.id)).all()
            return {'items':[{'id':r.id,'tranche_id':r.tranche_id,'action':r.action,'qty':r.qty,'fill_price':r.fill_price,'event_time':r.created_at,
                             'fee_usdt':r.payload.get('fee_usdt'),'fill_model':r.payload.get('fill_model','LEGACY'),
                             'trigger_price':r.payload.get('trigger_price'),'source_time':r.payload.get('source_time')} for r in rows]}

    @app.get('/api/level-campaign/{campaign_id}/ledger')
    def campaign_ledger(campaign_id:str):
        from backend.models.schema import LevelCampaign,CampaignLedgerEntry
        with repo.session() as s:
            if s.get(LevelCampaign,campaign_id) is None:raise HTTPException(404,'Campaign not found')
            rows=s.scalars(select(CampaignLedgerEntry).where(CampaignLedgerEntry.campaign_id==campaign_id).order_by(CampaignLedgerEntry.created_at,CampaignLedgerEntry.id))
            return {'items':[{'id':r.id,'kind':r.kind,'amount_usdt':r.amount_usdt,'event_time':r.created_at,**r.payload} for r in rows]}

    @app.get('/api/broke-pb/positions')
    def pb_positions(status: str | None = None, symbol: str | None = None, limit: int = Query(200, ge=1, le=1000)):
        with repo.session() as s:
            query = select(BrokePBPosition)
            if status: query = query.where(BrokePBPosition.status == status)
            if symbol: query = query.where(BrokePBPosition.symbol == symbol)
            rows = s.scalars(query.order_by(BrokePBPosition.entry_time.desc(), BrokePBPosition.id.desc()).limit(limit)).all()
            return {'items': [{
                'id': r.id, 'symbol': r.symbol, 'exchange': r.exchange, 'timeframe': r.timeframe,
                'direction': r.direction, 'status': r.status, 'nominal_usdt': r.nominal_usdt,
                'entry_price': r.entry_price, 'entry_time': r.entry_time, 'bar_start': r.bar_start,
                'sl': r.sl, 'tp1': r.tp1, 'runner': r.runner, 'tp1_hit': r.tp1_hit,
                'runner_hit': r.runner_hit, 'runner_be': r.runner_be, 'underwater': r.underwater,
                'reduced': r.reduced, 'exhaustion_taken': r.exhaustion_taken,
                'close_price': r.close_price, 'close_time': r.close_time, 'close_reason': r.close_reason,
                'pnl_usdt': r.pnl_usdt, 'pnl_pct': r.pnl_pct, 'payload': r.payload, 'updated_at': r.updated_at
            } for r in rows]}

    @app.get('/api/broke-pb/positions/active')
    def pb_active_positions():
        with repo.session() as s:
            rows = s.scalars(select(BrokePBPosition).where(BrokePBPosition.status == 'OPEN').order_by(BrokePBPosition.entry_time.desc())).all()
            current_map = {c.symbol: c.payload for c in s.scalars(select(Current))}
            items = []
            for r in rows:
                c = current_map.get(r.symbol, {})
                curr_price = c.get('price') or r.entry_price
                is_long = r.direction == 'LONG'
                unrealized_pct = ((curr_price - r.entry_price) / r.entry_price * 100.0) if is_long else ((r.entry_price - curr_price) / r.entry_price * 100.0)
                remaining_frac = 0.5 if r.tp1_hit else (0.5 if r.reduced else 1.0)
                realized = r.payload.get('realized_tp1_usdt', 0.0)
                unrealized_usdt = (r.nominal_usdt * remaining_frac) * (unrealized_pct / 100.0)
                if r.payload.get('campaign_id'):
                    curr_price = r.payload.get('current_price',curr_price)
                    unrealized_usdt = r.payload.get('unrealized_pnl',0.)
                    realized = r.payload.get('realized_pnl',0.)
                total_pnl = realized + unrealized_usdt
                if r.payload.get('campaign_id'):total_pnl=r.payload.get('net_total_pnl',total_pnl)
                total_pnl_pct = (total_pnl / r.nominal_usdt) * 100.0

                items.append({
                    'id': r.id, 'symbol': r.symbol, 'direction': r.direction, 'status': r.status,
                    'nominal_usdt': r.nominal_usdt, 'entry_price': r.entry_price, 'current_price': curr_price,
                    'entry_time': r.entry_time, 'sl': r.runner_be if r.tp1_hit else r.sl, 'original_sl': r.sl,
                    'tp1': r.tp1, 'runner': r.runner, 'tp1_hit': r.tp1_hit, 'runner_be': r.runner_be,
                    'underwater': r.underwater, 'reduced': r.reduced,
                    'pnl_usdt': round(total_pnl, 2), 'pnl_pct': round(total_pnl_pct, 2),
                    'action': 'TAKE 50% + BE' if r.tp1_hit else ('UNDERWATER' if r.underwater else 'HOLD / IN TRADE'),
                    'updated_at': r.updated_at
                })
            return {'items': items, 'total': len(items)}

    @app.get('/api/ai-agent/settings')
    def ai_agent_settings():
        from backend.ai_agent.settings import get_masked
        return get_masked(repo)

    class AIAgentSettingsInput(BaseModel):
        enabled:bool|None=None
        tg_bot_token:str|None=None
        primary_ai:str|None=None
        primary_api_token:str|None=None
        primary_model:str|None=None
        secondary_ai:str|None=None
        secondary_api_token:str|None=None
        secondary_model:str|None=None
        nim_base_url:str|None=None
        min_avg_setup:float|None=Field(default=None,ge=0,le=100)
        max_setups_in_report:int|None=Field(default=None,ge=1,le=50)
        allowed_chat_ids:str|None=None

    @app.put('/api/ai-agent/settings')
    def save_ai_agent_settings(body:AIAgentSettingsInput):
        from backend.ai_agent.settings import save as save_ai_settings
        patch={k:v for k,v in body.model_dump().items() if v is not None}
        return save_ai_settings(repo,patch)

    @app.get('/api/ai-agent/status')
    def ai_agent_status():
        from backend.models.schema import ServiceHealth
        with repo.session() as s:
            row=s.get(ServiceHealth,'ai-agent')
        if row is None: return {'status':'NOT_STARTED','note':'ai-agent service not running'}
        return {'status':row.payload.get('status','UNKNOWN'),'updated_at':row.updated_at,'stale':now_ms()-row.updated_at>90000,**row.payload}

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
        path=os.getenv('INTRABAR_REPORT_PATH','reports/intrabar-pool-14573.json')
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
        names=('processed_market_messages','market_data_lag_ms','calculations','calculations_per_second','calculation_latency_ms','checkpoint_export_latency_ms','checkpoint_pack_latency_ms','websocket_reconnects','db_write_latency_ms','stale_instruments','parity_failures')
        for name in names:
            for service in services:
                value=service.payload.get(name)
                if isinstance(value,(int,float)): lines.append(f'scalping_{name}{{service="{service.name}"}} {value}')
        return '\n'.join(lines)+'\n'

    @app.websocket('/ws/setups')
    async def websocket(ws:WebSocket,compact:bool=False,exchange:str|None=None,symbol:str|None=None,timeframe:str|None=None):
        await ws.accept()
        from backend.setup_summary import setup_message
        def frame(payload):
            return setup_message(payload,compact=compact,exchange=exchange,symbol=symbol,timeframe=timeframe,now=now_ms())
        def initial():
            query=select(Current.payload)
            for field,value in [('exchange',exchange),('symbol',symbol),('timeframe',timeframe)]:
                if value is not None: query=query.where(getattr(Current,field)==value)
            with repo.session() as s:
                return [frame(payload) for payload in s.scalars(query)]
        client=None; pubsub=None
        try:
            from redis.asyncio import Redis
            client=Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'),decode_responses=True,socket_connect_timeout=3,socket_timeout=5)
            pubsub=client.pubsub(); await pubsub.subscribe('setups')
            for item in await asyncio.to_thread(initial):
                if item is not None: await ws.send_json(item)
            while True:
                message=await pubsub.get_message(ignore_subscribe_messages=True,timeout=15)
                if message:
                    payload=json.loads(message['data'])
                    item=frame(payload)
                    if item is not None: await ws.send_json(item)
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
