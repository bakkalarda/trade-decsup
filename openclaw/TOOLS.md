# Trade DSS — Tool Reference

## DSS CLI Commands

The Python DSS engine is installed at the project root. All commands output JSON to stdout.

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

## Important Notes

- All commands output JSON. Parse the JSON for data, don't show raw JSON to the user.
- If a command fails, the error will be in the JSON under an "error" key.
- The DSS uses SQLite for state. Data persists across runs.
- Connector rate limits: crypto ~100ms between calls, yfinance ~1s.
- If connectors fail, default to CAUTION tradeability.

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
