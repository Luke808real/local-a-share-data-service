# R4A0 RESUMABLE FULL-BOOTSTRAP ORCHESTRATOR — AUTHOR REPORT

STATUS: ORCHESTRATOR_BUILT_AUTHOR_ONLY (independent Sol audit pending)
DATE: 2026-08-20
BRANCH: codex/r4a0-resumable-full-bootstrap-v01
BASE_HEAD: 6bb21580643f851530a66298808d2c86c698a6b9
UPSTREAM_CNEQUITY: v0.7.2 @ a18ee0484dfb0801650175471724def3228b8a17

## 1. Deliverable

A thin resumable orchestrator that runs the remaining corporate_actions
coverage in deterministic 24-symbol chunks, REUSING the audited bounded
adapter (`run_bounded_pilot`) as the ONLY execution primitive:

```text
src/ashare_data/r4a0_full_bootstrap_orchestrator.py   orchestrator
tools/run_r4a0_full_bootstrap_plan.py                 CLI (default --dry-run)
tests/test_r4a0_full_bootstrap_orchestrator.py        25 offline tests
```

No new downloader, no direct TDX/EastMoney call, no pinned-upstream change, no
broadening of `MAX_SYMBOL_N=24`.

## 2. Manifest is the resume authority

`FULLY_COVERED_SYMBOLS` is derived only from existing manifest receipt
evidence (`corporate_actions_chunk` + status=success + exact window
2016-01-01..2026-08-17). failed / warning / wrong-window receipts never count;
zero-event successful receipts DO count (query coverage, not row presence).
No separate local "completed" truth, no checkpoint file as authority.

Real-root dry-run confirmed the pilot's 24 symbols are recognized as covered
(`COVERED_SYMBOL_N=24`), so the orchestrator will not re-request them.

## 3. Deterministic plan

Remaining symbols are canonical-sorted; `CHUNK_SIZE=24` fixed; last chunk may
be < 24. Same manifest state => same `CHUNK_PLAN_HASH` (tested). Each chunk
carries index, symbol_n, first/last symbol, symbol_hash.

## 4. Real-config dry-run (2026-08-20)

```text
STATUS READY / DRY_RUN_STATUS OK
EXPECTED_SYMBOL_N 5456  EXPECTED_SYMBOL_HASH 2b1e7202... (match)
COVERED_SYMBOL_N  24     (from manifest receipts)
REMAINING_SYMBOL_N 5432
CHUNK_SIZE 24  CHUNK_COUNT 227   (226*24 + 8)
CHUNK_PLAN_HASH 9d34dd4b214945948edee3a1d584b31c7c5124c92545e354b9af41c049da6348
FIRST_3_CHUNKS [1,2,3]  LAST_3_CHUNKS [225,226,227]
MANIFEST_IS_RESUME_AUTHORITY true
NETWORK_PROVIDER_DATA_FETCH NO  MANIFEST_WRITE NO  REAL_ROOT_WRITE NO
```

## 5. Execution policy (future mode; NOT run this task)

Chunk sequence is deterministic; a chunk advances only when the adapter result
is PILOT_COMPLETE AND receipt_post_check STATUS OK AND CONFIG_INTEGRITY_STATUS
OK. PILOT_INCOMPLETE / EXECUTION_ERROR / WRITE_BOUNDARY_BREACH / receipt
UNKNOWN·MISMATCH / config UNKNOWN·CHANGED => STOP IMMEDIATELY. No automatic
retry, no skipping. Per-chunk progress receipts record coverage delta, but the
manifest remains the coverage authority. Formal gate runs at start, every N
chunks, and at the end; `FULL_BOOTSTRAP_COMPLETE` requires the final gate
`R4A0_READY=true`; otherwise `FULL_BOOTSTRAP_INCOMPLETE`. No auto R4A entry.

Real `--exec` is refused by the CLI
(`FULL_BOOTSTRAP_EXECUTION_FORBIDDEN`) until Sol audits this commit.

## 6. Tests

25 offline targeted tests (real pinned Manifest on tmp db for resume
authority; injected identities; fake adapter; fake formal gate): identity
accepted, pilot receipts -> covered 24, remaining 5432, failed/warning/wrong-
window not covered, zero-event covered, deterministic sorted plan, stable plan
hash, final chunk <24, no chunk >24, covered never in plan, unknown receipt
symbol fail-closed, identity mismatch fail-closed, dry-run zero
adapter/provider/write, exec reachable under fake adapter, chunk advance,
incomplete/receipt/config stop, restart recomputes from manifest, manifest
overrides stale progress, no direct provider import, final gate
false/true complete. Existing `test_r4a0_bounded_adapter.py` +
`test_r4a0_corporate_actions_gate.py` re-run clean.

```text
TARGETED_TESTS  25 (orchestrator) + 65 (adapter+gate) = 90 passed
GIT_DIFF_CHECK  CLEAN
```

## 7. Fixed state

```text
R3_SHSZ_CLOSEOUT=FROZEN
R4A0_GATE_CODE_AUDIT=PASS
R4A0_BOUNDED_ADAPTER_CODE_AUDIT=PASS
R4A0_REAL_PILOT_AUDIT=PASS
R4A0_READY=false
FULL_BOOTSTRAP_EXECUTION=FORBIDDEN_PENDING_SOL_ORCHESTRATOR_AUDIT
R4A_PRECLOSE_EXECUTION=FORBIDDEN
DAILY_READY=FALSE  BJ_EXTENSION=DEFERRED
PRODUCTION=false FORWARD=false TRADEPLAN=false
```

No parquet / manifest.db / cache / credentials uploaded.

---

## 8. EXECUTION HARDENING V01 (post re-review of 8f114de)

The production-execution blockers from the independent Sol re-review were
closed:

### 8.1 Formal CLI exec mode

`--exec` is reachable and calls `run_full_bootstrap(..., dry_run=False)`; the
hard-coded `FULL_BOOTSTRAP_EXECUTION_FORBIDDEN` rejection was removed.
`--dry-run`/`--exec` are a mutually-exclusive group and the default is
dry-run. CLI tests: default→dry-run, `--dry-run`→dry-run, `--exec`→
dry_run=False with the orchestrator invoked once, conflict→exit 2.

### 8.2 Real formal gate wired

The production default gate is the audited `r4a0_corporate_actions_gate.run_gate`
called with `root`, `expected_identity_n=5456`,
`expected_identity_hash=2b1e7202...`, and the frozen window. Dependency
injection is test-only; gate execution failure is FAIL CLOSED
(`GATE_EXECUTION_FAILURE`).

### 8.3 START / PERIODIC / FINAL gates

Real mode runs START gate → chunks → PERIODIC gate every `gate_every` (10)
successful chunks → FINAL gate.

- START: `R4A0_READY=false` allowed only for the expected coverage-incomplete
  blocker; identity/schema/scope/uniqueness/provenance correctness blockers
  stop before any adapter call (`START_GATE_FAILURE`).
- PERIODIC: new correctness blockers stop (`PERIODIC_GATE_FAILURE`); READY=true
  is not required mid-run.
- FINAL: `R4A0_READY=true` ⇒ `FULL_BOOTSTRAP_COMPLETE=true`; otherwise
  `FULL_BOOTSTRAP_INCOMPLETE` + blocker.

### 8.4 Zero-remaining resume

If remaining=0 in real execution the orchestrator runs the FINAL gate directly
with zero adapter calls: gate true ⇒ COMPLETE, gate false ⇒ INCOMPLETE
(tested both). Dry-run still reports the (empty) plan.

### 8.5 Real-mode telemetry

Real execution reports `MANIFEST_WRITE=YES`, `REAL_ROOT_WRITE=YES`,
`NETWORK_PROVIDER_DATA_FETCH=UNKNOWN` (no exact provider telemetry),
`NETWORK_PROVIDER_REQUEST_COUNT=UNVERIFIED`. Chunk `PROVIDER_STEP_ENTERED`
values are aggregated; request counts are never fabricated. Dry-run keeps
NO/NO/NO.

### 8.6 Full-run write boundary

Before/after inventory hash over protected R3 datasets
(curated+staging daily_bars / instruments / trading_calendar; path+size+
mtime_ns, no content SHA) and config SHA must match; any change ⇒
`WRITE_BOUNDARY_BREACH` and `FULL_BOOTSTRAP_COMPLETE=false`. Allowed changes:
corporate_actions + manifest/runtime metadata.

### 8.7 Coverage alignment

`compute_covered_symbols` now requires `dataset == "corporate_actions"` and
reuses the formal gate's per-symbol union semantics
(`merge_intervals`/`gaps_in_window`): a symbol is covered when its SUCCESSFUL
receipt intervals' union contiguously covers the window — equal to the gate, so
it can neither falsely skip nor falsely claim. A non-corporate dataset receipt
is never coverage.

### 8.8 WAL / manifest read failure

`load_chunk_receipts` failures (WAL pending / other) return a structured
`MANIFEST_READ_FAILURE` status instead of a traceback; no sidecar cleanup.

### 8.9 Progress

Per-chunk progress entries are retained; they are
`NON_AUTHORITY_DIAGNOSTIC_ONLY`. The manifest remains the sole coverage
authority.

### 8.10 Tests and real-config dry-run (2026-08-20)

`tests/test_r4a0_full_bootstrap_orchestrator.py` grew to **40 targeted tests**
(prior 25 + CLI 4 + production-gate wired + start-gate blocker + periodic
every-10 + periodic failure + zero-remaining true/false + real telemetry +
protected-mutation boundary + config-mutation boundary + wrong-dataset filter +
WAL-pending fail-closed). Existing adapter (42) + gate (23) re-run clean.
Total: **105 passed**.

Real-config `--dry-run` (unchanged):

```text
EXPECTED 5456 / COVERED 24 / REMAINING 5432 / CHUNK 24 / CHUNK_COUNT 227
CHUNK_PLAN_HASH 9d34dd4b214945948edee3a1d584b31c7c5124c92545e354b9af41c049da6348
NETWORK_PROVIDER_DATA_FETCH NO  MANIFEST_WRITE NO  REAL_ROOT_WRITE NO
PROVIDER_STEP_ENTERED NO
```

```text
TARGETED_TESTS  40 (orchestrator) + 42 (adapter) + 23 (gate) = 105 passed
GIT_DIFF_CHECK  CLEAN
```

---

## 9. FINAL FAIL-CLOSED / TERMINATION FIX V02 (post re-review of 3a94455)

### 9.1 CONFIG UNKNOWN fails closed

Removed the `before is None or after is None -> ok` bug.
`CONFIG_BOUNDARY_STATUS = OK` only when before and after both exist and are
equal. A missing side or hash failure is `UNKNOWN`; a mismatch is `CHANGED`.
Any non-OK status forbids `FULL_BOOTSTRAP_COMPLETE`. Tested: before-None /
after-None / both-None -> not complete; equal non-null -> OK.

### 9.2 Centralized termination boundary

Every real-execution exit path now runs `apply_write_boundary` (records
`PROTECTED_HASH_AFTER` + `CONFIG_SHA_AFTER` and compares): chunk incomplete,
adapter error, receipt mismatch, config unknown/changed, mid-run manifest
failure, PERIODIC_GATE_FAILURE, GATE_EXECUTION_FAILURE, normal incomplete and
normal complete. A breach makes `WRITE_BOUNDARY_BREACH` win and preserves the
original reason in `ORIGINAL_STOP_REASON`. START gate failure occurs before
any adapter call -> `EXECUTION_STARTED=false` (no real-data-write claim), while
config/protected before evidence is retained.

### 9.3 Mid-run manifest failure stops

The `except: covered=covered; continue` path is deleted. A manifest reload
failure (or WAL pending) -> `MANIFEST_READ_FAILURE` + `stop_chunk_index`, no
next adapter chunk, boundary finalizer runs, no sidecar cleanup. Tested:
chunk1 success -> reload raises -> adapter calls == 1 -> boundary after recorded.

### 9.4 Unknown receipt symbol from any trusted receipt

Unknown-symbol detection now scans the union of ALL successful
`corporate_actions_chunk` `symbols_json` (any window) against the frozen
identity, so a partial-window successful receipt carrying an out-of-identity
symbol fails closed (`UNKNOWN_RECEIPT_SYMBOL`), not only full-coverage rows.

### 9.5 CLI exit contract

`READY` and `FULL_BOOTSTRAP_COMPLETE` -> exit 0; every other terminal status
(INCOMPLETE / STOPPED / WRITE_BOUNDARY_BREACH / GATE_EXECUTION_FAILURE /
MANIFEST_READ_FAILURE / START_GATE_FAILURE / PERIODIC_GATE_FAILURE /
CONFIG_BOUNDARY_UNKNOWN) -> nonzero. Tested: COMPLETE -> 0, INCOMPLETE -> != 0.

### 9.6 Telemetry cleanup

Zero-remaining real resume performs only a read-only FINAL gate, so it reports
`EXECUTION_STARTED=false`, `MANIFEST_WRITE=NO`, `REAL_ROOT_WRITE=NO`,
`NETWORK_PROVIDER_DATA_FETCH=NO`, `PROVIDER_STEP_ENTERED=NO` — no fabricated
YES. Ordinary real chunks set the fields per real semantics once started.

### 9.7 Tests / real-config dry-run

`tests/test_r4a0_full_bootstrap_orchestrator.py` grew to **52 targeted tests**
(prior 40 + config before/after/both-None + equal-passes + chunk-failure-after +
periodic-failure-after + final-gate-exception-after + mid-run-manifest-stop +
partial-window-unknown-receipt + CLI complete/incomplete exit + zero-remaining
telemetry). Existing adapter (42) + gate (23) re-run clean.
Total: **117 passed**.

Real-config `--dry-run` unchanged:

```text
EXPECTED 5456 / COVERED 24 / REMAINING 5432 / CHUNK_COUNT 227
CHUNK_PLAN_HASH 9d34dd4b214945948edee3a1d584b31c7c5124c92545e354b9af41c049da6348
NETWORK/MANIFEST_WRITE/REAL_ROOT_WRITE NO  EXECUTION_STARTED false
```

```text
TARGETED_TESTS  52 (orchestrator) + 42 (adapter) + 23 (gate) = 117 passed
GIT_DIFF_CHECK  CLEAN
```
