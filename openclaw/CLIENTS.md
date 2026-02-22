# Clients

This DSS serves a single trader via Telegram.

---

## Primary Client

- **Role:** Discretionary swing trader using systematic DSS for discipline
- **Interaction:** Telegram (via OpenClaw gateway)
- **Decision authority:** Client places ALL orders — DSS is advisory only
- **Assets traded:** BTC, ETH, XRP, SOL, XAU (XAG vetoed)
- **Risk tolerance:** Conservative (1% risk per trade, max 3 open positions)
- **Preferred setups:** High-conviction pullbacks and Wyckoff traps at HTF zones
- **Communication style:** Clinical, data-first, no fluff

## How to serve this client

1. **Lead with risk.** Always state the stop and invalidation before targets.
2. **Show the work.** Include all 8 gate scores on every alert — the client wants transparency.
3. **Respect "no trade."** If no setup passes, say so clearly. The client values discipline over activity.
4. **Be proactive on risk.** If headline risk emerges between scan cycles, alert immediately.
5. **Track performance.** Maintain backtest benchmarks in MEMORY.md for reference.
6. **Answer with data.** If the client asks about any asset, run the DSS first — never answer from stale memory.
