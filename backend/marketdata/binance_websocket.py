"""Binance USD-M BTC context; native klines, never mark/index/spot prices."""
from __future__ import annotations
import asyncio
from dataclasses import asdict
import json
import logging
import random
import time
import websockets
from .models import Bar
from .timeframes import binance_interval
from .websocket import ShardHealth

log=logging.getLogger(__name__)


def parse_btc_kline(message, intervals, received_at):
    payload=message.get('data',message)
    if payload.get('e')!='kline' or payload.get('s')!='BTCUSDT':
        raise ValueError('Expected native Binance BTCUSDT futures kline')
    k=payload['k']
    tf=intervals[k['i']]
    bar=Bar('BINANCE','BTCUSDT',tf,int(k['t']),int(k['T'])+1,
            float(k['o']),float(k['h']),float(k['l']),float(k['c']),
            float(k['v']),bool(k['x']),received_at,float(k['q']))
    return {'type':'kline','bar':bar.to_dict(),'exchange_time':int(payload['E']),
            'received_at':received_at}


class BinanceBtcWebSocket:
    def __init__(self,callback,connect=None,stale_seconds=45):
        self.callback=callback;self.connect=connect or websockets.connect
        self.stale_seconds=stale_seconds;self.health=ShardHealth()
        self.task=None;self.intervals={};self.closed=False

    def start(self,timeframes):
        if self.task is not None:raise RuntimeError('BTC stream already started')
        self.intervals={binance_interval(tf):tf for tf in timeframes}
        if not self.intervals:raise ValueError('BTC context requires a timeframe')
        self.task=asyncio.create_task(self.run())

    async def close(self):
        self.closed=True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
            self.task=None

    async def notify_health(self):
        await self.callback({'type':'health',**asdict(self.health)})

    async def run(self):
        streams='/'.join(f'btcusdt@kline_{interval}' for interval in sorted(self.intervals))
        # Routed market endpoint is required by Binance's public/market split.
        url=f'wss://fstream.binance.com/market/stream?streams={streams}'
        failures=0
        while not self.closed:
            try:
                async with self.connect(url,ping_interval=20,close_timeout=5,max_size=1024*1024) as socket:
                    self.health.connected=True;self.health.error=None
                    await self.notify_health()
                    while True:
                        raw=await asyncio.wait_for(socket.recv(),timeout=self.stale_seconds)
                        received=int(time.time()*1000)
                        event=parse_btc_kline(json.loads(raw),self.intervals,received)
                        self.health.last_received=received;self.health.last_market_event=event['exchange_time']
                        self.health.messages+=1;failures=0
                        await self.callback(event)
            except asyncio.CancelledError:
                self.health.connected=False
                await self.notify_health()
                raise
            except Exception as exc:
                self.health.connected=False;self.health.error=str(exc);self.health.reconnects+=1
                failures+=1
                log.warning('binance_context_reconnect',extra={'error':str(exc),'reconnects':self.health.reconnects})
                await self.notify_health()
            await asyncio.sleep(min(2**min(failures,6),60)+random.uniform(0,1))
