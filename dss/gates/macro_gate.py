"""Macro Regime Gate — cross-asset regime classification + headline risk + event risk.

Outputs:
  - directional_regime: RISK_ON | MIXED | RISK_OFF
  - tradeability: NORMAL | CAUTION | NO_TRADE
  - size_multiplier
  - headline risk (from CryptoPanic news), sentiment, cross-asset state
  - Gold/BTC correlation, MOVE index stress, credit spread signal
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

    1. Cross-asset tape (DXY, real yields, Gold, credit, MOVE)
    2. Sentiment (Fear & Greed)
    3. Headline risk (CryptoPanic news)
    4. Combine into regime + tradeability
    """
    now = datetime.now(timezone.utc)
    macro_conn = MacroConnector()
    sent_conn = SentimentConnector()

    # --- Cross-asset tape (enhanced with Gold, MOVE, credit) ---
    cross_asset = _evaluate_cross_asset(macro_conn, cfg)

    # --- Sentiment ---
    sentiment = _evaluate_sentiment(sent_conn, cfg)

    # --- Headline risk (CryptoPanic) ---
    headline_risk = _evaluate_headline_risk()

    # --- Regime classification ---
    regime = _classify_regime(cross_asset, sentiment, headline_risk)

    # --- Tradeability ---
    tradeability = Tradeability.NORMAL
    size_mult = 1.0

    if sentiment["extreme"] == SentimentExtreme.FEAR_EXTREME:
        tradeability = Tradeability.CAUTION
        size_mult = 0.5
    elif sentiment["extreme"] == SentimentExtreme.GREED_EXTREME:
        tradeability = Tradeability.CAUTION
        size_mult = 0.5

    # MOVE index stress override — bond vol is the canary in the coal mine
    if cross_asset.get("move_regime") == "CRISIS":
        tradeability = Tradeability.NO_TRADE
        size_mult = 0.0
    elif cross_asset.get("move_regime") == "STRESS":
        tradeability = Tradeability.CAUTION
        size_mult = min(size_mult, 0.3)

    # Credit spread deterioration override
    if cross_asset.get("credit_z", 0) < -1.5:
        if tradeability == Tradeability.NORMAL:
            tradeability = Tradeability.CAUTION
        size_mult = min(size_mult, 0.5)

    # Headline risk override
    headline_risk_level = RiskLevel.LOW
    headline_score = 0.0
    if headline_risk.get("headline_risk") == "HIGH":
        headline_risk_level = RiskLevel.HIGH
        headline_score = -1.0
        if tradeability == Tradeability.NORMAL:
            tradeability = Tradeability.CAUTION
        size_mult = min(size_mult, 0.5)
    elif headline_risk.get("headline_risk") == "ELEVATED":
        headline_risk_level = RiskLevel.ELEVATED
        headline_score = -0.5

    # Macro score composite
    macro_score = _compute_composite_score(regime, cross_asset, sentiment, headline_score)

    sent_conn.close()
    macro_conn.close()

    notes_parts = []
    if cross_asset.get("gold_corr_signal"):
        notes_parts.append(f"Gold signal: {cross_asset['gold_corr_signal']}")
    if cross_asset.get("move_regime"):
        notes_parts.append(f"MOVE: {cross_asset['move_regime']}")
    if headline_risk.get("headline_count", 0) > 0:
        notes_parts.append(
            f"Headlines: {headline_risk['headline_count']} analyzed, "
            f"sentiment={headline_risk.get('avg_sentiment_score', 0):.2f}"
        )
    notes = " | ".join(notes_parts) if notes_parts else "Standard macro evaluation"

    return MacroState(
        timestamp_utc=now,
        directional_regime=regime,
        tradeability=tradeability,
        size_multiplier=size_mult,
        event_risk_state=RiskLevel.LOW,
        event_window=EventWindow.NONE,
        event_veto=False,
        headline_risk_state=headline_risk_level,
        headline_risk_score=headline_score,
        sentiment_state=sentiment["state"],
        sentiment_extreme=sentiment["extreme"],
        cross_asset_state=cross_asset["state"],
        macro_score=macro_score,
        notes=notes,
    )


def _evaluate_cross_asset(macro_conn: MacroConnector, cfg: DSSConfig) -> dict:
    """Evaluate DXY, real yields, Gold, MOVE, credit spread for cross-asset signal."""
    result = {
        "state": CrossAssetState.MIXED,
        "dxy_trend": "FLAT",
        "dxy_z": 0.0,
        "real_yield_trend": "FLAT",
        "real_yield_z": 0.0,
        "gold_trend": "FLAT",
        "gold_z": 0.0,
        "gold_corr_signal": None,
        "move_regime": "NORMAL",
        "move_z": 0.0,
        "credit_trend": "FLAT",
        "credit_z": 0.0,
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

    # --- Gold (safe haven / inflation hedge) ---
    try:
        gold = macro_conn.fetch_gold(timeframe="1d", limit=100)
        if not gold.empty:
            gold_close = gold["close"]
            result["gold_z"] = zscore_current(gold_close, lookback=50)
            slopes = rolling_regression_slope(gold_close, window=14)
            slope_val = float(slopes.iloc[-1]) if len(slopes) > 0 else 0.0
            result["gold_trend"] = "UP" if slope_val > 0.1 else ("DOWN" if slope_val < -0.1 else "FLAT")

            # Gold rising while DXY rising = genuine risk-off (gold overcoming USD headwind)
            if result["gold_trend"] == "UP" and result["dxy_trend"] == "UP":
                result["gold_corr_signal"] = "GENUINE_RISK_OFF"
            # Gold rising + DXY falling = standard risk-off rotation
            elif result["gold_trend"] == "UP" and result["dxy_trend"] == "DOWN":
                result["gold_corr_signal"] = "RISK_OFF_ROTATION"
            # Gold falling + DXY rising = pure USD strength (bearish crypto)
            elif result["gold_trend"] == "DOWN" and result["dxy_trend"] == "UP":
                result["gold_corr_signal"] = "USD_STRENGTH"
            else:
                result["gold_corr_signal"] = "NEUTRAL"
    except Exception as e:
        logger.warning("Gold fetch failed: %s", e)

    # --- MOVE Index (bond volatility) ---
    try:
        move = macro_conn.fetch_move_index(timeframe="1d", limit=100)
        if not move.empty:
            move_close = move["close"]
            move_val = float(move_close.iloc[-1])
            result["move_z"] = zscore_current(move_close, lookback=50)

            if move_val > 140:
                result["move_regime"] = "CRISIS"
            elif move_val > 120:
                result["move_regime"] = "STRESS"
            elif move_val > 100:
                result["move_regime"] = "ELEVATED"
            else:
                result["move_regime"] = "NORMAL"
    except Exception as e:
        logger.warning("MOVE index fetch failed: %s", e)

    # --- Credit Spread (HYG/IEF) ---
    try:
        credit = macro_conn.fetch_credit_spread(timeframe="1d", limit=100)
        if not credit.empty:
            credit_close = credit["close"]
            result["credit_z"] = zscore_current(credit_close, lookback=50)
            slopes = rolling_regression_slope(credit_close, window=14)
            slope_val = float(slopes.iloc[-1]) if len(slopes) > 0 else 0.0
            result["credit_trend"] = "IMPROVING" if slope_val > 0.0005 else (
                "DETERIORATING" if slope_val < -0.0005 else "FLAT"
            )
    except Exception as e:
        logger.warning("Credit spread fetch failed: %s", e)

    # Classification (enhanced — now includes Gold, MOVE, credit signals)
    # RISK_OFF: USD strengthening + real yields rising
    # RISK_ON: USD weakening + real yields falling
    dxy_bullish = result["dxy_trend"] == "UP" or result["dxy_z"] > 1.0
    dxy_bearish = result["dxy_trend"] == "DOWN" or result["dxy_z"] < -1.0
    ry_rising = result["real_yield_trend"] == "UP" or result["real_yield_z"] > 1.0
    ry_falling = result["real_yield_trend"] == "DOWN" or result["real_yield_z"] < -1.0

    # Enhanced signals
    move_stress = result["move_regime"] in ("STRESS", "CRISIS")
    credit_deteriorating = result["credit_trend"] == "DETERIORATING" or result["credit_z"] < -1.0
    credit_improving = result["credit_trend"] == "IMPROVING" or result["credit_z"] > 1.0
    gold_risk_off = result.get("gold_corr_signal") in ("GENUINE_RISK_OFF", "USD_STRENGTH")

    if dxy_bullish and ry_rising:
        result["state"] = CrossAssetState.CONFIRMS_RISK_OFF
    elif dxy_bearish and ry_falling:
        result["state"] = CrossAssetState.CONFIRMS_RISK_ON
    # New: MOVE stress or credit deterioration can override to RISK_OFF
    elif move_stress and credit_deteriorating:
        result["state"] = CrossAssetState.CONFIRMS_RISK_OFF
    # New: Credit improving with weak DXY = RISK_ON
    elif credit_improving and dxy_bearish:
        result["state"] = CrossAssetState.CONFIRMS_RISK_ON
    # Gold genuine risk-off overrides ambiguity
    elif gold_risk_off and move_stress:
        result["state"] = CrossAssetState.CONFIRMS_RISK_OFF
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


def _classify_regime(cross_asset: dict, sentiment: dict, headline_risk: dict) -> DirectionalRegime:
    """Combine cross-asset, sentiment, and headline risk into directional regime."""
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

    # Headline risk can tip the balance
    if headline_risk.get("headline_risk") == "HIGH":
        risk_off_signals += 1
    elif headline_risk.get("avg_sentiment_score", 0) > 0.3:
        risk_on_signals += 1

    # MOVE stress is a strong risk-off signal
    if cross_asset.get("move_regime") in ("STRESS", "CRISIS"):
        risk_off_signals += 1

    if risk_on_signals >= 2 and risk_off_signals == 0:
        return DirectionalRegime.RISK_ON
    elif risk_off_signals >= 2 and risk_on_signals == 0:
        return DirectionalRegime.RISK_OFF
    else:
        return DirectionalRegime.MIXED


def _compute_composite_score(
    regime: DirectionalRegime, cross_asset: dict, sentiment: dict,
    headline_score: float = 0.0,
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

    # Headline risk adjustment
    score += headline_score

    # MOVE stress penalty — bond vol predicts equity/crypto vol
    if cross_asset.get("move_regime") == "CRISIS":
        score -= 0.5
    elif cross_asset.get("move_regime") == "STRESS":
        score -= 0.25

    # Credit spread adjustment
    if cross_asset.get("credit_z", 0) < -1.5:
        score -= 0.25
    elif cross_asset.get("credit_z", 0) > 1.0:
        score += 0.25

    return clamp(score, -3.0, 3.0)


def _evaluate_headline_risk() -> dict:
    """Evaluate headline/news risk via CryptoPanic.

    Returns a dict with headline counts, sentiment scores, and risk level.
    Gracefully degrades if CryptoPanic is unavailable (returns LOW risk).
    """
    try:
        from dss.connectors.news import CryptoPanicConnector
        news_conn = CryptoPanicConnector()
        result = news_conn.fetch_headline_sentiment(currencies="BTC")
        news_conn.close()
        return result
    except Exception as e:
        logger.warning("CryptoPanic headline risk evaluation failed: %s", e)
        return {
            "headline_count": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "avg_sentiment_score": 0.0,
            "headline_risk": "LOW",
            "top_headlines": [],
            "bearish_dominance": False,
        }
