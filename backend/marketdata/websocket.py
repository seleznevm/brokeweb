"""Independent public WebSocket shards, dynamic subscriptions, health events."""
from __future__ import annotations
import asyncio
from contextlib import suppress
from dataclasses import asdict, dataclass
import json
import logging
import random
import time
from typing import Awaitable, Callable
import websockets
from .models import Bar
from .timeframes import bybit_interval

log = logging.getLogger(__name__)
Callback = Callable[[dict], Awaitable[None]]


@dataclass
class ShardHealth:
    connected: bool = False
    last_received: int | None = None
    last_market_event: int | None = None
    messages: int = 0
    reconnects: int = 0
    error: str | None = None


def subscription_shards(symbols: list[str], timeframes: list[str], full_realtime: set[str] | None = None, topics_per_shard=100) -> list[tuple[str, ...]]:
    if topics_per_shard < 1:
        raise ValueError('topics_per_shard must be positive')
    topics = set()
    for symbol in symbols:
        topics.update(f'kline.{bybit_interval(tf)}.{symbol}' for tf in timeframes if tf != '30S')
        if symbol in (full_realtime or set()):
            topics.add(f'publicTrade.{symbol}')
    ordered = sorted(topics)
    return [tuple(ordered[i:i + topics_per_shard]) for i in range(0, len(ordered), topics_per_shard)]


def parse_kline(message: dict, received_at: int | None = None) -> list[Bar]:
    _, interval, symbol = message['topic'].split('.', 2)
    return [Bar('BYBIT', symbol, interval, int(item['start']), int(item['end']) + 1, float(item['open']), float(item['high']), float(item['low']), float(item['close']), float(item['volume']), bool(item['confirm']), received_at, float(item['turnover'])) for item in message['data']]


class BybitWebSocketManager:
    """callback events: {type: kline|trade|health, ...}.

    kline: bar dict; trade: trades list + exchange_time; health: shard topics and
    connected state. The consumer backfills native klines on reconnect and
    invalidates 30S trade coverage (trade gaps cannot be REST repaired).
    """
    def __init__(self, callback: Callback, url='wss://stream.bybit.com/v5/public/linear', topics_per_shard=100, stale_seconds=45, connect=None, max_event_lag_seconds=90):
        self.callback = callback
        self.url = url
        self.topics_per_shard = topics_per_shard
        self.stale_seconds = stale_seconds
        self.max_event_lag_seconds = max_event_lag_seconds
        self.connect = connect or websockets.connect
        self.tasks: dict[int, asyncio.Task] = {}
        self.health: dict[int, ShardHealth] = {}
        self.topics: dict[int, set[str]] = {}
        self.sockets: dict[int, object] = {}
        self._subscription_lock = asyncio.Lock()
        self._update_lock = asyncio.Lock()
        self._next_shard = 0
        self._closed = False

    async def _change_topics(self, socket, operation, topics):
        ordered = sorted(topics)
        for offset in range(0, len(ordered), 10):
            await socket.send(json.dumps({'op': operation, 'args': ordered[offset:offset + 10]}))

    async def update_subscriptions(self, symbols: list[str], timeframes: list[str], full_realtime: set[str] | None = None):
        async with self._update_lock:
            await self._update_subscriptions(symbols, timeframes, full_realtime)

    async def _update_subscriptions(self, symbols, timeframes, full_realtime):
        desired = set().union(*subscription_shards(symbols, timeframes, full_realtime, self.topics_per_shard))
        obsolete_tasks = []
        async with self._subscription_lock:
            old_topics = {key: set(value) for key, value in self.topics.items()}
            # Preserve all existing topic assignments: adding a symbol cannot
            # move old symbols across connections or interrupt their coverage.
            for key in self.topics:
                self.topics[key].intersection_update(desired)
            assigned = set().union(*self.topics.values()) if self.topics else set()
            for topic in sorted(desired - assigned):
                key = next((k for k in sorted(self.topics) if len(self.topics[k]) < self.topics_per_shard), None)
                if key is None:
                    key = self._next_shard
                    self._next_shard += 1
                    self.topics[key] = set()
                    self.health[key] = ShardHealth()
                self.topics[key].add(topic)
            for key in list(self.topics):
                before, after = old_topics.get(key, set()), self.topics[key]
                socket = self.sockets.get(key)
                if socket is not None:
                    try:
                        await self._change_topics(socket, 'unsubscribe', before - after)
                        await self._change_topics(socket, 'subscribe', after - before)
                    except Exception as exc:
                        # A broken socket is reconnected by its own receiver.
                        # The desired assignment remains for resubscription.
                        log.warning('bybit_subscription_send_failed', extra={'error': str(exc), 'shard': key})
                        await socket.close()
                if not after:
                    task = self.tasks.pop(key, None)
                    if task:
                        task.cancel()
                        obsolete_tasks.append((key, task))
                    else:
                        self.topics.pop(key, None)
                        self.health.pop(key, None)
                elif key not in self.tasks or self.tasks[key].done():
                    previous = self.tasks.get(key)
                    if previous and not previous.cancelled():
                        error = previous.exception()
                        if error: log.error("bybit_receiver_task_failed", extra={"error": str(error), "shard": key})
                    self.tasks[key] = asyncio.create_task(self._run(key), name=f'bybit-ws-{key}')
        # Do not wait for cancelled receivers while holding their lock.
        for key, task in obsolete_tasks:
            with suppress(asyncio.CancelledError):
                await task
            self.topics.pop(key, None)
            self.health.pop(key, None)
            self.sockets.pop(key, None)

    async def close(self):
        self._closed = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()

    async def _heartbeat(self, ws):
        while True:
            await asyncio.sleep(20)
            await ws.send(json.dumps({'op': 'ping'}))

    async def _health_event(self, topics, state):
        await self.callback({'type': 'health', 'topics': list(topics), **asdict(state)})

    async def _run(self, shard):
        state = self.health[shard]
        failures = 0
        while not self._closed:
            heartbeat = None
            try:
                async with self.connect(self.url, ping_interval=None, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
                    async with self._subscription_lock:
                        await self._change_topics(ws, 'subscribe', self.topics.get(shard, set()))
                        self.sockets[shard] = ws
                    state.connected, state.error = True, None
                    await self._health_event(self.topics.get(shard, set()), state)
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=self.stale_seconds)
                        received_at = int(time.time() * 1000)
                        state.last_received = received_at
                        message = json.loads(raw)
                        if message.get('success') is False:
                            raise RuntimeError(f"Bybit subscription rejected: {message.get('ret_msg')}")
                        if 'topic' not in message:
                            continue
                        state.messages += 1
                        state.last_market_event = int(message.get('ts', received_at))
                        # A busy consumer can keep recv() returning buffered data
                        # forever without triggering the socket timeout. Reconnect
                        # through the normal recovery path instead of draining an
                        # obsolete stream while every trading decision stays STALE.
                        lag = received_at - state.last_market_event
                        if lag > self.max_event_lag_seconds * 1000:
                            raise RuntimeError(f'Bybit buffered market data is stale ({lag} ms); REST recovery required')
                        failures = 0
                        if message['topic'].startswith('kline.'):
                            for bar in parse_kline(message, received_at):
                                await self.callback({'type': 'kline', 'bar': bar.to_dict(), 'exchange_time': message.get('ts'), 'received_at': received_at})
                        elif message['topic'].startswith('publicTrade.'):
                            await self.callback({'type': 'trade', 'symbol': message['topic'].split('.', 1)[1], 'trades': message['data'], 'exchange_time': message.get('ts', received_at), 'received_at': received_at})
            except asyncio.CancelledError:
                state.connected = False
                await self._health_event(self.topics.get(shard, set()), state)
                raise
            except Exception as exc:
                state.connected, state.error = False, str(exc)
                state.reconnects += 1
                failures += 1
                log.warning('bybit_websocket_reconnect', extra={'error': str(exc), 'topics': len(self.topics.get(shard, set())), 'reconnects': state.reconnects})
                try:
                    await self._health_event(self.topics.get(shard, set()), state)
                except Exception as health_exc:
                    # Persistence failures must not strand a disconnected shard.
                    # Its health stays disconnected; the next connection must
                    # successfully notify the consumer before reading market data.
                    log.exception('bybit_disconnect_notification_failed', extra={'error': str(health_exc)})
            finally:
                self.sockets.pop(shard, None)
                if heartbeat:
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat
            await asyncio.sleep(min(2 ** min(failures, 6), 60) + random.uniform(0, 1))
