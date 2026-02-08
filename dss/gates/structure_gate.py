"""Technical Structure Gate — full structural analysis for an asset+timeframe.

Combines: pivots, zones, trend, stage, acceptance/rejection, Wyckoff events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from dss.config import DSSConfig
from dss.connectors.price import CryptoConnector, TraditionalConnector
from dss.features.pivots import detect_swing_pivots
from dss.features.zones import build_sr_zones, find_nearest_zones
from dss.features.trend_state import classify_trend
from dss.features.stage import classify_stage
from dss.features.acceptance import detect_acceptance_rejection
from dss.features.wyckoff import detect_wyckoff_events
from dss.features.trendlines import fit_trendlines
from dss.models.structure_state import (
    StructureState, TrendDirection, Stage, WyckoffEvent,
    PhaseAlignment, VolatilityRegime,
)
from dss.utils.math import atr as calc_atr, atr_percent, percentile_rank
from dss.storage.repository import Repository

logger = logging.getLogger(__name__)


def evaluate_structure_gate(
    asset: str,
    timeframe: str,
    cfg: DSSConfig,
) -> StructureState:
    """Run the full structure gate for an asset+timeframe.

    Fetches price data, computes all features, returns a StructureState.
    """
    now = datetime.now(timezone.utc)
    asset_cfg = cfg.assets.get(asset)
    if not asset_cfg:
        return StructureState(timestamp_utc=now, asset=asset, timeframe=timeframe)

    # Fetch OHLCV
    try:
        if asset_cfg.asset_class == "crypto":
            conn = CryptoConnector(asset_cfg.exchange)
            df = conn.fetch_ohlcv(asset_cfg.ccxt_symbol, timeframe, limit=asset_cfg.zone_lookback_bars)
        else:
            conn = TraditionalConnector()
            df = conn.fetch_ohlcv(asset_cfg.yfinance_symbol, timeframe, limit=asset_cfg.zone_lookback_bars)
    except Exception as e:
        logger.error("Failed to fetch data for %s %s: %s", asset, timeframe, e)
        return StructureState(timestamp_utc=now, asset=asset, timeframe=timeframe)

    if df.empty or len(df) < 20:
        return StructureState(timestamp_utc=now, asset=asset, timeframe=timeframe)

    # Cache data
    try:
        repo = Repository()
        repo.cache_ohlcv(asset, timeframe, df)
        repo.close()
    except Exception:
        pass

    current_price = float(df["close"].iloc[-1])

    # ATR
    atr_series = calc_atr(df["high"], df["low"], df["close"], asset_cfg.atr_period)
    atr_pct_series = atr_percent(df["high"], df["low"], df["close"], asset_cfg.atr_period)
    atr_pctile = percentile_rank(atr_series, lookback=100)

    current_atr = float(atr_series.iloc[-1]) if len(atr_series) > 0 else 0.0
    current_atr_pct = float(atr_pct_series.iloc[-1]) if len(atr_pct_series) > 0 else 0.0
    current_atr_percentile = float(atr_pctile.iloc[-1]) if len(atr_pctile) > 0 else 50.0

    # Volatility regime
    if current_atr_percentile <= cfg.structure_gate.vol_low_percentile:
        vol_regime = VolatilityRegime.LOW
    elif current_atr_percentile >= cfg.structure_gate.vol_high_percentile:
        vol_regime = VolatilityRegime.HIGH
    else:
        vol_regime = VolatilityRegime.NORMAL

    # Swing pivots
    pivots = detect_swing_pivots(df, asset_cfg.pivot_lookback)

    # S/R Zones
    zones = build_sr_zones(df, pivots, atr_series, cfg.structure_gate)

    # Nearest zones
    nearest_sup, nearest_res = find_nearest_zones(zones, current_price)

    # Distance to zones in ATR
    dist_sup = None
    dist_res = None
    if nearest_sup and current_atr > 0:
        dist_sup = (current_price - (nearest_sup.low + nearest_sup.high) / 2) / current_atr
    if nearest_res and current_atr > 0:
        dist_res = ((nearest_res.low + nearest_res.high) / 2 - current_price) / current_atr

    # In-zone detection
    in_zone = False
    zone_type = None
    for z in zones:
        if z.low <= current_price <= z.high:
            in_zone = True
            zone_type = "support" if z.is_support else "resistance"
            break

    # Trend
    trend = classify_trend(pivots, cfg.structure_gate.stage_trend_hhhl_min_swings)
    trend_dir = TrendDirection(trend["direction"])
    trend_conf = trend["confidence"]

    # Stage
    stage_info = classify_stage(df, pivots, trend, atr_pctile, cfg.structure_gate)
    stage_val = Stage(stage_info["stage"])
    stage_conf = stage_info["confidence"]

    # Acceptance / Rejection
    acc_rej = detect_acceptance_rejection(df, zones, atr_series, cfg.structure_gate)

    # Wyckoff events
    wyckoff_info = detect_wyckoff_events(df, pivots, zones, stage_info)
    wyckoff_event = WyckoffEvent(wyckoff_info.get("event", "NONE"))
    phase_align = PhaseAlignment(wyckoff_info.get("phase_alignment", "ALIGNED"))

    # Trendlines
    trendlines = fit_trendlines(pivots, cfg.structure_gate.trendline_max_residual_atr, current_atr)

    # Structure score
    structure_score = _compute_structure_score(trend_dir, trend_conf, zones, dist_sup, dist_res)
    phase_score = _compute_phase_score(stage_val, wyckoff_event, phase_align)

    return StructureState(
        timestamp_utc=now,
        asset=asset,
        timeframe=timeframe,
        trend_direction=trend_dir,
        trend_confidence=round(trend_conf, 2),
        stage=stage_val,
        stage_confidence=round(stage_conf, 2),
        wyckoff_event=wyckoff_event,
        phase_alignment=phase_align,
        atr=round(current_atr, 6),
        atr_pct=round(current_atr_pct, 4),
        volatility_regime=vol_regime,
        atr_percentile=round(current_atr_percentile, 1),
        sr_zones=zones[:10],
        nearest_support=nearest_sup,
        nearest_resistance=nearest_res,
        trendlines=trendlines[:4],
        recent_pivots=pivots[-10:],
        current_price=current_price,
        distance_to_support_atr=round(dist_sup, 2) if dist_sup is not None else None,
        distance_to_resistance_atr=round(dist_res, 2) if dist_res is not None else None,
        in_zone=in_zone,
        zone_type=zone_type,
        last_acceptance=acc_rej.get("last_acceptance"),
        last_rejection=acc_rej.get("last_rejection"),
        structure_score=round(structure_score, 2),
        phase_score=round(phase_score, 2),
    )


def _compute_structure_score(
    trend: TrendDirection,
    trend_conf: float,
    zones: list,
    dist_sup: float | None,
    dist_res: float | None,
) -> float:
    """Compute structure score (-3..+3)."""
    score = 0.0

    # Trend clarity
    if trend != TrendDirection.SIDEWAYS:
        score += trend_conf * 1.5
    else:
        score += 0.0

    # Zone quality
    if zones:
        best = zones[0]
        strength = best.strength if hasattr(best, "strength") else 0
        score += min(strength * 1.5, 1.5)

    return max(-3.0, min(3.0, score))


def _compute_phase_score(
    stage: Stage,
    wyckoff: WyckoffEvent,
    alignment: PhaseAlignment,
) -> float:
    """Compute phase alignment score (-2..+2)."""
    score = 0.0

    if alignment == PhaseAlignment.ALIGNED:
        score += 1.0
    else:
        score -= 1.0

    if wyckoff != WyckoffEvent.NONE:
        score += 0.5  # Active Wyckoff event adds value

    return max(-2.0, min(2.0, score))
