"""S/R zone builder — cluster-based zone detection from pivots and consolidation edges."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dss.models.structure_state import SRZone, SwingPivot
from dss.config import StructureGateConfig


def build_sr_zones(
    df: pd.DataFrame,
    pivots: list[SwingPivot],
    atr_series: pd.Series,
    cfg: StructureGateConfig,
) -> list[SRZone]:
    """Build S/R zones from pivot clusters.

    1. Collect all pivot prices.
    2. Cluster nearby pivots (within ATR * multiplier).
    3. Score each zone by touches, displacement, time separation, recency.
    4. Return sorted by strength (strongest first).
    """
    if not pivots or atr_series.empty:
        return []

    current_atr = float(atr_series.iloc[-1])
    if current_atr <= 0:
        return []

    cluster_dist = current_atr * cfg.zone_cluster_atr_multiplier
    total_bars = len(df)

    # Sort pivot prices
    pivot_prices = sorted([(p.price, p.bar_index, p.is_high) for p in pivots], key=lambda x: x[0])

    # Cluster
    zones: list[SRZone] = []
    used = set()

    for i, (price, bar_idx, is_high) in enumerate(pivot_prices):
        if i in used:
            continue

        cluster_prices = [price]
        cluster_bars = [bar_idx]
        cluster_types = [is_high]
        used.add(i)

        for j in range(i + 1, len(pivot_prices)):
            if j in used:
                continue
            if abs(pivot_prices[j][0] - price) <= cluster_dist:
                cluster_prices.append(pivot_prices[j][0])
                cluster_bars.append(pivot_prices[j][1])
                cluster_types.append(pivot_prices[j][2])
                used.add(j)
            elif pivot_prices[j][0] - price > cluster_dist:
                break  # Sorted, no more in range

        if len(cluster_prices) < cfg.zone_min_touches:
            continue

        zone_low = min(cluster_prices) - current_atr * 0.1
        zone_high = max(cluster_prices) + current_atr * 0.1
        touches = len(cluster_prices)

        # Displacement: average price move away from zone
        displacement = _calc_displacement(df, zone_low, zone_high, cluster_bars)

        # Time separation: max distance between touches (in bars)
        cluster_bars_sorted = sorted(cluster_bars)
        time_sep = (cluster_bars_sorted[-1] - cluster_bars_sorted[0]) / max(total_bars, 1)

        # Recency: how close is the most recent touch to the current bar
        recency = 1.0 - (total_bars - max(cluster_bars)) / max(total_bars, 1)
        recency = max(0.0, recency)

        # Weighted strength
        w = cfg.zone_strength_weights
        strength = (
            w["touches"] * min(touches / 5.0, 1.0) +
            w["displacement"] * min(displacement, 1.0) +
            w["time_separation"] * time_sep +
            w["recency"] * recency
        )

        # Determine if support or resistance relative to current price
        # NOTE: This classification is re-evaluated at query time by
        # find_nearest_zones(). Setting it here is a convenience default.
        # We intentionally use the CURRENT price (last bar) which is valid
        # because zones are rebuilt every eval bar with the growing window.
        current_price = float(df["close"].iloc[-1])
        is_support = ((zone_low + zone_high) / 2) < current_price

        zones.append(SRZone(
            low=round(zone_low, 6),
            high=round(zone_high, 6),
            strength=round(strength, 3),
            touches=touches,
            is_support=is_support,
            last_touch_bar=max(cluster_bars),
        ))

    # Sort by strength descending
    zones.sort(key=lambda z: z.strength, reverse=True)
    return zones


def _calc_displacement(
    df: pd.DataFrame, zone_low: float, zone_high: float, touch_bars: list[int]
) -> float:
    """Calculate average displacement (move away) after zone touches.

    Uses only bars that exist within the provided window — no lookahead
    bias since the window grows as the backtest progresses.
    """
    if not touch_bars:
        return 0.0

    displacements = []
    closes = df["close"].values
    total_bars = len(closes)
    zone_mid = (zone_low + zone_high) / 2

    for bar in touch_bars:
        # Look ahead up to 5 bars for displacement (capped at available data)
        ahead = min(bar + 5, total_bars)
        if bar + 1 >= total_bars:
            continue
        max_move = 0.0
        for k in range(bar + 1, ahead):
            move = abs(closes[k] - zone_mid) / max(zone_mid, 1e-10)
            max_move = max(max_move, move)
        displacements.append(max_move)

    return float(np.mean(displacements)) if displacements else 0.0


def find_nearest_zones(
    zones: list[SRZone], current_price: float
) -> tuple[SRZone | None, SRZone | None]:
    """Find nearest support and resistance zones relative to current price.

    Returns (nearest_support, nearest_resistance).
    """
    support = None
    resistance = None
    min_sup_dist = float("inf")
    min_res_dist = float("inf")

    for z in zones:
        zone_mid = (z.low + z.high) / 2
        if zone_mid < current_price:
            dist = current_price - zone_mid
            if dist < min_sup_dist:
                min_sup_dist = dist
                support = z
        else:
            dist = zone_mid - current_price
            if dist < min_res_dist:
                min_res_dist = dist
                resistance = z

    return support, resistance
