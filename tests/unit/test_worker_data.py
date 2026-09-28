"""Feed/recovery regression tests without exchange, PostgreSQL or Redis calls."""
import asyncio
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from backend.worker import Worker
from backend.marketdata.models import Bar


def worker_shell():
    worker=Worker.__new__(Worker)
    worker.stopping=False;worker.background=set()
    worker.messages=0;worker.reconnects=0;worker.last_events={}
    worker.full_since={};worker.full_charts={};worker.aggregators={}
    worker.ready={'XUSDT'};worker.recovering=set();worker.full={'XUSDT'}
    worker.instruments={'XUSDT':SimpleNamespace(symbol='XUSDT',tick_size=.01)}
    worker.timeframes=['1'];worker.engines={};worker.errors={}
    worker.locks=defaultdict(asyncio.Lock)
    worker.contexts={};worker.context_views={};worker.context_limit=100;worker.history_span_ms=600000
    worker.parameters={};worker.replay_origins={};worker.replay_skip_until={};worker.calculate=AsyncMock()
    worker.repo=SimpleNamespace(save_bar=lambda bar:None,save_bars=lambda bars:None,load_checkpoint=lambda *args:None)
    return worker


@pytest.mark.parametrize('case', ['fresh','stopped','lagged','missing_timestamp','recovering','missing_engine','disconnected','stream_lagged','empty','btc_missing'])
def test_runtime_health_requires_fresh_complete_coverage(monkeypatch,case):
    import backend.worker as module
    monkeypatch.setattr(module,'now_ms',lambda:200000)
    worker=worker_shell();worker.selected_count=1
    snapshot={'data_health':'HEALTHY','calculation_timestamp':199000,'market_data_lag_ms':500}
    worker.engines['XUSDT','1']=SimpleNamespace(snapshot=snapshot)
    stream=SimpleNamespace(connected=True,last_market_event=199000)
    worker.ws=SimpleNamespace(health={0:stream});worker.btc_stream_ready=lambda:case!='btc_missing'
    if case=='stopped':snapshot['calculation_timestamp']=100000
    if case=='lagged':snapshot['market_data_lag_ms']=100000
    if case=='missing_timestamp':snapshot.pop('calculation_timestamp')
    if case=='recovering':worker.recovering.add('XUSDT')
    if case=='missing_engine':worker.timeframes.append('5')
    if case=='disconnected':stream.connected=False
    if case=='stream_lagged':stream.last_market_event=100000
    if case=='empty':worker.engines.clear();worker.selected_count=0;worker.ready.clear()
    result=worker.runtime_health()
    assert result['status']==('HEALTHY' if case=='fresh' else 'RECOVERING')
    if case=='fresh':assert result['market_data_lag_ms']==1500
    if case in ('stopped','lagged','missing_timestamp'):
        assert result['healthy_engines']==0 and result['stale_instruments']==1


def test_checkpoint_export_runs_off_event_loop_and_finishes_before_save():
    import threading
    async def scenario():
        worker=worker_shell();events=[];loop_thread=threading.get_ident()
        worker.context_last=0;worker.parameter_id='params';worker.native_turnover24h={}
        worker.btc_stream_ready=lambda:True
        worker.calculations=0;worker.latencies=[];worker.checkpoint_latencies=[];worker.checkpoint_pack_latencies=[];worker.db_latencies=[]
        worker.redis=SimpleNamespace(publish=AsyncMock())
        def update(*args):return {'calculation_ms':1,'data_health':'HEALTHY'}
        def export():
            assert threading.get_ident()!=loop_thread
            events.append('export');return {'varip':{'samples':17}}
        def save(snapshot,state):
            from backend.models.checkpoints import PackedCheckpoint,unpack_checkpoint
            assert events==['export'] and isinstance(state,PackedCheckpoint)
            assert unpack_checkpoint(state.blob)=={'varip':{'samples':17}}
            events.append('save')
        worker.repo.save_snapshot=save
        engine=SimpleNamespace(symbol='XUSDT',timeframe='1',update=update,export_state=export)
        await worker.persist(engine,{'start':0,'received_at':0},True)
        assert events==['export','save'] and len(worker.checkpoint_latencies)==1
        worker.redis.publish.assert_awaited_once()
    asyncio.run(scenario())


def trade(ts,price,uid):return {'T':ts,'p':str(price),'v':'1','i':uid,'S':'Buy','s':'XUSDT'}


def test_worker_disconnect_invalidates_complete_trade_candle():
    async def scenario():
        worker=worker_shell()
        worker.full_since['XUSDT']=0
        worker.full_charts['XUSDT','1']={'complete':True,'start':60000}
        await worker.on_market({'type':'health','topics':['publicTrade.XUSDT'],'connected':False})
        assert 'XUSDT' in worker.recovering
        assert ('XUSDT','1') not in worker.full_charts or not worker.full_charts['XUSDT','1']['complete']
        assert 'XUSDT' not in worker.full_since
    asyncio.run(scenario())


def test_worker_fresh_connection_recovers_affected_symbol_even_zero_reconnect_counter():
    async def scenario():
        worker=worker_shell();worker.recovering.add('XUSDT');worker.bootstrap=AsyncMock()
        await worker.on_market({'type':'health','topics':['kline.1.XUSDT'],'connected':True,'reconnects':0})
        await asyncio.sleep(0)
        worker.bootstrap.assert_awaited_once_with(worker.instruments['XUSDT'],True)
    asyncio.run(scenario())


def test_worker_full_candle_open_close_follow_trade_time_not_delivery_order():
    async def scenario():
        worker=worker_shell();worker.full_since['XUSDT']=0
        await worker.on_market({'type':'trade','symbol':'XUSDT','trades':[trade(70000,12,'first'),trade(65000,8,'earlier')],'exchange_time':71000,'received_at':71000})
        candle=worker.full_charts['XUSDT','1']
        assert candle['complete'] is True
        assert (candle['open'],candle['close'],candle['high'],candle['low'],candle['volume'])==(8,12,12,8,2)
    asyncio.run(scenario())


def test_worker_failed_recovery_must_remain_recovering(monkeypatch):
    import backend.worker as module
    monkeypatch.setattr(module,'context_requirements',lambda *args:({'1'},set()))
    async def scenario():
        worker=worker_shell();worker.engines['XUSDT','1']=SimpleNamespace(runtime=SimpleNamespace(last_start=0))
        worker.bybit=SimpleNamespace(backfill=AsyncMock(side_effect=RuntimeError('exchange unavailable')))
        await worker.bootstrap(worker.instruments['XUSDT'],True)
        assert 'XUSDT' in worker.errors
        assert 'XUSDT' in worker.recovering
    asyncio.run(scenario())


def test_worker_history_gap_inside_backfill_is_not_silently_replayed(monkeypatch):
    import backend.worker as module
    monkeypatch.setattr(module,'context_requirements',lambda *args:({'1'},set()))
    monkeypatch.setattr(module,'required_history',lambda *args:10)
    class Engine:
        def __init__(self,*args):self.runtime=SimpleNamespace(last_start=None)
    monkeypatch.setattr(module,'PineEngine',Engine)
    async def scenario():
        worker=worker_shell();worker.ready.clear();worker.engines['XUSDT','1']=Engine()
        worker.bybit=SimpleNamespace(backfill=AsyncMock(return_value=[Bar('BYBIT','XUSDT','1',ts,ts+60000,10,12,8,11,5) for ts in (0,120000)]))
        worker.persist=AsyncMock()
        await worker.bootstrap(worker.instruments['XUSDT'])
        assert 'XUSDT' in worker.errors
        assert 'XUSDT' in worker.recovering
        assert 'XUSDT' not in worker.ready
        assert 'Missing 1 candle' in worker.errors['XUSDT']
    asyncio.run(scenario())


def test_cancellation_waits_for_actual_thread_completion():
    import threading
    async def scenario():
        worker=worker_shell();started=threading.Event();finish=threading.Event();completed=[]
        def transaction():
            started.set()
            assert finish.wait(2)
            completed.append(True)
        task=asyncio.create_task(worker.blocking(transaction))
        assert await asyncio.to_thread(started.wait,2)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done(), 'Cancellation must not detach a running transaction'
        finish.set()
        result=await asyncio.gather(task,return_exceptions=True)
        assert isinstance(result[0],asyncio.CancelledError)
        assert completed==[True]
    asyncio.run(scenario())


def test_shutdown_drains_recovery_and_receivers_before_releasing_lease():
    import threading
    async def scenario():
        worker=worker_shell();events=[];started=threading.Event();finish=threading.Event()
        def transaction():
            started.set()
            assert finish.wait(2)
            events.append('transaction finished')
        worker.launch(worker.blocking(transaction))
        assert await asyncio.to_thread(started.wait,2)
        async def close_feed():
            events.append('feed closed')
            # This is the health callback emitted by an intentional WS close.
            await worker.on_market({'type':'health','topics':['kline.1.XUSDT'],'connected':False})
        renewals=[]
        async def release(script,*args):
            if 'pexpire' in script:
                renewals.append(True)
                return 1
            events.append('lease released')
        worker.ws=SimpleNamespace(close=close_feed)
        worker.bybit=SimpleNamespace(close=AsyncMock());worker.btc=SimpleNamespace(close=AsyncMock());worker.btc_ws=SimpleNamespace(close=AsyncMock())
        worker.redis=SimpleNamespace(eval=release,aclose=AsyncMock())
        worker.repo.heartbeat=lambda *args:events.append('stopped heartbeat')
        worker.shard_count=1;worker.shard_index=0;worker.lease_key='lease';worker.lease_token='owner'
        lease=asyncio.create_task(worker.renew_lease(interval=.005))
        stop=asyncio.create_task(worker.shutdown(lease=lease))
        await asyncio.sleep(.02)
        assert 'lease released' not in events
        assert len(renewals)>=2, 'Ownership must renew while shutdown drains a blocked write'
        finish.set();await stop
        assert lease.cancelled()
        assert events==['transaction finished','feed closed','stopped heartbeat','lease released']
        assert worker.reconnects==0
        assert not worker.recovering
        assert not worker.background
    asyncio.run(scenario())


def test_lease_renewal_does_not_wait_for_thread_pool_or_database():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    async def scenario():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        worker=worker_shell();started=threading.Event();finish=threading.Event()
        renewed=asyncio.Event();calls=[]
        def blocked_sql():
            started.set()
            assert finish.wait(2)
        async def renew(*args):
            calls.append(args)
            if len(calls)>=3:renewed.set()
            return 1
        worker.redis=SimpleNamespace(eval=renew)
        worker.lease_key='shard';worker.lease_token='owner'
        write=asyncio.create_task(worker.blocking(blocked_sql))
        lease=asyncio.create_task(worker.renew_lease(interval=.005))
        try:
            await asyncio.wait_for(renewed.wait(),1)
            assert started.is_set() and not write.done()
            assert all(args[1:]==(1,'shard','owner') for args in calls)
            assert all("== ARGV[1]" in args[0] for args in calls)
        finally:
            finish.set();await write
            lease.cancel();await asyncio.gather(lease,return_exceptions=True)
    asyncio.run(scenario())


def test_lease_loss_still_stops_worker(monkeypatch):
    def exit_worker(code):
        assert code==76
        raise RuntimeError('worker stopped')
    monkeypatch.setattr('backend.worker.os._exit',exit_worker)
    async def scenario():
        worker=worker_shell();worker.lease_key='shard';worker.lease_token='owner'
        worker.redis=SimpleNamespace(eval=AsyncMock(return_value=0))
        with pytest.raises(RuntimeError,match='worker stopped'):
            await worker.renew_lease()
        worker.redis.eval.assert_awaited_once()
    asyncio.run(scenario())


def test_semantics_upgrade_replays_original_origin_and_rejects_truncated_history(monkeypatch):
    from contextlib import contextmanager
    from backend.engine.parameters import validate_parameters, parameter_hash
    import backend.worker as module
    monkeypatch.setattr(module,'context_requirements',lambda *args:({'1'},set()))
    monkeypatch.setattr(module,'required_history',lambda *args:2)
    monkeypatch.setattr(module,'now_ms',lambda:240000)
    class Engine:
        def __init__(self,*args):self.runtime=SimpleNamespace(last_start=None)
        def restore_state(self,*args):raise AssertionError('Old semantic state must not be restored')
    monkeypatch.setattr(module,'PineEngine',Engine)
    async def scenario(truncated):
        worker=worker_shell();worker.ready.clear();worker.backfilled={};worker.selected_count=1
        worker.history_span_ms=120000;worker.parameter_id='params'
        worker.parameters=validate_parameters({})
        checkpoint={'version':'old-semantics','pine_source_hash':module.PINE_HASH,
                    'parameter_hash':parameter_hash(worker.parameters),'first_bar_start':0}
        saved=SimpleNamespace(payload={'parameter_set_id':'params','bar_start':120000})
        @contextmanager
        def session():yield SimpleNamespace(get=lambda *args:saved)
        worker.repo=SimpleNamespace(session=session,read_checkpoint=lambda row:checkpoint,save_bars=lambda bars:None)
        times=(60000,120000) if truncated else (0,60000,120000)
        worker.bybit=SimpleNamespace(backfill=AsyncMock(return_value=[Bar('BYBIT','XUSDT','1',ts,ts+60000,10,12,8,11,5) for ts in times]))
        worker.refresh_btc=AsyncMock();worker.persist=AsyncMock()
        await worker.bootstrap(worker.instruments['XUSDT'])
        worker.refresh_btc.assert_awaited_once_with(True)
        assert worker.replay_skip_until['XUSDT','1']==120000
        if truncated:
            assert 'Original history origin unavailable' in worker.errors['XUSDT']
            worker.persist.assert_not_awaited()
            assert 'XUSDT' not in worker.ready
        else:
            assert [call.args[1]['start'] for call in worker.persist.await_args_list]==[0,60000,120000]
            assert all(call.args[2] is False for call in worker.persist.await_args_list)
            assert 'XUSDT' in worker.ready
    asyncio.run(scenario(False));asyncio.run(scenario(True))


@pytest.mark.parametrize('invalid',['none','missing_request','uncommitted','cold_chart','too_old','future','valid'])
def test_restored_backfill_anchor_requires_all_committed_requests(invalid):
    from backend.worker import restored_context_anchor,checkpoint_request_ids
    streams={key+'|BYBIT:XUSDT.P|60':(SimpleNamespace(last_start=120000,count=5),[]) for key in checkpoint_request_ids()}
    first=next(iter(streams))
    engine=SimpleNamespace(runtime=SimpleNamespace(last_start=180000,count=10),provider=SimpleNamespace(streams=streams))
    streams[first][0].last_start=60000
    if invalid=='none':streams.clear()
    if invalid=='missing_request':streams.pop(first)
    if invalid=='uncommitted':streams[first][0].last_start=None
    if invalid=='cold_chart':engine.runtime.count=0
    if invalid=='too_old':streams[first][0].last_start=-60000
    if invalid=='future':streams[first][0].last_start=300000
    assert restored_context_anchor([engine],240000,240000)==(60000 if invalid=='valid' else None)


@pytest.mark.parametrize('reject',[False,True])
def test_bootstrap_validates_checkpoint_before_shortening_rest(monkeypatch,reject):
    from contextlib import contextmanager
    from backend.worker import checkpoint_request_ids
    import backend.worker as module
    monkeypatch.setattr(module,'context_requirements',lambda *args:({'1'},set()))
    monkeypatch.setattr(module,'required_history',lambda *args:500)
    monkeypatch.setattr(module,'now_ms',lambda:240000)
    class Engine:
        def __init__(self,*args):
            self.runtime=SimpleNamespace(last_start=None,count=0)
            self.provider=SimpleNamespace(streams={})
        def restore_state(self,state):
            self.runtime.last_start=120000;self.runtime.count=50
            self.provider.streams={key+'|BYBIT:XUSDT.P|1':(SimpleNamespace(last_start=60000,count=50),[]) for key in checkpoint_request_ids()}
            if reject:raise ValueError('partially restored checkpoint rejected')
    monkeypatch.setattr(module,'PineEngine',Engine)
    async def scenario():
        worker=worker_shell();worker.ready.clear();worker.backfilled={};worker.selected_count=1;worker.parameter_id='params'
        saved=SimpleNamespace(payload={'parameter_set_id':'params','bar_start':120000})
        @contextmanager
        def session():yield SimpleNamespace(get=lambda *args:saved)
        worker.repo=SimpleNamespace(session=session,read_checkpoint=lambda row:{'version':module.ENGINE_VERSION},save_bars=lambda bars:None)
        worker.bybit=SimpleNamespace(backfill=AsyncMock(return_value=[Bar('BYBIT','XUSDT','1',ts,ts+60000,10,12,8,11,5) for ts in (60000,120000,180000)]))
        worker.persist=AsyncMock()
        await worker.bootstrap(worker.instruments['XUSDT'])
        count=worker.bybit.backfill.await_args.args[2]
        assert count==(500 if reject else 10)
        expected=[60000,120000,180000] if reject else [180000]
        assert [call.args[1]['start'] for call in worker.persist.await_args_list]==expected
        assert 'XUSDT' in worker.ready and not worker.errors
    asyncio.run(scenario())


@pytest.mark.parametrize('minimum,expected', [(10_000_000,['HIGH','EDGE']),(0,['HIGH','EDGE','LOW','UNKNOWN'])])
def test_discovery_filters_before_bootstrap_and_clears_excluded_state(monkeypatch,minimum,expected):
    from backend.marketdata.models import Instrument
    async def scenario():
        worker=worker_shell();worker.shard_index=0;worker.shard_count=1
        items=[Instrument('BYBIT',name,.01,name,'USDT','Trading','LinearPerpetual',0) for name in ('LOW','HIGH','EDGE','UNKNOWN')]
        worker.bybit=SimpleNamespace(instruments=AsyncMock(return_value=items),tickers=AsyncMock(return_value=[
            {'symbol':'HIGH','turnover24h':'25000000'}, {'symbol':'EDGE','turnover24h':'10000000'},
            {'symbol':'LOW','turnover24h':'9999999'}, {'symbol':'UNKNOWN','turnover24h':'NaN'}]))
        excluded=[]
        worker.repo.settings=lambda:{'universe_min_turnover24h_usdt':minimum}
        worker.repo.save_instruments=lambda *args:None
        worker.repo.exclude_from_universe=lambda names:excluded.extend(names)
        worker.ready={'LOW'};worker.engines={('LOW','1'):SimpleNamespace(snapshot={})}
        worker.contexts={'BYBIT:LOW.P|1':[{}]};worker.context_views={'BYBIT:LOW.P|1':object()}
        worker.errors={'LOW':'old failure'}
        worker.subscriptions=AsyncMock();worker.bootstrap=AsyncMock()
        monkeypatch.setenv('MAX_SYMBOLS','0')
        await worker.discover()
        assert list(worker.instruments)==expected
        assert worker.selected_count==len(expected) and worker.universe_count==4
        assert [c.args[0].symbol for c in worker.bootstrap.await_args_list]==[s for s in expected if s!='LOW']
        if minimum:
            assert set(excluded)=={'LOW','UNKNOWN'}
            assert not worker.engines and not worker.contexts and not worker.context_views
            assert not worker.errors and not worker.ready
    asyncio.run(scenario())


def test_native_turnover_rejects_unknown_nonfinite_negative_values():
    from backend.worker import turnover_values
    assert turnover_values([{'symbol':str(i),'turnover24h':v} for i,v in enumerate([None,'','bad','NaN','inf','-1','0','12.5'])])=={'6':0.,'7':12.5}
