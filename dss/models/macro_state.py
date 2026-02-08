"""Macro state model -- matches macro_state.schema.json."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DirectionalRegime(str, Enum):
    RISK_ON = "RISK_ON"
    MIXED = "MIXED"
    RISK_OFF = "RISK_OFF"


class Tradeability(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    NO_TRADE = "NO_TRADE"


class RiskLevel(str, Enum):
    LOW = "LOW"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"


class EventWindow(str, Enum):
    NONE = "NONE"
    PRE = "PRE"
    POST = "POST"


class SentimentState(str, Enum):
    RISK_SEEKING = "RISK_SEEKING"
    NEUTRAL = "NEUTRAL"
    RISK_AVERSE = "RISK_AVERSE"


class SentimentExtreme(str, Enum):
    FEAR_EXTREME = "FEAR_EXTREME"
    GREED_EXTREME = "GREED_EXTREME"
    NONE = "NONE"


class CrossAssetState(str, Enum):
    CONFIRMS_RISK_ON = "CONFIRMS_RISK_ON"
    CONFIRMS_RISK_OFF = "CONFIRMS_RISK_OFF"
    MIXED = "MIXED"


class DirectionalBias(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    AMBIGUOUS = "AMBIGUOUS"


class ActiveEvent(BaseModel):
    name: str
    tier: int = Field(ge=1, le=3)
    time_to_event_hours: float


class HeadlineFlag(BaseModel):
    topic: str
    severity: int = Field(ge=0, le=5)
    credibility: int = Field(ge=0, le=5)
    directional_bias: DirectionalBias
    asset_impact: Optional[str] = None
    horizon: Optional[str] = None


class MacroState(BaseModel):
    """Full macro gate output — matches the JSON schema."""

    timestamp_utc: datetime
    directional_regime: DirectionalRegime
    tradeability: Tradeability
    size_multiplier: float = Field(ge=0.0, le=1.0)

    event_risk_state: RiskLevel
    event_window: EventWindow = EventWindow.NONE
    event_veto: bool = False

    headline_risk_state: RiskLevel
    headline_risk_score: float = 0.0

    sentiment_state: SentimentState
    sentiment_extreme: SentimentExtreme = SentimentExtreme.NONE

    cross_asset_state: CrossAssetState
    crypto_catalyst_state: RiskLevel = RiskLevel.LOW

    active_events: list[ActiveEvent] = Field(default_factory=list)
    headline_flags: list[HeadlineFlag] = Field(default_factory=list)

    notes: Optional[str] = None

    # Computed composite macro score for scoring engine
    macro_score: float = 0.0
