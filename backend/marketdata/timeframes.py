"""UTC interval arithmetic, no local timezone or approximate month alignment."""
from datetime import datetime, timezone
from backend.engine.pine_compat import timeframe_seconds


def bounds(timestamp: int, timeframe: str) -> tuple[int, int]:
    tf = timeframe.upper()
    if tf.endswith('M'):
        n = int(tf[:-1] or 1)
        dt = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
        index = dt.year * 12 + dt.month - 1
        start_index = index // n * n
        year, month = divmod(start_index, 12)
        end_year, end_month = divmod(start_index + n, 12)
        return int(datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp() * 1000), int(datetime(end_year, end_month + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    duration = timeframe_seconds(tf) * 1000
    # 1970-01-05, Monday UTC; Pine crypto weeks begin Monday.
    anchor = 345600000 if tf.endswith('W') else 0
    start = (timestamp - anchor) // duration * duration + anchor
    return start, start + duration


def bybit_interval(timeframe: str) -> str:
    tf = str(timeframe).upper()
    tf = {'1D': 'D', '1W': 'W', '1M': 'M'}.get(tf, tf)
    if tf not in {'1', '3', '5', '15', '30', '60', '120', '240', '360', '720', 'D', 'W', 'M'}:
        raise ValueError(f'Bybit has no native kline interval {timeframe}; 30S requires public trades')
    return tf


def binance_interval(timeframe: str) -> str:
    tf = str(timeframe)
    mapping = {'1': '1m', '3': '3m', '5': '5m', '15': '15m', '30': '30m', '60': '1h', '120': '2h', '240': '4h', '360': '6h', '480': '8h', '720': '12h', 'D': '1d', '1D': '1d', '3D': '3d', 'W': '1w', '1W': '1w', 'M': '1M', '1M': '1M'}
    if tf not in mapping:
        raise ValueError(f'Binance has no native kline interval {timeframe}')
    return mapping[tf]
