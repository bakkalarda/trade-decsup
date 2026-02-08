"""Timeframe alignment and conversion utilities."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

# Mapping from DSS timeframe strings to pandas offset aliases
TF_MAP: dict[str, str] = {
    "1w": "W",
    "1d": "D",
    "4h": "4h",
    "1h": "1h",
}

# ccxt timeframe strings
CCXT_TF_MAP: dict[str, str] = {
    "1w": "1w",
    "1d": "1d",
    "4h": "4h",
    "1h": "1h",
}


def align_to_timeframe(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample an OHLCV DataFrame to the given timeframe.

    Expects columns: open, high, low, close, volume with a DatetimeIndex.
    """
    offset = TF_MAP.get(tf, tf)
    resampled = df.resample(offset).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    return resampled


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def tf_to_minutes(tf: str) -> int:
    """Convert timeframe string to minutes."""
    mapping = {"1w": 10080, "1d": 1440, "4h": 240, "1h": 60}
    return mapping.get(tf, 240)
