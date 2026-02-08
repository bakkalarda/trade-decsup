"""Macro data connectors — DXY, real yields, FRED."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd

from dss.connectors.price import TraditionalConnector

logger = logging.getLogger(__name__)


class MacroConnector:
    """Fetch macro proxy data (DXY, real yields, SPX, VIX)."""

    def __init__(self):
        self._trad = TraditionalConnector()
        self._fred_key = os.environ.get("FRED_API_KEY")

    def fetch_dxy(self, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
        """Fetch DXY (US Dollar Index)."""
        return self._trad.fetch_ohlcv("DX-Y.NYB", timeframe, limit=limit)

    def fetch_spx(self, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
        return self._trad.fetch_ohlcv("^GSPC", timeframe, limit=limit)

    def fetch_vix(self, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
        return self._trad.fetch_ohlcv("^VIX", timeframe, limit=limit)

    def fetch_real_yields(self, limit: int = 200) -> pd.DataFrame:
        """Fetch 10Y TIPS yield from FRED (daily only).

        Falls back to a simplified proxy if FRED key is not set.
        """
        if self._fred_key:
            return self._fetch_fred_series("DFII10", limit)

        # Fallback: use TIP ETF as rough proxy for real yield direction
        logger.info("No FRED_API_KEY — using TIP ETF as real yield proxy")
        try:
            df = self._trad.fetch_ohlcv("TIP", "1d", limit=limit)
            # Invert: when TIP rises, real yields fall
            df["close"] = -df["close"]
            return df
        except Exception as e:
            logger.warning("Failed to fetch TIP proxy: %s", e)
            return pd.DataFrame()

    def _fetch_fred_series(self, series_id: str, limit: int = 200) -> pd.DataFrame:
        """Fetch a FRED time series."""
        url = "https://api.stlouisfed.org/fred/series/observations"
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=limit * 2)  # buffer for weekends/holidays

        params = {
            "series_id": series_id,
            "api_key": self._fred_key,
            "file_type": "json",
            "observation_start": start.strftime("%Y-%m-%d"),
            "observation_end": end.strftime("%Y-%m-%d"),
            "sort_order": "asc",
        }
        try:
            resp = httpx.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("FRED fetch failed for %s: %s", series_id, e)
            return pd.DataFrame()

        observations = data.get("observations", [])
        if not observations:
            return pd.DataFrame()

        records = []
        for obs in observations:
            val = obs.get("value", ".")
            if val == ".":
                continue
            records.append({
                "timestamp": pd.Timestamp(obs["date"], tz="UTC"),
                "close": float(val),
            })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records).set_index("timestamp")
        # Add OHLCV-like columns for compatibility
        df["open"] = df["close"]
        df["high"] = df["close"]
        df["low"] = df["close"]
        df["volume"] = 0.0
        return df[["open", "high", "low", "close", "volume"]].tail(limit)

    def health_check(self) -> dict:
        status = {
            "connector": "MacroConnector",
            "fred_key_set": self._fred_key is not None,
        }
        try:
            df = self.fetch_dxy(limit=5)
            status["dxy_ok"] = not df.empty
        except Exception as e:
            status["dxy_ok"] = False
            status["dxy_error"] = str(e)
        return status
