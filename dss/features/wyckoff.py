"""Wyckoff event detection — Spring, UTAD, LPS, LPSY.

Only the events that matter for swing trading:
- Spring: sweep below range support -> close back above -> LPS retest holds
- UT/UTAD: sweep above range resistance -> close back inside -> LPSY retest fails
- SOS/LPS: strength + backing-up -> buy the LPS retest
- SOW/LPSY: weakness + last supply -> short the LPSY retest
"""

from __future__ import annotations

import pandas as pd

from dss.models.structure_state import SwingPivot, SRZone


def detect_wyckoff_events(
    df: pd.DataFrame,
    pivots: list[SwingPivot],
    zones: list[SRZone],
    stage: dict,
) -> dict:
    """Detect Wyckoff events based on price action relative to zones.

    Returns:
        {
            "event": "SPRING" | "UTAD" | "LPS" | "LPSY" | "NONE",
            "confidence": 0.0..1.0,
            "description": str,
            "phase_alignment": "ALIGNED" | "MISALIGNED",
        }
    """
    result = {
        "event": "NONE",
        "confidence": 0.0,
        "description": "",
        "phase_alignment": "ALIGNED",
    }

    if len(df) < 10 or not zones:
        return result

    current_stage = stage.get("stage", 1)
    closes = df["close"].values
    lows = df["low"].values
    highs = df["high"].values
    current_price = float(closes[-1])

    # Find range boundaries from zones
    support_zones = [z for z in zones if z.is_support]
    resistance_zones = [z for z in zones if not z.is_support]

    if not support_zones and not resistance_zones:
        return result

    # Use strongest zones
    key_support = support_zones[0] if support_zones else None
    key_resistance = resistance_zones[0] if resistance_zones else None

    # --- SPRING detection ---
    # Price swept below support (wick or close below) then closed back above
    if key_support:
        for i in range(max(len(df) - 10, 0), len(df)):
            sweep_below = lows[i] < key_support.low
            close_above = closes[i] > key_support.low

            if sweep_below and close_above:
                # Check if subsequent bars hold above support
                bars_after = closes[i + 1:] if i + 1 < len(df) else []
                holds = all(c > key_support.low for c in bars_after) if len(bars_after) > 0 else True

                if holds:
                    result["event"] = "SPRING"
                    result["confidence"] = 0.7
                    result["description"] = (
                        f"Spring: swept below support {key_support.low:.2f}, "
                        f"closed back above at bar {i}"
                    )
                    # Phase alignment: springs are best in Stage 1 (accumulation)
                    if current_stage in (1, 4):
                        result["phase_alignment"] = "ALIGNED"
                    else:
                        result["phase_alignment"] = "MISALIGNED"
                    break

    # --- UTAD detection ---
    # Price swept above resistance then closed back below
    if key_resistance and result["event"] == "NONE":
        for i in range(max(len(df) - 10, 0), len(df)):
            sweep_above = highs[i] > key_resistance.high
            close_below = closes[i] < key_resistance.high

            if sweep_above and close_below:
                bars_after = closes[i + 1:] if i + 1 < len(df) else []
                fails = all(c < key_resistance.high for c in bars_after) if len(bars_after) > 0 else True

                if fails:
                    result["event"] = "UTAD"
                    result["confidence"] = 0.7
                    result["description"] = (
                        f"UTAD: swept above resistance {key_resistance.high:.2f}, "
                        f"closed back below at bar {i}"
                    )
                    if current_stage in (3, 2):
                        result["phase_alignment"] = "ALIGNED"
                    else:
                        result["phase_alignment"] = "MISALIGNED"
                    break

    # --- LPS detection (Last Point of Support — bullish continuation) ---
    if key_support and result["event"] == "NONE" and current_stage == 2:
        # After a sign of strength, price pulls back to support and holds
        recent_low = min(lows[-5:]) if len(lows) >= 5 else lows[-1]
        in_zone = key_support.low <= recent_low <= key_support.high + (key_support.high - key_support.low) * 0.5

        if in_zone and current_price > key_support.high:
            result["event"] = "LPS"
            result["confidence"] = 0.6
            result["description"] = (
                f"LPS: pullback to support zone {key_support.low:.2f}-{key_support.high:.2f} "
                f"holding in Stage 2"
            )
            result["phase_alignment"] = "ALIGNED"

    # --- LPSY detection (Last Point of Supply — bearish continuation) ---
    if key_resistance and result["event"] == "NONE" and current_stage == 4:
        recent_high = max(highs[-5:]) if len(highs) >= 5 else highs[-1]
        in_zone = key_resistance.low - (key_resistance.high - key_resistance.low) * 0.5 <= recent_high <= key_resistance.high

        if in_zone and current_price < key_resistance.low:
            result["event"] = "LPSY"
            result["confidence"] = 0.6
            result["description"] = (
                f"LPSY: rally to resistance zone {key_resistance.low:.2f}-{key_resistance.high:.2f} "
                f"failing in Stage 4"
            )
            result["phase_alignment"] = "ALIGNED"

    return result


def get_phase_alignment(event: str, direction: str, stage: int) -> str:
    """Check if a given event + direction is aligned with the current stage.

    Returns 'ALIGNED' or 'MISALIGNED'.
    """
    aligned_map = {
        ("SPRING", "LONG"): {1, 4},      # Springs work in accumulation
        ("UTAD", "SHORT"): {3, 2},        # UTADs work in distribution
        ("LPS", "LONG"): {2},             # LPS is a Stage 2 continuation
        ("LPSY", "SHORT"): {4},           # LPSY is a Stage 4 continuation
    }

    key = (event, direction)
    allowed_stages = aligned_map.get(key, set())
    return "ALIGNED" if stage in allowed_stages else "MISALIGNED"
