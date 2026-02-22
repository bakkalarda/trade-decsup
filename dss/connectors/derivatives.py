"""Derivatives data connector — funding, OI, liquidations, L/S ratios.

Sources:
  - ccxt (Binance): funding rates, OI snapshots, funding history
  - Binance Futures API: liquidations, top-trader L/S, taker buy/sell
  - DeFiLlama: aggregated perps open interest (free, no key)
  - Deribit: funding chart data (public API)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd

from dss.connectors.price import CryptoConnector

logger = logging.getLogger(__name__)

DEFILLAMA_BASE = "https://api.llama.fi"


class DerivativesConnector:
    """Fetch derivatives positioning data (funding, OI, liquidations) via ccxt + DeFiLlama + Binance."""

    def __init__(self, exchange_id: str = "binance"):
        self._crypto = CryptoConnector(exchange_id)
        self._http = httpx.Client(timeout=20)
        # Lazy-init Binance futures connector only when needed
        self._binance_futures = None

    def _get_binance_futures(self):
        """Lazy-load Binance futures connector."""
        if self._binance_futures is None:
            from dss.connectors.binance_futures import BinanceFuturesConnector
            self._binance_futures = BinanceFuturesConnector()
        return self._binance_futures

    # ------------------------------------------------------------------
    # ccxt-based (single exchange)
    # ------------------------------------------------------------------

    def fetch_funding_rate(self, perp_symbol: str) -> Optional[float]:
        """Current funding rate from exchange."""
        return self._crypto.fetch_funding_rate(perp_symbol)

    def fetch_funding_history(
        self, perp_symbol: str, days: int = 90
    ) -> pd.DataFrame:
        """Historical funding rates from exchange."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return self._crypto.fetch_funding_history(perp_symbol, since=since, limit=1000)

    def fetch_open_interest(self, perp_symbol: str) -> Optional[float]:
        """Current OI in base currency from exchange."""
        return self._crypto.fetch_open_interest(perp_symbol)

    def fetch_derivatives_snapshot(self, perp_symbol: str) -> dict:
        """Quick snapshot: funding + OI."""
        funding = self.fetch_funding_rate(perp_symbol)
        oi = self.fetch_open_interest(perp_symbol)
        return {
            "symbol": perp_symbol,
            "funding_rate": funding,
            "open_interest": oi,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # DeFiLlama — aggregated derivatives data (free)
    # ------------------------------------------------------------------

    def fetch_defillama_perps_oi(self) -> pd.DataFrame:
        """Fetch aggregated perps open interest overview from DeFiLlama.

        This gives total OI across all major perps venues, which is a
        much better signal than single-exchange OI.
        """
        try:
            resp = self._http.get(f"{DEFILLAMA_BASE}/overview/derivatives")
            resp.raise_for_status()
            data = resp.json()

            # Total data chart gives historical OI
            chart_data = data.get("totalDataChart", [])
            if not chart_data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(d[0], unit="s", tz="UTC"),
                    "total_oi": float(d[1]),
                }
                for d in chart_data
                if len(d) >= 2
            ]
            df = pd.DataFrame(records).set_index("timestamp").sort_index()
            return df

        except Exception as e:
            logger.warning("DeFiLlama perps OI fetch failed: %s", e)
            return pd.DataFrame()

    def fetch_defillama_perps_volume(self) -> pd.DataFrame:
        """Fetch aggregated perps trading volume from DeFiLlama."""
        try:
            resp = self._http.get(
                f"{DEFILLAMA_BASE}/overview/derivatives",
                params={
                    "excludeTotalDataChart": "false",
                    "excludeTotalDataChartBreakdown": "true",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            chart_data = data.get("totalDataChart", [])
            if not chart_data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(d[0], unit="s", tz="UTC"),
                    "perps_volume": float(d[1]),
                }
                for d in chart_data
                if len(d) >= 2
            ]
            df = pd.DataFrame(records).set_index("timestamp").sort_index()
            return df

        except Exception as e:
            logger.warning("DeFiLlama perps volume fetch failed: %s", e)
            return pd.DataFrame()

    def fetch_defillama_protocol_oi(self, protocol: str = "binance") -> pd.DataFrame:
        """Fetch OI history for a specific perps protocol from DeFiLlama."""
        try:
            resp = self._http.get(
                f"{DEFILLAMA_BASE}/overview/derivatives/{protocol}"
            )
            resp.raise_for_status()
            data = resp.json()

            chart_data = data.get("totalDataChart", [])
            if not chart_data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(d[0], unit="s", tz="UTC"),
                    "oi": float(d[1]),
                }
                for d in chart_data
                if len(d) >= 2
            ]
            df = pd.DataFrame(records).set_index("timestamp").sort_index()
            return df

        except Exception as e:
            logger.warning("DeFiLlama protocol OI failed for %s: %s", protocol, e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Binance Futures — professional-grade positioning data
    # ------------------------------------------------------------------

    def fetch_top_trader_ls_ratio(
        self, symbol: str = "BTCUSDT", period: str = "4h", limit: int = 100
    ) -> pd.DataFrame:
        """Top trader long/short ratio history (free, no key)."""
        try:
            return self._get_binance_futures().fetch_top_trader_ls_ratio(symbol, period, limit)
        except Exception as e:
            logger.warning("Top trader L/S ratio failed: %s", e)
            return pd.DataFrame()

    def fetch_global_ls_ratio(
        self, symbol: str = "BTCUSDT", period: str = "4h", limit: int = 100
    ) -> pd.DataFrame:
        """Global long/short account ratio history (free, no key)."""
        try:
            return self._get_binance_futures().fetch_global_ls_ratio(symbol, period, limit)
        except Exception as e:
            logger.warning("Global L/S ratio failed: %s", e)
            return pd.DataFrame()

    def fetch_taker_buy_sell_ratio(
        self, symbol: str = "BTCUSDT", period: str = "4h", limit: int = 100
    ) -> pd.DataFrame:
        """Taker buy/sell volume ratio history (free, no key)."""
        try:
            return self._get_binance_futures().fetch_taker_buy_sell_ratio(symbol, period, limit)
        except Exception as e:
            logger.warning("Taker buy/sell ratio failed: %s", e)
            return pd.DataFrame()

    def fetch_recent_liquidations(
        self, symbol: str = "BTCUSDT", limit: int = 100
    ) -> pd.DataFrame:
        """Recent forced liquidation orders (free, no key)."""
        try:
            return self._get_binance_futures().fetch_recent_liquidations(symbol, limit)
        except Exception as e:
            logger.warning("Liquidations fetch failed: %s", e)
            return pd.DataFrame()

    def fetch_binance_oi_history(
        self, symbol: str = "BTCUSDT", period: str = "4h", limit: int = 200
    ) -> pd.DataFrame:
        """Historical open interest from Binance (free, no key)."""
        try:
            return self._get_binance_futures().fetch_oi_history(symbol, period, limit)
        except Exception as e:
            logger.warning("Binance OI history failed: %s", e)
            return pd.DataFrame()

    def fetch_full_derivatives_snapshot(self, perp_symbol: str) -> dict:
        """Full derivatives intelligence: funding + OI + L/S + taker + liquidations."""
        result = self.fetch_derivatives_snapshot(perp_symbol)

        # Map perp_symbol to Binance raw symbol (BTC/USDT:USDT → BTCUSDT)
        raw_symbol = perp_symbol.replace("/", "").replace(":USDT", "").replace(":USD", "")

        try:
            intel = self._get_binance_futures().fetch_derivatives_intelligence(raw_symbol)
            result.update(intel)
        except Exception as e:
            logger.warning("Binance intelligence failed: %s", e)

        return result

    def health_check(self) -> dict:
        results = {}
        try:
            results["ccxt"] = self._crypto.health_check()
        except Exception as e:
            results["ccxt"] = {"status": "error", "error": str(e)}

        try:
            resp = self._http.get(f"{DEFILLAMA_BASE}/overview/derivatives", timeout=10)
            results["defillama_derivatives"] = "ok" if resp.status_code == 200 else "error"
        except Exception as e:
            results["defillama_derivatives"] = f"error: {e}"

        return {"status": "ok", "connector": "DerivativesConnector", "details": results}

    def close(self):
        self._http.close()
