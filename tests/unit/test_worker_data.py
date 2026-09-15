"""Feed/recovery regression tests without exchange, PostgreSQL or Redis calls."""
import asyncio
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
    worker.contexts={};worker.context_limit=100;worker.history_span_ms=600000
    worker.parameters={};worker.replay_origins={};worker.replay_skip_until={};worker.calculate=AsyncMock()
    worker.repo=SimpleNamespace(save_bar=lambda bar:None,save_bars=lambda bars:None,load_checkpoint=lambda *args:None)
    return worker


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
        async def release(*args):events.append('lease released')
        worker.ws=SimpleNamespace(close=close_feed)
        worker.bybit=SimpleNamespace(close=AsyncMock());worker.btc=SimpleNamespace(close=AsyncMock());worker.btc_ws=SimpleNamespace(close=AsyncMock())
        worker.redis=SimpleNamespace(eval=release,aclose=AsyncMock())
        worker.repo.heartbeat=lambda *args:events.append('stopped heartbeat')
        worker.shard_count=1;worker.shard_index=0;worker.lease_key='lease';worker.lease_token='owner'
        stop=asyncio.create_task(worker.shutdown())
        await asyncio.sleep(.02)
        assert 'lease released' not in events
        finish.set();await stop
        assert events==['transaction finished','feed closed','stopped heartbeat','lease released']
        assert worker.reconnects==0
        assert not worker.recovering
        assert not worker.background
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
