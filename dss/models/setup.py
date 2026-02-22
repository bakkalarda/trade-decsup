"""Trade setup models — Setup A/B/C and trade plan."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SetupType(str, Enum):
    PULLBACK = "A_PULLBACK"
    BREAKOUT_RETEST = "B_BREAKOUT_RETEST"
    WYCKOFF_TRAP = "C_WYCKOFF_TRAP"
    MAMIS_TRANSITION = "D_MAMIS_TRANSITION"  # Legacy — no longer generated standalone
    RANGE_TRADE = "E_RANGE_TRADE"
    DIVERGENCE_REVERSAL = "F_DIVERGENCE_REVERSAL"
    VOLUME_CLIMAX = "G_VOLUME_CLIMAX"
    FIB_OTE = "H_FIB_OTE"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradePlan(BaseModel):
    """Actionable trade plan emitted for a valid setup."""
    direction: Direction
    entry_zone_low: float
    entry_zone_high: float
    trigger_description: str
    stop_loss: float
    target_1: float
    target_2: Optional[float] = None
    trail_method: str = ""
    risk_reward_t1: float = 0.0
    risk_reward_t2: Optional[float] = None


class Setup(BaseModel):
    """A detected setup candidate (before scoring/veto)."""

    timestamp_utc: datetime
    asset: str
    timeframe: str
    setup_type: SetupType
    direction: Direction

    # Location
    zone_description: str = ""
    zone_low: float = 0.0
    zone_high: float = 0.0

    # Quality assessment
    setup_quality_score: float = Field(default=0.0, ge=0.0, le=3.0)
    quality_reasons: list[str] = Field(default_factory=list)

    # Trade plan
    trade_plan: Optional[TradePlan] = None

    # Scoring context (filled by scoring engine)
    macro_score: float = 0.0
    flow_score: float = 0.0
    structure_score: float = 0.0
    phase_score: float = 0.0
    options_score: float = 0.0
    mamis_score: float = 0.0
    total_score: float = 0.0

    # Vetoes
    vetoed: bool = False
    veto_reasons: list[str] = Field(default_factory=list)

    # Final decision
    is_alert: bool = False
