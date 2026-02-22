"""Binance Futures data connector — liquidations, long/short ratios, taker volume.

All endpoints are PUBLIC and FREE (no API key required).
These are Binance-specific derivatives intelligence that dramatically
improve the flow gate's signal quality.

Data sources (all via direct HTTP, more reliable than ccxt for these):
  - /futures/data/topLongShortPositionRatio — Top trader L/S ratio
  - /futures/data/globalLongShortAccountRatio — Global L/S by accounts
  - /futures/data/takerlongshortRatio — Taker buy/sell volume ratio
  - /fapi/v1/allForceOrders — Recent liquidations
  - /futures/data/openInterestHist — Historical OI
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_FUTURES_DATA = "https://fapi.binance.com"


class BinanceFuturesConnector:
    """Fetch professional-grade derivatives data from Binance Futures.

    Everything here is free and public — no API key needed.
    These signals are what institutional desks actually watch.
    """

    def __init__(self):
        self._client = httpx.Client(timeout=20)

    # ------------------------------------------------------------------
    # Top Trader Long/Short Ratio (by position)
    # ------------------------------------------------------------------

    def fetch_top_trader_ls_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "4h",
        limit: int = 100,
    ) -> pd.DataFrame:
        """Fetch top trader long/short position ratio history.

        This tracks what the top 20% of traders (by notional) are doing.
        When top traders are heavily long → contrarian bearish signal.
        When top traders are heavily short → contrarian bullish signal.

        Returns DataFrame with: long_ratio, short_ratio, long_short_ratio
        """
        try:
            resp = self._client.get(
                f"{BINANCE_FUTURES_DATA}/futures/data/topLongShortPositionRatio",
                params={"symbol": symbol, "period": period, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(int(d["timestamp"]), unit="ms", tz="UTC"),
                    "long_ratio": float(d["longAccount"]),
                    "short_ratio": float(d["shortAccount"]),
                    "long_short_ratio": float(d["longShortRatio"]),
                }
                for d in data
            ]
            return pd.DataFrame(records).set_index("timestamp").sort_index()

        except Exception as e:
            logger.warning("Binance top trader L/S ratio failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Global Long/Short Account Ratio
    # ------------------------------------------------------------------

    def fetch_global_ls_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "4h",
        limit: int = 100,
    ) -> pd.DataFrame:
        """Fetch global long/short account ratio history.

        This is the ratio of ALL accounts on Binance Futures that are
        net long vs net short. Unlike top-trader ratio, this includes
        retail — useful for gauging retail sentiment/crowding.

        Returns DataFrame with: long_ratio, short_ratio, long_short_ratio
        """
        try:
            resp = self._client.get(
                f"{BINANCE_FUTURES_DATA}/futures/data/globalLongShortAccountRatio",
                params={"symbol": symbol, "period": period, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(int(d["timestamp"]), unit="ms", tz="UTC"),
                    "long_ratio": float(d["longAccount"]),
                    "short_ratio": float(d["shortAccount"]),
                    "long_short_ratio": float(d["longShortRatio"]),
                }
                for d in data
            ]
            return pd.DataFrame(records).set_index("timestamp").sort_index()

        except Exception as e:
            logger.warning("Binance global L/S ratio failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Taker Buy/Sell Volume Ratio
    # ------------------------------------------------------------------

    def fetch_taker_buy_sell_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "4h",
        limit: int = 100,
    ) -> pd.DataFrame:
        """Fetch taker buy/sell volume ratio history.

        Taker = market orders (the aggressor). This tells you WHO is
        driving price action:
        - buySellRatio > 1.0 → aggressive buyers (bullish pressure)
        - buySellRatio < 1.0 → aggressive sellers (bearish pressure)
        - Extreme readings (>1.5 or <0.67) are contrarian signals

        Returns DataFrame with: buy_vol, sell_vol, buy_sell_ratio
        """
        try:
            resp = self._client.get(
                f"{BINANCE_FUTURES_DATA}/futures/data/takerlongshortRatio",
                params={"symbol": symbol, "period": period, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(int(d["timestamp"]), unit="ms", tz="UTC"),
                    "buy_vol": float(d["buyVol"]),
                    "sell_vol": float(d["sellVol"]),
                    "buy_sell_ratio": float(d["buySellRatio"]),
                }
                for d in data
            ]
            return pd.DataFrame(records).set_index("timestamp").sort_index()

        except Exception as e:
            logger.warning("Binance taker buy/sell ratio failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Liquidation Data
    # ------------------------------------------------------------------

    def fetch_recent_liquidations(
        self,
        symbol: str = "BTCUSDT",
        limit: int = 100,
    ) -> pd.DataFrame:
        """Fetch recent forced liquidation orders.

        Liquidation clusters indicate:
        - Where stops are being hunted
        - Cascade risk (large liquidations breed more liquidations)
        - Support/resistance levels where overleveraged positions exist

        Returns DataFrame with: side, price, qty, notional_usd
        """
        try:
            resp = self._client.get(
                f"{BINANCE_FAPI}/fapi/v1/allForceOrders",
                params={"symbol": symbol, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(int(d["time"]), unit="ms", tz="UTC"),
                    "side": d["side"],  # BUY = short liquidated, SELL = long liquidated
                    "price": float(d["price"]),
                    "qty": float(d["origQty"]),
                    "notional_usd": float(d["price"]) * float(d["origQty"]),
                    "status": d.get("status", ""),
                }
                for d in data
            ]
            return pd.DataFrame(records).set_index("timestamp").sort_index()

        except Exception as e:
            logger.warning("Binance liquidations failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Historical Open Interest
    # ------------------------------------------------------------------

    def fetch_oi_history(
        self,
        symbol: str = "BTCUSDT",
        period: str = "4h",
        limit: int = 200,
    ) -> pd.DataFrame:
        """Fetch historical open interest from Binance.

        Returns DataFrame with: oi_value (in USDT), oi_qty (in base)
        """
        try:
            resp = self._client.get(
                f"{BINANCE_FUTURES_DATA}/futures/data/openInterestHist",
                params={"symbol": symbol, "period": period, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(int(d["timestamp"]), unit="ms", tz="UTC"),
                    "oi_value": float(d["sumOpenInterestValue"]),
                    "oi_qty": float(d["sumOpenInterest"]),
                }
                for d in data
            ]
            return pd.DataFrame(records).set_index("timestamp").sort_index()

        except Exception as e:
            logger.warning("Binance OI history failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Composite snapshot
    # ------------------------------------------------------------------

    def fetch_derivatives_intelligence(
        self,
        symbol: str = "BTCUSDT",
        period: str = "4h",
    ) -> dict:
        """All-in-one derivatives intelligence for live monitoring.

        Returns a dict with the latest readings from all data sources.
        """
        result: dict = {"symbol": symbol, "timestamp": datetime.now(timezone.utc).isoformat()}

        # Top trader L/S
        top_ls = self.fetch_top_trader_ls_ratio(symbol, period, limit=5)
        if not top_ls.empty:
            latest = top_ls.iloc[-1]
            result["top_trader_ls_ratio"] = round(float(latest["long_short_ratio"]), 4)
            result["top_trader_long_pct"] = round(float(latest["long_ratio"]) * 100, 1)

        # Global L/S
        global_ls = self.fetch_global_ls_ratio(symbol, period, limit=5)
        if not global_ls.empty:
            latest = global_ls.iloc[-1]
            result["global_ls_ratio"] = round(float(latest["long_short_ratio"]), 4)
            result["global_long_pct"] = round(float(latest["long_ratio"]) * 100, 1)

        # Taker buy/sell
        taker = self.fetch_taker_buy_sell_ratio(symbol, period, limit=5)
        if not taker.empty:
            latest = taker.iloc[-1]
            result["taker_buy_sell_ratio"] = round(float(latest["buy_sell_ratio"]), 4)

        # Recent liquidations summary
        liqs = self.fetch_recent_liquidations(symbol, limit=100)
        if not liqs.empty:
            long_liqs = liqs[liqs["side"] == "SELL"]  # SELL = long liquidated
            short_liqs = liqs[liqs["side"] == "BUY"]  # BUY = short liquidated
            result["recent_long_liq_usd"] = round(float(long_liqs["notional_usd"].sum()), 2)
            result["recent_short_liq_usd"] = round(float(short_liqs["notional_usd"].sum()), 2)
            result["liq_imbalance"] = "LONG_PAIN" if result["recent_long_liq_usd"] > result["recent_short_liq_usd"] * 1.5 else (
                "SHORT_PAIN" if result["recent_short_liq_usd"] > result["recent_long_liq_usd"] * 1.5 else "BALANCED"
            )

        return result

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        try:
            resp = self._client.get(
                f"{BINANCE_FAPI}/fapi/v1/time", timeout=5
            )
            return {
                "status": "ok" if resp.status_code == 200 else "error",
                "connector": "BinanceFuturesConnector",
            }
        except Exception as e:
            return {"status": "error", "connector": "BinanceFuturesConnector", "error": str(e)}

    def close(self):
        self._client.close()
