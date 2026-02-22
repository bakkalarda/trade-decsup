"""On-chain data connector — Blockchain.com + CoinGecko (free, no API key).

Blockchain.com charts API:
  - Exchange balances (BTC)
  - Hashrate, difficulty
  - Transaction count, mempool
  - Miner revenue

CoinGecko (free demo tier, generous limits):
  - Market dominance, global market cap
  - Trending coins
  - Exchange volumes

Together these provide on-chain flow signals for the flow gate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

BLOCKCHAIN_INFO_BASE = "https://api.blockchain.info"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class OnchainConnector:
    """Fetch on-chain metrics from free public APIs.

    Sources:
      - blockchain.info: BTC exchange balances, hashrate, tx count
      - CoinGecko: global market data, BTC dominance
    """

    def __init__(self):
        self._client = httpx.Client(timeout=20)

    # ------------------------------------------------------------------
    # Blockchain.com — BTC on-chain
    # ------------------------------------------------------------------

    def fetch_btc_onchain_flow(self, timespan: str = "90days") -> Optional[dict]:
        """Fetch BTC on-chain flow indicators.

        Uses:
          - estimated-transaction-volume-usd: on-chain USD volume
          - output-volume: total BTC moved on-chain
          - trade-volume: exchange trading volume

        Rising on-chain volume + falling trade volume = OTC accumulation (bullish).
        Rising trade volume = exchange activity (neutral/bearish if coupled with selling).
        """
        try:
            tx_vol = self._fetch_blockchain_chart(
                "estimated-transaction-volume-usd", timespan
            )
            trade_vol = self._fetch_blockchain_chart("trade-volume", timespan)

            if tx_vol.empty and trade_vol.empty:
                return None

            result: dict = {"asset": "BTC"}

            if not tx_vol.empty and len(tx_vol) >= 7:
                latest_tx = tx_vol["value"].iloc[-1]
                prev_7d_tx = tx_vol["value"].iloc[-7]
                result["tx_volume_usd_latest"] = latest_tx
                result["tx_volume_7d_change_pct"] = round(
                    (latest_tx / prev_7d_tx - 1) * 100, 2
                ) if prev_7d_tx > 0 else 0
                # Rising on-chain volume = more activity
                result["tx_volume_trend"] = (
                    "rising" if latest_tx > prev_7d_tx * 1.1
                    else "falling" if latest_tx < prev_7d_tx * 0.9
                    else "stable"
                )

            if not trade_vol.empty and len(trade_vol) >= 7:
                latest_tv = trade_vol["value"].iloc[-1]
                prev_7d_tv = trade_vol["value"].iloc[-7]
                result["trade_volume_latest"] = latest_tv
                result["trade_volume_7d_change_pct"] = round(
                    (latest_tv / prev_7d_tv - 1) * 100, 2
                ) if prev_7d_tv > 0 else 0
                result["trade_volume_trend"] = (
                    "rising" if latest_tv > prev_7d_tv * 1.1
                    else "falling" if latest_tv < prev_7d_tv * 0.9
                    else "stable"
                )

            # Derive on-chain flow signal
            tx_trend = result.get("tx_volume_trend", "stable")
            trade_trend = result.get("trade_volume_trend", "stable")
            if tx_trend == "rising" and trade_trend == "falling":
                result["flow_signal"] = "accumulation"  # OTC buying
            elif tx_trend == "falling" and trade_trend == "rising":
                result["flow_signal"] = "distribution"  # Exchange selling
            else:
                result["flow_signal"] = "neutral"

            result["data_points"] = max(len(tx_vol), len(trade_vol))
            return result

        except Exception as e:
            logger.warning("Failed to fetch BTC on-chain flow: %s", e)
            return None

    def fetch_btc_onchain_flow_history(self, timespan: str = "2years") -> pd.DataFrame:
        """Fetch BTC on-chain flow metrics as a time series for backtesting.

        Combines estimated transaction volume (USD) with trade volume to
        create a flow ratio.
        """
        try:
            tx_vol = self._fetch_blockchain_chart(
                "estimated-transaction-volume-usd", timespan
            )
            trade_vol = self._fetch_blockchain_chart("trade-volume", timespan)

            if tx_vol.empty:
                return pd.DataFrame()

            # Rename for clarity
            tx_vol = tx_vol.rename(columns={"value": "tx_volume_usd"})

            if not trade_vol.empty:
                trade_vol = trade_vol.rename(columns={"value": "trade_volume"})
                # Merge on nearest timestamp
                combined = tx_vol.join(trade_vol, how="left").ffill()
                # Flow ratio: on-chain tx volume / exchange trade volume
                # Higher = more on-chain activity relative to exchange (accumulation signal)
                combined["flow_ratio"] = combined["tx_volume_usd"] / (
                    combined["trade_volume"].replace(0, 1)
                )
            else:
                combined = tx_vol
                combined["trade_volume"] = 0.0
                combined["flow_ratio"] = 1.0

            return combined

        except Exception as e:
            logger.warning("BTC on-chain flow history failed: %s", e)
            return pd.DataFrame()

    def fetch_btc_hashrate(self, timespan: str = "180days") -> pd.DataFrame:
        """Fetch BTC hashrate history (miner health indicator)."""
        return self._fetch_blockchain_chart("hash-rate", timespan)

    def fetch_btc_tx_count(self, timespan: str = "90days") -> pd.DataFrame:
        """Fetch BTC daily transaction count (network activity)."""
        return self._fetch_blockchain_chart("n-transactions", timespan)

    def fetch_btc_mempool_size(self, timespan: str = "30days") -> pd.DataFrame:
        """Fetch BTC mempool size (congestion proxy)."""
        return self._fetch_blockchain_chart("mempool-size", timespan)

    def _fetch_blockchain_chart(
        self, chart_name: str, timespan: str
    ) -> pd.DataFrame:
        """Generic blockchain.info chart fetcher."""
        try:
            url = f"{BLOCKCHAIN_INFO_BASE}/charts/{chart_name}"
            params = {"timespan": timespan, "format": "json"}
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            values = resp.json().get("values", [])
            if not values:
                return pd.DataFrame()

            records = [
                {
                    "timestamp": pd.Timestamp(v["x"], unit="s", tz="UTC"),
                    "value": float(v["y"]),
                }
                for v in values
            ]
            return pd.DataFrame(records).set_index("timestamp").sort_index()

        except Exception as e:
            logger.warning("Blockchain chart %s failed: %s", chart_name, e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # CoinGecko — global market data
    # ------------------------------------------------------------------

    def fetch_global_market(self) -> Optional[dict]:
        """Fetch global crypto market metrics from CoinGecko.

        Returns: total_market_cap, btc_dominance, eth_dominance,
                 total_volume_24h, market_cap_change_24h_pct.
        """
        try:
            resp = self._client.get(f"{COINGECKO_BASE}/global")
            resp.raise_for_status()
            data = resp.json().get("data", {})

            total_mcap = data.get("total_market_cap", {}).get("usd", 0)
            total_vol = data.get("total_volume", {}).get("usd", 0)
            mcap_change = data.get("market_cap_change_percentage_24h_usd", 0)
            btc_dom = data.get("market_cap_percentage", {}).get("btc", 0)
            eth_dom = data.get("market_cap_percentage", {}).get("eth", 0)
            active_cryptos = data.get("active_cryptocurrencies", 0)

            return {
                "total_market_cap_usd": total_mcap,
                "total_volume_24h_usd": total_vol,
                "market_cap_change_24h_pct": round(mcap_change, 2),
                "btc_dominance_pct": round(btc_dom, 2),
                "eth_dominance_pct": round(eth_dom, 2),
                "active_cryptocurrencies": active_cryptos,
            }

        except Exception as e:
            logger.warning("CoinGecko global market fetch failed: %s", e)
            return None

    def fetch_btc_market_chart(self, days: int = 365) -> pd.DataFrame:
        """Fetch BTC market cap and volume history from CoinGecko.

        Useful as a flow proxy: market cap growth vs volume.
        """
        try:
            resp = self._client.get(
                f"{COINGECKO_BASE}/coins/bitcoin/market_chart",
                params={"vs_currency": "usd", "days": days, "interval": "daily"},
            )
            resp.raise_for_status()
            data = resp.json()

            prices = data.get("prices", [])
            mcaps = data.get("market_caps", [])
            volumes = data.get("total_volumes", [])

            if not prices:
                return pd.DataFrame()

            records = []
            for i, (ts, price) in enumerate(prices):
                rec = {
                    "timestamp": pd.Timestamp(ts, unit="ms", tz="UTC"),
                    "price": price,
                }
                if i < len(mcaps):
                    rec["market_cap"] = mcaps[i][1]
                if i < len(volumes):
                    rec["volume"] = volumes[i][1]
                records.append(rec)

            df = pd.DataFrame(records).set_index("timestamp").sort_index()
            return df

        except Exception as e:
            logger.warning("CoinGecko BTC market chart failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # NVT Signal — Network Value to Transactions (smoothed)
    # ------------------------------------------------------------------

    def fetch_nvt_signal(self, timespan: str = "2years") -> pd.DataFrame:
        """Compute NVT Signal = Market Cap / 90d_MA(daily_tx_volume_usd).

        Per the article: NVT Signal smooths daily tx volume with a 90-day
        moving average, filtering out short-term noise that plagues raw NVT.

        Interpretation:
          - NVT Signal > 150  → network overvalued (bubble territory)
          - NVT Signal 50-150 → fair value zone
          - NVT Signal < 50   → network undervalued (accumulation zone)

        Requires: Blockchain.com tx volume + CoinGecko market cap history.
        """
        try:
            # Fetch daily transaction volume from Blockchain.com
            tx_vol = self._fetch_blockchain_chart(
                "estimated-transaction-volume-usd", timespan
            )
            if tx_vol.empty or len(tx_vol) < 91:
                logger.warning("NVT Signal: insufficient tx volume data (%d rows)", len(tx_vol))
                return pd.DataFrame()

            # Fetch BTC market cap history from CoinGecko
            days = {"1year": 365, "2years": 730, "3years": 1095, "180days": 180}.get(timespan, 730)
            mcap_df = self.fetch_btc_market_chart(days=days)
            if mcap_df.empty or "market_cap" not in mcap_df.columns:
                logger.warning("NVT Signal: no market cap data from CoinGecko")
                return pd.DataFrame()

            # Align both to daily
            tx_daily = tx_vol.rename(columns={"value": "tx_volume_usd"}).resample("1D").last().ffill()
            mcap_daily = mcap_df[["market_cap"]].resample("1D").last().ffill()

            # Join on date
            combined = mcap_daily.join(tx_daily, how="inner")
            if combined.empty or len(combined) < 91:
                return pd.DataFrame()

            # 90-day moving average of tx volume (the "Signal" smoothing)
            combined["tx_vol_90d_ma"] = combined["tx_volume_usd"].rolling(90, min_periods=60).mean()

            # NVT Signal = market_cap / 90d_MA(tx_volume)
            combined["nvt_signal"] = combined["market_cap"] / combined["tx_vol_90d_ma"].replace(0, float("nan"))

            # Also compute raw NVT Ratio for reference
            combined["nvt_ratio"] = combined["market_cap"] / combined["tx_volume_usd"].replace(0, float("nan"))

            # Classify zone
            combined["nvt_zone"] = "fair_value"
            combined.loc[combined["nvt_signal"] > 150, "nvt_zone"] = "overvalued"
            combined.loc[combined["nvt_signal"] < 50, "nvt_zone"] = "undervalued"

            logger.info(
                "NVT Signal computed: %d rows, latest=%.1f (%s)",
                len(combined.dropna(subset=["nvt_signal"])),
                combined["nvt_signal"].dropna().iloc[-1] if not combined["nvt_signal"].dropna().empty else 0,
                combined["nvt_zone"].iloc[-1] if not combined.empty else "?",
            )

            return combined[["market_cap", "tx_volume_usd", "tx_vol_90d_ma", "nvt_signal", "nvt_ratio", "nvt_zone"]].dropna(subset=["nvt_signal"])

        except Exception as e:
            logger.warning("NVT Signal computation failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Hash Ribbons — miner capitulation / recovery detector
    # ------------------------------------------------------------------

    def fetch_hash_ribbons(self, timespan: str = "2years") -> pd.DataFrame:
        """Compute Hash Ribbons = 30d SMA vs 60d SMA of hashrate.

        Per the article: Hash Ribbons detect miner capitulation when the
        short-term (30d) hashrate SMA drops below the long-term (60d) SMA.
        Recovery (buy signal) occurs when 30d crosses back above 60d.

        Signal logic:
          - 30d < 60d  → CAPITULATION (miners unprofitable, selling BTC)
          - 30d > 60d  → RECOVERY (miners healthy, accumulation zone)
          - Cross from below to above = strong BUY signal (historically ~95% hit rate)

        Uses: Blockchain.com hashrate (free, no key).
        """
        try:
            hashrate = self.fetch_btc_hashrate(timespan=timespan)
            if hashrate.empty or len(hashrate) < 61:
                logger.warning("Hash Ribbons: insufficient hashrate data (%d rows)", len(hashrate))
                return pd.DataFrame()

            df = hashrate.rename(columns={"value": "hashrate"})

            # Compute 30d and 60d SMAs
            df["hash_sma_30d"] = df["hashrate"].rolling(30, min_periods=20).mean()
            df["hash_sma_60d"] = df["hashrate"].rolling(60, min_periods=40).mean()

            # Ribbon signal
            df["capitulation"] = df["hash_sma_30d"] < df["hash_sma_60d"]

            # Detect crossover (recovery buy signal)
            prev_cap = df["capitulation"].shift(1)
            df["recovery_cross"] = (~df["capitulation"]) & (prev_cap == True)

            # Classify state
            df["hash_ribbon_state"] = "healthy"
            df.loc[df["capitulation"], "hash_ribbon_state"] = "capitulation"
            df.loc[df["recovery_cross"], "hash_ribbon_state"] = "recovery_buy"

            # Hashrate momentum: 30d rate-of-change
            df["hash_roc_30d"] = df["hashrate"].pct_change(30) * 100

            logger.info(
                "Hash Ribbons: %d rows, current=%s, 30dROC=%.1f%%",
                len(df.dropna(subset=["hash_sma_30d"])),
                df["hash_ribbon_state"].iloc[-1] if not df.empty else "?",
                df["hash_roc_30d"].iloc[-1] if not df["hash_roc_30d"].dropna().empty else 0,
            )

            return df[["hashrate", "hash_sma_30d", "hash_sma_60d", "capitulation", "recovery_cross", "hash_ribbon_state", "hash_roc_30d"]].dropna(subset=["hash_sma_30d"])

        except Exception as e:
            logger.warning("Hash Ribbons computation failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Miner Revenue — miner economics proxy
    # ------------------------------------------------------------------

    def fetch_miner_revenue(self, timespan: str = "2years") -> pd.DataFrame:
        """Fetch BTC miner revenue history (block reward + fees).

        Per the article: Miner economics are a key leading indicator.
        Declining miner revenue → potential sell pressure (miners liquidate holdings).
        Post-halving revenue drops → capitulation periods.

        Uses: Blockchain.com 'miners-revenue' chart (free).
        """
        try:
            df = self._fetch_blockchain_chart("miners-revenue", timespan)
            if df.empty:
                return pd.DataFrame()

            df = df.rename(columns={"value": "miner_revenue_usd"})

            # 30d and 90d moving averages for trend
            df["miner_rev_30d_ma"] = df["miner_revenue_usd"].rolling(30, min_periods=15).mean()
            df["miner_rev_90d_ma"] = df["miner_revenue_usd"].rolling(90, min_periods=60).mean()

            # Miner stress: current revenue below 90d MA = potential sell pressure
            df["miner_stress"] = df["miner_revenue_usd"] < df["miner_rev_90d_ma"]

            # Revenue momentum
            df["miner_rev_roc_30d"] = df["miner_revenue_usd"].pct_change(30) * 100

            logger.info(
                "Miner revenue: %d rows, latest=$%.0f, stress=%s",
                len(df),
                df["miner_revenue_usd"].iloc[-1] if not df.empty else 0,
                df["miner_stress"].iloc[-1] if not df.empty else "?",
            )

            return df.dropna(subset=["miner_rev_30d_ma"])

        except Exception as e:
            logger.warning("Miner revenue fetch failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Stablecoin Supply Ratio (SSR) — requires cross-source computation
    # ------------------------------------------------------------------

    def fetch_ssr(self, days: int = 730) -> pd.DataFrame:
        """Compute Stablecoin Supply Ratio (SSR) = BTC Market Cap / Stablecoin Supply.

        Per the article: SSR measures the relative purchasing power of
        stablecoins against BTC. Low SSR = stablecoins have high buying
        power relative to BTC (bullish potential/dry powder). High SSR =
        BTC is large relative to stablecoin supply (less buying power).

        Interpretation:
          - SSR < 5   → massive stablecoin dry powder (very bullish)
          - SSR 5-15  → healthy ratio
          - SSR > 15  → limited buying power from stablecoins

        Requires: CoinGecko BTC market cap + DeFiLlama stablecoin supply.
        """
        try:
            from dss.connectors.defillama import DeFiLlamaConnector

            # BTC market cap history from CoinGecko
            mcap_df = self.fetch_btc_market_chart(days=days)
            if mcap_df.empty or "market_cap" not in mcap_df.columns:
                logger.warning("SSR: no market cap data")
                return pd.DataFrame()

            # Stablecoin supply history from DeFiLlama
            dl = DeFiLlamaConnector()
            stable_df = dl.fetch_stablecoin_supply_history()
            dl.close()

            if stable_df.empty or "total_supply" not in stable_df.columns:
                logger.warning("SSR: no stablecoin supply data")
                return pd.DataFrame()

            # Resample both to daily
            mcap_daily = mcap_df[["market_cap"]].resample("1D").last().ffill()
            stable_daily = stable_df[["total_supply"]].resample("1D").last().ffill()

            # Join on date
            combined = mcap_daily.join(stable_daily, how="inner")
            if combined.empty:
                return pd.DataFrame()

            # SSR = BTC market cap / total stablecoin supply
            combined["ssr"] = combined["market_cap"] / combined["total_supply"].replace(0, float("nan"))

            # SSR 14d moving average for smoother signal
            combined["ssr_14d_ma"] = combined["ssr"].rolling(14, min_periods=7).mean()

            # Classify zone
            combined["ssr_zone"] = "normal"
            combined.loc[combined["ssr"] < 5, "ssr_zone"] = "high_buying_power"
            combined.loc[combined["ssr"] > 15, "ssr_zone"] = "low_buying_power"

            # SSR rate of change (momentum of buying power shift)
            combined["ssr_roc_14d"] = combined["ssr"].pct_change(14) * 100

            logger.info(
                "SSR computed: %d rows, latest=%.1f (%s)",
                len(combined.dropna(subset=["ssr"])),
                combined["ssr"].dropna().iloc[-1] if not combined["ssr"].dropna().empty else 0,
                combined["ssr_zone"].iloc[-1] if not combined.empty else "?",
            )

            return combined[["market_cap", "total_supply", "ssr", "ssr_14d_ma", "ssr_zone", "ssr_roc_14d"]].dropna(subset=["ssr"])

        except Exception as e:
            logger.warning("SSR computation failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Unified flow proxy
    # ------------------------------------------------------------------

    def fetch_exchange_flow_proxy(self, asset: str) -> Optional[dict]:
        """Exchange flow proxy for any supported asset.

        BTC: uses blockchain.info on-chain transaction/trade volume.
        Others: uses CoinGecko global metrics as indirect proxy.
        """
        if asset.upper() == "BTC":
            return self.fetch_btc_onchain_flow()

        # For non-BTC assets, use global market data as a proxy
        global_data = self.fetch_global_market()
        if global_data:
            return {
                "asset": asset,
                "proxy_type": "global_market",
                "total_market_cap_usd": global_data["total_market_cap_usd"],
                "market_cap_change_24h_pct": global_data["market_cap_change_24h_pct"],
                "btc_dominance_pct": global_data["btc_dominance_pct"],
            }

        return None

    def health_check(self) -> dict:
        results = {}
        try:
            resp = self._client.get(
                f"{BLOCKCHAIN_INFO_BASE}/q/getblockcount", timeout=5
            )
            results["blockchain_info"] = "ok" if resp.status_code == 200 else "error"
        except Exception as e:
            results["blockchain_info"] = f"error: {e}"

        try:
            resp = self._client.get(f"{COINGECKO_BASE}/ping", timeout=5)
            results["coingecko"] = "ok" if resp.status_code == 200 else "error"
        except Exception as e:
            results["coingecko"] = f"error: {e}"

        overall = "ok" if all(v == "ok" for v in results.values()) else "partial"
        return {"status": overall, "connector": "OnchainConnector", "details": results}

    def close(self):
        self._client.close()
