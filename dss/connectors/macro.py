"""Macro data connectors — DXY, real yields, SPX, VIX, Gold, credit, MOVE, FRED.

Sources:
  - yfinance: DXY (DX-Y.NYB), SPX (^GSPC), VIX (^VIX), Gold (GC=F),
              MOVE (^MOVE), HYG, IEF (credit spread proxy), Treasury ETFs
  - FRED (free API key): DFII10 (10Y TIPS yield), DGS10, DTWEXBGS, T10YIE, DFF
  - Fallbacks for every series if primary source fails
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import pandas as pd

from dss.connectors.price import TraditionalConnector

logger = logging.getLogger(__name__)


class MacroConnector:
    """Fetch macro proxy data (DXY, real yields, SPX, VIX).

    Each method has a primary source and a fallback, so the system
    degrades gracefully when one API is down.
    """

    # FRED series IDs
    FRED_REAL_YIELDS = "DFII10"       # 10-year TIPS yield
    FRED_10Y_NOMINAL = "DGS10"         # 10-year Treasury yield
    FRED_TRADE_WEIGHTED_USD = "DTWEXBGS"  # Trade-weighted USD (broad)
    FRED_BREAKEVEN_INFL = "T10YIE"     # 10-year breakeven inflation
    FRED_FED_FUNDS = "DFF"             # Effective federal funds rate

    def __init__(self):
        self._trad = TraditionalConnector()
        self._fred_key = os.environ.get("FRED_API_KEY")
        self._http = httpx.Client(timeout=20)

    # ------------------------------------------------------------------
    # DXY (US Dollar Index)
    # ------------------------------------------------------------------

    def fetch_dxy(self, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
        """Fetch DXY — primary: yfinance, fallback: FRED trade-weighted USD."""
        try:
            df = self._trad.fetch_ohlcv("DX-Y.NYB", timeframe, limit=limit)
            if not df.empty:
                return df
        except Exception as e:
            logger.warning("yfinance DXY failed: %s — trying FRED fallback", e)

        # Fallback: FRED trade-weighted USD index
        if self._fred_key:
            logger.info("Using FRED DTWEXBGS as DXY proxy")
            return self._fetch_fred_series(self.FRED_TRADE_WEIGHTED_USD, limit)

        # Last resort: UUP ETF (USD Bull ETF)
        try:
            logger.info("Using UUP ETF as DXY proxy")
            return self._trad.fetch_ohlcv("UUP", timeframe, limit=limit)
        except Exception:
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # SPX (S&P 500)
    # ------------------------------------------------------------------

    def fetch_spx(self, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
        """Fetch S&P 500 — primary: yfinance ^GSPC, fallback: SPY ETF."""
        try:
            df = self._trad.fetch_ohlcv("^GSPC", timeframe, limit=limit)
            if not df.empty:
                return df
        except Exception as e:
            logger.warning("yfinance ^GSPC failed: %s — trying SPY", e)

        try:
            return self._trad.fetch_ohlcv("SPY", timeframe, limit=limit)
        except Exception:
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # VIX (Volatility Index)
    # ------------------------------------------------------------------

    def fetch_vix(self, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
        """Fetch VIX — primary: yfinance ^VIX, fallback: VIXY ETF."""
        try:
            df = self._trad.fetch_ohlcv("^VIX", timeframe, limit=limit)
            if not df.empty:
                return df
        except Exception as e:
            logger.warning("yfinance ^VIX failed: %s — trying VIXY", e)

        try:
            return self._trad.fetch_ohlcv("VIXY", timeframe, limit=limit)
        except Exception:
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Real Yields (10Y TIPS)
    # ------------------------------------------------------------------

    def fetch_real_yields(self, limit: int = 200) -> pd.DataFrame:
        """Fetch 10Y TIPS yield — primary: FRED DFII10, fallback: TIP ETF proxy."""
        if self._fred_key:
            df = self._fetch_fred_series(self.FRED_REAL_YIELDS, limit)
            if not df.empty:
                return df

        # Fallback: TIP ETF (inverted for yield proxy)
        logger.info("No FRED data — using TIP ETF as real yield proxy")
        try:
            df = self._trad.fetch_ohlcv("TIP", "1d", limit=limit)
            df["close"] = -df["close"]
            return df
        except Exception as e:
            logger.warning("TIP proxy failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Additional FRED series
    # ------------------------------------------------------------------

    def fetch_10y_yield(self, limit: int = 200) -> pd.DataFrame:
        """Fetch 10-year Treasury nominal yield from FRED."""
        if self._fred_key:
            df = self._fetch_fred_series(self.FRED_10Y_NOMINAL, limit)
            if not df.empty:
                return df
        # Fallback: TLT (inverted)
        try:
            df = self._trad.fetch_ohlcv("TLT", "1d", limit=limit)
            df["close"] = -df["close"]
            return df
        except Exception:
            return pd.DataFrame()

    def fetch_breakeven_inflation(self, limit: int = 200) -> pd.DataFrame:
        """Fetch 10-year breakeven inflation rate from FRED."""
        if self._fred_key:
            return self._fetch_fred_series(self.FRED_BREAKEVEN_INFL, limit)
        return pd.DataFrame()

    def fetch_fed_funds_rate(self, limit: int = 200) -> pd.DataFrame:
        """Fetch effective federal funds rate from FRED."""
        if self._fred_key:
            return self._fetch_fred_series(self.FRED_FED_FUNDS, limit)
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # FRED generic fetcher
    # ------------------------------------------------------------------

    def _fetch_fred_series(self, series_id: str, limit: int = 200) -> pd.DataFrame:
        """Fetch a FRED time series (requires FRED_API_KEY env var)."""
        if not self._fred_key:
            return pd.DataFrame()

        url = "https://api.stlouisfed.org/fred/series/observations"
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=limit * 2)  # Buffer for weekends/holidays

        params = {
            "series_id": series_id,
            "api_key": self._fred_key,
            "file_type": "json",
            "observation_start": start.strftime("%Y-%m-%d"),
            "observation_end": end.strftime("%Y-%m-%d"),
            "sort_order": "asc",
        }
        try:
            resp = self._http.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("FRED fetch failed for %s: %s", series_id, e)
            return pd.DataFrame()

        observations = data.get("observations", [])
        if not observations:
            return pd.DataFrame()

        records = []
        for obs in observations:
            val = obs.get("value", ".")
            if val == ".":
                continue
            records.append({
                "timestamp": pd.Timestamp(obs["date"], tz="UTC"),
                "close": float(val),
            })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records).set_index("timestamp")
        df["open"] = df["close"]
        df["high"] = df["close"]
        df["low"] = df["close"]
        df["volume"] = 0.0
        return df[["open", "high", "low", "close", "volume"]].tail(limit)

    # ------------------------------------------------------------------
    # Gold (GC=F) — safe haven / inflation hedge
    # ------------------------------------------------------------------

    def fetch_gold(self, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
        """Fetch Gold futures — primary: GC=F, fallback: GLD ETF.

        Gold rising + BTC rising = inflation hedge narrative (bullish crypto).
        Gold rising + BTC falling = risk-off, capital fleeing to safe haven.
        Gold/BTC correlation shifts signal regime changes.
        """
        try:
            df = self._trad.fetch_ohlcv("GC=F", timeframe, limit=limit)
            if not df.empty:
                return df
        except Exception as e:
            logger.warning("yfinance GC=F failed: %s — trying GLD", e)

        try:
            return self._trad.fetch_ohlcv("GLD", timeframe, limit=limit)
        except Exception:
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # MOVE Index (bond volatility — the "VIX for bonds")
    # ------------------------------------------------------------------

    def fetch_move_index(self, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
        """Fetch MOVE Index — bond market volatility.

        MOVE > 120 = bond market stress, risk-off cascade imminent.
        MOVE spikes typically precede VIX spikes and BTC selloffs.
        Professional desks watch MOVE before VIX for early warning.
        Fallback: TLT 20-day realized vol as proxy.
        """
        try:
            df = self._trad.fetch_ohlcv("^MOVE", timeframe, limit=limit)
            if not df.empty:
                return df
        except Exception as e:
            logger.warning("yfinance ^MOVE failed: %s — computing TLT vol proxy", e)

        # Fallback: TLT realized vol * scaling factor as MOVE proxy
        try:
            import numpy as np
            tlt = self._trad.fetch_ohlcv("TLT", timeframe, limit=limit + 30)
            if tlt.empty:
                return pd.DataFrame()
            log_ret = np.log(tlt["close"] / tlt["close"].shift(1))
            vol_20d = log_ret.rolling(20).std() * np.sqrt(252) * 100
            tlt["close"] = vol_20d  # Scale to approximate MOVE range
            tlt["open"] = tlt["close"]
            tlt["high"] = tlt["close"]
            tlt["low"] = tlt["close"]
            return tlt.dropna().tail(limit)
        except Exception:
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Credit Spread (HYG/IEF ratio — credit risk proxy)
    # ------------------------------------------------------------------

    def fetch_credit_spread(self, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
        """Fetch HYG/IEF ratio as a credit spread proxy.

        HYG = high-yield corporate bonds, IEF = 7-10yr Treasuries.
        HYG/IEF rising = risk appetite, credit tightening easing (bullish).
        HYG/IEF falling = credit stress, flight to quality (bearish).
        A sharp drop in this ratio precedes risk-off moves by 1-3 days.
        """
        try:
            hyg = self._trad.fetch_ohlcv("HYG", timeframe, limit=limit)
            ief = self._trad.fetch_ohlcv("IEF", timeframe, limit=limit)

            if hyg.empty or ief.empty:
                return pd.DataFrame()

            # Align indices
            combined = hyg[["close"]].rename(columns={"close": "hyg"}).join(
                ief[["close"]].rename(columns={"close": "ief"}),
                how="inner",
            )
            if combined.empty:
                return pd.DataFrame()

            combined["close"] = combined["hyg"] / combined["ief"]
            combined["open"] = combined["close"]
            combined["high"] = combined["close"]
            combined["low"] = combined["close"]
            combined["volume"] = 0.0

            return combined[["open", "high", "low", "close", "volume"]]

        except Exception as e:
            logger.warning("Credit spread (HYG/IEF) fetch failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # 2-Year Treasury Yield (Fed rate expectations)
    # ------------------------------------------------------------------

    def fetch_2y_yield(self, limit: int = 200) -> pd.DataFrame:
        """Fetch 2-year Treasury yield from FRED (DGS2).

        The 2Y is the market's best guess of where the Fed funds rate
        will be. Sharp moves in 2Y → immediate risk-asset repricing.
        """
        if self._fred_key:
            df = self._fetch_fred_series("DGS2", limit)
            if not df.empty:
                return df
        # Fallback: SHY ETF (inverted)
        try:
            df = self._trad.fetch_ohlcv("SHY", "1d", limit=limit)
            df["close"] = -df["close"]
            return df
        except Exception:
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Yield Curve (10Y - 2Y spread)
    # ------------------------------------------------------------------

    def fetch_yield_curve(self, limit: int = 200) -> pd.DataFrame:
        """Fetch 10Y-2Y yield spread (yield curve shape).

        Inverted curve (negative) = recession signal.
        Re-steepening from inversion = recession arriving (counter-intuitively bearish).
        """
        if not self._fred_key:
            return pd.DataFrame()

        try:
            ten_y = self._fetch_fred_series(self.FRED_10Y_NOMINAL, limit)
            two_y = self._fetch_fred_series("DGS2", limit)

            if ten_y.empty or two_y.empty:
                return pd.DataFrame()

            combined = ten_y[["close"]].rename(columns={"close": "y10"}).join(
                two_y[["close"]].rename(columns={"close": "y2"}),
                how="inner",
            )
            combined["close"] = combined["y10"] - combined["y2"]
            combined["open"] = combined["close"]
            combined["high"] = combined["close"]
            combined["low"] = combined["close"]
            combined["volume"] = 0.0

            return combined[["open", "high", "low", "close", "volume"]]

        except Exception as e:
            logger.warning("Yield curve fetch failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Composite macro snapshot
    # ------------------------------------------------------------------

    def fetch_macro_snapshot(self) -> dict:
        """Quick macro environment snapshot for live monitoring."""
        snapshot: dict = {}

        try:
            dxy = self.fetch_dxy(limit=5)
            if not dxy.empty:
                snapshot["dxy_latest"] = round(float(dxy["close"].iloc[-1]), 2)
        except Exception:
            pass

        try:
            vix = self.fetch_vix(limit=5)
            if not vix.empty:
                vix_val = float(vix["close"].iloc[-1])
                snapshot["vix_latest"] = round(vix_val, 2)
                snapshot["vix_regime"] = (
                    "ELEVATED" if vix_val > 25 else
                    "HIGH" if vix_val > 20 else
                    "NORMAL" if vix_val > 15 else
                    "LOW"
                )
        except Exception:
            pass

        try:
            ry = self.fetch_real_yields(limit=5)
            if not ry.empty:
                snapshot["real_yield_latest"] = round(float(ry["close"].iloc[-1]), 3)
        except Exception:
            pass

        try:
            gold = self.fetch_gold(limit=5)
            if not gold.empty:
                snapshot["gold_latest"] = round(float(gold["close"].iloc[-1]), 2)
        except Exception:
            pass

        try:
            move = self.fetch_move_index(limit=5)
            if not move.empty:
                move_val = float(move["close"].iloc[-1])
                snapshot["move_latest"] = round(move_val, 2)
                snapshot["move_regime"] = (
                    "CRISIS" if move_val > 140 else
                    "STRESS" if move_val > 120 else
                    "ELEVATED" if move_val > 100 else
                    "NORMAL"
                )
        except Exception:
            pass

        try:
            credit = self.fetch_credit_spread(limit=5)
            if not credit.empty:
                snapshot["credit_spread_latest"] = round(float(credit["close"].iloc[-1]), 4)
        except Exception:
            pass

        return snapshot

    def health_check(self) -> dict:
        status = {
            "connector": "MacroConnector",
            "fred_key_set": self._fred_key is not None,
        }
        try:
            df = self.fetch_dxy(limit=5)
            status["dxy_ok"] = not df.empty
        except Exception as e:
            status["dxy_ok"] = False
            status["dxy_error"] = str(e)
        return status

    def close(self):
        self._http.close()
