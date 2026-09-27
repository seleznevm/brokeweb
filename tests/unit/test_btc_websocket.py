import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from backend.marketdata.binance_websocket import BinanceBtcWebSocket,parse_btc_kline
from backend.marketdata.models import Bar
from backend.worker import Worker


def message(ts=1000,closed=False):
    return {'stream':'btcusdt@kline_1m','data':{'e':'kline','s':'BTCUSDT','E':ts,
            'k':{'t':0,'T':59999,'i':'1m','o':'10','h':'12','l':'9','c':'11',
                 'v':'5','q':'55','x':closed}}}


def shell():
    worker=Worker.__new__(Worker)
    worker.stopping=False;worker.contexts={};worker.context_views={};worker.context_limit=100
    worker.btc_last_events={};worker.context_last=0;worker.btc_recovering=True
    worker.btc_ws=SimpleNamespace(health=SimpleNamespace(connected=True))
    worker.btc_refresh_lock=asyncio.Lock();worker.parameters={};worker.timeframes=['1']
    worker.history_span_ms=60000;worker.errors={}
    return worker


def test_native_btc_kline_identity_confirmation_volume_and_end():
    event=parse_btc_kline(message(closed=True),{'1m':'1'},1001)
    bar=event['bar']
    assert (bar['exchange'],bar['symbol'],bar['timeframe'])==('BINANCE','BTCUSDT','1')
    assert (bar['end'],bar['confirmed'],bar['volume'],bar['turnover'])==(60000,True,5,55)
    wrong=message();wrong['data']['e']='markPriceUpdate'
    with pytest.raises(ValueError):parse_btc_kline(wrong,{'1m':'1'},1001)


def test_btc_socket_dispatch_and_intentional_close():
    events=[];urls=[]
    class Socket:
        async def recv(self):
            if not any(e['type']=='kline' for e in events):return json.dumps(message())
            await asyncio.Event().wait()
    class Connection:
        async def __aenter__(self):return Socket()
        async def __aexit__(self,*args):return False
    def connect(url,**kwargs):urls.append(url);return Connection()
    async def callback(event):events.append(event)
    async def scenario():
        ws=BinanceBtcWebSocket(callback,connect=connect);ws.start(['1'])
        for _ in range(20):
            await asyncio.sleep(0)
            if any(e['type']=='kline' for e in events):break
        await ws.close()
        assert not ws.health.connected
        assert ws.health.messages==1 and ws.health.reconnects==0
    asyncio.run(scenario())
    assert urls==['wss://fstream.binance.com/market/stream?streams=btcusdt@kline_1m']
    assert [e['type'] for e in events]==['health','kline','health']


def test_worker_rejects_out_of_order_btc_and_confirmed_to_open_downgrade():
    async def scenario():
        worker=shell()
        await worker.on_btc(parse_btc_kline(message(2000,True),{'1m':'1'},2001))
        await worker.on_btc(parse_btc_kline(message(1000),{'1m':'1'},2002))
        await worker.on_btc(parse_btc_kline(message(3000),{'1m':'1'},3001))
        rows=worker.contexts['BINANCE:BTCUSDT.P|1']
        assert len(rows)==1 and rows[0]['confirmed']
        assert rows[0]['exchange_time']==2000
    asyncio.run(scenario())


def test_slow_rest_cannot_erase_newer_websocket_sample(monkeypatch):
    import backend.worker as module
    monkeypatch.setattr(module,'context_requirements',lambda *args:(set(),{'1'}))
    monkeypatch.setattr(module,'now_ms',lambda:1000)
    async def scenario():
        worker=shell()
        async def rest(*args):
            newer=parse_btc_kline(message(1500),{'1m':'1'},1501)
            newer['bar']['close']=12
            await worker.on_btc(newer)
            return [Bar('BINANCE','BTCUSDT','1',0,60000,10,12,9,10,5,False,1600,55)]
        worker.btc=SimpleNamespace(backfill=rest)
        await worker.refresh_btc()
        assert worker.contexts['BINANCE:BTCUSDT.P|1'][-1]['close']==12
        assert not worker.btc_recovering
    asyncio.run(scenario())


def test_btc_recovery_fetches_full_gap_and_does_not_clear_health_on_failure(monkeypatch):
    import backend.worker as module
    monkeypatch.setattr(module,'context_requirements',lambda *args:(set(),{'1'}))
    monkeypatch.setattr(module,'now_ms',lambda:600000)
    async def scenario():
        worker=shell()
        worker.contexts['BINANCE:BTCUSDT.P|1']=[{'start':0,'end':60000,'confirmed':True}]
        worker.btc=SimpleNamespace(backfill=AsyncMock(side_effect=RuntimeError('offline')))
        with pytest.raises(RuntimeError,match='offline'):await worker.refresh_btc()
        worker.btc.backfill.assert_awaited_once_with('BTCUSDT','1',12)
        assert worker.btc_recovering
        assert not worker.btc_stream_ready()
    asyncio.run(scenario())


def test_one_fresh_btc_timeframe_does_not_mask_other_stale_timeframe(monkeypatch):
    import backend.worker as module
    monkeypatch.setattr(module,'context_requirements',lambda *args:(set(),{'1','15'}))
    monkeypatch.setattr(module,'now_ms',lambda:100000)
    worker=shell();worker.btc_recovering=False
    worker.btc_last_events={'1':100000,'15':1000}
    assert not worker.btc_stream_ready()
    worker.btc_last_events['15']=100000
    assert worker.btc_stream_ready()
