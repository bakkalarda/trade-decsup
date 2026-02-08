"""Flow & Positioning Gate — on-chain + derivatives analysis.

For each asset, produces:
  - flow_bias: BULLISH | NEUTRAL | BEARISH
  - crowding_state, squeeze_risk
  - positioning vetoes
  - sizing multiplier
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
        current_oi = deriv_conn.fetch_open_interest(perp)
        if current_oi is not None:
            result["oi_z"] = 0.0  # Need history for z-score; use snapshot for now
    except Exception as e:
        logger.warning("OI fetch failed for %s: %s", perp, e)

    # --- On-chain (Tier A only: BTC, ETH) ---
    if asset_cfg.tier == "A":
        try:
            onchain_conn = OnchainConnector()
            flow_data = onchain_conn.fetch_exchange_flow_proxy(asset)
            if flow_data:
                trend = flow_data.get("trend", "")
                if trend == "decreasing":
                    result["reserve_slope"] = -1.0  # Simplified: outflows
                elif trend == "increasing":
                    result["reserve_slope"] = 1.0   # Simplified: inflows
                else:
                    result["reserve_slope"] = 0.0
            onchain_conn.close()
        except Exception as e:
            logger.warning("On-chain fetch failed for %s: %s", asset, e)

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

    # --- Flow bias ---
    flow_bias = _classify_flow_bias(funding_z, result.get("reserve_slope", 0.0), flow_cfg)
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

    # --- Flow score ---
    result["flow_score"] = _compute_flow_score(flow_bias, crowding, funding_z, reserve_slope)

    return result


def _classify_flow_bias(
    funding_z: float,
    reserve_slope: float,
    cfg: FlowGateConfig,
) -> FlowBias:
    """Classify directional flow bias."""
    bull_signals = 0
    bear_signals = 0

    # Funding
    if funding_z < -1.0:
        bull_signals += 1  # Negative funding = shorts paying longs
    elif funding_z > 1.0:
        bear_signals += 1  # Positive funding = longs paying shorts

    # Reserves
    if reserve_slope < cfg.reserve_slope_bullish:
        bull_signals += 1  # Outflows = supply leaving
    elif reserve_slope > cfg.reserve_slope_bearish:
        bear_signals += 1  # Inflows = supply arriving

    if bull_signals > bear_signals:
        return FlowBias.BULLISH
    elif bear_signals > bull_signals:
        return FlowBias.BEARISH
    return FlowBias.NEUTRAL


def _compute_flow_score(
    bias: FlowBias, crowding: CrowdingState, funding_z: float, reserve_slope: float
) -> float:
    """Compute flow score (-3..+3)."""
    score = 0.0

    if bias == FlowBias.BULLISH:
        score += 1.5
    elif bias == FlowBias.BEARISH:
        score -= 1.5

    if crowding == CrowdingState.EXTREME:
        score -= 0.5 if funding_z > 0 else 0.5  # Penalize direction of crowd
    elif crowding == CrowdingState.HOT:
        score -= 0.3 if funding_z > 0 else 0.3

    return clamp(score, -3.0, 3.0)
