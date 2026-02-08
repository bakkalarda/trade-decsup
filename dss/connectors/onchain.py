"""On-chain data connector — free tier APIs (CoinGlass public, blockchain.info)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# CoinGlass public endpoints (no API key needed for basic data)
COINGLASS_BASE = "https://open-api.coinglass.com/public/v2"
BLOCKCHAIN_INFO_BASE = "https://api.blockchain.info"


class OnchainConnector:
    """Fetch on-chain metrics from free public APIs.

    This is the minimal-viable on-chain layer for BTC/ETH.
    Designed to be swappable with Glassnode/CryptoQuant if keys become available.
    """

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def fetch_btc_exchange_balance(self) -> Optional[dict]:
        """Fetch BTC exchange balance trend from blockchain.info.

        Returns a dict with recent exchange balance data, or None on failure.
        """
        try:
            # blockchain.info exchange balance chart
            url = f"{BLOCKCHAIN_INFO_BASE}/charts/balance-exchanges"
            params = {"timespan": "90days", "format": "json"}
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            values = data.get("values", [])
            if not values:
                return None

            records = [
                {"timestamp": pd.Timestamp(v["x"], unit="s", tz="UTC"), "balance": float(v["y"])}
                for v in values
            ]
            df = pd.DataFrame(records).set_index("timestamp")
            latest = df.iloc[-1]["balance"]
            prev_30d = df.iloc[-30]["balance"] if len(df) >= 30 else df.iloc[0]["balance"]
            return {
                "asset": "BTC",
                "latest_balance": latest,
                "balance_30d_ago": prev_30d,
                "change_30d": latest - prev_30d,
                "trend": "decreasing" if latest < prev_30d else "increasing",
                "data_points": len(df),
            }
        except Exception as e:
            logger.warning("Failed to fetch BTC exchange balance: %s", e)
            return None

    def fetch_exchange_flow_proxy(self, asset: str) -> Optional[dict]:
        """Very basic exchange flow proxy.

        For BTC uses blockchain.info, for others returns None (no free source).
        """
        if asset == "BTC":
            return self.fetch_btc_exchange_balance()
        # For ETH/SOL/XRP, free on-chain data is very limited
        # This connector is designed to be replaced with CryptoQuant/Glassnode
        logger.info("No free on-chain data for %s — returning None", asset)
        return None

    def health_check(self) -> dict:
        try:
            resp = self._client.get(f"{BLOCKCHAIN_INFO_BASE}/q/getblockcount", timeout=5)
            return {
                "status": "ok" if resp.status_code == 200 else "error",
                "connector": "OnchainConnector",
            }
        except Exception as e:
            return {"status": "error", "connector": "OnchainConnector", "error": str(e)}

    def close(self):
        self._client.close()
