"""Historical data loader for backtesting.

Fetches and aligns OHLCV, macro, flow, sentiment, options, on-chain, and
DeFi data into a single ``BacktestDataBundle`` keyed by timestamp so the
replay engine can look things up bar-by-bar without any API calls.

Data sources:
  - ccxt / yfinance           — OHLCV price data
  - yfinance / FRED           — DXY, SPX, VIX, real yields
  - Alternative.me            — Fear & Greed index
  - ccxt                      — Funding rate history
  - Deribit / synthetic       — IV (DVOL) history
  - DeFiLlama                 — Total OI, TVL, stablecoin supply
  - Blockchain.com            — BTC exchange balance history
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

from dss.config import DSSConfig, AssetConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data bundle returned to the replay engine
# ---------------------------------------------------------------------------

@dataclass
class BacktestDataBundle:
    """All data needed for a backtest run, pre-fetched and aligned."""

    asset: str
    timeframe: str
    start: datetime
    end: datetime

    # Core price data — OHLCV DataFrame indexed by UTC timestamp
    ohlcv: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Macro proxies — daily resolution, forward-filled to bar timeframe
    dxy: pd.DataFrame = field(default_factory=pd.DataFrame)
    spx: pd.DataFrame = field(default_factory=pd.DataFrame)
    vix: pd.DataFrame = field(default_factory=pd.DataFrame)
    real_yields: pd.DataFrame = field(default_factory=pd.DataFrame)
    gold: pd.DataFrame = field(default_factory=pd.DataFrame)
    move_index: pd.DataFrame = field(default_factory=pd.DataFrame)
    credit_spread: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Sentiment — daily, forward-filled
    fear_greed: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Flow (crypto only) — funding rate history, OI where available
    funding: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Binance Futures intelligence (crypto only)
    top_trader_ls: pd.DataFrame = field(default_factory=pd.DataFrame)
    global_ls: pd.DataFrame = field(default_factory=pd.DataFrame)
    taker_buy_sell: pd.DataFrame = field(default_factory=pd.DataFrame)
    binance_oi: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Options — IV history (DVOL for crypto)
    iv_history: pd.DataFrame = field(default_factory=pd.DataFrame)

    # DeFi / on-chain
    total_oi: pd.DataFrame = field(default_factory=pd.DataFrame)
    tvl_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    stablecoin_supply: pd.DataFrame = field(default_factory=pd.DataFrame)
    exchange_balance: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Advanced on-chain analytics (article framework)
    nvt_signal: pd.DataFrame = field(default_factory=pd.DataFrame)
    hash_ribbons: pd.DataFrame = field(default_factory=pd.DataFrame)
    miner_revenue: pd.DataFrame = field(default_factory=pd.DataFrame)
    ssr: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Metadata
    bars_loaded: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Minimum viability: enough OHLCV bars.  Missing IV data is non-fatal
        — the options gate will simply score 0 (degraded but honest)."""
        return not self.ohlcv.empty and self.bars_loaded >= 20


def load_backtest_data(
    asset: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    cfg: DSSConfig,
    warmup_bars: int = 120,
) -> BacktestDataBundle:
    """Fetch all historical data required for a backtest.

    Extends the date range backwards by *warmup_bars* to allow indicator
    computation from bar 0 of the evaluation window.
    """
    bundle = BacktestDataBundle(asset=asset, timeframe=timeframe, start=start, end=end)
    asset_cfg = cfg.assets.get(asset)
    if asset_cfg is None:
        bundle.errors.append(f"Unknown asset: {asset}")
        return bundle

    # Extend start for warm-up
    bar_hours = _timeframe_hours(timeframe)
    warmup_delta = timedelta(hours=bar_hours * warmup_bars * 1.2)  # 20% buffer
    fetch_start = start - warmup_delta

    # --- 1. OHLCV price data ---
    bundle.ohlcv = _fetch_ohlcv(asset_cfg, timeframe, fetch_start, end, bundle)
    if bundle.ohlcv.empty:
        return bundle
    bundle.bars_loaded = len(bundle.ohlcv)

    # --- 2. Macro proxies (daily, forward-filled to bar TF) ---
    bundle.dxy = _fetch_macro_proxy("DXY", fetch_start, end, timeframe, bundle, cfg)
    bundle.spx = _fetch_macro_proxy("SPX", fetch_start, end, timeframe, bundle, cfg)
    bundle.vix = _fetch_macro_proxy("VIX", fetch_start, end, timeframe, bundle, cfg)
    bundle.real_yields = _fetch_real_yields(fetch_start, end, timeframe, cfg, bundle)
    bundle.gold = _fetch_macro_proxy("GOLD", fetch_start, end, timeframe, bundle, cfg)
    bundle.move_index = _fetch_macro_proxy("MOVE", fetch_start, end, timeframe, bundle, cfg)
    bundle.credit_spread = _fetch_macro_proxy("CREDIT", fetch_start, end, timeframe, bundle, cfg)

    # --- 3. Sentiment ---
    bundle.fear_greed = _fetch_fear_greed(fetch_start, end, timeframe, bundle)

    # --- 4. Flow (crypto only) ---
    if asset_cfg.asset_class == "crypto" and asset_cfg.ccxt_perp_symbol:
        bundle.funding = _fetch_funding(asset_cfg, fetch_start, end, bundle)

    # --- 4b. Binance Futures intelligence (crypto only) ---
    if asset_cfg.asset_class == "crypto" and asset_cfg.ccxt_perp_symbol:
        raw_symbol = (
            asset_cfg.ccxt_perp_symbol
            .replace("/", "")
            .replace(":USDT", "")
            .replace(":USD", "")
        )
        bundle.top_trader_ls = _fetch_binance_ls(raw_symbol, "top", bundle)
        bundle.global_ls = _fetch_binance_ls(raw_symbol, "global", bundle)
        bundle.taker_buy_sell = _fetch_binance_taker(raw_symbol, bundle)
        bundle.binance_oi = _fetch_binance_oi_hist(raw_symbol, bundle)

    # --- 5. Options / IV history (crypto only for backtest — Deribit DVOL) ---
    if asset_cfg.asset_class == "crypto" and asset.upper() in ("BTC", "ETH"):
        bundle.iv_history = _fetch_iv_history(asset.upper(), fetch_start, end, bundle)
        if bundle.iv_history.empty:
            msg = (
                f"Deribit IV (DVOL) data unavailable for {asset.upper()} — "
                "options gate will score 0 (degraded mode)"
            )
            logger.warning(msg)
            bundle.errors.append(msg)

    # --- 6. DeFiLlama: Total OI, TVL, stablecoin supply (crypto context) ---
    if asset_cfg.asset_class == "crypto":
        bundle.total_oi = _fetch_defillama_oi(bundle)
        bundle.tvl_history = _fetch_defillama_tvl(bundle)
        bundle.stablecoin_supply = _fetch_defillama_stablecoins(bundle)

    # --- 7. On-chain: BTC exchange balance ---
    if asset.upper() == "BTC":
        bundle.exchange_balance = _fetch_exchange_balance(bundle)

    # --- 8. Advanced on-chain analytics (BTC only) ---
    if asset.upper() == "BTC":
        bundle.nvt_signal = _fetch_nvt_signal(bundle)
        bundle.hash_ribbons = _fetch_hash_ribbons(bundle)
        bundle.miner_revenue = _fetch_miner_revenue(bundle)
        bundle.ssr = _fetch_ssr(bundle)

    logger.info(
        "Loaded %d bars for %s %s (%s → %s) | macro=%s gold=%s move=%s credit=%s "
        "sentiment=%s funding=%s iv=%s oi=%s tvl=%s stables=%s onchain=%s "
        "top_ls=%s taker=%s binance_oi=%s nvt=%s hash_ribbons=%s miner=%s ssr=%s",
        bundle.bars_loaded,
        asset,
        timeframe,
        bundle.ohlcv.index[0] if not bundle.ohlcv.empty else "?",
        bundle.ohlcv.index[-1] if not bundle.ohlcv.empty else "?",
        "OK" if not bundle.dxy.empty else "EMPTY",
        "OK" if not bundle.gold.empty else "EMPTY",
        "OK" if not bundle.move_index.empty else "EMPTY",
        "OK" if not bundle.credit_spread.empty else "EMPTY",
        "OK" if not bundle.fear_greed.empty else "EMPTY",
        "OK" if not bundle.funding.empty else "N/A",
        "OK" if not bundle.iv_history.empty else "N/A",
        "OK" if not bundle.total_oi.empty else "N/A",
        "OK" if not bundle.tvl_history.empty else "N/A",
        "OK" if not bundle.stablecoin_supply.empty else "N/A",
        "OK" if not bundle.exchange_balance.empty else "N/A",
        "OK" if not bundle.top_trader_ls.empty else "N/A",
        "OK" if not bundle.taker_buy_sell.empty else "N/A",
        "OK" if not bundle.binance_oi.empty else "N/A",
        "OK" if not bundle.nvt_signal.empty else "N/A",
        "OK" if not bundle.hash_ribbons.empty else "N/A",
        "OK" if not bundle.miner_revenue.empty else "N/A",
        "OK" if not bundle.ssr.empty else "N/A",
    )

    return bundle


# ---------------------------------------------------------------------------
# Internal fetch helpers
# ---------------------------------------------------------------------------

def _timeframe_hours(tf: str) -> int:
    mapping = {"1w": 168, "1d": 24, "4h": 4, "1h": 1}
    return mapping.get(tf, 4)


def _fetch_ohlcv(
    asset_cfg: AssetConfig,
    timeframe: str,
    start: datetime,
    end: datetime,
    bundle: BacktestDataBundle,
) -> pd.DataFrame:
    """Fetch full OHLCV range via the appropriate connector."""
    try:
        if asset_cfg.asset_class == "crypto":
            return _fetch_crypto_ohlcv(asset_cfg, timeframe, start, end)
        else:
            return _fetch_trad_ohlcv(asset_cfg, timeframe, start, end)
    except Exception as e:
        msg = f"OHLCV fetch failed for {bundle.asset}: {e}"
        logger.error(msg)
        bundle.errors.append(msg)
        return pd.DataFrame()


def _fetch_crypto_ohlcv(
    asset_cfg: AssetConfig,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Paginate through ccxt to get the full date range."""
    from dss.connectors.price import CryptoConnector

    conn = CryptoConnector(asset_cfg.exchange)
    symbol = asset_cfg.ccxt_symbol

    all_frames: list[pd.DataFrame] = []
    cursor = start
    page_limit = 1000

    while cursor < end:
        df = conn.fetch_ohlcv(symbol, timeframe, since=cursor, limit=page_limit)
        if df.empty:
            break
        all_frames.append(df)
        last_ts = df.index[-1].to_pydatetime()
        bar_delta = timedelta(hours=_timeframe_hours(timeframe))
        cursor = last_ts + bar_delta
        if len(df) < page_limit:
            break

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames)
    combined = combined[~combined.index.duplicated(keep="last")]
    combined.sort_index(inplace=True)

    # Trim to requested range
    end_ts = (
        pd.Timestamp(end.replace(tzinfo=None), tz="UTC")
        if end.tzinfo
        else pd.Timestamp(end, tz="UTC")
    )
    combined = combined[combined.index <= end_ts]
    return combined


def _fetch_trad_ohlcv(
    asset_cfg: AssetConfig,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Fetch traditional asset data via yfinance."""
    from dss.connectors.price import TraditionalConnector

    conn = TraditionalConnector()
    return conn.fetch_ohlcv(
        asset_cfg.yfinance_symbol, timeframe, since=start, limit=10_000
    )


def _fetch_macro_proxy(
    macro_type: str,
    start: datetime,
    end: datetime,
    target_tf: str,
    bundle: BacktestDataBundle,
    cfg: DSSConfig,
) -> pd.DataFrame:
    """Fetch a macro proxy using the MacroConnector (with fallbacks).

    Uses days from *start* to today (not start-to-end) to ensure yfinance
    fetches far enough back to cover the entire backtest window.
    """
    try:
        from dss.connectors.macro import MacroConnector

        mc = MacroConnector()
        # Calculate days from start to NOW to ensure full coverage
        days_needed = (datetime.now(timezone.utc) - start).days + 30

        if macro_type == "DXY":
            df = mc.fetch_dxy(limit=days_needed)
        elif macro_type == "SPX":
            df = mc.fetch_spx(limit=days_needed)
        elif macro_type == "VIX":
            df = mc.fetch_vix(limit=days_needed)
        elif macro_type == "GOLD":
            df = mc.fetch_gold(limit=days_needed)
        elif macro_type == "MOVE":
            df = mc.fetch_move_index(limit=days_needed)
        elif macro_type == "CREDIT":
            df = mc.fetch_credit_spread(limit=days_needed)
        else:
            df = pd.DataFrame()

        mc.close()

        if df.empty:
            return df
        return _resample_ffill(df, target_tf)

    except Exception as e:
        bundle.errors.append(f"Macro proxy {macro_type}: {e}")
        return pd.DataFrame()


def _fetch_real_yields(
    start: datetime,
    end: datetime,
    target_tf: str,
    cfg: DSSConfig,
    bundle: BacktestDataBundle,
) -> pd.DataFrame:
    """Fetch real yields (FRED or TIP proxy)."""
    try:
        from dss.connectors.macro import MacroConnector

        mc = MacroConnector()
        df = mc.fetch_real_yields(limit=2000)
        mc.close()
        if df.empty:
            return df
        return _resample_ffill(df, target_tf)
    except Exception as e:
        bundle.errors.append(f"Real yields: {e}")
        return pd.DataFrame()


def _fetch_fear_greed(
    start: datetime,
    end: datetime,
    target_tf: str,
    bundle: BacktestDataBundle,
) -> pd.DataFrame:
    """Fetch historical Fear & Greed index."""
    try:
        from dss.connectors.sentiment import SentimentConnector

        sc = SentimentConnector()
        days = (end - start).days + 30
        df = sc.fetch_fear_greed(limit=min(days, 2000))
        sc.close()
        if df.empty:
            return df
        return _resample_ffill(df, target_tf)
    except Exception as e:
        bundle.errors.append(f"Fear & Greed: {e}")
        return pd.DataFrame()


def _fetch_funding(
    asset_cfg: AssetConfig,
    start: datetime,
    end: datetime,
    bundle: BacktestDataBundle,
) -> pd.DataFrame:
    """Fetch historical funding rates for a crypto perpetual."""
    try:
        from dss.connectors.price import CryptoConnector

        conn = CryptoConnector(asset_cfg.exchange)
        perp = asset_cfg.ccxt_perp_symbol

        all_frames: list[pd.DataFrame] = []
        cursor = start
        page_limit = 1000

        while cursor < end:
            df = conn.fetch_funding_history(perp, since=cursor, limit=page_limit)
            if df.empty:
                break
            all_frames.append(df)
            last_ts = df.index[-1].to_pydatetime()
            cursor = last_ts + timedelta(hours=8)
            if len(df) < page_limit:
                break

        if not all_frames:
            return pd.DataFrame()

        combined = pd.concat(all_frames)
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)
        return combined

    except Exception as e:
        bundle.errors.append(f"Funding history: {e}")
        return pd.DataFrame()


def _fetch_iv_history(
    currency: str,
    start: datetime,
    end: datetime,
    bundle: BacktestDataBundle,
) -> pd.DataFrame:
    """Fetch historical IV (DVOL) from Deribit for crypto assets.
    """
    try:
        from dss.connectors.options import DeribitOptionsConnector

        conn = DeribitOptionsConnector()
        days_total = (end - start).days + 30
        df = conn.fetch_options_history(currency, days=days_total)
        conn.close()

        if df.empty:
            logger.warning("No IV history for %s from Deribit", currency)
            bundle.errors.append(f"IV history: no Deribit IV data for {currency}")
            return pd.DataFrame()

        # Filter to date range
        start_ts = (
            pd.Timestamp(start.replace(tzinfo=None), tz="UTC")
            if start.tzinfo
            else pd.Timestamp(start, tz="UTC")
        )
        end_ts = (
            pd.Timestamp(end.replace(tzinfo=None), tz="UTC")
            if end.tzinfo
            else pd.Timestamp(end, tz="UTC")
        )
        df = df[(df.index >= start_ts) & (df.index <= end_ts)]
        return df

    except Exception as e:
        logger.warning("IV history fetch failed: %s", e)
        bundle.errors.append(f"IV history: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# NEW: DeFiLlama data (free, no key)
# ---------------------------------------------------------------------------


def _fetch_defillama_oi(bundle: BacktestDataBundle) -> pd.DataFrame:
    """Fetch aggregated perps OI history from DeFiLlama.

    This gives total OI across all exchanges — much better than single-exchange.
    Used in flow state to compute oi_z-score.
    """
    try:
        from dss.connectors.derivatives import DerivativesConnector

        dc = DerivativesConnector()
        df = dc.fetch_defillama_perps_oi()
        dc.close()

        if df.empty:
            return df

        # Resample to daily and forward-fill
        df_daily = df.resample("1D").last().ffill()
        return df_daily

    except Exception as e:
        bundle.errors.append(f"DeFiLlama OI: {e}")
        return pd.DataFrame()


def _fetch_defillama_tvl(bundle: BacktestDataBundle) -> pd.DataFrame:
    """Fetch global DeFi TVL history from DeFiLlama.

    TVL trend = capital commitment / risk appetite proxy.
    """
    try:
        from dss.connectors.defillama import DeFiLlamaConnector

        dl = DeFiLlamaConnector()
        df = dl.fetch_global_tvl_history()
        dl.close()
        return df

    except Exception as e:
        bundle.errors.append(f"DeFiLlama TVL: {e}")
        return pd.DataFrame()


def _fetch_defillama_stablecoins(bundle: BacktestDataBundle) -> pd.DataFrame:
    """Fetch stablecoin supply history from DeFiLlama.

    Rising stablecoin supply = fresh capital entering crypto (bullish).
    """
    try:
        from dss.connectors.defillama import DeFiLlamaConnector

        dl = DeFiLlamaConnector()
        df = dl.fetch_stablecoin_supply_history()
        dl.close()
        return df

    except Exception as e:
        bundle.errors.append(f"DeFiLlama stablecoins: {e}")
        return pd.DataFrame()


def _fetch_exchange_balance(bundle: BacktestDataBundle) -> pd.DataFrame:
    """Fetch BTC on-chain flow history from Blockchain.com.

    Uses tx volume / trade volume flow ratio as an accumulation proxy.
    """
    try:
        from dss.connectors.onchain import OnchainConnector

        oc = OnchainConnector()
        df = oc.fetch_btc_onchain_flow_history(timespan="2years")
        oc.close()
        return df

    except Exception as e:
        bundle.errors.append(f"BTC on-chain flow: {e}")
        return pd.DataFrame()


def _fetch_nvt_signal(bundle: BacktestDataBundle) -> pd.DataFrame:
    """Fetch NVT Signal (Network Value to Transactions, 90d smoothed).

    NVT Signal = Market Cap / 90d_MA(daily_tx_volume_usd).
    Overvalued (>150), Fair (50-150), Undervalued (<50).
    """
    try:
        from dss.connectors.onchain import OnchainConnector

        oc = OnchainConnector()
        df = oc.fetch_nvt_signal(timespan="2years")
        oc.close()
        return df

    except Exception as e:
        bundle.errors.append(f"NVT Signal: {e}")
        return pd.DataFrame()


def _fetch_hash_ribbons(bundle: BacktestDataBundle) -> pd.DataFrame:
    """Fetch Hash Ribbons (30d vs 60d SMA of hashrate).

    Capitulation = 30d < 60d SMA. Recovery buy = cross back above.
    """
    try:
        from dss.connectors.onchain import OnchainConnector

        oc = OnchainConnector()
        df = oc.fetch_hash_ribbons(timespan="2years")
        oc.close()
        return df

    except Exception as e:
        bundle.errors.append(f"Hash Ribbons: {e}")
        return pd.DataFrame()


def _fetch_miner_revenue(bundle: BacktestDataBundle) -> pd.DataFrame:
    """Fetch miner revenue history (block reward + fees).

    Declining miner revenue = sell pressure as miners liquidate.
    """
    try:
        from dss.connectors.onchain import OnchainConnector

        oc = OnchainConnector()
        df = oc.fetch_miner_revenue(timespan="2years")
        oc.close()
        return df

    except Exception as e:
        bundle.errors.append(f"Miner revenue: {e}")
        return pd.DataFrame()


def _fetch_ssr(bundle: BacktestDataBundle) -> pd.DataFrame:
    """Fetch Stablecoin Supply Ratio (SSR) = BTC mcap / stablecoin supply.

    Low SSR = stablecoins have high buying power (bullish dry powder).
    """
    try:
        from dss.connectors.onchain import OnchainConnector

        oc = OnchainConnector()
        df = oc.fetch_ssr(days=730)
        oc.close()
        return df

    except Exception as e:
        bundle.errors.append(f"SSR: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# NEW: Binance Futures intelligence data
# ---------------------------------------------------------------------------


def _fetch_binance_ls(
    raw_symbol: str, ls_type: str, bundle: BacktestDataBundle
) -> pd.DataFrame:
    """Fetch long/short ratio history from Binance Futures (free, no key).

    Args:
        raw_symbol: e.g. 'BTCUSDT'
        ls_type: 'top' for top-trader, 'global' for global account ratio
    """
    try:
        from dss.connectors.binance_futures import BinanceFuturesConnector

        bf = BinanceFuturesConnector()
        if ls_type == "top":
            df = bf.fetch_top_trader_ls_ratio(raw_symbol, period="4h", limit=200)
        else:
            df = bf.fetch_global_ls_ratio(raw_symbol, period="4h", limit=200)
        bf.close()
        return df

    except Exception as e:
        bundle.errors.append(f"Binance {ls_type} L/S: {e}")
        return pd.DataFrame()


def _fetch_binance_taker(
    raw_symbol: str, bundle: BacktestDataBundle
) -> pd.DataFrame:
    """Fetch taker buy/sell volume ratio from Binance Futures (free, no key)."""
    try:
        from dss.connectors.binance_futures import BinanceFuturesConnector

        bf = BinanceFuturesConnector()
        df = bf.fetch_taker_buy_sell_ratio(raw_symbol, period="4h", limit=200)
        bf.close()
        return df

    except Exception as e:
        bundle.errors.append(f"Binance taker buy/sell: {e}")
        return pd.DataFrame()


def _fetch_binance_oi_hist(
    raw_symbol: str, bundle: BacktestDataBundle
) -> pd.DataFrame:
    """Fetch historical open interest from Binance Futures (free, no key)."""
    try:
        from dss.connectors.binance_futures import BinanceFuturesConnector

        bf = BinanceFuturesConnector()
        df = bf.fetch_oi_history(raw_symbol, period="4h", limit=200)
        bf.close()
        return df

    except Exception as e:
        bundle.errors.append(f"Binance OI history: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Synthetic IV fallback
# ---------------------------------------------------------------------------


def _synthetic_iv_from_ohlcv(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Generate a synthetic IV proxy from realized volatility (ATR-based).

    Used when Deribit DVOL data is unavailable.
    """
    import numpy as np

    if ohlcv.empty:
        return pd.DataFrame()

    close = ohlcv["close"]
    log_returns = np.log(close / close.shift(1)).dropna()
    rolling_vol = log_returns.rolling(20, min_periods=10).std() * np.sqrt(365 * 6)
    iv_proxy = rolling_vol * 100

    df = pd.DataFrame(
        {
            "iv_close": iv_proxy,
            "iv_open": iv_proxy,
            "iv_high": iv_proxy * 1.05,
            "iv_low": iv_proxy * 0.95,
        },
        index=ohlcv.index,
    )

    return df.dropna()


# ---------------------------------------------------------------------------
# Resampling helpers
# ---------------------------------------------------------------------------


def _resample_ffill(df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """Resample a daily DataFrame to the target timeframe using forward-fill."""
    if target_tf == "1d" or df.empty:
        return df

    freq_map = {"1w": "W", "4h": "4h", "1h": "1h"}
    freq = freq_map.get(target_tf)
    if freq is None:
        return df

    if target_tf in ("4h", "1h"):
        resampled = df.resample(freq).ffill()
    else:
        resampled = df.resample(freq).last()

    return resampled.dropna(how="all")
