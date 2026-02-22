"""DeFiLlama connector — free API, no auth required.

Provides:
  - Total DeFi TVL and chain-level TVL (risk appetite proxy)
  - Stablecoin supply and supply changes (capital flow proxy)
  - DEX volumes (market activity)
  - Perps overview / open interest (derivatives positioning)

Base URL: https://api.llama.fi  (coins: https://coins.llama.fi)
Docs: https://defillama.com/docs/api
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

LLAMA_BASE = "https://api.llama.fi"
COINS_BASE = "https://coins.llama.fi"
STABLECOINS_BASE = "https://stablecoins.llama.fi"


class DeFiLlamaConnector:
    """Fetch DeFi metrics from DeFiLlama (free, no API key)."""

    def __init__(self):
        self._client = httpx.Client(timeout=20)

    # ------------------------------------------------------------------
    # TVL — Total Value Locked (risk appetite / capital commitment proxy)
    # ------------------------------------------------------------------

    def fetch_global_tvl_history(self) -> pd.DataFrame:
        """Historical total DeFi TVL across all chains (daily).

        Rising TVL = capital inflows, risk-on appetite.
        Falling TVL = capital outflows, risk-off.
        """
        try:
            resp = self._client.get(f"{LLAMA_BASE}/v2/historicalChainTvl")
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(d["date"], unit="s", tz="UTC"),
                    "tvl": float(d["tvl"]),
                }
                for d in data
                if "date" in d and "tvl" in d
            ]
            df = pd.DataFrame(records).set_index("timestamp").sort_index()
            return df

        except Exception as e:
            logger.warning("DeFiLlama global TVL fetch failed: %s", e)
            return pd.DataFrame()

    def fetch_chain_tvl_history(self, chain: str = "Ethereum") -> pd.DataFrame:
        """Historical TVL for a specific chain."""
        try:
            resp = self._client.get(f"{LLAMA_BASE}/v2/historicalChainTvl/{chain}")
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(d["date"], unit="s", tz="UTC"),
                    "tvl": float(d["tvl"]),
                }
                for d in data
                if "date" in d and "tvl" in d
            ]
            df = pd.DataFrame(records).set_index("timestamp").sort_index()
            return df

        except Exception as e:
            logger.warning("DeFiLlama chain TVL fetch failed for %s: %s", chain, e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Stablecoins — supply changes = capital flow signal
    # ------------------------------------------------------------------

    def fetch_stablecoin_supply_history(self) -> pd.DataFrame:
        """Historical total stablecoin supply (USDT + USDC + DAI + others).

        Rising stablecoin supply = fresh capital entering crypto (bullish).
        Falling supply = capital draining out (bearish).
        """
        try:
            resp = self._client.get(
                f"{STABLECOINS_BASE}/stablecoincharts/all",
                params={"stablecoin": 1},  # 1 = aggregate all
            )
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return pd.DataFrame()

            records = []
            for d in data:
                ts = d.get("date")
                total = d.get("totalCirculating", {})
                peggedUSD = total.get("peggedUSD", 0) if isinstance(total, dict) else 0
                if ts and peggedUSD:
                    try:
                        records.append({
                            "timestamp": pd.Timestamp(int(ts), unit="s", tz="UTC"),
                            "total_supply": float(peggedUSD),
                        })
                    except (ValueError, OverflowError):
                        continue

            if not records:
                return pd.DataFrame()

            df = pd.DataFrame(records).set_index("timestamp").sort_index()
            return df

        except Exception as e:
            logger.warning("DeFiLlama stablecoin supply fetch failed: %s", e)
            return pd.DataFrame()

    def fetch_stablecoin_chains(self) -> pd.DataFrame:
        """Current stablecoin breakdown by chain."""
        try:
            resp = self._client.get(f"{STABLECOINS_BASE}/stablecoins",
                                     params={"includePrices": "true"})
            resp.raise_for_status()
            data = resp.json().get("peggedAssets", [])

            records = []
            for coin in data[:10]:  # Top 10
                records.append({
                    "name": coin.get("name", ""),
                    "symbol": coin.get("symbol", ""),
                    "circulating": coin.get("circulating", {}).get("peggedUSD", 0),
                })

            return pd.DataFrame(records)

        except Exception as e:
            logger.warning("DeFiLlama stablecoin chains failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # DEX volumes — trading activity proxy
    # ------------------------------------------------------------------

    def fetch_dex_volume_history(self) -> pd.DataFrame:
        """Historical aggregate DEX trading volume (daily).

        High DEX volume + rising prices = conviction.
        High DEX volume + falling prices = panic / capitulation.
        """
        try:
            resp = self._client.get(f"{LLAMA_BASE}/overview/dexs",
                                     params={"excludeTotalDataChart": "false",
                                             "excludeTotalDataChartBreakdown": "true"})
            resp.raise_for_status()
            data = resp.json().get("totalDataChart", [])

            if not data:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(d[0], unit="s", tz="UTC"),
                    "dex_volume": float(d[1]),
                }
                for d in data
                if len(d) >= 2
            ]
            df = pd.DataFrame(records).set_index("timestamp").sort_index()
            return df

        except Exception as e:
            logger.warning("DeFiLlama DEX volume fetch failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Summary snapshot for live monitoring
    # ------------------------------------------------------------------

    def fetch_defi_snapshot(self) -> dict:
        """Get a quick DeFi health snapshot."""
        snapshot = {
            "global_tvl": None,
            "tvl_24h_change_pct": None,
            "stablecoin_total": None,
            "stablecoin_7d_change_pct": None,
        }

        try:
            # Current chain TVL
            resp = self._client.get(f"{LLAMA_BASE}/v2/chains")
            resp.raise_for_status()
            chains = resp.json()
            total_tvl = sum(c.get("tvl", 0) for c in chains if isinstance(c, dict))
            snapshot["global_tvl"] = round(total_tvl, 0)
        except Exception:
            pass

        try:
            tvl_hist = self.fetch_global_tvl_history()
            if len(tvl_hist) >= 2:
                latest = tvl_hist["tvl"].iloc[-1]
                prev = tvl_hist["tvl"].iloc[-2]
                if prev > 0:
                    snapshot["tvl_24h_change_pct"] = round(
                        ((latest - prev) / prev) * 100, 2
                    )
        except Exception:
            pass

        return snapshot

    def health_check(self) -> dict:
        try:
            resp = self._client.get(f"{LLAMA_BASE}/v2/chains", timeout=10)
            ok = resp.status_code == 200
            return {"status": "ok" if ok else "error", "connector": "DeFiLlamaConnector"}
        except Exception as e:
            return {"status": "error", "connector": "DeFiLlamaConnector", "error": str(e)}

    def close(self):
        self._client.close()
