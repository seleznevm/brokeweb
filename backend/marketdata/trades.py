"""30-second OHLCV from exchange trade timestamps, with explicit coverage.

Never manufactures empty candles, nor repairs missing trade history with minute
bars. First partial bucket and reconnect buckets are marked incomplete.
"""
from collections import OrderedDict
from dataclasses import dataclass
from .models import Bar


@dataclass
class _Bucket:
    start: int
    end: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float
    first: int
    last: int
    complete: bool


class TradeAggregator:
    CVD_VERSION = 'bybit-taker-base-volume-v1'

    def __init__(self, symbol: str, interval_ms=30000, allowed_lateness_ms=1000, dedupe_capacity=100000):
        if interval_ms != 30000:
            raise ValueError('This aggregator implements the Pine 30S micro feed')
        self.symbol = symbol
        self.interval_ms = interval_ms
        self.allowed_lateness_ms = allowed_lateness_ms
        self.dedupe_capacity = dedupe_capacity
        self._ids = OrderedDict()
        self._buckets: dict[int, _Bucket] = {}
        self._coverage_start: int | None = None
        self._committed_end = -1
        self.late_trades = 0
        self.duplicates = 0
        self.cvd = 0.0
        self.gaps: list[tuple[int, int | None]] = []
        self.watermark = 0

    def disconnected(self, exchange_time: int | None = None):
        self.gaps.append((exchange_time or self.watermark, None))
        self._coverage_start = None
        for bucket in self._buckets.values():
            bucket.complete = False

    def add(self, trade: dict) -> bool:
        timestamp, price, size = int(trade['T']), float(trade['p']), float(trade['v'])
        trade_id = str(trade['i'])
        if trade.get('s', self.symbol) != self.symbol:
            raise ValueError('Trade belongs to another symbol')
        if price <= 0 or size < 0:
            raise ValueError('Invalid trade price or size')
        if trade_id in self._ids:
            self.duplicates += 1
            return False
        self._ids[trade_id] = None
        if len(self._ids) > self.dedupe_capacity:
            self._ids.popitem(last=False)
        start = timestamp // self.interval_ms * self.interval_ms
        if start < self._committed_end:
            self.late_trades += 1
            self.gaps.append((start, start + self.interval_ms))
            return False
        self.watermark = max(self.watermark, timestamp)
        if self._coverage_start is None:
            # A connection can begin midway through a bucket. Only the next
            # bucket can be considered observed from its beginning.
            self._coverage_start = start + self.interval_ms
            if self.gaps and self.gaps[-1][1] is None:
                self.gaps[-1] = (self.gaps[-1][0], self._coverage_start)
        self.cvd += size if trade['S'] == 'Buy' else -size
        bucket = self._buckets.get(start)
        if bucket is None:
            self._buckets[start] = _Bucket(start, start + self.interval_ms, price, price, price, price, size, price * size, timestamp, timestamp, start >= self._coverage_start)
        else:
            bucket.high, bucket.low = max(bucket.high, price), min(bucket.low, price)
            bucket.volume += size
            bucket.turnover += price * size
            if timestamp < bucket.first:
                bucket.first, bucket.open = timestamp, price
            if timestamp >= bucket.last:
                bucket.last, bucket.close = timestamp, price
        return True

    def _bar(self, bucket: _Bucket, confirmed: bool, received_at=None) -> Bar:
        return Bar('BYBIT', self.symbol, '30S', bucket.start, bucket.end, bucket.open, bucket.high, bucket.low, bucket.close, bucket.volume, confirmed, received_at, bucket.turnover)

    def flush(self, exchange_time: int, received_at=None) -> list[tuple[Bar, bool]]:
        """Return closed bar + completeness flag, delayed by lateness allowance."""
        self.watermark = max(self.watermark, exchange_time)
        cutoff = exchange_time - self.allowed_lateness_ms
        result = []
        for start in sorted(self._buckets):
            bucket = self._buckets[start]
            if bucket.end <= cutoff:
                result.append((self._bar(bucket, True, received_at), bucket.complete))
                self._committed_end = max(self._committed_end, bucket.end)
                del self._buckets[start]
        return result

    def current(self, received_at=None) -> list[tuple[Bar, bool]]:
        return [(self._bar(b, False, received_at), b.complete) for _, b in sorted(self._buckets.items())]

    def export_state(self) -> dict:
        from dataclasses import asdict
        return {'symbol': self.symbol, 'buckets': [asdict(b) for b in self._buckets.values()], 'ids': list(self._ids), 'coverage_start': self._coverage_start, 'committed_end': self._committed_end, 'cvd': self.cvd, 'gaps': self.gaps, 'watermark': self.watermark, 'late_trades': self.late_trades, 'duplicates': self.duplicates}

    def restore_state(self, payload: dict):
        if payload['symbol'] != self.symbol:
            raise ValueError('Aggregator checkpoint symbol mismatch')
        self._buckets = {item['start']: _Bucket(**item) for item in payload['buckets']}
        self._ids = OrderedDict.fromkeys(payload['ids'][-self.dedupe_capacity:])
        self._coverage_start = payload['coverage_start']
        self._committed_end = payload['committed_end']
        self.cvd = payload['cvd']
        self.gaps = [tuple(item) for item in payload['gaps']]
        self.watermark = payload['watermark']
        self.late_trades = payload['late_trades']
        self.duplicates = payload['duplicates']
        # Process downtime cannot imply continued trade coverage.
        self.disconnected()
