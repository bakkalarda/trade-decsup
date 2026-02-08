"""SQLAlchemy database models and engine setup."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Column, Integer, Float, String, Boolean, DateTime, Text, JSON,
    create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------

class OHLCVCache(Base):
    """Cached OHLCV bars to reduce API calls."""
    __tablename__ = "ohlcv_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset = Column(String(10), nullable=False, index=True)
    timeframe = Column(String(5), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, default=0.0)
    fetched_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class StateSnapshot(Base):
    """Point-in-time snapshot of any gate state (JSON blob)."""
    __tablename__ = "state_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    asset = Column(String(10), nullable=True, index=True)
    gate = Column(String(30), nullable=False)  # macro, flow, structure, setup, decision
    timeframe = Column(String(5), nullable=True)
    state_json = Column(JSON, nullable=False)


class PositionRecord(Base):
    """Tracked positions with full lifecycle."""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset = Column(String(10), nullable=False, index=True)
    direction = Column(String(5), nullable=False)
    setup_type = Column(String(30), nullable=True)
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float, default=0.0)
    stop_loss = Column(Float, nullable=False)
    original_stop = Column(Float, default=0.0)
    target_1 = Column(Float, nullable=False)
    target_2 = Column(Float, nullable=True)
    size_multiplier = Column(Float, default=1.0)
    status = Column(String(15), default="OPEN", index=True)
    opened_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime(timezone=True), nullable=True)
    close_reason = Column(String(50), nullable=True)
    unrealized_pnl_pct = Column(Float, default=0.0)
    realized_pnl_pct = Column(Float, nullable=True)
    trail_active = Column(Boolean, default=False)
    trail_method = Column(String(50), default="")
    notes = Column(Text, nullable=True)


class AlertLog(Base):
    """Log of all emitted alerts."""
    __tablename__ = "alert_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    asset = Column(String(10), nullable=False)
    direction = Column(String(5), nullable=False)
    setup_type = Column(String(30), nullable=False)
    total_score = Column(Float, nullable=False)
    alert_json = Column(JSON, nullable=False)


class RejectedLog(Base):
    """Log of rejected / near-miss candidates."""
    __tablename__ = "rejected_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    asset = Column(String(10), nullable=False)
    setup_type = Column(String(30), nullable=False)
    direction = Column(String(5), nullable=False)
    total_score = Column(Float, nullable=False)
    failed_gate = Column(String(30), nullable=True)
    veto_reasons = Column(JSON, nullable=True)
    score_gap = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Engine and session factory
# ---------------------------------------------------------------------------

def _default_db_path() -> str:
    env = os.environ.get("DSS_DB_PATH")
    if env:
        return env
    db_dir = Path(__file__).resolve().parent.parent.parent / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "dss.db")


def get_engine(db_path: str | None = None):
    path = db_path or _default_db_path()
    engine = create_engine(f"sqlite:///{path}", echo=False)

    # Enable WAL mode for better concurrent read performance
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def init_db(db_path: str | None = None):
    """Create all tables if they don't exist."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine=None) -> Session:
    if engine is None:
        engine = get_engine()
    factory = sessionmaker(bind=engine)
    return factory()
