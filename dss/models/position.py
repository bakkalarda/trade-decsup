"""Position tracking models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from dss.models.setup import Direction, SetupType


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"
    TARGET_HIT = "TARGET_HIT"


class Position(BaseModel):
    """Tracked position with stops and targets."""

    id: Optional[int] = None
    asset: str
    direction: Direction
    setup_type: Optional[SetupType] = None

    entry_price: float
    current_price: float = 0.0
    stop_loss: float
    original_stop: float = 0.0
    target_1: float
    target_2: Optional[float] = None

    size_multiplier: float = 1.0
    status: PositionStatus = PositionStatus.OPEN

    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    close_reason: Optional[str] = None

    # P&L tracking
    unrealized_pnl_pct: float = 0.0
    realized_pnl_pct: Optional[float] = None

    # Trail state
    trail_active: bool = False
    trail_method: str = ""

    notes: Optional[str] = None
