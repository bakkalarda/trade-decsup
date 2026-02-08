---
name: trade-dss
description: Multi-asset swing trading Decision Support System. Scan markets, detect setups (Pullback/Breakout/Wyckoff), score with macro+flow+structure gates, manage positions.
metadata: {"openclaw": {"always": true, "emoji": "📊"}}
---

# Trade DSS Skill

## What this does

This skill provides a complete swing trading decision support workflow across BTC, ETH, XRP, SOL, XAU, and XAG. It connects to a Python-based analysis engine that handles:

- Price data fetching (crypto via ccxt, metals via yfinance)
- Technical structure analysis (pivots, S/R zones, trend state, Wyckoff stages)
- Flow & positioning analysis (funding rates, OI, exchange flows)
- Macro regime classification (DXY, real yields, sentiment)
- Setup detection (3 patterns: Pullback, Breakout+Retest, Wyckoff Trap)
- Scoring (composite -14..+14, threshold ±7) with hard vetoes
- Position tracking with stop/target monitoring

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

### Manage positions
```bash
exec dss position list
exec dss position check
exec dss position add --asset BTC --direction long --entry 98500 --stop 96200 --t1 104000 --t2 110000
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
- `structure.stage`: Current Wyckoff stage (1-4)
- `structure.trend`: Trend direction (UP/DOWN/SIDEWAYS)
- `flow.crowding_state`: Positioning crowding (CLEAN/WARM/HOT/EXTREME)
- `flow.squeeze_risk`: Squeeze risk level (LOW/MED/HIGH)

## Important rules

1. Never present raw JSON to the user. Always synthesize into trader-friendly language.
2. Always state the invalidation level (stop) before targets.
3. If any data source failed, mention it and default to CAUTION.
4. Never override DSS vetoes. Report them with the reason.
