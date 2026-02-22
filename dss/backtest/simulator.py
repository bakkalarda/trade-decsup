"""Position simulator for backtesting.

Handles:
  - Pending limit orders (fill at zone midpoint, expiry)
  - Open position tracking (stop-loss, T1, T2, trailing stop)
  - Partial close at T1 with breakeven stop on remainder
  - P&L calculation with slippage, fees, and funding costs
  - Proper position sizing based on equity and risk budget
  - One position per asset at a time (no pyramiding)
  - Max concurrent positions limit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from dss.backtest import BacktestConfig, BacktestTrade, ExitReason
from dss.models.setup import Direction, SetupType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pending order
# ---------------------------------------------------------------------------

@dataclass
class PendingOrder:
    """A limit order waiting to fill."""

    order_id: int
    asset: str
    direction: Direction
    setup_type: SetupType
    entry_price: float          # Zone midpoint
    stop_loss: float
    target_1: float
    target_2: Optional[float]
    trail_method: str = ""

    # Score snapshot from the alert
    total_score: float = 0.0
    macro_score: float = 0.0
    flow_score: float = 0.0
    structure_score: float = 0.0
    phase_score: float = 0.0
    options_score: float = 0.0
    mamis_score: float = 0.0

    # Lifecycle
    created_bar: int = 0
    created_time: Optional[datetime] = None
    expiry_bars: int = 10

    @property
    def is_expired(self) -> bool:
        return False  # Checked externally via bar count

    def bars_alive(self, current_bar: int) -> int:
        return current_bar - self.created_bar


# ---------------------------------------------------------------------------
# Open position
# ---------------------------------------------------------------------------

@dataclass
class OpenPosition:
    """A filled position being tracked."""

    trade_id: int
    asset: str
    direction: Direction
    setup_type: SetupType
    entry_price: float
    entry_time: datetime
    entry_bar: int
    stop_loss: float
    original_stop: float
    target_1: float
    target_2: Optional[float]

    # Score snapshot
    total_score: float = 0.0
    macro_score: float = 0.0
    flow_score: float = 0.0
    structure_score: float = 0.0
    phase_score: float = 0.0
    options_score: float = 0.0
    mamis_score: float = 0.0

    # Position sizing
    position_size_usd: float = 0.0
    portfolio_risk_pct: float = 0.0

    # State
    t1_hit: bool = False
    partial_closed: bool = False
    trail_active: bool = False
    trail_atr_mult: float = 2.0
    breakeven_activated: bool = False

    # Tracking
    max_favourable_price: float = 0.0
    max_adverse_price: float = 0.0

    def __post_init__(self):
        self.original_stop = self.stop_loss
        self.max_favourable_price = self.entry_price
        self.max_adverse_price = self.entry_price


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class PositionSimulator:
    """Manages pending orders and open positions during a backtest.

    Tracks portfolio equity with compounding, applies slippage + fees,
    and enforces position limits.
    """

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.pending_orders: list[PendingOrder] = []
        self.open_positions: dict[str, OpenPosition] = {}  # asset -> position
        self.closed_trades: list[BacktestTrade] = []
        self._next_order_id: int = 0
        self._next_trade_id: int = 0

        # Portfolio equity tracking (with compounding)
        self._equity: float = config.initial_equity
        self._peak_equity: float = config.initial_equity

        # Consecutive loss tracking / cooldown
        self._consecutive_losses: int = 0
        self._cooldown_until_bar: int = -1

        # Daily P&L circuit breaker
        self._daily_pnl: dict[str, float] = {}  # date_str -> cumulative pnl_pct
        self._halted_dates: set[str] = set()

        # Cost model (convert bps to fraction)
        self._slippage_frac: float = config.slippage_bps / 10_000
        self._fee_frac: float = config.fee_bps / 10_000
        self._funding_daily_frac: float = config.funding_rate_daily_bps / 10_000

    @property
    def equity(self) -> float:
        """Current portfolio equity in USD."""
        return self._equity

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def submit_order(
        self,
        asset: str,
        direction: Direction,
        setup_type: SetupType,
        entry_price: float,
        stop_loss: float,
        target_1: float,
        target_2: Optional[float],
        trail_method: str,
        current_bar: int,
        current_time: datetime,
        total_score: float = 0.0,
        macro_score: float = 0.0,
        flow_score: float = 0.0,
        structure_score: float = 0.0,
        phase_score: float = 0.0,
        options_score: float = 0.0,
        mamis_score: float = 0.0,
    ) -> Optional[PendingOrder]:
        """Submit a new pending limit order.

        Returns None if the asset already has an open position or pending order,
        or if max concurrent positions would be exceeded.
        """
        # No pyramiding
        if asset in self.open_positions:
            logger.debug("Skip order: %s already has open position", asset)
            return None

        # No duplicate pending orders for the same asset
        if any(o.asset == asset for o in self.pending_orders):
            logger.debug("Skip order: %s already has pending order", asset)
            return None

        # Max concurrent position limit
        total_exposure = len(self.open_positions) + len(self.pending_orders)
        if total_exposure >= self.config.max_open_positions:
            logger.debug("Skip order: max_open_positions (%d) reached", self.config.max_open_positions)
            return None

        # Cooldown after consecutive losses
        if self._cooldown_until_bar >= current_bar:
            logger.debug(
                "Skip order: in cooldown until bar %d (current: %d, consecutive losses: %d)",
                self._cooldown_until_bar, current_bar, self._consecutive_losses,
            )
            return None

        # Daily P&L circuit breaker
        day_key = current_time.strftime("%Y-%m-%d") if current_time else ""
        if day_key in self._halted_dates:
            logger.debug("Skip order: daily circuit breaker active for %s", day_key)
            return None

        self._next_order_id += 1
        order = PendingOrder(
            order_id=self._next_order_id,
            asset=asset,
            direction=direction,
            setup_type=setup_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            trail_method=trail_method,
            total_score=total_score,
            macro_score=macro_score,
            flow_score=flow_score,
            structure_score=structure_score,
            phase_score=phase_score,
            options_score=options_score,
            mamis_score=mamis_score,
            created_bar=current_bar,
            created_time=current_time,
            expiry_bars=self.config.order_expiry_bars,
        )
        self.pending_orders.append(order)
        return order

    # ------------------------------------------------------------------
    # Bar-by-bar processing
    # ------------------------------------------------------------------

    def process_bar(
        self,
        bar_idx: int,
        bar_time: datetime,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        current_atr: float,
    ) -> list[BacktestTrade]:
        """Process one bar: check fills, stops, targets.

        Returns list of trades closed on this bar.
        """
        closed_this_bar: list[BacktestTrade] = []

        # 1. Check pending order fills
        self._check_fills(bar_idx, bar_time, bar_open, bar_high, bar_low, bar_close)

        # 2. Expire old orders
        self._expire_orders(bar_idx)

        # 3. Check open positions for stops and targets
        assets_to_close: list[tuple[str, float, ExitReason]] = []

        for asset, pos in self.open_positions.items():
            # Update tracking extremes
            if pos.direction == Direction.LONG:
                if bar_high > pos.max_favourable_price:
                    pos.max_favourable_price = bar_high
                if bar_low < pos.max_adverse_price:
                    pos.max_adverse_price = bar_low
            else:
                if bar_low < pos.max_favourable_price:
                    pos.max_favourable_price = bar_low
                if bar_high > pos.max_adverse_price:
                    pos.max_adverse_price = bar_high

            # Update trailing stop
            if pos.trail_active and self.config.enable_trailing and current_atr > 0:
                trail_dist = current_atr * self.config.trail_atr_mult
                if pos.direction == Direction.LONG:
                    new_stop = bar_high - trail_dist
                    if new_stop > pos.stop_loss:
                        pos.stop_loss = new_stop
                else:
                    new_stop = bar_low + trail_dist
                    if new_stop < pos.stop_loss:
                        pos.stop_loss = new_stop

            # --- MFE breakeven stop: move to BE when unrealised hits threshold R ---
            if not pos.breakeven_activated and not pos.t1_hit:
                risk_per_unit = abs(pos.entry_price - pos.original_stop)
                if risk_per_unit > 0:
                    if pos.direction == Direction.LONG:
                        current_r = (bar_high - pos.entry_price) / risk_per_unit
                    else:
                        current_r = (pos.entry_price - bar_low) / risk_per_unit
                    if current_r >= self.config.breakeven_r_threshold:
                        pos.stop_loss = pos.entry_price
                        pos.breakeven_activated = True
                        logger.debug(
                            "BREAKEVEN: %s %s moved stop to BE at %.2f (MFE %.2fR)",
                            pos.direction.value, pos.asset, pos.entry_price, current_r,
                        )

            # --- Check stop-loss FIRST (conservative: assume worst case) ---
            stopped = False
            if pos.direction == Direction.LONG and bar_low <= pos.stop_loss:
                exit_price = pos.stop_loss
                if pos.trail_active:
                    reason = ExitReason.TRAILING_STOP
                elif pos.breakeven_activated:
                    reason = ExitReason.BREAKEVEN_STOP
                else:
                    reason = ExitReason.STOP_LOSS
                assets_to_close.append((asset, exit_price, reason))
                stopped = True
            elif pos.direction == Direction.SHORT and bar_high >= pos.stop_loss:
                exit_price = pos.stop_loss
                if pos.trail_active:
                    reason = ExitReason.TRAILING_STOP
                elif pos.breakeven_activated:
                    reason = ExitReason.BREAKEVEN_STOP
                else:
                    reason = ExitReason.STOP_LOSS
                assets_to_close.append((asset, exit_price, reason))
                stopped = True

            if stopped:
                continue

            # --- Check T2 (full exit) ---
            if pos.target_2 is not None:
                if pos.direction == Direction.LONG and bar_high >= pos.target_2:
                    assets_to_close.append((asset, pos.target_2, ExitReason.TARGET_2))
                    continue
                elif pos.direction == Direction.SHORT and bar_low <= pos.target_2:
                    assets_to_close.append((asset, pos.target_2, ExitReason.TARGET_2))
                    continue

            # --- Check T1 (partial close + trail activation) ---
            if not pos.t1_hit:
                t1_hit = False
                if pos.direction == Direction.LONG and bar_high >= pos.target_1:
                    t1_hit = True
                elif pos.direction == Direction.SHORT and bar_low <= pos.target_1:
                    t1_hit = True

                if t1_hit:
                    pos.t1_hit = True
                    pos.partial_closed = True
                    # Move stop to breakeven (entry price)
                    pos.stop_loss = pos.entry_price
                    # Activate trailing stop
                    if self.config.enable_trailing:
                        pos.trail_active = True

                    # If no T2, close fully at T1
                    if pos.target_2 is None:
                        assets_to_close.append((asset, pos.target_1, ExitReason.TARGET_1))

            # --- Check max time-in-trade (stale trade exit) ---
            bars_in_trade = bar_idx - pos.entry_bar
            if bars_in_trade >= self.config.max_bars_in_trade:
                assets_to_close.append((asset, bar_close, ExitReason.TIME_EXIT))

        # Close positions
        for asset, exit_price, reason in assets_to_close:
            trade = self._close_position(asset, exit_price, bar_idx, bar_time, reason)
            if trade:
                closed_this_bar.append(trade)

        return closed_this_bar

    def force_close_all(
        self, bar_idx: int, bar_time: datetime, bar_close: float,
    ) -> list[BacktestTrade]:
        """Force-close all open positions at end of data."""
        closed: list[BacktestTrade] = []
        for asset in list(self.open_positions.keys()):
            trade = self._close_position(
                asset, bar_close, bar_idx, bar_time, ExitReason.END_OF_DATA
            )
            if trade:
                closed.append(trade)
        # Expire all pending
        self.pending_orders.clear()
        return closed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_fills(
        self,
        bar_idx: int,
        bar_time: datetime,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
    ):
        """Check if any pending orders fill on this bar."""
        filled_ids: list[int] = []

        for order in self.pending_orders:
            # Skip if asset already has a position
            if order.asset in self.open_positions:
                continue

            filled = False
            if order.direction == Direction.LONG and bar_low <= order.entry_price:
                filled = True
            elif order.direction == Direction.SHORT and bar_high >= order.entry_price:
                filled = True

            if filled:
                self._fill_order(order, bar_idx, bar_time)
                filled_ids.append(order.order_id)

        self.pending_orders = [o for o in self.pending_orders if o.order_id not in filled_ids]

    def _fill_order(self, order: PendingOrder, bar_idx: int, bar_time: datetime):
        """Convert a filled pending order into an open position.

        Applies entry slippage and computes proper position size from
        current equity and risk budget.
        """
        # Apply entry slippage: worse fill for both directions
        if order.direction == Direction.LONG:
            fill_price = order.entry_price * (1 + self._slippage_frac)
        else:
            fill_price = order.entry_price * (1 - self._slippage_frac)

        # Compute position size: risk_per_trade_pct of equity / risk_per_unit
        risk_per_unit = abs(fill_price - order.stop_loss)
        if risk_per_unit <= 0:
            risk_per_unit = fill_price * 0.02  # Fallback: 2% risk

        risk_budget = self._equity * (self.config.risk_per_trade_pct / 100.0)

        # Score-based size scaling: high-conviction trades get larger size
        # Linear interpolation: at threshold → score_size_min, at threshold+8 → score_size_max
        score_mult = 1.0
        base_threshold = 6.0  # Default threshold
        score_excess = max(0.0, abs(order.total_score) - base_threshold)
        score_mult = self.config.score_size_min + (
            score_excess / 8.0
        ) * (self.config.score_size_max - self.config.score_size_min)
        score_mult = max(self.config.score_size_min, min(self.config.score_size_max, score_mult))
        risk_budget *= score_mult

        units = risk_budget / risk_per_unit
        position_size_usd = units * fill_price
        portfolio_risk_pct = (risk_budget / self._equity) * 100 if self._equity > 0 else 0

        self._next_trade_id += 1
        pos = OpenPosition(
            trade_id=self._next_trade_id,
            asset=order.asset,
            direction=order.direction,
            setup_type=order.setup_type,
            entry_price=fill_price,
            entry_time=bar_time,
            entry_bar=bar_idx,
            stop_loss=order.stop_loss,
            original_stop=order.stop_loss,
            target_1=order.target_1,
            target_2=order.target_2,
            total_score=order.total_score,
            macro_score=order.macro_score,
            flow_score=order.flow_score,
            structure_score=order.structure_score,
            phase_score=order.phase_score,
            options_score=order.options_score,
            mamis_score=order.mamis_score,
            trail_atr_mult=self.config.trail_atr_mult,
            position_size_usd=position_size_usd,
            portfolio_risk_pct=portfolio_risk_pct,
        )
        self.open_positions[order.asset] = pos
        logger.debug(
            "FILL: %s %s %s @ %.2f (slipped from %.2f) size=$%.0f risk=%.1f%% (bar %d)",
            order.direction.value, order.asset, order.setup_type.value,
            fill_price, order.entry_price, position_size_usd, portfolio_risk_pct, bar_idx,
        )

    def _expire_orders(self, current_bar: int):
        """Remove orders that have exceeded their expiry."""
        expired = [
            o for o in self.pending_orders
            if o.bars_alive(current_bar) >= o.expiry_bars
        ]
        for o in expired:
            logger.debug("EXPIRED: order %d for %s (bar %d)", o.order_id, o.asset, current_bar)
        self.pending_orders = [
            o for o in self.pending_orders
            if o.bars_alive(current_bar) < o.expiry_bars
        ]

    def _close_position(
        self,
        asset: str,
        exit_price: float,
        bar_idx: int,
        bar_time: datetime,
        reason: ExitReason,
    ) -> Optional[BacktestTrade]:
        """Close a position and record the trade.

        Applies exit slippage, computes round-trip costs (entry + exit
        slippage, entry + exit fees, funding over hold period), then
        updates portfolio equity with the net result.
        """
        pos = self.open_positions.pop(asset, None)
        if pos is None:
            return None

        # Apply exit slippage (worse fill)
        if pos.direction == Direction.LONG:
            slipped_exit = exit_price * (1 - self._slippage_frac)
        else:
            slipped_exit = exit_price * (1 + self._slippage_frac)

        # --- Gross P&L (with slippage already baked into entry and exit)
        if pos.direction == Direction.LONG:
            pnl_pct = ((slipped_exit - pos.entry_price) / pos.entry_price) * 100
            max_fav_pct = ((pos.max_favourable_price - pos.entry_price) / pos.entry_price) * 100
            max_adv_pct = ((pos.max_adverse_price - pos.entry_price) / pos.entry_price) * 100
        else:
            pnl_pct = ((pos.entry_price - slipped_exit) / pos.entry_price) * 100
            max_fav_pct = ((pos.entry_price - pos.max_favourable_price) / pos.entry_price) * 100
            max_adv_pct = ((pos.entry_price - pos.max_adverse_price) / pos.entry_price) * 100

        # Factor in partial close at T1
        if pos.partial_closed and reason != ExitReason.TARGET_1:
            partial_pct = self.config.partial_close_pct / 100.0
            if pos.direction == Direction.LONG:
                t1_pnl = ((pos.target_1 - pos.entry_price) / pos.entry_price) * 100
            else:
                t1_pnl = ((pos.entry_price - pos.target_1) / pos.entry_price) * 100
            pnl_pct = (partial_pct * t1_pnl) + ((1 - partial_pct) * pnl_pct)

        # --- Costs (entry fee + exit fee + funding)
        # Fee model: round-trip fees (entry + exit)
        fee_cost_pct = 2 * self._fee_frac * 100  # as percentage of notional

        # Funding cost: daily rate * holding period in days
        bars_held = bar_idx - pos.entry_bar
        bar_hours = 4  # 4h bars
        days_held = (bars_held * bar_hours) / 24.0
        funding_cost_pct = self._funding_daily_frac * days_held * 100

        cost_pct = round(fee_cost_pct + funding_cost_pct, 4)
        pnl_net_pct = round(pnl_pct - cost_pct, 4)

        # --- R-multiple (gross, before costs)
        risk_per_unit = abs(pos.entry_price - pos.original_stop)
        if risk_per_unit > 0:
            if pos.direction == Direction.LONG:
                pnl_r = (slipped_exit - pos.entry_price) / risk_per_unit
            else:
                pnl_r = (pos.entry_price - slipped_exit) / risk_per_unit
            if pos.partial_closed and reason != ExitReason.TARGET_1:
                partial_pct_v = self.config.partial_close_pct / 100.0
                if pos.direction == Direction.LONG:
                    t1_r = (pos.target_1 - pos.entry_price) / risk_per_unit
                else:
                    t1_r = (pos.entry_price - pos.target_1) / risk_per_unit
                pnl_r = (partial_pct_v * t1_r) + ((1 - partial_pct_v) * pnl_r)
        else:
            pnl_r = 0.0

        # --- Update portfolio equity (compound)
        # Position weight = position_size / equity_at_entry
        if self._equity > 0 and pos.position_size_usd > 0:
            weight = pos.position_size_usd / self._equity
            portfolio_pnl_pct = weight * pnl_net_pct
            self._equity *= (1 + portfolio_pnl_pct / 100.0)
            if self._equity > self._peak_equity:
                self._peak_equity = self._equity

        # --- Consecutive loss tracking + cooldown ---
        if pnl_net_pct < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.config.max_consecutive_losses:
                self._cooldown_until_bar = bar_idx + self.config.cooldown_bars
                logger.debug(
                    "COOLDOWN: %d consecutive losses, pausing until bar %d",
                    self._consecutive_losses, self._cooldown_until_bar,
                )
        else:
            self._consecutive_losses = 0

        # --- Daily P&L circuit breaker ---
        day_key = bar_time.strftime("%Y-%m-%d")
        daily_pnl = self._daily_pnl.get(day_key, 0.0) + pnl_net_pct
        self._daily_pnl[day_key] = daily_pnl
        if daily_pnl <= -self.config.daily_loss_limit_pct and day_key not in self._halted_dates:
            self._halted_dates.add(day_key)
            logger.debug(
                "CIRCUIT_BREAKER: daily loss %.2f%% exceeds limit %.1f%% on %s",
                daily_pnl, self.config.daily_loss_limit_pct, day_key,
            )

        trade = BacktestTrade(
            trade_id=pos.trade_id,
            asset=pos.asset,
            direction=pos.direction,
            setup_type=pos.setup_type,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            entry_bar_index=pos.entry_bar,
            exit_price=round(slipped_exit, 2),
            exit_time=bar_time,
            exit_bar_index=bar_idx,
            exit_reason=reason,
            stop_loss=pos.original_stop,
            target_1=pos.target_1,
            target_2=pos.target_2,
            pnl_pct=round(pnl_pct, 4),
            pnl_net_pct=pnl_net_pct,
            cost_pct=cost_pct,
            pnl_r=round(pnl_r, 4),
            bars_held=bars_held,
            max_favourable_pct=round(max_fav_pct, 4),
            max_adverse_pct=round(max_adv_pct, 4),
            position_size_usd=round(pos.position_size_usd, 2),
            portfolio_risk_pct=round(pos.portfolio_risk_pct, 2),
            total_score=pos.total_score,
            macro_score=pos.macro_score,
            flow_score=pos.flow_score,
            structure_score=pos.structure_score,
            phase_score=pos.phase_score,
            options_score=pos.options_score,
            mamis_score=pos.mamis_score,
        )
        self.closed_trades.append(trade)

        logger.debug(
            "CLOSE: %s %s @ %.2f→%.2f (%s) gross=%.2f%% net=%.2f%% cost=%.2f%% R=%.2fR bars=%d eq=$%.0f",
            pos.direction.value, pos.asset, exit_price, slipped_exit,
            reason.value, pnl_pct, pnl_net_pct, cost_pct, pnl_r, bars_held, self._equity,
        )

        return trade

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    @property
    def orders_filled(self) -> int:
        return self._next_trade_id

    @property
    def orders_expired_count(self) -> int:
        return self._next_order_id - self._next_trade_id - len(self.pending_orders)
