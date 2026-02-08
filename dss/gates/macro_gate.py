"""Macro Regime Gate — cross-asset regime classification + event risk.

Outputs:
  - directional_regime: RISK_ON | MIXED | RISK_OFF
  - tradeability: NORMAL | CAUTION | NO_TRADE
  - size_multiplier
  - event risk, headline risk, sentiment, cross-asset state
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from dss.config import DSSConfig
from dss.connectors.macro import MacroConnector
from dss.connectors.sentiment import SentimentConnector
from dss.models.macro_state import (
    MacroState, DirectionalRegime, Tradeability, RiskLevel, EventWindow,
    SentimentState, SentimentExtreme, CrossAssetState,
)
from dss.utils.math import zscore_current, rolling_regression_slope, clamp

logger = logging.getLogger(__name__)


def evaluate_macro_gate(cfg: DSSConfig) -> MacroState:
    """Run the full macro gate evaluation.

    1. Cross-asset tape (DXY, real yields)
    2. Sentiment (Fear & Greed)
    3. Combine into regime + tradeability
    """
    now = datetime.now(timezone.utc)
    macro_conn = MacroConnector()
    sent_conn = SentimentConnector()

    # --- Cross-asset tape ---
    cross_asset = _evaluate_cross_asset(macro_conn, cfg)

    # --- Sentiment ---
    sentiment = _evaluate_sentiment(sent_conn, cfg)

    # --- Regime classification ---
    regime = _classify_regime(cross_asset, sentiment)

    # --- Tradeability (conservative without event calendar) ---
    # Events and headlines are handled by the OpenClaw agent via web_search
    # The Python DSS provides the cross-asset + sentiment layer
    tradeability = Tradeability.NORMAL
    size_mult = 1.0

    if sentiment["extreme"] == SentimentExtreme.FEAR_EXTREME:
        tradeability = Tradeability.CAUTION
        size_mult = 0.5
    elif sentiment["extreme"] == SentimentExtreme.GREED_EXTREME:
        tradeability = Tradeability.CAUTION
        size_mult = 0.5

    # Macro score composite
    macro_score = _compute_composite_score(regime, cross_asset, sentiment)

    sent_conn.close()

    return MacroState(
        timestamp_utc=now,
        directional_regime=regime,
        tradeability=tradeability,
        size_multiplier=size_mult,
        event_risk_state=RiskLevel.LOW,  # Agent handles event calendar
        event_window=EventWindow.NONE,
        event_veto=False,
        headline_risk_state=RiskLevel.LOW,  # Agent handles headlines
        headline_risk_score=0.0,
        sentiment_state=sentiment["state"],
        sentiment_extreme=sentiment["extreme"],
        cross_asset_state=cross_asset["state"],
        macro_score=macro_score,
        notes=(
            "Event risk and headline risk are evaluated by the OpenClaw agent "
            "via web_search. This gate provides cross-asset and sentiment layers."
        ),
    )


def _evaluate_cross_asset(macro_conn: MacroConnector, cfg: DSSConfig) -> dict:
    """Evaluate DXY and real yields for cross-asset signal."""
    result = {
        "state": CrossAssetState.MIXED,
        "dxy_trend": "FLAT",
        "dxy_z": 0.0,
        "real_yield_trend": "FLAT",
        "real_yield_z": 0.0,
    }

    try:
        dxy = macro_conn.fetch_dxy(timeframe="1d", limit=100)
        if not dxy.empty:
            dxy_close = dxy["close"]
            result["dxy_z"] = zscore_current(dxy_close, lookback=50)
            slopes = rolling_regression_slope(dxy_close, window=14)
            slope_val = float(slopes.iloc[-1]) if len(slopes) > 0 else 0.0
            result["dxy_trend"] = "UP" if slope_val > 0.01 else ("DOWN" if slope_val < -0.01 else "FLAT")
    except Exception as e:
        logger.warning("DXY fetch failed: %s", e)

    try:
        ry = macro_conn.fetch_real_yields(limit=100)
        if not ry.empty:
            ry_close = ry["close"]
            result["real_yield_z"] = zscore_current(ry_close, lookback=50)
            slopes = rolling_regression_slope(ry_close, window=14)
            slope_val = float(slopes.iloc[-1]) if len(slopes) > 0 else 0.0
            result["real_yield_trend"] = "UP" if slope_val > 0.001 else ("DOWN" if slope_val < -0.001 else "FLAT")
    except Exception as e:
        logger.warning("Real yields fetch failed: %s", e)

    # Classification
    # RISK_OFF: USD strengthening + real yields rising
    # RISK_ON: USD weakening + real yields falling
    dxy_bullish = result["dxy_trend"] == "UP" or result["dxy_z"] > 1.0
    dxy_bearish = result["dxy_trend"] == "DOWN" or result["dxy_z"] < -1.0
    ry_rising = result["real_yield_trend"] == "UP" or result["real_yield_z"] > 1.0
    ry_falling = result["real_yield_trend"] == "DOWN" or result["real_yield_z"] < -1.0

    if dxy_bullish and ry_rising:
        result["state"] = CrossAssetState.CONFIRMS_RISK_OFF
    elif dxy_bearish and ry_falling:
        result["state"] = CrossAssetState.CONFIRMS_RISK_ON
    else:
        result["state"] = CrossAssetState.MIXED

    return result


def _evaluate_sentiment(sent_conn: SentimentConnector, cfg: DSSConfig) -> dict:
    """Evaluate Fear & Greed sentiment."""
    result = {
        "state": SentimentState.NEUTRAL,
        "extreme": SentimentExtreme.NONE,
        "value": 50,
    }

    try:
        current = sent_conn.fetch_current()
        if current:
            value = current["value"]
            result["value"] = value

            if value <= cfg.macro_gate.fear_extreme_threshold:
                result["state"] = SentimentState.RISK_AVERSE
                result["extreme"] = SentimentExtreme.FEAR_EXTREME
            elif value <= 35:
                result["state"] = SentimentState.RISK_AVERSE
            elif value >= cfg.macro_gate.greed_extreme_threshold:
                result["state"] = SentimentState.RISK_SEEKING
                result["extreme"] = SentimentExtreme.GREED_EXTREME
            elif value >= 65:
                result["state"] = SentimentState.RISK_SEEKING
    except Exception as e:
        logger.warning("Sentiment fetch failed: %s", e)

    return result


def _classify_regime(cross_asset: dict, sentiment: dict) -> DirectionalRegime:
    """Combine cross-asset and sentiment into directional regime."""
    ca_state = cross_asset["state"]
    sent_state = sentiment["state"]

    risk_on_signals = 0
    risk_off_signals = 0

    if ca_state == CrossAssetState.CONFIRMS_RISK_ON:
        risk_on_signals += 2
    elif ca_state == CrossAssetState.CONFIRMS_RISK_OFF:
        risk_off_signals += 2

    if sent_state == SentimentState.RISK_SEEKING:
        risk_on_signals += 1
    elif sent_state == SentimentState.RISK_AVERSE:
        risk_off_signals += 1

    if risk_on_signals >= 2 and risk_off_signals == 0:
        return DirectionalRegime.RISK_ON
    elif risk_off_signals >= 2 and risk_on_signals == 0:
        return DirectionalRegime.RISK_OFF
    else:
        return DirectionalRegime.MIXED


def _compute_composite_score(
    regime: DirectionalRegime, cross_asset: dict, sentiment: dict
) -> float:
    """Compute composite macro score (-3..+3)."""
    score = 0.0

    if regime == DirectionalRegime.RISK_ON:
        score += 1.5
    elif regime == DirectionalRegime.RISK_OFF:
        score -= 1.5

    if cross_asset["state"] == CrossAssetState.CONFIRMS_RISK_ON:
        score += 0.5
    elif cross_asset["state"] == CrossAssetState.CONFIRMS_RISK_OFF:
        score -= 0.5

    if sentiment["state"] == SentimentState.RISK_SEEKING:
        score += 0.5
    elif sentiment["state"] == SentimentState.RISK_AVERSE:
        score -= 0.5

    # Extreme sentiment is a contrarian warning
    if sentiment["extreme"] == SentimentExtreme.GREED_EXTREME:
        score -= 0.5
    elif sentiment["extreme"] == SentimentExtreme.FEAR_EXTREME:
        score += 0.5  # Contrarian: extreme fear can be bullish with confirmation

    return clamp(score, -3.0, 3.0)
