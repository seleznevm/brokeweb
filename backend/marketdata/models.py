from __future__ import annotations
from dataclasses import asdict, dataclass
from enum import StrEnum


class DataQuality(StrEnum):
    FULL_REALTIME = 'FULL_REALTIME'
    KLINE_REALTIME = 'KLINE_REALTIME'
    DEGRADED = 'DEGRADED'


@dataclass(frozen=True, slots=True)
class Bar:
    exchange: str
    symbol: str
    timeframe: str
    start: int
    end: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    confirmed: bool = True
    received_at: int | None = None
    turnover: float | None = None

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Instrument:
    exchange: str
    symbol: str
    tick_size: float
    base_coin: str
    quote_coin: str
    status: str
    contract_type: str
    launch_time: int

    def to_dict(self):
        return asdict(self)
