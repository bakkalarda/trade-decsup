# Trade Decision Support System (DSS)

Multi-asset swing trading decision support engine for **BTC, ETH, XRP, SOL, XAU, XAG**.

Runs 24/7 via [OpenClaw](https://docs.clawd.bot/) agent with Telegram interface. The system evaluates a rule-based strategy through three gates (Macro, Flow, Structure), detects setups (Pullback, Breakout+Retest, Wyckoff Trap), scores them, and alerts when a trade candidate passes all gates.

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
│  Session Management       │         │  │ Gates            │   │
└──────────────────────────┘          │  │ Scoring + Vetoes │   │
                                      │  └──────────────────┘   │
                                      │  SQLite (audit + cache) │
                                      └─────────────────────────┘
```

**OpenClaw** handles: Telegram, scheduling, LLM conversation, headline monitoring, memory.
**Python DSS** handles: deterministic analysis, data ingestion, scoring, veto logic.

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
| Macro | -3..+3 | Cross-asset regime + event risk + sentiment |
| Flow | -3..+3 | Positioning, funding, OI, exchange flows |
| Structure | -3..+3 | Trend alignment, zone quality, acceptance |
| Phase | -2..+2 | Stage/Wyckoff alignment with setup type |
| Setup Quality | 0..+3 | R:R ratio, zone strength, setup cleanliness |

**Long alert:** total >= +7 and no vetoes
**Short alert:** total <= -7 and no vetoes

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
│   ├── cli.py              CLI entrypoint
│   ├── config.py           Config loader
│   ├── models/             Pydantic data models
│   ├── connectors/         Data ingestion (ccxt, yfinance, FRED, etc.)
│   ├── features/           Feature engineering (pivots, zones, trend, stage, Wyckoff)
│   ├── gates/              Signal gates (macro, flow, structure)
│   ├── engine/             Setup detection, scoring, vetoes, decisions
│   ├── storage/            SQLite database + repository
│   └── utils/              Math utilities (z-scores, ATR, etc.)
├── openclaw/               OpenClaw workspace files
│   ├── AGENTS.md           Trading rules + decision framework
│   ├── SOUL.md             Professional trader persona
│   ├── TOOLS.md            DSS CLI reference for the agent
│   ├── HEARTBEAT.md        Heartbeat checklist
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
