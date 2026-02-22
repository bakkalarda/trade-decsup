# Multi-Asset Swing Trading Decision Support System (DSS)
Automated **macro + structure + (crypto) on-chain + positioning** confirmation engine for: **BTC, ETH, XRP, SOL, XAU, XAG**.  
The DSS runs on a schedule, evaluates your rule-based strategy, and **alerts** when a trade candidate passes gates (and no veto triggers).

**Architecture version: V8 — OTE-based entries with MAMIS/Wyckoff confirmation layers.**

---

## 1) Goals and non-goals

### Goals
- Convert the discretionary playbook into a **repeatable, auditable** pipeline.
- Produce **actionable alerts** (not auto-trading) with:
  - direction (long/short),
  - setup type (A–H: Pullback / Breakout-Retest / Wyckoff Trap / Range Trade / Divergence / Volume Climax / Fib OTE),
  - entry zone + trigger conditions,
  - invalidation (stop logic),
  - targets,
  - confidence score + reasons (8-gate composite),
  - veto flags (why a candidate was rejected).
- Provide **historical backtesting** with equity curves, walk-forward validation, and overfitting detection.

### Non-goals
- No broker execution, no portfolio rebalancing, no “AI guessing”.
- No black-box ML required; the first version is **rules-first**.
- No promise of profitability; DSS is a decision-support tool, not a guarantee.

---

## 2) Strategy recap (what the DSS automates)

### 2.1 Gate model (8-gate composite scoring)
A trade candidate is scored across **eight gates** and must exceed the threshold with no vetoes:

1. **Macro Regime Gate** (±1.5, capped) — cross-asset regime, event risk, sentiment (dampened — macro lags)
2. **Flow & Positioning Gate** (±3) — funding, OI, exchange flows, crowding, squeeze risk
3. **Structure Gate** (±3) — trend alignment, zone quality, acceptance/rejection
4. **Phase Gate** (±2) — Wyckoff stage alignment with setup type
5. **Setup Quality** (0..+3) — R:R ratio, zone strength, cleanliness
6. **Options Gate** (±2) — options market sentiment (crypto only)
7. **MAMIS Gate** (±2) — MAMIS cycle phase confirmation (all setups)
8. **MTF Confluence** (±1) — multi-timeframe SMA trend alignment

### 2.2 Trade setups (the patterns the DSS looks for)
- **Setup A — Trend Pullback Continuation** (active)
- **Setup B — Breakout + Retest** (currently vetoed — unprofitable in backtest)
- **Setup C — Wyckoff Trap Reversal (Spring / UTAD)** (active)
- **Setup D — MAMIS Transition** (legacy — removed as standalone; MAMIS is now a confirmation layer)
- **Setup E — Range Trade** (active, LONG only)
- **Setup F — RSI Divergence Reversal** (available)
- **Setup G — Volume Climax Reversal** (available)
- **Setup H — Fibonacci OTE** (defined, disabled as standalone entry)

### 2.3 Scoring + vetoes
- Score gates (8 total):
  - Macro: ±1.5 (capped, dampened at extremes)
  - Flow/positioning: ±3
  - Structure: ±3
  - Phase/Wyckoff: ±2
  - Setup quality: 0..+3
  - Options: ±2 (crypto only)
  - MAMIS: ±2 (confirmation layer on all setups)
  - MTF: ±1 (higher-timeframe trend confluence)
- Alert threshold:
  - Long: **score ≥ +6.5** (crypto); **≥ +4.6** (metals, ×0.71 scaling)
  - Short: **score ≤ -6.5** (crypto); **≤ -4.6** (metals)
- **Vetoes** override score (hard NO), e.g.:
  - B_BREAKOUT_RETEST: fully vetoed (unprofitable in backtest)
  - E_RANGE_TRADE SHORT: vetoed (historically unprofitable)
  - E_RANGE_TRADE in RISK_OFF: vetoed (ranges break in panic)
  - XAG: fully vetoed (12% WR, PF 0.08 — incompatible with system)
  - BTC/ETH: large exchange-inflow risk at HTF resistance for longs.
  - Funding/OI extremes that imply squeeze risk (shorting support with deeply negative funding).
  - XAU/XAG: buying breakout with rising real yields + strengthening USD.
  - Counter-trend: shorts in confirmed uptrend, longs in confirmed downtrend (exempt: C_WYCKOFF_TRAP).
  - Stage misalignment: shorts in Stage 2 without UTAD, longs in Stage 4 without Spring.

---

## 3) System overview

### 3.1 High-level architecture
```
Scheduler (cron/systemd)
  -> Data Ingestion
  -> Feature Engineering
  -> Signal Engines (Macro, Flow, Structure, Psychology, Setups)
  -> Scoring & Veto Engine
  -> Alert Generator
  -> Storage (DB + logs) + Da

### 7.4 Psychology / Phase Gate (Wyckoff + Stage) — Professional

#### Objective
Ensure you trade the **right playbook** for the current campaign phase and avoid the common killer mistake: trading “continuations” in Stage 1/3 or trading “reversals” in Stage 2/4 without proof.

---

#### A) Stage model (deterministic)
- **Stage 1 (Base):** range, compression, mean reversion; breakouts require acceptance.
- **Stage 2 (Markup):** trend up; buy pullbacks; avoid countertrend shorts.
- **Stage 3 (Distribution):** failed breakouts; sell rallies with confirmation; reduce longs.
- **Stage 4 (Markdown):** trend down; short rallies; avoid bottom-fishing.

**Classifier inputs**
- HTF trend state (Weekly/Daily HH/HL vs LH/LL)
- range detection (compression)
- acceptance/rejection events
- volatility regime shift

Output: `stage ∈ {1,2,3,4}` with confidence score.

---

#### B) Wyckoff events used (only those that matter for swing)
- **Spring (bullish trap of bears):** sweep below range support → close back above → LPS retest holds.
- **UT/UTAD (bearish trap of bulls):** sweep above range resistance → close back inside → LPSY retest fails.
- **SOS/LPS (bull continuation):** strength + backing-up → buy the LPS retest.
- **SOW/LPSY (bear continuation):** weakness + last supply → short the LPSY retest.

Outputs:
- `wyckoff_event ∈ {SPRING, UTAD, LPS, LPSY, NONE}`
- `phase_alignment ∈ {ALIGNED, MISALIGNED}` for the candidate setup type.

---

#### C) Psychology vetoes
- Do not short Stage 2 unless a UTAD/distribution is confirmed and macro/positioning allows.
- Do not buy Stage 4 unless a Spring/capitulation-reclaim is confirmed and macro allows.


shboard (optional)
```

### 3.2 Components
- `connectors/` — fetches raw data (prices, macro series, on-chain, perps).
- `features/` — derives indicators and structural primitives (zones, pivots, trendlines).
- `signals/` — produces boolean/continuous signals for each gate.
- `rules/` — scoring + veto rules + setup detection logic.
- `alerts/` — formats messages (Telegram/Discord/email/CLI).
- `storage/` — SQLite/Postgres + parquet logs for backtests and audit.
- `ui/` (optional) — minimal dashboard to inspect why a signal fired.

---

## 4) Data requirements

### 4.1 Market data (all assets)
- OHLCV at: **Weekly, Daily, 4H** (recommended for swing).
- Optional: 1H for refinement, not required.

Assets:
- Crypto: BTC, ETH, XRP, SOL (spot + perp if traded)
- Metals: XAUUSD, XAGUSD (or futures proxies)

### 4.2 Macro data (XAU/XAG primary, crypto regime secondary)
- **DXY** (USD index)
- **Real yields** (e.g., 10y TIPS yield or a close proxy)
- Optional: equity index (SPX/NQ), vol (VIX) for risk appetite

### 4.3 On-chain (BTC/ETH primary)
Minimum viable:
- Exchange netflows (or exchange reserves trend)
- Large holder flows (optional)
- Profit-taking proxy (SOPR/realized P&L or simplified approximations)

> Note: On-chain depends on your data vendor. Design connectors so the system can swap providers.

### 4.4 Derivatives positioning (crypto)
- Funding rate
- Open interest
- Liquidations (optional but useful)

---

## 5) Timeframes and workflow (default)
- **Weekly:** macro regime + major HTF S/R zones
- **Daily:** stage classification + zone refinement + setup scanning
- **4H:** trigger confirmation (structure break/reclaim, retest holds)

Run cadence:
- 4H scan every 4 hours.
- Daily/weekly updates at fixed times (e.g., after daily close).

---

## 6) Feature engineering (what the DSS computes)

### 6.1 Price structure
- Swing pivots (e.g., fractal pivots with configurable lookback)
- Range detection (compression / sideways segments)
- Trend state (HH/HL vs LH/LL)
- Volatility regime (ATR, ATR% percentile)

### 6.2 Support/Resistance zones (rules-based)
- Build zones from repeated pivots and consolidation edges:
  - zone = [min(close cluster), max(close cluster)] with buffer
- Level strength score:
  - touches count, displacement magnitude, time separation, “chop penalty”.

### 6.3 Trendlines & channels (rules-based)
- Valid trendline requires:
  - 2 major pivots + 3rd reaction
- Channel = trendline + parallel through opposite pivot
- Store:
  - slope, intercept, touch count, residual error

### 6.4 Acceptance vs rejection
- Acceptance:
  - close beyond zone boundary + follow-through (N bars)
  - retest holds (wick allowed, closes respect)
- Rejection:
  - sweep beyond zone then close back inside
  - failure to hold after breakout

### 6.5 Stage (1–4) classifier (deterministic)
Inputs:
- HTF trend state + distance from key zones + volatility regime
Outputs:
- Stage 1: sideways/base
- Stage 2: uptrend/markup
- Stage 3: topping/distribution (failures near highs, weakening)
- Stage 4: downtrend/markdown

### 6.6 Flow & positioning features
Crypto:
- Exchange netflow z-score (spike detection)
- Reserve slope (rolling regression)
- Funding z-score
- OI z-score and OI impulse
Metals:
- DXY trend + z-score
- Real yield trend + z-score

---

## 7) Signal engines (gate logic)

### 7.1 Macro Regime Gate (Professional, Always‑On)

#### Objective
Continuously classify:
1) **Directional Regime**: `RISK_ON | MIXED | RISK_OFF`  
2) **Tradeability State**: `NORMAL | CAUTION | NO_TRADE`  

This gate is not only “macro bias.” It is primarily a **risk management and volatility avoidance layer**, designed to prevent initiating swing positions into predictable volatility clusters (scheduled macro events) or unbounded uncertainty (geopolitics/systemic news).

---

#### A) Scheduled Macro Event Engine (Calendar + Pre‑Event Risk Windows)

**What the agent tracks (minimum)**
- **Central bank:** FOMC rate decision + press conference; minutes; Fed speakers (high-impact)
- **Inflation:** CPI, PCE, Core CPI/PCE
- **Labor:** NFP, unemployment rate, jobless claims
- **Growth:** GDP (advance), ISM PMI, retail sales
- **Rates (optional):** major UST auctions (helps in tight-liquidity regimes)
- **Global (optional):** ECB/BoE decisions, major sanctions announcements, key summits

**Tiering (impact classes)**
- **Tier‑1:** FOMC rate decision + press conference; CPI; NFP  
- **Tier‑2:** PCE; GDP (advance); ISM PMI; retail sales  
- **Tier‑3:** jobless claims; sentiment surveys; medium-impact prints  

**Hard rules (survival mode)**
- **Tier‑1 `NO_TRADE` window for new swing entries**
  - From **T‑48h to T+6h** around the release time.
  - Exception: allow **post‑event acceptance** setups only (breakout + retest; or Wyckoff trap confirmation after the event).
- **Tier‑2 `CAUTION` window**
  - From **T‑24h to T+2h** if price is at HTF decision levels or positioning is crowded.

**Outputs**
- `event_risk_state ∈ {LOW, ELEVATED, HIGH}`
- `event_window ∈ {NONE, PRE, POST}`
- `event_veto` boolean (Tier‑1 window active and not a post‑event acceptance trade)

---

#### B) Always‑On Headline Engine (Geopolitics, Policy, Systemic Risk, Crypto Catalysts)

This is the “don’t get blindsided” module.

**Source protocol (mandatory)**
- Ingest headlines from multiple reputable sources (e.g., Reuters/WSJ/Bloomberg equivalents where available) and/or primary sources (company press releases).
- A headline becomes “actionable” only if:
  - it comes from a top‑tier source **or**
  - it is confirmed by **≥2 independent reputable sources**.
- Otherwise label it `UNCONFIRMED` and set `tradeability = CAUTION` (not NO_TRADE).

**Topic taxonomy**
Each item is classified as:
- `GEO` (war escalation, sanctions, state conflict: e.g., Iran–US risk)
- `CENTRAL_BANK` (Fed path surprises, emergency actions)
- `REGULATORY` (ETF approvals/denials, enforcement)
- `SYSTEMIC` (exchange outage/halts, stablecoin depeg, critical infra failure)
- `CRYPTO_CORPORATE` (MSTR earnings/capital actions, treasury buys/sells)
- `RISK_SENTIMENT` (broad risk shock)

**Impact mapping**
- `asset_impact ∈ {CRYPTO_BROAD, BTC_HEAVY, ETH_HEAVY, ALTS_HIGH_BETA, USD, REAL_YIELDS, XAU, XAG}`
- `directional_bias ∈ {RISK_ON, RISK_OFF, AMBIGUOUS}`
- `horizon ∈ {INTRADAY_SHOCK, MULTI_DAY, STRUCTURAL}`

**Severity scoring**
For each headline:
- `severity` 0–5 (tail risk + volatility potential)
- `credibility` 0–5 (source quality + confirmations)
- `market_reaction` 0–5 (optional: how much price/vol moved already)

Compute:
- `headline_risk_score = severity * credibility + market_reaction`

**Tradeability rules**
- If `headline_risk_score ≥ HIGH_THRESHOLD` → `tradeability = NO_TRADE`
- If `headline_risk_score ≥ MED_THRESHOLD` → `tradeability = CAUTION`

**Hard `NO_TRADE` triggers (examples)**
- Credible geopolitical escalation with unknown end-state (`GEO`, severity ≥4, credibility ≥4)
- Systemic crypto malfunction (major exchange halt; stablecoin depeg)
- Emergency central bank actions / surprise measures

---

#### C) Crypto-Specific Catalyst Calendar (MSTR, ETFs, Major Events)

Crypto has its own macro catalysts; ignoring them is unacceptable for a pro workflow.

**What the agent tracks**
- **MSTR (Strategy/MicroStrategy):** earnings dates, major corporate actions (issuance, large BTC buys/sells)
- **ETF-related items:** approvals/denials, major issuer announcements, flow-related headlines (if applicable)
- **Protocol/system upgrades:** major ETH upgrades, high-impact chain events
- **Large unlocks/emissions events:** especially for SOL/XRP-like assets when material

**Catalyst risk windows**
- Default: `CAUTION` from **T‑24h to T+2h**
- Escalate to `NO_TRADE` if:
  - catalyst overlaps Tier‑1 macro window, or
  - headline risk is already ELEVATED/HIGH, or
  - positioning is crowded at HTF decision levels.

---

#### D) Sentiment Engine (Fear/Greed + Risk Appetite Modifiers)

Sentiment is not a standalone entry signal. It is a **risk modifier**.

**Inputs**
- Crypto Fear & Greed index (or proxy)
- Volatility regime (ATR%, realized vol)
- Breadth proxies (optional): alt performance vs BTC; risk-on sector proxies

**Rules**
- `GREED_EXTREME` near HTF resistance → tighten long criteria, reduce size, prefer pullbacks.
- `FEAR_EXTREME` near HTF support + capitulation signatures → allow reversal setups **only with confirmation**.

**Outputs**
- `sentiment_state ∈ {RISK_SEEKING, NEUTRAL, RISK_AVERSE}`
- `sentiment_extreme ∈ {FEAR_EXTREME, GREED_EXTREME, NONE}`

---

#### E) Cross-Asset Macro Tape (USD, Real Yields, Risk Benchmarks)

**Core series**
- USD proxy (e.g., DXY)
- Real yields proxy (e.g., 10y TIPS yield)
- Optional: equity trend/volatility proxy (SPX/NQ, VIX)

**Interpretation**
- `RISK_OFF` confirmation tends to occur when USD strengthens and real yields rise (and/or equities weaken).
- For **XAU/XAG**, real yields are a primary driver—avoid buying breakouts into strongly rising real yields.

**Output**
- `cross_asset_state ∈ {CONFIRMS_RISK_ON, CONFIRMS_RISK_OFF, MIXED}`

---

#### F) Final Outputs (what the gate emits)

Emit **two outputs**, not one:

1) **Directional regime**
- `directional_regime ∈ {RISK_ON, MIXED, RISK_OFF}`

2) **Tradeability**
- `tradeability ∈ {NORMAL, CAUTION, NO_TRADE}`
  - `NO_TRADE` triggers on:
    - Tier‑1 macro window active (unless post‑event acceptance setup), OR
    - credible high-severity geopolitical/systemic headline risk, OR
    - unstable market microstructure (halts, depegs, infra issues)

Also emit:
- `size_multiplier`: 1.0 (NORMAL), 0.5 (CAUTION), 0.0 (NO_TRADE)
- structured diagnostics:
  - `active_events`: list of {name, tier, time_to_event}
  - `headline_flags`: list of {topic, severity, credibility, bias}
  - `sentiment_state`, `cross_asset_state`, `crypto_catalyst_state`

---

#### G) Integration with scoring and vetoes

**Macro score becomes composite**
- `macro_score = regime_score + cross_asset_modifier + sentiment_modifier - event_penalty - headline_penalty`

**Hard veto**
- If `tradeability == NO_TRADE`: veto *all new entries* (you can still manage existing positions).

**Post-event rule**
- After Tier‑1 events, only allow:
  - **Breakout + retest (acceptance)** or
  - **Wyckoff trap confirmation** (sweep + reclaim + retest)
before resuming normal swing entries.


### 7.2 Flow & Positioning Gate (Professional)

#### Objective
Detect **who is forced to transact** and whether **incremental supply/demand** supports your intended direction.  
For a pro swing book, this gate exists to:
- avoid buying into **distribution / inventory arriving**,
- avoid shorting into **squeeze conditions**,
- size risk according to **crowding and liquidation risk**,
- distinguish “price moved” vs “positioning moved”.

This gate outputs both a **directional confirmation** and a **crowding/liquidation risk score**.

---

#### A) Crypto: Two-tier model (Majors vs High-beta alts)

**Tier A (BTC/ETH):** on-chain + positioning have the highest signal value.  
**Tier B (SOL/XRP):** rely more on derivatives/spot tape; on-chain used as context only.

---

#### B) Inputs (by asset class)

##### B1) BTC/ETH (core)
**On-chain (preferred, vendor-dependent)**
- Exchange **netflows** and/or **reserves trend** (supply available to sell)
- Large-holder / whale transfer alerts (only entity-labeled)
- Profit-taking proxies (SOPR / realized P&L / spent output metrics)
- Optional: coin-age/dormancy style metrics (macro positioning)

**Market microstructure**
- Spot volume + volume delta (where available)
- Order-book liquidity (optional)
- Volatility (ATR%, realized vol)

**Derivatives positioning**
- Funding rate (level + z-score)
- Open interest (level + impulse + z-score)
- Liquidations (if available) and liquidation concentration around key levels

##### B2) SOL/XRP (high-beta)
- Funding + OI + liquidations (primary)
- Spot volume confirmation (primary)
- “Large transfer” alerts (secondary; noisy unless entity-labeled)

##### B3) Metals (XAU/XAG)
(No on-chain.) Use:
- USD + real yields (from Macro Gate)
- Volatility regime (ATR%, realized vol)
- Optional: positioning proxies (COT/ETF flows) if you track them

---

#### C) Professional signal definitions (what the agent computes)

##### C1) Flow pressure (BTC/ETH)
- `exch_inflow_z`: z-score of exchange inflows (spike detection)
- `exch_netflow_z`: z-score of netflows (directional)
- `reserve_slope`: rolling regression slope of exchange reserves

**Interpretation**
- **Long supportive:** no inflow spike near HTF resistance/breakout; reserves flat/down.
- **Short supportive:** inflows up or reserves up/flat near HTF resistance; distribution signs.

##### C2) Profit-taking / distribution pressure (BTC/ETH)
- `profit_taking_state ∈ {LOW, NORMAL, ELEVATED, EXTREME}`
- `distribution_flag`: repeated profit spikes + failure to progress at HTF resistance

**Interpretation**
- Avoid initiating new longs into **EXTREME** distribution *at* HTF resistance.
- For shorts, EXTREME profit-taking near HTF resistance increases asymmetry.

##### C3) Crowding & squeeze risk (all crypto)
- `funding_z`: normalized funding
- `oi_z`: normalized OI
- `oi_impulse`: change in OI over short window

**Squeeze risk heuristics**
- **Short squeeze risk (avoid fresh shorts):** funding very negative + OI elevated + price at/above support.
- **Long squeeze risk (avoid fresh longs):** funding very positive + OI elevated + price at/below resistance.

Output:
- `crowding_state ∈ {CLEAN, WARM, HOT, EXTREME}`
- `squeeze_risk ∈ {LOW, MED, HIGH}`

##### C4) Liquidity condition (optional but professional)
- `liquidity_state ∈ {GOOD, THIN, DISLOCATED}`
Indicators:
- spread widening, sudden vol expansion, poor follow-through on breaks

---

#### D) Gate decisions (confirmations + vetoes)

##### D1) Directional confirmation
- `flow_bias ∈ {BULLISH, NEUTRAL, BEARISH}`

Examples:
- **BTC long confirmation:** no inflow spike + reserves down/flat + crowding not EXTREME.
- **BTC short confirmation:** inflows elevated or distribution flag + crowding not EXTREME negative at support.

##### D2) Hard vetoes (survival rules)
- **Long veto (BTC/ETH):**
  - exchange inflow spike **AND** price at HTF resistance/breakout level
  - crowding EXTREME (funding positive + OI high) **AND** buying resistance
- **Short veto (any crypto):**
  - crowding EXTREME (funding negative + OI high) **AND** shorting support
- **Liquidity veto:**
  - `liquidity_state == DISLOCATED` (halts, extreme spreads, unstable venues)

##### D3) Sizing modifiers
- `positioning_size_multiplier`:
  - CLEAN: 1.0
  - WARM: 0.7
  - HOT: 0.5
  - EXTREME: 0.3 (or veto depending on location)

---

#### E) Outputs (what gets logged)
- `flow_bias`
- `profit_taking_state`, `distribution_flag`
- `crowding_state`, `squeeze_risk`
- `liquidity_state`
- veto flags + sizing multiplier



## 8) Setup Detection and Execution Templates (Professional Swing)

The DSS detects eight setup families (A–H). Each setup outputs a **trade plan** (trigger, stop, targets, and management rules).  
The agent must *not* invent new patterns; it should enforce discipline.

**V8 Architecture Note:** MAMIS cycle phase and Wyckoff events are **confirmation layers** that boost quality on ALL OTE-based entries via the scorer (`mamis_score` ±2, `phase_score` ±2). They are not standalone entry triggers. D_MAMIS_TRANSITION has been removed as a standalone entry; Fibonacci OTE (`_fib_ote_confluence`) checks 61.8%–78.6% golden pocket retracement as an additional quality bonus.

**Currently active:** A_PULLBACK, C_WYCKOFF_TRAP, E_RANGE_TRADE (LONG only).
**Vetoed:** B_BREAKOUT_RETEST (unprofitable), E_RANGE_TRADE SHORT, XAG entirely.
**Available:** F_DIVERGENCE_REVERSAL, G_VOLUME_CLIMAX.
**Defined but disabled:** H_FIB_OTE (causes displacement in single-position-per-asset system).

---

### 8.1 Setup A — Trend Pullback Continuation (Primary)

**When to look**
- Stage 2 (longs) or Stage 4 (shorts)
- Macro tradeability not `NO_TRADE`
- Flow/positioning not in veto condition

**Location rules (must-have)**
- Pullback into a **strong HTF zone** and/or **channel boundary**
- Volatility contracts during pullback (ATR% down) and expands on resumption

**Trigger rules (4H)**
- Break of pullback micro-structure **and** reclaim/hold of the zone (close-based)
- Optional: acceptance on retest (best entries are retests, not first breaks)

**Stops**
- Structure invalidation:
  - Long: below pullback swing low (or below zone + buffer)
  - Short: above rally swing high (or above zone + buffer)

**Targets**
- T1: next HTF zone (first opposing decision level)
- T2: next Weekly zone or channel opposite boundary
- Trail: after T1, trail by 4H structure or channel midline

**Professional notes**
- Do not buy pullbacks when exchange inflow spikes at resistance (BTC/ETH).
- Reduce size when crowding is HOT (funding/OI).

---

### 8.2 Setup B — Breakout + Retest (Acceptance Only)

**When to look**
- Late Stage 1 → Stage 2 transition (long) or Stage 3 → Stage 4 (short)
- Post-event acceptance is preferred after Tier‑1 macro events

**Breakout requirements**
- Break of HTF range boundary with **acceptance**:
  - at least one close beyond boundary and follow-through
- Retest must **hold** (long) or **fail** (short)

**Trigger**
- Enter on retest confirmation (4H close), not on the initial spike.

**Stops**
- Long: below retest low / back inside range with acceptance failure
- Short: above retest high / back inside range with acceptance failure

**Targets**
- Measured move or next Weekly zone; partial at first HTF objective.

**Professional notes**
- BTC/ETH long breakout is vetoed if exchange inflow spike arrives into the breakout.
- Avoid breakouts in thin liquidity (`liquidity_state=THIN`) unless exceptionally clean.

---

### 8.3 Setup C — Wyckoff Trap Reversal (Spring / UTAD)

**Spring (Long)**
- Sweep below range support (or below key trendline) then close back above (rejection)
- Retest (LPS) holds inside range → trigger

**UTAD (Short)**
- Sweep above range resistance then close back inside (rejection)
- Retest (LPSY) fails → trigger

**Stops**
- Long: below the spring low (or below LPS failure)
- Short: above the UTAD high (or above LPSY failure)

**Targets**
- First: range midpoint / opposing zone
- Second: range high/low or next HTF zone
- Trail if price transitions to Stage 2/4.

**Professional notes**
- Traps are best when crowding is extreme (because the trap has fuel), but only if your entry is confirmed by reclaim/failure and macro tradeability is not compromised.

---

### 8.4 Setup D — MAMIS Transition (Legacy — Removed as Standalone)

**V8 Status:** D_MAMIS_TRANSITION has been **removed as a standalone entry**. MAMIS cycle phase is now purely a confirmation layer that enriches all OTE-based entries via the scorer's `mamis_score` (±2). This prevents MAMIS from generating low-quality standalone trades while preserving its value as a quality signal.

---

### 8.5 Setup E — Range Trade (LONG Only)

**When to look**
- Stage 1 (accumulation) or Stage 3 (distribution) — sideways phases
- Defined range with clear support/resistance boundaries
- Macro tradeability not `NO_TRADE`

**Location rules (must-have)**
- Price at range support (for LONG) with at least 2 touches
- Range must be established (minimum 3 tests of boundaries)

**Trigger rules (4H)**
- Rejection candle at range support + hold above support
- Volume confirmation preferred

**Stops**
- Below range support with ATR buffer

**Targets**
- T1: range midpoint
- T2: range resistance

**Professional notes**
- SHORT range trades are vetoed (historically unprofitable).
- E_RANGE_TRADE is vetoed in RISK_OFF regimes (ranges break in panic).
- Range trades want sideways structure — `structure_score` rewards `SIDEWAYS` trend for E setups.

---

### 8.6 Setup F — RSI Divergence Reversal

**When to look**
- Late Stage 4 / early Stage 1 (for longs) — markdown exhaustion
- Late Stage 2 / early Stage 3 (for shorts) — markup exhaustion
- Price at key S/R zone with RSI divergence

**Trigger rules**
- Bullish divergence: price makes lower low, RSI makes higher low
- Bearish divergence: price makes higher high, RSI makes lower high
- Must occur at a significant S/R zone

**Professional notes**
- Divergence alone is not enough — requires zone + phase alignment.
- Best in exhausted trends where the divergence signals loss of momentum.

---

### 8.7 Setup G — Volume Climax Reversal

**When to look**
- Capitulation events (Stage 4 for longs) or blow-off tops (Stage 2 for shorts)
- Extreme volume spike (>2σ above average) at key zone

**Trigger rules**
- Volume spike with rejection candle at S/R
- Subsequent bar must confirm (close in direction of reversal)

**Professional notes**
- Volume climax without zone confluence is noise.
- Best when combined with extreme crowding (squeeze fuel).

---

### 8.8 Setup H — Fibonacci OTE (Defined, Disabled as Standalone)

**V8 Status:** H_FIB_OTE is defined in the enum with full detection logic but **disabled as a standalone entry**. Backtesting showed it causes trade displacement in a single-position-per-asset system (displaces profitable A/C/E trades). It is retained as a confirmation bonus via `_fib_ote_confluence` on other setups.

**When to look (if re-enabled)**
- Stage 2 (longs) or Stage 4 (shorts) — clearly trending phases
- Price retraces to 61.8%–78.6% fibonacci golden pocket of the prior swing

**Trigger rules**
- Enter on rejection from the OTE zone
- Swing must be >4 ATR in range
- Price within ±0.5 ATR of the 61.8%–78.6% zone



## 9) Scoring, Vetoes, and Risk-Adjusted Decision (Professional)

### 9.1 Decision policy (survival first)
The DSS uses **vetoes** as hard risk brakes and **scoring** as soft alignment.

**Hard rule**
- If `tradeability == NO_TRADE` → veto all new entries (manage existing only).

---

### 9.2 Score model (V8 — 8-gate composite)
Compute:

- `macro_score` (from Macro Gate): -1.5..+1.5 (capped, dampened at extremes)
- `flow_score` (from Flow/Positioning): -3..+3
- `structure_score` (from Structure + Acceptance): -3..+3
- `phase_score` (Stage/Wyckoff alignment): -2..+2
- `setup_score` (quality + R:R + cleanliness): 0..+3
- `options_score` (Options sentiment, crypto only): -2..+2
- `mamis_score` (MAMIS cycle phase confirmation): -2..+2
- `mtf_score` (Multi-timeframe trend confluence): -1..+1

Total:
- `total_score = macro_score + flow_score + structure_score + phase_score + setup_score + options_score + mamis_score + mtf_score`

**Alert thresholds**
- Long candidate: `total_score ≥ +6.5` and no veto (crypto)
- Short candidate: `total_score ≤ -6.5` and no veto (crypto)
- Metals: thresholds scaled by ×0.71 → effective ±4.6 (compensates for missing flow + options gates)

> Pro note: Macro score is capped at ±1.5 because macro data is lagging and inversely correlated with short-term crypto moves. Extreme readings (|raw| > 2.0) are faded by 40%.

---

### 9.3 Professional veto catalogue (must-pass)
**Macro vetoes** (from Macro Gate)
- Tier-1 event window active (unless post-event acceptance setup)
- High headline risk (geo/systemic/regulatory) above threshold

**Flow/positioning vetoes**
- Buying resistance with extreme positive funding + high OI (crowded long)
- Shorting support with extreme negative funding + high OI (squeeze risk)
- BTC/ETH: exchange inflow spike into HTF resistance/breakout for longs
- Liquidity dislocation (halts, abnormal spreads)

**Structure vetoes**
- Middle-of-range trades without acceptance/rejection event
- First-break trades without retest (unless explicitly allowed in config)
- Shorts in Stage 2 without UTAD/distribution confirmation
- Longs in Stage 4 without Spring/capitulation confirmation

**Counter-trend vetoes**
- Shorts in confirmed uptrend (trend UP, confidence ≥60%) — exempt: C_WYCKOFF_TRAP
- Longs in confirmed downtrend (trend DOWN, confidence ≥70%) — exempt: C_WYCKOFF_TRAP

**Regime/setup vetoes (V8)**
- B_BREAKOUT_RETEST: fully vetoed (backtest unprofitable)
- D_MAMIS_TRANSITION: removed as standalone (MAMIS is confirmation only)
- E_RANGE_TRADE SHORT: vetoed (historically unprofitable)
- E_RANGE_TRADE in RISK_OFF: vetoed (ranges break in high-volatility regimes)
- XAG (Silver): fully vetoed (12% WR, PF 0.08 — incompatible with system)

---

### 9.4 Risk-adjusted sizing output
The DSS outputs:
- `size_multiplier = macro_size_multiplier * positioning_size_multiplier`
- default:
  - NORMAL: 1.0
  - CAUTION: 0.5
  - NO_TRADE: 0.0
- plus optional volatility scaling:
  - if ATR% percentile is high → reduce size further

---

### 9.5 “Rejected candidate” diagnostics
For every near-miss, log:
- which veto fired, or
- which gate failed (and by how much),
so you can refine thresholds without hindsight bias.
