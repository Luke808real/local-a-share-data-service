# R4 SH/SZ STABLE MARKET FACTS — IMPLEMENTATION PLAN V01

PLAN_STATUS: AUTHOR_ONLY_PENDING_SOL_AUDIT
AS_OF: 2026-08-19 (data AS_OF 2026-08-17)
BASE: 4696df0ba74b086863d0c3fb090ab2740a3889f8
UPSTREAM_CNEQUITY: v0.7.2 @ a18ee0484dfb0801650175471724def3228b8a17
R3_BASIS: R3 SH/SZ MVP CLOSEOUT FROZEN (R3_SHSZ_DAILY_FOUNDATION = PASS)

Scope: SH/SZ only. BJ = DEFERRED_EXTENSION. No code, no real R4 execution,
no market-data write, no legacy promotion.

## 1. What pinned CNEquity v0.7.2 already provides

| Fact | Pinned capability | Existing dataset | Gap |
|---|---|---|---|
| preclose | TDX security-list `pre_close` (live snapshot); TDX `xdxr` -> corporate_actions (cash_dividend/bonus/transfer/allotment); curated daily_bars close + trading_calendar prev-day | daily_bars, trading_calendar, corporate_actions | no persisted historical exchange preclose; ex-date parity must be derived+validated |
| trading_status | step `trading_status` (EastMoney ST board + suspension report via TDX facade, snapshot); Baostock `st_history` per-day `isST` backfill (binary ST; explicit normal rows); suspension reconstruction from bar gaps | trading_status (L0) | statuses only {normal, suspended, st}; no STAR_ST / DELISTING split; no daily-status history before first live snapshot |
| turnover_rate | Baostock k-data `turn` (percent, back to 2016) used internally by valuation backfill; EM valuation is live-only | valuation_metrics (does NOT persist `turn`) | new dataset required to persist turnover_rate |
| high/low limit | none per-symbol; only `derive/market_breadth._limit_threshold` conservative breadth rule (9.5/19.5/29.5/4.5) | none | new thin rule engine required |
| is_limit_up/down | none | none | derived in assembly from preclose + limits + close |

Conclusion: trading_status is nearly drop-in (thin status-enum wrapper + bounded
historical backfill); preclose is a thin derivation over curated bars +
corporate_actions with TDX security-list crosscheck; turnover_rate needs one
new thin Baostock dataset; price-limit rules need a small versioned engine;
stable_market_facts is a derived assembly dataset. No new provider or
reconciliation system is required.

## 2. Fact-by-fact design

### 2.1 preclose (R4A)

- PRIMARY: exchange-style preclose derived from curated daily_bars close of the
  previous trading day (trading_calendar) adjusted by corporate_actions on
  ex-dates (TDX xdxr primary, EastMoney backup already pinned).
- CROSSCHECK: TDX security-list `pre_close` for current days (bounded sample
  parity, no persist).
- Coverage: 2016-01-01 .. 2026-08-17 (R3 window).
- AS_OF safety: derivation only uses dates <= AS_OF; first trading day of each
  symbol has no preclose (allowed only when no-limit rule applies).
- BLOCKER: ex-date parity has not been validated on this lake; gate on bounded
  sample parity (e.g., all ex-dates in window, preclose == adjusted prev close).

### 2.2 trading_status (R4B)

- REUSE: pinned `trading_status` step (EM snapshot) + Baostock ST history
  backfill (exact-scope, 2016+), suspension from bar gaps (volume=0/amount=0
  lake convention).
- THIN WRAPPER ONLY: map to R4 enum
  NORMAL / SUSPENDED / ST / STAR_ST / DELISTING / UNKNOWN.
  * normal, suspended, st -> direct from pinned rows.
  * STAR_ST -> only when positively evidenced (e.g., current snapshot name
    prefix `*ST` / ChiNext-ST rule); otherwise the historical split is
    NOT_PROVIDED (Baostock `isST` is binary) -> UNKNOWN.
  * DELISTING -> delisting-arrangement period is NOT provided by pinned feeds;
    R3 formal delisted map gives delist_date but not arrangement intervals.
- Historical ST coverage: run pinned Baostock `st_history` exact scope for the
  SH/SZ MVP universe (2016-01-01..2026-08-17) with the existing resumable
  checkpoint; no per-symbol network loops beyond the pinned sweep.
- BLOCKER: STAR_ST/DELISTING historical completeness is unprovable with pinned
  sources -> requires Sol decision: accept UNKNOWN (with ST umbrella) or add a
  bounded historical name/announcement feed.

### 2.3 turnover_rate (R4C)

- NEW dataset `turnover_rate` (curated, partition trade_date):
  symbol, trade_date, turnover_rate (percent, Baostock `turn`), amount,
  source="baostock", data_version.
- PRIMARY: Baostock k-data per-symbol sweep (same session/retry machinery as
  valuation backfill; 2016+).
- FALLBACK: none historical pinned; EastMoney valuation snapshot is live-only
  and used for current-day crosscheck only.
- LEGACY ROLE: legacy roots CROSSCHECK_ONLY, never canonical.
- Suspension semantics: turnover_rate NULL (not 0) on no-trade sessions.

### 2.4 price-limit engine (R4D)

- NEW thin rule engine over preclose + status + exchange/board + effective
  dates; rule table versioned + hashed. Official basis:
  reports/planning/R4_PRICE_LIMIT_RULE_AUTHORITY_RESEARCH_V01.md.
- High/low limit = round(preclose * (1 ± pct), 2) when a limit applies;
  NULL + reason on no-limit days (IPO first 5 days, delisting first day, etc.).
- is_limit_up = close >= high_limit - epsilon; is_limit_down similarly
  (bounded epsilon for rounding).
- Rule table (SH/SZ, window 2016..2026-08-17):
  * main board normal: 10%
  * main board ST/*ST: 5% until 2026-07-05; 10% from 2026-07-06 (rule change
    INSIDE the R3 window — must be versioned)
  * ChiNext 300xxx: 10% until 2020-08-21; 20% from 2020-08-24 (risk-warning
    included: 5% -> 20% at same date)
  * STAR 688xxx / STAR CDR 689xxx: 20% from 2019-07-22 (ST included)
  * ChiNext/STAR risk-warning (ST/*ST): 20%
  * delisting-arrangement: first day no limit; then main 10%, ChiNext/STAR 20%
  * IPO first 5 trading days: no limit (STAR/ChiNext from their launches; main
    board from 2023-04-10; pre-2023 main-board IPO first-day 44% handling is an
    edge to be validated separately)
  * BJ 30% recorded for completeness; BJ execution DEFERRED.

### 2.5 stable_market_facts assembly (R4E)

- NEW derived dataset `stable_market_facts` (partition trade_date):
  symbol, trade_date, preclose, prev_close, open, high, low, close, volume,
  amount, status, is_trading, turnover_rate, high_limit, low_limit,
  is_limit_up, is_limit_down, pct_chg, fact_version, source facts lineage.
- Assembled ONLY from curated inputs (daily_bars, trading_calendar,
  trading_status, corporate_actions, turnover_rate) + rule engine; no provider
  call, no market-data write outside the derived dataset.
- AS_OF safety: all inputs <= AS_OF; no future facts.

## 3. R4 stage order

1. R4A preclose derivation + ex-date parity gate
2. R4B trading_status enum enrichment + historical ST exact-scope backfill +
   suspension reconstruction verification
3. R4C turnover_rate dataset (Baostock thin wrapper + crosscheck)
4. R4D price-limit rule engine (versioned table + research doc)
5. R4E stable_market_facts assembly
6. R4F FACTS_READY verifier (read-only)

## 4. FACTS_READY formula

FACTS_READY =
  PRECLOSE_GATE
  AND TRADING_STATUS_GATE
  AND TURNOVER_GATE
  AND PRICE_LIMIT_GATE
  AND ASSEMBLY_GATE
  AND ASOF_SAFE
  AND R3_SHSZ_DAILY_FOUNDATION = PASS

- PRECLOSE_GATE: 100% required symbol x trading-day preclose (non-IPO days);
  ex-date parity sample PASS.
- TRADING_STATUS_GATE: every required symbol x trading-day has status in enum;
  UNKNOWN only in explicitly accepted buckets; ST backfill scope complete;
  suspension reconstruction verified.
- TURNOVER_GATE: turnover_rate rows == expected traded sessions; unresolved
  symbols == 0; current-day EM crosscheck sample within tolerance.
- PRICE_LIMIT_GATE: rule table version pinned + hash; high/low limit present
  for every limit-applicable day; no-limit days explicit NULL + reason;
  is_limit_up/down consistency sample PASS.
- ASSEMBLY_GATE: stable_market_facts rows == expected union of traded sessions;
  no dup/null PK; no invalid OHLC; no negative volume/amount; no post-ASOF.
- ASOF_SAFE: no fact uses data after 2026-08-17; rule effective dates correct.

## 5. Blockers and open questions

CONFIRMED_BLOCKERS:
1. STAR_ST historical split unprovable with pinned sources (Baostock isST
   binary; EM ST board current-only) — needs Sol decision on UNKNOWN acceptance
   or a bounded historical name feed.
2. DELISTING arrangement period not provided by pinned feeds — same decision
   space as #1.
3. turnover_rate dataset does not exist (Baostock `turn` not persisted).
4. Ex-date preclose parity unvalidated.
5. Price-limit rule engine absent; official basis now documented (see research
   doc), implementation is new thin code.
6. Historical suspension is bar-gap reconstruction, not a daily-status feed;
   needs explicit verification scope.

OPEN_QUESTIONS:
- Accept STAR_ST/DELISTING historical status as UNKNOWN, or add a bounded
  historical name/announcement source (provider decision for Sol).
- preclose parity sample design (all ex-dates vs sampled) and tolerance.
- turnover_rate unit/scope semantics (Baostock `turn` = percent of float
  shares) — verify against EM current snapshot.
- stable_market_facts layer: derived (proposed) vs curated.

## 6. Explicitly out of scope

No BJ extension, 5m, industry/index, Query Core/MCP, B1/B2/TradePlan,
full-market provider download, legacy promotion, real R4 execution.

