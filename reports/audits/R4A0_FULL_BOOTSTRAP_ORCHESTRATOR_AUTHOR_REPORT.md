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
