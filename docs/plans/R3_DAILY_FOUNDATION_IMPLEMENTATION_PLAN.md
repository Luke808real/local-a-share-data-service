# R3 DAILY FOUNDATION IMPLEMENTATION PLAN

**TASK_CONTRACT:** `R3-DAILY-FOUNDATION-V01`  
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

## Frozen execution window

- `R3_HISTORY_START = 2016-01-01`
- `R3_AS_OF = 2026-08-17`
- All date bounds are inclusive.
- `2026-08-17` is deliberately a fully settled historical session relative to
  the plan date. No run may silently substitute wall-clock today.
- Daily prices are RAW/unadjusted. `daily_bars.volume` is shares and
  `daily_bars.amount` is CNY when present, per the pinned `v2` schema.
- Missing amount stays null with explicit source/coverage evidence; it is never
  rewritten as zero.

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
- resumable discovery progress parsing and no-progress blocking;
- manifest/run receipt parsing;
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
  `JobEngine._retry_run`, `JobEngine.run_step`, `discover_delisted`,
  `backfill_delisted_bars`, and `delisted_coverage_report` before the first
  writer call; unexpected upstream drift is `RUNTIME_CONTRACT_DRIFT`;
- stream logs and progress; never swallow or reinterpret a non-zero exit;
- resume only the documented stage or exact failed run; never start the whole
  phase again merely because one batch failed;
- treat `warning`, unresolved scope, partial failure, zero progress, malformed
  JSON, unexpected dataset, or missing compact receipt as non-success;
- never clean staging. Preserved staged data and manifest evidence are inputs to
  an explicit retry, not permission for deletion;
- call pinned `_retry_run(..., auto_finalize=False)` only for the exact failed
  run, then call only the `compact` step after all expected batches resolve.
  It must never enter the pinned generic finalize chain;
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
- full-universe and delisted evidence semantics;
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
- `R3_HISTORY_START` and `R3_AS_OF` are trading-window valid and the end session
  is final;
- every exact CLI subcommand and pinned source path has been statically approved;
- legacy paths appear only as denylist strings and are not accessed.

Any mismatch stops R3 before data execution. Commit any test/contract correction
only if it stays within this audited plan; otherwise return
`DESIGN_DECISION_REQUIRED` and seek a revised plan audit.

## Task 3 — Full-market daily acquisition

This task is the only full-market authorization in R3. Execute through the
service-owned runner and never by ad hoc shell commands.

### Stage A — Formal security master seed

Run a single pinned `JobEngine.run_job` containing only `instruments`, with
`backfill=True`, `trade_date=R3_AS_OF`, and `finalize_run=False`; after all
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

Repeat until returned `remaining = 0`. Each chunk is a full-market operation but
bounded in time and checkpointed by upstream every 100 probes. A failed probe
remains pending. Require monotonic progress; after three consecutive chunks
with no decrease in remaining scope, stop `BLOCKED_SOURCE_DISCOVERY` with all
evidence preserved. Never relabel a failed probe as never-issued.

### Stage C — Merge BSE/live-missing identities

Run the same exact one-step instruments operation a second time. The pinned
step now merges recent Sina-discovered, non-TDX symbols such as `.BJ`. Require:

- non-zero current stock counts for SH, SZ, and BJ;
- unique canonical symbols;
- no live BSE symbol carrying a fabricated delist date;
- non-zero formal historical delisted count;
- no strategy eligibility filtering.

Failure to establish BSE coverage is `BLOCKED_ALL_A_UNIVERSE`, not a warning.

### Stage D — Trading calendar foundation

Run a single pinned `JobEngine.run_job` containing only `trading_calendar`,
with `backfill=True`, `trade_date=R3_AS_OF`, and
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
trading-session set through `R3_AS_OF`.

### Stage E — Dedicated delisted daily recovery

The public command has no `--end` option and would silently use wall-clock
today. Therefore the runner must call pinned
`backfill_delisted_bars(config, run_id, R3_HISTORY_START,
end=R3_AS_OF)` directly, then run only the pinned `compact` step and finish the
manifest run. The broader CLI shape below is documentation only and must not
be executed in R3:

```text
cne delisted backfill --config config/cnequity.toml --since 2016-01-01
```

Require function success, successful compact, zero unresolved recovery targets,
and a complete versioned coverage receipt spanning the R3 window. Retry the
same checkpointed scope only. Do not run `delisted repair` or reconciliation
with `--apply` in R3.

### Stage F — Generic full-universe RAW daily backfill

Run a single pinned `JobEngine.run_job` containing only `daily_bars`, with
`backfill=True`, `trade_date=R3_AS_OF`, explicit `_backfill_start` and
`_backfill_end`, `workers=1`, and `finalize_run=False`. Run only `compact` after
every batch resolves. The equivalent CLI shape is:

```text
cne backfill daily_bars --config config/cnequity.toml \
  --start 2016-01-01 --end 2026-08-17 --workers 1
```

This includes SH/SZ through TDX and BSE/non-TDX symbols through the pinned Sina
fallback. The dedicated receipt from Stage E must satisfy delegated delisted
ownership. Require command status `success` and successful compact.

If a manifest run has retryable failed/warning batches, the runner may invoke
only the exact pinned internal recovery operation:

```text
JobEngine._retry_run(<exact recorded run_id>, R3_AS_OF, auto_finalize=False)
```

After zero incomplete batches, the runner runs only `compact` and finishes the
run. Retry at most three controller attempts per unchanged failed scope. The
runner must prove decreasing failed scope or stop. It must never launch a
second broad daily backfill while an exact run remains recoverable, and it must
prove that no adjustment/industry/audit batch was created.

### Stage G — Read-only delisted coverage gate

After writers exit, call pinned
`delisted_coverage_report(config, R3_HISTORY_START, R3_AS_OF, sample=20)`
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
- derived/raw contain no R3 payload. Staging files retained by upstream after a
  successful compact are permitted only when every file maps to a terminal
  manifest run with a successful compact batch and its dataset is one of the
  three R3 datasets. They remain non-authoritative, are inventoried and hashed,
  and are not deleted. Orphan, incomplete, uncompactable, or non-R3 staging
  blocks the gate;
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
- source and row counts, min/max dates, null counts, and per-source unit results
  are hashed into the receipt.

### Universe and survivorship gate

- active stock/CDR universe as of `R3_AS_OF` has non-zero SH, SZ, and BJ counts;
- every active instrument has at least one daily row on or after its effective
  in-window list date;
- formal historical delisted instruments are non-zero and the dedicated
  recovery receipt verifies the entire R3 window;
- at least one historical delisted stock with in-window history has valid RAW
  daily rows and can be selected by canonical symbol;
- no active name is excluded based on board, ST, liquidity, or strategy use.

### Coverage and gap map

For each stock and each exchange session inside its effective
`[max(list_date, history_start), min(delist_date, as_of)]` span, classify:

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
FULL_MARKET_AUTHORIZED
LEGACY_ROWS_MIGRATED
COMMAND_RECEIPTS
INSTRUMENT_COUNTS_BY_EXCHANGE
DELISTED_COVERAGE
DAILY_ROWS_AND_DATES
PK_AND_VALUE_QUALITY
UNIT_EVIDENCE
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
- bounded full daily scans for schema, PK, OHLC, units, provenance, dates, and
  coverage classifications;
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
4. SH/SZ/BJ current identities and historical delisted identities are present.
5. Multi-year RAW daily bars cover the frozen window through `R3_AS_OF`.
6. Schema, PK, OHLC, volume/amount, source, data-version, and fetched-at gates
   pass with explicit missing semantics.
7. Delisted recovery is receipt-complete and verified.
8. Every daily gap is explicit; R4-dependent status explanations remain
   pending rather than invented.
9. No later-phase dataset/fact/query/publish/strategy work occurred.
10. `DAILY_READY` is recorded as author-only, exact review SHA is pushed, and
    independent audit subsequently returns `AUDIT_PASS`.

This plan stops at the R3 exact-commit audit gate. It never authorizes R4.
