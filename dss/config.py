"""Configuration loader — reads YAML config files into typed structures."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Locate config directory
# ---------------------------------------------------------------------------

def _find_config_dir() -> Path:
    """Walk up from CWD or use DSS_CONFIG_DIR env var."""
    env = os.environ.get("DSS_CONFIG_DIR")
    if env:
        return Path(env)
    # Walk up from this file's location
    here = Path(__file__).resolve().parent.parent / "config"
    if here.is_dir():
        return here
    # Fallback: CWD/config
    return Path.cwd() / "config"


CONFIG_DIR = _find_config_dir()


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Typed config sections
# ---------------------------------------------------------------------------

class AssetConfig(BaseModel):
    asset_class: str = Field(alias="class", default="crypto")
    tier: str = "A"
    ccxt_symbol: Optional[str] = None
    ccxt_perp_symbol: Optional[str] = None
    exchange: str = "binance"
    yfinance_symbol: Optional[str] = None
    timeframes: list[str] = Field(default_factory=lambda: ["1w", "1d", "4h"])
    atr_period: int = 14
    zone_lookback_bars: int = 120
    pivot_lookback: int = 5

    model_config = {"populate_by_name": True}


class MacroProxyConfig(BaseModel):
    yfinance_symbol: Optional[str] = None
    fred_series: Optional[str] = None


class ScoringConfig(BaseModel):
    macro_range: list[int] = [-3, 3]
    flow_range: list[int] = [-3, 3]
    structure_range: list[int] = [-3, 3]
    phase_range: list[int] = [-2, 2]
    setup_range: list[int] = [0, 3]
    options_range: list[int] = [-2, 2]
    mamis_range: list[int] = [-2, 2]
    long_threshold: float = 6
    short_threshold: float = -6
    size_multipliers: dict[str, float] = Field(
        default_factory=lambda: {"NORMAL": 1.0, "CAUTION": 0.5, "NO_TRADE": 0.0}
    )
    positioning_multipliers: dict[str, float] = Field(
        default_factory=lambda: {"CLEAN": 1.0, "WARM": 0.7, "HOT": 0.5, "EXTREME": 0.3}
    )


class MacroGateConfig(BaseModel):
    tier1_no_trade_before_hours: int = 48
    tier1_no_trade_after_hours: int = 6
    tier2_caution_before_hours: int = 24
    tier2_caution_after_hours: int = 2
    headline_high_threshold: float = 20
    headline_med_threshold: float = 12
    fear_extreme_threshold: int = 15
    greed_extreme_threshold: int = 85


class FlowGateConfig(BaseModel):
    funding_extreme_pos: float = 2.5
    funding_extreme_neg: float = -2.5
    oi_elevated_z: float = 1.5
    oi_extreme_z: float = 2.5
    inflow_spike_z: float = 2.0
    crowding_warm: float = 1.0
    crowding_hot: float = 2.0
    crowding_extreme: float = 3.0
    reserve_slope_bullish: float = -0.5
    reserve_slope_bearish: float = 0.5
    zscore_lookback: int = 90


class StructureGateConfig(BaseModel):
    acceptance_follow_through_bars: int = 3
    acceptance_retest_buffer_atr: float = 0.3
    zone_cluster_atr_multiplier: float = 1.0
    zone_min_touches: int = 2
    zone_strength_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "touches": 0.3, "displacement": 0.3,
            "time_separation": 0.2, "recency": 0.2,
        }
    )
    trendline_min_pivots: int = 2
    trendline_max_residual_atr: float = 0.5
    stage_compression_atr_percentile: int = 25
    stage_trend_hhhl_min_swings: int = 3
    vol_low_percentile: int = 25
    vol_high_percentile: int = 75


class FreshnessConfig(BaseModel):
    max_age_minutes: dict[str, int] = Field(
        default_factory=lambda: {
            "ohlcv_4h": 300, "ohlcv_daily": 1500, "ohlcv_weekly": 10200,
            "derivatives": 60, "onchain": 360, "sentiment": 720, "macro": 1500,
        }
    )
    stale_action: str = "CAUTION"


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------

class DSSConfig(BaseModel):
    assets: dict[str, AssetConfig] = Field(default_factory=dict)
    macro_proxies: dict[str, MacroProxyConfig] = Field(default_factory=dict)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    macro_gate: MacroGateConfig = Field(default_factory=MacroGateConfig)
    flow_gate: FlowGateConfig = Field(default_factory=FlowGateConfig)
    structure_gate: StructureGateConfig = Field(default_factory=StructureGateConfig)
    freshness: FreshnessConfig = Field(default_factory=FreshnessConfig)


def load_config() -> DSSConfig:
    """Load all YAML config files and return a typed DSSConfig."""
    assets_raw = _load_yaml("assets.yaml")
    scoring_raw = _load_yaml("scoring.yaml")
    connectors_raw = _load_yaml("connectors.yaml")

    asset_defs = {}
    for name, cfg in assets_raw.get("assets", {}).items():
        asset_defs[name] = AssetConfig(**cfg)

    proxy_defs = {}
    for name, cfg in assets_raw.get("macro_proxies", {}).items():
        proxy_defs[name] = MacroProxyConfig(**cfg)

    freshness_data = connectors_raw.get("freshness", {})

    return DSSConfig(
        assets=asset_defs,
        macro_proxies=proxy_defs,
        scoring=ScoringConfig(**scoring_raw.get("scoring", {})),
        macro_gate=MacroGateConfig(**scoring_raw.get("macro_gate", {})),
        flow_gate=FlowGateConfig(**scoring_raw.get("flow_gate", {})),
        structure_gate=StructureGateConfig(**scoring_raw.get("structure_gate", {})),
        freshness=FreshnessConfig(**freshness_data) if freshness_data else FreshnessConfig(),
    )


# Singleton
_config: Optional[DSSConfig] = None


def get_config() -> DSSConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config
