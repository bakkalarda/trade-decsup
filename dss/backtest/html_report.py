"""HTML equity curve report — self-contained single-file output.

Generates a standalone HTML page with:
  - Equity curve chart (via embedded Chart.js CDN)
  - Drawdown overlay
  - Summary statistics grid
  - Setup type breakdown table
  - Monthly returns heatmap
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from dss.backtest import BacktestResult


def generate_html_report(
    result: BacktestResult,
    output_path: Optional[str] = None,
) -> str:
    """Generate a self-contained HTML backtest report.

    Args:
        result: Complete backtest result.
        output_path: If provided, write to this file path.

    Returns:
        The HTML string.
    """
    m = result.metrics
    cfg = result.config

    # Prepare equity curve data
    timestamps = []
    equity_values = []
    drawdown_values = []

    peak = 100.0
    for pt in result.equity_curve:
        timestamps.append(pt.get("timestamp", ""))
        eq_pct = pt.get("equity_pct", 100.0)
        equity_values.append(round(eq_pct, 2))
        if eq_pct > peak:
            peak = eq_pct
        dd = ((peak - eq_pct) / peak) * 100 if peak > 0 else 0
        drawdown_values.append(round(-dd, 2))

    # Monthly returns for heatmap
    monthly_data = m.monthly_returns

    # Breakdown by setup
    setup_breakdown = [
        {
            "label": r.label,
            "trades": r.trades,
            "win_rate": r.win_rate,
            "pnl": round(r.total_pnl_pct, 2),
            "pf": round(r.profit_factor, 2) if r.profit_factor != float("inf") else 999,
        }
        for r in m.by_setup_type
    ]

    compound_ret = (
        (result.final_equity / cfg.initial_equity - 1) * 100
        if result.final_equity
        else 0
    )
    compound_class = "positive" if compound_ret > 0 else "negative"

    # Build data block for JS
    data_js = json.dumps({
        "timestamps": timestamps,
        "equity": equity_values,
        "drawdown": drawdown_values,
        "monthly": monthly_data,
        "setups": setup_breakdown,
    })

    html = _build_html(
        asset=cfg.asset,
        timeframe=cfg.timeframe,
        start=cfg.start.strftime("%Y-%m-%d"),
        end=cfg.end.strftime("%Y-%m-%d"),
        total_trades=m.total_trades,
        wins=m.wins,
        losses=m.losses,
        win_rate=m.win_rate_pct,
        compound_return=round(compound_ret, 2),
        compound_class=compound_class,
        final_equity=round(result.final_equity, 0) if result.final_equity else 0,
        initial_equity=cfg.initial_equity,
        profit_factor=(
            round(m.profit_factor_net, 2)
            if m.profit_factor_net != float("inf")
            else "INF"
        ),
        sharpe=round(m.sharpe_ratio, 2),
        sortino=round(m.sortino_ratio, 2),
        calmar=round(m.calmar_ratio, 2),
        max_dd=round(m.max_drawdown_pct, 2),
        expectancy_r=round(m.expectancy_r, 2),
        avg_bars_held=round(m.avg_bars_held, 1),
        total_costs=round(m.total_costs_pct, 2),
        data_js=data_js,
    )

    if output_path:
        Path(output_path).write_text(html, encoding="utf-8")

    return html


def _build_html(**kw) -> str:
    """Construct the HTML using f-string to avoid .format() brace conflicts."""

    asset = kw["asset"]
    tf = kw["timeframe"]
    start = kw["start"]
    end = kw["end"]
    tt = kw["total_trades"]
    w = kw["wins"]
    lo = kw["losses"]
    wr = kw["win_rate"]
    cr = kw["compound_return"]
    cc = kw["compound_class"]
    fe = kw["final_equity"]
    ie = kw["initial_equity"]
    pf = kw["profit_factor"]
    sh = kw["sharpe"]
    so = kw["sortino"]
    ca = kw["calmar"]
    md = kw["max_dd"]
    er = kw["expectancy_r"]
    ab = kw["avg_bars_held"]
    tc = kw["total_costs"]
    dj = kw["data_js"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report — {asset} {tf}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {{ --bg: #0d1117; --card: #161b22; --text: #c9d1d9; --green: #3fb950;
           --red: #f85149; --blue: #58a6ff; --border: #30363d; --muted: #8b949e; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
         background: var(--bg); color: var(--text); padding: 20px; }}
  .header {{ text-align: center; margin-bottom: 24px; }}
  .header h1 {{ color: var(--blue); font-size: 1.6em; }}
  .header .meta {{ color: var(--muted); font-size: 0.9em; margin-top: 4px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
           gap: 12px; margin-bottom: 24px; }}
  .stat {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px;
           padding: 12px; text-align: center; }}
  .stat .value {{ font-size: 1.3em; font-weight: bold; }}
  .stat .label {{ color: var(--muted); font-size: 0.8em; margin-top: 4px; }}
  .positive {{ color: var(--green); }}
  .negative {{ color: var(--red); }}
  .chart-box {{ background: var(--card); border: 1px solid var(--border);
                border-radius: 8px; padding: 16px; margin-bottom: 24px; }}
  .chart-box h2 {{ font-size: 1.1em; margin-bottom: 12px; color: var(--blue); }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card);
           border: 1px solid var(--border); border-radius: 8px; margin-bottom: 24px; }}
  th, td {{ padding: 8px 12px; text-align: right; border-bottom: 1px solid var(--border); }}
  th {{ background: #1c2128; color: var(--blue); font-size: 0.85em; text-transform: uppercase; }}
  td:first-child, th:first-child {{ text-align: left; }}
  .monthly-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(90px, 1fr));
                   gap: 6px; margin-bottom: 24px; }}
  .month-cell {{ padding: 8px; border-radius: 4px; text-align: center; font-size: 0.8em; }}
</style>
</head>
<body>

<div class="header">
  <h1>Trade DSS Backtest Report</h1>
  <div class="meta">{asset} {tf} &mdash; {start} to {end}</div>
</div>

<div class="grid">
  <div class="stat"><div class="value {cc}">{cr}%</div><div class="label">Compound Return</div></div>
  <div class="stat"><div class="value">${fe:,.0f}</div><div class="label">Final Equity (from ${ie:,.0f})</div></div>
  <div class="stat"><div class="value">{tt}</div><div class="label">Trades ({w}W / {lo}L)</div></div>
  <div class="stat"><div class="value">{wr}%</div><div class="label">Win Rate</div></div>
  <div class="stat"><div class="value">{pf}</div><div class="label">Profit Factor (net)</div></div>
  <div class="stat"><div class="value">{sh}</div><div class="label">Sharpe Ratio</div></div>
  <div class="stat"><div class="value">{so}</div><div class="label">Sortino Ratio</div></div>
  <div class="stat"><div class="value negative">{md}%</div><div class="label">Max Drawdown</div></div>
  <div class="stat"><div class="value">{er}R</div><div class="label">Expectancy</div></div>
  <div class="stat"><div class="value">{ab}</div><div class="label">Avg Bars Held</div></div>
  <div class="stat"><div class="value">{tc}%</div><div class="label">Total Costs</div></div>
  <div class="stat"><div class="value">{ca}</div><div class="label">Calmar Ratio</div></div>
</div>

<div class="chart-box">
  <h2>Equity Curve</h2>
  <canvas id="equityChart" height="100"></canvas>
</div>

<div class="chart-box">
  <h2>Drawdown</h2>
  <canvas id="drawdownChart" height="60"></canvas>
</div>

<h2 style="color: var(--blue); margin-bottom: 12px;">Setup Type Breakdown</h2>
<table id="setupTable">
  <thead><tr><th>Setup</th><th>Trades</th><th>Win %</th><th>P&amp;L %</th><th>PF</th></tr></thead>
  <tbody></tbody>
</table>

<h2 style="color: var(--blue); margin-bottom: 12px;">Monthly Returns</h2>
<div class="monthly-grid" id="monthlyGrid"></div>

<script>
const D = {dj};

new Chart(document.getElementById('equityChart'), {{
  type: 'line',
  data: {{ labels: D.timestamps, datasets: [{{
    label: 'Equity %', data: D.equity, borderColor: '#58a6ff',
    backgroundColor: 'rgba(88,166,255,0.1)', fill: true, pointRadius: 0,
    borderWidth: 1.5, tension: 0.1
  }}] }},
  options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ maxTicksLimit: 12, color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
      y: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e', callback: v => v + '%' }} }}
    }}
  }}
}});

new Chart(document.getElementById('drawdownChart'), {{
  type: 'line',
  data: {{ labels: D.timestamps, datasets: [{{
    label: 'Drawdown', data: D.drawdown, borderColor: '#f85149',
    backgroundColor: 'rgba(248,81,73,0.15)', fill: true, pointRadius: 0,
    borderWidth: 1.5, tension: 0.1
  }}] }},
  options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ maxTicksLimit: 12, color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
      y: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e', callback: v => v + '%' }} }}
    }}
  }}
}});

const tbody = document.querySelector('#setupTable tbody');
D.setups.forEach(s => {{
  const cls = s.pnl > 0 ? 'positive' : (s.pnl < 0 ? 'negative' : '');
  tbody.innerHTML += '<tr><td>'+s.label+'</td><td>'+s.trades+'</td>' +
    '<td>'+s.win_rate.toFixed(1)+'%</td>' +
    '<td class="'+cls+'">'+(s.pnl>0?'+':'')+s.pnl.toFixed(2)+'%</td>' +
    '<td>'+(s.pf>=999?'INF':s.pf.toFixed(2))+'</td></tr>';
}});

const mg = document.getElementById('monthlyGrid');
D.monthly.forEach(m => {{
  const p = m.pnl_pct, i = Math.min(Math.abs(p)/5,1);
  const bg = p > 0
    ? 'rgba(63,185,80,'+(0.15+i*0.6)+')'
    : 'rgba(248,81,73,'+(0.15+i*0.6)+')';
  mg.innerHTML += '<div class="month-cell" style="background:'+bg+'">'+
    '<div style="font-weight:bold">'+(p>0?'+':'')+p.toFixed(1)+'%</div>'+
    '<div style="color:#8b949e;font-size:0.7em">'+m.month+'</div></div>';
}});
</script>
</body>
</html>"""
