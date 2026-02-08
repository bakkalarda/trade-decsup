"""Stage 1-4 classifier (deterministic Wyckoff stages).

Stage 1 (Base): sideways, compression, mean reversion
Stage 2 (Markup): uptrend, buy pullbacks
Stage 3 (Distribution): failed breakouts, weakening near highs
Stage 4 (Markdown): downtrend, short rallies
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dss.models.structure_state import SwingPivot
from dss.config import StructureGateConfig


def classify_stage(
    df: pd.DataFrame,
    pivots: list[SwingPivot],
    trend: dict,
    atr_percentile: pd.Series,
    cfg: StructureGateConfig,
) -> dict:
    """Classify current Wyckoff stage.

    Returns:
        {"stage": 1|2|3|4, "confidence": 0.0..1.0, "reasons": [...]}
    """
    result = {"stage": 1, "confidence": 0.5, "reasons": []}

    if len(df) < 20:
        result["reasons"].append("insufficient data")
        return result

    direction = trend.get("direction", "SIDEWAYS")
    trend_conf = trend.get("confidence", 0.0)

    # Volatility regime
    current_vol_pctile = float(atr_percentile.iloc[-1]) if len(atr_percentile) > 0 else 50.0
    is_compressed = current_vol_pctile <= cfg.stage_compression_atr_percentile
    is_expanding = current_vol_pctile >= cfg.vol_high_percentile

    # Range detection — price bounded within a % band
    recent = df.tail(20)
    recent_range = (recent["high"].max() - recent["low"].min()) / recent["close"].mean() * 100
    is_ranging = recent_range < 15  # Less than 15% range in 20 bars

    # Swing analysis
    swing_highs = [p for p in pivots if p.is_high]
    swing_lows = [p for p in pivots if not p.is_high]

    # Price relative to key levels
    if swing_highs and swing_lows:
        highest_recent = max(p.price for p in swing_highs[-5:]) if len(swing_highs) >= 2 else 0
        lowest_recent = min(p.price for p in swing_lows[-5:]) if len(swing_lows) >= 2 else 0
        current_price = float(df["close"].iloc[-1])
        range_size = highest_recent - lowest_recent if highest_recent > lowest_recent else 1
        position_in_range = (current_price - lowest_recent) / range_size if range_size > 0 else 0.5
    else:
        position_in_range = 0.5

    # Classification logic
    if direction == "UP" and trend_conf >= 0.6:
        if is_expanding or position_in_range > 0.8:
            # Could be Stage 3 (distribution) if we see failures at highs
            recent_hh = trend.get("hh_count", 0)
            recent_lh = trend.get("lh_count", 0)
            if recent_lh > 0 and position_in_range > 0.75:
                result["stage"] = 3
                result["confidence"] = 0.6
                result["reasons"].append("uptrend weakening near highs (LH appearing)")
                result["reasons"].append(f"position in range: {position_in_range:.0%}")
            else:
                result["stage"] = 2
                result["confidence"] = min(trend_conf, 0.9)
                result["reasons"].append("HH/HL sequence confirmed (markup)")
        else:
            result["stage"] = 2
            result["confidence"] = min(trend_conf, 0.85)
            result["reasons"].append("uptrend with moderate volatility")

    elif direction == "DOWN" and trend_conf >= 0.6:
        if is_expanding or position_in_range < 0.2:
            recent_ll = trend.get("ll_count", 0)
            recent_hl = trend.get("hl_count", 0)
            if recent_hl > 0 and position_in_range < 0.25:
                result["stage"] = 1
                result["confidence"] = 0.5
                result["reasons"].append("downtrend may be basing (HL appearing near lows)")
            else:
                result["stage"] = 4
                result["confidence"] = min(trend_conf, 0.9)
                result["reasons"].append("LH/LL sequence confirmed (markdown)")
        else:
            result["stage"] = 4
            result["confidence"] = min(trend_conf, 0.85)
            result["reasons"].append("downtrend with moderate volatility")

    elif direction == "SIDEWAYS" or trend_conf < 0.6:
        if is_compressed and is_ranging:
            result["stage"] = 1
            result["confidence"] = 0.7
            result["reasons"].append("compression + range = accumulation/distribution base")
        elif is_ranging:
            # In a range — check if it's after an uptrend (distribution) or downtrend (accumulation)
            # Use the overall trend from earlier pivots
            older_highs = swing_highs[:-3] if len(swing_highs) > 3 else swing_highs
            if len(older_highs) >= 2 and older_highs[-1].price > older_highs[0].price:
                result["stage"] = 3
                result["confidence"] = 0.55
                result["reasons"].append("range after uptrend — possible distribution")
            elif len(older_highs) >= 2 and older_highs[-1].price < older_highs[0].price:
                result["stage"] = 1
                result["confidence"] = 0.55
                result["reasons"].append("range after downtrend — possible accumulation")
            else:
                result["stage"] = 1
                result["confidence"] = 0.5
                result["reasons"].append("sideways range — stage 1 default")
        else:
            result["stage"] = 1
            result["confidence"] = 0.4
            result["reasons"].append("unclear structure — defaulting to stage 1")

    result["reasons"].append(f"vol_percentile={current_vol_pctile:.0f}")
    result["reasons"].append(f"trend={direction}(conf={trend_conf:.0%})")
    return result
