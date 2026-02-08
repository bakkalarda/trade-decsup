"""DSS CLI entrypoint — all commands output structured JSON."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

import typer
from rich.console import Console

from dss.config import get_config
from dss.storage.database import init_db
from dss.storage.repository import Repository

app = typer.Typer(
    name="dss",
    help="Trade Decision Support System — multi-asset swing trading engine.",
    no_args_is_help=True,
)
console = Console()

# Sub-commands
position_app = typer.Typer(help="Position management commands.")
data_app = typer.Typer(help="Data management commands.")
app.add_typer(position_app, name="position")
app.add_typer(data_app, name="data")


def _json_out(obj: dict):
    """Print JSON to stdout (for agent consumption)."""
    typer.echo(json.dumps(obj, indent=2, default=str))


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,  # Keep logs on stderr, JSON on stdout
    )


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------

@app.command()
def status(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """System health: data freshness, connector status, last scan times."""
    _setup_logging(verbose)
    cfg = get_config()
    engine = init_db()
    repo = Repository()

    # Data freshness
    freshness = repo.get_data_freshness()

    # Open positions
    positions = repo.get_open_positions()

    # Recent alerts
    recent_alerts = repo.get_recent_alerts(hours=24)

    # Connector health
    connector_health = {}
    try:
        from dss.connectors.price import CryptoConnector
        connector_health["crypto"] = CryptoConnector().health_check()
    except Exception as e:
        connector_health["crypto"] = {"status": "error", "error": str(e)}

    try:
        from dss.connectors.price import TraditionalConnector
        connector_health["traditional"] = TraditionalConnector().health_check()
    except Exception as e:
        connector_health["traditional"] = {"status": "error", "error": str(e)}

    try:
        from dss.connectors.sentiment import SentimentConnector
        connector_health["sentiment"] = SentimentConnector().health_check()
    except Exception as e:
        connector_health["sentiment"] = {"status": "error", "error": str(e)}

    _json_out({
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "assets_configured": list(cfg.assets.keys()),
        "data_freshness": freshness,
        "open_positions": len(positions),
        "recent_alerts_24h": len(recent_alerts),
        "connectors": connector_health,
    })
    repo.close()


@app.command()
def scan(
    asset: str = typer.Option(..., "--asset", "-a", help="Asset to scan (BTC, ETH, etc.) or 'all'"),
    timeframe: str = typer.Option("4h", "--timeframe", "-t"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    output: str = typer.Option("json", "--output", "-o"),
):
    """Full pipeline scan for an asset — gates, setups, scoring, vetoes."""
    _setup_logging(verbose)
    cfg = get_config()

    assets_to_scan = list(cfg.assets.keys()) if asset.lower() == "all" else [asset.upper()]

    results = []
    for a in assets_to_scan:
        if a not in cfg.assets:
            results.append({"asset": a, "error": f"Unknown asset: {a}"})
            continue
        result = _run_scan(a, timeframe, cfg)
        results.append(result)

    _json_out({"timestamp_utc": datetime.now(timezone.utc).isoformat(), "scans": results})


def _run_scan(asset: str, timeframe: str, cfg) -> dict:
    """Execute a full scan pipeline for one asset."""
    from dss.connectors.price import CryptoConnector, TraditionalConnector
    from dss.storage.repository import Repository

    repo = Repository()
    asset_cfg = cfg.assets[asset]
    result = {
        "asset": asset,
        "timeframe": timeframe,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    # Step 1: Fetch latest price data
    try:
        if asset_cfg.asset_class == "crypto":
            connector = CryptoConnector(asset_cfg.exchange)
            df = connector.fetch_ohlcv(asset_cfg.ccxt_symbol, timeframe)
        else:
            connector = TraditionalConnector()
            df = connector.fetch_ohlcv(asset_cfg.yfinance_symbol, timeframe)

        repo.cache_ohlcv(asset, timeframe, df)
        result["bars_fetched"] = len(df)
        result["latest_close"] = float(df["close"].iloc[-1])
        result["latest_bar_time"] = str(df.index[-1])
    except Exception as e:
        result["data_error"] = str(e)
        result["tradeability_override"] = "CAUTION"
        repo.close()
        return result

    # Step 2: Feature engineering
    try:
        from dss.features.pivots import detect_swing_pivots
        from dss.features.zones import build_sr_zones
        from dss.features.trend_state import classify_trend
        from dss.features.stage import classify_stage
        from dss.features.acceptance import detect_acceptance_rejection
        from dss.features.wyckoff import detect_wyckoff_events
        from dss.utils.math import atr as calc_atr, atr_percent, percentile_rank

        atr_val = calc_atr(df["high"], df["low"], df["close"], asset_cfg.atr_period)
        atr_pct = atr_percent(df["high"], df["low"], df["close"], asset_cfg.atr_period)
        atr_pctile = percentile_rank(atr_val, lookback=100)

        pivots = detect_swing_pivots(df, asset_cfg.pivot_lookback)
        zones = build_sr_zones(df, pivots, atr_val, cfg.structure_gate)
        trend = classify_trend(pivots)
        stage = classify_stage(df, pivots, trend, atr_pctile, cfg.structure_gate)
        acc_rej = detect_acceptance_rejection(df, zones, atr_val, cfg.structure_gate)
        wyckoff = detect_wyckoff_events(df, pivots, zones, stage)

        result["structure"] = {
            "trend": trend,
            "stage": stage,
            "wyckoff_event": wyckoff.get("event", "NONE"),
            "atr": float(atr_val.iloc[-1]) if len(atr_val) > 0 else 0,
            "atr_pct": float(atr_pct.iloc[-1]) if len(atr_pct) > 0 else 0,
            "atr_percentile": float(atr_pctile.iloc[-1]) if len(atr_pctile) > 0 else 50,
            "zones_count": len(zones),
            "zones": [z.model_dump() for z in zones[:6]],  # Top 6
            "pivots_count": len(pivots),
            "acceptance_rejection": acc_rej,
        }
    except Exception as e:
        result["structure_error"] = str(e)

    # Step 3: Flow & positioning (crypto only)
    if asset_cfg.asset_class == "crypto" and asset_cfg.ccxt_perp_symbol:
        try:
            from dss.gates.flow_gate import evaluate_flow_gate
            flow = evaluate_flow_gate(asset, asset_cfg, cfg.flow_gate)
            result["flow"] = flow
        except Exception as e:
            result["flow_error"] = str(e)

    # Step 4: Setup detection
    try:
        from dss.engine.setup_detector import detect_setups
        setups = detect_setups(asset, df, result.get("structure", {}), cfg)
        result["setups"] = [s.model_dump() for s in setups]
    except Exception as e:
        result["setup_error"] = str(e)

    # Step 5: Scoring & vetoes
    try:
        from dss.engine.scorer import score_setups
        from dss.engine.veto import apply_vetoes
        from dss.engine.decision import make_decisions

        scored = score_setups(result.get("setups", []), result, cfg)
        vetoed = apply_vetoes(scored, result, cfg)
        decisions = make_decisions(vetoed, cfg)
        result["decisions"] = decisions
    except Exception as e:
        result["decision_error"] = str(e)

    # Save state snapshot
    try:
        repo.save_state("scan", result, asset=asset, timeframe=timeframe)
    except Exception:
        pass

    repo.close()
    return result


@app.command(name="macro-state")
def macro_state_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    output: str = typer.Option("json", "--output", "-o"),
):
    """Evaluate the macro regime gate (cross-asset + event risk)."""
    _setup_logging(verbose)
    try:
        from dss.gates.macro_gate import evaluate_macro_gate
        cfg = get_config()
        state = evaluate_macro_gate(cfg)
        _json_out(state.model_dump())
    except Exception as e:
        _json_out({"error": str(e), "tradeability": "CAUTION"})


@app.command(name="flow-state")
def flow_state_cmd(
    asset: str = typer.Option(..., "--asset", "-a"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    output: str = typer.Option("json", "--output", "-o"),
):
    """Evaluate flow & positioning gate for an asset."""
    _setup_logging(verbose)
    try:
        from dss.gates.flow_gate import evaluate_flow_gate
        cfg = get_config()
        asset_upper = asset.upper()
        if asset_upper not in cfg.assets:
            _json_out({"error": f"Unknown asset: {asset_upper}"})
            raise typer.Exit(1)
        result = evaluate_flow_gate(asset_upper, cfg.assets[asset_upper], cfg.flow_gate)
        _json_out(result)
    except typer.Exit:
        raise
    except Exception as e:
        _json_out({"error": str(e)})


@app.command()
def structure(
    asset: str = typer.Option(..., "--asset", "-a"),
    timeframe: str = typer.Option("1d", "--timeframe", "-t"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    output: str = typer.Option("json", "--output", "-o"),
):
    """Evaluate technical structure for an asset+timeframe."""
    _setup_logging(verbose)
    try:
        from dss.gates.structure_gate import evaluate_structure_gate
        cfg = get_config()
        asset_upper = asset.upper()
        if asset_upper not in cfg.assets:
            _json_out({"error": f"Unknown asset: {asset_upper}"})
            raise typer.Exit(1)
        result = evaluate_structure_gate(asset_upper, timeframe, cfg)
        _json_out(result.model_dump())
    except typer.Exit:
        raise
    except Exception as e:
        _json_out({"error": str(e)})


@app.command()
def zones(
    asset: str = typer.Option(..., "--asset", "-a"),
    timeframe: str = typer.Option("1w", "--timeframe", "-t"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Compute and display S/R zones for an asset."""
    _setup_logging(verbose)
    try:
        from dss.gates.structure_gate import evaluate_structure_gate
        cfg = get_config()
        asset_upper = asset.upper()
        if asset_upper not in cfg.assets:
            _json_out({"error": f"Unknown asset: {asset_upper}"})
            raise typer.Exit(1)
        result = evaluate_structure_gate(asset_upper, timeframe, cfg)
        _json_out({
            "asset": asset_upper,
            "timeframe": timeframe,
            "zones": [z.model_dump() for z in result.sr_zones],
        })
    except typer.Exit:
        raise
    except Exception as e:
        _json_out({"error": str(e)})


@app.command()
def stage(
    asset: str = typer.Option(..., "--asset", "-a"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Classify the current Wyckoff stage for an asset."""
    _setup_logging(verbose)
    try:
        from dss.gates.structure_gate import evaluate_structure_gate
        cfg = get_config()
        asset_upper = asset.upper()
        if asset_upper not in cfg.assets:
            _json_out({"error": f"Unknown asset: {asset_upper}"})
            raise typer.Exit(1)
        result = evaluate_structure_gate(asset_upper, "1d", cfg)
        _json_out({
            "asset": asset_upper,
            "stage": result.stage.value,
            "stage_confidence": result.stage_confidence,
            "trend": result.trend_direction.value,
            "wyckoff_event": result.wyckoff_event.value,
            "volatility_regime": result.volatility_regime.value,
        })
    except typer.Exit:
        raise
    except Exception as e:
        _json_out({"error": str(e)})


@app.command()
def setups(
    asset: str = typer.Option(..., "--asset", "-a"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Detect setup candidates for an asset."""
    _setup_logging(verbose)
    _json_out({"info": "Setup detection — use 'dss scan' for full pipeline"})


@app.command()
def score(
    asset: str = typer.Option(..., "--asset", "-a"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Full score + veto analysis for an asset (alias for scan)."""
    _setup_logging(verbose)
    cfg = get_config()
    asset_upper = asset.upper()
    if asset_upper not in cfg.assets:
        _json_out({"error": f"Unknown asset: {asset_upper}"})
        raise typer.Exit(1)
    result = _run_scan(asset_upper, "4h", cfg)
    _json_out(result)


# ---------------------------------------------------------------------------
# Position sub-commands
# ---------------------------------------------------------------------------

@position_app.command("add")
def position_add(
    asset: str = typer.Option(..., "--asset", "-a"),
    direction: str = typer.Option(..., "--direction", "-d"),
    entry: float = typer.Option(..., "--entry"),
    stop: float = typer.Option(..., "--stop"),
    t1: float = typer.Option(..., "--t1"),
    t2: Optional[float] = typer.Option(None, "--t2"),
    size_mult: float = typer.Option(1.0, "--size-mult"),
    setup_type: Optional[str] = typer.Option(None, "--setup-type"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Add a new tracked position."""
    repo = Repository()
    pos_id = repo.add_position({
        "asset": asset.upper(),
        "direction": direction.upper(),
        "setup_type": setup_type,
        "entry_price": entry,
        "stop_loss": stop,
        "target_1": t1,
        "target_2": t2,
        "size_multiplier": size_mult,
        "notes": notes,
    })
    _json_out({"id": pos_id, "status": "created"})
    repo.close()


@position_app.command("list")
def position_list():
    """List all open positions."""
    repo = Repository()
    positions = repo.get_open_positions()
    _json_out({"positions": positions, "count": len(positions)})
    repo.close()


@position_app.command("update")
def position_update(
    id: int = typer.Option(..., "--id"),
    trail_stop: Optional[float] = typer.Option(None, "--trail-stop"),
    stop: Optional[float] = typer.Option(None, "--stop"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Update a position (trail stop, notes, etc.)."""
    repo = Repository()
    updates = {}
    if trail_stop is not None:
        updates["stop_loss"] = trail_stop
        updates["trail_active"] = True
    if stop is not None:
        updates["stop_loss"] = stop
    if notes is not None:
        updates["notes"] = notes
    repo.update_position(id, updates)
    pos = repo.get_position(id)
    _json_out(pos or {"error": "Position not found"})
    repo.close()


@position_app.command("check")
def position_check(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Check all open positions against current prices."""
    _setup_logging(verbose)
    from dss.connectors.price import CryptoConnector, TraditionalConnector
    cfg = get_config()
    repo = Repository()
    positions = repo.get_open_positions()

    if not positions:
        _json_out({"positions": [], "alerts": []})
        repo.close()
        return

    crypto_conn = CryptoConnector()
    trad_conn = TraditionalConnector()
    alerts = []

    for pos in positions:
        asset = pos["asset"]
        asset_cfg = cfg.assets.get(asset)
        if not asset_cfg:
            continue

        # Fetch current price
        try:
            if asset_cfg.asset_class == "crypto":
                df = crypto_conn.fetch_ohlcv(asset_cfg.ccxt_symbol, "1h", limit=1)
            else:
                df = trad_conn.fetch_ohlcv(asset_cfg.yfinance_symbol, "1h", limit=1)
            current = float(df["close"].iloc[-1])
        except Exception:
            continue

        pos["current_price"] = current
        direction = pos["direction"]
        entry = pos["entry_price"]

        # PnL
        if direction == "LONG":
            pnl_pct = ((current - entry) / entry) * 100
        else:
            pnl_pct = ((entry - current) / entry) * 100
        pos["unrealized_pnl_pct"] = round(pnl_pct, 2)

        # Check stop hit
        stop = pos["stop_loss"]
        if direction == "LONG" and current <= stop:
            alerts.append({"type": "STOP_HIT", "position_id": pos["id"], "asset": asset, "price": current, "stop": stop})
            repo.close_position(pos["id"], "STOPPED", pnl_pct)
        elif direction == "SHORT" and current >= stop:
            alerts.append({"type": "STOP_HIT", "position_id": pos["id"], "asset": asset, "price": current, "stop": stop})
            repo.close_position(pos["id"], "STOPPED", pnl_pct)

        # Check T1
        t1 = pos["target_1"]
        if direction == "LONG" and current >= t1:
            alerts.append({"type": "T1_HIT", "position_id": pos["id"], "asset": asset, "price": current, "target": t1})
        elif direction == "SHORT" and current <= t1:
            alerts.append({"type": "T1_HIT", "position_id": pos["id"], "asset": asset, "price": current, "target": t1})

        # Check T2
        t2 = pos.get("target_2")
        if t2:
            if direction == "LONG" and current >= t2:
                alerts.append({"type": "T2_HIT", "position_id": pos["id"], "asset": asset, "price": current, "target": t2})
            elif direction == "SHORT" and current <= t2:
                alerts.append({"type": "T2_HIT", "position_id": pos["id"], "asset": asset, "price": current, "target": t2})

        repo.update_position(pos["id"], {"current_price": current, "unrealized_pnl_pct": pnl_pct})

    _json_out({"positions": positions, "alerts": alerts})
    repo.close()


@position_app.command("close")
def position_close(
    id: int = typer.Option(..., "--id"),
    reason: str = typer.Option("manual", "--reason"),
):
    """Close a position manually."""
    repo = Repository()
    pos = repo.get_position(id)
    if not pos:
        _json_out({"error": "Position not found"})
        repo.close()
        raise typer.Exit(1)
    repo.close_position(id, reason)
    _json_out({"id": id, "status": "closed", "reason": reason})
    repo.close()


# ---------------------------------------------------------------------------
# Data sub-commands
# ---------------------------------------------------------------------------

@data_app.command("refresh")
def data_refresh(
    asset: str = typer.Option("all", "--asset", "-a"),
    timeframe: str = typer.Option("all", "--timeframe", "-t"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Force refresh data for an asset/timeframe."""
    _setup_logging(verbose)
    from dss.connectors.price import CryptoConnector, TraditionalConnector

    cfg = get_config()
    repo = Repository()

    assets_to_refresh = list(cfg.assets.keys()) if asset.lower() == "all" else [asset.upper()]
    results = {}

    crypto_conn = CryptoConnector()
    trad_conn = TraditionalConnector()

    for a in assets_to_refresh:
        acfg = cfg.assets.get(a)
        if not acfg:
            results[a] = {"error": f"Unknown asset: {a}"}
            continue

        tfs = acfg.timeframes if timeframe.lower() == "all" else [timeframe]
        results[a] = {}

        for tf in tfs:
            try:
                if acfg.asset_class == "crypto":
                    df = crypto_conn.fetch_ohlcv(acfg.ccxt_symbol, tf)
                else:
                    df = trad_conn.fetch_ohlcv(acfg.yfinance_symbol, tf)
                repo.cache_ohlcv(a, tf, df)
                results[a][tf] = {"bars": len(df), "latest": str(df.index[-1])}
            except Exception as e:
                results[a][tf] = {"error": str(e)}

    _json_out({"refreshed": results})
    repo.close()


@data_app.command("export")
def data_export(
    asset: str = typer.Option(..., "--asset", "-a"),
    timeframe: str = typer.Option("1d", "--timeframe", "-t"),
    output_dir: str = typer.Option("data/exports", "--output-dir"),
):
    """Export OHLCV data to parquet."""
    from dss.storage.export import export_ohlcv_parquet
    path = export_ohlcv_parquet(asset.upper(), timeframe, output_dir)
    _json_out({"exported": path or "no data"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
