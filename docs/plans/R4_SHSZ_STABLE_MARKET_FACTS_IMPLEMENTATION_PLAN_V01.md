# R4 SH/SZ STABLE MARKET FACTS — IMPLEMENTATION PLAN V01.2

PLAN_STATUS: AUTHOR_ONLY_PENDING_SOL_REAUDIT
VERSION: V01.2 (plan-contract closure of Sol V01.1 re-audit findings)
AS_OF: 2026-08-19 (data AS_OF 2026-08-17)
BASE: e9e164d593cae1f31b82454c1f6a0debfe84b6c4 (V01.1; V01 base = 1b45fa791a436763e31023e23a6751233043b0e8)
UPSTREAM_CNEQUITY: v0.7.2 @ a18ee0484dfb0801650175471724def3228b8a17
R3_BASIS: R3 SH/SZ MVP CLOSEOUT FROZEN (R3_SHSZ_DAILY_FOUNDATION = PASS)

Scope: SH/SZ only. BJ = DEFERRED_EXTENSION (no BJ research this round).
No code, no real R4 execution, no market-data write, no legacy promotion.

## 1. Source policy (frozen in V01.1)

Price-limit and trading-rule authority is ONLY:

- sse.com.cn
- szse.cn
- csrc.gov.cn

All secondary-source authorities are removed. BSE is out of scope because BJ
is DEFERRED_EXTENSION. Official-website rule research is permitted in later
R4 stages; market-provider data fetch is not.

## 2. What pinned CNEquity v0.7.2 already provides

| Fact | Pinned capability | Existing dataset | Gap |
|---|---|---|---|
| preclose | TDX security-list `pre_close` (live snapshot); TDX `xdxr` -> corporate_actions; curated daily_bars close + trading_calendar | daily_bars, trading_calendar, corporate_actions | no persisted historical exchange display preclose; ex-date parity unvalidated |
| trading_status | pinned step + `derive/trading_status_history.py` (suspension reconstruction from trading gaps); EastMoney ST board snapshot; Baostock `st_history` per-day binary isST backfill | trading_status (L0) | enum needs R4 wrapper; STAR_ST split NOT_PROVIDED |
| turnover_rate | Baostock k-data `turn` (percent, back to 2016) | valuation_metrics (does NOT persist `turn`) | new thin dataset required |
| high/low limit | none per-symbol; only `market_breadth._limit_threshold` breadth rule | none | new versioned rule engine required |
| is_limit_up/down | none | none | derived in assembly |

Conclusion: trading_status is a thin enum wrapper over pinned evidence;
preclose is a thin derivation over curated bars + corporate_actions with TDX
crosscheck; turnover_rate needs one thin Baostock dataset; price-limit rules
need a small versioned engine; stable_market_facts is a derived assembly.
No new provider or reconciliation system is required.

## 3. Preclose contract (R4A)

PRECLOSE_SEMANTIC = EXCHANGE_DISPLAY_PRECLOSE

The preclose value is the exchange-display reference price for the day, defined
per case:

| Case | Preclose definition |
|---|---|
| NORMAL | previous effective symbol close (the symbol's own previous effective
  close, NOT the previous MARKET trading-day row of another/placeholder source) |
| IPO_FIRST_LISTING_DAY | issue price |
| EX_RIGHT_EX_DIVIDEND_DAY | exchange ex-right/ex-dividend reference price
  (derived from previous effective close + corporate_actions on ex-date) |
| SPECIAL_RELISTING_OR_RESUMPTION | exchange-defined reference if an
  authoritative rule/announcement exists; otherwise UNKNOWN + reason |

Freezing:

- NO_LIMIT_DAY != NULL_PRECLOSE. A day with no price limit still has an
  exchange display preclose (issue price / reference price). NULL preclose is
  allowed ONLY when the exchange display preclose itself is not authoritative
  (SPECIAL_RELISTING_OR_RESUMPTION without authoritative reference) and is
  then persisted with an explicit reason.
- "First trading day has no preclose" is deleted. The first trading day has
  the issue price as preclose.

### 3.1 R4A0_CORPORATE_ACTION_AVAILABILITY_GATE (new)

The R3 PROJECT_STATE/real root does NOT prove corporate_actions is built.
Before any preclose derivation, R4A0 must verify, read-only:

- corporate_actions dataset exists
- schema valid
- SH/SZ scope only
- coverage 2016-01-01 .. 2026-08-17
- ex-date uniqueness per symbol (no duplicate ex-date/action-key)
- provider provenance captured

If any check fails: run a bounded bootstrap/backfill of corporate_actions
FIRST. Preclose derivation must not start on missing/unverified actions.

## 4. Trading status & suspension contract (R4B)

### 4.1 Suspension semantics

"volume==0 / amount==0 => suspended" is DELETED as a standalone rule.
Strictly reuse pinned `cnequity/derive/trading_status_history.py`:

- a listed symbol within its effective lifetime
- on a market trading day
- with no *traded* bar

is suspended evidence. A volume=0 placeholder row is only treated as part of
the no-traded-bar handling, never as independent suspension authority.
Evidence precedence follows the pinned rank semantics.

### 4.2 R4 enum

Thin wrapper mapping only:

- NORMAL
- SUSPENDED
- ST (risk-warning, subtype ST or unknown)
- STAR_ST
- DELISTING
- UNKNOWN

### 4.3 is_risk_warning / risk_warning_subtype

No new large-scale provider.

- is_risk_warning: TRUE / FALSE / UNKNOWN
- risk_warning_subtype: ST / STAR_ST / UNKNOWN

Baostock binary isST:

- isST=1 -> is_risk_warning=TRUE
- isST cannot prove subtype -> risk_warning_subtype=UNKNOWN
- known risk-warning is NEVER downgraded to NORMAL/FALSE

Price-limit rules key on is_risk_warning + board, not on ST vs *ST subtype,
unless a specific historical rule distinguishes them.

## 5. Turnover contract (R4C)

New curated dataset `turnover_rate` (partition trade_date). Required fields:

- symbol
- trade_date
- turnover_rate
- turnover_source
- turnover_semantic
- coverage_status
- source
- data_version
- fetched_at

Proven facts:

- Baostock `turn` unit = percent (PROVEN)
- TURNOVER_SEMANTIC =
  PROVIDER_REPORTED_PERCENT_FLOAT_BASED_UNVERIFIED_DENOMINATOR
  (frozen until a bounded validation pins the denominator; do not write an
  unvalidated precise free-float contract)

Missing semantics: missing != 0. Suspended/no-trade sessions have
turnover_rate NULL (not 0). FALLBACK: none historical pinned; EastMoney
valuation snapshot is live-only current-day crosscheck. LEGACY ROLE:
CROSSCHECK_ONLY, never canonical.

## 6. Price-limit engine (R4D)

### 6.1 Rounding (replaces round(...,2) + epsilon)

- Decimal arithmetic
- ROUND_HALF_UP
- exchange minimum tick for SH/SZ A-shares = 0.01 CNY (exchange trading rules;
  exact article re-pinned at R4D verification)
- minimum-one-tick rule: if a computed limit rounds to the preclose itself,
  the limit is preclose +/- 1 tick
- minimum-price-floor rule: limit prices are floored/ceiled per exchange
  absolute-price rules (>= tick magnitude; par-value floor rule pinned at R4D)

### 6.2 PRICE_LIMIT vs VALID_ORDER_PRICE_RANGE

Two distinct concepts:

- PRICE_LIMIT: per-day limit for close-vs-preclose (10/20/5/30% etc.)
- VALID_ORDER_PRICE_RANGE: the prices an order may be submitted at

On IPO first days / relisting first days / delisting-arrangement first days
without a price limit:

- limit_applicable = false
- high_limit = NULL
- low_limit = NULL
- no_limit_reason = <enum>

The historical main-board IPO valid order range 64%..144% of issue price is a
VALID_ORDER_PRICE_RANGE fact. It is NEVER written as high_limit/low_limit
(which would corrupt limit semantics).

### 6.3 Pre-2023 main-board IPO order range

SZ (frozen): SZSE official 2014 rule — 深证会〔2014〕54号 (Sol-verified
authority): full-day valid order range 64%..144% of issue price, rounded
half-up to tick. PRICE_LIMIT = NONE.
URL: https://www.szse.cn/www/disclosure/notice/company/t20140613_508770.html

SH: bounded SSE official-source research required. If it cannot be closed with
an SSE official source, keep:

SH_PRE_2023_IPO_ORDER_RANGE_OPEN

SH_PRE_2023_IPO_ORDER_RANGE_OPEN is an OPEN_NONBLOCKING_EDGE, not a hard
blocker. It does not block PRICE_LIMIT_COMPLETE: for those first listing days
the rule engine still emits a fully resolved PRICE_LIMIT = NONE row
(limit_applicable=false, high_limit=NULL, low_limit=NULL,
no_limit_reason=<enum>), so they contribute zero
unresolved_required_price_limit_rows. The edge affects only the future
VALID_ORDER_PRICE_RANGE facts and must not be silently assumed to equal the SZ
64%..144% range without an SSE official source.

### 6.4 Rule table (SH/SZ, window 2016-01-01..2026-08-17)

| Segment | Period | Limit | Authority |
|---|---|---|---|
| SH/SZ main board normal | full window | 10% | exchange trading rules |
| SH/SZ main board risk-warning | until 2026-07-05 | 5% | SSE/SZSE pre-2026 rules |
| SH/SZ main board risk-warning | from 2026-07-06 | 10% | SSE 上证发〔2026〕41号 / SZSE 深证上〔2026〕551号 (effective 2026-07-06; Sol-verified) |
| ChiNext 300xxx normal | until 2020-08-21 | 10% | pre-reform SZSE rules |
| ChiNext 300xxx normal | from 2020-08-24 | 20% | SZSE ChiNext Special Provisions (2020-08-24) |
| ChiNext risk-warning | until 2020-08-21 | 5% | pre-reform SZSE risk-warning rule |
| ChiNext risk-warning | from 2020-08-24 | 20% | SZSE 深证上〔2020〕620号 implementation |
| STAR 688xxx | from 2019-07-22 | 20% | SSE STAR rules / investor education |
| STAR CDR 689xxx | from 2019-07-22 | 20% | SSE STAR rules (CDR in board scope) |
| STAR/ChiNext delisting-arrangement | after first day | 20% | SSE/SZSE rules |
| Main board delisting-arrangement | after first day | 10% | SSE/SZSE rules |
| IPO first 5 trading days (STAR/ChiNext) | from board launch | NO LIMIT | SSE/SZSE rules |
| IPO first 5 trading days (main board) | from 2023-04-10 | NO LIMIT | CSRC full registration |
| Main board IPO first day (pre-2023) | until 2023-04-07 | PRICE_LIMIT NONE; order range edge per 6.3 | SZ frozen (深证会〔2014〕54号, Sol-verified) / SH open (OPEN_NONBLOCKING_EDGE) |

### 6.5 Delisting-arrangement interval

DELISTING_ARRANGEMENT_INTERVAL = CONFIRMED_BLOCKER (hard blocker).

- Never back-derive arrangement start from delist_date.
- If the interval is not authoritatively resolved, the affected historical
  price-limit rows carry coverage_status = UNKNOWN/PARTIAL. coverage_status
  is a COVERAGE label only: it means the row is NOT resolved, and it can
  NEVER be interpreted as satisfying PRICE_LIMIT_COMPLETE.
- For any affected rows: unresolved_required_price_limit_rows > 0 →
  PRICE_LIMIT_COMPLETE = false → FACTS_READY = false.
- The plan must later run bounded authority discovery to resolve the interval.
  Until then the affected rows keep PRICE_LIMIT_COMPLETE = false. No guessing,
  no PARTIAL-to-READY promotion (FACTS_READY stays boolean true/false).

## 7. Row universe (R4E)

Explicit scopes:

- TRADING_STATUS required grid = symbol active lifetime x market trading days
  (each listed symbol-day must have a status row)
- TURNOVER required rows = actual traded sessions only
- STABLE_MARKET_FACTS row scope = actual traded sessions (frozen default):
  OHLCV/pct/limits are only defined on traded sessions; suspended-day facts
  (status/is_trading) are queried from the trading_status dataset.
  If a later Sol decision instead selects an active-day grid, OHLCV absence
  semantics (NULL vs omitted row) must be explicitly defined first.

"expected union of traded sessions" (ambiguous) is removed.

## 8. R4 stage order

1. R4A0 corporate_actions availability gate (read-only verify, then bounded
   bootstrap/backfill if needed)
2. R4A preclose derivation (EXCHANGE_DISPLAY_PRECLOSE) + ex-date parity gate
3. R4B trading_status enum enrichment + historical ST exact-scope backfill +
   suspension reconstruction verification
4. R4C turnover_rate dataset (Baostock thin wrapper + crosscheck)
5. R4D price-limit rule engine (versioned table + research doc V01.2)
6. R4E stable_market_facts assembly
7. R4F FACTS_READY verifier (read-only)

## 9. FACTS_READY formula

FACTS_READY ∈ {true, false}. FACTS_READY is boolean-only; a PARTIAL value is
not a valid FACTS_READY state and FACTS_READY is never set to PARTIAL.

FACTS_READY =
  PRECLOSE_COMPLETE
  AND TRADING_STATUS_COMPLETE
  AND TURNOVER_COMPLETE
  AND PRICE_LIMIT_COMPLETE
  AND ASSEMBLY_COMPLETE
  AND ASOF_SAFE

FACTS_COVERAGE_STATUS ∈ {COMPLETE, PARTIAL, UNKNOWN} expresses partial or
unknown coverage separately when needed. FACTS_COVERAGE_STATUS != COMPLETE →
FACTS_READY = false. Any PARTIAL → READY promotion is forbidden.

- PRECLOSE_COMPLETE: corporate_actions gate PASS; every required symbol-day
  has a preclose (no-limit days included); ex-date parity sample PASS.
- TRADING_STATUS_COMPLETE: full grid covered; ST backfill scope complete;
  suspension reconstruction verified; UNKNOWN only in accepted buckets.
- TURNOVER_COMPLETE: rows == actual traded sessions; unresolved == 0;
  current-day EM crosscheck within tolerance.
- PRICE_LIMIT_COMPLETE (hard fail-closed):
  rule_table_pinned
  AND every required price-limit row resolved
  AND unresolved_required_price_limit_rows == 0
  A row classified coverage_status = UNKNOWN/PARTIAL is an unresolved required
  price-limit row; it can never satisfy PRICE_LIMIT_COMPLETE.
  unresolved_required_price_limit_rows > 0 → PRICE_LIMIT_COMPLETE = false →
  FACTS_READY = false.
- ASSEMBLY_COMPLETE: row-scope matches section 7; no dup/null PK; no invalid
  OHLC; no negative volume/amount; no post-ASOF.
- ASOF_SAFE: no fact uses data after 2026-08-17; rule effective dates correct.

Non-blocking by default:

- STAR_ST subtype = UNKNOWN does NOT block FACTS_READY as long as the
  risk-warning umbrella (is_risk_warning) is complete and no historical rule
  depends on ST vs *ST split.

Blocking:

- DELISTING arrangement interval unknown: affected historical limit rows keep
  coverage_status = UNKNOWN/PARTIAL, so unresolved_required_price_limit_rows
  > 0 → PRICE_LIMIT_COMPLETE = false → FACTS_READY = false. DELISTING coverage
  gaps can never be promoted to COMPLETE or READY.

## 10. Blocker / decision table

HARD_BLOCKERS (block PRICE_LIMIT_COMPLETE / FACTS_READY as specified):
1. DELISTING_ARRANGEMENT_INTERVAL — no pinned source; not derivable from
   delist_date; affected rows keep coverage_status = UNKNOWN/PARTIAL →
   unresolved_required_price_limit_rows > 0 → PRICE_LIMIT_COMPLETE = false →
   FACTS_READY = false.
2. corporate_actions availability unproven on real root — R4A0 gate is a
   precondition; must pass before preclose derivation.
3. turnover_rate dataset absent (Baostock `turn` not persisted).
4. Ex-date preclose parity unvalidated.
5. Price-limit rule engine absent (new thin code; authority now documented).
6. Historical suspension is reconstruction — verification scope required.

HARD_BLOCKER_N = 6

OPEN_NONBLOCKING_EDGES (do not count as hard blockers; do not block
PRICE_LIMIT_COMPLETE):
1. SH_PRE_2023_IPO_ORDER_RANGE_OPEN — those first listing days resolve to
   PRICE_LIMIT = NONE (resolved rows, zero
   unresolved_required_price_limit_rows contribution); the edge affects only
   future VALID_ORDER_PRICE_RANGE facts. Bounded SSE official research is a
   later-stage follow-up, not an R4 gate.

OPEN_NONBLOCKING_EDGE_N = 1

OPEN_QUESTIONS (design decisions for Sol):
1. Delisting-arrangement interval: bounded authority discovery vs. explicit
   UNKNOWN/PARTIAL coverage acceptance (expressed via FACTS_COVERAGE_STATUS;
   FACTS_READY stays boolean; PARTIAL → READY promotion remains forbidden).
2. Preclose parity sample design and tolerance.
3. turnover_rate denominator validation (before freezing exact free-float
   contract).
4. stable_market_facts layer: derived (default in this plan) vs curated.

RESOLVED_IN_V01_1:
1. Preclose semantic frozen (EXCHANGE_DISPLAY_PRECLOSE; first-day=issue price;
   NO_LIMIT_DAY != NULL_PRECLOSE).
2. Suspension semantics bound to pinned `derive/trading_status_history.py`;
   volume==0 is not standalone authority.
3. Turnover unit = percent; semantic label frozen until denominator
   validation.
4. PRICE_LIMIT vs VALID_ORDER_PRICE_RANGE separated; old IPO 64/144% never
   written as high/low_limit; SZ pre-2023 order range frozen.
5. Main-board risk-warning 5% -> 10% effective 2026-07-06 frozen.
6. ChiNext risk-warning transition 5% -> 20% at 2020-08-24 frozen (SZSE
   深证上〔2020〕620号 + implementation evidence).
7. is_risk_warning umbrella + risk_warning_subtype policy (ST/STAR_ST/UNKNOWN)
   with no new provider.
8. Source policy: only sse/szse/csrc for rule authority; secondary sources
   removed; BSE research dropped (BJ deferred).

RESOLVED_IN_V01_2 (this revision, Sol V01.1 re-audit closure):
1. PRICE_LIMIT_COMPLETE frozen as a hard fail-closed formula; delisting-
   afflicted UNKNOWN/PARTIAL rows can never satisfy PRICE_LIMIT_COMPLETE.
2. FACTS_READY frozen as boolean {true,false}; FACTS_COVERAGE_STATUS
   {COMPLETE,PARTIAL,UNKNOWN} carries coverage nuance; any PARTIAL → READY
   promotion is forbidden.
3. Blocker taxonomy split into HARD_BLOCKERS (n=6) and OPEN_NONBLOCKING_EDGES
   (n=1); SH_PRE_2023_IPO_ORDER_RANGE_OPEN is an OPEN_NONBLOCKING_EDGE
   (PRICE_LIMIT = NONE on those days).
4. Official anchors verified by Sol are bound: 深证会〔2014〕54号 (SZ pre-2023
   IPO order range), 深证上〔2026〕551号 and 上证发〔2026〕41号 (2026 Trading
   Rules, effective 2026-07-06). No new rule research, no secondary sources.

## 11. Explicitly out of scope

No BJ extension, no 5m, no industry/index, no Query Core/MCP, no
B1/B2/TradePlan, no full-market provider download, no legacy promotion,
no real R4 execution.
