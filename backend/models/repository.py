from __future__ import annotations
import dataclasses
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.models.schema import *
from backend.models.checkpoints import PackedCheckpoint,pack_checkpoint,unpack_checkpoint
from backend.alerts.outbox import digest,enqueue_matching

ROOT=Path(__file__).resolve().parents[2]
SCORES=('price','formation','execution','geometry','context','level','approach','exhaustion','mae','avg_setup','continuation','btc_shock','sl','t1','rr')
TRANSITIONS=('action','fsm','direction','setup_generation_id','locked_zone','candidate_path','trigger_path','active_plan_health','target_freshness')
def now_ms(): return time.time_ns()//1_000_000

def clean(value):
    kind=type(value)
    if kind is float:return value if math.isfinite(value) else None
    if kind is int or kind is str or kind is bool or value is None:return value
    if dataclasses.is_dataclass(value): value=dataclasses.asdict(value)
    if isinstance(value,dict): return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [clean(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value): return None
    return value

def input_schema():
    path=ROOT/'reference/inputs.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []

def validate_parameters(values):
    from backend.engine.parameters import validate_parameters as validate_source_parameters
    return validate_source_parameters(values)

class Repository:
    def __init__(self,database_url=None):
        url=database_url or os.getenv('DATABASE_URL','sqlite:///./brokeweb.db')
        kwargs={'pool_pre_ping':True}
        if url.startswith('sqlite'):
            kwargs['connect_args']={'check_same_thread':False,'timeout':30}
            if ':memory:' in url: kwargs['poolclass']=StaticPool
        self.engine=create_engine(url,**kwargs)
        self.session=sessionmaker(self.engine,expire_on_commit=False)
        self.snapshot_interval_sec=float(os.getenv('SNAPSHOT_INTERVAL_SEC','15'))
    def initialize(self):
        # Register historical research tables on the existing SQLAlchemy metadata.
        from backend.backtest import models as backtest_models  # noqa: F401
        Base.metadata.create_all(self.engine)
        with self.session.begin() as s:
            if s.scalar(select(ParameterSet.id).where(ParameterSet.active.is_(True))) is None:
                values=validate_parameters({}); key=digest(values)
                s.add(ParameterSet(id=key,created_at=now_ms(),values=values,active=True))
    def parameters(self):
        with self.session() as s:
            row=s.scalar(select(ParameterSet).where(ParameterSet.active.is_(True)).order_by(ParameterSet.created_at.desc()))
            if row is None: raise RuntimeError('Database not initialized')
            return {'id':row.id,'values':row.values,'schema':input_schema()}
    def set_parameters(self,values):
        values=validate_parameters(values); key=digest(values)
        with self.session.begin() as s:
            # PostgreSQL serializes parameter activation, maintaining exactly one active version.
            if self.engine.dialect.name=='postgresql': s.execute(text('SELECT pg_advisory_xact_lock(431579)'))
            s.execute(update(ParameterSet).values(active=False))
            row=s.get(ParameterSet,key)
            if row: row.active=True
            else: s.add(ParameterSet(id=key,created_at=now_ms(),values=values,active=True))
        return self.parameters()
    def save_instruments(self,items,replace_universe=False):
        items=[clean(item) for item in items]
        with self.session.begin() as s:
            if replace_universe:
                for exchange in {item.get('exchange','BYBIT') for item in items}:
                    symbols={item['symbol'] for item in items if item.get('exchange','BYBIT')==exchange}
                    for row in s.scalars(select(Instrument).where(Instrument.exchange==exchange,Instrument.symbol.not_in(symbols))):
                        row.status='Closed';row.payload={**row.payload,'status':'Closed'}
            for item in items:
                item=clean(item)
                s.merge(Instrument(exchange=item.get('exchange','BYBIT'),symbol=item['symbol'],status=item.get('status','Trading'),tick_size=item['tick_size'],payload=item))
    def save_bar(self,bar):
        bar=clean(bar)
        columns={c.name for c in MarketBar.__table__.columns}
        with self.session.begin() as s:
            key=tuple(bar[x] for x in ('exchange','symbol','timeframe','start'))
            existing=s.get(MarketBar,key)
            if existing and existing.confirmed and not bar.get('confirmed'): return
            s.merge(MarketBar(**{k:v for k,v in bar.items() if k in columns}))
    def save_bars(self,bars):
        """Bulk native-bar reconciliation; unchanged confirmed rows incur no rewrite."""
        from sqlalchemy import and_,or_
        if self.engine.dialect.name=='postgresql':
            from sqlalchemy.dialects.postgresql import insert
        elif self.engine.dialect.name=='sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            for bar in bars:self.save_bar(bar)
            return
        columns={c.name for c in MarketBar.__table__.columns}
        values=[{k:v for k,v in clean(bar).items() if k in columns} for bar in bars]
        with self.session.begin() as session:
            for start in range(0,len(values),1000):
                statement=insert(MarketBar).values(values[start:start+1000])
                excluded=statement.excluded
                changes=or_(*(getattr(MarketBar,k).is_distinct_from(getattr(excluded,k)) for k in ('open','high','low','close','volume','turnover','confirmed')))
                update={k:getattr(excluded,k) for k in columns-{'exchange','symbol','timeframe','start'}}
                statement=statement.on_conflict_do_update(index_elements=['exchange','symbol','timeframe','start'],set_=update,where=and_(or_(MarketBar.confirmed.is_(False),excluded.confirmed.is_(True)),changes))
                session.execute(statement)
    def load_checkpoint(self,exchange,symbol,timeframe):
        with self.session() as s:
            row=s.get(Current,(exchange,symbol,timeframe))
            return self.read_checkpoint(row) if row else None
    @staticmethod
    def read_checkpoint(row):
        return unpack_checkpoint(row.checkpoint_blob) if row.checkpoint_blob is not None else row.checkpoint
    def save_snapshot(self,snapshot,checkpoint=None):
        current=clean(snapshot); now=now_ms()
        wt=current.pop('wt',None)
        current.setdefault('strategy','BROKE_SETUPS');current.setdefault('signal_source','engine')
        packed=(checkpoint.blob if isinstance(checkpoint,PackedCheckpoint)
                else pack_checkpoint(clean(checkpoint)) if checkpoint is not None else None)
        current.setdefault('exchange','BYBIT'); current.setdefault('event_time',now); current.setdefault('received_at',now)
        current.setdefault('setup_generation_id',''); current.setdefault('bar_start',current['event_time']); current.setdefault('confirmed',False)
        if 'parameter_set_id' not in current:current['parameter_set_id']=self.parameters()['id']
        current.setdefault('engine_git_sha',os.getenv('GIT_SHA','unknown'))
        current.setdefault('calculation_timestamp',now); current.setdefault('data_source',current['exchange'])
        key=tuple(current[x] for x in ('exchange','symbol','timeframe'))
        with self.session.begin() as s:
            old=s.get(Current,key,with_for_update=True); previous=old.payload if old else None
            if current.get('strategy')=='BROKE_SETUPS' and current.get('metrics'):
                from backend.setup_diagnostics import snapshot_checks,record_generation
                parameters=s.get(ParameterSet,current['parameter_set_id'])
                current.update(snapshot_checks(current,parameters.values if parameters else {}))
                record_generation(s,current,previous,self.settings(s))
            changes={field:{'before':previous.get(field) if previous else None,'after':current.get(field)} for field in TRANSITIONS if previous is None or previous.get(field)!=current.get(field)}
            for field,change in changes.items():
                s.add(Event(exchange=key[0],symbol=key[1],timeframe=key[2],event_time=current['event_time'],kind=field,payload={**change,'setup_generation_id':current['setup_generation_id'],'parameter_set_id':current['parameter_set_id']}))
            signals=current.get('signals',[])
            same_signal_bar=previous and previous.get('bar_start')==current['bar_start'] and previous.get('setup_generation_id')==current['setup_generation_id']
            new_signal_events=[name for name in signals if not same_signal_bar or name not in previous.get('signals',[])]
            periodic=current.get('action')!='WAIT SETUP' and (old is None or now-old.last_snapshot>=self.settings(s)['snapshot_interval_sec']*1000)
            store=bool(changes or new_signal_events or current['confirmed'] or periodic)
            if store:
                record={k:current.get(k) for k in SCORES}
                record={k:v if isinstance(v,(int,float)) and not isinstance(v,bool) else None for k,v in record.items()}
                s.add(Snapshot(**record,exchange=key[0],symbol=key[1],timeframe=key[2],event_time=current['event_time'],bar_start=current['bar_start'],confirmed=current['confirmed'],generation=str(current['setup_generation_id']),action=current.get('action'),fsm=str(current['fsm']) if current.get('fsm') is not None else None,direction=current.get('direction'),parameter_set_id=current['parameter_set_id'],payload=current))
            for name in signals:
                dedupe=digest([*key,current['setup_generation_id'],current['bar_start'],name])
                if s.scalar(select(Signal.id).where(Signal.dedupe_key==dedupe)) is None:
                    s.add(Signal(dedupe_key=dedupe,symbol=key[1],timeframe=key[2],event_time=current['event_time'],name=name,parameter_set_id=current['parameter_set_id'],payload={**current,'event':name}))
                    s.add(Event(exchange=key[0],symbol=key[1],timeframe=key[2],event_time=current['event_time'],kind='signal',payload={**current,'event':name}))
            enqueue_matching(s,current,previous,now)
            if wt:
                from backend.wt import save_engine
                save_engine(s,wt,now)
            from backend.engine.level_campaign import process_campaign_snapshot
            process_campaign_snapshot(s, current, self.settings(s), now)
            self._save_research(s,current)
            if old:
                old.payload=current; old.updated_at=now
                if store: old.last_snapshot=now
                if checkpoint is not None:
                    old.checkpoint_blob=packed;old.checkpoint=None
            else:
                s.add(Current(exchange=key[0],symbol=key[1],timeframe=key[2],updated_at=now,last_snapshot=now if store else 0,payload=current,checkpoint_blob=packed))
        return current
    def _save_research(self,s,current):
        # Engine owns outcomes, ordering, horizon and freeze logic. Persist its exact raw records.
        research=current.get('research',{})
        if not isinstance(research,dict): return
        active=research.get('samples',[])
        completed=research.get('completed',[])
        for sample,is_completed in [(x,False) for x in active]+[(x,True) for x in completed]:
            fields=sample.get('fields',sample)
            identity=fields.get('id',fields.get('signalBar',fields.get('signal_bar',fields.get('entryTime'))))
            if identity is None: raise ValueError('Research record requires persistent signal identity')
            bucket=fields.get('bucket')
            # Pine source family bases are multiples of PATH_COUNT; stored codes stay intact.
            family=str(fields.get('family',bucket if bucket is not None else 'PINE'))
            key=digest([current['exchange'],current['symbol'],current['timeframe'],current['parameter_set_id'],current.get('history_start'),bucket,identity,fields.get('direction')])
            existing=s.get(ResearchSample,key)
            frozen=existing.payload.get('frozen_snapshot') if existing else {k:v for k,v in current.items() if k not in ('metrics','research','decision_panel')}
            payload={**fields,'raw':sample,'completed':is_completed or fields.get('completed',False),'frozen_snapshot':frozen}
            if existing:
                if existing.payload!=payload:existing.payload=payload;existing.event_time=current['event_time']
            else:
                s.add(ResearchSample(id=key,symbol=current['symbol'],timeframe=current['timeframe'],generation=str(current['setup_generation_id']),event_time=current['event_time'],parameter_set_id=current['parameter_set_id'],family=family,payload=payload))
    def settings(self,session=None):
        if session is None:
            with self.session() as s: return self.settings(s)
        row=session.get(ServiceHealth,'settings')
        from backend.engine.campaign_execution import DEFAULTS
        from backend.setups_config import defaults as broke_defaults
        return {'snapshot_interval_sec':self.snapshot_interval_sec,'timezone_offset_minutes':420,'universe_min_turnover24h_usdt':10000000,
                'campaign_enabled':True,'campaign_exit_policy':'CONTEXT_30M',**DEFAULTS,**broke_defaults(),**(row.payload if row else {})}
    def set_settings(self,value):
        if value.get('campaign_execution_mode')=='LIVE':
            raise ValueError('LIVE_EXECUTOR_UNAVAILABLE: use PAPER or DEMO')
        with self.session.begin() as s:
            row=s.get(ServiceHealth,'settings',with_for_update=True)
            merged={**self.settings(s),**value}
            if row:
                row.payload=merged;row.updated_at=now_ms()
            else:
                s.add(ServiceHealth(name='settings',updated_at=now_ms(),payload=merged))
        return merged

    def campaign_symbols(self):
        with self.session() as s:
            active=set(s.scalars(select(LevelCampaign.symbol).where(LevelCampaign.active_slot.is_not(None),LevelCampaign.exchange=='BYBIT')))
            legacy=set(s.scalars(select(BrokePBPosition.symbol).where(BrokePBPosition.status=='OPEN',BrokePBPosition.exchange=='BYBIT')))
            return active|legacy

    def manage_campaign_price(self,snapshot):
        return self.manage_campaign_prices([snapshot])[-1]

    def manage_campaign_prices(self,snapshots):
        from backend.engine.level_campaign import process_campaign_snapshot
        with self.session.begin() as s:
            settings=self.settings(s);received=now_ms()
            return [process_campaign_snapshot(s,{**snapshot,'price_only':True},settings,received) for snapshot in snapshots]

    def mark_campaign_coverage(self,symbols,quality):
        with self.session.begin() as s:
            for c in s.scalars(select(LevelCampaign).where(LevelCampaign.symbol.in_(symbols),LevelCampaign.exchange=='BYBIT',LevelCampaign.active_slot.is_not(None)).with_for_update()):
                c.payload={**c.payload,'execution_quality':quality}
    def exclude_from_universe(self,symbols):
        # Preserve history/checkpoints, but never expose a stopped calculation
        # as an actionable current setup. No alert/outbox event is generated.
        if not symbols:return
        with self.session.begin() as session:
            for row in session.scalars(select(Current).where(Current.exchange=='BYBIT',Current.symbol.in_(symbols)).with_for_update()):
                row.payload={**row.payload,'universe_excluded':True,'data_health':'STALE','action':'WAIT SETUP','signals':[]}

    def heartbeat(self,name,payload):
        with self.session.begin() as s: s.merge(ServiceHealth(name=name,updated_at=now_ms(),payload=clean(payload)))
