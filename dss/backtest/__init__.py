"""Backtest engine — replay historical data through the full DSS pipeline.

Models:
  BacktestConfig  — run parameters
  BacktestTrade   — single simulated trade record
  BacktestMetrics — aggregate performance statistics
  BacktestResult  — complete output of a backtest run
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from dss.models.setup import SetupType, Direction


# ---------------------------------------------------------------------------
# Exit reason enumeration
# ---------------------------------------------------------------------------

class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TARGET_1 = "TARGET_1"
    TARGET_2 = "TARGET_2"
    TRAILING_STOP = "TRAILING_STOP"
    BREAKEVEN_STOP = "BREAKEVEN_STOP"
    TIME_EXIT = "TIME_EXIT"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    END_OF_DATA = "END_OF_DATA"


# ---------------------------------------------------------------------------
# Backtest configuration
# ---------------------------------------------------------------------------

class BacktestConfig(BaseModel):
    """Parameters for a single backtest run."""

    asset: str
    timeframe: str = "4h"
    start: datetime
    end: datetime
    order_expiry_bars: int = Field(default=10, ge=1, description="Bars before a pending limit order expires")
    eval_interval: int = Field(default=1, ge=1, description="Run pipeline every N bars (1 = every bar)")
    warmup_bars: int = Field(default=120, ge=20, description="Bars reserved for indicator warm-up")
    initial_equity: float = Field(default=10_000.0, gt=0, description="Starting equity in USD (notional)")
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=10, description="Percent of equity risked per trade")
    partial_close_pct: float = Field(default=50.0, ge=0, le=100, description="Percent of position closed at T1")
    enable_trailing: bool = Field(default=True, description="Enable ATR trailing stop after T1")
    trail_atr_mult: float = Field(default=2.0, gt=0, description="ATR multiplier for trailing stop distance")

    # Realistic cost model
    slippage_bps: float = Field(default=3.0, ge=0, description="Slippage in basis points per side (entry+exit)")
    fee_bps: float = Field(default=4.0, ge=0, description="Exchange fee in basis points per side (taker)")
    funding_rate_daily_bps: float = Field(default=1.0, ge=0, description="Average daily funding cost in bps for perps")

    # Portfolio constraints
    max_open_positions: int = Field(default=3, ge=1, description="Maximum concurrent open positions")

    # Stop management
    min_stop_distance_atr: float = Field(default=1.5, gt=0, description="Minimum stop distance in ATR \u2014 setups with tighter stops are rejected")
    breakeven_r_threshold: float = Field(default=1.0, gt=0, le=3.0, description="Move stop to breakeven when unrealised profit reaches this R-multiple")

    # Risk management
    max_consecutive_losses: int = Field(default=2, ge=1, description="Consecutive losses before cooldown activates")
    cooldown_bars: int = Field(default=12, ge=0, description="Bars to pause trading after max consecutive losses")
    max_bars_in_trade: int = Field(default=60, ge=5, description="Force-close after this many bars (time exit)")
    daily_loss_limit_pct: float = Field(default=3.0, gt=0, description="Halt trading for the day after this cumulative loss %")

    # Score-based position sizing
    score_size_min: float = Field(default=0.5, gt=0, le=1.0, description="Size multiplier for trades at threshold")
    score_size_max: float = Field(default=1.5, ge=1.0, le=3.0, description="Size multiplier for highest-score trades")


# ---------------------------------------------------------------------------
# Single trade record
# ---------------------------------------------------------------------------

class BacktestTrade(BaseModel):
    """Record of one completed (or force-closed) simulated trade."""

    trade_id: int
    asset: str
    direction: Direction
    setup_type: SetupType

    # Entry
    entry_price: float
    entry_time: datetime
    entry_bar_index: int

    # Exit
    exit_price: float
    exit_time: datetime
    exit_bar_index: int
    exit_reason: ExitReason

    # Plan reference
    stop_loss: float
    target_1: float
    target_2: Optional[float] = None

    # Results
    pnl_pct: float = 0.0          # Percentage return on the trade (before costs)
    pnl_net_pct: float = 0.0      # Percentage return AFTER slippage + fees
    pnl_r: float = 0.0            # Return in R-multiples (after costs)
    cost_pct: float = 0.0         # Total round-trip cost (slippage + fees + funding)
    bars_held: int = 0
    max_favourable_pct: float = 0.0   # Best unrealised P&L during the trade
    max_adverse_pct: float = 0.0      # Worst unrealised P&L during the trade

    # Portfolio-level sizing
    position_size_usd: float = 0.0    # Notional size at entry
    portfolio_risk_pct: float = 0.0   # Actual % of equity risked

    # Score snapshot at alert time
    total_score: float = 0.0
    macro_score: float = 0.0
    flow_score: float = 0.0
    structure_score: float = 0.0
    phase_score: float = 0.0
    options_score: float = 0.0
    mamis_score: float = 0.0


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------

class BreakdownRow(BaseModel):
    """Metrics for a single slice (e.g. one asset or one setup type)."""
    label: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    total_pnl_pct: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0


class BacktestMetrics(BaseModel):
    """Aggregate performance statistics from a backtest run."""

    # Counts
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0

    # Rates
    win_rate_pct: float = 0.0

    # Returns
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    largest_win_pct: float = 0.0
    largest_loss_pct: float = 0.0
    total_return_pct: float = 0.0

    # Risk-adjusted
    profit_factor: float = 0.0     # gross_wins / gross_losses
    avg_r_multiple: float = 0.0
    expectancy_r: float = 0.0      # (win_rate * avg_win_r) + (loss_rate * avg_loss_r)
    sharpe_ratio: float = 0.0      # Annualised (per-bar equity returns)
    sortino_ratio: float = 0.0    # Annualised (downside deviation only)
    calmar_ratio: float = 0.0     # Annual return / max drawdown

    # Net of costs
    total_return_net_pct: float = 0.0   # After slippage + fees
    total_costs_pct: float = 0.0         # Sum of all costs
    profit_factor_net: float = 0.0       # After costs

    # Drawdown
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_bars: int = 0

    # Holding
    avg_bars_held: float = 0.0
    avg_bars_winners: float = 0.0
    avg_bars_losers: float = 0.0

    # Signals
    total_alerts: int = 0
    total_rejected: int = 0
    orders_expired: int = 0
    orders_filled: int = 0

    # Breakdowns
    by_asset: list[BreakdownRow] = Field(default_factory=list)
    by_setup_type: list[BreakdownRow] = Field(default_factory=list)
    by_direction: list[BreakdownRow] = Field(default_factory=list)
    monthly_returns: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Full backtest result
# ---------------------------------------------------------------------------

class BacktestResult(BaseModel):
    """Complete output of a backtest run."""

    config: BacktestConfig
    metrics: BacktestMetrics
    trades: list[BacktestTrade] = Field(default_factory=list)
    equity_curve: list[dict] = Field(default_factory=list)  # [{timestamp, equity_usd, equity_pct}]
    run_timestamp: datetime
    duration_seconds: float = 0.0
    bars_processed: int = 0
    final_equity: float = 0.0     # Final portfolio value in USD
    errors: list[str] = Field(default_factory=list)


__all__ = [
    "ExitReason",
    "BacktestConfig",
    "BacktestTrade",
    "BreakdownRow",
    "BacktestMetrics",
    "BacktestResult",
]
