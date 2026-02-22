# Trade DSS — Tool Reference

## DSS CLI Commands

The Python DSS engine is installed at the project root. All commands output JSON to stdout (logs go to stderr).

### Full pipeline scan
```bash
dss scan --asset BTC --timeframe 4h
dss scan --asset all --timeframe 4h    # Scan all 6 assets
```

### Individual gates
```bash
dss macro-state                         # Cross-asset regime + sentiment
dss flow-state --asset BTC              # Flow & positioning (crypto)
dss structure --asset BTC --timeframe 1d  # Technical structure
```

### Specific analysis
```bash
dss zones --asset BTC --timeframe 1w    # S/R zones
dss stage --asset BTC                   # Wyckoff stage
dss score --asset BTC                   # Full score + vetoes (alias for scan)
```

### Backtesting
```bash
# Historical backtest through full DSS pipeline
dss backtest --asset BTC --start 2024-01-01 --end 2025-06-01 --timeframe 4h --output table
dss backtest --asset all --start 2024-01-01 --end 2025-06-01 --output json
dss backtest --asset BTC --start 2024-01-01 --end 2025-06-01 --risk-pct 1.0 --html report.html

# Backtest options:
#   --order-expiry N     Bars before pending order expires (default: 10)
#   --eval-interval N    Run pipeline every N bars (default: 1)
#   --warmup N           Warm-up bars for indicators (default: 120)
#   --risk-pct N         Percent of equity risked per trade (default: 1.0)
#   --partial-close N    Percent closed at T1 (default: 50.0)
#   --trail-atr N        ATR multiplier for trailing stop (default: 2.0)
#   --no-trail           Disable trailing stop
#   --html PATH          Generate HTML equity curve report

# Walk-forward validation (detect overfitting)
dss walk-forward --asset BTC --start 2023-01-01 --train 6 --test 3 --step 3

# View last backtest results
dss backtest-report --asset BTC --output table
dss backtest-report --output json
```

### Position management
```bash
dss position add --asset BTC --direction long --entry 98500 --stop 96200 --t1 104000 --t2 110000
dss position update --id 1 --trail-stop 99800
dss position list
dss position check                      # Check all vs current prices
dss position close --id 1 --reason "T1 hit"
```

### Data management
```bash
dss data refresh --asset BTC --timeframe 4h  # Force data refresh
dss data refresh --asset all --timeframe all # Refresh everything
dss data export --asset BTC --timeframe 1d   # Export to parquet
```

### System status
```bash
dss status                              # Full health check
```

## Scoring Quick Reference

The DSS uses an 8-gate composite scoring system:

| Gate | Range | Key Details |
|------|-------|-------------|
| Macro | ±1.5 (capped) | Dampened at extremes; macro data lags short-term moves |
| Flow | ±3 | Funding, OI, exchange flows, crowding/squeeze |
| Structure | ±3 | Trend alignment, zone quality, acceptance |
| Phase | ±2 | Stage/Wyckoff event alignment with setup type |
| Setup Quality | 0..+3 | R:R, zone strength, cleanliness |
| Options | ±2 | Options sentiment (crypto only; n/a for metals) |
| MAMIS | ±2 | MAMIS cycle phase confirmation |
| MTF | ±1 | Higher-timeframe SMA trend alignment |

**Thresholds:** Long ≥ +6.5 / Short ≤ −6.5 (crypto). Metals ×0.71 → ±4.6.

## Important Notes

- All commands output JSON. Parse the JSON for data, don't show raw JSON to the user.
- If a command fails, the error will be in the JSON under an "error" key.
- The DSS uses SQLite for state. Data persists across runs.
- Connector rate limits: crypto ~100ms between calls, yfinance ~1s.
- If connectors fail, default to CAUTION tradeability.
- Backtest uses: `max_consecutive_losses=2`, `cooldown_bars=12`, `max_open_positions=3`.
- MAMIS/Wyckoff are **confirmation layers** (via scorer), not standalone entry triggers.
- Currently active setups: A_PULLBACK, C_WYCKOFF_TRAP, E_RANGE_TRADE (LONG only).
- Vetoed: B_BREAKOUT_RETEST, E_RANGE_TRADE SHORT, XAG entirely.

## Web Search Patterns

For headline monitoring:
```
web_search "crypto market news today February 2026"
web_search "FOMC meeting schedule 2026"
web_search "US economic calendar this week"
web_search "bitcoin ETF flows today"
web_search "geopolitical risk markets today"
```

For macro calendar:
```
web_fetch "https://www.forexfactory.com/calendar"
```
