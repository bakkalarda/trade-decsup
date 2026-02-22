"""Replay engine — walk bar-by-bar through historical data.

Runs the full DSS pipeline (macro, flow, structure gates → setup detection →
scoring → vetoes → decisions) at each evaluation point, then feeds alerts
into the position simulator.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from dss.backtest import BacktestConfig, BacktestResult
from dss.backtest.data_loader import BacktestDataBundle
from dss.backtest.simulator import PositionSimulator
from dss.config import DSSConfig
from dss.models.setup import Direction

logger = logging.getLogger(__name__)


def run_backtest(
    bundle: BacktestDataBundle,
    cfg: DSSConfig,
    bt_cfg: BacktestConfig,
) -> BacktestResult:
    """Execute a full backtest on pre-loaded data.

    Args:
        bundle: Pre-fetched historical data from ``load_backtest_data``.
        cfg: DSS configuration.
        bt_cfg: Backtest run parameters.

    Returns:
        A ``BacktestResult`` with trades, metrics, and equity curve.
    """
    t0 = time.time()
    errors: list[str] = list(bundle.errors)  # Carry forward data-load errors

    if not bundle.is_valid:
        return BacktestResult(
            config=bt_cfg,
            metrics=_empty_metrics(),
            run_timestamp=datetime.now(timezone.utc),
            errors=errors + ["Insufficient data to run backtest"],
        )

    ohlcv = bundle.ohlcv
    asset = bundle.asset
    asset_cfg = cfg.assets.get(asset)

    sim = PositionSimulator(bt_cfg)

    # Counters
    total_alerts = 0
    total_rejected = 0
    equity_curve: list[dict] = []

    # Macro gate cache — re-evaluate once per calendar day
    _macro_cache: dict[str, dict] = {}
    _macro_cache_date: Optional[str] = None

    warmup = bt_cfg.warmup_bars

    n_bars = len(ohlcv)
    logger.info("Replay: %d bars, warmup=%d, eval_interval=%d", n_bars, warmup, bt_cfg.eval_interval)

    for i in range(n_bars):
        bar = ohlcv.iloc[i]
        bar_time = ohlcv.index[i].to_pydatetime()
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)

        bar_open = float(bar["open"])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        bar_close = float(bar["close"])

        # Compute current ATR for trailing stop
        current_atr = _get_atr_at_bar(ohlcv, i, period=asset_cfg.atr_period if asset_cfg else 14)

        # --- Process bar in simulator (fills, stops, targets) ---
        closed = sim.process_bar(i, bar_time, bar_open, bar_high, bar_low, bar_close, current_atr)

        # Record equity from simulator (compound returns)
        equity_usd = sim.equity
        initial_eq = bt_cfg.initial_equity
        equity_return_pct = ((equity_usd / initial_eq) - 1.0) * 100 if initial_eq > 0 else 0.0
        equity_curve.append({
            "timestamp": bar_time.isoformat(),
            "bar_index": i,
            "equity_usd": round(equity_usd, 2),
            "equity_pct": round(100.0 + equity_return_pct, 4),
            "cumulative_pnl_pct": round(equity_return_pct, 4),
        })

        # --- Skip warm-up bars for signal generation ---
        if i < warmup:
            continue

        # --- Only evaluate on interval ---
        if (i - warmup) % bt_cfg.eval_interval != 0:
            continue

        # --- Run the pipeline on the growing OHLCV window ---
        window = ohlcv.iloc[:i + 1]  # All bars up to current

        try:
            scan_result = _run_pipeline(
                asset, window, cfg, bundle, bar_time,
                _macro_cache, _macro_cache_date,
            )
            # Update macro cache date
            bar_date = bar_time.strftime("%Y-%m-%d")
            if bar_date != _macro_cache_date:
                _macro_cache_date = bar_date
                if "macro_state" in scan_result:
                    _macro_cache[bar_date] = scan_result["macro_state"]
        except Exception as e:
            errors.append(f"Pipeline error at bar {i}: {e}")
            continue

        # --- Extract decisions ---
        decisions = scan_result.get("decisions", {})
        alerts = decisions.get("alerts", [])
        rejected = decisions.get("rejected", [])

        total_alerts += len(alerts)
        total_rejected += len(rejected)

        # --- Submit orders for each alert (highest score first for priority) ---
        alerts.sort(key=lambda a: a.get("total_score", 0), reverse=True)
        for alert in alerts:
            trade_plan = alert.get("trade_plan", {})
            if not trade_plan:
                continue

            entry_low = trade_plan.get("entry_zone_low", 0)
            entry_high = trade_plan.get("entry_zone_high", 0)
            entry_price = (entry_low + entry_high) / 2.0
            if entry_price <= 0:
                continue

            direction_str = alert.get("direction", "LONG")
            direction = Direction.LONG if direction_str == "LONG" else Direction.SHORT

            setup_type_str = alert.get("setup_type", "A_PULLBACK")
            from dss.models.setup import SetupType
            try:
                setup_type = SetupType(setup_type_str)
            except ValueError:
                setup_type = SetupType.PULLBACK

            sim.submit_order(
                asset=asset,
                direction=direction,
                setup_type=setup_type,
                entry_price=entry_price,
                stop_loss=trade_plan.get("stop_loss", 0),
                target_1=trade_plan.get("target_1", 0),
                target_2=trade_plan.get("target_2"),
                trail_method=trade_plan.get("trail_method", ""),
                current_bar=i,
                current_time=bar_time,
                total_score=alert.get("total_score", 0),
                macro_score=alert.get("macro_score", 0),
                flow_score=alert.get("flow_score", 0),
                structure_score=alert.get("structure_score", 0),
                phase_score=alert.get("phase_score", 0),
                options_score=alert.get("options_score", 0),
                mamis_score=alert.get("mamis_score", 0),
            )

    # --- Force-close remaining positions ---
    if n_bars > 0:
        last_bar = ohlcv.iloc[-1]
        last_time = ohlcv.index[-1].to_pydatetime()
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        force_closed = sim.force_close_all(n_bars - 1, last_time, float(last_bar["close"]))
        # Update final equity point
        if equity_curve:
            equity_usd = sim.equity
            initial_eq = bt_cfg.initial_equity
            equity_return_pct = ((equity_usd / initial_eq) - 1.0) * 100 if initial_eq > 0 else 0.0
            equity_curve[-1]["equity_usd"] = round(equity_usd, 2)
            equity_curve[-1]["equity_pct"] = round(100.0 + equity_return_pct, 4)
            equity_curve[-1]["cumulative_pnl_pct"] = round(equity_return_pct, 4)

    # --- Compute metrics ---
    from dss.backtest.metrics import compute_metrics
    metrics = compute_metrics(sim.closed_trades, equity_curve, bt_cfg)
    metrics.total_alerts = total_alerts
    metrics.total_rejected = total_rejected
    metrics.orders_expired = sim.orders_expired_count
    metrics.orders_filled = sim.orders_filled

    duration = time.time() - t0

    return BacktestResult(
        config=bt_cfg,
        metrics=metrics,
        trades=sim.closed_trades,
        equity_curve=equity_curve,
        run_timestamp=datetime.now(timezone.utc),
        duration_seconds=round(duration, 2),
        bars_processed=n_bars,
        final_equity=round(sim.equity, 2),
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Pipeline runner (reuses live DSS modules)
# ---------------------------------------------------------------------------

def _run_pipeline(
    asset: str,
    window: pd.DataFrame,
    cfg: DSSConfig,
    bundle: BacktestDataBundle,
    bar_time: datetime,
    macro_cache: dict,
    macro_cache_date: Optional[str],
) -> dict:
    """Run the full DSS pipeline on the current OHLCV window.

    Mirrors the logic from ``cli.py:_run_scan`` but uses pre-fetched data
    for macro/flow instead of live API calls.
    """
    from dss.features.pivots import detect_swing_pivots
    from dss.features.zones import build_sr_zones
    from dss.features.trend_state import classify_trend
    from dss.features.stage import classify_stage
    from dss.features.acceptance import detect_acceptance_rejection
    from dss.features.wyckoff import detect_wyckoff_events
    from dss.features.mamis import classify_mamis_phase
    from dss.features.options_features import compute_options_score
    from dss.utils.math import atr as calc_atr, atr_percent, percentile_rank
    from dss.engine.setup_detector import detect_setups
    from dss.engine.scorer import score_setups
    from dss.engine.veto import apply_vetoes

    asset_cfg = cfg.assets.get(asset)
    result: dict = {
        "asset": asset,
        "timestamp_utc": bar_time.isoformat(),
    }

    df = window

    # --- Feature engineering ---
    atr_period = asset_cfg.atr_period if asset_cfg else 14
    pivot_lookback = asset_cfg.pivot_lookback if asset_cfg else 5

    atr_val = calc_atr(df["high"], df["low"], df["close"], atr_period)
    atr_pct = atr_percent(df["high"], df["low"], df["close"], atr_period)
    atr_pctile = percentile_rank(atr_val, lookback=100)

    pivots = detect_swing_pivots(df, pivot_lookback)
    zones = build_sr_zones(df, pivots, atr_val, cfg.structure_gate)
    trend = classify_trend(pivots)
    stage = classify_stage(df, pivots, trend, atr_pctile, cfg.structure_gate)
    acc_rej = detect_acceptance_rejection(df, zones, atr_val, cfg.structure_gate)
    wyckoff = detect_wyckoff_events(df, pivots, zones, stage)

    current_atr_pctile = float(atr_pctile.iloc[-1]) if len(atr_pctile) > 0 else 50.0

    result["structure"] = {
        "trend": trend,
        "stage": stage,
        "wyckoff_event": wyckoff.get("event", "NONE"),
        "atr": float(atr_val.iloc[-1]) if len(atr_val) > 0 else 0,
        "atr_pct": float(atr_pct.iloc[-1]) if len(atr_pct) > 0 else 0,
        "atr_percentile": current_atr_pctile,
        "zones_count": len(zones),
        "zones": [z.model_dump() for z in zones[:6]],
        "pivots_count": len(pivots),
        "acceptance_rejection": acc_rej,
    }

    # --- Mamis cycle classification ---
    mamis = classify_mamis_phase(df, atr_percentile=current_atr_pctile)
    result["mamis"] = mamis

    # --- Macro gate (from pre-fetched data) ---
    macro_state = _build_macro_state(bundle, bar_time, cfg)
    if macro_state:
        result["macro_state"] = macro_state

    # --- Flow gate (from pre-fetched data for crypto) ---
    if asset_cfg and asset_cfg.asset_class == "crypto":
        flow_state = _build_flow_state(bundle, bar_time, cfg)
        if flow_state:
            result["flow"] = flow_state

    # --- Options data (from pre-fetched IV history) ---
    options_data = _build_options_state(bundle, bar_time, asset)
    if options_data:
        result["options"] = options_data

    # --- Setup detection (now includes Mamis awareness) ---
    setups = detect_setups(asset, df, result.get("structure", {}), cfg, mamis=mamis)
    result["setups"] = [s.model_dump() for s in setups]

    # --- Scoring and vetoes ---
    # Pass OHLCV for MTF confluence computation
    result["ohlcv"] = df
    scored = score_setups(result.get("setups", []), result, cfg)
    vetoed = apply_vetoes(scored, result, cfg)

    # --- Decisions (inline, no DB logging) ---
    decisions = _make_decisions_no_db(vetoed, cfg, result)
    result["decisions"] = decisions

    return result


def _build_macro_state(
    bundle: BacktestDataBundle,
    bar_time: datetime,
    cfg: DSSConfig,
) -> Optional[dict]:
    """Build a synthetic macro state from pre-fetched data at bar_time.

    Now uses SPX and VIX in addition to DXY and real yields.
    SPX trend = equity risk appetite proxy.
    VIX level = volatility regime (elevated = risk-off).
    """
    from dss.utils.math import zscore_current, rolling_regression_slope, clamp

    bar_ts = pd.Timestamp(bar_time)

    # ---- DXY ----
    dxy_z = 0.0
    dxy_trend = "FLAT"
    if not bundle.dxy.empty:
        dxy_slice = bundle.dxy[bundle.dxy.index <= bar_ts]
        if len(dxy_slice) >= 20:
            dxy_close = dxy_slice["close"].tail(100)
            dxy_z = zscore_current(dxy_close, lookback=50)
            slopes = rolling_regression_slope(dxy_close, window=14)
            slope_val = float(slopes.iloc[-1]) if len(slopes) > 0 else 0.0
            dxy_trend = "UP" if slope_val > 0.01 else ("DOWN" if slope_val < -0.01 else "FLAT")

    # ---- Real yields ----
    ry_z = 0.0
    ry_trend = "FLAT"
    if not bundle.real_yields.empty:
        ry_slice = bundle.real_yields[bundle.real_yields.index <= bar_ts]
        if len(ry_slice) >= 20:
            ry_close = ry_slice["close"].tail(100)
            ry_z = zscore_current(ry_close, lookback=50)
            slopes = rolling_regression_slope(ry_close, window=14)
            slope_val = float(slopes.iloc[-1]) if len(slopes) > 0 else 0.0
            ry_trend = "UP" if slope_val > 0.001 else ("DOWN" if slope_val < -0.001 else "FLAT")

    # ---- SPX (equity risk appetite) ----
    spx_trend = "FLAT"
    spx_z = 0.0
    if not bundle.spx.empty:
        spx_slice = bundle.spx[bundle.spx.index <= bar_ts]
        if len(spx_slice) >= 20:
            spx_close = spx_slice["close"].tail(100)
            spx_z = zscore_current(spx_close, lookback=50)
            slopes = rolling_regression_slope(spx_close, window=14)
            slope_val = float(slopes.iloc[-1]) if len(slopes) > 0 else 0.0
            spx_trend = "UP" if slope_val > 0.5 else ("DOWN" if slope_val < -0.5 else "FLAT")

    # ---- VIX (volatility regime) ----
    vix_level = "NORMAL"
    vix_value = 0.0
    if not bundle.vix.empty:
        vix_slice = bundle.vix[bundle.vix.index <= bar_ts]
        if not vix_slice.empty:
            vix_value = float(vix_slice["close"].iloc[-1])
            if vix_value > 30:
                vix_level = "EXTREME"
            elif vix_value > 25:
                vix_level = "ELEVATED"
            elif vix_value > 20:
                vix_level = "HIGH"
            elif vix_value < 15:
                vix_level = "LOW"

    # ---- Gold (safe haven / inflation hedge) ----
    gold_trend = "FLAT"
    gold_corr_signal = "NEUTRAL"
    if not bundle.gold.empty:
        gold_slice = bundle.gold[bundle.gold.index <= bar_ts]
        if len(gold_slice) >= 20:
            gold_close = gold_slice["close"].tail(100)
            slopes = rolling_regression_slope(gold_close, window=14)
            slope_val = float(slopes.iloc[-1]) if len(slopes) > 0 else 0.0
            gold_trend = "UP" if slope_val > 0.1 else ("DOWN" if slope_val < -0.1 else "FLAT")
            # Correlation signal
            if gold_trend == "UP" and dxy_trend == "UP":
                gold_corr_signal = "GENUINE_RISK_OFF"
            elif gold_trend == "UP" and dxy_trend == "DOWN":
                gold_corr_signal = "RISK_OFF_ROTATION"
            elif gold_trend == "DOWN" and dxy_trend == "UP":
                gold_corr_signal = "USD_STRENGTH"

    # ---- MOVE Index (bond volatility) ----
    move_regime = "NORMAL"
    if not bundle.move_index.empty:
        move_slice = bundle.move_index[bundle.move_index.index <= bar_ts]
        if not move_slice.empty:
            move_val = float(move_slice["close"].iloc[-1])
            if move_val > 140:
                move_regime = "CRISIS"
            elif move_val > 120:
                move_regime = "STRESS"
            elif move_val > 100:
                move_regime = "ELEVATED"

    # ---- Credit Spread (HYG/IEF) ----
    credit_trend = "FLAT"
    credit_z = 0.0
    if not bundle.credit_spread.empty:
        credit_slice = bundle.credit_spread[bundle.credit_spread.index <= bar_ts]
        if len(credit_slice) >= 20:
            credit_close = credit_slice["close"].tail(100)
            credit_z = zscore_current(credit_close, lookback=50)
            slopes = rolling_regression_slope(credit_close, window=14)
            slope_val = float(slopes.iloc[-1]) if len(slopes) > 0 else 0.0
            credit_trend = "IMPROVING" if slope_val > 0.0005 else (
                "DETERIORATING" if slope_val < -0.0005 else "FLAT"
            )

    # ---- Cross-asset state (enhanced with Gold/MOVE/credit) ----
    dxy_bullish = dxy_trend == "UP" or dxy_z > 1.0
    dxy_bearish = dxy_trend == "DOWN" or dxy_z < -1.0
    ry_rising = ry_trend == "UP" or ry_z > 1.0
    ry_falling = ry_trend == "DOWN" or ry_z < -1.0

    if dxy_bullish and ry_rising:
        cross_asset_state = "CONFIRMS_RISK_OFF"
    elif dxy_bearish and ry_falling:
        cross_asset_state = "CONFIRMS_RISK_ON"
    else:
        cross_asset_state = "MIXED"

    # Override: MOVE stress or credit deterioration forces risk-off
    if move_regime in ("CRISIS", "STRESS") and cross_asset_state != "CONFIRMS_RISK_OFF":
        cross_asset_state = "CONFIRMS_RISK_OFF"
    if credit_z < -1.5 and credit_trend == "DETERIORATING":
        cross_asset_state = "CONFIRMS_RISK_OFF"

    # ---- Sentiment ----
    sentiment_state = "NEUTRAL"
    sentiment_extreme = "NONE"
    fng_value = 50
    if not bundle.fear_greed.empty and "value" in bundle.fear_greed.columns:
        fg_slice = bundle.fear_greed[bundle.fear_greed.index <= bar_ts]
        if not fg_slice.empty:
            fng_value = int(fg_slice["value"].iloc[-1])
            if fng_value <= cfg.macro_gate.fear_extreme_threshold:
                sentiment_state = "RISK_AVERSE"
                sentiment_extreme = "FEAR_EXTREME"
            elif fng_value <= 35:
                sentiment_state = "RISK_AVERSE"
            elif fng_value >= cfg.macro_gate.greed_extreme_threshold:
                sentiment_state = "RISK_SEEKING"
                sentiment_extreme = "GREED_EXTREME"
            elif fng_value >= 65:
                sentiment_state = "RISK_SEEKING"

    # ---- Regime classification (enhanced with SPX + VIX + MOVE + Gold) ----
    risk_on = 0
    risk_off = 0

    if cross_asset_state == "CONFIRMS_RISK_ON":
        risk_on += 2
    elif cross_asset_state == "CONFIRMS_RISK_OFF":
        risk_off += 2

    if sentiment_state == "RISK_SEEKING":
        risk_on += 1
    elif sentiment_state == "RISK_AVERSE":
        risk_off += 1

    # SPX contribution: rising equities = risk-on
    if spx_trend == "UP":
        risk_on += 1
    elif spx_trend == "DOWN":
        risk_off += 1

    # VIX contribution: elevated VIX = risk-off
    if vix_level in ("ELEVATED", "HIGH", "EXTREME"):
        risk_off += 1
    elif vix_level == "LOW":
        risk_on += 1

    # MOVE stress is a strong risk-off signal
    if move_regime == "CRISIS":
        risk_off += 2
    elif move_regime == "STRESS":
        risk_off += 1

    # Gold genuine risk-off (both gold & DXY rising)
    if gold_corr_signal == "GENUINE_RISK_OFF":
        risk_off += 1

    if risk_on >= 3 and risk_off <= 1:
        regime = "RISK_ON"
    elif risk_off >= 3 and risk_on <= 1:
        regime = "RISK_OFF"
    else:
        regime = "MIXED"

    # ---- Tradeability (enhanced with MOVE + credit) ----
    tradeability = "NORMAL"
    size_mult = 1.0

    # MOVE crisis = no trade; MOVE stress = caution
    if move_regime == "CRISIS":
        tradeability = "NO_TRADE"
        size_mult = 0.0
    elif move_regime == "STRESS":
        tradeability = "CAUTION"
        size_mult = min(size_mult, 0.3)

    # Credit stress
    if credit_z < -1.5:
        tradeability = "CAUTION" if tradeability == "NORMAL" else tradeability
        size_mult = min(size_mult, 0.5)

    if sentiment_extreme in ("FEAR_EXTREME", "GREED_EXTREME"):
        tradeability = "CAUTION" if tradeability == "NORMAL" else tradeability
        size_mult = min(size_mult, 0.5)
    if vix_level == "EXTREME":
        tradeability = "CAUTION" if tradeability == "NORMAL" else tradeability
        size_mult = min(size_mult, 0.5)

    # ---- Composite macro score ----
    score = 0.0
    if regime == "RISK_ON":
        score += 1.5
    elif regime == "RISK_OFF":
        score -= 1.5

    if cross_asset_state == "CONFIRMS_RISK_ON":
        score += 0.5
    elif cross_asset_state == "CONFIRMS_RISK_OFF":
        score -= 0.5

    if sentiment_state == "RISK_SEEKING":
        score += 0.3
    elif sentiment_state == "RISK_AVERSE":
        score -= 0.3

    # Contrarian sentiment at extremes
    if sentiment_extreme == "GREED_EXTREME":
        score -= 0.5
    elif sentiment_extreme == "FEAR_EXTREME":
        score += 0.5

    # SPX momentum
    if spx_trend == "UP":
        score += 0.3
    elif spx_trend == "DOWN":
        score -= 0.3

    # VIX — symmetric: high VIX = risk-off penalty, low VIX = risk-on bonus
    if vix_level == "EXTREME":
        score -= 0.5
    elif vix_level == "ELEVATED":
        score -= 0.3
    elif vix_level == "LOW":
        score += 0.3

    # MOVE penalty
    if move_regime == "CRISIS":
        score -= 0.5
    elif move_regime == "STRESS":
        score -= 0.25

    # Credit adjustment
    if credit_z < -1.0:
        score -= 0.25
    elif credit_z > 1.0:
        score += 0.25

    # Gold correlation
    if gold_corr_signal == "GENUINE_RISK_OFF":
        score -= 0.3
    elif gold_corr_signal == "RISK_OFF_ROTATION":
        score -= 0.15

    score = clamp(score, -3.0, 3.0)

    return {
        "directional_regime": regime,
        "tradeability": tradeability,
        "size_multiplier": size_mult,
        "event_risk_state": "LOW",
        "event_window": "NONE",
        "event_veto": False,
        "headline_risk_state": "LOW",
        "sentiment_state": sentiment_state,
        "sentiment_extreme": sentiment_extreme,
        "cross_asset_state": cross_asset_state,
        "spx_trend": spx_trend,
        "vix_level": vix_level,
        "vix_value": round(vix_value, 1),
        "gold_trend": gold_trend,
        "gold_corr_signal": gold_corr_signal,
        "move_regime": move_regime,
        "credit_trend": credit_trend,
        "credit_z": round(credit_z, 2),
        "macro_score": round(score, 2),
    }


def _build_flow_state(
    bundle: BacktestDataBundle,
    bar_time: datetime,
    cfg: DSSConfig,
) -> Optional[dict]:
    """Build a synthetic flow state from pre-fetched data.

    Now integrates:
      - Funding rate z-score (from ccxt)
      - Aggregated OI z-score (from DeFiLlama)
      - TVL trend (risk appetite proxy)
      - Stablecoin supply trend (capital inflow proxy)
      - BTC exchange balance trend (accumulation/distribution)
    """
    from dss.features.flow_features import (
        compute_funding_z,
        classify_crowding,
        classify_squeeze_risk,
    )
    from dss.utils.math import clamp, zscore_current

    bar_ts = pd.Timestamp(bar_time)

    # ---- Funding z-score ----
    funding_z = 0.0
    if not bundle.funding.empty:
        funding_slice = bundle.funding[bundle.funding.index <= bar_ts]
        if len(funding_slice) >= 10:
            funding_z = compute_funding_z(
                funding_slice, cfg.flow_gate.zscore_lookback
            )

    # ---- OI z-score (from DeFiLlama aggregated perps OI) ----
    oi_z = 0.0
    if not bundle.total_oi.empty:
        oi_slice = bundle.total_oi[bundle.total_oi.index <= bar_ts]
        if len(oi_slice) >= 20:
            oi_z = zscore_current(oi_slice["total_oi"].tail(100), lookback=60)

    # ---- TVL trend (capital commitment) ----
    tvl_trend = "FLAT"
    if not bundle.tvl_history.empty:
        tvl_slice = bundle.tvl_history[bundle.tvl_history.index <= bar_ts]
        if len(tvl_slice) >= 14:
            recent_tvl = tvl_slice["tvl"].tail(14)
            tvl_7d_change = (recent_tvl.iloc[-1] / recent_tvl.iloc[0] - 1) * 100
            if tvl_7d_change > 3:
                tvl_trend = "RISING"     # Capital inflow → bullish
            elif tvl_7d_change < -3:
                tvl_trend = "FALLING"    # Capital outflow → bearish

    # ---- Stablecoin supply trend ----
    stablecoin_trend = "FLAT"
    if not bundle.stablecoin_supply.empty:
        sc_slice = bundle.stablecoin_supply[bundle.stablecoin_supply.index <= bar_ts]
        if len(sc_slice) >= 14:
            recent_sc = sc_slice["total_supply"].tail(14)
            sc_7d_change = (recent_sc.iloc[-1] / recent_sc.iloc[0] - 1) * 100
            if sc_7d_change > 1:
                stablecoin_trend = "RISING"   # New capital → bullish
            elif sc_7d_change < -1:
                stablecoin_trend = "FALLING"  # Capital drain → bearish

    # ---- On-chain flow ratio (BTC only) ----
    reserve_slope = 0.0
    if not bundle.exchange_balance.empty and "flow_ratio" in bundle.exchange_balance.columns:
        eb_slice = bundle.exchange_balance[bundle.exchange_balance.index <= bar_ts]
        if len(eb_slice) >= 14:
            recent_fr = eb_slice["flow_ratio"].tail(14)
            if len(recent_fr) >= 2 and recent_fr.iloc[0] > 0:
                fr_change = (recent_fr.iloc[-1] / recent_fr.iloc[0] - 1) * 100
                # Rising flow ratio = more on-chain vs exchange = accumulation
                # Use 50% threshold — this data is inherently volatile
                if fr_change > 50:
                    reserve_slope = -1.0  # More on-chain → accumulation → bullish
                elif fr_change < -50:
                    reserve_slope = 1.0   # More exchange → distribution → bearish

    # ---- NVT Signal (network valuation) ----
    nvt_bias = 0.0
    nvt_zone = "fair_value"
    if not bundle.nvt_signal.empty and "nvt_signal" in bundle.nvt_signal.columns:
        nvt_slice = bundle.nvt_signal[bundle.nvt_signal.index <= bar_ts]
        if not nvt_slice.empty:
            latest_nvt = float(nvt_slice["nvt_signal"].iloc[-1])
            nvt_zone = str(nvt_slice["nvt_zone"].iloc[-1])
            if latest_nvt > 150:
                nvt_bias = -0.5  # Overvalued → bearish warning
            elif latest_nvt > 120:
                nvt_bias = -0.25
            elif latest_nvt < 50:
                nvt_bias = 0.5   # Undervalued → bullish signal
            elif latest_nvt < 70:
                nvt_bias = 0.25

    # ---- Hash Ribbons (miner capitulation / recovery) ----
    hash_ribbon_state = "healthy"
    hash_ribbon_bias = 0.0
    if not bundle.hash_ribbons.empty and "hash_ribbon_state" in bundle.hash_ribbons.columns:
        hr_slice = bundle.hash_ribbons[bundle.hash_ribbons.index <= bar_ts]
        if not hr_slice.empty:
            hash_ribbon_state = str(hr_slice["hash_ribbon_state"].iloc[-1])
            if hash_ribbon_state == "recovery_buy":
                hash_ribbon_bias = 1.0  # Strong buy signal (historically ~95% accurate)
            elif hash_ribbon_state == "capitulation":
                # Capitulation is short-term bearish but eventually leads to buy
                # Check if we're in late capitulation (hashrate recovering)
                hash_roc = float(hr_slice["hash_roc_30d"].iloc[-1]) if "hash_roc_30d" in hr_slice.columns else 0
                if hash_roc > 0:
                    hash_ribbon_bias = 0.25  # Late capitulation, recovering
                else:
                    hash_ribbon_bias = -0.25  # Active capitulation

    # ---- Miner Revenue (miner economics) ----
    miner_stress_bias = 0.0
    if not bundle.miner_revenue.empty and "miner_stress" in bundle.miner_revenue.columns:
        mr_slice = bundle.miner_revenue[bundle.miner_revenue.index <= bar_ts]
        if not mr_slice.empty:
            is_stress = bool(mr_slice["miner_stress"].iloc[-1])
            miner_roc = float(mr_slice["miner_rev_roc_30d"].iloc[-1]) if "miner_rev_roc_30d" in mr_slice.columns and not pd.isna(mr_slice["miner_rev_roc_30d"].iloc[-1]) else 0
            if is_stress and miner_roc < -20:
                miner_stress_bias = -0.5  # Severe miner sell pressure
            elif is_stress:
                miner_stress_bias = -0.25  # Moderate miner stress

    # ---- SSR (Stablecoin Supply Ratio) ----
    ssr_bias = 0.0
    ssr_zone = "normal"
    if not bundle.ssr.empty and "ssr" in bundle.ssr.columns:
        ssr_slice = bundle.ssr[bundle.ssr.index <= bar_ts]
        if not ssr_slice.empty:
            latest_ssr = float(ssr_slice["ssr"].iloc[-1])
            ssr_zone = str(ssr_slice["ssr_zone"].iloc[-1])
            ssr_roc = float(ssr_slice["ssr_roc_14d"].iloc[-1]) if "ssr_roc_14d" in ssr_slice.columns and not pd.isna(ssr_slice["ssr_roc_14d"].iloc[-1]) else 0
            if latest_ssr < 5:
                ssr_bias = 0.5   # Massive dry powder → bullish
            elif latest_ssr < 8:
                ssr_bias = 0.25  # Good buying power
            elif latest_ssr > 15:
                ssr_bias = -0.5  # Low buying power → bearish
            elif latest_ssr > 12:
                ssr_bias = -0.25

    # ---- Binance Futures Intelligence ----
    taker_bias = 0.0
    ls_contrarian = 0.0

    # Top trader L/S ratio (contrarian signal)
    if not bundle.top_trader_ls.empty:
        ls_slice = bundle.top_trader_ls[bundle.top_trader_ls.index <= bar_ts]
        if not ls_slice.empty:
            latest_ls = float(ls_slice.iloc[-1]["long_short_ratio"])
            if latest_ls > 1.5:
                ls_contrarian = -0.5
            elif latest_ls > 1.2:
                ls_contrarian = -0.25
            elif latest_ls < 0.67:
                ls_contrarian = 0.5
            elif latest_ls < 0.83:
                ls_contrarian = 0.25

    # Taker buy/sell ratio (direct aggression signal)
    if not bundle.taker_buy_sell.empty:
        taker_slice = bundle.taker_buy_sell[bundle.taker_buy_sell.index <= bar_ts]
        if not taker_slice.empty:
            latest_taker = float(taker_slice.iloc[-1]["buy_sell_ratio"])
            if latest_taker > 1.15:
                taker_bias = 0.5
            elif latest_taker > 1.05:
                taker_bias = 0.25
            elif latest_taker < 0.85:
                taker_bias = -0.5
            elif latest_taker < 0.95:
                taker_bias = -0.25

    # ---- Classify crowding & squeeze ----
    crowding = classify_crowding(funding_z, oi_z, cfg.flow_gate)
    squeeze_long = classify_squeeze_risk(funding_z, oi_z, "LONG", cfg.flow_gate)
    squeeze_short = classify_squeeze_risk(funding_z, oi_z, "SHORT", cfg.flow_gate)

    # ---- Flow bias (enhanced) ----
    bull_signals = 0
    bear_signals = 0

    if funding_z < -1.0:
        bull_signals += 1
    elif funding_z > 1.0:
        bear_signals += 1

    if oi_z > 1.5:
        bear_signals += 1  # Very high OI = overcrowded
    elif oi_z < -1.0:
        bull_signals += 1  # Low OI = room to grow

    if tvl_trend == "RISING":
        bull_signals += 1
    elif tvl_trend == "FALLING":
        bear_signals += 1

    if stablecoin_trend == "RISING":
        bull_signals += 1
    elif stablecoin_trend == "FALLING":
        bear_signals += 1

    if reserve_slope < 0:
        bull_signals += 1  # Exchange outflows = bullish
    elif reserve_slope > 0:
        bear_signals += 1  # Exchange inflows = bearish

    # NVT Signal (network valuation — medium weight)
    if nvt_bias > 0:
        bull_signals += abs(nvt_bias)
    elif nvt_bias < 0:
        bear_signals += abs(nvt_bias)

    # Hash Ribbons (miner capitulation/recovery — high weight for recovery signal)
    if hash_ribbon_bias > 0:
        bull_signals += hash_ribbon_bias
    elif hash_ribbon_bias < 0:
        bear_signals += abs(hash_ribbon_bias)

    # Miner stress (sell pressure indicator)
    if miner_stress_bias < 0:
        bear_signals += abs(miner_stress_bias)

    # SSR (stablecoin dry powder — medium weight)
    if ssr_bias > 0:
        bull_signals += abs(ssr_bias)
    elif ssr_bias < 0:
        bear_signals += abs(ssr_bias)

    # Taker aggression (direct market pressure, high weight)
    if taker_bias > 0:
        bull_signals += 1.5 * abs(taker_bias) / 0.5  # Normalize to a 1.5 weight
    elif taker_bias < 0:
        bear_signals += 1.5 * abs(taker_bias) / 0.5

    # Top trader L/S contrarian
    if ls_contrarian > 0:
        bull_signals += abs(ls_contrarian)
    elif ls_contrarian < 0:
        bear_signals += abs(ls_contrarian)

    if bull_signals >= 3 and bear_signals <= 1:
        flow_bias = "BULLISH"
    elif bear_signals >= 3 and bull_signals <= 1:
        flow_bias = "BEARISH"
    else:
        flow_bias = "NEUTRAL"

    # ---- Flow score (enhanced with Binance intelligence) ----
    score = 0.0
    if flow_bias == "BULLISH":
        score += 1.5
    elif flow_bias == "BEARISH":
        score -= 1.5

    # Taker aggression (strongest direct signal)
    score += taker_bias

    # Top trader L/S contrarian
    score += ls_contrarian * 0.5

    # Crowding penalizes the CROWDED direction:
    # Positive funding_z = longs crowded → subtract (penalize bullish)
    # Negative funding_z = shorts crowded → add (penalize bearish)
    if crowding.value == "EXTREME":
        if funding_z > 0:
            score -= 0.5   # Crowded long → penalize bullish
        elif funding_z < 0:
            score += 0.5   # Crowded short → penalize bearish
    elif crowding.value == "HOT":
        if funding_z > 0:
            score -= 0.3
        elif funding_z < 0:
            score += 0.3

    # TVL / stablecoin bonus
    if tvl_trend == "RISING":
        score += 0.3
    elif tvl_trend == "FALLING":
        score -= 0.3
    if stablecoin_trend == "RISING":
        score += 0.3
    elif stablecoin_trend == "FALLING":
        score -= 0.3

    # On-chain analytics (article framework metrics)
    score += nvt_bias * 0.5           # NVT valuation signal
    score += hash_ribbon_bias * 0.5   # Miner health / capitulation
    score += miner_stress_bias * 0.3  # Miner sell pressure
    score += ssr_bias * 0.4           # Stablecoin buying power

    score = clamp(score, -3.0, 3.0)

    mult_map = {"CLEAN": 1.0, "WARM": 0.7, "HOT": 0.5, "EXTREME": 0.3}

    return {
        "flow_bias": flow_bias,
        "crowding_state": crowding.value,
        "squeeze_risk": max(squeeze_long.value, squeeze_short.value),
        "funding_z": round(funding_z, 3),
        "oi_z": round(oi_z, 3),
        "tvl_trend": tvl_trend,
        "stablecoin_trend": stablecoin_trend,
        "reserve_slope": reserve_slope,
        "taker_bias": round(taker_bias, 3),
        "ls_contrarian": round(ls_contrarian, 3),
        "nvt_bias": round(nvt_bias, 3),
        "nvt_zone": nvt_zone,
        "hash_ribbon_state": hash_ribbon_state,
        "hash_ribbon_bias": round(hash_ribbon_bias, 3),
        "miner_stress_bias": round(miner_stress_bias, 3),
        "ssr_bias": round(ssr_bias, 3),
        "ssr_zone": ssr_zone,
        "positioning_size_multiplier": mult_map.get(crowding.value, 1.0),
        "flow_score": round(score, 2),
    }


def _build_options_state(
    bundle: BacktestDataBundle,
    bar_time: datetime,
    asset: str,
) -> Optional[dict]:
    """Build options state from pre-fetched IV history.

    For backtest: we use DVOL (Deribit Volatility Index) history only.
    No synthetic P/C ratio — that would be circular (IV already captures
    the same information). Instead, derive the options score purely from:
      - IV percentile (high IV = fear = contrarian long signal)
      - IV rate-of-change (rising IV = uncertainty)
      - IV term structure (if available)
    """
    from dss.features.options_features import compute_iv_percentile

    if not hasattr(bundle, "iv_history") or bundle.iv_history.empty:
        return None

    bar_ts = pd.Timestamp(bar_time)
    iv_slice = bundle.iv_history[bundle.iv_history.index <= bar_ts]
    if len(iv_slice) < 20:
        return None

    iv_pctile = compute_iv_percentile(iv_slice, lookback=252)
    current_iv = float(iv_slice["iv_close"].iloc[-1])

    # IV rate-of-change (5-bar)
    if len(iv_slice) >= 6:
        iv_roc = (current_iv / float(iv_slice["iv_close"].iloc[-6]) - 1.0) * 100
    else:
        iv_roc = 0.0

    # Pure IV-based options score (-2..+2)
    # Logic: extremely high IV (>90th pctile) = market panic → contrarian long
    #        extremely low IV (<10th pctile) = complacency → contrarian warning
    #        Rapidly rising IV penalizes longs, falling IV is neutral
    score = 0.0

    # Contrarian IV percentile signal
    if iv_pctile >= 90:
        score += 1.5   # Extreme fear → contrarian bullish
    elif iv_pctile >= 75:
        score += 0.75  # Elevated fear
    elif iv_pctile <= 10:
        score -= 1.5   # Extreme complacency → contrarian bearish
    elif iv_pctile <= 25:
        score -= 0.75  # Low fear / complacency

    # IV momentum (fast IV rise = uncertainty, penalizes longs)
    if iv_roc > 30:
        score -= 0.5   # Rapid IV expansion — destabilizing
    elif iv_roc < -20:
        score += 0.5   # IV collapse — stabilizing

    # Clamp to -2..+2
    score = max(-2.0, min(2.0, score))

    return {
        "iv_percentile": iv_pctile,
        "current_iv": round(current_iv, 2),
        "iv_roc_5bar": round(iv_roc, 2),
        "options_score": round(score, 2),
    }


def _make_decisions_no_db(setups: list[dict], cfg: DSSConfig, scan_result: dict | None = None) -> dict:
    """Make alert/reject decisions without writing to the database.

    Mirrors ``dss.engine.decision.make_decisions`` but skips DB logging
    to keep the backtest fast and side-effect free.

    Thresholds are adapted based on:
      - Asset class (metals lack flow + options gates → lower capacity)
      - Macro regime (RISK_ON → permissive, RISK_OFF → selective)
      - Degraded gates (missing options on crypto → penalty)
    """
    alerts = []
    rejected = []

    # Determine asset class for threshold scaling
    asset_name = scan_result.get("asset", "") if scan_result else ""
    asset_cfg = cfg.assets.get(asset_name)
    asset_class = asset_cfg.asset_class if asset_cfg else "crypto"

    # Regime-adaptive threshold adjustment
    threshold_adj = 0.0
    if scan_result:
        macro = scan_result.get("macro_state", {})
        regime = macro.get("directional_regime", "MIXED")
        event_risk = macro.get("event_risk_state", "LOW")

        if regime == "RISK_ON" and event_risk == "LOW":
            threshold_adj = -0.5  # Slightly more permissive in clear risk-on
        elif regime == "RISK_OFF":
            threshold_adj = +1.0  # More selective in risk-off
        elif event_risk in ("HIGH", "ELEVATED"):
            threshold_adj = +0.5  # More selective around events

        # Degraded-gate penalty for CRYPTO only: if options gate returned
        # no data, the score is missing ~2 pts → raise threshold.
        # Metals structurally lack flow + options, so no penalty applied.
        if asset_class == "crypto":
            options = scan_result.get("options")
            if not options or options.get("options_score", 0) == 0:
                threshold_adj += 1.0  # Compensate for missing options on crypto

    for setup in setups:
        direction = setup.get("direction", "LONG")
        total = setup.get("total_score", 0.0)
        vetoed = setup.get("vetoed", False)
        base_threshold = cfg.scoring.long_threshold if direction == "LONG" else abs(cfg.scoring.short_threshold)
        # Metals lack flow (±3) and options (±2) gates → scale threshold
        # to ~71% of crypto capacity (12.5/17.5)
        if asset_class == "metals":
            base_threshold = base_threshold * 0.71
        threshold = base_threshold + threshold_adj

        passes = False
        if direction == "LONG" and total >= threshold:
            passes = True
        elif direction == "SHORT" and total >= threshold:
            passes = True

        if passes and not vetoed:
            setup["is_alert"] = True
            alerts.append(setup)
        else:
            setup["is_alert"] = False
            gap = threshold - total
            rejected.append({
                "asset": setup.get("asset", ""),
                "setup_type": setup.get("setup_type", ""),
                "direction": direction,
                "total_score": total,
                "score_gap": round(abs(gap), 2),
                "threshold_used": round(threshold, 1),
                "vetoed": vetoed,
                "veto_reasons": setup.get("veto_reasons", []),
            })

    return {"alerts": alerts, "rejected": rejected}


def _get_atr_at_bar(ohlcv: pd.DataFrame, bar_idx: int, period: int = 14) -> float:
    """Compute ATR at the given bar index."""
    if bar_idx < period:
        return 0.0
    window = ohlcv.iloc[max(0, bar_idx - period * 2):bar_idx + 1]
    from dss.utils.math import atr as calc_atr
    atr_series = calc_atr(window["high"], window["low"], window["close"], period)
    if atr_series.empty:
        return 0.0
    return float(atr_series.iloc[-1])


def _empty_metrics():
    from dss.backtest import BacktestMetrics
    return BacktestMetrics()
