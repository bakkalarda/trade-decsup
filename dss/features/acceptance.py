"""Acceptance vs Rejection detection at S/R zones.

Acceptance: close beyond zone + follow-through + retest holds.
Rejection: sweep beyond zone then close back inside.
"""

from __future__ import annotations

import pandas as pd

from dss.models.structure_state import SRZone
from dss.config import StructureGateConfig


def detect_acceptance_rejection(
    df: pd.DataFrame,
    zones: list[SRZone],
    atr_series: pd.Series,
    cfg: StructureGateConfig,
) -> dict:
    """Scan recent bars for acceptance/rejection events at zones.

    Returns:
        {
            "events": [
                {
                    "type": "ACCEPTANCE" | "REJECTION",
                    "zone_low": float,
                    "zone_high": float,
                    "direction": "BULLISH" | "BEARISH",
                    "bar_index": int,
                    "description": str,
                }
            ],
            "last_acceptance": str | None,
            "last_rejection": str | None,
        }
    """
    events = []
    if len(df) < cfg.acceptance_follow_through_bars + 2 or not zones:
        return {"events": [], "last_acceptance": None, "last_rejection": None}

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    current_atr = float(atr_series.iloc[-1]) if len(atr_series) > 0 else 0
    buffer = current_atr * cfg.acceptance_retest_buffer_atr

    # Only scan recent bars (last 20)
    scan_start = max(0, len(df) - 20)
    ft_bars = cfg.acceptance_follow_through_bars

    for zone in zones[:10]:  # Check top 10 zones by strength
        zone_mid = (zone.low + zone.high) / 2

        for i in range(scan_start, len(df) - ft_bars):
            # --- Bullish Acceptance: close above resistance + follow-through ---
            if zone_mid > closes[i - 1] if i > 0 else True:
                if closes[i] > zone.high + buffer:
                    # Check follow-through
                    follow_through = all(
                        closes[i + k] > zone.high - buffer
                        for k in range(1, ft_bars + 1)
                        if i + k < len(df)
                    )
                    if follow_through:
                        events.append({
                            "type": "ACCEPTANCE",
                            "zone_low": zone.low,
                            "zone_high": zone.high,
                            "direction": "BULLISH",
                            "bar_index": i,
                            "description": f"Bullish acceptance above {zone.high:.2f} with {ft_bars}-bar follow-through",
                        })

            # --- Bearish Acceptance: close below support + follow-through ---
            if zone_mid < closes[i - 1] if i > 0 else True:
                if closes[i] < zone.low - buffer:
                    follow_through = all(
                        closes[i + k] < zone.low + buffer
                        for k in range(1, ft_bars + 1)
                        if i + k < len(df)
                    )
                    if follow_through:
                        events.append({
                            "type": "ACCEPTANCE",
                            "zone_low": zone.low,
                            "zone_high": zone.high,
                            "direction": "BEARISH",
                            "bar_index": i,
                            "description": f"Bearish acceptance below {zone.low:.2f} with {ft_bars}-bar follow-through",
                        })

            # --- Bullish Rejection: wick below support then close above ---
            if lows[i] < zone.low - buffer and closes[i] > zone.low:
                events.append({
                    "type": "REJECTION",
                    "zone_low": zone.low,
                    "zone_high": zone.high,
                    "direction": "BULLISH",
                    "bar_index": i,
                    "description": f"Bullish rejection (sweep below {zone.low:.2f}, close back above)",
                })

            # --- Bearish Rejection: wick above resistance then close below ---
            if highs[i] > zone.high + buffer and closes[i] < zone.high:
                events.append({
                    "type": "REJECTION",
                    "zone_low": zone.low,
                    "zone_high": zone.high,
                    "direction": "BEARISH",
                    "bar_index": i,
                    "description": f"Bearish rejection (sweep above {zone.high:.2f}, close back below)",
                })

    # Sort by recency
    events.sort(key=lambda e: e["bar_index"], reverse=True)

    last_acc = None
    last_rej = None
    for e in events:
        if e["type"] == "ACCEPTANCE" and last_acc is None:
            last_acc = e["description"]
        if e["type"] == "REJECTION" and last_rej is None:
            last_rej = e["description"]
        if last_acc and last_rej:
            break

    return {
        "events": events[:10],  # Keep top 10 most recent
        "last_acceptance": last_acc,
        "last_rejection": last_rej,
    }
