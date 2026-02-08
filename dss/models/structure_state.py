"""Technical structure & phase gate state model."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TrendDirection(str, Enum):
    UP = "UP"           # HH/HL
    DOWN = "DOWN"       # LH/LL
    SIDEWAYS = "SIDEWAYS"


class Stage(int, Enum):
    BASE = 1            # Range, compression
    MARKUP = 2          # Uptrend
    DISTRIBUTION = 3    # Topping, failed breakouts
    MARKDOWN = 4        # Downtrend


class WyckoffEvent(str, Enum):
    SPRING = "SPRING"
    UTAD = "UTAD"
    LPS = "LPS"
    LPSY = "LPSY"
    NONE = "NONE"


class PhaseAlignment(str, Enum):
    ALIGNED = "ALIGNED"
    MISALIGNED = "MISALIGNED"


class VolatilityRegime(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class SRZone(BaseModel):
    """Support/Resistance zone."""
    low: float
    high: float
    strength: float = 0.0
    touches: int = 0
    timeframe: str = ""
    is_support: bool = True
    last_touch_bar: int = 0


class Trendline(BaseModel):
    """Fitted trendline."""
    slope: float
    intercept: float
    touch_count: int = 0
    residual_error: float = 0.0
    is_ascending: bool = True
    start_bar: int = 0
    end_bar: int = 0


class SwingPivot(BaseModel):
    """A detected swing high or low."""
    bar_index: int
    price: float
    is_high: bool
    timestamp: Optional[datetime] = None


class StructureState(BaseModel):
    """Technical structure gate output for a single asset + timeframe."""

    timestamp_utc: datetime
    asset: str
    timeframe: str

    # Trend
    trend_direction: TrendDirection = TrendDirection.SIDEWAYS
    trend_confidence: float = 0.0

    # Stage
    stage: Stage = Stage.BASE
    stage_confidence: float = 0.0

    # Wyckoff
    wyckoff_event: WyckoffEvent = WyckoffEvent.NONE
    phase_alignment: PhaseAlignment = PhaseAlignment.ALIGNED

    # Volatility
    atr: float = 0.0
    atr_pct: float = 0.0
    volatility_regime: VolatilityRegime = VolatilityRegime.NORMAL
    atr_percentile: float = 50.0

    # Zones
    sr_zones: list[SRZone] = Field(default_factory=list)
    nearest_support: Optional[SRZone] = None
    nearest_resistance: Optional[SRZone] = None

    # Trendlines
    trendlines: list[Trendline] = Field(default_factory=list)

    # Pivots
    recent_pivots: list[SwingPivot] = Field(default_factory=list)

    # Current price context
    current_price: float = 0.0
    distance_to_support_atr: Optional[float] = None
    distance_to_resistance_atr: Optional[float] = None
    in_zone: bool = False
    zone_type: Optional[str] = None  # "support" / "resistance" / None

    # Acceptance / Rejection
    last_acceptance: Optional[str] = None  # description
    last_rejection: Optional[str] = None

    # Vetoes
    structure_veto: bool = False
    structure_veto_reasons: list[str] = Field(default_factory=list)

    # Composite scores
    structure_score: float = 0.0
    phase_score: float = 0.0
