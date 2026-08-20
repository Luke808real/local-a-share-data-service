# R4A0 FULL BOOTSTRAP RERUN — EXECUTION REPORT (STOPPED AT CHUNK 182)

DATE: 2026-08-20
BRANCH: codex/r4a0-resumable-full-bootstrap-v01
BASE_HEAD: f6af077717f2f507cc0fc1ab4f16a02456e99b9c

## 1. Verdict

**STATUS = FULL_BOOTSTRAP_STOPPED** (NOT COMPLETE). The identity-contract fix is
verified live, 181 of 227 chunks completed successfully, and the run stopped
fail-closed on chunk 182 (a real provider/TDX fetch failure for the whole
chunk). No code change was made, no automatic retry or second --exec occurred,
no R4A entered, and the site is preserved for Sol review.

## 2. Pre-flight (passed)

```text
GIT_HEAD == f6af077...  true
PIN_MATCH true
FORMAL_IDENTITY_N 5456 ; HASH 2b1e7202...
dry-run: COVERED 24 / REMAINING 5432 / CHUNK_COUNT 227
PLAN_HASH 9d34dd4b214945948edee3a1d584b31c7c5124c92545e354b9af41c049da6348
NETWORK / MANIFEST_WRITE / REAL_ROOT_WRITE NO
```

## 3. Execution (single run)

```text
EXECUTION_STARTED true
START gate passed (identity PASS; expected SYMBOL_COVERAGE_INCOMPLETE only)
CHUNK_1: STATUS PILOT_COMPLETE  RUN_ID 49e0f563-...  REQUESTED_SYMBOL_N 24
        RECEIPT OK  CONFIG OK  (identity regression NOT present)
progress: chunks 1..181 completed (PILOT_COMPLETE true, RECEIPT OK each)
LAST_COMPLETED_CHUNK 181 ; stop_chunk_index 182
CHUNK_182: adapter STATUS PILOT_INCOMPLETE, final failed, compact success,
  MARKET_DATA_WRITE NO, failed_symbols = all 24 symbols (603321..603350)
stop_reason RECEIPT_MISMATCH (adapter fail-closed -> no success receipt)
```

Failure class: real provider/TDX fetch failure for the chunk (no identity /
contract / correctness bug observed; the first-chunk regression proof passed).

## 4. Coverage after stop

```text
COVERED_SYMBOL_N 4368 (per-symbol union of success receipts)
REMAINING_SYMBOL_N 1088
chunk receipts: 182 success (24 pilot + 181 new)
```

## 5. Write boundary

```text
PROTECTED_HASH_BEFORE == AFTER e5504cb0... (unchanged)
CONFIG_SHA_BEFORE == AFTER fac5abd1... ; CONFIG_BOUNDARY_STATUS OK
only corporate_actions (curated + manifest receipts) changed
```

## 6. Data summary (read-only)

```text
ROW_COUNT 33484 ; SYMBOL_WITH_EVENT_N 4120 ; ZERO_EVENT_COVERED_N 248
MIN_EX_DATE 2016-01-07 ; MAX_EX_DATE 2026-08-14
SOURCE {tdx_protocol: 33484} ; VERSION {v1: 33484}
DUPLICATE_PK_N 0 ; UNEXPECTED_SYMBOL_N 0 ; OUT_OF_WINDOW_ROW_N 0
curated partitions 11
```

## 7. Formal gate after (read-only, no promotion)

```text
R4A0_READY false ; SUCCESSFULLY_COVERED 4368 ; MISSING 1088 ; PARTIAL 0
DATE_COVERAGE_PASS true ; SYMBOL_COVERAGE_PASS false
ROW_COUNT 33484 ; DUPLICATE 0 ; UNRESOLVED 0 ; BLOCKER SYMBOL_COVERAGE_INCOMPLETE
```

Pilot/partial run PASS != R4A0_READY; R4A0 stays false. No R4A preclose entered.

## 8. Bounded next action (for Sol)

The run is resumable: manifest receipts are the resume authority
(COVERED=4368). Sol may audit the execution evidence and either authorize a
resume (which will skip already-covered symbols, starting at chunk 183) or
decide on retry conditions for transient provider failures. Until then:
`FULL_BOOTSTRAP_EXECUTION` stays forbidden by policy.

## 9. Fixed state (unchanged)

```text
R3_SHSZ_CLOSEOUT=FROZEN
R4A0_GATE_CODE_AUDIT=PASS
R4A0_BOUNDED_ADAPTER_CODE_AUDIT=PASS
R4A0_REAL_PILOT_AUDIT=PASS
R4A0_FULL_BOOTSTRAP_ORCHESTRATOR_CODE_AUDIT=PASS
R4A0_READY=false
FULL_BOOTSTRAP_EXECUTION=FORBIDDEN_PENDING_SOL_REAUDIT
R4A_PRECLOSE_EXECUTION=FORBIDDEN
DAILY_READY=FALSE BJ_EXTENSION=DEFERRED
PRODUCTION=false FORWARD=false TRADEPLAN=false
```

No parquet / manifest.db / staging market data / cache / credentials uploaded.
