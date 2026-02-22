"""Backtest report formatters — JSON and Rich console tables."""

from __future__ import annotations

import json
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from dss.backtest import BacktestResult, BacktestMetrics, BacktestTrade, BreakdownRow


# ---------------------------------------------------------------------------
# JSON output (for agent / CLI piping)
# ---------------------------------------------------------------------------

def format_json(result: BacktestResult) -> str:
    """Serialise a BacktestResult to indented JSON."""
    return result.model_dump_json(indent=2)


def format_json_dict(result: BacktestResult) -> dict:
    """Serialise a BacktestResult to a plain dict (for _json_out)."""
    return json.loads(result.model_dump_json())


# ---------------------------------------------------------------------------
# Rich console report
# ---------------------------------------------------------------------------

def print_report(result: BacktestResult, console: Optional[Console] = None):
    """Print a comprehensive Rich-formatted backtest report to the terminal."""
    if console is None:
        console = Console(stderr=True)

    m = result.metrics

    # --- Header ---
    cfg = result.config
    header = Text()
    header.append("BACKTEST REPORT", style="bold underline")
    header.append(f"\n{cfg.asset} {cfg.timeframe}  ")
    header.append(f"{cfg.start.strftime('%Y-%m-%d')} -> {cfg.end.strftime('%Y-%m-%d')}")
    header.append(f"\n{result.bars_processed} bars processed in {result.duration_seconds:.1f}s")
    if result.errors:
        header.append(f"\n{len(result.errors)} warning(s)", style="yellow")
    console.print(Panel(header, title="Trade DSS Backtest"))

    # --- Summary table ---
    summary = Table(title="Performance Summary", show_header=False, padding=(0, 2))
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", justify="right")

    _add_pnl_row(summary, "Total Return (gross)", m.total_return_pct, suffix="%")
    _add_pnl_row(summary, "Total Return (net)", m.total_return_net_pct, suffix="%")
    summary.add_row("Total Costs", f"{m.total_costs_pct:.2f}%")
    if hasattr(result, "final_equity") and result.final_equity:
        compound_ret = (result.final_equity / result.config.initial_equity - 1) * 100
        _add_pnl_row(summary, "Compound Return", compound_ret, suffix="%")
        summary.add_row("Final Equity", f"${result.final_equity:,.0f}")
    summary.add_row("Total Trades", str(m.total_trades))
    summary.add_row("Wins / Losses / BE", f"{m.wins} / {m.losses} / {m.breakeven}")
    _add_pnl_row(summary, "Win Rate", m.win_rate_pct, suffix="%")
    _add_pnl_row(summary, "Avg Win", m.avg_win_pct, suffix="%")
    _add_pnl_row(summary, "Avg Loss", m.avg_loss_pct, suffix="%", invert=True)
    _add_pnl_row(summary, "Largest Win", m.largest_win_pct, suffix="%")
    _add_pnl_row(summary, "Largest Loss", m.largest_loss_pct, suffix="%", invert=True)
    summary.add_row("", "")
    pf_str = f"{m.profit_factor:.2f}" if m.profit_factor != float("inf") else "INF"
    pf_net_str = f"{m.profit_factor_net:.2f}" if m.profit_factor_net != float("inf") else "INF"
    summary.add_row("Profit Factor (gross)", pf_str)
    summary.add_row("Profit Factor (net)", pf_net_str)
    summary.add_row("Avg R-Multiple", f"{m.avg_r_multiple:.2f}R")
    summary.add_row("Expectancy", f"{m.expectancy_r:.2f}R")
    summary.add_row("Sharpe Ratio", f"{m.sharpe_ratio:.2f}")
    summary.add_row("Sortino Ratio", f"{m.sortino_ratio:.2f}")
    summary.add_row("Calmar Ratio", f"{m.calmar_ratio:.2f}")
    summary.add_row("", "")
    summary.add_row("Max Drawdown", f"{m.max_drawdown_pct:.2f}%")
    summary.add_row("DD Duration", f"{m.max_drawdown_duration_bars} bars")
    summary.add_row("", "")
    summary.add_row("Avg Bars Held", f"{m.avg_bars_held:.1f}")
    summary.add_row("Avg Bars (Winners)", f"{m.avg_bars_winners:.1f}")
    summary.add_row("Avg Bars (Losers)", f"{m.avg_bars_losers:.1f}")
    summary.add_row("", "")
    summary.add_row("Alerts Generated", str(m.total_alerts))
    summary.add_row("Setups Rejected", str(m.total_rejected))
    summary.add_row("Orders Filled", str(m.orders_filled))
    summary.add_row("Orders Expired", str(m.orders_expired))

    console.print(summary)

    # --- Breakdown by setup type ---
    if m.by_setup_type:
        _print_breakdown(console, "By Setup Type", m.by_setup_type)

    # --- Breakdown by direction ---
    if m.by_direction:
        _print_breakdown(console, "By Direction", m.by_direction)

    # --- Breakdown by asset ---
    if m.by_asset and len(m.by_asset) > 1:
        _print_breakdown(console, "By Asset", m.by_asset)

    # --- Monthly returns ---
    if m.monthly_returns:
        monthly = Table(title="Monthly Returns", padding=(0, 1))
        monthly.add_column("Month", style="cyan")
        monthly.add_column("P&L %", justify="right")
        monthly.add_column("Trades", justify="right")

        for row in m.monthly_returns:
            pnl = row["pnl_pct"]
            style = "green" if pnl > 0 else ("red" if pnl < 0 else "dim")
            monthly.add_row(row["month"], f"{pnl:+.2f}%", str(row["trades"]), style=style)

        console.print(monthly)

    # --- Trade log (last 20) ---
    if result.trades:
        _print_trade_log(console, result.trades)

    # --- Monte Carlo analysis ---
    if result.trades and len(result.trades) >= 5:
        from dss.backtest.metrics import monte_carlo_analysis
        mc = monte_carlo_analysis(result.trades, result.config.initial_equity)
        mc_table = Table(title="Monte Carlo Analysis (1000 simulations)", show_header=False, padding=(0, 2))
        mc_table.add_column("Metric", style="cyan")
        mc_table.add_column("Value", justify="right")
        mc_table.add_row("Median Return", f"{mc['median_return_pct']:+.2f}%")
        mc_table.add_row("5th Percentile Return", Text(f"{mc['p5_return_pct']:+.2f}%", style="red"))
        mc_table.add_row("95th Percentile Return", Text(f"{mc['p95_return_pct']:+.2f}%", style="green"))
        mc_table.add_row("Median Max Drawdown", f"{mc['median_max_dd_pct']:.2f}%")
        mc_table.add_row("95th Percentile Max DD", Text(f"{mc['p95_max_dd_pct']:.2f}%", style="red"))
        mc_table.add_row("Ruin Probability (>50% DD)", Text(f"{mc['ruin_probability_pct']:.1f}%", style="red" if mc['ruin_probability_pct'] > 5 else "green"))
        console.print(mc_table)

    # --- Errors ---
    if result.errors:
        err_text = "\n".join(result.errors[:10])
        if len(result.errors) > 10:
            err_text += f"\n... and {len(result.errors) - 10} more"
        console.print(Panel(err_text, title="Warnings / Errors", style="yellow"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_breakdown(console: Console, title: str, rows: list[BreakdownRow]):
    """Print a breakdown table."""
    table = Table(title=title, padding=(0, 1))
    table.add_column("", style="cyan")
    table.add_column("Trades", justify="right")
    table.add_column("W/L", justify="right")
    table.add_column("Win %", justify="right")
    table.add_column("Avg Win", justify="right")
    table.add_column("Avg Loss", justify="right")
    table.add_column("Total P&L", justify="right")
    table.add_column("PF", justify="right")
    table.add_column("Avg R", justify="right")

    for r in rows:
        pnl_style = "green" if r.total_pnl_pct > 0 else ("red" if r.total_pnl_pct < 0 else "dim")
        pf_str = f"{r.profit_factor:.2f}" if r.profit_factor != float("inf") else "INF"
        table.add_row(
            r.label,
            str(r.trades),
            f"{r.wins}/{r.losses}",
            f"{r.win_rate:.1f}%",
            f"{r.avg_win_pct:+.2f}%",
            f"{r.avg_loss_pct:+.2f}%",
            Text(f"{r.total_pnl_pct:+.2f}%", style=pnl_style),
            pf_str,
            f"{r.avg_r:+.2f}R",
        )

    console.print(table)


def _print_trade_log(console: Console, trades: list[BacktestTrade], max_rows: int = 20):
    """Print a condensed trade log with score decomposition."""
    table = Table(title=f"Trade Log (last {min(len(trades), max_rows)} of {len(trades)})", padding=(0, 1))
    table.add_column("#", justify="right", style="dim")
    table.add_column("Dir", justify="center")
    table.add_column("Setup", style="cyan")
    table.add_column("Entry", justify="right")
    table.add_column("Exit", justify="right")
    table.add_column("P&L %", justify="right")
    table.add_column("R", justify="right")
    table.add_column("Bars", justify="right")
    table.add_column("Exit Reason")
    table.add_column("Tot", justify="right", style="dim")
    table.add_column("Ma", justify="right", style="dim")
    table.add_column("Fl", justify="right", style="dim")
    table.add_column("St", justify="right", style="dim")
    table.add_column("Ph", justify="right", style="dim")
    table.add_column("Op", justify="right", style="dim")
    table.add_column("Mm", justify="right", style="dim")

    for t in trades[-max_rows:]:
        pnl_style = "green" if t.pnl_pct > 0 else ("red" if t.pnl_pct < 0 else "dim")
        dir_style = "green" if t.direction.value == "LONG" else "red"
        table.add_row(
            str(t.trade_id),
            Text(t.direction.value[0], style=dir_style),
            t.setup_type.value.split("_", 1)[-1][:8],
            f"{t.entry_price:.2f}",
            f"{t.exit_price:.2f}",
            Text(f"{t.pnl_pct:+.2f}%", style=pnl_style),
            f"{t.pnl_r:+.1f}R",
            str(t.bars_held),
            t.exit_reason.value,
            f"{t.total_score:.1f}",
            f"{t.macro_score:+.1f}",
            f"{t.flow_score:+.1f}",
            f"{t.structure_score:+.1f}",
            f"{t.phase_score:+.1f}",
            f"{t.options_score:+.1f}",
            f"{t.mamis_score:+.1f}",
        )

    console.print(table)


def _add_pnl_row(
    table: Table,
    label: str,
    value: float,
    suffix: str = "",
    invert: bool = False,
):
    """Add a row to a summary table with colour based on sign."""
    if invert:
        style = "green" if value < 0 else ("red" if value > 0 else "dim")
    else:
        style = "green" if value > 0 else ("red" if value < 0 else "dim")
    table.add_row(label, Text(f"{value:+.2f}{suffix}", style=style))
