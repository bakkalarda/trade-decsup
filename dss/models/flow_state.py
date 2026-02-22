"""Flow & positioning gate state model."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FlowBias(str, Enum):
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"


class ProfitTakingState(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    EXTREME = "EXTREME"


class CrowdingState(str, Enum):
    CLEAN = "CLEAN"
    WARM = "WARM"
    HOT = "HOT"
    EXTREME = "EXTREME"


class SqueezeRisk(str, Enum):
    LOW = "LOW"
    MED = "MED"
    HIGH = "HIGH"


class LiquidityState(str, Enum):
    GOOD = "GOOD"
    THIN = "THIN"
    DISLOCATED = "DISLOCATED"


class FlowState(BaseModel):
    """Flow & positioning gate output for a single asset."""

    timestamp_utc: datetime
    asset: str

    # Directional confirmation
    flow_bias: FlowBias = FlowBias.NEUTRAL

    # On-chain (BTC/ETH Tier A)
    exch_inflow_z: Optional[float] = None
    exch_netflow_z: Optional[float] = None
    reserve_slope: Optional[float] = None

    # Advanced on-chain analytics (article framework)
    nvt_signal: Optional[float] = None
    nvt_zone: Optional[str] = None
    hash_ribbon_state: Optional[str] = None
    miner_stress: Optional[bool] = None
    ssr: Optional[float] = None
    ssr_zone: Optional[str] = None

    # Profit-taking / distribution
    profit_taking_state: ProfitTakingState = ProfitTakingState.NORMAL
    distribution_flag: bool = False

    # Crowding & squeeze risk
    funding_z: Optional[float] = None
    oi_z: Optional[float] = None
    oi_impulse: Optional[float] = None
    crowding_state: CrowdingState = CrowdingState.CLEAN
    squeeze_risk: SqueezeRisk = SqueezeRisk.LOW

    # Liquidity
    liquidity_state: LiquidityState = LiquidityState.GOOD

    # Vetoes
    long_veto: bool = False
    short_veto: bool = False
    long_veto_reasons: list[str] = Field(default_factory=list)
    short_veto_reasons: list[str] = Field(default_factory=list)

    # Sizing
    positioning_size_multiplier: float = 1.0

    # Composite flow score for scoring engine
    flow_score: float = 0.0
