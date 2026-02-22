"""Price connectors — ccxt for crypto, yfinance for metals/macro."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import ccxt
import pandas as pd
import yfinance as yf

from dss.connectors.base import BaseConnector, ConnectorError
from dss.utils.timeframes import CCXT_TF_MAP

logger = logging.getLogger(__name__)


class CryptoConnector(BaseConnector):
    """Fetch crypto OHLCV data via ccxt (default: Binance)."""

    def __init__(self, exchange_id: str = "binance"):
        exchange_cls = getattr(ccxt, exchange_id, None)
        if exchange_cls is None:
            raise ConnectorError(f"Unknown exchange: {exchange_id}")
        self._exchange: ccxt.Exchange = exchange_cls({
            "enableRateLimit": True,
        })

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        since: Optional[datetime] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        tf = CCXT_TF_MAP.get(timeframe, timeframe)
        since_ms = int(since.timestamp() * 1000) if since else None

        try:
            raw = self._exchange.fetch_ohlcv(symbol, tf, since=since_ms, limit=limit)
        except ccxt.BaseError as e:
            raise ConnectorError(f"ccxt error fetching {symbol} {tf}: {e}") from e

        if not raw:
            raise ConnectorError(f"No data returned for {symbol} {tf}")

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        return df

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        """Fetch the latest funding rate for a perpetual contract."""
        try:
            info = self._exchange.fetch_funding_rate(symbol)
            return float(info.get("fundingRate", 0.0))
        except Exception as e:
            logger.warning("Failed to fetch funding rate for %s: %s", symbol, e)
            return None

    def fetch_funding_history(
        self, symbol: str, since: Optional[datetime] = None, limit: int = 100
    ) -> pd.DataFrame:
        """Fetch historical funding rates."""
        since_ms = int(since.timestamp() * 1000) if since else None
        try:
            raw = self._exchange.fetch_funding_rate_history(symbol, since=since_ms, limit=limit)
        except Exception as e:
            logger.warning("Failed to fetch funding history for %s: %s", symbol, e)
            return pd.DataFrame()

        if not raw:
            return pd.DataFrame()

        records = [
            {"timestamp": r["timestamp"], "funding_rate": float(r.get("fundingRate", 0.0))}
            for r in raw
        ]
        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        return df

    def fetch_open_interest(self, symbol: str) -> Optional[float]:
        """Fetch current open interest (in base currency)."""
        try:
            oi = self._exchange.fetch_open_interest(symbol)
            return float(oi.get("openInterestAmount", 0.0))
        except Exception as e:
            logger.warning("Failed to fetch OI for %s: %s", symbol, e)
            return None

    def health_check(self) -> dict:
        try:
            self._exchange.load_markets()
            return {"status": "ok", "connector": "CryptoConnector", "exchange": self._exchange.id}
        except Exception as e:
            return {"status": "error", "connector": "CryptoConnector", "error": str(e)}


class TraditionalConnector(BaseConnector):
    """Fetch metals, indices, and macro proxy OHLCV via yfinance."""

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        since: Optional[datetime] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        # yfinance interval mapping
        yf_interval_map = {"1w": "1wk", "1d": "1d", "4h": "1h", "1h": "1h"}
        interval = yf_interval_map.get(timeframe, "1d")

        # Calculate period from limit
        if since:
            start_str = since.strftime("%Y-%m-%d")
            end_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        else:
            # Estimate start date from limit and timeframe
            days_map = {"1w": limit * 7, "1d": limit, "4h": limit // 6 + 1, "1h": limit // 24 + 1}
            days_back = days_map.get(timeframe, limit)
            start_dt = datetime.now(timezone.utc) - timedelta(days=min(days_back, 3650))
            start_str = start_dt.strftime("%Y-%m-%d")
            end_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_str, end=end_str, interval=interval)
        except Exception as e:
            raise ConnectorError(f"yfinance error fetching {symbol}: {e}") from e

        if df.empty:
            raise ConnectorError(f"No data returned for {symbol}")

        # Normalize column names
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df.index = pd.to_datetime(df.index, utc=True)
        df = df.astype(float)

        # If we fetched 1h for a 4h timeframe, resample
        if timeframe == "4h" and interval == "1h":
            df = df.resample("4h").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna(subset=["open"])

        return df

    def health_check(self) -> dict:
        try:
            test = yf.Ticker("GC=F")
            info = test.history(period="1d")
            if info.empty:
                return {"status": "error", "connector": "TraditionalConnector", "error": "no data"}
            return {"status": "ok", "connector": "TraditionalConnector"}
        except Exception as e:
            return {"status": "error", "connector": "TraditionalConnector", "error": str(e)}
