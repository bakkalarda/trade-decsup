"""Flow feature engineering — z-scores, slopes, crowding metrics."""

from __future__ import annotations

import pandas as pd

from dss.utils.math import zscore_current, rolling_regression_slope
from dss.config import FlowGateConfig
from dss.models.flow_state import CrowdingState, SqueezeRisk


def compute_funding_z(funding_history: pd.DataFrame, lookback: int = 90) -> float:
    """Z-score of the latest funding rate."""
    if funding_history.empty or "funding_rate" not in funding_history.columns:
        return 0.0
    return zscore_current(funding_history["funding_rate"], lookback)


def compute_oi_z(oi_history: pd.Series, lookback: int = 90) -> float:
    """Z-score of the latest open interest."""
    if oi_history.empty:
        return 0.0
    return zscore_current(oi_history, lookback)


def compute_oi_impulse(oi_history: pd.Series, window: int = 5) -> float:
    """Short-term OI change (impulse)."""
    if len(oi_history) < window + 1:
        return 0.0
    return float(oi_history.iloc[-1] - oi_history.iloc[-window - 1])


def compute_reserve_slope(reserve_series: pd.Series, window: int = 14) -> float:
    """Rolling regression slope of exchange reserves."""
    if len(reserve_series) < window:
        return 0.0
    slopes = rolling_regression_slope(reserve_series, window)
    val = slopes.iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def classify_crowding(
    funding_z: float,
    oi_z: float,
    cfg: FlowGateConfig,
) -> CrowdingState:
    """Classify crowding state from funding and OI z-scores.

    Composite: abs(funding_z) + max(0, oi_z - 1) -> thresholds
    """
    composite = abs(funding_z) + max(0, oi_z - 1.0)

    if composite >= cfg.crowding_extreme:
        return CrowdingState.EXTREME
    elif composite >= cfg.crowding_hot:
        return CrowdingState.HOT
    elif composite >= cfg.crowding_warm:
        return CrowdingState.WARM
    else:
        return CrowdingState.CLEAN


def classify_squeeze_risk(
    funding_z: float,
    oi_z: float,
    direction: str,
    cfg: FlowGateConfig,
) -> SqueezeRisk:
    """Determine squeeze risk for a given direction.

    Short squeeze risk (for shorts): funding very negative + OI elevated
    Long squeeze risk (for longs): funding very positive + OI elevated
    """
    if direction == "SHORT":
        if funding_z <= cfg.funding_extreme_neg and oi_z >= cfg.oi_elevated_z:
            return SqueezeRisk.HIGH
        elif funding_z < -1.5 and oi_z >= cfg.oi_elevated_z:
            return SqueezeRisk.MED
    elif direction == "LONG":
        if funding_z >= cfg.funding_extreme_pos and oi_z >= cfg.oi_elevated_z:
            return SqueezeRisk.HIGH
        elif funding_z > 1.5 and oi_z >= cfg.oi_elevated_z:
            return SqueezeRisk.MED

    return SqueezeRisk.LOW
