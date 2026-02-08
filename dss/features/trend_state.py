"""Trend state classifier — HH/HL (up) vs LH/LL (down) vs sideways."""

from __future__ import annotations

from dss.models.structure_state import SwingPivot


def classify_trend(pivots: list[SwingPivot], min_swings: int = 3) -> dict:
    """Classify trend from swing pivots.

    Returns:
        {
            "direction": "UP" | "DOWN" | "SIDEWAYS",
            "confidence": 0.0..1.0,
            "hh_count": int,
            "hl_count": int,
            "lh_count": int,
            "ll_count": int,
        }
    """
    result = {
        "direction": "SIDEWAYS",
        "confidence": 0.0,
        "hh_count": 0,
        "hl_count": 0,
        "lh_count": 0,
        "ll_count": 0,
    }

    swing_highs = [p for p in pivots if p.is_high]
    swing_lows = [p for p in pivots if not p.is_high]

    if len(swing_highs) < 2 and len(swing_lows) < 2:
        return result

    # Count higher-highs and lower-highs
    hh = 0
    lh = 0
    for i in range(1, len(swing_highs)):
        if swing_highs[i].price > swing_highs[i - 1].price:
            hh += 1
        elif swing_highs[i].price < swing_highs[i - 1].price:
            lh += 1

    # Count higher-lows and lower-lows
    hl = 0
    ll = 0
    for i in range(1, len(swing_lows)):
        if swing_lows[i].price > swing_lows[i - 1].price:
            hl += 1
        elif swing_lows[i].price < swing_lows[i - 1].price:
            ll += 1

    result["hh_count"] = hh
    result["hl_count"] = hl
    result["lh_count"] = lh
    result["ll_count"] = ll

    total_bull = hh + hl
    total_bear = lh + ll
    total = total_bull + total_bear

    if total == 0:
        return result

    bull_ratio = total_bull / total
    bear_ratio = total_bear / total

    # Use recent swings (last N) for more weight
    recent_highs = swing_highs[-min_swings:] if len(swing_highs) >= min_swings else swing_highs
    recent_lows = swing_lows[-min_swings:] if len(swing_lows) >= min_swings else swing_lows

    recent_hh = sum(
        1 for i in range(1, len(recent_highs))
        if recent_highs[i].price > recent_highs[i - 1].price
    )
    recent_lh = sum(
        1 for i in range(1, len(recent_highs))
        if recent_highs[i].price < recent_highs[i - 1].price
    )
    recent_hl = sum(
        1 for i in range(1, len(recent_lows))
        if recent_lows[i].price > recent_lows[i - 1].price
    )
    recent_ll = sum(
        1 for i in range(1, len(recent_lows))
        if recent_lows[i].price < recent_lows[i - 1].price
    )

    recent_bull = recent_hh + recent_hl
    recent_bear = recent_lh + recent_ll
    recent_total = recent_bull + recent_bear

    if recent_total == 0:
        # Fall back to overall
        if bull_ratio > 0.6:
            result["direction"] = "UP"
            result["confidence"] = bull_ratio
        elif bear_ratio > 0.6:
            result["direction"] = "DOWN"
            result["confidence"] = bear_ratio
        return result

    recent_bull_ratio = recent_bull / recent_total

    if recent_bull_ratio >= 0.65:
        result["direction"] = "UP"
        result["confidence"] = round(recent_bull_ratio, 2)
    elif recent_bull_ratio <= 0.35:
        result["direction"] = "DOWN"
        result["confidence"] = round(1 - recent_bull_ratio, 2)
    else:
        result["direction"] = "SIDEWAYS"
        result["confidence"] = round(0.5, 2)

    return result
