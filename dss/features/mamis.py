"""Justin Mamis market cycle classifier.

Maps price action to Mamis's psychology-based market phases from
"The Nature of Risk":

  ACCUMULATION ZONE (bottom):
    DESPONDENCY  → DEPRESSION → HOPE → RELIEF

  MARKUP ZONE:
    OPTIMISM → EXCITEMENT → THRILL

  DISTRIBUTION ZONE (top):
    ANXIETY → DENIAL → FEAR

  MARKDOWN ZONE:
    DESPERATION → PANIC → CAPITULATION

The classifier uses:
  - Price position relative to 50-day and 200-day moving averages
  - Rate of change (momentum)
  - Volatility regime (ATR percentile)
  - Volume patterns
  - Trend strength / exhaustion
"""

from __future__ import annotations

from enum import Enum

import numpy as np
import pandas as pd


class MamisPhase(str, Enum):
    # Accumulation zone
    DESPONDENCY = "DESPONDENCY"
    DEPRESSION = "DEPRESSION"
    HOPE = "HOPE"
    RELIEF = "RELIEF"
    # Markup zone
    OPTIMISM = "OPTIMISM"
    EXCITEMENT = "EXCITEMENT"
    THRILL = "THRILL"
    # Distribution zone
    ANXIETY = "ANXIETY"
    DENIAL = "DENIAL"
    FEAR = "FEAR"
    # Markdown zone
    DESPERATION = "DESPERATION"
    PANIC = "PANIC"
    CAPITULATION = "CAPITULATION"


# Group phases for scoring
BULLISH_PHASES = {
    MamisPhase.HOPE, MamisPhase.RELIEF, MamisPhase.OPTIMISM,
}
STRONG_BULLISH_PHASES = {
    MamisPhase.DEPRESSION, MamisPhase.DESPONDENCY,  # Contrarian
}
CAUTION_PHASES = {
    MamisPhase.EXCITEMENT, MamisPhase.THRILL,
    MamisPhase.ANXIETY,
}
BEARISH_PHASES = {
    MamisPhase.DENIAL, MamisPhase.FEAR, MamisPhase.DESPERATION,
}
STRONG_BEARISH_PHASES = {
    MamisPhase.PANIC, MamisPhase.CAPITULATION,  # Contrarian
}


def classify_mamis_phase(
    df: pd.DataFrame,
    atr_percentile: float = 50.0,
) -> dict:
    """Classify the current Justin Mamis market cycle phase.

    Args:
        df: OHLCV DataFrame with at least 200 bars.
        atr_percentile: Current ATR percentile (0-100).

    Returns:
        {
            "phase": MamisPhase,
            "phase_group": "ACCUMULATION" | "MARKUP" | "DISTRIBUTION" | "MARKDOWN",
            "confidence": 0.0..1.0,
            "score": -2..+2 (bullish/bearish bias for scoring),
            "signals": {
                "ma50_position", "ma200_position", "roc_20",
                "volume_trend", "volatility"
            },
            "reasons": [...]
        }
    """
    result = {
        "phase": MamisPhase.OPTIMISM,
        "phase_group": "MARKUP",
        "confidence": 0.5,
        "score": 0.0,
        "signals": {},
        "reasons": [],
    }

    if len(df) < 50:
        result["reasons"].append("insufficient data (need 50+ bars)")
        return result

    close = df["close"]
    volume = df["volume"] if "volume" in df.columns else pd.Series(dtype=float)
    current_price = float(close.iloc[-1])

    # --- Moving averages ---
    ma50 = close.rolling(50, min_periods=30).mean()
    use_ma200 = len(close) >= 200
    ma200 = close.rolling(200, min_periods=100).mean() if use_ma200 else ma50

    ma50_val = float(ma50.iloc[-1]) if not pd.isna(ma50.iloc[-1]) else current_price
    ma200_val = float(ma200.iloc[-1]) if not pd.isna(ma200.iloc[-1]) else current_price

    above_ma50 = current_price > ma50_val
    above_ma200 = current_price > ma200_val

    # Distance from MAs (as %)
    ma50_dist = ((current_price - ma50_val) / ma50_val) * 100 if ma50_val > 0 else 0
    ma200_dist = ((current_price - ma200_val) / ma200_val) * 100 if ma200_val > 0 else 0

    # MA slope (trend direction)
    ma50_slope = float(ma50.iloc[-1] - ma50.iloc[-5]) / max(ma50_val, 1) * 100 if len(ma50) >= 5 else 0
    ma200_slope = float(ma200.iloc[-1] - ma200.iloc[-5]) / max(ma200_val, 1) * 100 if use_ma200 and len(ma200) >= 5 else 0

    # --- Rate of change ---
    roc_20 = ((current_price - float(close.iloc[-20])) / float(close.iloc[-20]) * 100) if len(close) >= 20 else 0
    roc_5 = ((current_price - float(close.iloc[-5])) / float(close.iloc[-5]) * 100) if len(close) >= 5 else 0

    # --- Volume trend ---
    vol_trend = "FLAT"
    if not volume.empty and len(volume) >= 20:
        recent_vol = volume.tail(5).mean()
        avg_vol = volume.tail(20).mean()
        if avg_vol > 0:
            vol_ratio = recent_vol / avg_vol
            if vol_ratio > 1.5:
                vol_trend = "SURGE"
            elif vol_ratio > 1.2:
                vol_trend = "RISING"
            elif vol_ratio < 0.7:
                vol_trend = "DECLINING"

    # --- Volatility ---
    vol_regime = "NORMAL"
    if atr_percentile > 75:
        vol_regime = "HIGH"
    elif atr_percentile < 25:
        vol_regime = "LOW"

    # Store signals
    result["signals"] = {
        "above_ma50": above_ma50,
        "above_ma200": above_ma200,
        "ma50_dist_pct": round(ma50_dist, 2),
        "ma200_dist_pct": round(ma200_dist, 2),
        "ma50_slope": round(ma50_slope, 4),
        "roc_20": round(roc_20, 2),
        "roc_5": round(roc_5, 2),
        "volume_trend": vol_trend,
        "volatility": vol_regime,
    }

    # === PHASE CLASSIFICATION ===

    # Below both MAs — accumulation or markdown territory
    if not above_ma50 and not above_ma200:
        if vol_regime == "HIGH" and roc_20 < -15:
            if vol_trend == "SURGE":
                result["phase"] = MamisPhase.CAPITULATION
                result["phase_group"] = "MARKDOWN"
                result["confidence"] = 0.8
                result["reasons"].append("sharp decline + volume surge + high volatility")
            else:
                result["phase"] = MamisPhase.PANIC
                result["phase_group"] = "MARKDOWN"
                result["confidence"] = 0.7
                result["reasons"].append("sharp decline + high volatility")
        elif roc_20 < -10:
            result["phase"] = MamisPhase.DESPERATION
            result["phase_group"] = "MARKDOWN"
            result["confidence"] = 0.65
            result["reasons"].append("meaningful decline below both MAs")
        elif vol_regime == "LOW" and abs(roc_20) < 5:
            if ma50_slope > 0:
                result["phase"] = MamisPhase.HOPE
                result["phase_group"] = "ACCUMULATION"
                result["confidence"] = 0.6
                result["reasons"].append("below MAs but MA50 turning up + low vol")
            else:
                result["phase"] = MamisPhase.DEPRESSION
                result["phase_group"] = "ACCUMULATION"
                result["confidence"] = 0.6
                result["reasons"].append("below MAs + low vol + flat momentum")
        elif roc_5 > 2 and ma50_slope > 0:
            result["phase"] = MamisPhase.HOPE
            result["phase_group"] = "ACCUMULATION"
            result["confidence"] = 0.6
            result["reasons"].append("below MAs but bouncing with improving momentum")
        else:
            result["phase"] = MamisPhase.DESPONDENCY
            result["phase_group"] = "ACCUMULATION"
            result["confidence"] = 0.55
            result["reasons"].append("below both MAs, no clear recovery yet")

    # Between MAs — transition zone
    elif above_ma50 and not above_ma200:
        if roc_20 > 5 and ma50_slope > 0:
            result["phase"] = MamisPhase.RELIEF
            result["phase_group"] = "ACCUMULATION"
            result["confidence"] = 0.65
            result["reasons"].append("above MA50 but below MA200, positive momentum")
        elif roc_20 < -3:
            result["phase"] = MamisPhase.FEAR
            result["phase_group"] = "DISTRIBUTION"
            result["confidence"] = 0.6
            result["reasons"].append("losing MA200 support, negative momentum")
        else:
            result["phase"] = MamisPhase.RELIEF
            result["phase_group"] = "ACCUMULATION"
            result["confidence"] = 0.5
            result["reasons"].append("recovering — between MA50 and MA200")

    elif not above_ma50 and above_ma200:
        if roc_20 < -5:
            result["phase"] = MamisPhase.FEAR
            result["phase_group"] = "DISTRIBUTION"
            result["confidence"] = 0.6
            result["reasons"].append("lost MA50 but above MA200, pullback deepening")
        else:
            result["phase"] = MamisPhase.DENIAL
            result["phase_group"] = "DISTRIBUTION"
            result["confidence"] = 0.55
            result["reasons"].append("below MA50 but above MA200 — first cracks")

    # Above both MAs — markup or distribution
    elif above_ma50 and above_ma200:
        if ma50_dist > 20 and roc_20 > 15:
            if vol_trend in ("SURGE", "RISING"):
                result["phase"] = MamisPhase.THRILL
                result["phase_group"] = "MARKUP"
                result["confidence"] = 0.75
                result["reasons"].append("far above MAs + strong momentum + high volume")
            else:
                result["phase"] = MamisPhase.EXCITEMENT
                result["phase_group"] = "MARKUP"
                result["confidence"] = 0.7
                result["reasons"].append("far above MAs + strong momentum")
        elif ma50_dist > 10 and roc_20 > 5:
            result["phase"] = MamisPhase.EXCITEMENT
            result["phase_group"] = "MARKUP"
            result["confidence"] = 0.65
            result["reasons"].append("extended above MAs with solid momentum")
        elif roc_20 > 0 and ma50_slope > 0:
            result["phase"] = MamisPhase.OPTIMISM
            result["phase_group"] = "MARKUP"
            result["confidence"] = 0.65
            result["reasons"].append("above both MAs with positive trend")
        elif roc_20 < -3 and ma50_dist < 3:
            result["phase"] = MamisPhase.ANXIETY
            result["phase_group"] = "DISTRIBUTION"
            result["confidence"] = 0.6
            result["reasons"].append("above MAs but momentum fading, near MA50")
        elif roc_5 < -2 and vol_trend in ("SURGE", "RISING"):
            result["phase"] = MamisPhase.ANXIETY
            result["phase_group"] = "DISTRIBUTION"
            result["confidence"] = 0.6
            result["reasons"].append("sharp short-term pullback with volume")
        else:
            result["phase"] = MamisPhase.OPTIMISM
            result["phase_group"] = "MARKUP"
            result["confidence"] = 0.55
            result["reasons"].append("above both MAs, moderate momentum")

    # === SCORE for composite scoring ===
    result["score"] = _compute_mamis_score(result["phase"])

    return result


def _compute_mamis_score(phase: MamisPhase) -> float:
    """Map Mamis phase to a directional score (-2..+2).

    Contrarian logic at extremes, trend-following in mid-cycle.

    SYMMETRIC design: The positive and negative scores are mirror images
    so the system has no inherent long or short bias.

    Bottom half (bullish):                    Top half (bearish):
      CAPITULATION  +1.5  (contrarian buy)  ↔  THRILL       -1.5  (contrarian sell)
      PANIC         +1.0  (contrarian buy)  ↔  EXCITEMENT   -1.0  (extended)
      DESPONDENCY   +0.5  (early accum)     ↔  ANXIETY      -0.5  (first cracks)
      DEPRESSION    +0.5  (quiet accum)     ↔  DENIAL       -0.5  (distribution)
      HOPE          +1.5  (transition up)   ↔  FEAR         -1.5  (transition down)
      RELIEF        +1.0  (confirmed recov) ↔  DESPERATION  -1.0  (confirmed decline)

    Mid-cycle (truly neutral):
      OPTIMISM      0.0   (healthy trend, no bias)
    """
    scores = {
        # Accumulation / markdown contrarian (bullish)
        MamisPhase.CAPITULATION: 1.5,    # Extreme contrarian buy
        MamisPhase.PANIC: 1.0,           # Contrarian buy
        MamisPhase.DESPONDENCY: 0.5,     # Early accumulation
        MamisPhase.DEPRESSION: 0.5,      # Quiet accumulation
        MamisPhase.HOPE: 1.5,            # Transition up — strong buy zone
        MamisPhase.RELIEF: 1.0,          # Confirmed recovery
        # Mid-cycle (neutral — no directional bias)
        MamisPhase.OPTIMISM: 0.0,        # Healthy trend, zero bias
        # Distribution / markup exhaustion (bearish)
        MamisPhase.EXCITEMENT: -1.0,     # Getting extended
        MamisPhase.THRILL: -1.5,         # Extreme contrarian sell
        MamisPhase.ANXIETY: -0.5,        # First cracks
        MamisPhase.DENIAL: -0.5,         # Distribution forming
        MamisPhase.FEAR: -1.5,           # Transition down — strong sell zone
        MamisPhase.DESPERATION: -1.0,    # Confirmed decline
    }
    # Positive sum: 1.5+1.0+0.5+0.5+1.5+1.0 = 6.0
    # Negative sum: -1.0-1.5-0.5-0.5-1.5-1.0 = -6.0
    # Perfectly symmetric
    return scores.get(phase, 0.0)


def mamis_phase_aligns_with_direction(phase: MamisPhase, direction: str) -> bool:
    """Check if the Mamis phase supports the trade direction."""
    if direction == "LONG":
        return phase in (
            BULLISH_PHASES | STRONG_BULLISH_PHASES |
            {MamisPhase.CAPITULATION, MamisPhase.PANIC}  # Contrarian longs
        )
    else:  # SHORT
        return phase in (
            BEARISH_PHASES | {MamisPhase.THRILL, MamisPhase.ANXIETY}
        )
