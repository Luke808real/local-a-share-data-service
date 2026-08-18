# R3 DAILY FOUNDATION IMPLEMENTATION PLAN

**TASK_CONTRACT:** `R3-DAILY-FOUNDATION-V03`
**SPEC_VERSION:** `V1.0 FROZEN`
**BASE_HEAD:** `0254122a99f0a365d2be12f29a2a59b951497fd3`
**PHASE:** `R3 — DAILY FOUNDATION`
**MODE:** `IMPLEMENTATION / DATA EXECUTION / VALIDATION`
**FULL_MARKET_AUTHORIZED:** `YES — only after this exact plan receives independent AUDIT_PASS`
**SUPERPOWERS:** `NOT_USED — current user override`

## Goal

Build the first authoritative, multi-year RAW daily foundation in the clean
CNEquity root, covering SSE, SZSE, BSE, and historical delistings without
copying any R1 legacy row. Produce an explicit gap map and a fail-closed quality
receipt. R3 exits only as `DAILY_READY`; it does not build R4 facts, R5 5m,
R6 market/industry context, R7 publication, or R8 queries.

## Frozen daily window and security-master observation

- `R3_HISTORY_START = 2016-01-01`
- `R3_DAILY_AS_OF = 2026-08-17`
- All date bounds are inclusive.
- `2026-08-17` is deliberately a fully settled historical session relative to
  the plan date. No daily-bar or calendar run may silently substitute
  wall-clock today; live security-master observation uses the separately named
  receipt below.
- Daily prices are RAW/unadjusted. `daily_bars.volume` is shares and
  `daily_bars.amount` is CNY when present, per the pinned `v2` schema.
- Missing amount stays null with explicit source/coverage evidence; it is never
  rewritten as zero.

`R3_DAILY_AS_OF` governs bars and calendar-based quality. It does not claim that
the pinned live security-list and issued-code discovery APIs can reconstruct a
historical security-master snapshot. Those APIs have no as-of parameter. R3
therefore records a separate, immutable observation:

```text
R3_UNIVERSE_OBSERVED_AT = actual UTC completion timestamp of discovery
R3_UNIVERSE_REFERENCE_DATE = pinned discovery reference date
R3_UNIVERSE_CATALOG_SHA256 = complete issued-code catalog hash
R3_UNIVERSE_METADATA_SHA256 = curated instruments hash after enrichment
```

The effective universe for daily coverage is reconstructed by verified
`list_date <= R3_DAILY_AS_OF` and
`delist_date is null or delist_date >= R3_DAILY_AS_OF`. Securities listed after
the daily as-of may remain in the current authoritative security master, but
they are excluded from R3 expected coverage and are never represented as known
on 2026-08-17. Delayed execution changes the observation receipt, not the
frozen daily window; exact catalog and metadata hashes make that difference
auditable.

The 2016 floor follows the pinned CNEquity supported daily backfill floor and
provides more than ten years of daily history. Earlier history is not required
for `DAILY_READY` and must not be fetched by R3's separate THS history step.

## Entry gate

All conditions are mandatory:

1. Local `main`, `origin/main`, and `BASE_HEAD` are identical.
2. `docs/PROJECT_STATE.md` says `CURRENT_PHASE: R3 — DAILY FOUNDATION` and
   `R3_EXECUTION: NOT_STARTED`.
3. R2 exact commit `e354f59297cc2cf9722304f39a315712761d4b91` has
   `AUDIT_PASS`, and the administrative activation commit at `BASE_HEAD` has
   independently passed review.
4. Runtime provenance remains CNEquity v0.7.2 at Git commit
   `a18ee0484dfb0801650175471724def3228b8a17`; Python is 3.12.x; lock and
   config hashes remain the R2 audited values.
5. `/Users/luke808/AI/local-a-share-data-service-data` passes the R2 verifier
   as the exact 18-entry, zero-market-data baseline before the first R3 write.
6. No writer process or open writable handle exists under the target root.
7. At least 100 GiB is free on the target volume.
8. This exact plan commit has `AUDIT_PASS`. A plan author `PASS` is not enough.

If the target is no longer the exact R2 zero-data baseline before execution,
stop with `R3_PREFLIGHT_STATE_DRIFT`; do not repair, delete, or reinitialize it.

## Authority and scope decisions

### Legacy disposition

R1 assigned `CROSSCHECK_ONLY` to instruments and daily bars and assigned no
dataset `DIRECT_REUSE` or `MIGRATE_AFTER_NORMALIZATION`. Therefore the
Master Spec's legacy-validation/migration branch resolves in R3 as:

```text
LEGACY_DAILY_VALIDATION = R1 evidence accepted as CROSSCHECK_ONLY
LEGACY_DAILY_MIGRATION = NOT_AUTHORIZED / ZERO ROWS
NETWORK_GAP_BACKFILL = REQUIRED
```

R3 must not stat, list, open, hash, query, or otherwise access any legacy root.
No legacy row, manifest, schema, or footer may enter the target root.

### Why ordinary `cne init` is forbidden

At the pin, configured init phases also execute corporate actions, index bars,
trading status, adjustment derivation, industry derivation, and an audit that
can perform external cross-checks. Those belong to later phases. R3 uses only
the exact targeted command surface approved below; ordinary `cne init`,
`cne run daily`, and configured init/resume are forbidden.

### All-A and survivorship boundary

The pinned TDX adapter serves SH/SZ only. BSE instruments and bars require the
pinned Sina fallback, and the upstream code discovers BSE/live-missing names
through the resumable issued-code-space discovery. A TDX-only instrument list
is not `all_a` and must fail the gate.

The pinned instruments backfill uses Baostock formal delisting identity. The
dedicated delisted recovery path must produce a complete, window-scoped
receipt before the generic daily sweep is accepted. Current and historical
symbols remain separate from strategy eligibility; no ST/BSE/delisted symbol
is filtered because a strategy might not trade it.

## Global prohibitions

- Do not access any legacy root:
  - `/Users/luke808/AI/asl-shared`
  - `/Users/luke808/AI/asl-r8-5m-lake`
  - `/Users/luke808/AI/V flash/data`
- Do not delete, move, rename, repair, compact manually, vacuum, checkpoint,
  or clean any repository, target-root, legacy, worktree, or Trash object.
- Do not run ordinary `cne init`, `cne run`, `cne catchup`, `cne retry`,
  `cne clean`, `cne verify --repair`, `cne audit`, `cne query`, `cne status`,
  `cne demo`, `cne mcp`, or any live/query interface. At this pin the generic
  retry path force-runs `compact`, adjustment derivation, industry derivation,
  and audit after a recovered batch, so it is not R3-safe.
- Do not execute `daily_bars_history`, corporate actions, adjustment factors,
  trading status, turnover, price-limit facts, index, industry, 5m, publish,
  Query Core, MCP, scheduler, strategy, B1/B2, Forward, backtest, or TradePlan.
- Do not change `MASTER_SPEC.md`, `DECISIONS.md`, the R2 contract, the pinned
  dependency, config semantics, data-root path, or R1 evidence.
- Do not create a second data lake or temporary market-data root.
- Do not use mock data, force push, amend audited commits, or delete evidence
  branches/worktrees.
- Do not call a source outside the pinned CNEquity paths named by this plan.

## Planned repository file map

Create during R3 implementation:

- `src/ashare_data/__init__.py`
- `src/ashare_data/r3_daily.py`
- `tools/run_r3_daily_foundation.py`
- `tools/verify_r3_daily_foundation.py`
- `tests/test_r3_daily_foundation.py`
- `docs/contracts/R3_DAILY_FOUNDATION_CONTRACT.md`
- `reports/quality/R3_DAILY_GAP_MAP.md`
- `reports/audits/R3_DAILY_FOUNDATION_AUTHOR_REPORT.md`

Modify only when needed by the implementation contract:

- `pyproject.toml` (package discovery/entry metadata only; no new dependency
  unless independently reviewed before data execution)
- `docs/PROJECT_STATE.md` (final author handoff only)
- `tests/test_project_docs_contract.py` (final state assertions only)

Detailed machine receipts and logs belong under the external authoritative
root, not Git:

```text
meta/asl/r3/
  execution-state.json
  command-receipts/*.json
  logs/*.log
  r3-daily-quality.json
  r3-daily-gap-map.json
```

The runner and verifier must explicitly allow and inventory these paths. They
must not create data outside the existing authoritative root.

## Task 1 — Implement the fail-closed R3 control plane using TDD

### 1.1 RED tests first

Add focused tests before implementation. Synthetic fixtures must use pytest
temporary directories and must never point at the real target or a legacy
path. Initial RED must cover:

- exact runtime/config/lock/root/window validation;
- refusal without a reviewed-plan authorization token;
- refusal on non-zero or drifted R2 baseline at first execution;
- refusal if a legacy path appears in any argument/config/runtime log;
- exact command allowlist and order;
- separate frozen daily-as-of versus current universe-observation semantics;
- resumable discovery progress parsing and no-progress blocking;
- manifest/run receipt parsing;
- BSE metadata enrichment completeness and provenance;
- service-ledger/controller-batch coverage for direct/Sina staging and exact
  failed-scope retry;
- proof that Stage E never creates `derived/delisting_events`;
- source-scoped Sina null-amount and independent volume cross-check gates;
- duplicate PK, OHLC, negative volume/amount, provenance, schema, date-window,
  and unexpected-dataset failures;
- SH/SZ/BJ active-universe presence and historical-delisting presence;
- local-only read behavior of the verifier;
- no `latest_good_as_of` or Published state in R3;
- interrupted-run state machine behavior without deletion or blind restart.

Run only the focused synthetic test and record expected RED. Tests may not
open the real target databases, call a market endpoint, or create repository
cache artifacts.

### 1.2 Service-owned runner

`tools/run_r3_daily_foundation.py` is the only R3 data-execution entrypoint.
It delegates ingestion to a statically pinned, minimal subset of CNEquity step
APIs while owning the narrower ASL phase contract. Direct operator use of the
broader CLI is not part of R3. The runner must:

- require `--config config/cnequity.toml`, `--history-start 2016-01-01`,
  `--as-of 2026-08-17`, and an exact reviewed-plan SHA;
- verify Git/runtime/config/lock/target facts before invoking any writer;
- acquire one non-blocking service lock under `meta/asl/r3/` and refuse a
  second writer;
- persist an atomic stage machine and execution receipts with exact callable,
  arguments, start/end, result status, run ID, hashes, and retry lineage;
- be launched only by `uv run --frozen --no-sync --offline python
  tools/run_r3_daily_foundation.py ...`; uv stays offline and frozen while the
  pinned CNEquity adapters may use the explicitly authorized market endpoints;
- assert the exact pinned signatures/source identities for `JobEngine.run_job`,
  `JobEngine.run_step`, `discover_delisted`, TDX
  `fetch_daily_bars_parallel`/`fetch_daily_bars`, Baostock
  `fetch_delisted_bars`, Sina `fetch_daily_bars_sina`/`fetch_bars_via_sina`,
  EastMoney `fetch_daily_bars`, EastMoney
  `fetch_clist_pages`/`clist_rows_to_symbols`, and
  `delisted_coverage_report` before the first writer call; unexpected upstream
  drift is `RUNTIME_CONTRACT_DRIFT`;
- stream logs and progress; never swallow or reinterpret a non-zero exit;
- resume only the documented stage or exact failed run; never start the whole
  phase again merely because one batch failed;
- treat `warning`, unresolved scope, partial failure, zero progress, malformed
  JSON, unexpected dataset, or missing compact receipt as non-success;
- never clean staging. Preserved staged data and manifest evidence are inputs to
  an explicit retry, not permission for deletion;
- never call `run_job(..., retry_failed_only=True)` or the generic daily step.
  Every retry reuses the exact recorded source/symbol/window scope through the
  same approved raw adapter (`fetch_daily_bars_parallel` for TDX worker
  batches, Sina for non-TDX, EastMoney for the explicit fallback); after every
  expected batch resolves, call only the `compact` step;
- maintain a service-owned batch ledger for every direct or fallback scope.
  Each record contains run ID, physical dataset, symbols hash and bounded
  sample, window, adapter, attempt, staging relative path/hash, rows, and
  status. A controller manifest batch with the same identity and
  `blocks_compaction=True` must surround every scope not represented by an
  upstream worker batch;
- require both the upstream manifest and service ledger to have zero incomplete
  scope before compact. The compact receipt records the complete input staging
  inventory/hash, and a post-compact proof checks every input PK/provenance is
  present in curated while staging content itself remains unchanged;
- require every manifest batch dataset to be in the R3 allowlist and every
  retry to match a failed service-ledger scope. Reject any orphan reconciliation
  or mutation outside the exact current R3 run set;
- stop before any later task if a gate fails.

The runner must support `--preflight-only`, which is strictly read-only and
reuses the audited R2 verifier. First market-data execution is impossible until
preflight output records the exact plan audit authority.

### 1.3 Read-only verifier

`tools/verify_r3_daily_foundation.py` must open only the new target root. It
must not import or instantiate upstream writer helpers. Requirements:

- reject symlinks/path escape;
- prove databases/files exist before opening;
- use SQLite `mode=ro&immutable=1` only after rejecting a non-empty WAL;
- use DuckDB `read_only=True` for catalog metadata only;
- scan Parquet with bounded/lazy operations, one partition at a time where a
  global materialization would be unbounded;
- capture complete before/after path/type/size/inode/mtime/ctime/content-hash
  snapshots and fail on any verifier-caused change;
- print deterministic JSON to stdout; it writes no receipt itself.

### 1.4 Freeze the R3 contract

`docs/contracts/R3_DAILY_FOUNDATION_CONTRACT.md` must record:

- exact date window, sources, schema, PK, units, provenance, raw-price rule;
- separate current-universe observation and frozen daily-as-of semantics;
- full-universe, active-BJ metadata, and delisted evidence semantics;
- service batch ledger, controller manifest batch, compact input, and curated
  post-proof lineage;
- Sina null-amount acceptance boundary and independent volume-evidence gate;
- expected/observed/explained/unexplained daily coverage categories;
- the fact that status-based missing explanations remain pending R4;
- data/gap/quality receipt schemas and hashes;
- the exact R3 command surface and retry rules;
- `DAILY_READY` versus R7 `PUBLISHED` separation.

### 1.5 Task 1 verification and commit

Run frozen/offline focused tests and `git diff --check`. No real-root or market
write occurs in Task 1. Commit:

```text
feat: add R3 daily foundation controls
```

## Task 2 — Exact preflight and dry authorization proof

Run the new `--preflight-only` path. It must prove and record without mutation:

- exact accepted Git base and reviewed plan SHA;
- exact runtime, lock SHA, config SHA, package direct-url Git commit;
- exact R2 zero-data tree digest
  `ddcf9dc509b6bfb0cea8bd27511360ba6d1b4151b4a745f3e0fcb230ecd43dd5`;
- no target-root writer/open lock and no manifest WAL payload;
- target volume free space;
- configured sources enabled, `allow_mock=false`, workers=1;
- no credential or proxy value is written to a report;
- `R3_HISTORY_START` and `R3_DAILY_AS_OF` are trading-window valid and the end session
  is final;
- every exact internal callable, read-only CLI shape, signature/source identity,
  network provider, and write path has been statically approved;
- legacy paths appear only as denylist strings and are not accessed.

Any mismatch stops R3 before data execution. Commit any test/contract correction
only if it stays within this audited plan; otherwise return
`DESIGN_DECISION_REQUIRED` and seek a revised plan audit.

## Task 3 — Full-market daily acquisition

This task is the only full-market authorization in R3. Execute through the
service-owned runner and never by ad hoc shell commands.

### Stage A — Formal security master seed

Run a single pinned `JobEngine.run_job` containing only `instruments`, with
`backfill=True`, `trade_date=R3_DAILY_AS_OF`, and `finalize_run=False`; after all
batches resolve, run only the pinned `compact` step and finish the manifest
run. This is semantically the narrow part of:

```text
cne backfill instruments --config config/cnequity.toml
```

Require a successful run and compact batch. This obtains the live SH/SZ
snapshot plus Baostock formal delisting identity. Verify unique symbols,
provenance, and formal identity evidence before continuing.

### Stage B — Resumable full issued-code discovery

Call pinned `discover_delisted(config, limit=1000)` in bounded chunks. The
equivalent CLI shape is:

```text
cne delisted discover --config config/cnequity.toml --limit 1000
```

Repeat until returned `complete=true`, `failed=0`, and `remaining=0`. Each chunk
is a full-market operation but bounded in time and checkpointed by upstream
every 100 probes. A failed probe remains pending. Require monotonic progress;
after three consecutive chunks with no decrease in remaining scope, stop
`BLOCKED_SOURCE_DISCOVERY` with all evidence preserved. Never relabel a failed
probe as never-issued.

Discovery is a current observation because the pinned API has no as-of
parameter. Capture its actual UTC completion time, the exact internal reference
date, catalog hash, classified live/recent hash, and formal-delisted hash. Do
not label this snapshot `as_of=2026-08-17`; use it only to build the current
security master and then reconstruct the separate daily-as-of universe from
verified identity dates.

### Stage C — Merge BSE/live-missing identities

Run the same exact one-step instruments operation a second time. The pinned
step now merges recent Sina-discovered, non-TDX symbols such as `.BJ`. Require:

- non-zero current stock counts for SH, SZ, and BJ;
- unique canonical symbols;
- no live BSE symbol carrying a fabricated delist date;
- non-zero formal historical delisted count;
- no strategy eligibility filtering.

Failure to establish BSE coverage is `BLOCKED_ALL_A_UNIVERSE`, not a warning.

### Stage C2 — Active BSE metadata enrichment

The pinned merge adds non-TDX symbols after its ordinary EastMoney date
enrichment and leaves those rows with `name=None` and `list_date=None`.
Therefore the service runner must perform one thin, source-pinned enrichment:

1. Fetch the EastMoney clist once through the pinned client with fields
   `f12,f13,f14,f26` (code, market, name, list date).
2. Map rows only through pinned `clist_rows_to_symbols`; reject any noncanonical
   or cross-market identity.
3. Join only the active/recent `.BJ` set from the completed discovery receipt.
4. Require every effective-as-of active BJ stock to have a nonblank name and a
   valid `list_date <= R3_DAILY_AS_OF`; a symbol absent from or contradictory
   across the two sources is `BLOCKED_ALL_A_METADATA`.
5. Stage a complete instruments snapshot: existing non-BJ rows unchanged plus
   enriched BJ rows with `source=eastmoney`, `data_version=v1`, fresh
   `fetched_at`, and explicit enrichment receipt. Never stage a BJ-only partial
   snapshot into `compact_instruments`.
6. Wrap the staging file in matching service-ledger and manifest controller
   batches, compact only after success, then prove no active BJ row has null
   `name`/`list_date` or a fabricated `delist_date`.

`code`, board, and security-type projections not physically present in the
pinned schema are deterministic from canonical symbol/exchange/asset_type and
are frozen for the R8 query projection; R3 does not fork the upstream Parquet
schema merely to duplicate them.

### Stage D — Trading calendar foundation

Run a single pinned `JobEngine.run_job` containing only `trading_calendar`,
with `backfill=True`, `trade_date=R3_DAILY_AS_OF`, and
`config._backfill_start=R3_HISTORY_START`; compact only that run after every
batch resolves. The equivalent CLI shape is shown for review:

```text
cne backfill trading_calendar --config config/cnequity.toml \
  --start 2016-01-01 --end 2026-08-17
```

The pinned step intentionally ignores `_backfill_end` and extends calendar
coverage to `trade_date + 365 days`; this forward horizon is expected and must
be recorded rather than misreported as daily-bar scope drift. Require
successful compact, unique dates, all required columns, and a non-empty
trading-session set through `R3_DAILY_AS_OF`.

### Stage E — Dedicated delisted daily recovery

Do not call pinned `backfill_delisted_bars`: that helper unconditionally writes
`derived/delisting_events` when it recovers rows, crossing the R3 boundary.
Instead implement a narrow, service-owned recovery adapter composed only from
the pinned raw-bar adapters:

1. Build an immutable target set from complete Baostock formal-delisting
   identity plus completed issued-code discovery classified-delisted entries.
   Exclude `live_missing`, `never_issued`, and all currently active names.
   Compute an explicit `R3_DAILY_AS_OF` cutoff: a discovery terminal on or
   after the cut-off must re-check evaluation against the active security
   master; a terminal strictly before it is a delisted target. Classify every
   target as `RECOVERY_REQUIRED`, `EXPECTED_NO_DATA_BEFORE_WINDOW`, or
   `UNRESOLVED` for the exact daily window; hash the full set.
2. Route SH/SZ targets through pinned Baostock `fetch_delisted_bars`, which
   supplies RAW bars with volume in shares and amount in CNY. Route a BJ target
   only through pinned Sina `fetch_daily_bars_sina`, with the explicit amount
   limitation below. No target may silently change route.
3. For every bounded symbol chunk, create matching service-ledger and blocking
   manifest controller batches before fetching. Stage only `daily_bars` with
   pinned schema/provenance. Do not compute or write ending patterns,
   `delisting_events`, status, adjustment, or any other derived dataset.
4. Any in-window BJ delisted target must already have nonblank name and valid
   list/delist metadata from an approved identity source; otherwise stop
   `BLOCKED_HISTORICAL_BJ_METADATA` rather than inventing a name/date.
5. A transport failure or empty response for a target whose formal dates prove
   overlap stays `UNRESOLVED`. Retry only that exact target chunk, at most three
   unchanged attempts.
6. Compact only when both ledgers show every target recovered or explicitly
   expected-no-data. Produce an ASL-owned versioned coverage receipt containing
   window, target-set hash, per-route symbol/row/span hashes, expected-no-data,
   unresolved (required zero), compact run/batch IDs, and curated post-proof.
7. Verify the read-only pinned `delisted_coverage_report` independently after
   compact, but use the stricter ASL receipt as R3 completion authority.

This path is a thin orchestration of pinned source adapters, not a new market
lake or a copy of the upstream delisting-event subsystem. Do not run public
`cne delisted backfill`, `delisted repair`, or reconciliation with `--apply`.

### Stage F — Per-route RAW daily backfill without the generic step

Do not call the pinned generic `step_daily_bars` or its unbounded expected-date
logic. The controller fetches daily bars separately per exchange route and
keeps `config.failover_enabled=false` so no EastMoney/source backup is written
implicitly. Every staged daily row must fall inside its symbol's effective
`[max(list_date, R3_HISTORY_START), min(delist_date, R3_DAILY_AS_OF)]` span and
obey the pinned schema/provenance contract.

#### F1 — SH/SZ via TDX parallel workers

1. Build the effective active SH/SZ symbol set from the security master using
   verified list/delist dates; exclude Stage E delisted targets, `never_issued`,
   and `live_missing` from the fetch set.
2. Create one manifest run with `Manifest.start_run("r3_daily_bars", ...)` and
   build deterministic `BatchSpec` tuples
   `(batch_id, symbols, window_start, window_end)` from the pinned
   `_symbol_batch_id` convention and `config.batch_size`, one chunk per symbol
   slice. Record them in the service ledger.
3. Invoke pinned `fetch_daily_bars_parallel(config, symbols=[], window,
   run_id, batch_specs=chunks)` once. This writes a manifest worker batch per
   chunk, stages only TDX rows, and returns `had_error` plus every failed
   symbol. It does not trigger any EastMoney/Sina gap-fill.
4. For each failed batch, re-invoke the same pinned callable with only that
   recorded batch spec (deterministic `batch_id`), at most three unchanged
   attempts, and require strict decrease of the failed-symbol set. A batch
   whose symbols all prove `EXPECTED_NO_DATA` may be finalized with a success
   controller batch plus explicit evidence only after at least one
   `fetch_daily_bars_parallel` attempt proves they are absent.
5. Never call the pinned generic `step_daily_bars`, `JobEngine.run_job` for
   daily bars, or the pinned `_retry_run` path under Stage F; the controller
   owns every failed-batch re-run through the raw parallel callable above.

#### F2 — Non-TDX (mainly BJ) via Sina, bounded EastMoney fallback

1. Build the effective active non-TDX symbol set from the security master with
   verified metadata; the same exclusions apply.
2. Call pinned `fetch_bars_via_sina(config, symbols, window, run_id)` once for
   the full set under one blocking controller/ledger pair. Every row staged by
   that adapter gets `source=sina` and the documented null-amount limitation
   below.
3. Interpret the adapter result as partial, not complete: a symbol with failed
   fetch, empty response, or missing expected effective dates is an explicit
   retry scope. Retry each scope deterministically through the same exact
   pinned callable at most three unchanged attempts, recording attempt state.
4. If a scope still fails, fall back per symbol to pinned EastMoney
   `fetch_daily_bars` with an explicit service ledger/controller batch and
   `source=eastmoney` provenance; amount is retained when present. A same-PK
   Sina/EastMoney value conflict is `DATA_CONFLICT` and blocks the gate.
5. A symbol keeps the Sina rows when the fallback matches, or the fallback's
   rows when Sina has none; it never receives both. Compact only after both
   ledgers show every route complete and the run has zero incomplete manifest
   batches. Symbols that still report failure or an empty full-window response
   after every route are `UNEXPLAINED_MISSING` and block the gate; only a
   bounded, source-documented suspension explanation (interior gap with an
   observed in-window row) may stay `PENDING_R4_STATUS_EXPLANATION`.

#### F3 — Daily-bar result classification

The pinned adapters do not distinguish a suspended session from a source
outage. The controller records, per symbol, the exact observed effective-date
set and differences versus the trading calendar, then classifies uncovered
expected keys as `PENDING_R4_STATUS_EXPLANATION` when the symbol has an
in-window observed row and the gap is interior or bounded, and as
`EXPLAINED_MISSING` only for pre-list/effective-span exclusions. A symbol with
zero in-window rows after all routes is `UNEXPLAINED_MISSING` and blocks the
gate. The R3 gap map therefore never claims sessions are normal merely because
no bar row exists.

### Stage G — Read-only delisted coverage gate

After writers exit, call pinned
`delisted_coverage_report(config, R3_HISTORY_START, R3_DAILY_AS_OF, sample=20)`
directly. The equivalent CLI shape is:

```text
cne delisted coverage --config config/cnequity.toml \
  --start 2016-01-01 --end 2026-08-17 --sample 20
```

Require `verified=true`. This command must receive a before/after target-tree
snapshot; any change is a verifier defect and blocks R3.

## Task 4 — Daily quality, gap map, and `DAILY_READY` gate

Run the service-owned verifier only after no writer handle remains. It must
produce all of the following exact evidence.

### L0 structural gate

- only `instruments`, `trading_calendar`, and `daily_bars` contain R3 Parquet;
- `derived/delisting_events` and every other derived/raw dataset remain absent;
  the R3-safe Stage E adapter is tested to make no such write. Staging files retained by upstream after a
  successful compact are permitted only when every file maps to a terminal
  manifest run, a complete service-ledger scope, the exact compact input
  inventory/hash, a successful compact batch, and a curated PK/provenance
  post-proof; its dataset must be one of the three R3 datasets. Staging remains
  non-authoritative and content-unchanged, is inventoried and hashed, and is
  not deleted. Orphan, incomplete, uncompactable, or non-R3 staging blocks the
  gate;
- registered schema and required columns match;
- duplicate `instruments.symbol`, calendar date, and
  `(daily_bars.symbol, trade_date)` counts are zero;
- every daily row is inside the frozen window and has canonical symbol,
  `source`, `data_version`, and UTC `fetched_at`;
- every daily `data_version` is the pinned v2 contract;
- no adjusted-price column/series is stored as authoritative daily data.

### L1 daily values and units

- prices are finite and positive for traded rows;
- `low <= open/close <= high`, `low <= high`;
- volume is finite and non-negative; amount is null or finite/non-negative;
- for every source with at least 200 positive-volume rows and non-null amount,
  median `amount / close / volume` must be within `[0.8, 1.25]`, proving shares
  and CNY rather than a 100x lot error;
- null amount coverage is grouped by source and date. Only a pinned, documented
  source limitation may be `EXPLAINED_MISSING`; any new source or semantic is
  `DATA_CONFLICT` and blocks the gate;
- `amount IS NULL` is permitted only when `source=sina`. The receipt must split
  it by exchange, active versus delisted ownership, date, rows, symbols, and
  complete PK hash. Any null amount from a non-Sina source blocks
  `DAILY_READY`. Sina null is recorded as
  `EXPLAINED_MISSING_SOURCE_FIELD`; it is never described as a verified CNY
  value or filled from another row;
- Sina volume is a separate evidence class: the pinned adapter/source contract
  says it emits shares, but the amount-ratio identity cannot validate those
  rows. R3 must independently cross-check a bounded, deterministic sample
  against pinned EastMoney historical kline for at least 20 BJ symbols and 20
  overlapping traded dates per available symbol. Require identical PK, close
  within 1 bp, and volume ratio within `[0.99, 1.01]`; source disagreement or an
  insufficient sample blocks with `BLOCKED_SINA_UNIT_EVIDENCE`. Cross-check
  rows are evidence only and are not written into canonical daily bars;
- source and row counts, min/max dates, null counts, and per-source unit results
  are hashed into the receipt.

### Universe and survivorship gate

- the security-master observation receipt is explicit and is not mislabeled as
  a historical as-of snapshot;
- effective active stock/CDR universe as of `R3_DAILY_AS_OF`, reconstructed
  from verified list/delist dates, has non-zero SH, SZ, and BJ counts;
- every effective active BJ instrument has nonblank `name` and non-null
  `list_date`, with the Stage C2 provenance receipt;
- every active instrument has at least one daily row on or after its effective
  in-window list date;
- formal historical delisted instruments are non-zero and the dedicated
  recovery receipt verifies the entire R3 window;
- at least one historical delisted stock with in-window history has valid RAW
  daily rows and can be selected by canonical symbol;
- no active name is excluded based on board, ST, liquidity, or strategy use.

### Coverage and gap map

For each stock and each exchange session inside its effective
`[max(list_date, history_start), min(delist_date, R3_DAILY_AS_OF)]` span, classify:

```text
EXPECTED
OBSERVED
EXPLAINED_MISSING
UNEXPLAINED_MISSING
PENDING_R4_STATUS_EXPLANATION
```

R3 has no authority to invent suspension/ST facts. A missing key that requires
historical status is therefore `PENDING_R4_STATUS_EXPLANATION`, not silently
explained. The gap map must include counts and bounded samples per exchange,
symbol, month, and source, plus a hash of the complete machine-readable set.

`DAILY_READY` requires:

1. zero structural, PK, OHLC, provenance, date-bound, and unit errors;
2. full SH/SZ/BJ identity presence and complete delisted coverage evidence;
3. zero active instrument with no in-window daily history;
4. newest observed market date exactly `2026-08-17`;
5. all missing expected keys explicitly classified and hashed;
6. no unexplained source/provider conflict;
7. no non-R3 dataset, fact, adjusted series, or publication state;
8. `LATEST_GOOD_AS_OF` remains `NOT_PUBLISHED`.
9. Sina null-amount scope and separate volume-evidence limitation are explicit
   in the contract, machine receipt, gap summary, state carry-forward, and
   author report.

`PENDING_R4_STATUS_EXPLANATION` is permitted only as explicit carry-forward and
cannot be counted as `EXPLAINED_MISSING` until R4 proves it. The report must not
claim session-perfect coverage from the absence of a status dataset.

Write the detailed JSON receipts atomically under `meta/asl/r3/`. Commit only a
bounded Markdown summary with hashes, aggregate counts, samples, exact command
receipts, and blockers; do not add market data or huge machine output to Git.

## Task 5 — Author verification, commit, and exact review publication

### 5.1 Verification

After acquisition and quality gates:

```text
uv lock --check --offline
uv run --frozen --no-sync --offline cne --version
uv run --frozen --no-sync --offline cne config validate --config config/cnequity.toml
PYTHONDONTWRITEBYTECODE=1 uv run --frozen --no-sync --offline \
  pytest -q -p no:cacheprovider \
  tests/test_r3_daily_foundation.py tests/test_r2_baseline_contract.py \
  tests/test_project_docs_contract.py
uv run --frozen --no-sync --offline python tools/verify_r3_daily_foundation.py \
  --config config/cnequity.toml --history-start 2016-01-01 \
  --as-of 2026-08-17 --json
git diff --check
```

Hash `uv.lock`, config, all R3 receipts, and the complete target inventory
before/after read-only verification. Require equality. Do not run upstream
`cne audit` because its enabled-source path can perform network cross-checks and
its full-lake contract includes later-phase datasets.

### 5.2 Author report and conservative state

Create `reports/audits/R3_DAILY_FOUNDATION_AUTHOR_REPORT.md` containing at
least:

```text
AUTHOR_STATUS
SPEC_VERSION
BASE_HEAD
HEAD_BEFORE_FINAL_COMMIT
PLAN_AUDIT_SHA
RUNTIME_PIN
DATA_ROOT
R3_WINDOW
UNIVERSE_OBSERVED_AT
UNIVERSE_REFERENCE_DATE
UNIVERSE_CATALOG_AND_METADATA_HASHES
FULL_MARKET_AUTHORIZED
LEGACY_ROWS_MIGRATED
COMMAND_RECEIPTS
SERVICE_BATCH_LEDGER
COMPACT_INPUT_AND_CURATED_POST_PROOF
INSTRUMENT_COUNTS_BY_EXCHANGE
BSE_METADATA_STATUS
DELISTED_COVERAGE
DAILY_ROWS_AND_DATES
PK_AND_VALUE_QUALITY
UNIT_EVIDENCE
SINA_AMOUNT_AND_VOLUME_LIMITATION
GAP_CLASSIFICATION
NON_R3_DATASETS
LATEST_GOOD_AS_OF
TESTS
BLOCKERS
REPORT_PATH
```

If every gate passes, author status may be `PASS`, but it remains author-only.
Update `PROJECT_STATE.md` to keep:

```text
CURRENT_PHASE: R3 — DAILY FOUNDATION
R3_EXECUTION: AUTHOR_VERIFICATION_COMPLETE
daily: DAILY_READY — AUTHOR_ONLY
LATEST_GOOD_AS_OF: NOT_PUBLISHED
NEXT_ACTION: Independent audit of the exact pushed R3 review commit.
Do not execute R4 before R3 AUDIT_PASS.
```

All later datasets remain `NOT_BUILT`. Record `HEAD: SELF — commit containing
this PROJECT_STATE` and never embed a self-referential final SHA in the file.

### 5.3 Commit and push

Commit only planned R3 repository files. Suggested final message:

```text
data: finalize R3 daily foundation
```

Push non-force to:

```text
codex/r3-daily-foundation-v01
```

Verify local and remote 40-character SHAs are identical. Do not update main and
do not execute R4.

## Independent audit gate

An independent reviewer of the exact pushed commit must verify:

- plan authority and exact diff scope;
- runtime/config hashes and actual installed Git pin;
- command receipts and manifest lineage;
- target-root inventory and no legacy access evidence;
- full SH/SZ/BJ plus delisted universe evidence;
- current universe-observation versus frozen daily-as-of separation;
- complete active-BJ name/list-date metadata and its provenance;
- bounded full daily scans for schema, PK, OHLC, units, provenance, dates, and
  coverage classifications;
- service-ledger/manifest/compact input-to-curated lineage for every direct and
  fallback staging file;
- exact Sina null-amount scope and independent volume evidence;
- no R4+ dataset, fact, publish state, query path, or strategy semantics;
- targeted tests and independent read-only verifier run;
- local/remote exact SHA equality.

Only `R3 AUDIT_PASS` may authorize a separate administrative R3→R4 activation
commit. Audit rejection must preserve data and receipts; fixes require a
bounded corrective plan and must never start with cleanup.

## Exit gate

R3 exits only when all are true:

1. The exact R3 implementation plan had independent `AUDIT_PASS` before data
   execution.
2. No legacy root was accessed and zero legacy rows were migrated.
3. The pinned runtime alone built the authorized R3 datasets in the clean root.
4. SH/SZ/BJ current identities and historical delisted identities are present;
   active BJ name/list-date metadata is complete and source-traceable.
5. Multi-year RAW daily bars cover the frozen window through `R3_DAILY_AS_OF`.
6. Schema, PK, OHLC, volume/amount, source, data-version, and fetched-at gates
   pass with explicit Sina amount/volume limitations.
7. Delisted recovery is receipt-complete and verified.
8. Every daily gap is explicit; R4-dependent status explanations remain
   pending rather than invented.
9. No `delisting_events` or other later-phase dataset/fact/query/publish/
   strategy work occurred.
10. `DAILY_READY` is recorded as author-only, exact review SHA is pushed, and
    independent audit subsequently returns `AUDIT_PASS`.

This plan stops at the R3 exact-commit audit gate. It never authorizes R4.
