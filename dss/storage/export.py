"""Export utilities — parquet files for backtesting."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from dss.storage.database import OHLCVCache, StateSnapshot, get_session, init_db

logger = logging.getLogger(__name__)


def export_ohlcv_parquet(
    asset: str,
    timeframe: str,
    output_dir: str = "data/exports",
    session: Session | None = None,
) -> str:
    """Export cached OHLCV for an asset+tf to parquet. Returns output path."""
    if session is None:
        engine = init_db()
        session = get_session(engine)

    rows = (
        session.query(OHLCVCache)
        .filter(OHLCVCache.asset == asset, OHLCVCache.timeframe == timeframe)
        .order_by(OHLCVCache.timestamp.asc())
        .all()
    )

    if not rows:
        logger.warning("No data to export for %s %s", asset, timeframe)
        return ""

    records = [
        {
            "timestamp": r.timestamp,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
        }
        for r in rows
    ]
    df = pd.DataFrame(records).set_index("timestamp")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    filename = out_path / f"{asset}_{timeframe}.parquet"
    df.to_parquet(str(filename))
    logger.info("Exported %d rows to %s", len(df), filename)
    return str(filename)


def export_states_parquet(
    gate: str,
    output_dir: str = "data/exports",
    session: Session | None = None,
) -> str:
    """Export state snapshots for a gate to parquet."""
    if session is None:
        engine = init_db()
        session = get_session(engine)

    rows = (
        session.query(StateSnapshot)
        .filter(StateSnapshot.gate == gate)
        .order_by(StateSnapshot.timestamp.asc())
        .all()
    )

    if not rows:
        return ""

    records = [
        {"timestamp": r.timestamp, "asset": r.asset, "gate": r.gate, "state": r.state_json}
        for r in rows
    ]
    df = pd.DataFrame(records).set_index("timestamp")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    filename = out_path / f"states_{gate}.parquet"
    df.to_parquet(str(filename))
    return str(filename)
