"""Durable analytic records. UTC timestamps are integer milliseconds throughout."""
from __future__ import annotations
from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import LargeBinary

J = JSON().with_variant(JSONB, 'postgresql')
class Base(DeclarativeBase): pass

class ParameterSet(Base):
    __tablename__='parameter_sets'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger)
    values: Mapped[dict] = mapped_column(J)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Instrument(Base):
    __tablename__='instruments'
    exchange: Mapped[str] = mapped_column(String(30), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(60), primary_key=True)
    status: Mapped[str] = mapped_column(String(30))
    tick_size: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(J)

class MarketBar(Base):
    __tablename__='market_bars'
    exchange: Mapped[str] = mapped_column(String(30), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(60), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(16), primary_key=True)
    start: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    end: Mapped[int] = mapped_column(BigInteger)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    confirmed: Mapped[bool] = mapped_column(Boolean)
    received_at: Mapped[int | None] = mapped_column(BigInteger)

class Current(Base):
    __tablename__='setup_current'
    exchange: Mapped[str] = mapped_column(String(30), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(60), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(16), primary_key=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, index=True)
    last_snapshot: Mapped[int] = mapped_column(BigInteger, default=0)
    payload: Mapped[dict] = mapped_column(J)
    checkpoint: Mapped[dict | None] = mapped_column(J, deferred=True)
    checkpoint_blob: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)

class Snapshot(Base):
    # PostgreSQL migration 0005 uses physical PK (id,event_time) for RANGE
    # partitions. The shared sequence preserves the logical ORM identity id.
    __tablename__='setup_snapshots'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(30))
    symbol: Mapped[str] = mapped_column(String(60))
    timeframe: Mapped[str] = mapped_column(String(16))
    event_time: Mapped[int] = mapped_column(BigInteger)
    bar_start: Mapped[int] = mapped_column(BigInteger)
    confirmed: Mapped[bool] = mapped_column(Boolean)
    generation: Mapped[str] = mapped_column(String(160))
    action: Mapped[str | None] = mapped_column(String(120))
    fsm: Mapped[str | None] = mapped_column(String(60))
    direction: Mapped[str | None] = mapped_column(String(16))
    parameter_set_id: Mapped[str] = mapped_column(ForeignKey('parameter_sets.id'))
    price: Mapped[float | None] = mapped_column(Float)
    formation: Mapped[float | None] = mapped_column(Float)
    execution: Mapped[float | None] = mapped_column(Float)
    geometry: Mapped[float | None] = mapped_column(Float)
    context: Mapped[float | None] = mapped_column(Float)
    level: Mapped[float | None] = mapped_column(Float)
    approach: Mapped[float | None] = mapped_column(Float)
    exhaustion: Mapped[float | None] = mapped_column(Float)
    mae: Mapped[float | None] = mapped_column(Float)
    avg_setup: Mapped[float | None] = mapped_column(Float)
    continuation: Mapped[float | None] = mapped_column(Float)
    btc_shock: Mapped[float | None] = mapped_column(Float)
    sl: Mapped[float | None] = mapped_column(Float)
    t1: Mapped[float | None] = mapped_column(Float)
    rr: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(J)
    __table_args__=(Index('ix_snapshot_market_time','exchange','symbol','timeframe','event_time'),Index('ix_snapshot_generation','generation','event_time'),Index('ix_snapshot_retention','event_time','id'))

class Event(Base):
    # Same logical/physical key convention as Snapshot; no IDs are regenerated.
    __tablename__='setup_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(30))
    symbol: Mapped[str] = mapped_column(String(60))
    timeframe: Mapped[str] = mapped_column(String(16))
    event_time: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(J)
    __table_args__=(Index('ix_event_market_time','exchange','symbol','timeframe','event_time'),Index('ix_event_retention','event_time','id'))

class Signal(Base):
    __tablename__='signals'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    symbol: Mapped[str] = mapped_column(String(60), index=True)
    timeframe: Mapped[str] = mapped_column(String(16))
    event_time: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(120))
    parameter_set_id: Mapped[str] = mapped_column(ForeignKey('parameter_sets.id'))
    payload: Mapped[dict] = mapped_column(J)

class TradingViewAlert(Base):
    __tablename__='tradingview_alerts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    received_at: Mapped[int] = mapped_column(BigInteger, index=True)
    raw_body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64), index=True)
    exchange: Mapped[str | None] = mapped_column(String(30))
    symbol: Mapped[str | None] = mapped_column(String(60))
    timeframe: Mapped[str | None] = mapped_column(String(16))
    bar_start: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(J)
    __table_args__=(Index('ix_tv_market_bar','exchange','symbol','timeframe','bar_start'),)

class WTCurrent(Base):
    __tablename__='wt_current'
    exchange: Mapped[str] = mapped_column(String(30),primary_key=True)
    symbol: Mapped[str] = mapped_column(String(60),primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(16),primary_key=True)
    signal_source: Mapped[str] = mapped_column(String(20),primary_key=True)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(J)

class WTEvent(Base):
    __tablename__='wt_events'
    id: Mapped[int] = mapped_column(Integer,primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64),unique=True)
    received_at: Mapped[int] = mapped_column(BigInteger,index=True)
    exchange: Mapped[str] = mapped_column(String(30))
    symbol: Mapped[str] = mapped_column(String(60))
    timeframe: Mapped[str] = mapped_column(String(16))
    signal_source: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(J)
    __table_args__=(Index('ix_wt_event_market','exchange','symbol','timeframe','received_at'),)

class WTTrade(Base):
    __tablename__='wt_trades'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    exchange: Mapped[str] = mapped_column(String(30))
    symbol: Mapped[str] = mapped_column(String(60))
    timeframe: Mapped[str] = mapped_column(String(16))
    parameter_hash: Mapped[str] = mapped_column(String(64))
    entry_timestamp: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger,index=True)
    payload: Mapped[dict] = mapped_column(J)
    __table_args__=(Index('ix_wt_trade_market','kind','symbol','timeframe','entry_timestamp'),)

class Rule(Base):
    __tablename__='alert_rules'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    payload: Mapped[dict] = mapped_column(J)

class RuleVersion(Base):
    __tablename__='alert_rule_versions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(J)
    __table_args__=(UniqueConstraint('rule_id','version'),)

class RuleState(Base):
    __tablename__='alert_rule_state'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    previous: Mapped[dict | None] = mapped_column(J)
    last_enqueued: Mapped[int | None] = mapped_column(BigInteger)
    generation: Mapped[str | None] = mapped_column(String(160))

class Delivery(Base):
    __tablename__='alert_deliveries'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    rule_id: Mapped[str] = mapped_column(String(36))
    rule_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    next_attempt: Mapped[int] = mapped_column(BigInteger, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(J)

class ResearchSample(Base):
    __tablename__='research_samples'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(60), index=True)
    timeframe: Mapped[str] = mapped_column(String(16))
    generation: Mapped[str] = mapped_column(String(160))
    event_time: Mapped[int] = mapped_column(BigInteger)
    parameter_set_id: Mapped[str] = mapped_column(ForeignKey('parameter_sets.id'))
    family: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(J)

class ParityReference(Base):
    __tablename__='parity_reference'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(J)
class ParityResult(Base):
    __tablename__='parity_results'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger, index=True)
    payload: Mapped[dict] = mapped_column(J)
class ServiceHealth(Base):
    __tablename__='service_health'
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    updated_at: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(J)


class ArchiveBatch(Base):
    __tablename__='archive_batches'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    table_name: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[int] = mapped_column(BigInteger,index=True)
    cutoff: Mapped[int] = mapped_column(BigInteger)
    first_event: Mapped[int] = mapped_column(BigInteger)
    last_event: Mapped[int] = mapped_column(BigInteger)
    row_count: Mapped[int] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(Text,unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    file_bytes: Mapped[int] = mapped_column(BigInteger)
    restored_at: Mapped[int | None] = mapped_column(BigInteger)
