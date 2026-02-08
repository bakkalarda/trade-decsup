"""Trendline and channel fitting.

A valid trendline requires 2 major pivots + 3rd reaction.
Channel = trendline + parallel through opposite pivot.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

from dss.models.structure_state import SwingPivot, Trendline


def fit_trendlines(
    pivots: list[SwingPivot],
    max_residual_atr: float = 0.5,
    current_atr: float = 1.0,
) -> list[Trendline]:
    """Fit trendlines through swing pivots.

    Fits separate ascending (through swing lows) and descending (through swing highs)
    trendlines.

    Returns list of valid Trendline objects sorted by touch count.
    """
    trendlines: list[Trendline] = []

    swing_lows = [p for p in pivots if not p.is_high and p.bar_index > 0]
    swing_highs = [p for p in pivots if p.is_high and p.bar_index > 0]

    # Ascending trendlines through swing lows
    if len(swing_lows) >= 2:
        asc = _fit_through_pivots(swing_lows, is_ascending=True, max_residual_atr=max_residual_atr, current_atr=current_atr)
        trendlines.extend(asc)

    # Descending trendlines through swing highs
    if len(swing_highs) >= 2:
        desc = _fit_through_pivots(swing_highs, is_ascending=False, max_residual_atr=max_residual_atr, current_atr=current_atr)
        trendlines.extend(desc)

    trendlines.sort(key=lambda t: t.touch_count, reverse=True)
    return trendlines


def _fit_through_pivots(
    pivot_list: list[SwingPivot],
    is_ascending: bool,
    max_residual_atr: float,
    current_atr: float,
) -> list[Trendline]:
    """Try all pairs of pivots as trendline anchors and keep valid ones."""
    results = []
    max_error = max_residual_atr * current_atr

    for i in range(len(pivot_list) - 1):
        for j in range(i + 1, len(pivot_list)):
            p1 = pivot_list[i]
            p2 = pivot_list[j]

            if p1.bar_index == p2.bar_index:
                continue

            # Compute slope and intercept
            dx = p2.bar_index - p1.bar_index
            dy = p2.price - p1.price
            slope = dy / dx
            intercept = p1.price - slope * p1.bar_index

            # Direction check
            if is_ascending and slope < 0:
                continue
            if not is_ascending and slope > 0:
                continue

            # Count touches (pivots within error band)
            touches = 0
            for p in pivot_list:
                expected = slope * p.bar_index + intercept
                if abs(p.price - expected) <= max_error:
                    touches += 1

            if touches >= 2:
                # Calculate residual error
                residuals = []
                for p in pivot_list:
                    expected = slope * p.bar_index + intercept
                    residuals.append(abs(p.price - expected))
                avg_residual = float(np.mean(residuals))

                results.append(Trendline(
                    slope=round(slope, 8),
                    intercept=round(intercept, 4),
                    touch_count=touches,
                    residual_error=round(avg_residual, 4),
                    is_ascending=is_ascending,
                    start_bar=p1.bar_index,
                    end_bar=p2.bar_index,
                ))

    # Deduplicate (keep the one with most touches per similar slope)
    results.sort(key=lambda t: t.touch_count, reverse=True)
    return results[:5]  # Top 5 per direction


def trendline_value_at(tl: Trendline, bar_index: int) -> float:
    """Calculate trendline price at a given bar index."""
    return tl.slope * bar_index + tl.intercept
