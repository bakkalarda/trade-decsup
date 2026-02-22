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


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI (Relative Strength Index), 0-100."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_divergence(
    close: pd.Series,
    rsi_series: pd.Series,
    lookback: int = 30,
) -> str:
    """Detect RSI divergence over the last *lookback* bars.

    Bullish divergence: price makes lower low, RSI makes higher low.
    Bearish divergence: price makes higher high, RSI makes lower high.

    Returns: 'BULLISH', 'BEARISH', or 'NONE'.
    """
    if len(close) < lookback or len(rsi_series) < lookback:
        return "NONE"

    c = close.values[-lookback:]
    r = rsi_series.values[-lookback:]

    # Remove NaN
    mask = ~(np.isnan(c) | np.isnan(r))
    c, r = c[mask], r[mask]
    if len(c) < 10:
        return "NONE"

    half = len(c) // 2

    # Compare first-half vs second-half extremes
    price_low_1 = np.min(c[:half])
    price_low_2 = np.min(c[half:])
    rsi_low_1 = np.min(r[:half])
    rsi_low_2 = np.min(r[half:])

    price_high_1 = np.max(c[:half])
    price_high_2 = np.max(c[half:])
    rsi_high_1 = np.max(r[:half])
    rsi_high_2 = np.max(r[half:])

    # Bullish: price lower low + RSI higher low
    if price_low_2 < price_low_1 * 0.998 and rsi_low_2 > rsi_low_1 + 2:
        return "BULLISH"

    # Bearish: price higher high + RSI lower high
    if price_high_2 > price_high_1 * 1.002 and rsi_high_2 < rsi_high_1 - 2:
        return "BEARISH"

    return "NONE"


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))
