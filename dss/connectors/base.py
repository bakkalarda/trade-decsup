"""Abstract base for all data connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import pandas as pd


class ConnectorError(Exception):
    """Raised when a data connector fails."""


class BaseConnector(ABC):
    """Every connector must implement fetch_ohlcv at minimum."""

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[datetime] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Return a DataFrame with columns: open, high, low, close, volume
        and a UTC DatetimeIndex."""
        ...

    def health_check(self) -> dict:
        """Return connector health status."""
        return {"status": "ok", "connector": self.__class__.__name__}
