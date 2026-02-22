"""Options feature engineering — signals from derivatives market.

Features:
  - P/C ratio classification (contrarian)
  - IV percentile (fear/complacency gauge)
  - IV skew (hedging demand)
  - Max pain magnet effect
  - Options sentiment composite score
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

import pandas as pd

from dss.utils.math import percentile_rank


class OptionsSentiment(str, Enum):
    FEARFUL = "FEARFUL"           # High P/C, high IV — potential contrarian buy
    CAUTIOUS = "CAUTIOUS"         # Elevated P/C or IV
    NEUTRAL = "NEUTRAL"
    COMPLACENT = "COMPLACENT"     # Low P/C, low IV — potential contrarian sell
    EUPHORIC = "EUPHORIC"         # Very low P/C, very low IV


def classify_options_sentiment(
    pc_ratio: Optional[float],
    iv_percentile: Optional[float],
    iv_skew: Optional[float],
) -> OptionsSentiment:
    """Classify options market sentiment.

    P/C ratio interpretation (contrarian):
      > 1.3  = Fearful (lots of put buying — contrarian bullish)
      > 1.0  = Cautious
      0.7-1.0 = Neutral
      < 0.7  = Complacent (call buying dominant — contrarian bearish)
      < 0.5  = Euphoric

    IV percentile:
      > 80th = High fear / uncertainty
      < 20th = Low fear / complacency
    """
    if pc_ratio is None and iv_percentile is None:
        return OptionsSentiment.NEUTRAL

    fear_signals = 0
    complacency_signals = 0

    if pc_ratio is not None:
        if pc_ratio > 1.3:
            fear_signals += 2
        elif pc_ratio > 1.0:
            fear_signals += 1
        elif pc_ratio < 0.5:
            complacency_signals += 2
        elif pc_ratio < 0.7:
            complacency_signals += 1

    if iv_percentile is not None:
        if iv_percentile > 80:
            fear_signals += 2
        elif iv_percentile > 65:
            fear_signals += 1
        elif iv_percentile < 20:
            complacency_signals += 2
        elif iv_percentile < 35:
            complacency_signals += 1

    if iv_skew is not None:
        if iv_skew > 10:  # Puts much more expensive than calls
            fear_signals += 1
        elif iv_skew < -5:  # Calls more expensive (unusual)
            complacency_signals += 1

    if fear_signals >= 3:
        return OptionsSentiment.FEARFUL
    elif fear_signals >= 2:
        return OptionsSentiment.CAUTIOUS
    elif complacency_signals >= 3:
        return OptionsSentiment.EUPHORIC
    elif complacency_signals >= 2:
        return OptionsSentiment.COMPLACENT
    return OptionsSentiment.NEUTRAL


def compute_iv_percentile(iv_history: pd.DataFrame, lookback: int = 252) -> Optional[float]:
    """Compute IV percentile rank over the lookback window.

    Args:
        iv_history: DataFrame with 'iv_close' column, datetime-indexed.
        lookback: Number of bars for percentile computation.

    Returns:
        Current IV's percentile rank (0-100), or None if insufficient data.
    """
    if iv_history.empty or "iv_close" not in iv_history.columns:
        return None

    series = iv_history["iv_close"].dropna()
    if len(series) < 20:
        return None

    pctile = percentile_rank(series, lookback=min(lookback, len(series)))
    if pctile.empty or pd.isna(pctile.iloc[-1]):
        return None

    return round(float(pctile.iloc[-1]), 2)


def compute_options_score(
    pc_ratio: Optional[float],
    iv_percentile: Optional[float],
    iv_skew: Optional[float],
    max_pain_distance_pct: Optional[float],
    direction: str,
) -> float:
    """Compute options contribution to the composite score (-2..+2).

    For LONG trades:
      - High P/C (fear) = bullish (contrarian) → positive
      - Low IV = complacency → negative (potential top)
      - Price below max pain → bullish magnet
      - Put skew (high IV skew) → contrarian bullish

    For SHORT trades: inverse logic.
    """
    score = 0.0

    # P/C ratio (contrarian)
    if pc_ratio is not None:
        if pc_ratio > 1.3:
            score += 1.0   # Extreme put buying — contrarian bullish
        elif pc_ratio > 1.0:
            score += 0.5
        elif pc_ratio < 0.5:
            score -= 1.0   # Extreme call buying — contrarian bearish
        elif pc_ratio < 0.7:
            score -= 0.5

    # IV percentile
    if iv_percentile is not None:
        if iv_percentile > 80:
            score += 0.5   # High fear — contrarian bullish
        elif iv_percentile < 20:
            score -= 0.5   # Complacency — potential top

    # Max pain magnet
    if max_pain_distance_pct is not None:
        if max_pain_distance_pct > 5:
            # Price well above max pain — gravity pulls down
            score -= 0.5
        elif max_pain_distance_pct < -5:
            # Price well below max pain — gravity pulls up
            score += 0.5

    # Clamp to range
    score = max(-2.0, min(2.0, score))

    # Flip for shorts
    if direction == "SHORT":
        score = -score

    return round(score, 2)
