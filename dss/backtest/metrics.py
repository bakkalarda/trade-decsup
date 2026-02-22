"""Performance metrics computation for backtests.

Takes a list of closed ``BacktestTrade`` objects and an equity curve and
returns a fully populated ``BacktestMetrics``.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Optional

from dss.backtest import (
    BacktestConfig,
    BacktestMetrics,
    BacktestTrade,
    BreakdownRow,
)


def compute_metrics(
    trades: list[BacktestTrade],
    equity_curve: list[dict],
    bt_cfg: BacktestConfig,
) -> BacktestMetrics:
    """Compute aggregate performance metrics from closed trades."""

    m = BacktestMetrics()

    if not trades:
        return m

    # --- Basic counts ---
    m.total_trades = len(trades)
    wins = [t for t in trades if t.pnl_net_pct > 0]
    losses = [t for t in trades if t.pnl_net_pct < 0]
    breakeven = [t for t in trades if t.pnl_net_pct == 0]

    m.wins = len(wins)
    m.losses = len(losses)
    m.breakeven = len(breakeven)

    # --- Win rate ---
    m.win_rate_pct = round((m.wins / m.total_trades) * 100, 2) if m.total_trades > 0 else 0.0

    # --- Return stats (use NET returns — after costs) ---
    win_pcts = [t.pnl_net_pct for t in wins]
    loss_pcts = [t.pnl_net_pct for t in losses]

    m.avg_win_pct = round(_safe_mean(win_pcts), 4) if win_pcts else 0.0
    m.avg_loss_pct = round(_safe_mean(loss_pcts), 4) if loss_pcts else 0.0
    m.largest_win_pct = round(max(win_pcts), 4) if win_pcts else 0.0
    m.largest_loss_pct = round(min(loss_pcts), 4) if loss_pcts else 0.0

    all_net_pnls = [t.pnl_net_pct for t in trades]
    all_gross_pnls = [t.pnl_pct for t in trades]
    m.total_return_pct = round(sum(all_gross_pnls), 4)
    m.total_return_net_pct = round(sum(all_net_pnls), 4)
    m.total_costs_pct = round(sum(t.cost_pct for t in trades), 4)

    # --- Profit factor (gross and net) ---
    gross_wins = sum(p for p in all_net_pnls if p > 0)
    gross_losses = abs(sum(p for p in all_net_pnls if p < 0))
    m.profit_factor_net = round(gross_wins / gross_losses, 4) if gross_losses > 0 else (
        float("inf") if gross_wins > 0 else 0.0
    )
    # Gross profit factor (before costs)
    gw_raw = sum(p for p in all_gross_pnls if p > 0)
    gl_raw = abs(sum(p for p in all_gross_pnls if p < 0))
    m.profit_factor = round(gw_raw / gl_raw, 4) if gl_raw > 0 else (
        float("inf") if gw_raw > 0 else 0.0
    )

    # --- R-multiples (net of costs) ---
    all_r = [t.pnl_r for t in trades]
    m.avg_r_multiple = round(_safe_mean(all_r), 4)
    win_r = [t.pnl_r for t in wins]
    loss_r = [t.pnl_r for t in losses]
    avg_win_r = _safe_mean(win_r) if win_r else 0.0
    avg_loss_r = _safe_mean(loss_r) if loss_r else 0.0
    win_rate = m.wins / m.total_trades if m.total_trades > 0 else 0.0
    loss_rate = 1.0 - win_rate
    m.expectancy_r = round((win_rate * avg_win_r) + (loss_rate * avg_loss_r), 4)

    # --- Sharpe, Sortino, Calmar (from per-bar equity returns) ---
    m.sharpe_ratio = _compute_sharpe_from_equity(equity_curve, bt_cfg.timeframe)
    m.sortino_ratio = _compute_sortino_from_equity(equity_curve, bt_cfg.timeframe)

    # --- Drawdown ---
    dd_pct, dd_duration = _compute_max_drawdown(equity_curve)
    m.max_drawdown_pct = round(dd_pct, 4)
    m.max_drawdown_duration_bars = dd_duration

    # Calmar = annualised return / max drawdown
    if m.max_drawdown_pct > 0 and equity_curve:
        n_bars = len(equity_curve)
        bars_per_year = {"1w": 52, "1d": 252, "4h": 252 * 6, "1h": 252 * 24}
        bpy = bars_per_year.get(bt_cfg.timeframe, 252 * 6)
        years = n_bars / bpy if bpy > 0 else 1.0
        annual_return = m.total_return_net_pct / years if years > 0 else 0.0
        m.calmar_ratio = round(annual_return / m.max_drawdown_pct, 4)

    # --- Holding periods ---
    all_bars = [t.bars_held for t in trades]
    m.avg_bars_held = round(_safe_mean(all_bars), 1) if all_bars else 0.0
    win_bars = [t.bars_held for t in wins]
    loss_bars = [t.bars_held for t in losses]
    m.avg_bars_winners = round(_safe_mean(win_bars), 1) if win_bars else 0.0
    m.avg_bars_losers = round(_safe_mean(loss_bars), 1) if loss_bars else 0.0

    # --- Breakdowns ---
    m.by_asset = _breakdown(trades, key=lambda t: t.asset)
    m.by_setup_type = _breakdown(trades, key=lambda t: t.setup_type.value)
    m.by_direction = _breakdown(trades, key=lambda t: t.direction.value)

    # --- Monthly returns ---
    m.monthly_returns = _monthly_returns(trades)

    return m


# ---------------------------------------------------------------------------
# Breakdown helpers
# ---------------------------------------------------------------------------

def _breakdown(trades: list[BacktestTrade], key) -> list[BreakdownRow]:
    """Group trades by a key function and compute per-group metrics."""
    groups: dict[str, list[BacktestTrade]] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)

    rows = []
    for label, group_trades in sorted(groups.items()):
        wins = [t for t in group_trades if t.pnl_pct > 0]
        losses = [t for t in group_trades if t.pnl_pct < 0]
        total = len(group_trades)

        win_pcts = [t.pnl_pct for t in wins]
        loss_pcts = [t.pnl_pct for t in losses]
        all_pnls = [t.pnl_pct for t in group_trades]
        all_r = [t.pnl_r for t in group_trades]

        gross_wins = sum(win_pcts) if win_pcts else 0.0
        gross_losses = abs(sum(loss_pcts)) if loss_pcts else 0.0

        rows.append(BreakdownRow(
            label=label,
            trades=total,
            wins=len(wins),
            losses=len(losses),
            win_rate=round((len(wins) / total) * 100, 2) if total > 0 else 0.0,
            avg_win_pct=round(_safe_mean(win_pcts), 4) if win_pcts else 0.0,
            avg_loss_pct=round(_safe_mean(loss_pcts), 4) if loss_pcts else 0.0,
            total_pnl_pct=round(sum(all_pnls), 4),
            profit_factor=round(gross_wins / gross_losses, 4) if gross_losses > 0 else (
                float("inf") if gross_wins > 0 else 0.0
            ),
            avg_r=round(_safe_mean(all_r), 4),
        ))

    return rows


def _monthly_returns(trades: list[BacktestTrade]) -> list[dict]:
    """Compute P&L by calendar month."""
    monthly: dict[str, float] = defaultdict(float)
    monthly_count: dict[str, int] = defaultdict(int)

    for t in trades:
        key = t.exit_time.strftime("%Y-%m")
        monthly[key] += t.pnl_pct
        monthly_count[key] += 1

    return [
        {
            "month": month,
            "pnl_pct": round(pnl, 4),
            "trades": monthly_count[month],
        }
        for month, pnl in sorted(monthly.items())
    ]


# ---------------------------------------------------------------------------
# Sharpe / Sortino from per-bar equity returns (CORRECT methodology)
# ---------------------------------------------------------------------------

def _compute_sharpe_from_equity(equity_curve: list[dict], timeframe: str) -> float:
    """Annualised Sharpe ratio from per-bar equity returns (risk-free = 0).

    Uses the equity curve (per-bar snapshots) to compute bar-by-bar returns,
    then annualises using the correct timeframe factor.
    """
    if len(equity_curve) < 10:
        return 0.0

    equities = [p.get("equity_usd", p.get("equity_pct", 100.0)) for p in equity_curve]

    # Per-bar returns
    returns = []
    for i in range(1, len(equities)):
        if equities[i - 1] > 0:
            returns.append((equities[i] - equities[i - 1]) / equities[i - 1])

    if len(returns) < 5:
        return 0.0

    mean_r = _safe_mean(returns)
    std_r = _safe_std(returns)

    if std_r == 0:
        return 0.0

    # Bars per year
    bars_per_year = {"1w": 52, "1d": 252, "4h": 252 * 6, "1h": 252 * 24}
    ann_factor = math.sqrt(bars_per_year.get(timeframe, 252 * 6))

    sharpe = (mean_r / std_r) * ann_factor
    return round(sharpe, 4)


def _compute_sortino_from_equity(equity_curve: list[dict], timeframe: str) -> float:
    """Annualised Sortino ratio from per-bar equity returns.

    Like Sharpe but uses only downside deviation (negative returns).
    """
    if len(equity_curve) < 10:
        return 0.0

    equities = [p.get("equity_usd", p.get("equity_pct", 100.0)) for p in equity_curve]

    returns = []
    for i in range(1, len(equities)):
        if equities[i - 1] > 0:
            returns.append((equities[i] - equities[i - 1]) / equities[i - 1])

    if len(returns) < 5:
        return 0.0

    mean_r = _safe_mean(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return float("inf") if mean_r > 0 else 0.0
    downside_std = _safe_std(downside)

    if downside_std == 0:
        return 0.0

    bars_per_year = {"1w": 52, "1d": 252, "4h": 252 * 6, "1h": 252 * 24}
    ann_factor = math.sqrt(bars_per_year.get(timeframe, 252 * 6))

    sortino = (mean_r / downside_std) * ann_factor
    return round(sortino, 4)


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------

def _compute_max_drawdown(equity_curve: list[dict]) -> tuple[float, int]:
    """Compute maximum drawdown percentage and duration in bars.

    Returns (max_dd_pct, max_dd_duration_bars).
    """
    if not equity_curve:
        return 0.0, 0

    peak = equity_curve[0].get("equity_pct", 100.0)
    max_dd = 0.0
    dd_start = 0
    max_dd_duration = 0
    current_dd_start = 0
    in_drawdown = False

    for i, point in enumerate(equity_curve):
        equity = point.get("equity_pct", 100.0)

        if equity > peak:
            peak = equity
            if in_drawdown:
                duration = i - current_dd_start
                if duration > max_dd_duration:
                    max_dd_duration = duration
                in_drawdown = False
        else:
            dd = ((peak - equity) / peak) * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
            if not in_drawdown:
                in_drawdown = True
                current_dd_start = i

    # If still in drawdown at end
    if in_drawdown:
        duration = len(equity_curve) - current_dd_start
        if duration > max_dd_duration:
            max_dd_duration = duration

    return max_dd, max_dd_duration


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _safe_mean(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


# ---------------------------------------------------------------------------
# Monte Carlo confidence intervals
# ---------------------------------------------------------------------------

def monte_carlo_analysis(
    trades: list[BacktestTrade],
    initial_equity: float = 10_000.0,
    n_simulations: int = 1000,
    seed: int = 42,
) -> dict:
    """Run Monte Carlo simulation by shuffling trade order.

    Shuffles the sequence of trade returns (not the returns themselves)
    to estimate the distribution of outcomes and confidence intervals
    for total return and max drawdown.

    Returns a dict with:
      - median_return_pct, p5_return_pct, p95_return_pct
      - median_max_dd_pct, p5_max_dd_pct, p95_max_dd_pct
      - ruin_probability (% of sims ending below -50% drawdown)
      - n_simulations
    """
    if not trades or len(trades) < 5:
        return {
            "n_simulations": 0,
            "median_return_pct": 0.0,
            "p5_return_pct": 0.0,
            "p95_return_pct": 0.0,
            "median_max_dd_pct": 0.0,
            "p5_max_dd_pct": 0.0,
            "p95_max_dd_pct": 0.0,
            "ruin_probability_pct": 0.0,
        }

    rng = random.Random(seed)
    pnl_pcts = [t.pnl_net_pct for t in trades]

    final_returns = []
    max_drawdowns = []

    for _ in range(n_simulations):
        shuffled = pnl_pcts[:]
        rng.shuffle(shuffled)

        equity = initial_equity
        peak = equity

        sim_max_dd = 0.0

        for pnl in shuffled:
            equity *= (1 + pnl / 100.0)
            if equity > peak:
                peak = equity
            dd = ((peak - equity) / peak) * 100 if peak > 0 else 0.0
            if dd > sim_max_dd:
                sim_max_dd = dd

        final_ret = ((equity / initial_equity) - 1.0) * 100
        final_returns.append(final_ret)
        max_drawdowns.append(sim_max_dd)

    final_returns.sort()
    max_drawdowns.sort()

    n = len(final_returns)
    p5_idx = int(n * 0.05)
    p50_idx = int(n * 0.50)
    p95_idx = min(int(n * 0.95), n - 1)

    ruin_count = sum(1 for dd in max_drawdowns if dd > 50.0)

    return {
        "n_simulations": n_simulations,
        "median_return_pct": round(final_returns[p50_idx], 2),
        "p5_return_pct": round(final_returns[p5_idx], 2),
        "p95_return_pct": round(final_returns[p95_idx], 2),
        "median_max_dd_pct": round(max_drawdowns[p50_idx], 2),
        "p5_max_dd_pct": round(max_drawdowns[p5_idx], 2),
        "p95_max_dd_pct": round(max_drawdowns[p95_idx], 2),
        "ruin_probability_pct": round((ruin_count / n_simulations) * 100, 1),
    }
