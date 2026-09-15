"""Exchange-native public REST clients with bounded retry and pagination."""
from __future__ import annotations
import asyncio
import random
import time
from typing import Protocol
import httpx
from .models import Bar, Instrument
from .timeframes import bounds, bybit_interval, binance_interval


class ExchangeError(RuntimeError):
    pass


class ExchangeAdapter(Protocol):
    async def instruments(self) -> list[Instrument]: ...
    async def klines(self, symbol: str, timeframe: str, limit=1000, end=None, start=None) -> list[Bar]: ...
    async def backfill(self, symbol: str, timeframe: str, count: int, end=None) -> list[Bar]: ...
    async def close(self) -> None: ...


class RestAdapter:
    def __init__(self, client: httpx.AsyncClient | None, base_url: str, retries=4):
        self.client = client or httpx.AsyncClient(base_url=base_url, timeout=20)
        self._owns_client = client is None
        self.base_url = base_url.rstrip('/')
        self.retries = retries
        self._limit = asyncio.Semaphore(5)

    async def close(self):
        if self._owns_client:
            await self.client.aclose()

    async def _get(self, path, params):
        for attempt in range(self.retries + 1):
            try:
                async with self._limit:
                    response = await self.client.get(self.base_url + path, params={k: v for k, v in params.items() if v is not None})
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt == self.retries:
                        response.raise_for_status()
                    delay = min(float(response.headers.get('retry-after', 2 ** attempt)), 30)
                    await asyncio.sleep(delay + random.uniform(0, .2))
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get('retCode', 0) != 0:
                    if payload['retCode'] in (10006, 10016) and attempt < self.retries:
                        await asyncio.sleep(min(2 ** attempt, 30))
                        continue
                    raise ExchangeError(f"{path}: {payload.get('retCode')} {payload.get('retMsg')}")
                return payload
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                if attempt == self.retries:
                    raise
                await asyncio.sleep(min(2 ** attempt, 30))
        raise ExchangeError(f'{path}: exhausted retries')

    async def backfill(self, symbol: str, timeframe: str, count: int, end=None) -> list[Bar]:
        if count < 1:
            return []
        # Binance permits 1500; use <=1000 consistently to control request weight.
        cursor = end if end is not None else int(time.time() * 1000)
        result = {}
        while len(result) < count:
            page = await self.klines(symbol, timeframe, limit=min(1000, count - len(result)), end=cursor)
            if not page:
                break
            for bar in page:
                if bar.start <= cursor:
                    result[bar.start] = bar
            next_cursor = min(bar.start for bar in page) - 1
            if next_cursor >= cursor:
                raise ExchangeError('Kline pagination failed to advance')
            cursor = next_cursor
        return sorted(result.values(), key=lambda b: b.start)[-count:]


class BybitAdapter(RestAdapter):
    def __init__(self, client=None, base_url='https://api.bybit.com', retries=4):
        super().__init__(client, base_url, retries)

    async def instruments(self) -> list[Instrument]:
        result, cursor, seen = {}, None, set()
        while True:
            payload = await self._get('/v5/market/instruments-info', {'category': 'linear', 'status': 'Trading', 'limit': 1000, 'cursor': cursor})
            for item in payload['result']['list']:
                if item['status'] != 'Trading' or item['quoteCoin'] != 'USDT' or item['contractType'] != 'LinearPerpetual' or item.get('isPreListing', False):
                    continue
                result[item['symbol']] = Instrument('BYBIT', item['symbol'], float(item['priceFilter']['tickSize']), item['baseCoin'], item['quoteCoin'], item['status'], item['contractType'], int(item['launchTime']))
            cursor = payload['result'].get('nextPageCursor')
            if not cursor:
                break
            if cursor in seen:
                raise ExchangeError('Instrument cursor repeated')
            seen.add(cursor)
        return sorted(result.values(), key=lambda item: item.symbol)

    async def klines(self, symbol: str, timeframe: str, limit=1000, end=None, start=None) -> list[Bar]:
        interval = bybit_interval(timeframe)
        payload = await self._get('/v5/market/kline', {'category': 'linear', 'symbol': symbol, 'interval': interval, 'limit': min(max(limit, 1), 1000), 'end': end, 'start': start})
        # Exchange time prevents local clock skew from confirming an open candle.
        now = int(payload.get('time', time.time() * 1000))
        received = int(time.time() * 1000)
        result = {}
        for row in payload['result']['list']:
            timestamp = int(row[0])
            _, stop = bounds(timestamp, timeframe)
            result[timestamp] = Bar('BYBIT', symbol, timeframe, timestamp, stop, *map(float, row[1:6]), confirmed=stop <= now, received_at=received, turnover=float(row[6]))
        return sorted(result.values(), key=lambda b: b.start)

    async def open_interest(self, symbol: str, interval='5min', start=None, end=None, cursor=None):
        """Optional external source. Never injected into the baseline automatically."""
        return (await self._get('/v5/market/open-interest', {'category': 'linear', 'symbol': symbol, 'intervalTime': interval, 'startTime': start, 'endTime': end, 'cursor': cursor, 'limit': 200}))['result']

    async def tickers(self):
        """Native turnover24h is metadata only, never the Pine volume proxy."""
        return (await self._get('/v5/market/tickers', {'category': 'linear'}))['result']['list']


class BinanceBtcContextAdapter(RestAdapter):
    def __init__(self, client=None, base_url='https://fapi.binance.com', retries=4):
        super().__init__(client, base_url, retries)

    async def instruments(self):
        payload = await self._get('/fapi/v1/exchangeInfo', {})
        for item in payload['symbols']:
            if item['symbol'] == 'BTCUSDT' and item['contractType'] == 'PERPETUAL':
                tick = next(f['tickSize'] for f in item['filters'] if f['filterType'] == 'PRICE_FILTER')
                return [Instrument('BINANCE', 'BTCUSDT', float(tick), item['baseAsset'], item['quoteAsset'], item['status'], item['contractType'], item['onboardDate'])]
        raise ExchangeError('Binance BTCUSDT perpetual context unavailable')

    async def klines(self, symbol='BTCUSDT', timeframe='1', limit=1000, end=None, start=None):
        if symbol != 'BTCUSDT':
            raise ValueError('This adapter is exclusively the Pine Binance BTC context')
        payload = await self._get('/fapi/v1/klines', {'symbol': symbol, 'interval': binance_interval(timeframe), 'limit': min(max(limit, 1), 1000), 'endTime': end, 'startTime': start})
        # Use exchange server time for the open/closed distinction.
        now = int((await self._get('/fapi/v1/time', {}))['serverTime'])
        received = int(time.time() * 1000)
        return sorted([Bar('BINANCE', symbol, timeframe, int(row[0]), int(row[6]) + 1, *map(float, row[1:6]), confirmed=int(row[6]) < now, received_at=received, turnover=float(row[7])) for row in payload], key=lambda b: b.start)
