"""Veto catalogue — hard-NO rules that override score.

Vetoes are survival rules. A veto kills a trade regardless of score.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from dss.config import DSSConfig
from dss.engine.event_calendar import EventCalendar

logger = logging.getLogger(__name__)

# Module-level singleton — loaded once
_event_calendar: EventCalendar | None = None


def _get_event_calendar() -> EventCalendar:
    global _event_calendar
    if _event_calendar is None:
        _event_calendar = EventCalendar()
    return _event_calendar


def apply_vetoes(
    setups: list[dict],
    scan_result: dict,
    cfg: DSSConfig,
) -> list[dict]:
    """Apply all veto rules to scored setups.

    Modifies setups in-place with vetoed=True and veto_reasons.
    """
    macro = scan_result.get("macro_state", {})
    flow = scan_result.get("flow", {})
    structure = scan_result.get("structure", {})

    # Parse bar timestamp for event calendar check
    bar_time_str = scan_result.get("timestamp_utc")
    bar_time = None
    if bar_time_str:
        try:
            bar_time = datetime.fromisoformat(bar_time_str)
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    for setup in setups:
        reasons = []

        direction = setup.get("direction", "LONG")
        setup_type = setup.get("setup_type", "")
        asset = setup.get("asset", "")

        # --- Macro vetoes ---
        reasons.extend(_macro_vetoes(macro, setup_type))

        # --- Flow/positioning vetoes ---
        reasons.extend(_flow_vetoes(flow, direction, asset, structure, cfg))

        # --- Structure vetoes ---
        reasons.extend(_structure_vetoes(structure, direction, setup_type))

        # --- Counter-trend short veto (HTF trend) ---
        reasons.extend(_counter_trend_vetoes(structure, direction, setup_type))

        # --- Regime-specific setup disabling ---
        reasons.extend(_regime_setup_vetoes(macro, setup_type, direction))

        # --- Asset-level vetoes ---
        # XAG (Silver): A_PULLBACK has 12% WR / PF 0.08 across all tested periods.
        # Silver's whipsaw price action defeats pullback detection.
        if asset == "XAG":
            reasons.append(
                f"ASSET_VETO: {asset} disabled "
                "(pullback setups incompatible with silver price action; "
                "12% WR, PF 0.08 in backtest)"
            )

        # --- Event calendar vetoes ---
        if bar_time and setup_type not in ("C_WYCKOFF_TRAP",):
            cal = _get_event_calendar()
            in_window, event_name = cal.is_in_event_window(bar_time)
            if in_window:
                reasons.append(f"EVENT_VETO: within event window for '{event_name}'")

        if reasons:
            setup["vetoed"] = True
            setup["veto_reasons"] = reasons
        else:
            setup["vetoed"] = False
            setup["veto_reasons"] = []

    return setups


def _macro_vetoes(macro: dict, setup_type: str) -> list[str]:
    """Macro gate vetoes."""
    reasons = []

    tradeability = macro.get("tradeability", "NORMAL")
    event_veto = macro.get("event_veto", False)

    # Hard veto: NO_TRADE kills all new entries
    if tradeability == "NO_TRADE":
        # Exception: post-event acceptance or Wyckoff trap
        if setup_type in ("B_BREAKOUT_RETEST", "C_WYCKOFF_TRAP"):
            event_window = macro.get("event_window", "NONE")
            if event_window == "POST":
                pass  # Allow post-event acceptance setups
            else:
                reasons.append(f"MACRO_NO_TRADE: tradeability={tradeability}")
        else:
            reasons.append(f"MACRO_NO_TRADE: tradeability={tradeability}")

    # Event veto (Tier-1 window active)
    if event_veto and setup_type not in ("B_BREAKOUT_RETEST", "C_WYCKOFF_TRAP"):
        reasons.append("MACRO_EVENT_VETO: Tier-1 event window active")

    # High headline risk
    headline_risk = macro.get("headline_risk_state", "LOW")
    if headline_risk == "HIGH":
        reasons.append(f"MACRO_HEADLINE_RISK: headline_risk={headline_risk}")

    return reasons


def _flow_vetoes(
    flow: dict, direction: str, asset: str, structure: dict, cfg: DSSConfig
) -> list[str]:
    """Flow/positioning gate vetoes."""
    reasons = []

    if not flow:
        return reasons

    crowding = flow.get("crowding_state", "CLEAN")
    funding_z = flow.get("funding_z", 0.0)
    oi_z = flow.get("oi_z", 0.0)
    inflow_z = flow.get("exch_inflow_z")
    liquidity = flow.get("liquidity_state", "GOOD")
    squeeze = flow.get("squeeze_risk", "LOW")

    # Check if at key structural level
    at_resistance = _at_resistance(structure)
    at_support = _at_support(structure)

    # --- Long vetoes ---
    if direction == "LONG":
        # Exchange inflow spike at resistance (BTC/ETH)
        if inflow_z is not None and inflow_z >= cfg.flow_gate.inflow_spike_z and at_resistance:
            reasons.append(
                f"FLOW_VETO_LONG: exchange inflow spike (z={inflow_z:.1f}) at resistance"
            )

        # Crowded long at resistance
        if crowding == "EXTREME" and funding_z > 0 and at_resistance:
            reasons.append(
                f"FLOW_VETO_LONG: EXTREME crowding (funding_z={funding_z:.1f}, "
                f"oi_z={oi_z:.1f}) buying resistance"
            )

    # --- Short vetoes ---
    if direction == "SHORT":
        # Crowded short at support (squeeze risk)
        if crowding == "EXTREME" and funding_z < 0 and at_support:
            reasons.append(
                f"FLOW_VETO_SHORT: EXTREME crowding (funding_z={funding_z:.1f}, "
                f"oi_z={oi_z:.1f}) shorting support — squeeze risk"
            )

    # --- Liquidity veto (both directions) ---
    if liquidity == "DISLOCATED":
        reasons.append("FLOW_VETO_LIQUIDITY: market liquidity dislocated")

    return reasons


def _structure_vetoes(structure: dict, direction: str, setup_type: str) -> list[str]:
    """Structure gate vetoes."""
    reasons = []

    if not structure:
        return reasons

    stage = structure.get("stage", 1)
    if isinstance(stage, dict):
        stage = stage.get("stage", 1)

    # Psychology vetoes from spec section 7.4
    # Do not short Stage 2 unless UTAD/distribution confirmed
    if direction == "SHORT" and stage == 2:
        wyckoff = structure.get("wyckoff_event", "NONE")
        if isinstance(wyckoff, dict):
            wyckoff = wyckoff.get("event", "NONE")
        if wyckoff not in ("UTAD", "LPSY"):
            reasons.append(
                "STRUCTURE_VETO: shorting Stage 2 without UTAD/distribution confirmation"
            )

    # Do not buy Stage 4 unless Spring/capitulation confirmed
    if direction == "LONG" and stage == 4:
        wyckoff = structure.get("wyckoff_event", "NONE")
        if isinstance(wyckoff, dict):
            wyckoff = wyckoff.get("event", "NONE")
        if wyckoff not in ("SPRING", "LPS"):
            reasons.append(
                "STRUCTURE_VETO: buying Stage 4 without Spring/capitulation confirmation"
            )

    # Middle-of-range trades (no acceptance/rejection event)
    acc_rej = structure.get("acceptance_rejection", {})
    events = acc_rej.get("events", []) if isinstance(acc_rej, dict) else []
    if setup_type == "B_BREAKOUT_RETEST" and not events:
        reasons.append("STRUCTURE_VETO: breakout/retest without acceptance event")

    return reasons


def _at_resistance(structure: dict) -> bool:
    """Check if price is near resistance."""
    dist = structure.get("distance_to_resistance_atr")
    if dist is not None and dist < 1.0:
        return True
    # Fallback: check atr_percentile position
    return False


def _at_support(structure: dict) -> bool:
    """Check if price is near support."""
    dist = structure.get("distance_to_support_atr")
    if dist is not None and dist < 1.0:
        return True
    return False


def _counter_trend_vetoes(structure: dict, direction: str, setup_type: str) -> list[str]:
    """Veto shorts in a confirmed uptrend (and longs in confirmed downtrend).

    Exception: C_WYCKOFF_TRAP is allowed counter-trend (it IS the reversal).
    """
    reasons = []
    if not structure:
        return reasons

    trend = structure.get("trend", "SIDEWAYS")
    if isinstance(trend, dict):
        trend_dir = trend.get("direction", "SIDEWAYS")
        trend_conf = trend.get("confidence", 0.5)
    else:
        trend_dir = trend
        trend_conf = 0.5

    # Exempt Wyckoff traps — they ARE counter-trend by design
    if setup_type in ("C_WYCKOFF_TRAP", "WYCKOFF_TRAP"):
        return reasons

    # Veto shorts in confirmed uptrend
    if direction == "SHORT" and trend_dir == "UP" and trend_conf >= 0.6:
        reasons.append(
            f"COUNTER_TREND_VETO: shorting in confirmed uptrend "
            f"(trend={trend_dir}, conf={trend_conf:.0%})"
        )

    # Veto longs in confirmed downtrend
    if direction == "LONG" and trend_dir == "DOWN" and trend_conf >= 0.6:
        # Slightly more lenient for longs — only veto with very strong downtrend
        if trend_conf >= 0.7:
            reasons.append(
                f"COUNTER_TREND_VETO: buying in confirmed downtrend "
                f"(trend={trend_dir}, conf={trend_conf:.0%})"
            )

    return reasons


def _regime_setup_vetoes(macro: dict, setup_type: str, direction: str) -> list[str]:
    """Disable certain setup types in specific macro regimes.

    - E_RANGE_TRADE: disabled in RISK_OFF (ranges break in panic)
    - E_RANGE_TRADE SHORT: disabled entirely (historically unprofitable)
    - D_MAMIS_TRANSITION SHORT: disabled in RISK_ON (counter-trend mean-reversion
      against a strong bull trend is low-probability)
    """
    reasons = []
    regime = macro.get("directional_regime", "MIXED") if macro else "MIXED"

    # E_RANGE_TRADE is unprofitable in trending/volatile regimes
    if setup_type in ("E_RANGE_TRADE", "RANGE_TRADE"):
        if regime == "RISK_OFF":
            reasons.append(
                f"REGIME_VETO: {setup_type} disabled in RISK_OFF "
                "(ranges break in high-volatility regimes)"
            )
        # Range trade shorts have been universally poor
        if direction == "SHORT":
            reasons.append(
                f"REGIME_VETO: {setup_type} SHORT disabled (historically unprofitable)"
            )

    # D_MAMIS_TRANSITION: removed as standalone entry in v8.
    # MAMIS phases are now confirmation bonuses on all OTE entries.
    # No veto needed since D is no longer generated.

    # B_BREAKOUT_RETEST: v8 tested — still only produced losers.
    # Keep vetoed until pattern detection is fundamentally improved.
    if setup_type in ("B_BREAKOUT_RETEST", "BREAKOUT_RETEST"):
        reasons.append(
            f"REGIME_VETO: {setup_type} disabled — v8 test still unprofitable"
        )

    # C_WYCKOFF_TRAP: v7 enhanced with strict stage gating (Stage 1/4 only
    # for springs, 2/3 for UTADs), reclaim candle requirement, volume
    # divergence check, wider 1.5 ATR stop. Re-enabled — net +8.50% across portfolio.

    return reasons
