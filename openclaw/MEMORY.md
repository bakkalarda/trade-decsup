# Memory — Long-Term Knowledge

Persistent observations, lessons learned, and system performance history.

---

## System Performance (V8 — Backtest Benchmarks)

### Portfolio (2025-08-18 to 2026-02-18)
- **BTC:** +2.59% (10 trades)
- **ETH:** +13.92% (15 trades)
- **XAU:** +7.93% (9 trades)
- **XAG:** Vetoed (0 trades)
- **Portfolio total:** +24.43%

### Key Metrics
- Active setup types generating trades: A_PULLBACK, C_WYCKOFF_TRAP, E_RANGE_TRADE
- B_BREAKOUT_RETEST: vetoed (only produced losers)
- F_DIVERGENCE_REVERSAL, G_VOLUME_CLIMAX: available but produce 0 trades in backtest period

## Architecture Evolution

### V8 (Current)
- Removed D_MAMIS_TRANSITION as standalone entry
- MAMIS/Wyckoff now purely confirmation layers via scorer (mamis_score ±2, phase_score ±2)
- Added H_FIB_OTE to enum (disabled as standalone — causes displacement)
- Added `_fib_ote_confluence` helper for confirmation bonus
- Threshold restored to ±6.5 (experiments at 5.5/6.0 caused regressions)
- Cooldown restored to 12 bars (experiments at 6/8 caused regressions)

### V8 Lessons Learned
1. **Score perturbation cascades.** ANY change to scoring (quality bonuses, threshold, cooldown) displaces trades in a single-position-per-asset system. The V7.2b trade selection was near-optimal.
2. **MAMIS was already a confirmation.** The scorer's mamis_score (±2) and phase_score (±2) already served as the confirmation layer. Only D_MAMIS_TRANSITION was the violation.
3. **H_FIB_OTE displacement.** Adding H as standalone entry worked well for ETH (+11.21%) but caused massive BTC/XAU regression via displacement.
4. **Macro dampening is correct.** Macro score capped at ±1.5 with contrarian fade at extremes — macro data is lagging.
5. **Minimal changes win.** V8-final (only D removal) preserved +24.43% vs V7.2b's +24.71%.

## Asset-Specific Notes

### BTC
- Best setups: A_PULLBACK in Stage 2, C_WYCKOFF_TRAP springs
- Sensitive to trade displacement — one bad entry blocks better entries

### ETH
- Higher trade frequency than BTC (15 vs 10 trades)
- More responsive to C_WYCKOFF_TRAP patterns
- Fib OTE confluence works well but only as confirmation

### XAU (Gold)
- Metals threshold ×0.71 (effective ±4.6) compensates for missing flow + options
- E_RANGE_TRADE LONG generates good trades in Stage 1/3
- Macro regime less relevant — XAU has its own drivers (real yields, USD)

### XAG (Silver)
- **Permanently vetoed** — 12% win rate, PF 0.08 across all tested periods
- Silver's whipsaw price action defeats pullback detection
- Not worth revisiting without fundamental detection approach change

## Veto History
- B_BREAKOUT_RETEST: vetoed since V5, re-tested in V8 — still unprofitable
- E_RANGE_TRADE SHORT: vetoed since V6 — universally poor
- XAG: vetoed since V4 — incompatible price action
- D_MAMIS_TRANSITION: removed V8 — was generating low-quality standalone entries
