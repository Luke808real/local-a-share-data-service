# ASL Daily Facts vs CNEquity capability dedup audit

## 0. Scope and method

Read-only audit. No provider request, no publication, no pointer or manifest
mutation, no data cleanup, no daily-maintenance execution. Every claim below is
derived from the **locally installed** CNEquity package, its source, and the
existing local lake state; no upstream latest was consulted as fact.

Headline question answered first: **the roughly 10 hour Daily Facts full-market
BaoStock acquisition is largely a re-implementation of capability CNEquity
already has.** Four of the five fields are either native to a CNEquity dataset
or derivable from CNEquity bulk sources; none of the five requires a
per-symbol provider sweep. See section 14 for the exact scope of the exception.

````text
ASL_HEAD     = 54c12300120c3661f60901e15916c94e5198c000
BRANCH       = codex/r3-incremental-hardening-daily-facts-phase1-v01
````

## 1. Which CNEquity is actually running

Measured from the installed distribution metadata, not from requirements files.

| Environment | Package | Version | Resolved install path |
|---|---|---|---|
`| /Users/luke808/ASL/.venv | `cnequity` | `0.7.2` | `.venv/lib/python3.12/site-packages/cnequity` |
`| /Users/luke808/ASL/.venv-mcp | not installed | - | - |

````text
CNEQUITY_RUNTIME_VERSION = 0.7.2
CNEQUITY_RUNTIME_SHA     = a18ee0484dfb0801650175471724def3228b8a17
CNEQUITY_INSTALL_PATH    = /Users/luke808/ASL/.venv/lib/python3.12/site-packages/cnequity
CNEQUITY_CONFIG          = /Users/luke808/ASL/config/cnequity.toml
CNEQUITY_PYTHON          = CPython 3.12.13
````

`direct_url.json` records `git+https://github.com/rootSunc/CNEquity.git` at
`requested_revision = commit_id = a18ee0484dfb0801650175471724def3228b8a17`,
matching the `pyproject.toml` pin and the R2 baseline contract. `.venv-mcp` carries
no CNEquity at all: the MCP/query environment is a pure read runtime.

ASL imports resolve into `.venv`:

````text
cnequity.adapters.baostock._session -> .venv/.../cnequity/adapters/baostock/_session.py
cnequity.config.loader              -> .venv/.../cnequity/config/loader.py
cnequity.orchestrator.manifest      -> .venv/.../cnequity/orchestrator/manifest.py
````

`config/cnequity.toml` points `[data] root` at
`/Users/luke808/AI/local-a-share-data-service-data`. That is **the same root ASL
writes its R3 authority into**: ASL's `curated/daily_bars` *is* CNEquity's `daily_bars`,
sharing one lake and one schema. The R3 authority is a gate layered on top of
that shared lake, not a separate store.

## 2. Dataset inventory

The installed package registers **42** datasets in
`cnequity.domain.datasets.DATASETS`. The registry, `domain/schemas.py` (polars
dtypes) and the step registry are the source of truth; nothing below is inferred
from file names.

| Dataset | Schema (non-provenance columns) | PK | Partition | Daily semantics | Primary / backup / backfill | Watermark | Local state |
|---|---|---|---|---|---|---|---|
`| `daily_bars` | symbol, trade_date, open, high, low, close, volume, amount | (symbol, trade_date) | day / trade_date | `by_date` | tdx_protocol / eastmoney / - | yes, 1d | 2,598 partitions through 2026-09-10 (ASL R3) |
`| `trading_status` | symbol, trade_date, **is_trading (Bool)**, **status (Utf8)** | (symbol, trade_date) | month / trade_date | `snapshot` | tdx_protocol facade / eastmoney / **baostock** | yes, 1d | **absent (0 files)** |
`| `corporate_actions` | symbol, **ex_date**, action_type, **cash_dividend**, **bonus_ratio**, **transfer_ratio**, allotment_ratio, allotment_price | (symbol, ex_date, action_type) | year / ex_date | `by_date` | tdx_protocol / eastmoney / - | yes, 1d | 11 partitions, 40,694 rows, ex_date 2016-01-07..2026-09-08 |
`| `valuation_metrics` | symbol, trade_date, pe_ttm, pb, ps_ttm, total_mv, **float_mv** | (symbol, trade_date) | day / trade_date | `snapshot` | eastmoney / - / baostock | yes, 1d | **absent (0 files)** |
`| `share_structure` | symbol, **change_date**, total_shares, **float_shares**, restricted_shares, free_float_shares, change_reason, announce_date | (symbol, change_date) | year / change_date | `by_date`, **PIT** | eastmoney / - / - | n/a (PIT) | **absent (0 files)** |
`| `instruments` | symbol, name, exchange, asset_type, list_date, delist_date, prev_symbol | symbol | merge file | `by_date` | tdx_protocol / baostock / baostock | yes | 7,757 rows (SH 3,583 + SZ 4,174) |
`| `trading_calendar` | trade_date, is_trading, ... | trade_date | year | `by_date` | tdx_protocol | yes | through 2027-09-08 |
`| `adj_factors` | symbol, trade_date, adjust_type, factor | (symbol, trade_date, adjust_type) | day (derived) | `by_date` | sina | yes | watermark 2026-09-08 |

Two registry facts that shape the recommendation:

1. `trading_status` carries **both** an `is_trading` boolean and a `status` string.
   `query/universe.py` defines `EXCLUDED_STATUSES = {st, *st, suspended}`, so one
   dataset encodes suspension and ST state together.
2. `corporate_actions` stores per-share, pre-tax values with an explicit `ex_date` -- the
   three inputs an exchange reference price needs (cash dividend, bonus ratio,
   transfer ratio). The schema carries a UNIT CONTRACT comment stating adapters
   divide the Chinese per-ten-share convention by 10, so a declared 10-for-8.5
   yuan distribution arrives as 0.85.

## 3. preclose findings

**A. Not native.** `DAILY_BARS_SCHEMA` has exactly eleven columns and stores no
`preclose`, `pre_close`, `prev_close`, `pct_chg` or `change_pct`. A scan of all 42
registered schemas plus the adapters finds `pre_close` only inside the TDX wire
parser for the live security list (`get_security_list`), used by
`adapters/tdx_protocol/quotes.py`; it is never written to a curated schema.

**B. Determinable from existing local authority for ordinary sessions.** On a
normal trading day the exchange display preclose is the prior published close,
which the R3 `daily_bars` partitions already hold through 2026-09-10.

**C. Determinable for ex-dates from CNEquity `corporate_actions`.** The local
`corporate_actions` already holds `(symbol, ex_date, cash_dividend, bonus_ratio,`
`transfer_ratio)`. The reference price is a deterministic function of the prior
close and those three ratios. This is materially stronger than the 2026-09-09
workflow, which fetched sixteen CNInfo PDFs to recover one effective adjustment
per symbol.

Direct evidence of the gap that remains in the local data:

````text
local corporate_actions max(ex_date)              = 2026-09-08
ASL 2026-09-09 exception keys with a matching row  = 0 of 16
````

The data is the right shape but one day stale relative to the 09-09 run: the
daily `corporate_actions` step had last executed on 2026-09-08
(`meta/state/corporate_actions.json`), so the 09-09 ex-dates were never fetched.
That is a scheduling gap, not a capability gap. The daily step is one EastMoney
datacenter call filtered to the exact ex_date
(`fetch_corporate_actions_eastmoney(..., backfill=False)` sets
`filter_expr = (EX_DATE='2026-09-09')`), returning the day's handful of events in a
single request with no per-symbol traffic.

**Contract conflict -- the one field that cannot be changed by fiat.**
`docs/contracts/DAILY_FACTS_PHASE1_PRECLOSE_AUTHORITY_V01.md` freezes:

````text
PRECLOSE_SEMANTIC     = EXCHANGE_DISPLAY_PRECLOSE
CANONICAL_SOURCE      = BAOSTOCK_HISTORY_K_PRECLOSE
CORPORATE_ACTION_ROLE = VALIDATION / SENTINEL / DIAGNOSTIC ONLY
````

and states that corporate-action records "must not reconstruct the canonical
preclose dataset". A locally derived preclose therefore contradicts a frozen,
already-accepted contract and requires an explicit design decision rather than a
refactor inside a task (AGENTS.md: return `DESIGN_DECISION_REQUIRED`).

````text
PRECLOSE_SOURCE_RECOMMENDATION = LOCAL_DERIVED_FROM_CNEQUITY
   (this requires an explicit revision of the frozen preclose contract; under the
    contract as it stands today the honest label is ASL_EXTRA_PROVIDER_REQUIRED)
````

A second, narrower caveat: a derived reference price must reproduce the
exchange's tick rounding, not the raw arithmetic. The 2026-09-09 work already
documents this shape of risk -- `300196.SZ` needed the treasury-share-adjusted
`0.1974617` rather than the nominal `0.20`. MASTER_SPEC section 14 already owns
that precision problem in the Price Limit Rule Engine, and a derived preclose
would have to answer to the same rule.

## 4. pct_chg findings

No dataset stores a percentage change for stocks. `change_pct` exists only on
`sector_bars` and `sector_fund_flow`, which are board-level aggregates.

It is a pure function of two columns the lake already holds:
`pct_chg = (close / preclose - 1) * 100`. ASL's own certification already computes
this and uses the provider value only as crosscheck evidence: `reconcile()` writes
`pct_chg_calculated` and marks `pct_chg_reconciliation = MATCH` when the two agree
within 0.02. The frozen 2026-09-09 receipt records `PCT_CHG_UNRESOLVED_N = 0`, i.e.
the derivation already agreed with the provider on all 5,208 rows.

````text
PCT_CHG_SOURCE_RECOMMENDATION = LOCAL_DERIVED_FROM_CNEQUITY
````

Keeping a provider request purely to re-fetch a value the lake can compute from
its own published columns is the clearest redundancy in the current design.

## 5. trade_status findings

**Native.** `trading_status` is a required, watermarked, daily dataset with
`is_trading` and `status`.

- Provider: the daily step calls `fetch_trading_status` through the TDX facade,
  which the step's own comment documents as an EastMoney current-state snapshot
  re-attributed with `source="eastmoney"` so PIT precedence never mistakes it
  for exchange history.
- Call pattern: `_fetch_st_symbols` = one paginated `clist` sweep of the
  risk-warning board (`fs=m:0+f:4,m:1+f:4`, `page_size=100`);
  `_fetch_suspended_symbols` = one `RPT_CUSTOM_SUSPEND_DATA_INTERFACE`
  datacenter query with a server-side date filter. Both are **bulk**. Rows for
  every symbol are then emitted locally -- no per-symbol request.
- Failure mode is fail-closed: an empty ST set is accepted, but a transport or
  malformed response raises rather than silently labelling every name normal.
  The suspension path likewise raises when the response contains no row covering
  the requested date.
- History: `adapters/baostock/st_history.py` is the registered backfill source and
  reconstructs per-day ST labels back to 2016 by a per-symbol sweep. That is a
  **one-time historical** path, not a daily one, and the module states a missing
  row stays unknown rather than being read as non-ST.
- Local gap: the dataset is **not built locally** (0 files). `meta/state` holds no
  `trading_status` watermark.

Additionally, CNEquity already knows how to close this locally with **zero
network**: `cne derive trading_status` calls `derive_suspension_history()`, which
writes `status="suspended"` rows from daily-bar gaps. Its rule is the robust one for
this lake -- a listed symbol with **no traded bar** (absent, or present with
`volume=0`) was suspended -- and it ranks sources so a stale current-state
snapshot cannot overwrite point-in-time evidence.

That rule matters because the local R3 bars encode suspension inconsistently:

````text
2026-09-08: 9 requested-but-unobserved symbols -> 0 rows in the R3 partition
2026-09-09: 10 symbols present with volume=0 and amount=0
````

A `volume == 0` test alone would have missed all nine 2026-09-08 cases; a presence
test alone would have missed all ten 2026-09-09 cases. CNEquity's derive covers
both by construction.

````text
TRADE_STATUS_SOURCE_RECOMMENDATION = CNEQUITY_NATIVE (daily bulk)
                                     + LOCAL_DERIVED_FROM_CNEQUITY (bar-gap derive)
````

**Answer to the pointed question: no, ASL does not need to call BaoStock
tradestatus for 5,208 symbols every day.** It needs approximately four bulk
requests, or zero network if the bar-gap derive is accepted as the suspension
authority.

## 6. is_st findings

**Native, in the same dataset.** `trading_status.status` already distinguishes
`st` (and `*st` by vocabulary, though `st_history` documents that BaoStock's
`isST` is binary and collapses both into `st`). It is a daily current-state field,
which is what a daily fact needs; historical as-of state comes from the Baostock
backfill or from the `st_coverage` receipt module, which keeps completion
evidence separate from the facts themselves.

Mapping onto the current ASL tri-state is direct:

````text
CNEquity status = normal     -> ASL is_st = FALSE
CNEquity status = st / *st   -> ASL is_st = TRUE
CNEquity status = suspended  -> ASL is_st = FALSE  (suspension is the dominant fact)
no row                       -> ASL is_st = UNKNOWN (remains fail-closed)
````

The `UNKNOWN` arm is load-bearing: `st_history` fails the whole symbol closed on
unexpected `isST` vocabulary precisely so an unknown never becomes negative
evidence, which is compatible with ASL's `IS_ST_UNKNOWN_N` gate.

````text
IS_ST_SOURCE_RECOMMENDATION = CNEQUITY_NATIVE
````

**Answer: no, ASL does not need a daily per-symbol BaoStock isST sweep.**
**Answer: no, ASL does not need a daily per-symbol BaoStock isST sweep.**

## 7. turnover_rate findings

This is the field that looked hardest and turned out to be the most interesting.

**A. Not produced natively for stocks.** An exhaustive scan of all 42 registered
schemas for field names containing turn, float or circulat returns only:

````text
share_structure    -> float_shares, free_float_shares
shareholder_counts -> avg_float_shares
valuation_metrics  -> float_mv
sector_fund_flow   -> turnover_pct     (board level, not per stock)
````

No per-stock `turnover_rate`. Note also a vocabulary trap: many comments in
the package use the word turnover to mean 成交额 (`amount`), e.g. the Sina adapter
noting it serves no turnover. Those are not 换手率 and must not be read as such.

**B. Yes -- exactly derivable, and the identity is verifiable.**

````text
turnover_rate = volume / float_shares * 100
float_shares  = share_structure.float_shares, as-of the trade date (PIT)
volume        = daily_bars.volume, in shares (lake unit contract)
````

`domain/units.py` fixes the lake unit as shares and documents per-vendor
conversions (TDX daily K is in lots and is multiplied by 100 at the adapter
boundary). The local R3 partition confirms it empirically: `000001.SZ` on 2026-09-09
has `volume = 58,230,600` and `amount = 682,946,752` at `close = 11.70`, giving
`amount / close / volume = 1.0022`. The lake really is in shares, so no 100x
correction belongs in the derivation.

Inverting the BaoStock `turn` already present in local RAW evidence reproduces
plausible float share counts, which is an independent check on the identity:

| Symbol | R3 volume (shares) | BaoStock turn (%) | Implied float shares | Implied (bn) |
|---|---|---|---|---|
| `000001.SZ` | 58,230,600 | 0.3001 | 19,403,732,089 | 19.404 |
| `600519.SH` | 3,222,600 | 0.2578 | 1,250,038,790 | 1.250 |
| `601888.SH` | 21,003,200 | 1.0757 | 1,952,514,642 | 1.953 |
| `002580.SZ` | 64,897,600 | 14.3471 | 452,339,497 | 0.452 |
| `000002.SZ` | 112,849,700 | 1.1616 | 9,715,022,383 | 9.715 |
| `600036.SH` | 43,207,300 | 0.2095 | 20,624,009,547 | 20.624 |
| `300750.SZ` | 39,024,000 | 0.9160 | 4,260,262,009 | 4.260 |
| `601318.SH` | 52,422,200 | 0.4918 | 10,659,251,728 | 10.659 |

These match the issuers' known float counts (平安银行 near 19.4bn, 贵州茅台 near
1.25bn, 招商银行 near 20.6bn, 万科A near 9.7bn). BaoStock's `turn` is therefore the same
volume-over-float-shares definition the derivation uses, not a different one.

**C. Yes -- a whole-market snapshot exists and is bulk.** `valuation_metrics` is
fetched from a single paginated `clist` sweep
(`_VALUATION_FIELDS = f12,f13,f9,f23,f45,f20,f21`, `page_size=100`) and carries
`float_mv` (EastMoney `f21`). Two local routes follow:

1. `volume / share_structure.float_shares * 100` -- the exchange definition.
2. `amount / valuation_metrics.float_mv * 100` -- same-day, no as-of join, but an
   approximation that mixes an intraday-traded amount with a close-priced market
   cap.

For a published fact, route 1 is the one whose semantics match what ASL already
stores under `turnover_unit = PERCENT`, and it needs `share_structure`, which
derives its precision from `change_date` (the date the count *changed*), not a
report period. An as-of join on `change_date <= trade_date` is therefore the
correct lookup.

Circularity warning, recorded because it is easy to get wrong: the Baostock
*valuation history* adapter computes `float_mv = amount / (turn/100)`, i.e. it
derives market cap **from** turnover. Recovering turnover from such a row would
be algebra, not evidence. The derivation above deliberately uses
`share_structure.float_shares` (EastMoney), which is an independent input.

**D. Where BaoStock sits in this CNEquity.** Not a daily primary for anything in
this list:

````text
daily_bars        : primary tdx_protocol, backup eastmoney, baostock unused
trading_status    : primary tdx_protocol (eastmoney snapshot), baostock = backfill_source
corporate_actions : primary tdx_protocol, backup eastmoney, baostock unused
valuation_metrics : primary eastmoney (daily snapshot), baostock = backfill_source
share_structure   : primary eastmoney, no baostock
````

BaoStock in this pin is a **historical backfill and crosscheck** engine: a
per-symbol sweep affordable once for history, never a daily full-market path.
The pacing in `config/cnequity.toml` (`min_interval_seconds = 1.0`,
`batch_size = 20`, `batch_rest_seconds = 120`) is the same throttle ASL inherits,
and it is what turns a per-symbol sweep into a ten-hour job.

````text
TURNOVER_SOURCE_RECOMMENDATION = LOCAL_DERIVED_FROM_CNEQUITY
   (volume / share_structure.float_shares, as-of; valuation_metrics.float_mv as crosscheck)
````

## 8. CNEquity daily pipeline findings

**It exists and ASL already configures it.** `cne run daily` runs waves from
`config/cnequity.toml`, which in this checkout declares:

````text
wave reference                 : instruments, trading_calendar, trading_status   (parallel)
wave corporate_actions_to_bars : corporate_actions, daily_bars                 (sequential)
wave index                     : index_bars                                    (parallel)
wave finalize                  : compact, derive_adj_factors, derive_industry_index, audit
````

That is the whole answer to whether ASL should write a second provider acquisition
layer: the datasets needed to derive all five fields are **already in ASL's own
configured daily job**. `trading_status` and `corporate_actions` are in the
`reference` and `corporate_actions_to_bars` waves today.

````text
DAILY_PIPELINE_AVAILABLE              = YES  (cne run daily; cne run daily --stale-only)
DAILY_PIPELINE_DATASETS               = instruments, trading_calendar, trading_status,
                                        corporate_actions, daily_bars, index_bars,
                                        compact, derive_adj_factors,
                                        derive_industry_index, audit
DAILY_PIPELINE_PROVIDER_CALL_PATTERN  = bulk per dataset (TDX batched; EastMoney clist
                                        paginated; EastMoney datacenter filtered by date)
DAILY_PIPELINE_BATCHING               = yes -- TDX chunks of batch_size=50, manifest batches
                                        per chunk
DAILY_PIPELINE_RESUME_SUPPORT         = yes -- manifest.db batches, retryable chunk receipts,
                                        RunLock single-writer; --stale-only is a documented
                                        second window for snapshot datasets that lost their day
````

The dependency graph is explicit: waves declare order (`corporate_actions` before
`daily_bars`; `derive_industry_index` depends on `daily_bars`; `market_breadth`
depends on `daily_bars`). `stale_fetch_steps()` excludes derived-layer datasets and
datasets without a registered step, so a stale-only pass re-fetches only what a
source outage actually cost -- precisely the failure that lost `valuation_metrics` on
2026-07-30 and 07-31 in the package's own incident note, where a snapshot dataset
that missed its one scheduled window could not be replayed tomorrow.

**Answer to the question in the task: yes.** Given that `daily_bars`,
`trading_status` and `corporate_actions` are updated in dependency order by one
pipeline, ASL should consume those products through its quality and semantics
layer rather than maintain a second provider acquisition path for the same facts.

## 9. Current ASL request pattern (measured, not estimated from taste)

From the frozen 2026-09-09 ledger and its persisted RAW evidence:

````text
units in ledger                  = 5,208
sum(attempts)                    = 5,208
RAW files persisted              = 5,208
requested_start = requested_end  = 2026-09-09 for every sampled unit (400 of 400)
rows per RAW file                = 1
````

So the granularity is **one provider request per symbol per day** -- not
per-symbol-date-range, not batched. The `_year_windows()` split in
`cnequity_bridge.py` means a multi-year request would cost one query per year per
symbol; the 2026-09-09 run was launched single-date, which is why it stayed at
5,208 rather than roughly 57,000.

Wall clock decomposes from pacing, not from data volume:

````text
5208 symbols / batch_size 20      = 261 batches
261 x batch_rest_seconds 120      = 31,320 s  = 8.7 h
5208 x min_interval_seconds 1.0   =  5,208 s = 1.45 h
observed acquisition span (first request start -> last batch start) = 10.3 h
observed whole run 2026-09-09T15:52:42Z -> 2026-09-10T05:24:44Z     = 13.5 h
````

For contrast, the R3 path that produces the OHLCV authority itself uses the same
vendor-neutral TDX adapter at 50 symbols per batch and completed 2026-09-10 in
105 batches in **3.8 minutes** for the same 5,208 symbols.

Structural comparison for the same five fields:

| Path | Requests per day | Observed / estimated wall time |
|---|---|---|
| ASL Daily Facts (BaoStock per-symbol, today) | 5,208 | about 10.3 h acquisition |
| CNEquity `daily_bars` (TDX bulk) | 105 batches | 3.8 min (measured 2026-09-10) |
| CNEquity `trading_status` (ST board pages + 1 suspension report) | about 4 | seconds |
| CNEquity `corporate_actions` (1 exact-date datacenter call) | 1 | seconds |
| CNEquity `valuation_metrics` (clist, `page_size=100` over 7,757 instruments) | about 78 | 1 to 2 min |
| CNEquity `share_structure` (datacenter NOTICE_DATE window) | about 1 to 3 | seconds |
| CNEquity `share_structure` (datacenter NOTICE_DATE window) | about 1 to 3 | seconds |

## 10. Field-level decision matrix

| Fact | Current ASL Source | CNEquity Native? | Can Derive Locally? | Extra Provider Needed? | Recommended Future Source |
|---|---|---|---|---|---|
| `preclose` | BaoStock `preclose` (per-symbol, 5,208 req/day) | No -- no stored field in any of 42 schemas | Yes for ordinary days (prior published R3 close) and for ex-dates (prior close plus `corporate_actions`) | No, subject to a preclose-contract revision | `LOCAL_DERIVED_FROM_CNEQUITY` |
| `pct_chg` | BaoStock `pctChg` (same request) | No -- `change_pct` exists only at board level | Yes -- `(close / preclose - 1) * 100`; already computed and matched on 5,208 of 5,208 rows | No | `LOCAL_DERIVED_FROM_CNEQUITY` |
| `turnover_rate` | BaoStock `turn` (same request) | No per-stock field; `float_shares` and `float_mv` are | Yes -- `volume / share_structure.float_shares * 100`, verified against 8 symbols | No | `LOCAL_DERIVED_FROM_CNEQUITY` |
| `trade_status` | BaoStock `tradestatus` (same request) | **Yes** -- `trading_status.is_trading` and `.status`, daily, bulk | Also yes -- `cne derive trading_status` (bar gap plus zero volume), zero network | No | `CNEQUITY_NATIVE` plus local bar-gap derive as fallback |
| `is_st` | BaoStock `isST` (same request) | **Yes** -- `trading_status.status` in normal, st, *st, suspended | Partly -- local derives cover suspension, not ST | No | `CNEQUITY_NATIVE` |

## 11. Redundant vs required components

**Redundant** (duplicated capability, measured):

````text
1. Daily Facts BaoStock full-market sweep as a five-field bundle
   -- 5,208 requests/day; 5208 of 5208 units carried exactly one requested date and one row.
2. Provider pct_chg as a fetched field   -- re-derivation of (close, preclose).
3. Provider tradestatus as a fetched field -- trading_status dataset + local bar-gap derive.
4. Provider isST as a fetched field      -- trading_status.status.
5. Provider turn as a fetched field      -- volume / float_shares.
6. The 16-PDF CNInfo evidence workflow for a one-day ex-date delta
   -- only necessary because corporate_actions had not been run for that date.
````

**Required** (not duplicated by CNEquity, must stay in ASL):

````text
1. The R3 frozen-identity authority (5,456 SH/SZ lifecycle) and its pointer-last
   publication contract -- CNEquity has no such scoped authority.
2. Quality and certification gates with their evidence receipts (as-of binding to a
   manifest hash, fail-closed blocker accounting).
3. The Daily Facts semantics layer: tri-state UNKNOWN, turnover_unit = PERCENT,
   per-key provenance, and independent authority scope versus R3.
4. Reference-price exception evidence and its hash-verified official-source
   contract -- needed for as long as preclose must equal the exchange display value
   rather than a locally computed one.
5. The read-only LocalQuery and MCP access layer with its published-manifest allowlist.
6. The single-writer, resumable maintenance entry point.
````

## 12. Recommended architecture (one option, not a menu)

````text
CNEquity (pinned 0.7.2 at a18ee04) -- shared lake at the ASL data root
  |- daily_bars          (TDX bulk; already produced through 2026-09-10)
  |- trading_status      (EastMoney bulk: ST board + suspension report)   [TO BUILD]
  |- corporate_actions   (EastMoney bulk, exact-date; ex_date + cash/bonus/transfer)
  |- share_structure     (EastMoney bulk, PIT change_date; float_shares)   [TO BUILD]
  \- valuation_metrics   (EastMoney clist; float_mv as crosscheck)        [TO BUILD]
           |
           v
ASL authority / quality / semantics layer
  |- freeze and verify the exact CNEquity dataset manifests it consumes
  |- derive preclose, pct_chg, turnover_rate (versioned, reproducible rule id)
  |- map trading_status to trade_status / is_st tri-state, fail closed on absence
  \- pointer-last publication of the Daily Facts authority
           |
           v
LocalQuery (published-manifest allowlist)
           |
           v
MCP (status / instrument / bars / latest / facts)
````

The only place an ASL-specific provider call would remain legitimate is a field
CNEquity genuinely cannot supply. On this audit, **no such field exists among the
five**: every one is native or derivable. The genuinely ASL-owned acquisition that
must survive is the bounded official-disclosure evidence path, and only while
`preclose` keeps its current frozen `EXCHANGE_DISPLAY_PRECLOSE` semantics.

## 13. Expected daily runtime and network impact

````text
CURRENT    : 5,208 per-symbol BaoStock requests/day, about 10.3 h acquisition,
             about 13.5 h end-to-end for one trading date.
REDESIGNED : about 105 TDX batches (daily_bars, already the R3 path) + about 85 bulk
             HTTP requests (trading_status ~4, corporate_actions 1,
             valuation_metrics ~78, share_structure ~1-3).
             Order of minutes rather than half a day.
ZERO-NETWORK OPTION for suspension: cne derive trading_status (local bar gaps)
             removes even the ~4 trading_status requests when the bar-gap rule is
             accepted as the suspension authority.
````

Two honest qualifications. First, the ~85 figure counts HTTP requests under the
documented page sizes; the `clist` sweep for `valuation_metrics` dominates it, and
`page_size=100` was chosen upstream because larger pages trip push2 502s, so it
is a deliberate trade of request count for reliability rather than a defect.
Second, these are code-path counts, not measured runs: this audit was read-only by
instruction and issued no provider traffic (section 15).
instruction and issued no provider traffic (section 15).

## 14. Answers to the task's headline decisions

````text
SHOULD_KEEP_CURRENT_BAOSTOCK_FULL_MARKET_ACQUISITION = NO

   Not PARTIAL: none of the five fields is left without a native or local source.
   A PARTIAL answer would be warranted only if, say, turnover had no float-share
   input -- it does. The single caveat is contractual, not technical: preclose may
   only stop being a per-symbol provider field if the frozen preclose authority
   contract is explicitly revised.

CAN_DAILY_FACTS_BECOME_MOSTLY_LOCAL_DERIVED = YES

   4 of 5 fields derive from data the lake already holds or CNEquity bulk-fetches;
   the 5th (trade_status and is_st) is native. The word mostly understates it.

EXPECTED_NETWORK_COMPLEXITY_AFTER_REDESIGN = bulk snapshot + bounded exception requests
   (about 190 requests/day total including the existing 105 TDX batches, versus
    5,208 today; zero per-symbol provider calls)
````

## 15. Limitations of this audit

````text
1. No provider request was issued, so bulk for trading_status, corporate_actions,
   valuation_metrics and share_structure is established from code paths and
   pagination constants, not from an observed run. One live smoke per dataset in a
   later authorized task would settle it.
2. trading_status, valuation_metrics and share_structure are NOT built in this lake
   (0 files each). Their watermarks and coverage are unproven, and the derivation
   cannot be validated end-to-end until they are.
3. The float-share identity was validated on 8 symbols from one session, using
   BaoStock's own turn as the reference. It is a strong consistency check, not a
   full-market reconciliation against an exchange-published turnover rate.
4. The derived-preclose path needs the exchange tick-rounding rule before it can be
   called a reference price; that rule is not implemented in this repo.
5. The frozen preclose contract conflicts with the recommended derivation. Per
   AGENTS.md this is a DESIGN_DECISION_REQUIRED, not something this audit may
   decide. Downstream designs must resolve section 3 before any redesign.
6. Historical ST and suspension backfill is a per-symbol BaoStock sweep in CNEquity
   too; this audit concerns the DAILY incremental path only. Bulk ST history exists
   in that path; it is a separate, one-time cost rather than a daily one.
````

## 16. Next implementation task (proposal, not authorized here)

````text
TITLE: Daily Facts local-derivation feasibility, phase 1 -- build the CNEquity inputs

STEP 1  Resolve the preclose contract question first (DESIGN_DECISION_REQUIRED).
        Either revise DAILY_FACTS_PHASE1_PRECLOSE_AUTHORITY_V01 to allow a locally
        derived display reference price, or keep the provider contract and accept a
        per-symbol sweep for this field alone.
STEP 2  Build the three absent CNEquity datasets and record their watermarks and
        coverage receipts: trading_status, valuation_metrics, share_structure.
        THIS IS THE GATE: no derivation can be certified against a dataset that is
        not built.
STEP 3  Implement a versioned derivation module with explicit rule ids
        (for example PRECLOSE_DERIVED_V01, TURNOVER_DERIVED_V01) and offline tests
        against the frozen 2026-09-09 Daily Facts partition as the regression fixture
        -- reproduce 5,208 of 5,208 rows before touching any live path.
STEP 4  Cross-validate against the already-persisted 2026-09-09 BaoStock RAW
        (5,208 files, still on disk) as provider crosscheck evidence. The fixture for
        this comparison already exists and needs no new network.
STEP 5  Only then replace the daily acquisition path and re-run the maintenance
        command's idempotency and fresh-date tests.
````

Step 4 is the cheap win: the evidence needed to prove or falsify the whole
redesign is already on disk from the 2026-09-09 run, and validating against it
requires no provider traffic at all.
