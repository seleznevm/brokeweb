"""Public-feed worker, source engine, checkpoint recovery and snapshot publisher.

Marketdata and engine are colocated to preserve update order. API and notifier
are separate processes; Redis distributes snapshots, PostgreSQL is durable truth.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import math
import os
import time
import uuid
import signal
from collections import defaultdict
from dataclasses import asdict
from contextlib import suppress
from functools import lru_cache
from redis.asyncio import Redis
from backend.engine.runtime import PineEngine, ENGINE_VERSION, PINE_HASH
from backend.engine.context_bars import ContextBars
from backend.engine.parameters import required_history, parameter_hash
from backend.engine.interpreter import tf_seconds,qualified
from backend.engine.syntax import load_program
from backend.marketdata import BybitAdapter,BinanceBtcContextAdapter
from backend.marketdata.websocket import BybitWebSocketManager
from backend.marketdata.trades import TradeAggregator
from backend.marketdata.binance_websocket import BinanceBtcWebSocket
from backend.models.repository import Repository,now_ms
from backend.models.checkpoints import PackedCheckpoint
from backend.models.schema import Current,MarketBar
from sqlalchemy import select
from backend.logging_config import configure_logging

log=logging.getLogger(__name__)
def bar_dict(b):
    out=b.to_dict() if hasattr(b,'to_dict') else dict(b)
    out['received_at']=out.get('received_at') or now_ms()
    return out

def context_requirements(parameters,timeframes,wt_parameters=None):
    own={'60',parameters['htfBaseTf'],*(parameters[f'mtfTrendTf{i}'] for i in range(1,5))};btc=set(timeframes)
    for tf in timeframes:
        sec=tf_seconds(tf)
        auto=parameters['tfProfileMode']=='Auto'
        micro=('30S' if sec<=60 else '1' if sec<=300 else '5' if sec<=3600 else '30' if sec<=14400 else '60') if auto else parameters['manualMicroTf']
        shock=('1' if sec<=300 else '5' if sec<=900 else '15' if sec<=3600 else '60' if sec<=14400 else '240') if auto else parameters['manualBtcShockTf']
        own.add(micro);btc.add(shock)
    if wt_parameters is not None:
        from backend.engine.wt import context_requirements as wt_context_requirements
        wt_own,wt_btc=wt_context_requirements(wt_parameters,timeframes);own.update(wt_own);btc.update(wt_btc)
    return own,btc


@lru_cache(maxsize=1)
def checkpoint_request_ids():
    ids=set()
    def expression(node):
        if node.kind=='call' and qualified(node.args[0]) in ('request.security','request.security_lower_tf'):
            ids.add(node.uid)
        for child in node.args:expression(child)
    def statements(nodes):
        for node in nodes:
            if node.expr:expression(node.expr)
            statements(node.body);statements(node.otherwise)
    program=load_program();statements(program.statements)
    for function in program.functions.values():statements(function.body)
    return frozenset(ids)


def restored_context_anchor(engines, now, history_span):
    """Only shorten REST history when every restored request has committed state.

    Rolling/recursive request history lives in the checkpoint. Fetch from the
    oldest committed chart/request candle (plus the caller's overlap), never
    from the last snapshot timestamp or an unconfirmed intrabar execution.
    Cold, incomplete, or older-than-loaded-BTC checkpoints keep full bootstrap.
    """
    starts=[]
    for engine in engines:
        streams=getattr(getattr(engine,'provider',None),'streams',{})
        if not streams or {key.split('|',1)[0] for key in streams}!=checkpoint_request_ids():return None
        executions=[engine.runtime,*(execution for execution,_ in streams.values())]
        for execution in executions:
            start=execution.last_start
            if type(start) is not int or type(execution.count) is not int or execution.count<=0 or not now-history_span<=start<=now:
                return None
            starts.append(start)
    return min(starts) if starts else None

def turnover_values(tickers):
    values={}
    for row in tickers:
        try:value=float(row.get('turnover24h'))
        except (TypeError,ValueError):continue
        if math.isfinite(value) and value>=0:values[row['symbol']]=value
    return values


class Worker:
    def __init__(self):
        self.repo=Repository();self.redis=Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'),decode_responses=True)
        testnet=os.getenv('BYBIT_ENV','mainnet')=='testnet'
        self.bybit=BybitAdapter(base_url='https://api-testnet.bybit.com' if testnet else 'https://api.bybit.com');self.btc=BinanceBtcContextAdapter()
        self.ws=BybitWebSocketManager(self.on_market,url='wss://stream-testnet.bybit.com/v5/public/linear' if testnet else 'wss://stream.bybit.com/v5/public/linear')
        self.btc_ws=BinanceBtcWebSocket(self.on_btc);self.btc_refresh_lock=asyncio.Lock();self.btc_recovering=True;self.btc_last_events={}
        self.timeframes=[s.strip() for s in os.getenv('ACTIVE_TIMEFRAMES','30').split(',') if s.strip()]
        for tf in self.timeframes:tf_seconds(tf)
        self.engines={};self.contexts={};self.instruments={};self.native_turnover24h={};self.aggregators={};self.full_charts={};self.full_since={};self.full=set();self.ready=set();self.recovering=set();self.locks=defaultdict(asyncio.Lock)
        self.messages=0;self.calculations=0;self.errors={};self.status='RECOVERING';self.universe_count=0;self.selected_count=0;self.last_events={};self.reconnects=0;self.latencies=[];self.parameter_id=None;self.parameters={};self.pending={};self.replay_skip_until={};self.replay_origins={};self.context_last=0;self.reconciliation_errors=0;self.db_latencies=[];self.backfilled={};self.previous_calculations=0;self.previous_heartbeat=time.monotonic()
        self.lease_token=uuid.uuid4().hex
        self.checkpoint_latencies=[]
        self.checkpoint_pack_latencies=[]
        self.context_views={}
        self.wt_parameters=None
        self.stopping=False;self.background=set()
        self.shard_index=int(os.getenv('ENGINE_SHARD_INDEX','0'));self.shard_count=int(os.getenv('ENGINE_SHARD_COUNT','1'))
        if not 0<=self.shard_index<self.shard_count:raise ValueError('Invalid engine shard')
    async def blocking(self, function, *args):
        # Cancelling to_thread does not stop its thread. Keep the shard lease
        # until an in-flight calculation/transaction has actually finished.
        task=asyncio.create_task(asyncio.to_thread(function,*args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            finally:
                raise

    def launch(self, coroutine):
        task=asyncio.create_task(coroutine)
        self.background.add(task)
        def finished(done):
            self.background.discard(done)
            if not done.cancelled() and done.exception() is not None:
                log.error('background_recovery_failed',exc_info=done.exception())
        task.add_done_callback(finished)
        return task

    def put_context(self,key,bar):
        self.context_views.pop(key,None)
        rows=self.contexts.setdefault(key,[])
        if rows and rows[-1]['start']==bar['start']:
            if not rows[-1]['confirmed'] or bar['confirmed']:rows[-1]=bar
        elif not rows or bar['start']>rows[-1]['start']:rows.append(bar)
        else:
            by_time={b['start']:b for b in rows};by_time[bar['start']]=bar;rows[:]=[by_time[t] for t in sorted(by_time)]
        if len(rows)>self.context_limit:del rows[:-self.context_limit]
    def context_view(self,symbol):
        view={}
        for key,rows in self.contexts.items():
            if key.startswith(f'BYBIT:{symbol}.P|') or key.startswith('BINANCE:BTCUSDT.P|'):
                if key not in self.context_views:self.context_views[key]=ContextBars(rows)
                view[key]=self.context_views[key]
        return view
    def runtime_health(self):
        """Re-evaluate freshness even when no new calculation has completed."""
        now=now_ms();healthy=0;lags=[];stale=set()
        for (symbol,_),engine in self.engines.items():
            snapshot=engine.snapshot
            if not snapshot:continue
            calculated=snapshot.get('calculation_timestamp')
            lag=snapshot.get('market_data_lag_ms')
            # Stored lag alone freezes when the feed/calculation stops.
            age=lag+max(0,now-calculated) if calculated is not None and lag is not None else None
            if age is not None:lags.append(age)
            fresh=age is not None and age<=90000
            if fresh and snapshot.get('data_health')=='HEALTHY' and symbol not in self.recovering:healthy+=1
            if not fresh or snapshot.get('data_health') in ('STALE','DEGRADED'):stale.add(symbol)
        expected=self.selected_count*len(self.timeframes)
        streams=list(self.ws.health.values())
        streams_ready=bool(streams) and all(s.connected and s.last_market_event and now-s.last_market_event<=90000 for s in streams)
        ready=(expected>0 and len(self.ready)==self.selected_count and healthy==expected
               and not self.errors and not self.recovering and streams_ready and self.btc_stream_ready())
        return {'status':'HEALTHY' if ready else 'RECOVERING','healthy_engines':healthy,
                'stale_instruments':len(stale),'market_data_lag_ms':max(lags,default=None)}

    async def renew_lease(self, interval=10):
        # Ownership must not wait for SQL telemetry, bootstrap or thread-pool
        # availability. Keep this task alive until all writes have drained.
        while True:
            renewed=await asyncio.wait_for(self.redis.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('pexpire',KEYS[1],60000) else return 0 end",1,self.lease_key,self.lease_token),timeout=10)
            if not renewed:
                log.error('engine_shard_lease_lost');os._exit(76)
            await asyncio.sleep(interval)

    async def heartbeat(self):
        while True:
            payload={'status':self.status,'universe':self.universe_count,'selected':self.selected_count,'initialized':len(self.ready),'timeframes':self.timeframes,'engines':len(self.engines),'processed_market_messages':self.messages,'calculations':self.calculations,'calculation_ms_p95':sorted(self.latencies)[int(.95*(len(self.latencies)-1))] if self.latencies else None,'websocket_reconnects':self.reconnects,'errors':self.errors,'reconciliation_errors':self.reconciliation_errors,'parity_status':'UNVERIFIED','quality_mode':'KLINE_REALTIME; full trade OHLCV after complete chart boundary','max_symbols':int(os.getenv('MAX_SYMBOLS','0')),'shard_index':self.shard_index,'shard_count':self.shard_count,'context_updated_at':self.context_last,'last_market_event':max(self.last_events.values(),default=None)}
            elapsed=max(time.monotonic()-self.previous_heartbeat,.001)
            payload.update(btc_stream=asdict(self.btc_ws.health),btc_recovering=self.btc_recovering,btc_timeframe_events=self.btc_last_events,calculations_per_second=(self.calculations-self.previous_calculations)/elapsed,calculation_latency_ms=payload['calculation_ms_p95'],db_write_latency_ms=sum(self.db_latencies)/len(self.db_latencies) if self.db_latencies else None,last_successful_rest_backfill=self.backfilled,streams=[asdict(state) for state in self.ws.health.values()])
            payload.update(self.runtime_health())
            payload.update(universe_min_turnover24h_usdt=getattr(self,'universe_min_turnover',None),liquidity_excluded=getattr(self,'liquidity_excluded_count',0))
            payload['checkpoint_export_latency_ms']=sum(self.checkpoint_latencies)/len(self.checkpoint_latencies) if self.checkpoint_latencies else None
            payload['checkpoint_pack_latency_ms']=sum(self.checkpoint_pack_latencies)/len(self.checkpoint_pack_latencies) if self.checkpoint_pack_latencies else None
            self.status=payload['status']
            self.previous_heartbeat=time.monotonic();self.previous_calculations=self.calculations
            await self.blocking(self.repo.heartbeat,'engine' if self.shard_count==1 else f'engine:{self.shard_index}',payload)
            await asyncio.sleep(10)
    async def persist(self,engine,bar,realtime):
        context_view=self.context_view(engine.symbol)
        snapshot=await self.blocking(engine.update,bar,context_view,realtime)
        snapshot['parameter_set_id']=self.parameter_id
        snapshot['native_turnover']=bar.get('turnover')
        snapshot['universe_excluded']=False
        snapshot['native_turnover24h']=self.native_turnover24h.get(engine.symbol)
        age=max(0,now_ms()-bar.get('exchange_time',bar['received_at']))
        if realtime and age>90000:snapshot['data_health']='STALE'
        if engine.symbol in self.recovering:snapshot['data_health']='RECOVERING'
        if self.context_last and now_ms()-self.context_last>90000:snapshot['data_health']='DEGRADED'
        snapshot['btc_data_quality']='BINANCE_KLINE_WS' if self.btc_stream_ready() else 'BINANCE_REST_FALLBACK'
        if realtime and not self.btc_stream_ready():snapshot['data_health']='DEGRADED'
        if not realtime:snapshot['data_health']='RECOVERING'
        snapshot['data_quality']='FULL_REALTIME' if (engine.symbol,engine.timeframe) in self.full_charts and self.full_charts[(engine.symbol,engine.timeframe)].get('complete') else 'KLINE_REALTIME'
        snapshot['market_data_lag_ms']=age
        # Historical replay persists results for research but must never enqueue alerts.
        snapshot['replay']=not realtime
        if getattr(self,'wt_parameters',None) is not None:
            def update_wt():
                from backend.engine.wt import WTEngine
                if not hasattr(engine,'wt'):
                    engine.wt=WTEngine(engine.symbol,engine.timeframe,engine.tick_size,self.wt_parameters)
                    state=getattr(engine,'wt_checkpoint',None)
                    if state:
                        try:engine.wt.restore_state(state)
                        except ValueError:pass
                    if not engine.wt.runtime.count:
                        for past in engine.chart_bars:
                            if past['start']<bar['start']:engine.wt.update(past,context_view,False)
                value=engine.wt.update(bar,context_view,realtime,snapshot,plan_history=engine.chart_bars)
                if snapshot['data_health']!='HEALTHY':value['data_health']=snapshot['data_health']
                return value
            snapshot['wt']=await self.blocking(update_wt)
        self.calculations+=1;self.latencies.append(snapshot['calculation_ms']);self.latencies=self.latencies[-1000:]
        # Intrabar state (especially varip) is checkpointed as well as closed bars.
        if not realtime and bar['start'] <= self.replay_skip_until.get((engine.symbol,engine.timeframe),-1):return snapshot
        checkpoint=None
        if realtime or engine.runtime.count % 50 == 0:
            export_started=time.perf_counter()
            # The symbol lock still covers export + save. Do not block feed and
            # lease heartbeats on this CPU-heavy traversal; cancellation drains it.
            state=await self.blocking(engine.export_state)
            state.get('snapshot',{}).pop('wt',None)
            if hasattr(engine,'wt'):state['wt']=await self.blocking(engine.wt.export_state)
            self.checkpoint_latencies.append((time.perf_counter()-export_started)*1000)
            self.checkpoint_latencies=self.checkpoint_latencies[-1000:]
            pack_started=time.perf_counter()
            checkpoint=await self.blocking(PackedCheckpoint.from_state,state)
            self.checkpoint_pack_latencies.append((time.perf_counter()-pack_started)*1000)
            self.checkpoint_pack_latencies=self.checkpoint_pack_latencies[-1000:]
        write_started=time.perf_counter()
        await self.blocking(self.repo.save_snapshot,snapshot,checkpoint)
        self.db_latencies.append((time.perf_counter()-write_started)*1000);self.db_latencies=self.db_latencies[-1000:]
        if realtime:await self.redis.publish('setups',json.dumps({'type':'snapshot','data':{k:v for k,v in snapshot.items() if k!='wt'}},ensure_ascii=False))
        if realtime and snapshot.get('wt'):
            await self.redis.publish('setups',json.dumps({'type':'wt_snapshot','data':{k:v for k,v in snapshot['wt'].items() if k not in ('plan_updates','research_updates')}},ensure_ascii=False))
        return snapshot
    def btc_stream_ready(self):
        _,tfs=context_requirements(self.parameters,self.timeframes,getattr(self,'wt_parameters',None))
        return (self.btc_ws.health.connected and not self.btc_recovering
                and all(now_ms()-self.btc_last_events.get(tf,0)<90000 for tf in tfs))

    async def on_btc(self,event):
        if self.stopping:return
        if event['type']=='health':
            self.btc_recovering=True
            if event['connected']:self.launch(self.refresh_btc())
            return
        value=bar_dict(event['bar']);value['exchange_time']=event['exchange_time']
        key=f"BINANCE:BTCUSDT.P|{value['timeframe']}"
        rows=self.contexts.get(key,[])
        # Drop delayed revisions of an open candle. A closing update wins over
        # an open sample, but a late open sample cannot undo that confirmation.
        previous=next((b for b in reversed(rows) if b['start']==value['start']),None)
        if previous and previous.get('exchange_time',0)>value['exchange_time']:return
        self.put_context(key,value)
        self.btc_last_events[value['timeframe']]=event['received_at']
        self.context_last=event['received_at']

    async def refresh_btc(self,initial=False):
        async with self.btc_refresh_lock:
            _,tfs=context_requirements(self.parameters,self.timeframes,getattr(self,'wt_parameters',None))
            for tf in sorted(tfs,key=tf_seconds):
                key=f'BINANCE:BTCUSDT.P|{tf}';rows=self.contexts.get(key,[])
                last_closed=next((b['end'] for b in reversed(rows) if b['confirmed']),None)
                history=max(500,int(self.history_span_ms/(tf_seconds(tf)*1000))+500) if initial else max(3,int((now_ms()-(last_closed or now_ms()))/(tf_seconds(tf)*1000))+3)
                requested_at=now_ms()
                bars=await self.btc.backfill('BTCUSDT',tf,history)
                if not bars:raise RuntimeError(f'Empty Binance BTC context: {tf}')
                for previous_bar,next_bar in zip(bars,bars[1:]):
                    if next_bar.start!=previous_bar.end:raise RuntimeError(f'Missing Binance {tf} candle at {previous_bar.end}')
                for b in bars:
                    value=bar_dict(b)
                    previous=next((row for row in reversed(self.contexts.get(key,[])) if row['start']==b.start),None)
                    # A REST response must not overwrite WS data received while
                    # its request was in flight with an older open sample.
                    if not b.confirmed and previous and previous.get('received_at',0)>=requested_at:continue
                    self.put_context(key,value)
            self.context_last=now_ms();self.errors.pop('context',None)
            self.btc_recovering=not self.btc_ws.health.connected
    async def bootstrap(self,instrument,recover=False):
        symbol=instrument.symbol
        async with self.locks[symbol]:
            if symbol not in self.instruments:return
            self.recovering.add(symbol)
            succeeded=False
            try:
                saved_states={}
                # A semantics upgrade invalidates old checkpoints. Fetch from
                # their original chart origin before replay, with alerts muted
                # through the last saved candle (the existing skip mechanism).
                for tf in self.timeframes:
                    key=(symbol,tf)
                    if key in self.engines:continue
                    def saved_state():
                        with self.repo.session() as session:
                            row=session.get(Current,('BYBIT',symbol,tf))
                            return (self.repo.read_checkpoint(row),row.payload) if row else (None,None)
                    checkpoint,saved=await self.blocking(saved_state)
                    saved_states[tf]=(checkpoint,saved)
                    if checkpoint and getattr(self,'wt_parameters',None):
                        from backend.engine.wt import WTEngine
                        probe=WTEngine(symbol,tf,instrument.tick_size,self.wt_parameters)
                        try:probe.restore_state(checkpoint.get('wt',{}))
                        except ValueError:
                            # A rejected WT state cannot authorize incremental
                            # context backfill, even when BROKE itself resumes.
                            checkpoint['wt']=None
                            if self.wt_parameters.get('useBrokeCorrelation') and isinstance(checkpoint.get('first_bar_start'),int):
                                self.replay_origins[key]=checkpoint['first_bar_start']
                    if checkpoint and checkpoint.get('parameter_hash')==parameter_hash(self.parameters) and (checkpoint.get('version')!=ENGINE_VERSION or checkpoint.get('pine_source_hash')!=PINE_HASH):
                        origin=checkpoint.get('first_bar_start')
                        if isinstance(origin,int):
                            self.replay_origins[key]=origin
                            log.info('checkpoint_semantics_upgrade_replay',extra={'symbol':symbol,'timeframe':tf,'history_start':origin,'previous_version':checkpoint.get('version'),'engine_version':ENGINE_VERSION})
                own,_=context_requirements(self.parameters,self.timeframes,getattr(self,'wt_parameters',None))
                origins=[origin for (s,t),origin in self.replay_origins.items() if s==symbol]
                rebuild_start=min(origins) if origins else None
                if rebuild_start is not None and now_ms()-rebuild_start>self.history_span_ms:
                    self.history_span_ms=now_ms()-rebuild_start
                    self.context_limit=max(self.context_limit,int(self.history_span_ms/30000)+1000)
                    await self.refresh_btc(True)
                # Validate/restore before choosing REST depth. A rejected or
                # partial checkpoint cannot authorize a shortened bootstrap.
                for tf in self.timeframes:
                    key=(symbol,tf)
                    if key not in self.engines:
                        engine=PineEngine(symbol,tf,instrument.tick_size,self.parameters)
                        checkpoint,saved=saved_states[tf]
                        if saved and saved.get('parameter_set_id')==self.parameter_id:self.replay_skip_until[key]=saved['bar_start']
                        if checkpoint and key not in self.replay_origins:
                            try:
                                engine.restore_state(checkpoint)
                                engine.wt_checkpoint=checkpoint.get('wt')
                                log.info('checkpoint_restored',extra={'symbol':symbol,'timeframe':tf,'processed':engine.runtime.count,'bar_timestamp':engine.runtime.last_start})
                            except ValueError as exc:
                                log.warning('checkpoint_requires_replay',extra={'symbol':symbol,'timeframe':tf,'error':str(exc)})
                                engine=PineEngine(symbol,tf,instrument.tick_size,self.parameters)
                        self.engines[key]=engine
                resume_start=None if rebuild_start is not None or recover else restored_context_anchor(
                    [self.engines[symbol,tf] for tf in self.timeframes],now_ms(),self.history_span_ms)
                if getattr(self,'wt_parameters',None) is not None and any(not getattr(self.engines[symbol,tf],'wt_checkpoint',None) for tf in self.timeframes):resume_start=None
                if resume_start is not None:
                    log.info('checkpoint_incremental_backfill',extra={'symbol':symbol,'history_start':resume_start})
                for tf in sorted(own|set(self.timeframes),key=tf_seconds):
                    if tf=='30S':
                        def stored_micro():
                            with self.repo.session() as session:
                                rows=session.scalars(select(MarketBar).where(MarketBar.exchange=='BYBIT',MarketBar.symbol==symbol,MarketBar.timeframe=='30S').order_by(MarketBar.start.desc()).limit(self.context_limit)).all()
                                return [{c.name:getattr(row,c.name) for c in MarketBar.__table__.columns} for row in reversed(rows)]
                        for value in await self.blocking(stored_micro):self.put_context(f'BYBIT:{symbol}.P|30S',value)
                        continue
                    count=max(required_history(self.parameters,tf),int(self.history_span_ms/(tf_seconds(tf)*1000))+100)
                    if rebuild_start is not None:count=max(count,int((now_ms()-rebuild_start)/(tf_seconds(tf)*1000))+500)
                    if resume_start is not None:count=max(10,int((now_ms()-resume_start)/(tf_seconds(tf)*1000))+5)
                    if recover and rebuild_start is None and resume_start is None:count=max(10,int((now_ms()-min((e.runtime.last_start or now_ms()) for (s,t),e in self.engines.items() if s==symbol))/(tf_seconds(tf)*1000))+5)
                    bars=await self.bybit.backfill(symbol,tf,count)
                    values=[bar_dict(b) for b in bars]
                    for value in values:self.put_context(f'BYBIT:{symbol}.P|{tf}',value)
                    if values:await self.blocking(self.repo.save_bars,values)
                for tf in self.timeframes:
                    key=(symbol,tf)
                    engine=self.engines[key]
                    rows=self.contexts.get(f'BYBIT:{symbol}.P|{tf}',[])
                    limit=required_history(self.parameters,tf)
                    if key in self.replay_origins:
                        rows=[bar for bar in rows if bar['start']>=self.replay_origins[key]]
                        if not rows or rows[0]['start']!=self.replay_origins[key]:raise RuntimeError('Original history origin unavailable for semantics replay')
                    else:rows=rows[-limit:] if engine.runtime.last_start is None else rows
                    if engine.runtime.last_start is not None and rows and rows[0]['start']>engine.runtime.last_start+tf_seconds(tf)*1000:
                        raise RuntimeError('Recovery gap exceeds loaded history; refusing to jump engine state')
                    for previous_bar,next_bar in zip(rows,rows[1:]):
                        if next_bar['start'] != previous_bar['end']:raise RuntimeError(f'Missing {tf} candle between {previous_bar["end"]} and {next_bar["start"]}')
                    for bar in rows:
                        if engine.runtime.last_start is not None and bar['start']<=engine.runtime.last_start:continue
                        if bar['confirmed']:await self.persist(engine,bar,False)
                    if rows and not rows[-1]['confirmed']:
                        await self.persist(engine,rows[-1],True)
                self.ready.add(symbol);self.errors.pop(symbol,None);succeeded=True;self.backfilled[symbol]=now_ms()
                for key in list(self.replay_origins):
                    if key[0]==symbol:del self.replay_origins[key]
                log.info('instrument_ready',extra={'symbol':symbol,'processed':len(self.ready),'total':self.selected_count})
            except Exception as exc:
                self.errors[symbol]=str(exc);log.exception('instrument_bootstrap_failed',extra={'symbol':symbol})
            finally:
                if succeeded:self.recovering.discard(symbol)
    async def rebuild(self,symbol,reason):
        if symbol in self.recovering:return
        self.recovering.add(symbol)
        self.errors[symbol]=reason
        async with self.locks[symbol]:
            for key in list(self.engines):
                if key[0]==symbol:
                    engine=self.engines.pop(key)
                    if engine.first_bar_start is not None:self.replay_origins[key]=engine.first_bar_start
                    if engine.snapshot:
                        snapshot=dict(engine.snapshot);snapshot.update(data_health='RECOVERING',signals=[],recovery_reason=reason)
                        await self.blocking(self.repo.save_snapshot,snapshot)
        await self.bootstrap(self.instruments[symbol])

    async def on_market(self,event):
        if self.stopping:return
        self.messages+=1;kind=event['type']
        if kind=='health':
            symbols={t.split('.')[-1] for t in event['topics']}
            if not event['connected']:
                self.reconnects+=1
                # Invalidate every affected symbol before the first SQL await.
                self.recovering.update(symbols)
                for symbol in symbols:
                    self.recovering.add(symbol);self.full_since.pop(symbol,None)
                    for key in list(self.full_charts):
                        if key[0]==symbol:del self.full_charts[key]
                    if symbol in self.aggregators:self.aggregators[symbol].disconnected()
                    for (s,tf),engine in list(self.engines.items()):
                        if s==symbol and engine.snapshot:
                            state=dict(engine.snapshot);state.update(data_health='RECOVERING',signals=[])
                            await self.blocking(self.repo.save_snapshot,state)
            else:
                for symbol in symbols & self.ready & self.recovering:
                    self.launch(self.bootstrap(self.instruments[symbol],True))
            return
        if kind=='trade':
            symbol=event['symbol'];self.last_events[symbol]=event['received_at']
            agg=self.aggregators.setdefault(symbol,TradeAggregator(symbol))
            for trade in event['trades']:
                late_before=agg.late_trades
                if not agg.add(trade):
                    if agg.late_trades>late_before:
                        self.full_since.pop(symbol,None)
                        for key in list(self.full_charts):
                            if key[0]==symbol:del self.full_charts[key]
                    continue
                ts=int(trade['T']);price=float(trade['p']);volume=float(trade['v'])
                for micro,complete in agg.flush(ts,event['received_at']):
                    if complete:
                        value=bar_dict(micro);self.put_context(f'BYBIT:{symbol}.P|30S',value);await self.blocking(self.repo.save_bar,value)
                self.full_since.setdefault(symbol,ts)
                for tf in self.timeframes:
                    duration=tf_seconds(tf)*1000;start=ts//duration*duration;key=(symbol,tf)
                    candle=self.full_charts.get(key)
                    if candle is None or candle['start']<start:
                        candle={'exchange':'BYBIT','symbol':symbol,'timeframe':tf,'start':start,'end':start+duration,'open':price,'high':price,'low':price,'close':price,'volume':0.,'turnover':0.,'confirmed':False,'complete':self.full_since[symbol]<start,'first_trade_time':ts,'last_trade_time':ts};self.full_charts[key]=candle
                    if candle['start']!=start:continue
                    candle['high']=max(candle['high'],price);candle['low']=min(candle['low'],price);candle['volume']+=volume;candle['turnover']+=price*volume;candle['received_at']=event['received_at'];candle['exchange_time']=ts
                    if ts < candle['first_trade_time']:candle['open']=price;candle['first_trade_time']=ts
                    if ts >= candle['last_trade_time']:candle['close']=price;candle['last_trade_time']=ts
                    if candle['complete'] and symbol in self.ready:
                        # Every trade updates the baseline; no throttling of varip sampling.
                        await self.calculate(dict(candle),True)
            for b,complete in agg.flush(event['exchange_time'],event['received_at']):
                if complete:
                    value=bar_dict(b);self.put_context(f'BYBIT:{symbol}.P|30S',value);await self.blocking(self.repo.save_bar,value)
            return
        bar=bar_dict(event['bar']);bar['exchange_time']=event.get('exchange_time') or event['received_at'];symbol=bar['symbol'];tf=bar['timeframe'];self.last_events[symbol]=event['received_at']
        self.put_context(f'BYBIT:{symbol}.P|{tf}',bar)
        if bar['confirmed']:
            await self.blocking(self.repo.save_bar,bar)
            engine=self.engines.get((symbol,tf))
            if engine and engine.runtime.last_start is not None and bar['start']<=engine.runtime.last_start:
                old=next((b for b in reversed(engine.chart_bars) if b['start']==bar['start']),None)
                if old and any(old[k]!=bar[k] for k in ('open','high','low','close','volume')):
                    self.launch(self.rebuild(symbol,'Authoritative confirmed kline revised; deterministic historical replay'))
                    return
            trade=self.full_charts.get((symbol,tf))
            if trade and trade.get('complete') and trade['start']==bar['start']:
                differences={k:trade[k]-bar[k] for k in ('open','high','low','close','volume') if abs(trade[k]-bar[k])>max(self.instruments[symbol].tick_size if k!='volume' else 1e-8,1e-10)}
                if differences:self.reconciliation_errors+=1;log.warning('reconciliation_error',extra={'symbol':symbol,'timeframe':tf,'error':str(differences)})
        if symbol not in self.ready or tf not in self.timeframes:return
        trade=self.full_charts.get((symbol,tf))
        if not bar['confirmed'] and trade and trade.get('complete') and trade['start']==bar['start']:return
        # KLINE_REALTIME's sampling is the native WebSocket stream; don't invent ticks.
        await self.calculate(bar,True)
    async def calculate(self,bar,realtime):
        if self.stopping:return
        symbol=bar['symbol'];key=(symbol,bar['timeframe']);engine=self.engines.get(key)
        if engine is None or symbol in self.recovering:return
        async with self.locks[symbol]:
            if self.engines.get(key) is not engine:return
            if engine.runtime.last_start is not None and bar['start']<=engine.runtime.last_start:return
            if engine.runtime.last_start is not None and bar['start']>engine.runtime.last_start+tf_seconds(bar['timeframe'])*1000:
                self.recovering.add(symbol);self.launch(self.bootstrap(self.instruments[symbol],True));return
            try:await self.persist(engine,bar,realtime)
            except Exception as exc:
                self.errors[symbol]=str(exc);self.recovering.add(symbol);log.exception('calculation_failed',extra={'symbol':symbol,'timeframe':bar['timeframe']})
    async def subscriptions(self):
        own,_=context_requirements(self.parameters,self.timeframes,getattr(self,'wt_parameters',None))
        pinned={s.strip() for s in os.getenv('PINNED_SYMBOLS','').split(',') if s.strip()}
        active={s for (s,t),e in self.engines.items() if e.snapshot and e.snapshot.get('direction')!='NONE'}
        maximum=int(os.getenv('MAX_FULL_REALTIME_SYMBOLS','20'))
        previous_full=set(self.full)
        self.full=set(sorted((pinned|active)&self.ready)[:maximum])
        # 1m needs trade coverage even before a setup; capacity shortfall stays DEGRADED.
        if '1' in self.timeframes:self.full.update(sorted(self.ready-self.full)[:max(0,maximum-len(self.full))])
        for symbol in previous_full-self.full:
            self.full_since.pop(symbol,None)
            if symbol in self.aggregators:self.aggregators[symbol].disconnected()
            for key in list(self.full_charts):
                if key[0]==symbol:del self.full_charts[key]
        await self.ws.update_subscriptions(sorted(self.ready),sorted(own|set(self.timeframes),key=tf_seconds),self.full)
    async def discover(self):
        instruments=await self.bybit.instruments();self.universe_count=len(instruments)
        tickers=await self.bybit.tickers();self.native_turnover24h=turnover_values(tickers)
        settings=await self.blocking(self.repo.settings)
        minimum=settings['universe_min_turnover24h_usdt']
        self.universe_min_turnover=minimum
        metadata=[{**item.to_dict(),'native_turnover24h':self.native_turnover24h.get(item.symbol)} for item in instruments]
        await self.blocking(self.repo.save_instruments,metadata,True)
        excluded=[i.symbol for i in instruments if minimum>0 and self.native_turnover24h.get(i.symbol,-1)<minimum]
        self.liquidity_excluded_count=len(excluded)
        excluded_set=set(excluded)
        instruments=[i for i in instruments if i.symbol not in excluded_set]
        instruments=sorted(instruments,key=lambda x:(-self.native_turnover24h.get(x.symbol,0),x.symbol))
        maximum=int(os.getenv('MAX_SYMBOLS','0'))
        if maximum:instruments=instruments[:maximum]
        selected=[i for i in instruments if int(hashlib.sha256(i.symbol.encode()).hexdigest(),16)%self.shard_count==self.shard_index]
        self.instruments={i.symbol:i for i in selected};self.selected_count=len(selected)
        removed=(self.ready|{s for s,t in self.engines})-set(self.instruments)
        for symbol in removed:
            async with self.locks[symbol]:
                self.ready.discard(symbol);self.recovering.discard(symbol)
                self.errors.pop(symbol,None);self.aggregators.pop(symbol,None)
                self.full.discard(symbol);self.full_since.pop(symbol,None);self.last_events.pop(symbol,None)
                for mapping in (self.engines,self.full_charts,self.replay_origins,self.replay_skip_until):
                    for key in list(mapping):
                        if key[0]==symbol:del mapping[key]
                for key in list(self.contexts):
                    if key.startswith(f'BYBIT:{symbol}.P|'):
                        del self.contexts[key];self.context_views.pop(key,None)
        await self.subscriptions()
        await self.blocking(self.repo.exclude_from_universe,sorted(set(excluded)|removed))
        for instrument in selected:
            if instrument.symbol not in self.ready:
                await self.bootstrap(instrument)
                await self.subscriptions()
        await self.subscriptions()
    async def maintain(self):
        while True:
            try:
                await self.refresh_btc()
                current=await self.blocking(self.repo.parameters)
                from backend.wt import config as wt_config
                wt_changed=(await self.blocking(wt_config,self.repo))['values']!=self.wt_parameters
                if current['id']!=self.parameter_id or wt_changed:
                    log.info('parameter_version_changed_replay_required')
                    # Restart the worker to apply the new version through the same
                    # deterministic bootstrap path. Old parameter snapshots remain.
                    os._exit(75)
                await self.subscriptions()
                self.status=self.runtime_health()['status']
            except Exception as exc:
                self.status='DEGRADED';log.exception('context_refresh_failed');self.errors['context']=str(exc)
            await asyncio.sleep(float(os.getenv('CONTEXT_REFRESH_SEC','15')))
    async def shutdown(self, beat=None, maintenance=None, lease=None):
        self.stopping=True;self.status='RECOVERING'
        pending=[task for task in (maintenance,*self.background) if task is not None]
        for task in pending:task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
        # close() waits for receiver callbacks, including shielded DB writes.
        # Intentional close must not schedule recovery or rewrite every setup.
        await self.ws.close();await self.btc_ws.close()
        await self.bybit.close();await self.btc.close()
        if beat:
            beat.cancel()
            await asyncio.gather(beat,return_exceptions=True)
        name='engine' if self.shard_count==1 else f'engine:{self.shard_index}'
        await self.blocking(self.repo.heartbeat,name,{'status':'RECOVERING','reason':'worker stopped','parity_status':'UNVERIFIED'})
        if lease:
            lease.cancel()
            await asyncio.gather(lease,return_exceptions=True)
        await self.redis.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end",1,self.lease_key,self.lease_token)
        await self.redis.aclose()
        log.info('engine_shutdown_complete')

    async def run(self):
        current_task=asyncio.current_task()
        loop=asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM,current_task.cancel)
        self.lease_key=f'brokeweb:engine-shard:{self.shard_count}:{self.shard_index}'
        if not await self.redis.set(self.lease_key,self.lease_token,nx=True,px=60000):
            raise RuntimeError('Another worker owns this engine shard')
        def lease_done(task):
            if not task.cancelled() and task.exception() is not None:
                log.error('lease_renewal_failed_stopping_shard');os._exit(77)
        lease=asyncio.create_task(self.renew_lease())
        lease.add_done_callback(lease_done)
        beat=None;maintenance=None
        try:
            await self.blocking(self.repo.initialize)
            params=await self.blocking(self.repo.parameters);self.parameters=params['values'];self.parameter_id=params['id']
            from backend.wt import config as wt_config
            self.wt_parameters=(await self.blocking(wt_config,self.repo))['values']
            self.history_span_ms=max(required_history(self.parameters,tf)*tf_seconds(tf)*1000 for tf in self.timeframes)
            self.context_limit=max(5000,int(self.history_span_ms/30000)+1000)
            beat=asyncio.create_task(self.heartbeat())
            def heartbeat_done(task):
                if not task.cancelled() and task.exception() is not None:
                    log.error('heartbeat_failed_stopping_shard');os._exit(77)
            beat.add_done_callback(heartbeat_done)
            await self.refresh_btc(True)
            _,btc_tfs=context_requirements(self.parameters,self.timeframes,getattr(self,'wt_parameters',None))
            self.btc_ws.start(btc_tfs)
            maintenance=asyncio.create_task(self.maintain())
            while True:
                await self.discover()
                await asyncio.sleep(float(os.getenv('UNIVERSE_REFRESH_SEC','3600')))
        except asyncio.CancelledError:
            log.info('engine_shutdown_requested')
        finally:
            # A second SIGTERM must not cancel the drain and release ownership
            # while a previous thread is still mutating state.
            loop.add_signal_handler(signal.SIGTERM,lambda:None)
            await self.shutdown(beat,maintenance,lease)
            loop.remove_signal_handler(signal.SIGTERM)

if __name__=='__main__':
    configure_logging();asyncio.run(Worker().run())
