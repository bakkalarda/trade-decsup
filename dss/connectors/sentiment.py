"""Sentiment data connector — multiple free sources.

Sources:
  - Alternative.me: Fear & Greed index (free, no key)
  - CoinGecko: Trending coins, global market data (free demo tier)
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

FEAR_GREED_URL = "https://api.alternative.me/fng/"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class SentimentConnector:
    """Fetch crypto sentiment data from free APIs."""

    def __init__(self):
        self._client = httpx.Client(timeout=20)

    # ------------------------------------------------------------------
    # Fear & Greed Index (Alternative.me)
    # ------------------------------------------------------------------

    def fetch_fear_greed(self, limit: int = 30) -> pd.DataFrame:
        """Fetch Fear & Greed index history.

        Returns DataFrame with columns: value, classification, indexed by timestamp.
        """
        try:
            resp = self._client.get(
                FEAR_GREED_URL, params={"limit": limit, "format": "json"}
            )
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

    # ------------------------------------------------------------------
    # CoinGecko — Trending & Social Signals
    # ------------------------------------------------------------------

    def fetch_trending(self) -> Optional[dict]:
        """Fetch trending coins from CoinGecko.

        Extreme trending activity can signal retail FOMO (contrarian bearish).
        """
        try:
            resp = self._client.get(f"{COINGECKO_BASE}/search/trending")
            resp.raise_for_status()
            data = resp.json()

            coins = data.get("coins", [])
            if not coins:
                return None

            top_trending = []
            for item in coins[:7]:
                c = item.get("item", {})
                top_trending.append({
                    "name": c.get("name", ""),
                    "symbol": c.get("symbol", ""),
                    "market_cap_rank": c.get("market_cap_rank"),
                    "score": c.get("score", 0),
                })

            return {
                "count": len(top_trending),
                "coins": top_trending,
            }

        except Exception as e:
            logger.warning("CoinGecko trending failed: %s", e)
            return None

    def fetch_global_sentiment_metrics(self) -> Optional[dict]:
        """Derive sentiment signals from CoinGecko global market data.

        Useful for:
          - Total market cap change (momentum)
          - BTC dominance trends (risk rotation)
          - Active cryptos count
        """
        try:
            resp = self._client.get(f"{COINGECKO_BASE}/global")
            resp.raise_for_status()
            data = resp.json().get("data", {})

            mcap_change_24h = data.get("market_cap_change_percentage_24h_usd", 0)
            btc_dom = data.get("market_cap_percentage", {}).get("btc", 0)

            # Interpret
            if mcap_change_24h > 5:
                momentum = "EUPHORIC"
            elif mcap_change_24h > 2:
                momentum = "BULLISH"
            elif mcap_change_24h < -5:
                momentum = "PANIC"
            elif mcap_change_24h < -2:
                momentum = "BEARISH"
            else:
                momentum = "NEUTRAL"

            # High BTC dominance = flight to quality (risk-off within crypto)
            if btc_dom > 60:
                rotation = "QUALITY_FLIGHT"
            elif btc_dom < 40:
                rotation = "ALT_SEASON"
            else:
                rotation = "BALANCED"

            return {
                "market_cap_change_24h_pct": round(mcap_change_24h, 2),
                "btc_dominance_pct": round(btc_dom, 2),
                "market_momentum": momentum,
                "capital_rotation": rotation,
            }

        except Exception as e:
            logger.warning("CoinGecko global sentiment failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Composite
    # ------------------------------------------------------------------

    def fetch_sentiment_snapshot(self) -> dict:
        """All-in-one sentiment snapshot for live monitoring."""
        snapshot: dict = {}

        fng = self.fetch_current()
        if fng:
            snapshot["fear_greed"] = fng

        global_sent = self.fetch_global_sentiment_metrics()
        if global_sent:
            snapshot["global"] = global_sent

        trending = self.fetch_trending()
        if trending:
            snapshot["trending"] = trending

        return snapshot

    def health_check(self) -> dict:
        results = {}
        try:
            current = self.fetch_current()
            results["fear_greed"] = "ok" if current else "error"
        except Exception as e:
            results["fear_greed"] = f"error: {e}"

        try:
            resp = self._client.get(f"{COINGECKO_BASE}/ping", timeout=5)
            results["coingecko"] = "ok" if resp.status_code == 200 else "error"
        except Exception as e:
            results["coingecko"] = f"error: {e}"

        overall = "ok" if all(v == "ok" for v in results.values()) else "partial"
        return {"status": overall, "connector": "SentimentConnector", "details": results}

    def close(self):
        self._client.close()
