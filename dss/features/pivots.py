"""Swing pivot detection — fractal-based pivot highs and lows."""

from __future__ import annotations

import pandas as pd

from dss.models.structure_state import SwingPivot


def detect_swing_pivots(
    df: pd.DataFrame,
    lookback: int = 5,
) -> list[SwingPivot]:
    """Detect fractal swing pivots.

    A swing high requires *lookback* bars on each side with lower highs.
    A swing low requires *lookback* bars on each side with higher lows.

    Returns a list of SwingPivot sorted by bar_index.
    """
    if len(df) < 2 * lookback + 1:
        return []

    highs = df["high"].values
    lows = df["low"].values
    timestamps = df.index
    pivots: list[SwingPivot] = []

    for i in range(lookback, len(df) - lookback):
        # Check swing high
        is_swing_high = True
        for j in range(1, lookback + 1):
            if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                is_swing_high = False
                break

        if is_swing_high:
            pivots.append(SwingPivot(
                bar_index=i,
                price=float(highs[i]),
                is_high=True,
                timestamp=timestamps[i],
            ))

        # Check swing low
        is_swing_low = True
        for j in range(1, lookback + 1):
            if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                is_swing_low = False
                break

        if is_swing_low:
            pivots.append(SwingPivot(
                bar_index=i,
                price=float(lows[i]),
                is_high=False,
                timestamp=timestamps[i],
            ))

    # Sort by bar_index
    pivots.sort(key=lambda p: p.bar_index)
    return pivots


def get_recent_swing_highs(pivots: list[SwingPivot], n: int = 5) -> list[SwingPivot]:
    """Return the N most recent swing highs."""
    highs = [p for p in pivots if p.is_high]
    return highs[-n:] if len(highs) >= n else highs


def get_recent_swing_lows(pivots: list[SwingPivot], n: int = 5) -> list[SwingPivot]:
    """Return the N most recent swing lows."""
    lows = [p for p in pivots if not p.is_high]
    return lows[-n:] if len(lows) >= n else lows
