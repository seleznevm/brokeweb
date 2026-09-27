"""Indexed snapshot of an ordered native candle stream.

The worker replaces candle dictionaries instead of mutating them. A tuple keeps
an in-flight execution's view stable while a newer feed update is received.
Only explicitly indexed streams use the fast path; ordinary sequences retain
the existing interpreter traversal semantics.
"""
from bisect import bisect_right
from collections.abc import Sequence


class ContextBars(Sequence):
    def __init__(self, bars):
        self._bars = tuple(b.to_dict() if hasattr(b, 'to_dict') else b for b in bars)
        self._starts = tuple(b['start'] for b in self._bars)
        if any(a >= b for a, b in zip(self._starts, self._starts[1:])):
            raise ValueError('Context candles must have strictly increasing starts')
        if any(b['end'] <= b['start'] for b in self._bars):
            raise ValueError('Invalid context candle interval')

    def __len__(self):
        return len(self._bars)

    def __getitem__(self, key):
        return self._bars[key]

    def after_until(self, committed_start, asof):
        left = 0 if committed_start is None else bisect_right(self._starts, committed_start)
        right = bisect_right(self._starts, asof)
        return iter(self._bars[left:max(left, right)])
