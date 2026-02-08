"""Alert and rejected-candidate models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from dss.models.setup import Setup, Direction, SetupType, TradePlan


class Alert(BaseModel):
    """Actionable alert sent to the trader."""

    timestamp_utc: datetime
    asset: str
    direction: Direction
    setup_type: SetupType
    trade_plan: TradePlan

    total_score: float
    confidence_reasons: list[str] = Field(default_factory=list)

    size_multiplier: float = 1.0
    timeframe: str = "4h"
    notes: Optional[str] = None


class RejectedCandidate(BaseModel):
    """Near-miss setup that didn't pass — logged for diagnostics."""

    timestamp_utc: datetime
    asset: str
    setup_type: SetupType
    direction: Direction
    total_score: float

    # What killed it
    failed_gate: Optional[str] = None
    veto_reasons: list[str] = Field(default_factory=list)
    score_gap: float = 0.0  # How far from threshold

    notes: Optional[str] = None
