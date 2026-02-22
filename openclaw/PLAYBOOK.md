# Playbook — Decision Frameworks

Core decision frameworks for the Trade DSS agent. Reference these when making any trading-related decision.

---

## Framework 1: Setup Evaluation Pipeline

Every setup candidate goes through this exact sequence:

```
1. Is macro tradeability NO_TRADE?
   → YES: Veto all entries (manage existing only)
   → NO: Continue

2. Is there an active event window (Tier-1)?
   → YES + setup is NOT C_WYCKOFF_TRAP or B_BREAKOUT_RETEST post-event: Veto
   → NO: Continue

3. Does the setup pass hard vetoes?
   → Check: asset veto (XAG), setup veto (B, E SHORT, E in RISK_OFF),
            flow vetoes, structure vetoes, counter-trend vetoes
   → ANY veto fires: Reject with reason
   → NO vetoes: Continue

4. Does total_score meet threshold?
   → Crypto: total ≥ +6.5 (long) or ≤ -6.5 (short)
   → Metals: total ≥ +4.6 (long) or ≤ -4.6 (short)
   → YES: ALERT — send full trade plan
   → NO: Log as rejected candidate with gap-to-threshold
```

## Framework 2: 8-Gate Score Interpretation

| Gate | What it tells you | Red flag |
|------|-------------------|----------|
| Macro (±1.5) | Is the environment supportive? | Negative in both directions = confused regime |
| Flow (±3) | Who is positioned where? | EXTREME crowding at your entry level |
| Structure (±3) | Is trend + zone aligned? | Counter-trend without Wyckoff confirmation |
| Phase (±2) | Right playbook for this stage? | Pullback in Stage 4, Range trade in Stage 2 |
| Quality (0..+3) | How clean is the setup? | Score < 1.0 = marginal setup |
| Options (±2) | What does options flow say? | Strong opposing signal |
| MAMIS (±2) | Cycle phase confirmation | Strongly against entry direction |
| MTF (±1) | Does HTF agree? | HTF opposing = reduced conviction |

## Framework 3: Position Management Decision Tree

```
Position open:
├── Stop hit? → Close, log, notify immediately
├── T1 hit? → Partial close (50%), move stop to breakeven, notify
├── T2 hit? → Close remainder, notify
├── Trailing?
│   ├── Trail by 4H swing structure (below last HL for longs)
│   └── If trail stop hit → close, log, notify
└── None of the above → Report current P&L in daily summary
```

## Framework 4: Headline Risk Assessment

```
Headline received:
├── Source quality?
│   ├── Top-tier (Reuters/Bloomberg/WSJ/official) → Actionable
│   ├── Confirmed by ≥2 sources → Actionable
│   └── Single non-tier source → UNCONFIRMED, set CAUTION
├── severity × credibility ≥ 20 → NO_TRADE
├── severity × credibility ≥ 12 → CAUTION
└── severity × credibility < 12 → LOW, continue normally
```

## Framework 5: When to Run Backtests

Run backtests when:
- User asks about strategy performance or a specific asset's history
- A parameter change is proposed (validate before applying)
- Walk-forward validation is needed to check for overfitting
- After any code change to the engine (regression check)
- Weekly review to confirm system is still performing as expected

**Critical rule:** ANY change to scoring (threshold, bonuses, cooldown) must be validated against the full portfolio (BTC + ETH + XAU minimum) before going live. Single-asset improvements that degrade portfolio performance are rejected.

## Framework 6: Scan Cycle Priority

When running a scan cycle, prioritize in this order:

1. **Safety first** — Check headline risk, event calendar, macro tradeability
2. **Existing positions** — Check stops/targets on open positions
3. **New opportunities** — Run full scan pipeline
4. **Diagnostics** — Log rejected candidates and near-misses
5. **Memory** — Write scan summary to daily memory

Never skip step 1 to get to step 3 faster.

## Framework 7: What NOT to Do

- Do NOT lower the threshold to generate more alerts
- Do NOT override vetoes for "obvious" trades
- Do NOT add MAMIS/Wyckoff as standalone entry triggers
- Do NOT test single-asset changes without full portfolio validation
- Do NOT use breakout entries without acceptance confirmation
- Do NOT trade XAG under any circumstances
- Do NOT provide financial advice — you are a decision-support tool
