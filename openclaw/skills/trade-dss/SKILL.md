---
name: trade-dss
description: "Multi-asset swing trading Decision Support System (V8). Scan markets, detect OTE-based setups (Pullback/Wyckoff Trap/Range Trade + 5 more), score with 8-gate composite engine, backtest strategies, manage positions."
metadata: {"openclaw": {"always": true, "emoji": "📊"}}
---

# Trade DSS Skill

## What this does

This skill provides a complete swing trading decision support workflow across **BTC, ETH, XRP, SOL, XAU, XAG**. It connects to a Python-based analysis engine (V8 architecture) that handles:

- **Price data fetching** — crypto via ccxt (Binance), metals/indices via yfinance
- **Technical structure analysis** — pivots, S/R zones, trend state, Wyckoff stages (1–4), acceptance/rejection
- **Flow & positioning analysis** — funding rates, OI, exchange flows, crowding/squeeze detection
- **Macro regime classification** — DXY, real yields, SPX/VIX, sentiment, event calendar
- **Setup detection** — 8 setup types (A–H), OTE-based entries with MAMIS/Wyckoff confirmation
- **8-gate composite scoring** — threshold ±6.5 (crypto), ±4.6 (metals)
- **Hard veto engine** — macro, flow, structure, counter-trend, regime, and asset vetoes
- **Historical backtesting** — full pipeline replay with equity curves, walk-forward validation
- **Position tracking** — stop/target monitoring with trailing stop support

## V8 Architecture

The DSS uses **Optimal Trade Entry (OTE)** philosophy:
- **Entry triggers**: price-action setups at key levels (A/C/E are primary, F/G available)
- **Confirmation layers**: MAMIS cycle phase and Wyckoff events boost quality on ALL setups via the scorer (`mamis_score` ±2, `phase_score` ±2) — they are not standalone entries
- **Fibonacci OTE**: `_fib_ote_confluence` helper checks 61.8%–78.6% golden pocket retracement as additional confirmation

### Setup Types (SetupType enum)

| Code | Name | Status | Description |
|------|------|--------|-------------|
| A | `A_PULLBACK` | **Active** | Trend pullback continuation at S/R in Stage 2/4 |
| B | `B_BREAKOUT_RETEST` | Vetoed | Acceptance-based breakout retest (unprofitable in backtest) |
| C | `C_WYCKOFF_TRAP` | **Active** | Spring/UTAD reversal with strict stage gating |
| D | `D_MAMIS_TRANSITION` | Legacy | Removed as standalone; MAMIS is now confirmation only |
| E | `E_RANGE_TRADE` | **Active** | Buy range low / sell range high in defined range (LONG only) |
| F | `F_DIVERGENCE_REVERSAL` | Available | RSI divergence reversal at S/R zones |
| G | `G_VOLUME_CLIMAX` | Available | Volume exhaustion spike at key zone |
| H | `H_FIB_OTE` | Defined | Fibonacci golden pocket retracement (disabled as standalone) |

### 8-Gate Scoring System

| Gate | Range | Description |
|------|-------|-------------|
| Macro | ±1.5 (capped) | Cross-asset regime + event risk + sentiment (dampened — macro lags) |
| Flow | ±3 | Positioning, funding, OI, exchange flows, crowding |
| Structure | ±3 | Trend alignment, zone quality, acceptance |
| Phase | ±2 | Stage/Wyckoff alignment with setup type |
| Setup Quality | 0..+3 | R:R ratio, zone strength, setup cleanliness |
| Options | ±2 | Options market sentiment (crypto only) |
| MAMIS | ±2 | MAMIS cycle phase alignment (confirmation layer) |
| MTF | ±1 | Multi-timeframe trend confluence (HTF SMA alignment) |

**Long alert:** total ≥ +6.5 and no vetoes (crypto) / ≥ +4.6 (metals, ×0.71 scaling)
**Short alert:** total ≤ −6.5 and no vetoes (crypto) / ≤ −4.6 (metals)

## How to use

### Scan all assets (cron job trigger)
```bash
exec dss scan --asset all --timeframe 4h
```

### Check macro environment
```bash
exec dss macro-state
```
Then also run `web_search` for headlines and event calendar.

### Analyze a specific asset
```bash
exec dss structure --asset BTC --timeframe 1d
exec dss flow-state --asset BTC
exec dss scan --asset BTC --timeframe 4h
```

### Run a backtest
```bash
exec dss backtest --asset BTC --start 2024-01-01 --end 2025-06-01 --timeframe 4h --risk-pct 1.0 --output json
exec dss backtest --asset all --start 2024-01-01 --end 2025-06-01 --output table
```

### Walk-forward validation
```bash
exec dss walk-forward --asset BTC --start 2023-01-01 --train 6 --test 3 --step 3
```

### View last backtest results
```bash
exec dss backtest-report --asset BTC --output table
```

### Manage positions
```bash
exec dss position list
exec dss position check
exec dss position add --asset BTC --direction long --entry 98500 --stop 96200 --t1 104000 --t2 110000
exec dss position update --id 1 --trail-stop 99800
exec dss position close --id 1 --reason "T1 hit"
```

## Cron job prompts

For the 4H scan cron job, use this prompt:
"Run the 4H scan cycle. Check headlines first, then run dss scan --asset all --timeframe 4H, check positions, and report findings."

For the daily update:
"Run the daily update. Full scan + daily structure for all assets + stage analysis + write daily summary to memory."

For the weekly update:
"Run the weekly update. Weekly structure + zone recalculation + macro regime reassessment + write weekly summary to memory."

## Output interpretation

The DSS outputs JSON. Key fields to look for:
- `decisions.alerts`: Setups that passed all gates (report these to user)
- `decisions.rejected`: Near-misses (include in daily diagnostics)
- `decisions.summary`: Human-readable summary
- `structure.stage`: Current Wyckoff stage (1–4)
- `structure.trend`: Trend direction (UP/DOWN/SIDEWAYS)
- `flow.crowding_state`: Positioning crowding (CLEAN/WARM/HOT/EXTREME)
- `flow.squeeze_risk`: Squeeze risk level (LOW/MED/HIGH)
- `macro_score`, `flow_score`, `structure_score`, `phase_score`, `options_score`, `mamis_score`, `mtf_score`: Individual gate scores
- `total_score`: Composite score used for alert threshold

## Important rules

- MAMIS and Wyckoff are **confirmations**, not entry triggers. They contribute via `mamis_score` (±2) and `phase_score` (±2) in the scorer.
- `B_BREAKOUT_RETEST` is vetoed — do not expect alerts from it.
- `E_RANGE_TRADE` SHORT is vetoed — only LONG range trades are allowed.
- `E_RANGE_TRADE` is vetoed in RISK_OFF regimes (ranges break in panic).
- XAG (Silver) is fully vetoed — price action is incompatible with the system.
- Metals get ×0.71 threshold scaling (effective ±4.6) to compensate for missing flow + options gates.
- Macro score is capped at ±1.5 with contrarian dampening at extremes (macro data lags).

1. Never present raw JSON to the user. Always synthesize into trader-friendly language.
2. Always state the invalidation level (stop) before targets.
3. If any data source failed, mention it and default to CAUTION.
4. Never override DSS vetoes. Report them with the reason.
