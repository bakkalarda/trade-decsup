"""Mathematical utility functions — z-scores, rolling regression, ATR, etc."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def zscore(series: pd.Series, lookback: int = 90) -> pd.Series:
    """Rolling z-score over *lookback* periods."""
    rolling_mean = series.rolling(window=lookback, min_periods=max(lookback // 3, 5)).mean()
    rolling_std = series.rolling(window=lookback, min_periods=max(lookback // 3, 5)).std()
    # Avoid division by zero
    rolling_std = rolling_std.replace(0, np.nan)
    return (series - rolling_mean) / rolling_std


def zscore_current(series: pd.Series, lookback: int = 90) -> float:
    """Return the latest z-score value."""
    z = zscore(series, lookback)
    val = z.iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def rolling_regression_slope(series: pd.Series, window: int = 14) -> pd.Series:
    """Rolling OLS slope (linear regression over *window* bars)."""
    def _slope(arr: np.ndarray) -> float:
        if len(arr) < 3 or np.isnan(arr).any():
            return np.nan
        x = np.arange(len(arr))
        slope, _, _, _, _ = stats.linregress(x, arr)
        return slope

    return series.rolling(window=window, min_periods=max(window // 2, 3)).apply(
        _slope, raw=True
    )


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def atr_percent(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR as a percentage of close price."""
    atr_val = atr(high, low, close, period)
    return (atr_val / close) * 100


def percentile_rank(series: pd.Series, lookback: int = 100) -> pd.Series:
    """Rolling percentile rank of the latest value within the lookback window."""
    def _pctile(arr: np.ndarray) -> float:
        if len(arr) < 2:
            return 50.0
        return float(stats.percentileofscore(arr[:-1], arr[-1], kind="rank"))

    return series.rolling(window=lookback, min_periods=max(lookback // 3, 5)).apply(
        _pctile, raw=True
    )


def impulse(series: pd.Series, window: int = 5) -> pd.Series:
    """Short-term change (impulse) over *window* bars."""
    return series.diff(window)


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))
