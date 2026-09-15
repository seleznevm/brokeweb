"""Pine-compatible scalar operations on chronological series.

NA is float('nan'). EMA starts at the first non-NA observation; Wilder RMA
starts with the SMA of length non-NA observations. External TradingView
reference verification remains required; analytical fixtures are not parity.
"""
from __future__ import annotations

import builtins
import math
import re
from decimal import Decimal, ROUND_FLOOR
from typing import Sequence

NA = math.nan


def na(value) -> bool:
    return value is None or isinstance(value, float) and math.isnan(value)


def nz(value, replacement=0.0):
    return replacement if na(value) else value


def history(values: Sequence, offset: int = 0):
    if offset < 0:
        raise ValueError('Pine history offset cannot be negative')
    return values[-offset - 1] if len(values) > offset else NA


def _length(length: int) -> int:
    if isinstance(length, bool) or int(length) != length or length < 1:
        raise ValueError('length must be a positive integer')
    return int(length)


def _valid_tail(values: Sequence, length: int) -> list:
    length = _length(length)
    result = []
    for value in reversed(values):
        if not na(value):
            result.append(value)
        if len(result) == length:
            break
    return result


def sma(values: Sequence, length: int) -> float:
    tail = _valid_tail(values, length)
    return builtins.sum(tail) / length if len(tail) == length else NA


def sum(values: Sequence, length: int) -> float:
    """Pine math.sum ignores NA, collecting length non-NA observations."""
    tail = _valid_tail(values, length)
    return builtins.sum(tail) if len(tail) == length else NA


def ema_series(values: Sequence, length: int) -> list[float]:
    alpha, state, result = 2.0 / (_length(length) + 1), NA, []
    for value in values:
        if not na(value):
            state = value if na(state) else alpha * value + (1 - alpha) * state
        result.append(state)
    return result


def ema(values: Sequence, length: int) -> float:
    return history(ema_series(values, length))


def rma_series(values: Sequence, length: int) -> list[float]:
    length = _length(length)
    alpha, seed, state, result = 1.0 / length, [], NA, []
    for value in values:
        if not na(value):
            if na(state):
                seed.append(value)
                if len(seed) == length:
                    state = builtins.sum(seed) / length
            else:
                state = alpha * value + (1 - alpha) * state
        result.append(state)
    return result


def rma(values: Sequence, length: int) -> float:
    return history(rma_series(values, length))


def tr(high: float, low: float, previous_close=NA, handle_na=True) -> float:
    if na(high) or na(low):
        return NA
    if na(previous_close):
        return high - low if handle_na else NA
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr(highs: Sequence, lows: Sequence, closes: Sequence, length=14) -> float:
    if not len(highs) == len(lows) == len(closes):
        raise ValueError('OHLC histories must have equal lengths')
    return rma([tr(h, l, closes[i - 1] if i else NA) for i, (h, l) in enumerate(zip(highs, lows))], length)


def highest(values: Sequence, length: int) -> float:
    tail = [x for x in values[-_length(length):] if not na(x)]
    return max(tail) if tail else NA


def lowest(values: Sequence, length: int) -> float:
    tail = [x for x in values[-_length(length):] if not na(x)]
    return min(tail) if tail else NA


def highestbars(values: Sequence, length: int):
    target = highest(values, length)
    return next((-i for i, x in enumerate(reversed(values[-_length(length):])) if x == target), NA)


def lowestbars(values: Sequence, length: int):
    target = lowest(values, length)
    return next((-i for i, x in enumerate(reversed(values[-_length(length):])) if x == target), NA)


def _pivot(values: Sequence, left: int, right: int, high: bool):
    if left < 0 or right < 0:
        raise ValueError('pivot strengths cannot be negative')
    if len(values) < left + right + 1:
        return NA
    window = list(values[-(left + right + 1):])
    candidate = window[left]
    if any(na(v) for v in window):
        return NA
    # A tie on the left is allowed; a later equal extreme wins on the right.
    valid = (all(candidate >= v for v in window[:left]) and all(candidate > v for v in window[left + 1:])) if high else (all(candidate <= v for v in window[:left]) and all(candidate < v for v in window[left + 1:]))
    return candidate if valid else NA


def pivothigh(values: Sequence, left: int, right: int):
    return _pivot(values, left, right, True)


def pivotlow(values: Sequence, left: int, right: int):
    return _pivot(values, left, right, False)


def crossover(a: Sequence, b: Sequence) -> bool:
    return len(a) >= 2 and len(b) >= 2 and a[-1] > b[-1] and a[-2] <= b[-2]


def crossunder(a: Sequence, b: Sequence) -> bool:
    return len(a) >= 2 and len(b) >= 2 and a[-1] < b[-1] and a[-2] >= b[-2]


def barssince(conditions: Sequence):
    return next((i for i, value in enumerate(reversed(conditions)) if not na(value) and bool(value)), NA)


def valuewhen(conditions: Sequence, values: Sequence, occurrence: int = 0):
    if occurrence < 0 or len(conditions) != len(values):
        raise ValueError('invalid valuewhen arguments')
    for condition, value in zip(reversed(conditions), reversed(values)):
        if not na(condition) and bool(condition):
            if occurrence == 0:
                return value
            occurrence -= 1
    return NA


def change(values: Sequence, length: int = 1):
    a, b = history(values), history(values, length)
    if na(a) or na(b):
        return NA
    return a != b if isinstance(a, bool) else a - b


def hlc3(high, low, close):
    return (high + low + close) / 3.0


def clamp(value, low, high):
    return NA if any(na(x) for x in (value, low, high)) else min(max(value, low), high)


def round(value, precision=0):
    if na(value):
        return NA
    scale = Decimal(10) ** precision
    return float((Decimal(str(value)) * scale + Decimal('0.5')).to_integral_value(rounding=ROUND_FLOOR) / scale)


def round_to_mintick(value, tick_size):
    if na(value):
        return NA
    if tick_size <= 0:
        raise ValueError('tick_size must be positive')
    tick = Decimal(str(tick_size))
    units = (Decimal(str(value)) / tick + Decimal('0.5')).to_integral_value(rounding=ROUND_FLOOR)
    return float(units * tick)


def timeframe_seconds(timeframe: str) -> int:
    """Pine minute/second/day/week strings; month seconds are average duration.

    Calendar alignment must use marketdata.timeframes.bounds for months.
    """
    match = re.fullmatch(r'(\d*)([SDWM]?)', str(timeframe).upper())
    if not match:
        raise ValueError(f'Unsupported Pine timeframe: {timeframe}')
    count, unit = int(match[1] or '1'), match[2]
    if count <= 0:
        raise ValueError('timeframe must be positive')
    return count * {'': 60, 'S': 1, 'D': 86400, 'W': 604800, 'M': 2628000}[unit]


def micro_timeframe(chart_timeframe: str) -> str:
    seconds = timeframe_seconds(chart_timeframe)
    for bound, micro in ((60, '30S'), (300, '1'), (900, '5'), (3600, '5'), (14400, '30')):
        if seconds <= bound:
            return micro
    return '60'
