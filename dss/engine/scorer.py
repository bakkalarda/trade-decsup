"""Composite scoring engine.

Score = macro_score + flow_score + structure_score + phase_score + setup_quality_score

Ranges (section 9.2):
  macro:     -3..+3
  flow:      -3..+3
  structure: -3..+3
  phase:     -2..+2
  setup:      0..+3

Long alert:  total >= +7
Short alert: total <= -7
"""

from __future__ import annotations

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

    for setup in setups_raw:
        if isinstance(setup, dict):
            direction = setup.get("direction", "LONG")

            # Structure score based on zone proximity and trend alignment
            struct_score = _compute_structure_score(structure_data, direction, cfg)

            # Phase score based on stage alignment with setup type
            phase_score = _compute_phase_score(structure_data, setup, cfg)

            # Setup quality (already computed in detector, 0..3)
            setup_quality = setup.get("setup_quality_score", 0.0)

            # Direction sign: for shorts, invert macro and flow scores
            if direction == "SHORT":
                signed_macro = -macro_score
                signed_flow = -flow_score
                signed_struct = -struct_score
                signed_phase = -phase_score
            else:
                signed_macro = macro_score
                signed_flow = flow_score
                signed_struct = struct_score
                signed_phase = phase_score

            total = signed_macro + signed_flow + signed_struct + signed_phase + setup_quality

            setup["macro_score"] = round(signed_macro, 2)
            setup["flow_score"] = round(signed_flow, 2)
            setup["structure_score"] = round(signed_struct, 2)
            setup["phase_score"] = round(signed_phase, 2)
            setup["total_score"] = round(total, 2)

    return setups_raw


def _compute_macro_score(scan_result: dict, cfg: DSSConfig) -> float:
    """Derive macro score from macro state data.

    If no macro state data, default to 0 (neutral).
    """
    macro = scan_result.get("macro_state", {})
    if not macro:
        return 0.0

    regime = macro.get("directional_regime", "MIXED")
    tradeability = macro.get("tradeability", "NORMAL")
    sentiment = macro.get("sentiment_state", "NEUTRAL")
    cross_asset = macro.get("cross_asset_state", "MIXED")

    score = 0.0

    # Regime
    if regime == "RISK_ON":
        score += 1.5
    elif regime == "RISK_OFF":
        score -= 1.5

    # Cross-asset confirmation
    if cross_asset == "CONFIRMS_RISK_ON":
        score += 0.5
    elif cross_asset == "CONFIRMS_RISK_OFF":
        score -= 0.5

    # Sentiment modifier
    if sentiment == "RISK_SEEKING":
        score += 0.5
    elif sentiment == "RISK_AVERSE":
        score -= 0.5

    # Event penalty
    event_risk = macro.get("event_risk_state", "LOW")
    if event_risk == "HIGH":
        score -= 1.0
    elif event_risk == "ELEVATED":
        score -= 0.5

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

    # Crowding penalty
    crowding = flow_data.get("crowding_state", "CLEAN")
    if crowding == "EXTREME":
        score -= 1.0
    elif crowding == "HOT":
        score -= 0.5

    # Squeeze risk bonus (traps benefit from crowding)
    squeeze = flow_data.get("squeeze_risk", "LOW")
    if squeeze == "HIGH":
        score -= 0.5  # Risk of being wrong

    return clamp(score, -3.0, 3.0)


def _compute_structure_score(structure: dict, direction: str, cfg: DSSConfig) -> float:
    """Derive structure score from technical analysis."""
    if not structure:
        return 0.0

    score = 0.0
    trend = structure.get("trend", "SIDEWAYS")
    if isinstance(trend, dict):
        trend = trend.get("direction", "SIDEWAYS")

    # Trend alignment
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
        elif direction == "LONG" and stage in (3, 4):
            score -= 1.5  # Buying pullbacks in distribution/markdown
        elif direction == "SHORT" and stage in (1, 2):
            score -= 1.5  # Shorting rallies in accumulation/markup

    # Breakout + retest alignment
    elif setup_type == "B_BREAKOUT_RETEST":
        if direction == "LONG" and stage in (1, 2):
            score += 1.5
        elif direction == "SHORT" and stage in (3, 4):
            score += 1.5
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
        else:
            score += 0.5  # Some value but not ideal phase

    return clamp(score, -2.0, 2.0)
