# R4A0 CORPORATE_ACTION AVAILABILITY GATE — AUTHOR REPORT

STATUS: AUTHOR-ONLY (code-author status; independent Sol audit required)
DATE: 2026-08-20
BRANCH: codex/r4a0-corporate-actions-availability-gate-v01
BASE_HEAD: ab150e171e4aae69d0e48b055b994c10f12abc0b
UPSTREAM_CNEQUITY: v0.7.2 @ a18ee0484dfb0801650175471724def3228b8a17
DATA_AS_OF: 2026-08-17

## 0. Verdict

**R4A0_READY = false — FAIL CLOSED.**

The authoritative real root does NOT yet contain a built
`corporate_actions` curated dataset, so R4A historical preclose derivation
must NOT start. No bootstrap/backfill was performed (correctly out of scope
and explicitly forbidden); the next action is the Sol-decided bounded
bootstrap `R4A0_CORPORATE_ACTION_BOUNDED_BOOTSTRAP`.

## 1. Question answered

> 当前 authoritative real root 是否已经拥有足够可靠的 SH/SZ
> corporate_actions 数据，可作为 R4A historical preclose derivation 的输入？

**NEIN — 未拥有。** FAIL-CLOSED. EXIST 门失败（数据集未构建）。

## 2. Real-root gate evidence (read-only, 2026-08-20)

Data root: `/Users/luke808/AI/local-a-share-data-service-data`

| Gate | Status | Evidence |
|---|---|---|
| EXISTS | FAIL | `curated/corporate_actions/` ABSENT; zero parquet anywhere under the root |
| SCHEMA | NOT_AVAILABLE | no dataset to scan; required fields recorded from pinned CNEquity contract |
| SCOPE | NOT_AVAILABLE | no dataset to measure (BJ deferred by policy) |
| COVERAGE | UNKNOWN_PARTIAL | no rows, no ingestion run, no watermark |
| UNIQUENESS | NOT_AVAILABLE | no dataset to measure |
| PROVENANCE | NOT_AVAILABLE | no manifest run, no watermark, no parquet lineage |

Reported:

```text
ROW_COUNT            0
MIN_EX_DATE          None
MAX_EX_DATE          None
SH_ROWS / SZ_ROWS / OTHER_ROWS   0 / 0 / 0
DUPLICATE_ACTION_N   0
UNRESOLVED_N         0
manifest corporate_run_success   false
manifest corporate_watermark     false
manifest_wal_pending             false
```

BLOCKER: `CORPORATE_ACTIONS_DATASET_NOT_BUILT`
MISSING_CAPABILITY: `corporate_actions curated dataset (no parquet under
curated/corporate_actions/) and no corporate_actions ingestion run`
BOUNDED_NEXT_ACTION: `R4A0_CORPORATE_ACTION_BOUNDED_BOOTSTRAP (Sol decision
required)`

Corroborating read-only observations:

- `meta/manifest.db` contains only the R3 runs (`r3_instruments`,
  `r3_trading_calendar`, `r3_delisted_daily`, R3 daily batches); no
  `corporate_actions` ingestion run exists.
- `meta/state/corporate_actions.json` watermark does not exist; only the
  layout placeholder `meta/state/corporate_actions.lock` (0 bytes) exists.
- DuckDB hosts a metadata VIEW `corporate_actions` only; the underlying
  parquet store is absent and the view holds zero rows.
- DuckDB/manifest layout view schemas match the pinned CNEquity contract
  exactly (contract check: match=true; direct_url vcs git commit
  a18ee0484dfb0801650175471724def3228b8a17).

## 3. Pinned CNEquity contract used (not guessed)

From `rootSunc/CNEquity v0.7.2 @ a18ee0484d...` (installed package verified):

- Dataset: `corporate_actions`; layer curated; partition `ex_date` (year);
  primary source `tdx_protocol`, backup `eastmoney`.
- Schema (R4A preclose-relevant): symbol, ex_date, action_type,
  cash_dividend, bonus_ratio, transfer_ratio, allotment_ratio,
  allotment_price, source, data_version, fetched_at.
- Primary key: `(symbol, ex_date, action_type)` — same symbol/ex_date with
  multiple action types is a legal multi-component record, NOT a duplicate.

## 4. Implementation surface

- `src/ashare_data/r4a0_corporate_actions_gate.py` — read-only gate
  (EXISTS / SCHEMA / SCOPE / COVERAGE / UNIQUENESS / PROVENANCE; event-data
  coverage semantics; immutable SQLite manifest read).
- `tools/verify_r4a0_corporate_actions_gate.py` — read-only CLI
  (`--config`, `--contract-check`).
- `tests/test_r4a0_corporate_actions_gate.py` — 7 targeted scenarios.

No code under `src/`/`tests/`/`config/` other than the above was touched.

## 5. Tests

Targeted tests, 7 scenarios, all PASS (offline, frozen venv):

1. valid dataset -> PASS
2. dataset missing -> FAIL (EXISTS)
3. required schema missing -> FAIL (SCHEMA)
4. provenance missing -> FAIL (PROVENANCE, UNRESOLVED_N>0)
5. true duplicate action (same full PK) -> FAIL (UNIQUENESS)
6. legal same-date multi-component action -> NOT flagged duplicate (PASS)
7. coverage UNKNOWN/PARTIAL -> READY=false

Result: `7 passed in 0.13s`.

## 6. Read-only guarantee

- Gate never writes to the data root; verified by identical pre/post
  `manifest.db` SHA-256 and unchanged `corporate_actions.lock` stat.
- Manifest read uses SQLite `immutable=1` (fail-closed on non-empty WAL),
  so it does not create/refresh `-wal`/`-shm` sidecars; verified unchanged
  mtime before/after re-run.
- No provider call, no bootstrap/backfill, no R4A+ execution.

NOTE: opening the WAL-mode manifest with the initial non-immutable read
connection refreshed the runtime `manifest.db-wal` (0 B) / `manifest.db-shm`
(32 KB) sidecars on the real root (R2 contract classifies these as runtime
state, not market data; they are not git-tracked). The gate now uses
immutable reads and no longer touches them. The sidecars were left in place;
the user may request their removal.

## 7. Verification block

```text
CODE_FILES_CHANGED          2   (src/ashare_data/r4a0_corporate_actions_gate.py,
                                tools/verify_r4a0_corporate_actions_gate.py)
TEST_FILES_CHANGED          1
REPORT_FILES_CHANGED        1
MARKET_DATA_CHANGED         NO
NETWORK_PROVIDER_DATA_FETCH 0
REAL_ROOT_WRITE             NO (runtime -wal/-shm sidecar mtime touched once by
                             the pre-fix read; fixed; no lasting sidecar writes)
git diff --check            CLEAN
```

Frozen status lines:

```text
R3_SHSZ_CLOSEOUT=FROZEN
R4_PLAN_V01_2_SOL_REAUDIT=PASS
R4A_PRECLOSE_EXECUTION=FORBIDDEN_UNTIL_R4A0_PASS
BJ_EXTENSION=DEFERRED
DAILY_READY=FALSE
PRODUCTION=false
FORWARD=false
TRADEPLAN=false
```

This report is author status only. Independent Sol audit of the exact pushed
commit is required before any R4A activity.

---

## 8. CORRECTNESS FIX V01 (post code re-audit)

Two fail-closed correctness bugs found by the independent Sol code audit were
fixed (commit on branch `codex/r4a0-corporate-actions-availability-gate-v01`):

### 8.1 Pinned-upstream check (was: pin printed, not enforced)

`tools/verify_r4a0_corporate_actions_gate.py` `contract_check` now explicitly
verifies the installed CNEquity `direct_url.vcs_info.commit_id` equals the
pinned upstream SHA, and that comparison participates in `contract_check.match`
(schema AND source AND pin). A pin mismatch exits non-zero and R4A0 cannot
PASS.

Real-root result (2026-08-20 re-run):

```text
PIN_EXPECTED  a18ee0484dfb0801650175471724def3228b8a17
PIN_ACTUAL    a18ee0484dfb0801650175471724def3228b8a17
PIN_MATCH     true
```

### 8.2 Coverage hard fix (was: any successful run -> COVERAGE_PASS)

That rule is deleted. `corporate_actions` is a sparse event dataset; row
min/max cannot prove historical completeness. `COVERAGE_PASS` now requires
authoritative ingestion evidence whose covered window actually spans the
requested window, compared strictly:

```text
COVERAGE_PROOF_SEMANTIC  INGESTION_WINDOW_OR_WATERMARK_MUST_COVER_REQUESTED_WINDOW
REQUESTED_WINDOW         2016-01-01 .. 2026-08-17
covered_start <= 2016-01-01  AND  covered_end >= 2026-08-17
```

`run_gate(window_start, window_end)` parameters now participate in the
decision. Success without a window, or a partial window, is UNKNOWN_PARTIAL
and never PASS.

Real-root result (unchanged, still fail-closed):

```text
COVERED_WINDOW   None  (dataset absent)
COVERAGE_STATUS  UNKNOWN_PARTIAL
PARTIAL_RUN_REJECTED  false (no dataset to be partial)
R4A0_READY       false
BLOCKER          CORPORATE_ACTIONS_DATASET_NOT_BUILT
```

### 8.3 Targeted tests

Original 7 scenarios retained; 4 new scenarios added (all pass):

```text
A  successful run with window only 2026-08-01..2026-08-17  -> UNKNOWN_PARTIAL, READY=false
B  successful run explicitly covering 2016-01-01..2026-08-17 -> COVERAGE_PASS
C  watermark start after 2016-01-01                          -> UNKNOWN_PARTIAL, READY=false
D  wrong CNEquity pin                                        -> contract_check FAIL (PIN_MATCH=false)

TARGETED_TESTS  11 passed
```

The gate was not weakened to make tests pass; the new tests encode the hard
fail-closed semantics.

### 8.4 Re-run guarantee

Only the READ-ONLY R4A0 gate was re-run on the real root. No bootstrap, no
backfill, no provider fetch, no R4A execution, no sidecar cleanup; manifest
SQLite reads remain `immutable=1` (verified: `-wal`/`-shm` mtime unchanged
before/after re-run).
