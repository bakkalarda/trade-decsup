"""Setup detection — scan for Setup A/B/C candidates.

Setup A: Trend Pullback Continuation (Stage 2 long / Stage 4 short)
Setup B: Breakout + Retest (late Stage 1 -> 2 or Stage 3 -> 4)
Setup C: Wyckoff Trap Reversal (Spring / UTAD)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from dss.models.setup import Setup, SetupType, Direction, TradePlan
from dss.models.structure_state import SRZone
from dss.config import DSSConfig


def detect_setups(
    asset: str,
    df: pd.DataFrame,
    structure: dict,
    cfg: DSSConfig,
) -> list[Setup]:
    """Detect all valid setup candidates for an asset.

    Args:
        asset: Asset symbol (BTC, ETH, etc.)
        df: OHLCV DataFrame
        structure: Structure analysis dict from scan pipeline
        cfg: Full config

    Returns:
        List of Setup objects (not yet scored or vetoed)
    """
    if len(df) < 20 or not structure:
        return []

    setups: list[Setup] = []
    now = datetime.now(timezone.utc)
    current_price = float(df["close"].iloc[-1])
    current_atr = structure.get("atr", 0.0)
    stage_info = structure.get("stage", 1) if isinstance(structure.get("stage"), int) else 1

    # Extract stage from nested dict if needed
    if isinstance(structure.get("stage"), dict):
        stage_info = structure["stage"].get("stage", 1)

    trend = structure.get("trend", "SIDEWAYS")
    if isinstance(trend, dict):
        trend = trend.get("direction", "SIDEWAYS")

    zones = _parse_zones(structure.get("zones", []))
    wyckoff = structure.get("wyckoff_event", "NONE")
    if isinstance(wyckoff, dict):
        wyckoff = wyckoff.get("event", "NONE")

    acc_rej = structure.get("acceptance_rejection", {})
    events = acc_rej.get("events", []) if isinstance(acc_rej, dict) else []

    # Find nearest zones
    nearest_support = None
    nearest_resistance = None
    for z in zones:
        zmid = (z.low + z.high) / 2
        if zmid < current_price and (nearest_support is None or zmid > (nearest_support.low + nearest_support.high) / 2):
            nearest_support = z
        elif zmid >= current_price and (nearest_resistance is None or zmid < (nearest_resistance.low + nearest_resistance.high) / 2):
            nearest_resistance = z

    # --- Setup A: Trend Pullback Continuation ---
    setup_a = _detect_setup_a(
        asset, now, current_price, current_atr, stage_info, trend,
        nearest_support, nearest_resistance, df,
    )
    if setup_a:
        setups.append(setup_a)

    # --- Setup B: Breakout + Retest ---
    setup_b = _detect_setup_b(
        asset, now, current_price, current_atr, stage_info, trend,
        zones, events, df,
    )
    if setup_b:
        setups.append(setup_b)

    # --- Setup C: Wyckoff Trap Reversal ---
    setup_c = _detect_setup_c(
        asset, now, current_price, current_atr, stage_info, wyckoff,
        nearest_support, nearest_resistance, df,
    )
    if setup_c:
        setups.append(setup_c)

    return setups


def _detect_setup_a(
    asset: str, now: datetime, price: float, atr: float,
    stage: int, trend: str,
    support: SRZone | None, resistance: SRZone | None,
    df: pd.DataFrame,
) -> Setup | None:
    """Setup A — Trend Pullback Continuation.

    Long: Stage 2, pullback to strong support zone, volatility contracting
    Short: Stage 4, rally to strong resistance zone, volatility contracting
    """
    if atr <= 0:
        return None

    # Long pullback in Stage 2
    if stage == 2 and trend == "UP" and support:
        dist = (price - (support.low + support.high) / 2) / atr
        if 0 < dist < 2.0:  # Within 2 ATR of support
            stop = support.low - atr * 0.5
            t1 = resistance.high if resistance else price + atr * 3
            t2 = price + atr * 5 if resistance is None else None

            rr = (t1 - price) / (price - stop) if price > stop else 0
            quality = _assess_quality(support.strength, dist, rr)

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe="4h",
                setup_type=SetupType.PULLBACK,
                direction=Direction.LONG,
                zone_description=f"Support {support.low:.2f}-{support.high:.2f}",
                zone_low=support.low,
                zone_high=support.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"Stage 2 pullback to support",
                    f"Distance: {dist:.1f} ATR",
                    f"Zone strength: {support.strength:.2f}",
                    f"R:R to T1: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.LONG,
                    entry_zone_low=support.low,
                    entry_zone_high=support.high,
                    trigger_description="4H close reclaims zone + holds on retest",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="Trail by 4H structure after T1",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    # Short rally in Stage 4
    if stage == 4 and trend == "DOWN" and resistance:
        dist = ((resistance.low + resistance.high) / 2 - price) / atr
        if 0 < dist < 2.0:
            stop = resistance.high + atr * 0.5
            t1 = support.low if support else price - atr * 3
            t2 = price - atr * 5 if support is None else None

            rr = (price - t1) / (stop - price) if stop > price else 0
            quality = _assess_quality(resistance.strength, dist, rr)

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe="4h",
                setup_type=SetupType.PULLBACK,
                direction=Direction.SHORT,
                zone_description=f"Resistance {resistance.low:.2f}-{resistance.high:.2f}",
                zone_low=resistance.low,
                zone_high=resistance.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"Stage 4 rally to resistance",
                    f"Distance: {dist:.1f} ATR",
                    f"Zone strength: {resistance.strength:.2f}",
                    f"R:R to T1: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.SHORT,
                    entry_zone_low=resistance.low,
                    entry_zone_high=resistance.high,
                    trigger_description="4H close rejects zone + fails retest",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="Trail by 4H structure after T1",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    return None


def _detect_setup_b(
    asset: str, now: datetime, price: float, atr: float,
    stage: int, trend: str,
    zones: list[SRZone], events: list[dict],
    df: pd.DataFrame,
) -> Setup | None:
    """Setup B — Breakout + Retest (acceptance only).

    Long: Late Stage 1 -> 2, breakout above resistance + acceptance + retest
    Short: Stage 3 -> 4, breakdown below support + acceptance + retest
    """
    if atr <= 0:
        return None

    # Look for recent acceptance events
    bullish_acceptances = [e for e in events if e.get("type") == "ACCEPTANCE" and e.get("direction") == "BULLISH"]
    bearish_acceptances = [e for e in events if e.get("type") == "ACCEPTANCE" and e.get("direction") == "BEARISH"]

    # Bullish breakout + retest
    if bullish_acceptances and stage in (1, 2):
        acc = bullish_acceptances[0]  # Most recent
        zone_high = acc.get("zone_high", 0)
        zone_low = acc.get("zone_low", 0)

        # Price should be near or slightly above the broken zone (retest)
        dist_from_zone = (price - zone_high) / atr if atr > 0 else 99
        if -0.5 <= dist_from_zone <= 2.0:
            stop = zone_low - atr * 0.3
            t1 = price + atr * 3
            rr = (t1 - price) / (price - stop) if price > stop else 0
            quality = min(2.5, 1.0 + (0.5 if dist_from_zone < 1.0 else 0))

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe="4h",
                setup_type=SetupType.BREAKOUT_RETEST,
                direction=Direction.LONG,
                zone_description=f"Broken resistance {zone_low:.2f}-{zone_high:.2f}",
                zone_low=zone_low,
                zone_high=zone_high,
                setup_quality_score=quality,
                quality_reasons=[
                    "Bullish acceptance above resistance",
                    f"Retest distance: {dist_from_zone:.1f} ATR",
                    f"R:R: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.LONG,
                    entry_zone_low=zone_high - atr * 0.2,
                    entry_zone_high=zone_high + atr * 0.5,
                    trigger_description="4H close holds above broken zone on retest",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=price + atr * 5,
                    trail_method="Trail by 4H structure after T1",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    # Bearish breakout + retest
    if bearish_acceptances and stage in (3, 4):
        acc = bearish_acceptances[0]
        zone_high = acc.get("zone_high", 0)
        zone_low = acc.get("zone_low", 0)

        dist_from_zone = (zone_low - price) / atr if atr > 0 else 99
        if -0.5 <= dist_from_zone <= 2.0:
            stop = zone_high + atr * 0.3
            t1 = price - atr * 3
            rr = (price - t1) / (stop - price) if stop > price else 0
            quality = min(2.5, 1.0 + (0.5 if dist_from_zone < 1.0 else 0))

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe="4h",
                setup_type=SetupType.BREAKOUT_RETEST,
                direction=Direction.SHORT,
                zone_description=f"Broken support {zone_low:.2f}-{zone_high:.2f}",
                zone_low=zone_low,
                zone_high=zone_high,
                setup_quality_score=quality,
                quality_reasons=[
                    "Bearish acceptance below support",
                    f"Retest distance: {dist_from_zone:.1f} ATR",
                    f"R:R: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.SHORT,
                    entry_zone_low=zone_low - atr * 0.5,
                    entry_zone_high=zone_low + atr * 0.2,
                    trigger_description="4H close fails to reclaim broken zone on retest",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=price - atr * 5,
                    trail_method="Trail by 4H structure after T1",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    return None


def _detect_setup_c(
    asset: str, now: datetime, price: float, atr: float,
    stage: int, wyckoff: str,
    support: SRZone | None, resistance: SRZone | None,
    df: pd.DataFrame,
) -> Setup | None:
    """Setup C — Wyckoff Trap Reversal (Spring / UTAD)."""
    if atr <= 0:
        return None

    # Spring (long trap reversal)
    if wyckoff == "SPRING" and support:
        stop = support.low - atr * 1.0  # Below the spring low
        t1_zone = resistance if resistance else None
        t1 = t1_zone.low if t1_zone else price + atr * 3
        t2 = t1_zone.high if t1_zone else price + atr * 5

        rr = (t1 - price) / (price - stop) if price > stop else 0
        quality = min(3.0, 1.5 + (0.5 if stage in (1, 4) else 0))

        return Setup(
            timestamp_utc=now,
            asset=asset,
            timeframe="4h",
            setup_type=SetupType.WYCKOFF_TRAP,
            direction=Direction.LONG,
            zone_description=f"Spring below {support.low:.2f}",
            zone_low=support.low,
            zone_high=support.high,
            setup_quality_score=quality,
            quality_reasons=[
                "Wyckoff Spring (bear trap)",
                f"Stage {stage}",
                f"R:R: {rr:.1f}",
            ],
            trade_plan=TradePlan(
                direction=Direction.LONG,
                entry_zone_low=support.low,
                entry_zone_high=support.high,
                trigger_description="LPS retest holds inside range after spring",
                stop_loss=stop,
                target_1=t1,
                target_2=t2,
                trail_method="Trail if transitions to Stage 2",
                risk_reward_t1=round(rr, 2),
            ),
        )

    # UTAD (short trap reversal)
    if wyckoff == "UTAD" and resistance:
        stop = resistance.high + atr * 1.0
        t1_zone = support if support else None
        t1 = t1_zone.high if t1_zone else price - atr * 3
        t2 = t1_zone.low if t1_zone else price - atr * 5

        rr = (price - t1) / (stop - price) if stop > price else 0
        quality = min(3.0, 1.5 + (0.5 if stage in (3, 2) else 0))

        return Setup(
            timestamp_utc=now,
            asset=asset,
            timeframe="4h",
            setup_type=SetupType.WYCKOFF_TRAP,
            direction=Direction.SHORT,
            zone_description=f"UTAD above {resistance.high:.2f}",
            zone_low=resistance.low,
            zone_high=resistance.high,
            setup_quality_score=quality,
            quality_reasons=[
                "Wyckoff UTAD (bull trap)",
                f"Stage {stage}",
                f"R:R: {rr:.1f}",
            ],
            trade_plan=TradePlan(
                direction=Direction.SHORT,
                entry_zone_low=resistance.low,
                entry_zone_high=resistance.high,
                trigger_description="LPSY retest fails inside range after UTAD",
                stop_loss=stop,
                target_1=t1,
                target_2=t2,
                trail_method="Trail if transitions to Stage 4",
                risk_reward_t1=round(rr, 2),
            ),
        )

    return None


def _assess_quality(zone_strength: float, distance_atr: float, rr: float) -> float:
    """Assess setup quality score 0-3."""
    score = 0.0
    # Zone strength contribution
    score += min(zone_strength * 2, 1.0)
    # Distance (closer to zone = better)
    if distance_atr < 1.0:
        score += 1.0
    elif distance_atr < 1.5:
        score += 0.5
    # R:R contribution
    if rr >= 3.0:
        score += 1.0
    elif rr >= 2.0:
        score += 0.5
    return min(score, 3.0)


def _parse_zones(zones_raw: list) -> list[SRZone]:
    """Parse zone dicts into SRZone objects."""
    result = []
    for z in zones_raw:
        if isinstance(z, SRZone):
            result.append(z)
        elif isinstance(z, dict):
            result.append(SRZone(**z))
    return result
