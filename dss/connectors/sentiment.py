"""Sentiment data connector — Fear & Greed index."""

from __future__ import annotations

import logging
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

FEAR_GREED_URL = "https://api.alternative.me/fng/"


class SentimentConnector:
    """Fetch crypto Fear & Greed index (free API)."""

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def fetch_fear_greed(self, limit: int = 30) -> pd.DataFrame:
        """Fetch Fear & Greed index history.

        Returns DataFrame with columns: value, classification, indexed by timestamp.
        """
        try:
            resp = self._client.get(FEAR_GREED_URL, params={"limit": limit, "format": "json"})
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except Exception as e:
            logger.warning("Failed to fetch Fear & Greed: %s", e)
            return pd.DataFrame()

        if not data:
            return pd.DataFrame()

        records = [
            {
                "timestamp": pd.Timestamp(int(d["timestamp"]), unit="s", tz="UTC"),
                "value": int(d["value"]),
                "classification": d["value_classification"],
            }
            for d in data
        ]
        df = pd.DataFrame(records).set_index("timestamp").sort_index()
        return df

    def fetch_current(self) -> Optional[dict]:
        """Return the latest Fear & Greed reading."""
        df = self.fetch_fear_greed(limit=1)
        if df.empty:
            return None
        row = df.iloc[-1]
        return {
            "value": int(row["value"]),
            "classification": row["classification"],
            "timestamp": str(df.index[-1]),
        }

    def health_check(self) -> dict:
        try:
            current = self.fetch_current()
            return {
                "status": "ok" if current else "error",
                "connector": "SentimentConnector",
                "latest_value": current.get("value") if current else None,
            }
        except Exception as e:
            return {"status": "error", "connector": "SentimentConnector", "error": str(e)}

    def close(self):
        self._client.close()
