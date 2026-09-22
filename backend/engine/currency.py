"""Explicit, time-bounded external currency observations; never inferred from scores."""
from bisect import bisect_right
from copy import deepcopy
import math
from .values import NA


class CurrencyRates:
    def __init__(self, records=None):
        self.records = deepcopy(records or {})
        self.starts = {}
        for pair, rows in self.records.items():
            if len(pair.split('|')) != 2 or not all(pair.split('|')):
                raise ValueError('Currency key must be FROM|TO')
            previous_end = -1
            for row in rows:
                start, end, rate = row['available_at'], row['expires_at'], row['rate']
                if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or start < 0 or start < previous_end or end <= start:
                    raise ValueError('Currency observations must be ordered, nonoverlapping UTC intervals')
                if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0):
                    raise ValueError('Currency rate must be positive, finite or null')
                previous_end = end
            self.starts[pair] = [r['available_at'] for r in rows]

    def at(self, source, target, timestamp):
        if source == target: return 1.0
        pair = source + '|' + target
        i = bisect_right(self.starts.get(pair, []), timestamp) - 1
        if i < 0: return NA
        row = self.records[pair][i]
        return NA if timestamp >= row['expires_at'] or row['rate'] is None else row['rate']
