"""Security selection and reconciliation utilities with explicit missing data."""
from dataclasses import dataclass, field
from .models import Bar, DataQuality
from .timeframes import bounds


def security_bar(bars: list[Bar], chart_bar: Bar, realtime=False) -> Bar | None:
    """gaps_off / lookahead_off: closed history; developing HTF only realtime."""
    if realtime:
        observed_until = chart_bar.received_at if chart_bar.received_at is not None else chart_bar.start
        eligible = [bar for bar in bars if bar.start <= observed_until and bar.start < chart_bar.end]
    else:
        # request.security on a lower timeframe returns the last available
        # intrabar historically; do not restrict starts to chart-bar open.
        eligible = [bar for bar in bars if bar.confirmed and bar.end <= chart_bar.end]
    return max(eligible, key=lambda b: b.start) if eligible else None


def lower_tf_bars(bars: list[Bar], chart_bar: Bar, realtime=False) -> list[Bar]:
    return sorted([bar for bar in bars if chart_bar.start <= bar.start and bar.end <= chart_bar.end and (realtime or bar.confirmed)], key=lambda bar: bar.start)


def missing_intervals(bars: list[Bar]) -> list[tuple[int, int]]:
    ordered = sorted(bars, key=lambda b: b.start)
    return [(before.end, after.start) for before, after in zip(ordered, ordered[1:]) if before.end < after.start]


@dataclass
class BarBuffer:
    """Deduplicates revisions, protects confirmed bars from delayed partial frames."""
    capacity: int = 10000
    bars: dict[int, Bar] = field(default_factory=dict)
    revisions: int = 0

    def upsert(self, bar: Bar) -> bool:
        old = self.bars.get(bar.start)
        if old and old.confirmed and not bar.confirmed:
            return False
        if old and old.received_at and bar.received_at and old.received_at > bar.received_at:
            return False
        if old == bar:
            return False
        if old and old.confirmed and bar.confirmed:
            self.revisions += 1
        self.bars[bar.start] = bar
        if len(self.bars) > self.capacity:
            for key in sorted(self.bars)[:len(self.bars) - self.capacity]:
                del self.bars[key]
        return True

    def reconcile(self, rest_bars: list[Bar]) -> list[int]:
        corrected = []
        for bar in rest_bars:
            if bar.confirmed and self.upsert(bar):
                corrected.append(bar.start)
        return corrected

    def chronological(self) -> list[Bar]:
        return sorted(self.bars.values(), key=lambda b: b.start)


def quality(chart_timeframe: str, *, kline_fresh: bool, trades_complete: bool, gaps=False) -> dict:
    from backend.engine.pine_compat import micro_timeframe
    reasons = []
    if not kline_fresh:
        reasons.append('kline_stale')
    if gaps:
        reasons.append('history_gap')
    if micro_timeframe(chart_timeframe) == '30S' and not trades_complete:
        reasons.append('30s_trade_history_incomplete')
    status = DataQuality.DEGRADED if reasons else DataQuality.FULL_REALTIME if trades_complete else DataQuality.KLINE_REALTIME
    return {'quality': status.value, 'reasons': reasons}


def reconcile_micro(native: Bar, intrabars: list[Bar], tick_size: float, volume_tolerance=1e-8) -> dict:
    """Compare aggregated 30S OHLCV to authoritative closed native bar.

    Returns a report; state replay belongs to the engine owner. No mutation of
    micro candles can reconstruct the unknown distribution inside a minute.
    """
    selected = lower_tf_bars(intrabars, native)
    complete = bool(selected) and selected[0].start == native.start and selected[-1].end == native.end and not missing_intervals(selected)
    if not native.confirmed or not complete:
        return {'status': 'INCOMPLETE', 'bar_start': native.start, 'differences': {}}
    calculated = {'open': selected[0].open, 'high': max(b.high for b in selected), 'low': min(b.low for b in selected), 'close': selected[-1].close, 'volume': sum(b.volume for b in selected)}
    differences = {key: calculated[key] - getattr(native, key) for key in calculated}
    price_match = all(abs(differences[key]) <= tick_size * 1e-6 for key in ('open', 'high', 'low', 'close'))
    volume_match = abs(differences['volume']) <= max(volume_tolerance, abs(native.volume) * volume_tolerance)
    return {'status': 'MATCH' if price_match and volume_match else 'MISMATCH', 'bar_start': native.start, 'differences': differences, 'requires_replay': not (price_match and volume_match)}
