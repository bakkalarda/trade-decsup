"""Setup detection — scan for Setup A/B/C/E/F/G/H candidates.

Architecture (v8):
  Entry triggers (Optimal Trade Entries — OTE):
    A: Trend Pullback Continuation (trending conditions near S/R)
    B: Breakout + Retest (acceptance of key zone)
    C: Wyckoff Trap Reversal (Spring / UTAD — false breakout at S/R)
    E: Range Trade (buy range low / sell range high in defined range)
    F: RSI Divergence Reversal at S/R zones
    G: Volume Climax Reversal (exhaustion spike at key zone)
    H: Fibonacci OTE (retracement to 61.8%-78.6% golden pocket in trend)

  Confirmation layers (boost quality on ALL setups):
    - MAMIS cycle phase alignment → +0.3 to +0.5 quality bonus
    - Wyckoff event alignment → +0.3 quality bonus
    (D_MAMIS_TRANSITION removed as standalone entry; MAMIS is now purely a
     confirmation that enriches all OTE-based entries.)
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from dss.models.setup import Setup, SetupType, Direction, TradePlan
from dss.models.structure_state import SRZone
from dss.config import DSSConfig
from dss.utils.math import rsi, rsi_divergence


# ---------------------------------------------------------------------------
# Fibonacci OTE zone check (confirmation — not standalone entry)
# ---------------------------------------------------------------------------

def _fib_ote_confluence(
    price: float, atr: float, direction: str,
    df: pd.DataFrame,
) -> tuple[bool, str]:
    """Check if current price is near a Fibonacci OTE zone (61.8%-78.6%).

    Used as a confirmation bonus on existing OTE-based entries (A, E),
    not as a standalone setup trigger.

    Returns:
        (in_ote: bool, description: str)
    """
    if atr <= 0 or len(df) < 50:
        return False, ""

    highs = df["high"].values
    lows = df["low"].values
    lookback = min(60, len(df) - 5)

    if direction == "LONG":
        section_h = highs[-lookback:]
        section_l = lows[-lookback:]
        sh_off = int(np.argmax(section_h))
        swing_high = float(section_h[sh_off])
        if sh_off >= lookback - 3:
            return False, ""
        before = section_l[:sh_off + 1]
        if len(before) < 3:
            return False, ""
        sl_off = int(np.argmin(before))
        swing_low = float(before[sl_off])
        swing_range = swing_high - swing_low
        if swing_range < 4 * atr or sl_off >= sh_off:
            return False, ""
        ote_high = swing_high - swing_range * 0.618
        ote_low = swing_high - swing_range * 0.786
        margin = atr * 0.5
        if ote_low - margin <= price <= ote_high + margin:
            return True, f"Fib OTE zone {ote_low:.0f}-{ote_high:.0f} (swing {swing_low:.0f}-{swing_high:.0f})"
    else:  # SHORT
        section_h = highs[-lookback:]
        section_l = lows[-lookback:]
        sl_off = int(np.argmin(section_l))
        swing_low = float(section_l[sl_off])
        if sl_off >= lookback - 3:
            return False, ""
        before = section_h[:sl_off + 1]
        if len(before) < 3:
            return False, ""
        sh_off = int(np.argmax(before))
        swing_high = float(before[sh_off])
        swing_range = swing_high - swing_low
        if swing_range < 4 * atr or sh_off >= sl_off:
            return False, ""
        ote_low = swing_low + swing_range * 0.618
        ote_high = swing_low + swing_range * 0.786
        margin = atr * 0.5
        if ote_low - margin <= price <= ote_high + margin:
            return True, f"Fib OTE zone {ote_low:.0f}-{ote_high:.0f} (swing {swing_high:.0f}-{swing_low:.0f})"

    return False, ""


# ---------------------------------------------------------------------------
# Volume confirmation helpers
# ---------------------------------------------------------------------------

def _volume_confirmation(
    df: pd.DataFrame,
    direction: str,
    lookback: int = 5,
    avg_period: int = 20,
) -> tuple[bool, float, str]:
    """Check if recent volume supports the trade direction.

    For longs: want to see higher-than-average volume on up-bars near entry,
               or declining volume on the pullback (dry-up).
    For shorts: mirror logic.

    Returns:
        (confirmed: bool, vol_score: float 0..1, description: str)
    """
    if "volume" not in df.columns or len(df) < avg_period + lookback:
        return True, 0.5, "No volume data — neutral"

    vol = df["volume"].values
    closes = df["close"].values
    opens = df["open"].values

    avg_vol = float(np.mean(vol[-avg_period - lookback:-lookback])) if lookback < len(vol) else float(np.mean(vol[:-lookback] if lookback < len(vol) else vol))
    if avg_vol <= 0:
        return True, 0.5, "Avg volume zero — neutral"

    recent_vol = vol[-lookback:]
    recent_closes = closes[-lookback:]
    recent_opens = opens[-lookback:]

    # Classify bars as up/down
    up_vol = sum(v for v, c, o in zip(recent_vol, recent_closes, recent_opens) if c >= o)
    down_vol = sum(v for v, c, o in zip(recent_vol, recent_closes, recent_opens) if c < o)
    total_recent_vol = float(np.sum(recent_vol))
    avg_recent = total_recent_vol / lookback if lookback > 0 else 0

    # Volume ratio vs average
    vol_ratio = avg_recent / avg_vol if avg_vol > 0 else 1.0

    if direction == "LONG":
        # Ideal: pullback on declining volume (dry-up) OR bounce on expanding volume
        pullback_dryup = vol_ratio < 0.7
        bounce_expand = vol_ratio > 1.3 and up_vol > down_vol * 1.5
        if pullback_dryup:
            return True, 0.8, f"Volume dry-up on pullback ({vol_ratio:.1f}x avg)"
        elif bounce_expand:
            return True, 0.9, f"Expanding volume on bounce ({vol_ratio:.1f}x avg, up/down ratio {up_vol / max(down_vol, 1):.1f})"
        elif vol_ratio < 1.5 and up_vol >= down_vol:
            return True, 0.5, f"Neutral volume ({vol_ratio:.1f}x avg)"
        else:
            return False, 0.2, f"Heavy selling volume ({vol_ratio:.1f}x avg, down dominant)"
    else:  # SHORT
        pullback_dryup = vol_ratio < 0.7
        selloff_expand = vol_ratio > 1.3 and down_vol > up_vol * 1.5
        if pullback_dryup:
            return True, 0.8, f"Volume dry-up on rally ({vol_ratio:.1f}x avg)"
        elif selloff_expand:
            return True, 0.9, f"Expanding volume on selloff ({vol_ratio:.1f}x avg)"
        elif vol_ratio < 1.5 and down_vol >= up_vol:
            return True, 0.5, f"Neutral volume ({vol_ratio:.1f}x avg)"
        else:
            return False, 0.2, f"Heavy buying volume ({vol_ratio:.1f}x avg, up dominant)"


def detect_setups(
    asset: str,
    df: pd.DataFrame,
    structure: dict,
    cfg: DSSConfig,
    mamis: dict | None = None,
    timeframe: str = "4h",
) -> list[Setup]:
    """Detect all valid setup candidates for an asset.

    Args:
        asset: Asset symbol (BTC, ETH, etc.)
        df: OHLCV DataFrame
        structure: Structure analysis dict from scan pipeline
        cfg: Full config
        mamis: Optional Mamis cycle classification dict
        timeframe: Bar timeframe (e.g. "4h", "1d") — passed through to setups

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
    trend_conf = 0.5
    if isinstance(trend, dict):
        trend_conf = trend.get("confidence", 0.5)
        trend = trend.get("direction", "SIDEWAYS")

    zones = _parse_zones(structure.get("zones", []))
    wyckoff = structure.get("wyckoff_event", "NONE")
    if isinstance(wyckoff, dict):
        wyckoff = wyckoff.get("event", "NONE")

    acc_rej = structure.get("acceptance_rejection", {})
    events = acc_rej.get("events", []) if isinstance(acc_rej, dict) else []

    mamis_phase = mamis.get("phase", "OPTIMISM") if mamis else "OPTIMISM"
    mamis_group = mamis.get("phase_group", "MARKUP") if mamis else "MARKUP"

    # ---- Pre-compute RSI for all setups that need it ----
    rsi_series = rsi(df["close"], period=14) if len(df) >= 20 else pd.Series(dtype=float)
    current_rsi = float(rsi_series.iloc[-1]) if len(rsi_series) > 0 and not np.isnan(rsi_series.iloc[-1]) else 50.0
    div_signal = rsi_divergence(df["close"], rsi_series, lookback=30) if len(rsi_series) >= 30 else "NONE"

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
        asset, now, current_price, current_atr, stage_info, trend, trend_conf,
        nearest_support, nearest_resistance, df, timeframe,
    )
    if setup_a:
        # Volume confirmation — quality adjustment, not hard filter
        vol_ok, vol_score, vol_desc = _volume_confirmation(df, setup_a.direction.value)
        setup_a.quality_reasons.append(f"Volume: {vol_desc}")
        if not vol_ok:
            setup_a.setup_quality_score = max(0, setup_a.setup_quality_score - 0.5)
        else:
            setup_a.setup_quality_score = min(3.0, setup_a.setup_quality_score + (vol_score - 0.5) * 0.5)
        # RSI divergence bonus: if RSI diverges in our direction, boost quality
        if (setup_a.direction == Direction.LONG and div_signal == "BULLISH") or \
           (setup_a.direction == Direction.SHORT and div_signal == "BEARISH"):
            setup_a.setup_quality_score = min(3.0, setup_a.setup_quality_score + 0.5)
            setup_a.quality_reasons.append(f"RSI divergence: {div_signal} (quality boost)")
        setups.append(setup_a)

    # --- Setup B: Breakout + Retest (ENHANCED) ---
    # Now requires volume surge, clean close, zone strength, wider stop
    setup_b = _detect_setup_b(
        asset, now, current_price, current_atr, stage_info, trend,
        zones, events, df, timeframe,
    )
    if setup_b:
        vol_ok, vol_score, vol_desc = _volume_confirmation(df, setup_b.direction.value)
        setup_b.quality_reasons.append(f"Volume: {vol_desc}")
        if not vol_ok:
            setup_b.setup_quality_score = max(0, setup_b.setup_quality_score - 0.5)
        else:
            setup_b.setup_quality_score = min(3.0, setup_b.setup_quality_score + (vol_score - 0.5) * 0.5)
        setups.append(setup_b)

    # --- Setup C: Wyckoff Trap Reversal (ENHANCED) ---
    # Now requires volume divergence + reclaim candle + strict stage gating
    setup_c = _detect_setup_c(
        asset, now, current_price, current_atr, stage_info, wyckoff,
        nearest_support, nearest_resistance, df, timeframe,
    )
    if setup_c:
        vol_ok, vol_score, vol_desc = _volume_confirmation(df, setup_c.direction.value)
        setup_c.quality_reasons.append(f"Volume: {vol_desc}")
        if not vol_ok:
            setup_c.setup_quality_score = max(0, setup_c.setup_quality_score - 0.75)
        else:
            setup_c.setup_quality_score = min(3.0, setup_c.setup_quality_score + (vol_score - 0.5) * 0.5)
        setups.append(setup_c)

    # --- Setup D: REMOVED as standalone (v8) ---
    # MAMIS cycle phases are now applied as confirmation bonuses on all
    # OTE-based entries (see confirmation layer below).  The _detect_setup_d
    # function is retained but no longer called.

    # --- Setup E: Range Trade ---
    atr_pctile = structure.get("atr_percentile", 50.0)
    if isinstance(atr_pctile, dict):
        atr_pctile = 50.0
    setup_e_list = _detect_setup_e(
        asset, now, current_price, current_atr, stage_info, trend, trend_conf,
        zones, df, atr_pctile, timeframe,
    )
    for se in setup_e_list:
        vol_ok, vol_score, vol_desc = _volume_confirmation(df, se.direction.value)
        se.quality_reasons.append(f"Volume: {vol_desc}")
        if not vol_ok:
            se.setup_quality_score = max(0, se.setup_quality_score - 0.5)
    setups.extend(setup_e_list)

    # --- Setup F: RSI Divergence Reversal (NEW) ---
    setup_f = _detect_setup_f(
        asset, now, current_price, current_atr, stage_info,
        nearest_support, nearest_resistance, df, timeframe,
        current_rsi, div_signal, rsi_series,
    )
    if setup_f:
        vol_ok, vol_score, vol_desc = _volume_confirmation(df, setup_f.direction.value)
        setup_f.quality_reasons.append(f"Volume: {vol_desc}")
        if not vol_ok:
            setup_f.setup_quality_score = max(0, setup_f.setup_quality_score - 0.5)
        else:
            setup_f.setup_quality_score = min(3.0, setup_f.setup_quality_score + (vol_score - 0.5) * 0.5)
        setups.append(setup_f)

    # --- Setup G: Volume Climax Reversal (NEW) ---
    setup_g = _detect_setup_g(
        asset, now, current_price, current_atr, stage_info,
        nearest_support, nearest_resistance, df, timeframe, current_rsi,
    )
    if setup_g:
        setups.append(setup_g)

    # --- Setup H: Fibonacci OTE (v8) ---
    # Temporarily disabled: H causes displacement regression
    # on BTC/XAU while helping ETH. Will be re-enabled with
    # stricter filters after baseline verification.
    # setup_h = _detect_setup_h(
    #     asset, now, current_price, current_atr, stage_info, trend, trend_conf,
    #     nearest_support, nearest_resistance, df, timeframe,
    # )
    # if setup_h:
    #     vol_ok, vol_score, vol_desc = _volume_confirmation(df, setup_h.direction.value)
    #     setup_h.quality_reasons.append(f"Volume: {vol_desc}")
    #     if not vol_ok:
    #         setup_h.setup_quality_score = max(0, setup_h.setup_quality_score - 0.5)
    #     else:
    #         setup_h.setup_quality_score = min(3.0, setup_h.setup_quality_score + (vol_score - 0.5) * 0.5)
    #     if (setup_h.direction == Direction.LONG and div_signal == "BULLISH") or \
    #        (setup_h.direction == Direction.SHORT and div_signal == "BEARISH"):
    #         setup_h.setup_quality_score = min(3.0, setup_h.setup_quality_score + 0.5)
    #         setup_h.quality_reasons.append(f"RSI divergence: {div_signal} (quality boost)")
    #     setups.append(setup_h)

    # ---- MAMIS + Wyckoff confirmation ----
    # NOT applied as quality bonuses here. The scorer already computes
    # mamis_score (-2..+2) and phase_score (incl. Wyckoff alignment) as
    # separate components.  Adding quality bonuses on top would double-count
    # and perturb score ordering, displacing proven V7.2b winners.
    # MAMIS and Wyckoff are confirmations via the SCORING ENGINE, not
    # via the setup quality layer.

    return setups


def _detect_setup_a(
    asset: str, now: datetime, price: float, atr: float,
    stage: int, trend: str, trend_conf: float,
    support: SRZone | None, resistance: SRZone | None,
    df: pd.DataFrame, timeframe: str = "4h",
) -> Setup | None:
    """Setup A — Trend Pullback Continuation.

    Long: Any bullish bias (trend UP or stage 1/2), pullback near support
    Short: Any bearish bias (trend DOWN or stage 3/4), rally near resistance
    """
    if atr <= 0:
        return None

    # Long pullback
    long_eligible = (
        (trend == "UP" and trend_conf >= 0.5) or
        (stage in (1, 2) and trend != "DOWN") or
        (trend == "SIDEWAYS" and stage != 4)
    )

    if long_eligible and support:
        dist = (price - (support.low + support.high) / 2) / atr
        if -0.3 < dist < 3.0:  # Wider distance tolerance (was 0..2)
            stop = support.low - atr * 1.5
            t1 = resistance.high if resistance else price + atr * 3
            t2 = price + atr * 5 if resistance is None else None

            rr = (t1 - price) / (price - stop) if price > stop else 0
            if rr < 1.2:  # Minimum R:R filter
                return None

            quality = _assess_quality(support.strength, dist, rr)
            stage_label = f"Stage {stage}" if stage == 2 else f"Stage {stage} (relaxed)"

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.PULLBACK,
                direction=Direction.LONG,
                zone_description=f"Support {support.low:.2f}-{support.high:.2f}",
                zone_low=support.low,
                zone_high=support.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"{stage_label} pullback to support",
                    f"Trend: {trend} (conf {trend_conf:.0%})",
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

    # Short rally — allow when trend is DOWN *or* stage 3/4 with resistance nearby
    short_eligible = (
        (trend == "DOWN" and trend_conf >= 0.5) or
        (stage in (3, 4) and trend != "UP") or
        (trend == "SIDEWAYS" and stage != 2)
    )

    if short_eligible and resistance:
        dist = ((resistance.low + resistance.high) / 2 - price) / atr
        if -0.3 < dist < 3.0:
            stop = resistance.high + atr * 1.5
            t1 = support.low if support else price - atr * 3
            t2 = price - atr * 5 if support is None else None

            rr = (price - t1) / (stop - price) if stop > price else 0
            if rr < 1.2:
                return None

            quality = _assess_quality(resistance.strength, dist, rr)
            stage_label = f"Stage {stage}" if stage == 4 else f"Stage {stage} (relaxed)"

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.PULLBACK,
                direction=Direction.SHORT,
                zone_description=f"Resistance {resistance.low:.2f}-{resistance.high:.2f}",
                zone_low=resistance.low,
                zone_high=resistance.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"{stage_label} rally to resistance",
                    f"Trend: {trend} (conf {trend_conf:.0%})",
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
    df: pd.DataFrame, timeframe: str = "4h",
) -> Setup | None:
    """Setup B — Breakout + Retest (ENHANCED).

    Improvements over original:
    1. Zone strength minimum ≥ 0.3 and ≥ 2 touches
    2. Volume surge on breakout bar (>1.3x avg)
    3. Clean close filter: breakout bar closes in top/bottom 30% of range
    4. Acceptance event must be within last 10 bars (freshness)
    5. Wider stop: 1.5 ATR (was 1.0)
    6. Multi-bar confirmation: 2 consecutive closes beyond zone

    Long: Breakout above resistance + acceptance + retest
    Short: Breakdown below support + acceptance + retest
    """
    if atr <= 0:
        return None

    # Volume data for surge check
    has_volume = "volume" in df.columns and len(df) >= 25
    avg_vol = float(np.mean(df["volume"].values[-25:-5])) if has_volume else 0

    # Look for recent acceptance events (only within last 10 bars — freshness filter)
    bar_count = len(df)
    recent_cutoff = bar_count - 10
    bullish_acceptances = [
        e for e in events
        if e.get("type") == "ACCEPTANCE" and e.get("direction") == "BULLISH"
        and e.get("bar_index", 0) >= recent_cutoff
    ]
    bearish_acceptances = [
        e for e in events
        if e.get("type") == "ACCEPTANCE" and e.get("direction") == "BEARISH"
        and e.get("bar_index", 0) >= recent_cutoff
    ]

    # --- Find the broken zone and check its quality ---
    def _zone_quality_ok(zone_low: float, zone_high: float) -> bool:
        """Check if the broken zone has sufficient strength/touches."""
        for z in zones:
            if abs(z.low - zone_low) < atr * 0.3 and abs(z.high - zone_high) < atr * 0.3:
                return z.strength >= 0.3 and z.touches >= 2
        return False  # Zone not found in our zone list — skip

    def _clean_close_check(bar_idx: int, direction: str) -> bool:
        """Check breakout bar has a 'clean' close — top/bottom 30% of range."""
        if bar_idx < 0 or bar_idx >= len(df):
            return True  # Can't check, assume ok
        h = float(df["high"].iloc[bar_idx])
        l = float(df["low"].iloc[bar_idx])
        c = float(df["close"].iloc[bar_idx])
        bar_range = h - l
        if bar_range <= 0:
            return True
        if direction == "BULLISH":
            return (c - l) / bar_range >= 0.70  # Close in top 30%
        else:
            return (h - c) / bar_range >= 0.70  # Close in bottom 30%

    def _volume_surge_check(bar_idx: int) -> tuple[bool, str]:
        """Check if breakout bar had above-average volume."""
        if not has_volume or avg_vol <= 0 or bar_idx < 0 or bar_idx >= len(df):
            return True, "No volume data"
        bar_vol = float(df["volume"].iloc[bar_idx])
        ratio = bar_vol / avg_vol
        if ratio >= 1.3:
            return True, f"Volume surge {ratio:.1f}x avg"
        return False, f"Weak breakout volume ({ratio:.1f}x avg)"

    # Bullish breakout + retest
    if bullish_acceptances and stage in (1, 2, 3):
        acc = bullish_acceptances[0]
        zone_high = acc.get("zone_high", 0)
        zone_low = acc.get("zone_low", 0)
        breakout_bar = acc.get("bar_index", bar_count - 5)

        # Zone quality gate
        if not _zone_quality_ok(zone_low, zone_high):
            pass  # Skip this acceptance — weak zone
        else:
            dist_from_zone = (price - zone_high) / atr if atr > 0 else 99
            if -0.5 <= dist_from_zone <= 2.0:
                # Multi-bar confirmation: last 2 bars must close above zone
                confirmed = True
                if len(df) >= 3:
                    for k in range(1, 3):
                        if float(df["close"].iloc[-k]) < zone_high:
                            confirmed = False
                            break

                # Clean close on breakout bar
                if confirmed:
                    confirmed = _clean_close_check(breakout_bar, "BULLISH")

                # Volume surge on breakout bar
                vol_surge_ok, vol_desc = _volume_surge_check(breakout_bar)

                if confirmed:
                    stop = zone_low - atr * 1.5  # WIDER stop (was 1.0)
                    t1 = price + atr * 3
                    rr = (t1 - price) / (price - stop) if price > stop else 0

                    quality = min(2.5, 1.0 + (0.5 if dist_from_zone < 1.0 else 0))
                    if vol_surge_ok:
                        quality = min(3.0, quality + 0.5)

                    return Setup(
                        timestamp_utc=now,
                        asset=asset,
                        timeframe=timeframe,
                        setup_type=SetupType.BREAKOUT_RETEST,
                        direction=Direction.LONG,
                        zone_description=f"Broken resistance {zone_low:.2f}-{zone_high:.2f}",
                        zone_low=zone_low,
                        zone_high=zone_high,
                        setup_quality_score=quality,
                        quality_reasons=[
                            "Enhanced: multi-bar confirm + clean close + zone quality",
                            f"Retest distance: {dist_from_zone:.1f} ATR",
                            f"{vol_desc}",
                            f"R:R: {rr:.1f}",
                        ],
                        trade_plan=TradePlan(
                            direction=Direction.LONG,
                            entry_zone_low=zone_high - atr * 0.2,
                            entry_zone_high=zone_high + atr * 0.5,
                            trigger_description="4H: 2 consecutive closes above broken zone",
                            stop_loss=stop,
                            target_1=t1,
                            target_2=price + atr * 5,
                            trail_method="Trail by 4H structure after T1",
                            risk_reward_t1=round(rr, 2),
                        ),
                    )

    # Bearish breakout + retest
    if bearish_acceptances and stage in (1, 3, 4):
        acc = bearish_acceptances[0]
        zone_high = acc.get("zone_high", 0)
        zone_low = acc.get("zone_low", 0)
        breakout_bar = acc.get("bar_index", bar_count - 5)

        if not _zone_quality_ok(zone_low, zone_high):
            pass
        else:
            dist_from_zone = (zone_low - price) / atr if atr > 0 else 99
            if -0.5 <= dist_from_zone <= 2.0:
                confirmed = True
                if len(df) >= 3:
                    for k in range(1, 3):
                        if float(df["close"].iloc[-k]) > zone_low:
                            confirmed = False
                            break

                if confirmed:
                    confirmed = _clean_close_check(breakout_bar, "BEARISH")

                vol_surge_ok, vol_desc = _volume_surge_check(breakout_bar)

                if confirmed:
                    stop = zone_high + atr * 1.5  # WIDER stop
                    t1 = price - atr * 3
                    rr = (price - t1) / (stop - price) if stop > price else 0

                    quality = min(2.5, 1.0 + (0.5 if dist_from_zone < 1.0 else 0))
                    if vol_surge_ok:
                        quality = min(3.0, quality + 0.5)

                    return Setup(
                        timestamp_utc=now,
                        asset=asset,
                        timeframe=timeframe,
                        setup_type=SetupType.BREAKOUT_RETEST,
                        direction=Direction.SHORT,
                        zone_description=f"Broken support {zone_low:.2f}-{zone_high:.2f}",
                        zone_low=zone_low,
                        zone_high=zone_high,
                        setup_quality_score=quality,
                        quality_reasons=[
                            "Enhanced: multi-bar confirm + clean close + zone quality",
                            f"Retest distance: {dist_from_zone:.1f} ATR",
                            f"{vol_desc}",
                            f"R:R: {rr:.1f}",
                        ],
                        trade_plan=TradePlan(
                            direction=Direction.SHORT,
                            entry_zone_low=zone_low - atr * 0.5,
                            entry_zone_high=zone_low + atr * 0.2,
                            trigger_description="4H: 2 consecutive closes below broken zone",
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
    df: pd.DataFrame, timeframe: str = "4h",
) -> Setup | None:
    """Setup C — Wyckoff Trap Reversal (ENHANCED).

    Improvements:
    1. Strict stage gating: Spring only in Stage 1/4, UTAD only in Stage 2/3
    2. Reclaim candle: price must close back INSIDE range (not just trigger)
    3. Volume divergence: sweep bar should have high vol, recovery declining
    4. Wider stop: 1.5 ATR (was 1.0) to survive the whipsaw
    """
    if atr <= 0:
        return None

    has_volume = "volume" in df.columns and len(df) >= 25
    avg_vol = float(np.mean(df["volume"].values[-25:-5])) if has_volume else 0

    # Spring (long trap reversal) — strict: only Stage 1 or 4
    if wyckoff == "SPRING" and support and stage in (1, 4):
        # Reclaim check: current price must be above support.low (closed back inside)
        if price <= support.low:
            pass  # Not reclaimed yet — skip
        else:
            # Volume divergence check: look for declining volume on recovery bars
            vol_divergence = False
            vol_desc = "No volume divergence data"
            if has_volume and avg_vol > 0 and len(df) >= 5:
                # Last 3 bars volume should be declining (recovery on dry volume = strong)
                recent_vols = [float(df["volume"].iloc[-k]) for k in range(1, 4)]
                sweep_vol = float(df["volume"].iloc[-4]) if len(df) >= 5 else avg_vol
                if sweep_vol > avg_vol * 1.2 and recent_vols[0] < recent_vols[-1]:
                    vol_divergence = True
                    vol_desc = f"Vol divergence: sweep {sweep_vol/avg_vol:.1f}x avg, recovery declining"
                else:
                    vol_desc = f"No clear vol divergence (sweep {sweep_vol/avg_vol:.1f}x avg)"

            stop = support.low - atr * 1.5  # WIDER stop
            t1_zone = resistance if resistance else None
            t1 = t1_zone.low if t1_zone else price + atr * 3
            t2 = t1_zone.high if t1_zone else price + atr * 5

            rr = (t1 - price) / (price - stop) if price > stop else 0
            quality = min(3.0, 1.5 + (0.5 if vol_divergence else 0) + (0.5 if stage == 1 else 0))

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.WYCKOFF_TRAP,
                direction=Direction.LONG,
                zone_description=f"Spring below {support.low:.2f}",
                zone_low=support.low,
                zone_high=support.high,
                setup_quality_score=quality,
                quality_reasons=[
                    "Enhanced: reclaim confirmed + strict stage gating",
                    f"Wyckoff Spring (bear trap) — Stage {stage}",
                    vol_desc,
                    f"R:R: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.LONG,
                    entry_zone_low=support.low,
                    entry_zone_high=support.high,
                    trigger_description="Price reclaimed above support after spring sweep",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="Trail if transitions to Stage 2",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    # UTAD (short trap reversal) — strict: only Stage 2 or 3
    if wyckoff == "UTAD" and resistance and stage in (2, 3):
        # Reclaim check: price must be below resistance.high (closed back inside)
        if price >= resistance.high:
            pass  # Not reclaimed yet
        else:
            vol_divergence = False
            vol_desc = "No volume divergence data"
            if has_volume and avg_vol > 0 and len(df) >= 5:
                recent_vols = [float(df["volume"].iloc[-k]) for k in range(1, 4)]
                sweep_vol = float(df["volume"].iloc[-4]) if len(df) >= 5 else avg_vol
                if sweep_vol > avg_vol * 1.2 and recent_vols[0] < recent_vols[-1]:
                    vol_divergence = True
                    vol_desc = f"Vol divergence: sweep {sweep_vol/avg_vol:.1f}x avg, recovery declining"
                else:
                    vol_desc = f"No clear vol divergence (sweep {sweep_vol/avg_vol:.1f}x avg)"

            stop = resistance.high + atr * 1.5  # WIDER stop
            t1_zone = support if support else None
            t1 = t1_zone.high if t1_zone else price - atr * 3
            t2 = t1_zone.low if t1_zone else price - atr * 5

            rr = (price - t1) / (stop - price) if stop > price else 0
            quality = min(3.0, 1.5 + (0.5 if vol_divergence else 0) + (0.5 if stage == 3 else 0))

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.WYCKOFF_TRAP,
                direction=Direction.SHORT,
                zone_description=f"UTAD above {resistance.high:.2f}",
                zone_low=resistance.low,
                zone_high=resistance.high,
                setup_quality_score=quality,
                quality_reasons=[
                    "Enhanced: reclaim confirmed + strict stage gating",
                    f"Wyckoff UTAD (bull trap) — Stage {stage}",
                    vol_desc,
                    f"R:R: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.SHORT,
                    entry_zone_low=resistance.low,
                    entry_zone_high=resistance.high,
                    trigger_description="Price rejected back below resistance after UTAD sweep",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="Trail if transitions to Stage 4",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    return None


def _detect_setup_d(
    asset: str, now: datetime, price: float, atr: float,
    mamis_phase: str, mamis_group: str,
    support: SRZone | None, resistance: SRZone | None,
    df: pd.DataFrame, timeframe: str = "4h",
) -> Setup | None:
    """Setup D — Mamis Transition (mean-reversion at cycle extremes)."""
    if atr <= 0:
        return None

    # Long setups: accumulation / markdown extremes
    long_mamis_phases = {
        "HOPE", "RELIEF", "DEPRESSION", "DESPONDENCY", "CAPITULATION", "PANIC",
    }
    # Short setups: distribution / markup extremes
    short_mamis_phases = {
        "EXCITEMENT", "THRILL", "ANXIETY", "DENIAL", "FEAR", "DESPERATION",
    }

    if mamis_phase in long_mamis_phases and support:
        dist = (price - (support.low + support.high) / 2) / atr
        if -0.5 < dist < 3.0:
            stop = support.low - atr * 1.5
            t1 = resistance.high if resistance else price + atr * 3
            t2 = price + atr * 5 if resistance is None else None

            rr = (t1 - price) / (price - stop) if price > stop else 0
            if rr < 1.0:
                return None

            quality = min(2.5, 1.0 + (0.5 if mamis_phase in ("HOPE", "CAPITULATION", "PANIC") else 0))

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.MAMIS_TRANSITION,
                direction=Direction.LONG,
                zone_description=f"Mamis {mamis_phase} near support {support.low:.2f}-{support.high:.2f}",
                zone_low=support.low,
                zone_high=support.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"Mamis phase: {mamis_phase} ({mamis_group})",
                    f"Cycle bottom transition — mean-reversion long",
                    f"Distance to support: {dist:.1f} ATR",
                    f"R:R to T1: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.LONG,
                    entry_zone_low=support.low,
                    entry_zone_high=support.high,
                    trigger_description=f"Mamis {mamis_phase} — 4H holds support zone",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="Trail by 4H structure after T1",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    if mamis_phase in short_mamis_phases and resistance:
        dist = ((resistance.low + resistance.high) / 2 - price) / atr
        if -0.5 < dist < 3.0:
            stop = resistance.high + atr * 1.5
            t1 = support.low if support else price - atr * 3
            t2 = price - atr * 5 if support is None else None

            rr = (price - t1) / (stop - price) if stop > price else 0
            if rr < 1.0:
                return None

            quality = min(2.5, 1.0 + (0.5 if mamis_phase in ("THRILL", "FEAR") else 0))

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.MAMIS_TRANSITION,
                direction=Direction.SHORT,
                zone_description=f"Mamis {mamis_phase} near resistance {resistance.low:.2f}-{resistance.high:.2f}",
                zone_low=resistance.low,
                zone_high=resistance.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"Mamis phase: {mamis_phase} ({mamis_group})",
                    f"Cycle top transition — mean-reversion short",
                    f"Distance to resistance: {dist:.1f} ATR",
                    f"R:R to T1: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.SHORT,
                    entry_zone_low=resistance.low,
                    entry_zone_high=resistance.high,
                    trigger_description=f"Mamis {mamis_phase} — 4H rejects resistance zone",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="Trail by 4H structure after T1",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    return None


def _detect_setup_e(
    asset: str, now: datetime, price: float, atr: float,
    stage: int, trend: str, trend_conf: float,
    zones: list[SRZone],
    df: pd.DataFrame,
    atr_percentile: float, timeframe: str = "4h",
) -> list[Setup]:
    """Setup E — Range Trade.

    Scans ALL zone pairs to find the best-defined range, then checks if
    price is near a boundary.

    Conditions for a valid range:
    1. A support zone below price and a resistance zone above price
    2. Range width between 2-20 ATR
    3. Price is near one of the boundaries (within 2 ATR or position < 0.40 / > 0.60)
    4. Not a very strong unidirectional trend
    5. At least 2 touches on each boundary
    """
    if atr <= 0 or len(zones) < 2:
        return []

    # Range environment check
    is_range_env = (
        trend == "SIDEWAYS" or
        trend_conf < 0.70 or
        stage in (1, 3)
    )
    if not is_range_env:
        return []

    # Separate zones into support (below price) and resistance (above price)
    support_zones = []
    resistance_zones = []
    for z in zones:
        zmid = (z.low + z.high) / 2
        if zmid < price:
            support_zones.append(z)
        elif zmid >= price:
            resistance_zones.append(z)

    if not support_zones or not resistance_zones:
        return []

    # Find the best range pair: support with most touches + resistance with most touches
    # that forms a valid-width range
    best_pair = None
    best_pair_score = -1

    for sup in support_zones:
        for res in resistance_zones:
            range_width = res.high - sup.low
            range_width_atr = range_width / atr if atr > 0 else 0

            if range_width_atr < 2.0 or range_width_atr > 20.0:
                continue

            if sup.touches < 2 or res.touches < 2:
                continue

            # Score this pair: more touches + stronger zones = better range
            pair_score = (
                sup.strength + res.strength +
                min(sup.touches, 5) * 0.2 +
                min(res.touches, 5) * 0.2
            )

            if pair_score > best_pair_score:
                best_pair_score = pair_score
                best_pair = (sup, res, range_width, range_width_atr)

    if best_pair is None:
        return []

    support, resistance, range_width, range_width_atr = best_pair
    range_low = support.low
    range_high = resistance.high

    setups: list[Setup] = []

    # Price position within range (0 = at support, 1 = at resistance)
    range_position = (price - range_low) / range_width if range_width > 0 else 0.5

    # Recent consolidation check
    recent = df.tail(10)
    recent_range = (recent["high"].max() - recent["low"].min()) / atr if atr > 0 else 0
    is_consolidating = recent_range < range_width_atr * 0.6

    # --- Long at range low ---
    dist_to_support = (price - (support.low + support.high) / 2) / atr
    if range_position < 0.40 and dist_to_support < 2.0:
        stop = support.low - atr * 1.2
        t1 = (range_low + range_high) / 2  # Range midpoint
        t2 = resistance.low

        rr = (t1 - price) / (price - stop) if price > stop else 0
        if rr >= 1.0:
            quality = _assess_range_quality(
                support.strength, resistance.strength,
                range_width_atr, dist_to_support, rr,
                atr_percentile, is_consolidating,
            )

            setups.append(Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.RANGE_TRADE,
                direction=Direction.LONG,
                zone_description=(
                    f"Range [{range_low:.0f}-{range_high:.0f}] "
                    f"({range_width_atr:.1f} ATR) — buy at support"
                ),
                zone_low=support.low,
                zone_high=support.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"Range trade: buy at range low",
                    f"Range: {range_low:.0f}-{range_high:.0f} ({range_width_atr:.1f} ATR wide)",
                    f"Position in range: {range_position:.0%}",
                    f"Support: {support.touches} touches, str={support.strength:.2f}",
                    f"Resistance: {resistance.touches} touches, str={resistance.strength:.2f}",
                    f"R:R to midpoint: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.LONG,
                    entry_zone_low=support.low,
                    entry_zone_high=support.high,
                    trigger_description="4H close holds range support, bullish candle",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="No trail — level-to-level range trade",
                    risk_reward_t1=round(rr, 2),
                ),
            ))

    # --- Short at range high ---
    dist_to_resistance = ((resistance.low + resistance.high) / 2 - price) / atr
    if range_position > 0.60 and dist_to_resistance < 2.0:
        stop = resistance.high + atr * 1.2
        t1 = (range_low + range_high) / 2
        t2 = support.high

        rr = (price - t1) / (stop - price) if stop > price else 0
        if rr >= 1.0:
            quality = _assess_range_quality(
                resistance.strength, support.strength,
                range_width_atr, dist_to_resistance, rr,
                atr_percentile, is_consolidating,
            )

            setups.append(Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.RANGE_TRADE,
                direction=Direction.SHORT,
                zone_description=(
                    f"Range [{range_low:.0f}-{range_high:.0f}] "
                    f"({range_width_atr:.1f} ATR) — sell at resistance"
                ),
                zone_low=resistance.low,
                zone_high=resistance.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"Range trade: sell at range high",
                    f"Range: {range_low:.0f}-{range_high:.0f} ({range_width_atr:.1f} ATR wide)",
                    f"Position in range: {range_position:.0%}",
                    f"Resistance: {resistance.touches} touches, str={resistance.strength:.2f}",
                    f"Support: {support.touches} touches, str={support.strength:.2f}",
                    f"R:R to midpoint: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.SHORT,
                    entry_zone_low=resistance.low,
                    entry_zone_high=resistance.high,
                    trigger_description="4H close rejects range resistance, bearish candle",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="No trail — level-to-level range trade",
                    risk_reward_t1=round(rr, 2),
                ),
            ))

    return setups


def _detect_setup_f(
    asset: str, now: datetime, price: float, atr: float,
    stage: int,
    support: SRZone | None, resistance: SRZone | None,
    df: pd.DataFrame, timeframe: str = "4h",
    current_rsi: float = 50.0, div_signal: str = "NONE",
    rsi_series: pd.Series = None,
) -> Setup | None:
    """Setup F — RSI Divergence Reversal at S/R zones (NEW).

    High-probability mean-reversion: RSI divergence at a key zone is one of
    the most reliable reversal signals in technical analysis.

    Bullish: Price makes lower low, RSI makes higher low, near support zone.
    Bearish: Price makes higher high, RSI makes lower high, near resistance zone.

    Requirements:
    - Active RSI divergence signal
    - Price must be near a valid S/R zone (within 2 ATR)
    - RSI extreme (< 35 for longs, > 65 for shorts)
    - Minimum R:R 1.5 (higher bar — divergences are reversal trades)
    """
    if atr <= 0 or div_signal == "NONE":
        return None

    # Bullish divergence near support
    if div_signal == "BULLISH" and support and current_rsi < 40:
        dist = (price - (support.low + support.high) / 2) / atr
        if -0.5 < dist < 2.5:
            stop = support.low - atr * 1.5
            t1 = resistance.high if resistance else price + atr * 3
            t2 = price + atr * 5 if resistance is None else None

            rr = (t1 - price) / (price - stop) if price > stop else 0
            if rr < 1.5:
                return None

            # Quality: zone strength + RSI extremity + distance
            quality = 0.0
            quality += min(support.strength * 2, 1.0)
            if current_rsi < 25:
                quality += 1.0  # Deeply oversold
            elif current_rsi < 35:
                quality += 0.5
            if dist < 1.0:
                quality += 0.5
            if rr >= 3.0:
                quality += 0.5
            quality = min(quality, 3.0)

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.DIVERGENCE_REVERSAL,
                direction=Direction.LONG,
                zone_description=f"RSI bullish divergence at support {support.low:.2f}-{support.high:.2f}",
                zone_low=support.low,
                zone_high=support.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"RSI bullish divergence (RSI {current_rsi:.0f})",
                    f"Price near support zone (dist {dist:.1f} ATR)",
                    f"Zone strength: {support.strength:.2f}, touches: {support.touches}",
                    f"R:R to T1: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.LONG,
                    entry_zone_low=support.low,
                    entry_zone_high=support.high,
                    trigger_description="4H RSI bullish divergence at support — wait for close above zone",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="Trail by 4H structure after T1",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    # Bearish divergence near resistance
    if div_signal == "BEARISH" and resistance and current_rsi > 60:
        dist = ((resistance.low + resistance.high) / 2 - price) / atr
        if -0.5 < dist < 2.5:
            stop = resistance.high + atr * 1.5
            t1 = support.low if support else price - atr * 3
            t2 = price - atr * 5 if support is None else None

            rr = (price - t1) / (stop - price) if stop > price else 0
            if rr < 1.5:
                return None

            quality = 0.0
            quality += min(resistance.strength * 2, 1.0)
            if current_rsi > 75:
                quality += 1.0
            elif current_rsi > 65:
                quality += 0.5
            if dist < 1.0:
                quality += 0.5
            if rr >= 3.0:
                quality += 0.5
            quality = min(quality, 3.0)

            return Setup(
                timestamp_utc=now,
                asset=asset,
                timeframe=timeframe,
                setup_type=SetupType.DIVERGENCE_REVERSAL,
                direction=Direction.SHORT,
                zone_description=f"RSI bearish divergence at resistance {resistance.low:.2f}-{resistance.high:.2f}",
                zone_low=resistance.low,
                zone_high=resistance.high,
                setup_quality_score=quality,
                quality_reasons=[
                    f"RSI bearish divergence (RSI {current_rsi:.0f})",
                    f"Price near resistance zone (dist {dist:.1f} ATR)",
                    f"Zone strength: {resistance.strength:.2f}, touches: {resistance.touches}",
                    f"R:R to T1: {rr:.1f}",
                ],
                trade_plan=TradePlan(
                    direction=Direction.SHORT,
                    entry_zone_low=resistance.low,
                    entry_zone_high=resistance.high,
                    trigger_description="4H RSI bearish divergence at resistance — wait for rejection candle",
                    stop_loss=stop,
                    target_1=t1,
                    target_2=t2,
                    trail_method="Trail by 4H structure after T1",
                    risk_reward_t1=round(rr, 2),
                ),
            )

    return None


def _detect_setup_g(
    asset: str, now: datetime, price: float, atr: float,
    stage: int,
    support: SRZone | None, resistance: SRZone | None,
    df: pd.DataFrame, timeframe: str = "4h",
    current_rsi: float = 50.0,
) -> Setup | None:
    """Setup G — Volume Climax Reversal (NEW).

    Detects exhaustion/capitulation events: extreme volume spike (>2.5x avg)
    with a reversal candle at a key S/R zone. These are high-conviction
    "smart money absorption" signals.

    Bullish climax: huge selling volume at support + hammer/doji candle
    Bearish climax: huge buying volume at resistance + shooting star candle

    Requirements:
    - Volume on last completed bar > 2.5x 20-period average
    - Bar occurs near a key S/R zone (within 1.5 ATR)
    - Reversal candle pattern (close in favorable direction)
    - Minimum R:R 1.5
    """
    if atr <= 0:
        return None
    if "volume" not in df.columns or len(df) < 25:
        return None

    volumes = df["volume"].values
    avg_vol = float(np.mean(volumes[-25:-5]))
    if avg_vol <= 0:
        return None

    # Check last completed bar for volume climax
    last_vol = float(volumes[-1])
    vol_ratio = last_vol / avg_vol

    if vol_ratio < 2.5:
        return None  # Not a climax

    last_open = float(df["open"].iloc[-1])
    last_close = float(df["close"].iloc[-1])
    last_high = float(df["high"].iloc[-1])
    last_low = float(df["low"].iloc[-1])
    bar_range = last_high - last_low
    if bar_range <= 0:
        return None

    # Bullish volume climax: big volume at support + close in upper half (absorption)
    if support:
        dist = (price - (support.low + support.high) / 2) / atr
        if -0.5 < dist < 1.5:
            # Reversal candle: close in upper 50% of bar range (buyers absorbed sellers)
            close_position = (last_close - last_low) / bar_range
            if close_position >= 0.50 and last_close > last_open:  # Bullish close
                stop = support.low - atr * 1.5
                t1 = resistance.high if resistance else price + atr * 3
                t2 = price + atr * 5 if resistance is None else None

                rr = (t1 - price) / (price - stop) if price > stop else 0
                if rr < 1.5:
                    pass  # Skip
                else:
                    quality = min(3.0,
                        1.0 +
                        min((vol_ratio - 2.5) * 0.3, 0.6) +  # Higher vol = better
                        (0.5 if close_position > 0.7 else 0) +  # Strong close
                        (0.5 if current_rsi < 35 else 0) +  # RSI oversold confirmation
                        min(support.strength, 0.5)
                    )

                    return Setup(
                        timestamp_utc=now,
                        asset=asset,
                        timeframe=timeframe,
                        setup_type=SetupType.VOLUME_CLIMAX,
                        direction=Direction.LONG,
                        zone_description=f"Volume climax ({vol_ratio:.1f}x avg) at support {support.low:.2f}-{support.high:.2f}",
                        zone_low=support.low,
                        zone_high=support.high,
                        setup_quality_score=quality,
                        quality_reasons=[
                            f"Volume climax: {vol_ratio:.1f}x average ({last_vol:.0f} vs avg {avg_vol:.0f})",
                            f"Bullish absorption candle (close at {close_position:.0%} of range)",
                            f"Near support zone (dist {dist:.1f} ATR)",
                            f"RSI: {current_rsi:.0f}",
                            f"R:R to T1: {rr:.1f}",
                        ],
                        trade_plan=TradePlan(
                            direction=Direction.LONG,
                            entry_zone_low=support.low,
                            entry_zone_high=price,
                            trigger_description=f"Volume climax reversal ({vol_ratio:.1f}x) — buyers absorbing at support",
                            stop_loss=stop,
                            target_1=t1,
                            target_2=t2,
                            trail_method="Trail by 4H structure after T1",
                            risk_reward_t1=round(rr, 2),
                        ),
                    )

    # Bearish volume climax: big volume at resistance + close in lower half
    if resistance:
        dist = ((resistance.low + resistance.high) / 2 - price) / atr
        if -0.5 < dist < 1.5:
            close_position = (last_high - last_close) / bar_range  # Inverted for shorts
            if close_position >= 0.50 and last_close < last_open:  # Bearish close
                stop = resistance.high + atr * 1.5
                t1 = support.low if support else price - atr * 3
                t2 = price - atr * 5 if support is None else None

                rr = (price - t1) / (stop - price) if stop > price else 0
                if rr >= 1.5:
                    quality = min(3.0,
                        1.0 +
                        min((vol_ratio - 2.5) * 0.3, 0.6) +
                        (0.5 if close_position > 0.7 else 0) +
                        (0.5 if current_rsi > 65 else 0) +
                        min(resistance.strength, 0.5)
                    )

                    return Setup(
                        timestamp_utc=now,
                        asset=asset,
                        timeframe=timeframe,
                        setup_type=SetupType.VOLUME_CLIMAX,
                        direction=Direction.SHORT,
                        zone_description=f"Volume climax ({vol_ratio:.1f}x avg) at resistance {resistance.low:.2f}-{resistance.high:.2f}",
                        zone_low=resistance.low,
                        zone_high=resistance.high,
                        setup_quality_score=quality,
                        quality_reasons=[
                            f"Volume climax: {vol_ratio:.1f}x average ({last_vol:.0f} vs avg {avg_vol:.0f})",
                            f"Bearish rejection candle (close at {close_position:.0%} from high)",
                            f"Near resistance zone (dist {dist:.1f} ATR)",
                            f"RSI: {current_rsi:.0f}",
                            f"R:R to T1: {rr:.1f}",
                        ],
                        trade_plan=TradePlan(
                            direction=Direction.SHORT,
                            entry_zone_low=price,
                            entry_zone_high=resistance.high,
                            trigger_description=f"Volume climax reversal ({vol_ratio:.1f}x) — sellers absorbing at resistance",
                            stop_loss=stop,
                            target_1=t1,
                            target_2=t2,
                            trail_method="Trail by 4H structure after T1",
                            risk_reward_t1=round(rr, 2),
                        ),
                    )

    return None


def _detect_setup_h(
    asset: str, now: datetime, price: float, atr: float,
    stage: int, trend: str, trend_conf: float,
    support: SRZone | None, resistance: SRZone | None,
    df: pd.DataFrame, timeframe: str = "4h",
) -> Setup | None:
    """Setup H — Fibonacci OTE (Optimal Trade Entry).

    In a trending market, find the most recent completed swing and check if
    price has retraced to the 61.8%-78.6% Fibonacci zone (the institutional
    "golden pocket" / OTE zone).

    Long OTE:  Up-swing detected → price retraces to 61.8-78.6% of the move
    Short OTE: Down-swing detected → price retraces to 61.8-78.6% of the move

    Requirements:
    - Trending market (UP or DOWN, conf >= 0.4) or appropriate stage
    - Recent swing of at least 3 ATR in size
    - Price currently in/near the OTE zone (within 0.5 ATR margin)
    - Minimum R:R 1.5
    """
    if atr <= 0 or len(df) < 50:
        return None

    highs = df["high"].values
    lows = df["low"].values
    lookback = min(60, len(df) - 5)

    # ---- Long OTE: bullish trend, find up-swing, price retracing ----
    if (trend == "UP" and trend_conf >= 0.4) or (stage in (1, 2) and trend != "DOWN"):
        section_h = highs[-lookback:]
        section_l = lows[-lookback:]

        # Find highest high in lookback window
        sh_off = int(np.argmax(section_h))
        swing_high = float(section_h[sh_off])

        # Swing high must not be in last 3 bars (need retracement after)
        if sh_off < lookback - 3:
            # Find lowest low BEFORE the swing high
            before_sh_lows = section_l[: sh_off + 1]
            if len(before_sh_lows) >= 3:
                sl_off = int(np.argmin(before_sh_lows))
                swing_low = float(before_sh_lows[sl_off])
                swing_range = swing_high - swing_low

                if swing_range >= 3 * atr and sl_off < sh_off:
                    # OTE zone (golden pocket)
                    ote_high = swing_high - swing_range * 0.618
                    ote_low = swing_high - swing_range * 0.786
                    margin = atr * 0.5

                    if ote_low - margin <= price <= ote_high + margin:
                        stop = ote_low - atr * 1.0
                        if price > stop:
                            t1 = swing_high
                            t2 = swing_high + swing_range * 0.272  # 127.2% ext

                            rr = (t1 - price) / (price - stop)
                            if rr >= 1.5:
                                quality = 2.0
                                # In the sweet spot of OTE zone
                                if ote_low <= price <= ote_high:
                                    quality += 0.5
                                # S/R zone confluence
                                if support:
                                    zmid = (support.low + support.high) / 2
                                    if abs(zmid - price) < atr * 2.0:
                                        quality += 0.5
                                quality = min(3.0, quality)

                                return Setup(
                                    timestamp_utc=now,
                                    asset=asset,
                                    timeframe=timeframe,
                                    setup_type=SetupType.FIB_OTE,
                                    direction=Direction.LONG,
                                    zone_description=(
                                        f"Fib OTE {ote_low:.2f}-{ote_high:.2f} "
                                        f"(swing {swing_low:.0f}-{swing_high:.0f})"
                                    ),
                                    zone_low=ote_low,
                                    zone_high=ote_high,
                                    setup_quality_score=quality,
                                    quality_reasons=[
                                        "Fibonacci OTE long in uptrend",
                                        f"Swing: {swing_low:.0f} → {swing_high:.0f} "
                                        f"({swing_range / atr:.1f} ATR)",
                                        f"OTE zone: {ote_low:.2f} - {ote_high:.2f} "
                                        f"(61.8%-78.6%)",
                                        f"R:R to swing high: {rr:.1f}",
                                    ],
                                    trade_plan=TradePlan(
                                        direction=Direction.LONG,
                                        entry_zone_low=ote_low,
                                        entry_zone_high=ote_high,
                                        trigger_description=(
                                            "Price enters OTE zone (61.8-78.6%) "
                                            "with bullish reaction"
                                        ),
                                        stop_loss=stop,
                                        target_1=t1,
                                        target_2=t2,
                                        trail_method="Trail by 4H structure after T1",
                                        risk_reward_t1=round(rr, 2),
                                    ),
                                )

    # ---- Short OTE: bearish trend, find down-swing, price retracing ----
    if (trend == "DOWN" and trend_conf >= 0.4) or (stage in (3, 4) and trend != "UP"):
        section_h = highs[-lookback:]
        section_l = lows[-lookback:]

        # Find lowest low in lookback window
        sl_off = int(np.argmin(section_l))
        swing_low = float(section_l[sl_off])

        # Swing low must not be in last 3 bars
        if sl_off < lookback - 3:
            # Find highest high BEFORE the swing low
            before_sl_highs = section_h[: sl_off + 1]
            if len(before_sl_highs) >= 3:
                sh_off = int(np.argmax(before_sl_highs))
                swing_high = float(before_sl_highs[sh_off])
                swing_range = swing_high - swing_low

                if swing_range >= 3 * atr and sh_off < sl_off:
                    ote_low_val = swing_low + swing_range * 0.618
                    ote_high_val = swing_low + swing_range * 0.786
                    margin = atr * 0.5

                    if ote_low_val - margin <= price <= ote_high_val + margin:
                        stop = ote_high_val + atr * 1.0
                        if stop > price:
                            t1 = swing_low
                            t2 = swing_low - swing_range * 0.272

                            rr = (price - t1) / (stop - price)
                            if rr >= 1.5:
                                quality = 2.0
                                if ote_low_val <= price <= ote_high_val:
                                    quality += 0.5
                                if resistance:
                                    zmid = (resistance.low + resistance.high) / 2
                                    if abs(zmid - price) < atr * 2.0:
                                        quality += 0.5
                                quality = min(3.0, quality)

                                return Setup(
                                    timestamp_utc=now,
                                    asset=asset,
                                    timeframe=timeframe,
                                    setup_type=SetupType.FIB_OTE,
                                    direction=Direction.SHORT,
                                    zone_description=(
                                        f"Fib OTE {ote_low_val:.2f}-{ote_high_val:.2f} "
                                        f"(swing {swing_high:.0f}-{swing_low:.0f})"
                                    ),
                                    zone_low=ote_low_val,
                                    zone_high=ote_high_val,
                                    setup_quality_score=quality,
                                    quality_reasons=[
                                        "Fibonacci OTE short in downtrend",
                                        f"Swing: {swing_high:.0f} → {swing_low:.0f} "
                                        f"({swing_range / atr:.1f} ATR)",
                                        f"OTE zone: {ote_low_val:.2f} - {ote_high_val:.2f} "
                                        f"(61.8%-78.6%)",
                                        f"R:R to swing low: {rr:.1f}",
                                    ],
                                    trade_plan=TradePlan(
                                        direction=Direction.SHORT,
                                        entry_zone_low=ote_low_val,
                                        entry_zone_high=ote_high_val,
                                        trigger_description=(
                                            "Price enters OTE zone (61.8-78.6%) "
                                            "with bearish reaction"
                                        ),
                                        stop_loss=stop,
                                        target_1=t1,
                                        target_2=t2,
                                        trail_method="Trail by 4H structure after T1",
                                        risk_reward_t1=round(rr, 2),
                                    ),
                                )

    return None


def _assess_range_quality(
    entry_zone_strength: float,
    target_zone_strength: float,
    range_width_atr: float,
    distance_atr: float,
    rr: float,
    atr_percentile: float,
    is_consolidating: bool,
) -> float:
    """Assess range trade quality score 0-3.

    Higher quality when:
    - Both boundaries are strong (well-tested)
    - Range is well-defined (5-10 ATR wide)
    - Volatility is compressed (low ATR percentile)
    - Price is close to the entry boundary
    - Good R:R
    """
    score = 0.0

    # Entry zone strength
    score += min(entry_zone_strength * 1.5, 0.75)

    # Target zone strength (important for range — need it to hold)
    score += min(target_zone_strength * 1.0, 0.5)

    # Range width quality (5-10 ATR is ideal)
    if 5.0 <= range_width_atr <= 10.0:
        score += 0.5
    elif 3.0 <= range_width_atr <= 15.0:
        score += 0.25

    # Proximity to entry zone
    if distance_atr < 0.5:
        score += 0.5
    elif distance_atr < 1.0:
        score += 0.25

    # Low volatility bonus (range works better in compression)
    if atr_percentile < 30:
        score += 0.5
    elif atr_percentile < 50:
        score += 0.25

    # Consolidating price action bonus
    if is_consolidating:
        score += 0.25

    return min(score, 3.0)


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
