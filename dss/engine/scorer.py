"""Composite scoring engine.

Score = macro + flow + structure + phase + setup_quality + options + mamis + mtf_confluence

Ranges:
  macro:     -3..+3
  flow:      -3..+3
  structure: -3..+3
  phase:     -2..+2
  setup:      0..+3
  options:   -2..+2
  mamis:     -2..+2
  mtf:       -1..+1  (bonus/penalty for HTF alignment)

Long alert:  total >= +6
Short alert: total <= -6
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dss.config import DSSConfig
from dss.utils.math import clamp


def score_setups(
    setups_raw: list[dict],
    scan_result: dict,
    cfg: DSSConfig,
) -> list[dict]:
    """Score each setup candidate using all gate outputs.

    Modifies setups in-place with score fields and returns the list.
    """
    # Extract gate scores from scan result
    flow_data = scan_result.get("flow", {})
    structure_data = scan_result.get("structure", {})

    # Macro score — computed from macro state if available
    macro_score = _compute_macro_score(scan_result, cfg)

    # Flow score
    flow_score = _compute_flow_score(flow_data, cfg)

    # Options score
    options_score = _compute_options_score(scan_result, cfg)

    # Mamis score
    mamis_score = _compute_mamis_score(scan_result)

    # MTF confluence — higher-timeframe trend from OHLCV
    ohlcv = scan_result.get("ohlcv")  # DataFrame if available
    htf_trend = _compute_htf_trend(ohlcv) if ohlcv is not None else "SIDEWAYS"

    for setup in setups_raw:
        if isinstance(setup, dict):
            direction = setup.get("direction", "LONG")

            # Structure score based on zone proximity and trend alignment
            setup_type_str = setup.get("setup_type", "")
            struct_score = _compute_structure_score(structure_data, direction, cfg, setup_type_str)

            # Phase score based on stage alignment with setup type
            phase_score = _compute_phase_score(structure_data, setup, cfg)

            # Setup quality (already computed in detector, 0..3)
            setup_quality = setup.get("setup_quality_score", 0.0)

            # MTF confluence bonus/penalty
            mtf_score = _compute_mtf_confluence(htf_trend, direction)

            # Direction sign: for shorts, invert only ENVIRONMENT scores
            if direction == "SHORT":
                signed_macro = -macro_score
                signed_flow = -flow_score
                signed_options = -options_score
                signed_mamis = -mamis_score
            else:
                signed_macro = macro_score
                signed_flow = flow_score
                signed_options = options_score
                signed_mamis = mamis_score

            # Structure, phase, and MTF are already direction-aware
            signed_struct = struct_score
            signed_phase = phase_score

            total = (signed_macro + signed_flow + signed_struct +
                     signed_phase + setup_quality + signed_options + signed_mamis + mtf_score)

            setup["macro_score"] = round(signed_macro, 2)
            setup["flow_score"] = round(signed_flow, 2)
            setup["structure_score"] = round(signed_struct, 2)
            setup["phase_score"] = round(signed_phase, 2)
            setup["options_score"] = round(signed_options, 2)
            setup["mamis_score"] = round(signed_mamis, 2)
            setup["mtf_score"] = round(mtf_score, 2)
            setup["total_score"] = round(total, 2)

    return setups_raw


# ---------------------------------------------------------------------------
# Multi-timeframe helpers
# ---------------------------------------------------------------------------

def _compute_htf_trend(ohlcv: pd.DataFrame | None) -> str:
    """Compute higher-timeframe trend from 4h OHLCV by resampling to daily.

    Uses 50-period and 200-period SMA on daily bars.
    Returns: 'UP', 'DOWN', or 'SIDEWAYS'
    """
    if ohlcv is None or len(ohlcv) < 200:
        return "SIDEWAYS"

    try:
        daily = ohlcv["close"].resample("1D").last().dropna()
        if len(daily) < 50:
            return "SIDEWAYS"

        sma50 = daily.rolling(50).mean().iloc[-1]
        sma200 = daily.rolling(200).mean().iloc[-1] if len(daily) >= 200 else sma50
        last_close = daily.iloc[-1]

        # Up: close > sma50 > sma200
        if last_close > sma50 and sma50 > sma200:
            return "UP"
        # Down: close < sma50 < sma200
        elif last_close < sma50 and sma50 < sma200:
            return "DOWN"
        return "SIDEWAYS"
    except Exception:
        return "SIDEWAYS"


def _compute_mtf_confluence(htf_trend: str, direction: str) -> float:
    """Return MTF confluence score (-1..+1).

    +1: trade direction aligned with higher-timeframe trend
    -1: trade direction against higher-timeframe trend
     0: HTF is sideways (neutral)
    """
    if htf_trend == "SIDEWAYS":
        return 0.0
    if (direction == "LONG" and htf_trend == "UP") or (direction == "SHORT" and htf_trend == "DOWN"):
        return 1.0
    if (direction == "LONG" and htf_trend == "DOWN") or (direction == "SHORT" and htf_trend == "UP"):
        return -1.0
    return 0.0


def _compute_macro_score(scan_result: dict, cfg: DSSConfig) -> float:
    """Derive macro score from macro state data.

    If the macro state already contains a pre-computed macro_score (e.g.
    from the backtest replay which includes SPX/VIX), use it directly.
    Otherwise recompute from the state fields.

    Score is symmetric: positive = risk-on (bullish), negative = risk-off.
    """
    macro = scan_result.get("macro_state", {})
    if not macro:
        return 0.0

    # Use pre-computed score if available (includes SPX/VIX effects)
    # Dampen macro weight: macro data is lagging and inversely correlated
    # with short-term crypto moves. Cap at ±1.5 and apply a fade factor
    # when the signal is extreme (contrarian damping).
    if "macro_score" in macro:
        raw = float(macro["macro_score"])
        # Extreme macro readings (|raw| > 2.0) are often peak-lagging;
        # fade them toward zero by 40%
        if abs(raw) > 2.0:
            raw *= 0.6
        return clamp(raw, -1.5, 1.5)

    regime = macro.get("directional_regime", "MIXED")
    sentiment = macro.get("sentiment_state", "NEUTRAL")
    cross_asset = macro.get("cross_asset_state", "MIXED")

    score = 0.0

    # Regime (symmetric)
    if regime == "RISK_ON":
        score += 1.5
    elif regime == "RISK_OFF":
        score -= 1.5

    # Cross-asset confirmation (symmetric)
    if cross_asset == "CONFIRMS_RISK_ON":
        score += 0.5
    elif cross_asset == "CONFIRMS_RISK_OFF":
        score -= 0.5

    # Sentiment modifier (symmetric)
    if sentiment == "RISK_SEEKING":
        score += 0.5
    elif sentiment == "RISK_AVERSE":
        score -= 0.5

    # Event risk — reduces magnitude toward 0 (symmetric: dampens both directions)
    event_risk = macro.get("event_risk_state", "LOW")
    if event_risk == "HIGH":
        score *= 0.5    # Halve whatever score exists
    elif event_risk == "ELEVATED":
        score *= 0.75   # Dampen

    return clamp(score, -3.0, 3.0)


def _compute_flow_score(flow_data: dict, cfg: DSSConfig) -> float:
    """Derive flow score from flow gate output."""
    if not flow_data:
        return 0.0

    score = 0.0
    flow_bias = flow_data.get("flow_bias", "NEUTRAL")

    if flow_bias == "BULLISH":
        score += 1.5
    elif flow_bias == "BEARISH":
        score -= 1.5

    # Crowding penalizes the CROWDED direction, not both:
    # Positive funding = longs crowded → subtract (penalize bullish bias)
    # Negative funding = shorts crowded → add (penalize bearish bias)
    crowding = flow_data.get("crowding_state", "CLEAN")
    funding_z = flow_data.get("funding_z", 0.0)
    if crowding == "EXTREME":
        if funding_z > 0:
            score -= 1.0   # Crowded long → penalize bullish
        elif funding_z < 0:
            score += 1.0   # Crowded short → penalize bearish
    elif crowding == "HOT":
        if funding_z > 0:
            score -= 0.5
        elif funding_z < 0:
            score += 0.5

    # Squeeze risk — penalizes the squeezed direction
    squeeze = flow_data.get("squeeze_risk", "LOW")
    if squeeze == "HIGH":
        if funding_z > 0:
            score -= 0.5   # Long squeeze risk
        elif funding_z < 0:
            score += 0.5   # Short squeeze risk

    return clamp(score, -3.0, 3.0)


def _compute_structure_score(structure: dict, direction: str, cfg: DSSConfig,
                              setup_type: str = "") -> float:
    """Derive structure score from technical analysis."""
    if not structure:
        return 0.0

    score = 0.0
    trend = structure.get("trend", "SIDEWAYS")
    if isinstance(trend, dict):
        trend = trend.get("direction", "SIDEWAYS")

    is_range_trade = setup_type == "E_RANGE_TRADE"

    # Trend alignment — range trades WANT sideways, trending setups want direction
    if is_range_trade:
        # Range trades benefit from sideways conditions
        if trend == "SIDEWAYS":
            score += 1.5  # Ideal for range
        elif (direction == "LONG" and trend == "UP") or (direction == "SHORT" and trend == "DOWN"):
            score += 0.5  # Trend-aligned range trade is okay
        else:
            score -= 0.5  # Counter-trend range trade is risky
    else:
        # Standard trending logic
        if direction == "LONG" and trend == "UP":
            score += 1.5
        elif direction == "SHORT" and trend == "DOWN":
            score += 1.5
        elif trend == "SIDEWAYS":
            score += 0.0
        else:
            score -= 1.0  # Counter-trend

    # Zone quality
    zones = structure.get("zones", [])
    if zones:
        best_strength = zones[0].get("strength", 0) if isinstance(zones[0], dict) else 0
        score += min(best_strength * 1.5, 1.5)

    return clamp(score, -3.0, 3.0)


def _compute_phase_score(structure: dict, setup: dict, cfg: DSSConfig) -> float:
    """Score phase alignment (stage + Wyckoff vs setup type)."""
    if not structure:
        return 0.0

    stage = structure.get("stage", 1)
    if isinstance(stage, dict):
        stage = stage.get("stage", 1)

    setup_type = setup.get("setup_type", "")
    direction = setup.get("direction", "LONG")

    score = 0.0

    # Pullback continuation alignment
    if setup_type == "A_PULLBACK":
        if direction == "LONG" and stage == 2:
            score += 2.0
        elif direction == "SHORT" and stage == 4:
            score += 2.0
        elif direction == "LONG" and stage == 1:
            score += 1.0  # Accumulation pullback is decent
        elif direction == "SHORT" and stage == 3:
            score += 1.0  # Distribution rally is decent
        elif direction == "LONG" and stage in (3, 4):
            score -= 1.5  # Buying pullbacks in distribution/markdown
        elif direction == "SHORT" and stage in (1, 2):
            score -= 1.5  # Shorting rallies in accumulation/markup
        else:
            score += 0.0  # Sideways / neutral

    # Breakout + retest alignment
    elif setup_type == "B_BREAKOUT_RETEST":
        if direction == "LONG" and stage in (1, 2):
            score += 1.5
        elif direction == "SHORT" and stage in (3, 4):
            score += 1.5
        elif direction == "LONG" and stage == 3:
            score += 0.5  # Breakout from distribution can work
        elif direction == "SHORT" and stage == 1:
            score += 0.5
        else:
            score -= 1.0

    # Wyckoff trap alignment
    elif setup_type == "C_WYCKOFF_TRAP":
        wyckoff = structure.get("wyckoff_event", "NONE")
        if isinstance(wyckoff, dict):
            wyckoff = wyckoff.get("event", "NONE")

        if direction == "LONG" and wyckoff == "SPRING" and stage in (1, 4):
            score += 2.0
        elif direction == "SHORT" and wyckoff == "UTAD" and stage in (3, 2):
            score += 2.0
        elif (direction == "LONG" and wyckoff == "SPRING") or (direction == "SHORT" and wyckoff == "UTAD"):
            score += 1.0  # Right event but sub-optimal stage
        else:
            score -= 0.5  # No confirmed Wyckoff event — penalize instead of rewarding

    # Mamis transition alignment
    elif setup_type == "D_MAMIS_TRANSITION":
        if direction == "LONG" and stage in (1, 4):
            score += 1.5  # Accumulation / markdown bottom
        elif direction == "SHORT" and stage in (2, 3):
            score += 1.5  # Markup top / distribution
        else:
            score += 0.5

    # Range trade alignment — best in Stage 1 or 3 (sideways phases)
    elif setup_type == "E_RANGE_TRADE":
        if stage in (1, 3):
            score += 2.0  # Perfect phase for range trading
        elif stage == 2 and direction == "LONG":
            score += 1.0  # Range trade in markup is okay for longs
        elif stage == 4 and direction == "SHORT":
            score += 1.0  # Range trade in markdown is okay for shorts
        else:
            score -= 0.5  # Trending phases are unfriendly to range trades

    # Divergence reversal — works in late-stage phases (reversals at extremes)
    elif setup_type == "F_DIVERGENCE_REVERSAL":
        if direction == "LONG" and stage in (4, 1):
            score += 2.0  # Late markdown / early accumulation — ideal
        elif direction == "SHORT" and stage in (2, 3):
            score += 2.0  # Late markup / early distribution — ideal
        elif direction == "LONG" and stage == 3:
            score += 0.5  # Distribution can have support bounces
        elif direction == "SHORT" and stage == 1:
            score += 0.5  # Accumulation can have resistance fails
        else:
            score -= 0.5  # Against the phase

    # Volume climax — works in any phase but best at extremes
    elif setup_type == "G_VOLUME_CLIMAX":
        if direction == "LONG" and stage in (4, 1):
            score += 2.0  # Capitulation at bottom
        elif direction == "SHORT" and stage in (2, 3):
            score += 2.0  # Blow-off top
        elif direction == "LONG" and stage == 2:
            score += 1.0  # Shakeout in uptrend
        elif direction == "SHORT" and stage == 4:
            score += 1.0  # Relief rally exhaustion
        else:
            score += 0.0  # Neutral

    # Fibonacci OTE — best in clearly trending phases
    elif setup_type == "H_FIB_OTE":
        if direction == "LONG" and stage == 2:
            score += 2.0  # Perfect: OTE retracement in markup
        elif direction == "SHORT" and stage == 4:
            score += 2.0  # Perfect: OTE retracement in markdown
        elif direction == "LONG" and stage == 1:
            score += 1.0  # OTE in accumulation is decent
        elif direction == "SHORT" and stage == 3:
            score += 1.0  # OTE in distribution is decent
        elif direction == "LONG" and stage in (3, 4):
            score -= 1.0  # OTE against the trend
        elif direction == "SHORT" and stage in (1, 2):
            score -= 1.0  # OTE against the trend
        else:
            score += 0.5  # Neutral

    return clamp(score, -2.0, 2.0)


def _compute_options_score(scan_result: dict, cfg: DSSConfig) -> float:
    """Derive options score from options gate output (-2..+2)."""
    options = scan_result.get("options", {})
    if not options:
        return 0.0
    return clamp(options.get("options_score", 0.0), -2.0, 2.0)


def _compute_mamis_score(scan_result: dict) -> float:
    """Derive Mamis cycle score (-2..+2)."""
    mamis = scan_result.get("mamis", {})
    if not mamis:
        return 0.0
    return clamp(mamis.get("score", 0.0), -2.0, 2.0)
