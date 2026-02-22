# Trade Decision Support System (DSS)

Multi-asset swing trading decision support engine for **BTC, ETH, XRP, SOL, XAU, XAG**.

Runs 24/7 via [OpenClaw](https://docs.clawd.bot/) agent with Telegram interface. The system evaluates a rule-based strategy through an 8-gate scoring engine, detects OTE-based setups (8 types: Pullback, Wyckoff Trap, Range Trade, Divergence Reversal, Volume Climax, and more), scores them with MAMIS/Wyckoff confirmations, and alerts when a trade candidate passes all gates.

**This is a decision-support tool, not auto-trading software. The human places all orders.**

---

## Architecture

```
OpenClaw Gateway (always-on)          Python DSS Engine (CLI)
┌──────────────────────────┐          ┌─────────────────────────┐
│  Telegram ←→ LLM Agent   │──exec──→│  dss scan --asset BTC   │
│  Cron Scheduler           │         │  ┌──────────────────┐   │
│  Web Search (headlines)   │←──JSON──│  │ Connectors       │   │
│  Agent Memory             │         │  │ Features         │   │
│  Session Management       │         │  │ Gates (8-gate)   │   │
└──────────────────────────┘          │  │ Scoring + Vetoes │   │
                                      │  │ Backtest Engine  │   │
                                      │  └──────────────────┘   │
                                      │  SQLite (audit + cache) │
                                      └─────────────────────────┘
```

**OpenClaw** handles: Telegram, scheduling, LLM conversation, headline monitoring, memory.
**Python DSS** handles: deterministic analysis, data ingestion, scoring, veto logic, backtesting.

### V8 Architecture — OTE-Based Entries with Confirmation Layers

The DSS uses an **Optimal Trade Entry (OTE)** philosophy:
- **Entry triggers** are price-action setups at key levels (pullbacks, traps, range extremes)
- **Confirmation layers** (MAMIS cycle phase, Wyckoff events, Fibonacci OTE) boost or penalize quality on ALL setups via the scorer — they are never standalone entry triggers
- **8-gate composite scoring** evaluates every candidate across macro, flow, structure, phase, setup quality, options, MAMIS, and multi-timeframe confluence

### Setup Types

| Code | Name | Status | Description |
|------|------|--------|-------------|
| A | A_PULLBACK | **Active** | Trend pullback continuation at S/R in Stage 2/4 |
| B | B_BREAKOUT_RETEST | Vetoed | Acceptance-based breakout retest |
| C | C_WYCKOFF_TRAP | **Active** | Spring/UTAD reversal with strict stage gating |
| D | D_MAMIS_TRANSITION | Legacy | Removed as standalone; MAMIS is confirmation only |
| E | E_RANGE_TRADE | **Active** | Buy range low in defined range (LONG only) |
| F | F_DIVERGENCE_REVERSAL | Available | RSI divergence reversal at S/R zones |
| G | G_VOLUME_CLIMAX | Available | Volume exhaustion spike at key zone |
| H | H_FIB_OTE | Defined | Fibonacci golden pocket retracement |

---

## Quick Start

### 1. Install Python dependencies

```bash
cd trade_decsup
pip install -e .
```

### 2. Initialize the database

```bash
python -c "from dss.storage.database import init_db; init_db()"
```

### 3. Test the DSS

```bash
dss status                                    # Health check
dss data refresh --asset BTC --timeframe 4h   # Fetch data
dss scan --asset BTC --timeframe 4h           # Full scan pipeline
dss macro-state                               # Macro regime
```

### 4. Install OpenClaw

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

### 5. Configure OpenClaw workspace

Set `agents.defaults.workspace` in `~/.openclaw/openclaw.json` to point to the `openclaw/` directory in this project (or symlink it).

### 6. Set up Telegram + cron jobs

```bash
openclaw channels login telegram
bash setup_openclaw.sh
```

---

## DSS CLI Reference

```bash
# Full pipeline scan
dss scan --asset BTC --timeframe 4h
dss scan --asset all --timeframe 4h

# Individual gates
dss macro-state
dss flow-state --asset BTC
dss structure --asset BTC --timeframe 1d

# Specific analysis
dss zones --asset BTC --timeframe 1w
dss stage --asset BTC

# Backtesting
dss backtest --asset BTC --start 2024-01-01 --end 2025-06-01 --timeframe 4h --output table
dss backtest --asset all --start 2024-01-01 --end 2025-06-01 --output json
dss backtest --asset ETH --start 2024-06-01 --end 2025-06-01 --risk-pct 1.0 --html report.html
dss walk-forward --asset BTC --start 2023-01-01 --train 6 --test 3 --step 3
dss backtest-report --asset BTC --output table

# Position management
dss position add --asset BTC --direction long --entry 98500 --stop 96200 --t1 104000 --t2 110000
dss position list
dss position check
dss position update --id 1 --trail-stop 99800
dss position close --id 1 --reason "T1 hit"

# Data management
dss data refresh --asset all
dss data export --asset BTC --timeframe 1d

# System health
dss status
```

All commands output JSON to stdout. Logs go to stderr.

---

## Configuration

All thresholds and parameters live in `config/`:

- `assets.yaml` — per-asset parameters (symbols, exchange, timeframes, ATR period)
- `scoring.yaml` — score weights, alert thresholds, veto rules, gate configs
- `connectors.yaml` — API endpoints, rate limits, data freshness rules

**No magic numbers in code.** Everything is configurable.

---

## Scoring System

| Gate | Range | Description |
|------|-------|-------------|
| Macro | ±1.5 (capped) | Cross-asset regime + event risk + sentiment (dampened — macro data lags) |
| Flow | -3..+3 | Positioning, funding, OI, exchange flows, crowding/squeeze |
| Structure | -3..+3 | Trend alignment, zone quality, acceptance |
| Phase | -2..+2 | Stage/Wyckoff alignment with setup type |
| Setup Quality | 0..+3 | R:R ratio, zone strength, setup cleanliness |
| Options | -2..+2 | Options market sentiment (crypto only) |
| MAMIS | -2..+2 | MAMIS cycle phase confirmation (all setups) |
| MTF | -1..+1 | Multi-timeframe trend confluence (HTF SMA alignment) |

**Long alert:** total ≥ +6.5 and no vetoes (crypto) / ≥ +4.6 (metals, ×0.71 scaling)
**Short alert:** total ≤ −6.5 and no vetoes (crypto) / ≤ −4.6 (metals)

---

## Data Sources (all free tier)

| Data | Source | Notes |
|------|--------|-------|
| Crypto OHLCV | ccxt (Binance) | 4H/Daily/Weekly |
| Crypto derivatives | ccxt (Binance) | Funding, OI |
| Metals/Indices | yfinance | XAU, XAG, DXY, SPX, VIX |
| Real yields | FRED API or TIP ETF proxy | 10Y TIPS |
| Sentiment | alternative.me | Fear & Greed |
| On-chain (basic) | blockchain.info | BTC exchange balance |
| Headlines | OpenClaw web_search | Agent-driven |
| Event calendar | OpenClaw web_search | Agent-driven |

---

## Project Structure

```
trade_decsup/
├── dss/                    Python DSS engine
│   ├── cli.py              CLI entrypoint (scan, backtest, walk-forward, etc.)
│   ├── config.py           Config loader
│   ├── models/             Pydantic data models (setup types A–H, positions, alerts)
│   ├── connectors/         Data ingestion (ccxt, yfinance, FRED, sentiment, options)
│   ├── features/           Feature engineering (pivots, zones, trend, stage, Wyckoff, MAMIS, trendlines)
│   ├── gates/              Signal gates (macro, flow, structure)
│   ├── engine/             Setup detection (A–H), 8-gate scorer, vetoes, decisions
│   ├── backtest/           Historical backtesting engine (replay, metrics, reports)
│   ├── storage/            SQLite database + repository
│   └── utils/              Math utilities (z-scores, ATR, RSI, etc.)
├── openclaw/               OpenClaw workspace files
│   ├── IDENTITY.md         Bot identity and introduction
│   ├── USER.md             Trader profile and preferences
│   ├── SOUL.md             Professional trader persona + OTE philosophy (customized)
│   ├── BRAIN.md            Live working memory (current market state)
│   ├── MEMORY.md           Long-term memory (performance history, lessons)
│   ├── HEARTBEAT.md        Autonomous thinking loop checklist
│   ├── CLIENTS.md          Client profile and service guidelines
│   ├── PLAYBOOK.md         Decision frameworks (scoring, vetoes, position mgmt)
│   ├── AGENTS.md           Trading rules + scan cycle workflow (V8)
│   ├── TOOLS.md            DSS CLI reference for the agent
│   └── skills/trade-dss/   OpenClaw skill definition
├── config/                 YAML configuration files
├── setup_openclaw.sh       OpenClaw cron job installer
├── pyproject.toml          Python package config
└── requirements.txt        Dependencies
```

---

## Environment Variables (optional)

| Variable | Description |
|----------|-------------|
| `FRED_API_KEY` | FRED API key for real yield data (falls back to TIP ETF proxy) |
| `DSS_CONFIG_DIR` | Override config directory location |
| `DSS_DB_PATH` | Override SQLite database location |

---

## License

This is a private trading tool. Not intended for distribution.
