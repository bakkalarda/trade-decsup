"""Options data connector — Deribit for crypto, yfinance for traditional.

Provides aggregate options metrics:
  - Put/Call volume ratio
  - ATM implied volatility
  - IV skew (OTM put IV - OTM call IV)
  - Max pain level
  - DVOL (Deribit Volatility Index) history
"""

from __future__ import annotations

import logging
import os
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

DERIBIT_BASE = "https://www.deribit.com/api/v2/public"

DEFAULT_DERIBIT_OVERRIDE_IPS = ("104.18.4.240", "104.18.5.240")


def _make_client(timeout: int = 25) -> httpx.Client:
    """Create an httpx client with relaxed SSL for Deribit.

    Deribit's certificate chain sometimes causes verification failures
    in some environments.  We disable SSL verification entirely and use
    a generous timeout with retries via httpx transport.
    """
    transport = httpx.HTTPTransport(retries=0)
    timeout_cfg = httpx.Timeout(connect=8.0, read=float(timeout), write=8.0, pool=8.0)
    return httpx.Client(timeout=timeout_cfg, verify=False, transport=transport)


class DeribitOptionsConnector:
    """Fetch crypto options data from Deribit (BTC, ETH — public API, no key).

    Endpoints used (all public, no auth):
      - get_book_summary_by_currency  — live P/C, OI, IV
      - get_index_price               — spot/index price
      - get_volatility_index_data     — DVOL historical data
      - get_funding_chart_data        — funding rate chart
    """

    SUPPORTED = ("BTC", "ETH")

    def __init__(self):
        self._client = _make_client(timeout=25)
        self._override_ips = self._load_override_ips()

    def _load_override_ips(self) -> tuple[str, ...]:
        """Load optional Deribit DNS override IPs.

        Users can provide comma-separated values via DSS_DERIBIT_IP_OVERRIDE.
        Example: DSS_DERIBIT_IP_OVERRIDE=104.18.4.240,104.18.5.240
        """
        raw = os.environ.get("DSS_DERIBIT_IP_OVERRIDE", "").strip()
        if raw:
            ips = tuple(x.strip() for x in raw.split(",") if x.strip())
            if ips:
                return ips
        return DEFAULT_DERIBIT_OVERRIDE_IPS

    @contextmanager
    def _override_deribit_dns(self, ip: str):
        """Temporarily override DNS resolution for Deribit hosts to a known IP."""
        original_getaddrinfo = socket.getaddrinfo
        deribit_hosts = {"www.deribit.com", "test.deribit.com", "deribit.com"}

        def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            if host in deribit_hosts:
                return original_getaddrinfo(ip, port, family, type, proto, flags)
            return original_getaddrinfo(host, port, family, type, proto, flags)

        socket.getaddrinfo = patched_getaddrinfo
        try:
            yield
        finally:
            socket.getaddrinfo = original_getaddrinfo

    def _deribit_get(self, path: str, params: dict | None = None) -> httpx.Response:
        """GET with retry + DNS override fallback for Deribit connectivity issues."""
        url = f"{DERIBIT_BASE}/{path}"

        # First try normal resolver path
        try:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            return resp
        except Exception as first_err:
            last_err = first_err

        # Fallback: override DNS to known Cloudflare IPs
        for ip in self._override_ips:
            try:
                with self._override_deribit_dns(ip):
                    resp = self._client.get(url, params=params)
                resp.raise_for_status()
                return resp
            except Exception as e:
                last_err = e

        raise last_err

    # ------------------------------------------------------------------
    # Live snapshot
    # ------------------------------------------------------------------

    def fetch_options_summary(self, currency: str = "BTC") -> dict:
        """Fetch aggregate options metrics for a currency.

        Returns dict with: pc_ratio, atm_iv, iv_skew, max_pain, spot_price.
        """
        currency = currency.upper()
        if currency not in self.SUPPORTED:
            return self._empty_result()

        try:
            resp = self._deribit_get(
                "get_book_summary_by_currency",
                params={"currency": currency, "kind": "option"},
            )
            data = resp.json().get("result", [])

            if not data:
                return self._empty_result()

            spot_resp = self._deribit_get(
                "get_index_price",
                params={"index_name": f"{currency.lower()}_usd"},
            )
            spot_price = spot_resp.json().get("result", {}).get("index_price", 0)

            return self._compute_metrics(data, spot_price, currency)

        except Exception as e:
            logger.warning("Deribit options snapshot failed for %s: %s", currency, e)
            return self._empty_result()

    # ------------------------------------------------------------------
    # Historical DVOL (Deribit Volatility Index)
    # ------------------------------------------------------------------

    def fetch_options_history(
        self, currency: str = "BTC", days: int = 90
    ) -> pd.DataFrame:
        """Fetch historical IV data using the DVOL index.

        The DVOL index provides a 30-day forward-looking IV reading,
        analogous to VIX for equities. Available for BTC and ETH.
        """
        currency = currency.upper()
        if currency not in self.SUPPORTED:
            return pd.DataFrame()

        try:
            now = datetime.now(timezone.utc)
            start_dt = now - timedelta(days=days)

            chunk_days = 120
            chunks: list[tuple[datetime, datetime]] = []
            cursor = start_dt
            while cursor < now:
                chunk_end = min(cursor + timedelta(days=chunk_days), now)
                chunks.append((cursor, chunk_end))
                cursor = chunk_end

            records: list[dict] = []
            for chunk_start, chunk_end in chunks:
                start_ts = int(chunk_start.timestamp() * 1000)
                end_ts = int(chunk_end.timestamp() * 1000)

                data = self._fetch_dvol_chunk(currency, start_ts, end_ts)
                if not data:
                    continue

                for point in data:
                    if len(point) < 5:
                        continue
                    records.append({
                        "timestamp": pd.Timestamp(point[0], unit="ms", tz="UTC"),
                        "iv_open": point[1],
                        "iv_high": point[2],
                        "iv_low": point[3],
                        "iv_close": point[4],
                    })

            if not records:
                return pd.DataFrame()

            df = pd.DataFrame(records).drop_duplicates(subset=["timestamp"]).set_index("timestamp").sort_index()
            return df

        except Exception as e:
            logger.warning("Deribit IV history failed for %s: %s", currency, e)
            return pd.DataFrame()

    def _fetch_dvol_chunk(self, currency: str, start_ts: int, end_ts: int) -> list:
        """Fetch one DVOL chunk with retries to reduce transient timeout failures."""
        for attempt in range(1, 4):
            try:
                resp = self._client.get(
                    f"{DERIBIT_BASE}/get_volatility_index_data",
                    params={
                        "currency": currency,
                        "start_timestamp": start_ts,
                        "end_timestamp": end_ts,
                        "resolution": "1D",
                    },
                )
                resp.raise_for_status()
                return resp.json().get("result", {}).get("data", [])
            except Exception as e:
                # DNS override fallback within attempt
                dns_ok = False
                for ip in self._override_ips:
                    try:
                        with self._override_deribit_dns(ip):
                            resp = self._client.get(
                                f"{DERIBIT_BASE}/get_volatility_index_data",
                                params={
                                    "currency": currency,
                                    "start_timestamp": start_ts,
                                    "end_timestamp": end_ts,
                                    "resolution": "1D",
                                },
                            )
                        resp.raise_for_status()
                        dns_ok = True
                        return resp.json().get("result", {}).get("data", [])
                    except Exception:
                        continue

                if attempt == 3 and not dns_ok:
                    logger.warning(
                        "Deribit DVOL chunk failed for %s (%s..%s): %s",
                        currency,
                        start_ts,
                        end_ts,
                        e,
                    )
                    return []
                time.sleep(0.5 * attempt)
        return []

    # ------------------------------------------------------------------
    # Deribit funding rates (alternative to ccxt for Deribit perps)
    # ------------------------------------------------------------------

    def fetch_funding_chart(
        self, currency: str = "BTC", length: str = "8h"
    ) -> pd.DataFrame:
        """Fetch funding rate chart data from Deribit."""
        currency = currency.upper()
        if currency not in self.SUPPORTED:
            return pd.DataFrame()

        instrument = f"{currency}-PERPETUAL"
        try:
            resp = self._deribit_get(
                "get_funding_chart_data",
                params={"instrument_name": instrument, "length": length},
            )
            data = resp.json().get("result", {})
            interest = data.get("interest_8h", data.get("interest_1h", 0))
            current_rate = data.get("current_interest", 0)
            return {
                "instrument": instrument,
                "funding_8h": interest,
                "current_rate": current_rate,
            }
        except Exception as e:
            logger.warning("Deribit funding chart failed: %s", e)
            return {}

    # ------------------------------------------------------------------
    # Metrics computation
    # ------------------------------------------------------------------

    def _compute_metrics(
        self, instruments: list[dict], spot_price: float, currency: str
    ) -> dict:
        """Compute aggregate options metrics from instrument summaries."""
        result = self._empty_result()
        result["spot_price"] = spot_price

        if spot_price <= 0:
            return result

        total_call_volume = 0.0
        total_put_volume = 0.0
        total_call_oi = 0.0
        total_put_oi = 0.0

        strike_pain: dict[float, float] = {}
        atm_ivs: list[float] = []
        otm_put_ivs: list[float] = []
        otm_call_ivs: list[float] = []

        for inst in instruments:
            name = inst.get("instrument_name", "")
            volume = inst.get("volume", 0) or 0
            oi = inst.get("open_interest", 0) or 0
            mark_iv = inst.get("mark_iv", 0) or 0

            parts = name.split("-")
            if len(parts) < 4:
                continue

            try:
                strike = float(parts[2])
            except (ValueError, IndexError):
                continue

            is_call = parts[3] == "C"
            is_put = parts[3] == "P"
            moneyness = (strike - spot_price) / spot_price

            if is_call:
                total_call_volume += volume
                total_call_oi += oi
            elif is_put:
                total_put_volume += volume
                total_put_oi += oi

            if strike not in strike_pain:
                strike_pain[strike] = 0.0
            if is_call:
                strike_pain[strike] += oi * max(0, spot_price - strike)
            elif is_put:
                strike_pain[strike] += oi * max(0, strike - spot_price)

            if mark_iv > 0:
                if abs(moneyness) < 0.05:
                    atm_ivs.append(mark_iv)
                elif is_put and moneyness < -0.05:
                    otm_put_ivs.append(mark_iv)
                elif is_call and moneyness > 0.05:
                    otm_call_ivs.append(mark_iv)

        if total_call_volume > 0:
            result["pc_ratio"] = round(total_put_volume / total_call_volume, 4)
        if total_call_oi > 0:
            result["pc_oi_ratio"] = round(total_put_oi / total_call_oi, 4)
        if atm_ivs:
            result["atm_iv"] = round(sum(atm_ivs) / len(atm_ivs), 4)
        if otm_put_ivs and otm_call_ivs:
            result["iv_skew"] = round(
                sum(otm_put_ivs) / len(otm_put_ivs) - sum(otm_call_ivs) / len(otm_call_ivs),
                4,
            )
        if strike_pain:
            max_pain_strike = min(strike_pain, key=strike_pain.get)
            result["max_pain"] = max_pain_strike
            result["max_pain_distance_pct"] = round(
                ((spot_price - max_pain_strike) / spot_price) * 100, 2
            )

        return result

    def _empty_result(self) -> dict:
        return {
            "pc_ratio": None,
            "pc_oi_ratio": None,
            "atm_iv": None,
            "iv_skew": None,
            "max_pain": None,
            "max_pain_distance_pct": None,
            "spot_price": None,
        }

    def health_check(self) -> dict:
        try:
            result = self.fetch_options_summary("BTC")
            ok = result.get("pc_ratio") is not None
            return {"status": "ok" if ok else "error", "connector": "DeribitOptionsConnector"}
        except Exception as e:
            return {"status": "error", "connector": "DeribitOptionsConnector", "error": str(e)}

    def close(self):
        self._client.close()


class TraditionalOptionsConnector:
    """Fetch options data for traditional assets via yfinance (GLD, SLV proxies)."""

    _TICKER_MAP = {
        "XAU": "GLD",
        "XAG": "SLV",
    }

    def fetch_options_summary(self, asset: str) -> dict:
        """Fetch aggregate options metrics for a traditional asset."""
        ticker_sym = self._TICKER_MAP.get(asset.upper())
        if not ticker_sym:
            return self._empty_result()

        try:
            import yfinance as yf

            ticker = yf.Ticker(ticker_sym)
            dates = ticker.options
            if not dates:
                return self._empty_result()

            chain = ticker.option_chain(dates[0])
            calls = chain.calls
            puts = chain.puts

            spot = ticker.info.get("regularMarketPrice") or ticker.info.get(
                "previousClose", 0
            )
            if spot <= 0:
                return self._empty_result()

            result = self._empty_result()
            result["spot_price"] = spot

            call_vol = calls["volume"].sum() if "volume" in calls.columns else 0
            put_vol = puts["volume"].sum() if "volume" in puts.columns else 0
            if call_vol > 0:
                result["pc_ratio"] = round(put_vol / call_vol, 4)

            call_oi = (
                calls["openInterest"].sum() if "openInterest" in calls.columns else 0
            )
            put_oi = (
                puts["openInterest"].sum() if "openInterest" in puts.columns else 0
            )
            if call_oi > 0:
                result["pc_oi_ratio"] = round(put_oi / call_oi, 4)

            if "impliedVolatility" in calls.columns and "strike" in calls.columns:
                calls_sorted = calls.copy()
                calls_sorted["dist"] = (calls_sorted["strike"] - spot).abs()
                atm_call = calls_sorted.nsmallest(3, "dist")
                atm_iv_vals = atm_call["impliedVolatility"].dropna()
                if not atm_iv_vals.empty:
                    result["atm_iv"] = round(atm_iv_vals.mean() * 100, 2)

            if (
                "impliedVolatility" in puts.columns
                and "impliedVolatility" in calls.columns
            ):
                otm_puts = puts[puts["strike"] < spot * 0.95][
                    "impliedVolatility"
                ].dropna()
                otm_calls = calls[calls["strike"] > spot * 1.05][
                    "impliedVolatility"
                ].dropna()
                if not otm_puts.empty and not otm_calls.empty:
                    result["iv_skew"] = round(
                        (otm_puts.mean() - otm_calls.mean()) * 100, 2
                    )

            return result

        except Exception as e:
            logger.warning("Traditional options fetch failed for %s: %s", asset, e)
            return self._empty_result()

    def _empty_result(self) -> dict:
        return {
            "pc_ratio": None,
            "pc_oi_ratio": None,
            "atm_iv": None,
            "iv_skew": None,
            "max_pain": None,
            "max_pain_distance_pct": None,
            "spot_price": None,
        }
