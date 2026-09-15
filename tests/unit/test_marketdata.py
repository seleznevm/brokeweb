import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import httpx
import pytest
from backend.marketdata import Bar, BybitAdapter, BinanceBtcContextAdapter, ExchangeError
from backend.marketdata.alignment import BarBuffer, security_bar, lower_tf_bars, missing_intervals, quality, reconcile_micro
from backend.marketdata.timeframes import bounds, bybit_interval
from backend.marketdata.trades import TradeAggregator
from backend.marketdata.websocket import subscription_shards, parse_kline, BybitWebSocketManager


def run(coro):
    return asyncio.run(coro)


def instrument(symbol, **overrides):
    return {'symbol':symbol, 'status':'Trading', 'quoteCoin':'USDT', 'baseCoin':'X', 'contractType':'LinearPerpetual', 'priceFilter':{'tickSize':'.001'}, 'launchTime':'123', **overrides}


def test_instruments_paginated_and_filtered_no_hardcoded_universe():
    seen=[]
    def handler(request):
        seen.append(dict(request.url.params))
        if 'cursor' not in request.url.params:
            items=[instrument('NEWUSDT'), instrument('OLDUSDT', status='Settled'), instrument('AUSDC',quoteCoin='USDC')]
            cursor='page-2'
        else:
            assert request.url.params['cursor']=='page-2'
            items=[instrument('ZUSDT'), instrument('PREUSDT',isPreListing=True), instrument('FUTUSDT',contractType='LinearFutures')]
            cursor=''
        return httpx.Response(200,json={'retCode':0,'result':{'list':items,'nextPageCursor':cursor}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            values=await BybitAdapter(client).instruments()
            assert [x.symbol for x in values]==['NEWUSDT','ZUSDT']
            assert values[0].tick_size==.001
    run(scenario())
    assert len(seen)==2
    assert seen[0]['category']=='linear'


def test_repeated_universe_cursor_is_visible_error():
    def handler(request):
        return httpx.Response(200,json={'retCode':0,'result':{'list':[],'nextPageCursor':'repeat'}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ExchangeError,match='cursor repeated'):
                await BybitAdapter(client).instruments()
    run(scenario())


def test_backward_backfill_deduplicates_sorts_and_uses_exchange_confirmation():
    calls=[]
    def handler(request):
        end=int(request.url.params['end'])
        calls.append(end)
        stamps=[120000,60000] if end>=120000 else [0]
        rows=[[str(ts),'10','12','8','11','5','55'] for ts in stamps]
        return httpx.Response(200,json={'retCode':0,'time':150000,'result':{'list':rows}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            bars=await BybitAdapter(client).backfill('NEWUSDT','1',3,end=150000)
            assert [b.start for b in bars]==[0,60000,120000]
            assert [b.confirmed for b in bars]==[True,True,False]
            assert bars[-1].end==180000 and bars[-1].turnover==55
    run(scenario())
    assert calls==[150000,59999]


def test_bybit_retcode_does_not_return_empty_success():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'retCode':10001,'retMsg':'bad symbol'}))) as client:
            with pytest.raises(ExchangeError,match='10001'):
                await BybitAdapter(client).klines('INVALID','1')
    run(scenario())


def test_binance_uses_futures_source_and_exclusive_end():
    def handler(request):
        assert request.url.host=='fapi.binance.com'
        if request.url.path.endswith('/time'):
            return httpx.Response(200,json={'serverTime':60000})
        assert request.url.path=='/fapi/v1/klines'
        assert request.url.params['interval']=='1m'
        return httpx.Response(200,json=[[0,'1','3','1','2','8',59999,'16']])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            bars=await BinanceBtcContextAdapter(client).klines(timeframe='1')
            assert bars[0].exchange=='BINANCE' and bars[0].confirmed
            assert bars[0].end==60000
    run(scenario())


def bar(start,tf='1',confirmed=True,**kwargs):
    end=bounds(start,tf)[1]
    return Bar('BYBIT','XUSDT',tf,start,end,10,12,9,11,5,confirmed=confirmed,**kwargs)


def test_utc_alignment_week_and_leap_month():
    stamp=int(datetime(2024,2,20,tzinfo=timezone.utc).timestamp()*1000)
    first,last=bounds(stamp,'M')
    assert datetime.fromtimestamp(first/1000,timezone.utc).isoformat()=='2024-02-01T00:00:00+00:00'
    assert last-first==29*86400000
    assert datetime.fromtimestamp(bounds(stamp,'W')[0]/1000,timezone.utc).weekday()==0
    assert bounds(59999,'30S')==(30000,60000)
    with pytest.raises(ValueError,match='public trades'):
        bybit_interval('30S')


def trade(ts,price,size=1,id=None,side='Buy'):
    return {'T':ts,'p':str(price),'v':str(size),'i':id or str(ts),'S':side,'s':'XUSDT'}


def test_trade_aggregation_exchange_timestamps_dedup_out_of_order_and_partial_start():
    agg=TradeAggregator('XUSDT',allowed_lateness_ms=0)
    agg.add(trade(10000,10,2))
    agg.add(trade(20000,12,3))
    agg.add(trade(15000,8,4))
    assert not agg.add(trade(20000,12,3))
    agg.add(trade(30000,13,1))
    closed,complete=agg.flush(30000)[0]
    assert (closed.start,closed.end,closed.open,closed.high,closed.low,closed.close,closed.volume)==(0,30000,10,12,8,12,9)
    assert not complete and agg.duplicates==1
    assert agg.current()[0][1] is True
    assert agg.cvd==10
    assert not agg.add(trade(25000,14,id='late'))
    assert agg.late_trades==1


def test_disconnect_and_restart_never_claim_missing_30s_coverage():
    agg=TradeAggregator('XUSDT',allowed_lateness_ms=0)
    agg.add(trade(100,10))
    agg.add(trade(30100,11))
    agg.flush(30000)
    saved=agg.export_state()
    restored=TradeAggregator('XUSDT',allowed_lateness_ms=0)
    restored.restore_state(saved)
    assert restored.current()[0][1] is False
    assert not restored.add(trade(30100,11))
    restored.add(trade(40000,12))
    assert not restored.flush(60000)[0][1]
    restored.add(trade(60100,13))
    assert restored.current()[0][1] is True
    # No fabricated intermediate bars in a quiet feed.
    assert restored.flush(180000)[0][0].start==60000
    assert restored.flush(240000)==[]


def test_security_no_lookahead_and_lower_tf_array():
    htf=[bar(0,'5'),bar(300000,'5',False)]
    assert security_bar(htf,bar(180000)) is None
    assert security_bar(htf,bar(240000))==htf[0]
    assert security_bar(htf,bar(300000))==htf[0]
    assert security_bar(htf,bar(300000),True)==htf[1]
    lower=[bar(0,'30S'),bar(30000,'30S'),bar(60000,'30S')]
    assert len(lower_tf_bars(lower,bar(0)))==2
    assert security_bar(lower,bar(0))==lower[1]


def test_bar_reconciliation_preserves_confirmed_and_detects_gap():
    buffer=BarBuffer()
    assert buffer.upsert(bar(0,received_at=10))
    assert not buffer.upsert(bar(0,confirmed=False,received_at=20))
    changed=replace(bar(0,received_at=30),close=12)
    assert buffer.reconcile([changed])==[0]
    assert buffer.revisions==1
    buffer.upsert(bar(120000))
    assert missing_intervals(buffer.chronological())==[(60000,120000)]
    assert quality('1',kline_fresh=True,trades_complete=False)['quality']=='DEGRADED'
    assert quality('15',kline_fresh=True,trades_complete=False)['quality']=='KLINE_REALTIME'


def test_micro_native_reconciliation_reports_mismatch_without_fabricating():
    pieces=[bar(0,'30S'),bar(30000,'30S')]
    native=replace(bar(0),volume=10)
    assert reconcile_micro(native,pieces,.01)['status']=='MATCH'
    assert reconcile_micro(replace(native,close=11.01),pieces,.01)['status']=='MISMATCH'
    assert reconcile_micro(native,pieces[:1],.01)['status']=='INCOMPLETE'


def test_sharding_and_kline_end_normalization():
    shards=subscription_shards(['XUSDT','YUSDT'],['1','5'],{'XUSDT'},2)
    assert list(map(len,shards))==[2,2,1]
    assert len(set(sum((list(s) for s in shards),[])))==5
    message={'topic':'kline.1.XUSDT','data':[{'start':0,'end':59999,'open':'10','high':'12','low':'9','close':'11','volume':'5','turnover':'55','confirm':True}]}
    assert parse_kline(message)[0].end==60000


def test_websocket_dispatch_and_dynamic_unsubscribe_with_fake_transport():
    events=[]
    sent=[]
    class Socket:
        async def send(self,payload):
            sent.append(json.loads(payload))
        async def recv(self):
            if not any(e['type']=='trade' for e in events):
                return json.dumps({'topic':'publicTrade.XUSDT','ts':123,'data':[trade(123,10)]})
            await asyncio.Event().wait()
    class Connection:
        async def __aenter__(self): return Socket()
        async def __aexit__(self,*args): return False
    async def callback(event): events.append(event)
    async def scenario():
        manager=BybitWebSocketManager(callback,connect=lambda *a,**k:Connection())
        await manager.update_subscriptions(['XUSDT'],['1'],{'XUSDT'})
        for _ in range(20):
            await asyncio.sleep(0)
            if any(e['type']=='trade' for e in events): break
        await manager.update_subscriptions([],[])
        await manager.close()
        assert manager.tasks=={}
    run(scenario())
    assert any(e['type']=='trade' and e['symbol']=='XUSDT' for e in events)
    assert sent[0]['op']=='subscribe'
    assert events[-1]['type']=='health' and not events[-1]['connected']


def test_websocket_incremental_topics_preserve_live_shards_and_trade_continuity():
    sockets=[]
    events=[]
    class Socket:
        def __init__(self):self.sent=[]
        async def send(self,payload):self.sent.append(json.loads(payload))
        async def recv(self):await asyncio.Event().wait()
    class Connection:
        async def __aenter__(self):
            socket=Socket();sockets.append(socket);return socket
        async def __aexit__(self,*args):return False
    async def callback(event):events.append(event)
    async def settle():
        for _ in range(8):await asyncio.sleep(0)
    async def scenario():
        manager=BybitWebSocketManager(callback,topics_per_shard=3,connect=lambda *a,**k:Connection())
        await manager.update_subscriptions(['XUSDT'],['1'],{'XUSDT'})
        await settle()
        assert len(sockets)==1
        initial=set(manager.topics[0])
        await manager.update_subscriptions(['XUSDT','YUSDT'],['1'],{'XUSDT','YUSDT'})
        await settle()
        assert len(sockets)==2
        assert initial <= manager.topics[0]
        assert all(event['connected'] for event in events)
        # Drop only Y's trade feed, retaining its candle stream and X's trades.
        await manager.update_subscriptions(['XUSDT','YUSDT'],['1'],{'XUSDT'})
        await settle()
        assert len(sockets)==2  # No newly opened connection.
        unsubscribed=[topic for socket in sockets for message in socket.sent if message['op']=='unsubscribe' for topic in message['args']]
        assert unsubscribed==['publicTrade.YUSDT']
        assert 'publicTrade.XUSDT' in manager.topics[0]
        await manager.close()
    run(scenario())


def test_websocket_simultaneous_updates_leave_exact_final_topic_assignment():
    class Socket:
        async def send(self,payload):pass
        async def recv(self):await asyncio.Event().wait()
    class Connection:
        async def __aenter__(self):return Socket()
        async def __aexit__(self,*args):return False
    async def callback(event):pass
    async def scenario():
        manager=BybitWebSocketManager(callback,connect=lambda *a,**k:Connection())
        await manager.update_subscriptions(['XUSDT'],['1'])
        for _ in range(5):await asyncio.sleep(0)
        await asyncio.gather(manager.update_subscriptions([],[]),manager.update_subscriptions(['YUSDT'],['5']))
        assert set().union(*manager.topics.values())=={'kline.5.YUSDT'}
        assert len(manager.tasks)==1
        await manager.close()
    run(scenario())
