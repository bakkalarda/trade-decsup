"""Flow & Positioning Gate — on-chain + derivatives + Binance intelligence.

For each asset, produces:
  - flow_bias: BULLISH | NEUTRAL | BEARISH
  - crowding_state, squeeze_risk
  - positioning vetoes (including liquidation-aware vetoes)
  - sizing multiplier
  - Binance-sourced: taker aggression, top-trader positioning, liquidation imbalance
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from dss.config import AssetConfig, FlowGateConfig
from dss.connectors.derivatives import DerivativesConnector
from dss.connectors.onchain import OnchainConnector
from dss.features.flow_features import (
    compute_funding_z, compute_oi_z, classify_crowding, classify_squeeze_risk,
)
from dss.models.flow_state import (
    FlowBias, CrowdingState, SqueezeRisk, LiquidityState,
    ProfitTakingState,
)
from dss.utils.math import clamp

import pandas as pd

logger = logging.getLogger(__name__)


def evaluate_flow_gate(
    asset: str,
    asset_cfg: AssetConfig,
    flow_cfg: FlowGateConfig,
) -> dict:
    """Evaluate flow & positioning gate for a single asset.

    Returns a dict matching FlowState model fields.
    """
    now = datetime.now(timezone.utc)
    result = {
        "timestamp_utc": now.isoformat(),
        "asset": asset,
        "flow_bias": FlowBias.NEUTRAL.value,
        "crowding_state": CrowdingState.CLEAN.value,
        "squeeze_risk": SqueezeRisk.LOW.value,
        "liquidity_state": LiquidityState.GOOD.value,
        "profit_taking_state": ProfitTakingState.NORMAL.value,
        "distribution_flag": False,
        "long_veto": False,
        "short_veto": False,
        "long_veto_reasons": [],
        "short_veto_reasons": [],
        "positioning_size_multiplier": 1.0,
        "flow_score": 0.0,
    }

    if asset_cfg.asset_class != "crypto":
        # Metals: flow gate is minimal (USD + real yields from macro gate)
        result["flow_bias"] = FlowBias.NEUTRAL.value
        return result

    perp = asset_cfg.ccxt_perp_symbol
    if not perp:
        return result

    deriv_conn = DerivativesConnector(asset_cfg.exchange)

    # --- Funding rate ---
    funding_z = 0.0
    try:
        funding_hist = deriv_conn.fetch_funding_history(perp, days=flow_cfg.zscore_lookback)
        if not funding_hist.empty:
            funding_z = compute_funding_z(funding_hist, flow_cfg.zscore_lookback)
            result["funding_z"] = round(funding_z, 3)
    except Exception as e:
        logger.warning("Funding history failed for %s: %s", perp, e)

    # --- Open interest ---
    oi_z = 0.0
    try:
        # Use DeFiLlama aggregated OI history for proper z-score
        oi_hist = deriv_conn.fetch_defillama_perps_oi()
        if not oi_hist.empty and len(oi_hist) >= 20:
            oi_z = compute_oi_z(oi_hist["total_oi"], flow_cfg.zscore_lookback)
            result["oi_z"] = round(oi_z, 3)
        else:
            # Fallback: snapshot only, z-score unavailable
            current_oi = deriv_conn.fetch_open_interest(perp)
            if current_oi is not None:
                result["oi_snapshot"] = current_oi
                result["oi_z"] = 0.0
    except Exception as e:
        logger.warning("OI fetch failed for %s: %s", perp, e)

    # --- On-chain (Tier A only: BTC, ETH) ---
    nvt_bias = 0.0
    hash_ribbon_bias = 0.0
    miner_stress_bias = 0.0
    ssr_bias = 0.0

    if asset_cfg.tier == "A":
        try:
            onchain_conn = OnchainConnector()
            flow_data = onchain_conn.fetch_exchange_flow_proxy(asset)
            if flow_data:
                flow_signal = flow_data.get("flow_signal", "neutral")
                if flow_signal == "accumulation":
                    result["reserve_slope"] = -1.0  # On-chain accumulation
                elif flow_signal == "distribution":
                    result["reserve_slope"] = 1.0   # Exchange distribution
                else:
                    result["reserve_slope"] = 0.0

            # ---- Advanced On-Chain Analytics (BTC only) ----
            if asset.upper() == "BTC":
                # NVT Signal
                try:
                    nvt_df = onchain_conn.fetch_nvt_signal(timespan="180days")
                    if not nvt_df.empty and "nvt_signal" in nvt_df.columns:
                        latest_nvt = float(nvt_df["nvt_signal"].iloc[-1])
                        nvt_zone = str(nvt_df["nvt_zone"].iloc[-1])
                        result["nvt_signal"] = round(latest_nvt, 1)
                        result["nvt_zone"] = nvt_zone
                        if latest_nvt > 150:
                            nvt_bias = -0.5
                        elif latest_nvt > 120:
                            nvt_bias = -0.25
                        elif latest_nvt < 50:
                            nvt_bias = 0.5
                        elif latest_nvt < 70:
                            nvt_bias = 0.25
                except Exception as e:
                    logger.warning("NVT Signal live failed: %s", e)

                # Hash Ribbons
                try:
                    hr_df = onchain_conn.fetch_hash_ribbons(timespan="180days")
                    if not hr_df.empty and "hash_ribbon_state" in hr_df.columns:
                        hr_state = str(hr_df["hash_ribbon_state"].iloc[-1])
                        result["hash_ribbon_state"] = hr_state
                        if hr_state == "recovery_buy":
                            hash_ribbon_bias = 1.0
                        elif hr_state == "capitulation":
                            hash_roc = float(hr_df["hash_roc_30d"].iloc[-1]) if "hash_roc_30d" in hr_df.columns else 0
                            hash_ribbon_bias = 0.25 if hash_roc > 0 else -0.25
                except Exception as e:
                    logger.warning("Hash Ribbons live failed: %s", e)

                # Miner Revenue
                try:
                    mr_df = onchain_conn.fetch_miner_revenue(timespan="180days")
                    if not mr_df.empty and "miner_stress" in mr_df.columns:
                        is_stress = bool(mr_df["miner_stress"].iloc[-1])
                        miner_roc = float(mr_df["miner_rev_roc_30d"].iloc[-1]) if not pd.isna(mr_df["miner_rev_roc_30d"].iloc[-1]) else 0
                        result["miner_stress"] = is_stress
                        if is_stress and miner_roc < -20:
                            miner_stress_bias = -0.5
                        elif is_stress:
                            miner_stress_bias = -0.25
                except Exception as e:
                    logger.warning("Miner revenue live failed: %s", e)

                # SSR (Stablecoin Supply Ratio)
                try:
                    ssr_df = onchain_conn.fetch_ssr(days=180)
                    if not ssr_df.empty and "ssr" in ssr_df.columns:
                        latest_ssr = float(ssr_df["ssr"].iloc[-1])
                        ssr_zone = str(ssr_df["ssr_zone"].iloc[-1])
                        result["ssr"] = round(latest_ssr, 1)
                        result["ssr_zone"] = ssr_zone
                        if latest_ssr < 5:
                            ssr_bias = 0.5
                        elif latest_ssr < 8:
                            ssr_bias = 0.25
                        elif latest_ssr > 15:
                            ssr_bias = -0.5
                        elif latest_ssr > 12:
                            ssr_bias = -0.25
                except Exception as e:
                    logger.warning("SSR live failed: %s", e)

            onchain_conn.close()
        except Exception as e:
            logger.warning("On-chain fetch failed for %s: %s", asset, e)

    # --- Binance Futures Intelligence (L/S ratio, taker, liquidations) ---
    taker_bias = 0.0
    ls_contrarian = 0.0
    liq_imbalance = "BALANCED"

    raw_symbol = perp.replace("/", "").replace(":USDT", "").replace(":USD", "")
    try:
        # Top trader long/short ratio — contrarian signal
        top_ls = deriv_conn.fetch_top_trader_ls_ratio(raw_symbol, "4h", limit=5)
        if not top_ls.empty:
            latest_ls = float(top_ls.iloc[-1]["long_short_ratio"])
            result["top_trader_ls_ratio"] = round(latest_ls, 4)
            # If top traders are heavily long (>1.5), contrarian bearish
            # If top traders are heavily short (<0.67), contrarian bullish
            if latest_ls > 1.5:
                ls_contrarian = -0.5  # Too many top traders long
            elif latest_ls > 1.2:
                ls_contrarian = -0.25
            elif latest_ls < 0.67:
                ls_contrarian = 0.5  # Too many top traders short
            elif latest_ls < 0.83:
                ls_contrarian = 0.25
            result["ls_contrarian_signal"] = round(ls_contrarian, 3)
    except Exception as e:
        logger.warning("Top trader L/S failed for %s: %s", asset, e)

    try:
        # Taker buy/sell ratio — direct aggression signal
        taker = deriv_conn.fetch_taker_buy_sell_ratio(raw_symbol, "4h", limit=5)
        if not taker.empty:
            latest_taker = float(taker.iloc[-1]["buy_sell_ratio"])
            result["taker_buy_sell_ratio"] = round(latest_taker, 4)
            # > 1.0 = aggressive buyers, < 1.0 = aggressive sellers
            if latest_taker > 1.15:
                taker_bias = 0.5
            elif latest_taker > 1.05:
                taker_bias = 0.25
            elif latest_taker < 0.85:
                taker_bias = -0.5
            elif latest_taker < 0.95:
                taker_bias = -0.25
            result["taker_bias_signal"] = round(taker_bias, 3)
    except Exception as e:
        logger.warning("Taker buy/sell failed for %s: %s", asset, e)

    try:
        # Liquidation imbalance — cluster detection
        liqs = deriv_conn.fetch_recent_liquidations(raw_symbol, limit=100)
        if not liqs.empty:
            long_liqs = liqs[liqs["side"] == "SELL"]["notional_usd"].sum()
            short_liqs = liqs[liqs["side"] == "BUY"]["notional_usd"].sum()
            result["recent_long_liq_usd"] = round(float(long_liqs), 0)
            result["recent_short_liq_usd"] = round(float(short_liqs), 0)
            if long_liqs > short_liqs * 2.0:
                liq_imbalance = "LONG_PAIN"
            elif short_liqs > long_liqs * 2.0:
                liq_imbalance = "SHORT_PAIN"
            result["liq_imbalance"] = liq_imbalance
    except Exception as e:
        logger.warning("Liquidations fetch failed for %s: %s", asset, e)

    # --- Classify crowding ---
    crowding = classify_crowding(funding_z, oi_z, flow_cfg)
    result["crowding_state"] = crowding.value

    # --- Classify squeeze risk ---
    long_squeeze = classify_squeeze_risk(funding_z, oi_z, "LONG", flow_cfg)
    short_squeeze = classify_squeeze_risk(funding_z, oi_z, "SHORT", flow_cfg)
    # Report the higher risk
    if long_squeeze.value > short_squeeze.value:
        result["squeeze_risk"] = long_squeeze.value
    else:
        result["squeeze_risk"] = short_squeeze.value

    # --- Flow bias (enhanced with Binance intelligence + on-chain analytics) ---
    flow_bias = _classify_flow_bias(
        funding_z, result.get("reserve_slope", 0.0), taker_bias, ls_contrarian,
        nvt_bias, hash_ribbon_bias, miner_stress_bias, ssr_bias, flow_cfg
    )
    result["flow_bias"] = flow_bias.value

    # --- Positioning vetoes ---
    long_veto_reasons = []
    short_veto_reasons = []

    if crowding == CrowdingState.EXTREME and funding_z > 0:
        long_veto_reasons.append(
            f"EXTREME crowded long (funding_z={funding_z:.2f})"
        )
    if crowding == CrowdingState.EXTREME and funding_z < 0:
        short_veto_reasons.append(
            f"EXTREME crowded short (funding_z={funding_z:.2f}) — squeeze risk"
        )

    reserve_slope = result.get("reserve_slope", 0.0)
    if reserve_slope and reserve_slope >= flow_cfg.reserve_slope_bearish:
        long_veto_reasons.append(
            f"Exchange reserves increasing (inflow trend)"
        )

    # Liquidation-aware vetoes: if recent liquidations are heavily one-sided,
    # the cascade may not be over
    if liq_imbalance == "LONG_PAIN":
        long_veto_reasons.append("Heavy long liquidation cascade in progress")
    elif liq_imbalance == "SHORT_PAIN":
        short_veto_reasons.append("Heavy short liquidation cascade in progress")

    result["long_veto"] = len(long_veto_reasons) > 0
    result["short_veto"] = len(short_veto_reasons) > 0
    result["long_veto_reasons"] = long_veto_reasons
    result["short_veto_reasons"] = short_veto_reasons

    # --- Size multiplier ---
    mult_map = {
        CrowdingState.CLEAN: 1.0,
        CrowdingState.WARM: 0.7,
        CrowdingState.HOT: 0.5,
        CrowdingState.EXTREME: 0.3,
    }
    result["positioning_size_multiplier"] = mult_map.get(crowding, 1.0)

    # --- Flow score (enhanced with on-chain analytics) ---
    result["flow_score"] = _compute_flow_score(
        flow_bias, crowding, funding_z, reserve_slope, taker_bias, ls_contrarian,
        nvt_bias, hash_ribbon_bias, miner_stress_bias, ssr_bias
    )

    return result


def _classify_flow_bias(
    funding_z: float,
    reserve_slope: float,
    taker_bias: float,
    ls_contrarian: float,
    nvt_bias: float,
    hash_ribbon_bias: float,
    miner_stress_bias: float,
    ssr_bias: float,
    cfg: FlowGateConfig,
) -> FlowBias:
    """Classify directional flow bias using all available signals."""
    bull_signals = 0.0
    bear_signals = 0.0

    # Funding (weight: 1.0)
    if funding_z < -1.0:
        bull_signals += 1.0  # Negative funding = shorts paying longs
    elif funding_z > 1.0:
        bear_signals += 1.0  # Positive funding = longs paying shorts

    # Reserve slope (weight: 1.0)
    if reserve_slope < cfg.reserve_slope_bullish:
        bull_signals += 1.0  # Outflows = supply leaving
    elif reserve_slope > cfg.reserve_slope_bearish:
        bear_signals += 1.0  # Inflows = supply arriving

    # Taker aggression (weight: 0.75 — direct market pressure)
    if taker_bias > 0:
        bull_signals += abs(taker_bias) * 1.5
    elif taker_bias < 0:
        bear_signals += abs(taker_bias) * 1.5

    # Top trader L/S contrarian (weight: 0.5 — soft contrarian signal)
    if ls_contrarian > 0:
        bull_signals += ls_contrarian
    elif ls_contrarian < 0:
        bear_signals += abs(ls_contrarian)

    # NVT Signal (network valuation — medium weight)
    if nvt_bias > 0:
        bull_signals += abs(nvt_bias)
    elif nvt_bias < 0:
        bear_signals += abs(nvt_bias)

    # Hash Ribbons (miner health — high weight for recovery)
    if hash_ribbon_bias > 0:
        bull_signals += hash_ribbon_bias
    elif hash_ribbon_bias < 0:
        bear_signals += abs(hash_ribbon_bias)

    # Miner stress
    if miner_stress_bias < 0:
        bear_signals += abs(miner_stress_bias)

    # SSR (stablecoin buying power)
    if ssr_bias > 0:
        bull_signals += abs(ssr_bias)
    elif ssr_bias < 0:
        bear_signals += abs(ssr_bias)

    if bull_signals > bear_signals + 0.3:
        return FlowBias.BULLISH
    elif bear_signals > bull_signals + 0.3:
        return FlowBias.BEARISH
    return FlowBias.NEUTRAL


def _compute_flow_score(
    bias: FlowBias,
    crowding: CrowdingState,
    funding_z: float,
    reserve_slope: float,
    taker_bias: float = 0.0,
    ls_contrarian: float = 0.0,
    nvt_bias: float = 0.0,
    hash_ribbon_bias: float = 0.0,
    miner_stress_bias: float = 0.0,
    ssr_bias: float = 0.0,
) -> float:
    """Compute flow score (-3..+3) with enhanced on-chain analytics."""
    score = 0.0

    if bias == FlowBias.BULLISH:
        score += 1.0
    elif bias == FlowBias.BEARISH:
        score -= 1.0

    # Taker aggression (strongest direct signal)
    score += taker_bias

    # Top trader L/S contrarian
    score += ls_contrarian * 0.5

    # Crowding penalizes the CROWDED direction
    if crowding == CrowdingState.EXTREME:
        if funding_z > 0:
            score -= 0.5   # Crowded long
        elif funding_z < 0:
            score += 0.5   # Crowded short
    elif crowding == CrowdingState.HOT:
        if funding_z > 0:
            score -= 0.3
        elif funding_z < 0:
            score += 0.3

    # On-chain analytics (article framework)
    score += nvt_bias * 0.5           # NVT network valuation
    score += hash_ribbon_bias * 0.5   # Miner health / recovery signal
    score += miner_stress_bias * 0.3  # Miner sell pressure
    score += ssr_bias * 0.4           # Stablecoin dry powder

    return clamp(score, -3.0, 3.0)
