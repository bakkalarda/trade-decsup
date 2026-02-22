"""Data access layer — CRUD operations on the database."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from dss.storage.database import (
    OHLCVCache, StateSnapshot, PositionRecord, AlertLog, RejectedLog,
    get_session, init_db,
)

logger = logging.getLogger(__name__)


class Repository:
    """Unified data access for the DSS engine."""

    def __init__(self, session: Session | None = None):
        if session is None:
            engine = init_db()
            self._session = get_session(engine)
        else:
            self._session = session

    # ------- OHLCV Cache -------

    def get_cached_ohlcv(
        self, asset: str, timeframe: str, limit: int = 500
    ) -> pd.DataFrame:
        """Retrieve cached OHLCV bars."""
        rows = (
            self._session.query(OHLCVCache)
            .filter(OHLCVCache.asset == asset, OHLCVCache.timeframe == timeframe)
            .order_by(OHLCVCache.timestamp.desc())
            .limit(limit)
            .all()
        )
        if not rows:
            return pd.DataFrame()

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
        df = pd.DataFrame(records).set_index("timestamp").sort_index()
        return df

    def cache_ohlcv(self, asset: str, timeframe: str, df: pd.DataFrame):
        """Store OHLCV bars in cache (upsert by asset+tf+timestamp)."""
        for ts, row in df.iterrows():
            existing = (
                self._session.query(OHLCVCache)
                .filter(
                    OHLCVCache.asset == asset,
                    OHLCVCache.timeframe == timeframe,
                    OHLCVCache.timestamp == ts,
                )
                .first()
            )
            if existing:
                existing.open = float(row["open"])
                existing.high = float(row["high"])
                existing.low = float(row["low"])
                existing.close = float(row["close"])
                existing.volume = float(row["volume"])
                existing.fetched_at = datetime.now(timezone.utc)
            else:
                self._session.add(OHLCVCache(
                    asset=asset,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                ))
        self._session.commit()

    def get_ohlcv_freshness(self, asset: str, timeframe: str) -> Optional[datetime]:
        """Return the timestamp of the most recent cached bar."""
        row = (
            self._session.query(OHLCVCache.timestamp)
            .filter(OHLCVCache.asset == asset, OHLCVCache.timeframe == timeframe)
            .order_by(OHLCVCache.timestamp.desc())
            .first()
        )
        return row[0] if row else None

    # ------- State Snapshots -------

    def save_state(self, gate: str, state_dict: dict, asset: str | None = None, timeframe: str | None = None):
        """Persist a gate state snapshot."""
        self._session.add(StateSnapshot(
            asset=asset,
            gate=gate,
            timeframe=timeframe,
            state_json=state_dict,
        ))
        self._session.commit()

    def get_latest_state(self, gate: str, asset: str | None = None) -> Optional[dict]:
        """Retrieve the most recent state snapshot for a gate."""
        q = self._session.query(StateSnapshot).filter(StateSnapshot.gate == gate)
        if asset:
            q = q.filter(StateSnapshot.asset == asset)
        row = q.order_by(StateSnapshot.timestamp.desc()).first()
        return row.state_json if row else None

    # ------- Positions -------

    def add_position(self, pos: dict) -> int:
        """Add a new position, return its ID."""
        record = PositionRecord(**pos)
        if record.original_stop == 0.0:
            record.original_stop = record.stop_loss
        self._session.add(record)
        self._session.commit()
        return record.id

    def get_open_positions(self) -> list[dict]:
        rows = self._session.query(PositionRecord).filter(PositionRecord.status == "OPEN").all()
        return [self._position_to_dict(r) for r in rows]

    def get_position(self, pos_id: int) -> Optional[dict]:
        row = self._session.get(PositionRecord, pos_id)
        return self._position_to_dict(row) if row else None

    def update_position(self, pos_id: int, updates: dict):
        row = self._session.get(PositionRecord, pos_id)
        if not row:
            return
        for k, v in updates.items():
            if hasattr(row, k):
                setattr(row, k, v)
        self._session.commit()

    def close_position(self, pos_id: int, reason: str, realized_pnl_pct: float = 0.0):
        row = self._session.get(PositionRecord, pos_id)
        if not row:
            return
        row.status = "CLOSED"
        row.close_reason = reason
        row.closed_at = datetime.now(timezone.utc)
        row.realized_pnl_pct = realized_pnl_pct
        self._session.commit()

    def _position_to_dict(self, r: PositionRecord) -> dict:
        return {
            "id": r.id,
            "asset": r.asset,
            "direction": r.direction,
            "setup_type": r.setup_type,
            "entry_price": r.entry_price,
            "current_price": r.current_price,
            "stop_loss": r.stop_loss,
            "original_stop": r.original_stop,
            "target_1": r.target_1,
            "target_2": r.target_2,
            "size_multiplier": r.size_multiplier,
            "status": r.status,
            "opened_at": r.opened_at.isoformat() if r.opened_at else None,
            "closed_at": r.closed_at.isoformat() if r.closed_at else None,
            "close_reason": r.close_reason,
            "unrealized_pnl_pct": r.unrealized_pnl_pct,
            "realized_pnl_pct": r.realized_pnl_pct,
            "trail_active": r.trail_active,
            "trail_method": r.trail_method,
            "notes": r.notes,
        }

    # ------- Alert/Rejected Logs -------

    def log_alert(self, alert_dict: dict):
        self._session.add(AlertLog(
            asset=alert_dict.get("asset", ""),
            direction=alert_dict.get("direction", ""),
            setup_type=alert_dict.get("setup_type", ""),
            total_score=alert_dict.get("total_score", 0.0),
            alert_json=alert_dict,
        ))
        self._session.commit()

    def log_rejected(self, rejected_dict: dict):
        self._session.add(RejectedLog(
            asset=rejected_dict.get("asset", ""),
            setup_type=rejected_dict.get("setup_type", ""),
            direction=rejected_dict.get("direction", ""),
            total_score=rejected_dict.get("total_score", 0.0),
            failed_gate=rejected_dict.get("failed_gate"),
            veto_reasons=rejected_dict.get("veto_reasons", []),
            score_gap=rejected_dict.get("score_gap", 0.0),
            notes=rejected_dict.get("notes"),
        ))
        self._session.commit()

    def get_recent_alerts(self, hours: int = 24) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = (
            self._session.query(AlertLog)
            .filter(AlertLog.timestamp >= cutoff)
            .order_by(AlertLog.timestamp.desc())
            .all()
        )
        return [r.alert_json for r in rows]

    # ------- Status -------

    def get_data_freshness(self) -> dict:
        """Return data freshness for all assets/timeframes."""
        result = {}
        rows = (
            self._session.query(
                OHLCVCache.asset,
                OHLCVCache.timeframe,
                func.max(OHLCVCache.fetched_at).label("latest_fetched_at"),
            )
            .group_by(OHLCVCache.asset, OHLCVCache.timeframe)
            .order_by(OHLCVCache.asset, OHLCVCache.timeframe)
            .all()
        )
        for r in rows:
            key = f"{r.asset}_{r.timeframe}"
            result[key] = r.latest_fetched_at.isoformat() if r.latest_fetched_at else None
        return result

    def close(self):
        self._session.close()
