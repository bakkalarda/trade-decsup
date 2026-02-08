"""Derivatives data connector — funding, OI, liquidations via ccxt."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

from dss.connectors.price import CryptoConnector

logger = logging.getLogger(__name__)


class DerivativesConnector:
    """Fetch derivatives positioning data (funding, OI) via ccxt."""

    def __init__(self, exchange_id: str = "binance"):
        self._crypto = CryptoConnector(exchange_id)

    def fetch_funding_rate(self, perp_symbol: str) -> Optional[float]:
        """Current funding rate."""
        return self._crypto.fetch_funding_rate(perp_symbol)

    def fetch_funding_history(
        self, perp_symbol: str, days: int = 90
    ) -> pd.DataFrame:
        """Historical funding rates."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return self._crypto.fetch_funding_history(perp_symbol, since=since, limit=1000)

    def fetch_open_interest(self, perp_symbol: str) -> Optional[float]:
        """Current OI in base currency."""
        return self._crypto.fetch_open_interest(perp_symbol)

    def fetch_derivatives_snapshot(self, perp_symbol: str) -> dict:
        """Return a dict with current funding + OI for quick consumption."""
        funding = self.fetch_funding_rate(perp_symbol)
        oi = self.fetch_open_interest(perp_symbol)
        return {
            "symbol": perp_symbol,
            "funding_rate": funding,
            "open_interest": oi,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

    def health_check(self) -> dict:
        return self._crypto.health_check()
