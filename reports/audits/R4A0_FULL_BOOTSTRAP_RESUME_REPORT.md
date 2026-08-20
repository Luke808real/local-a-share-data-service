# R4A0 FULL BOOTSTRAP RESUME — COMPLETION REPORT

DATE: 2026-08-20
BRANCH: codex/r4a0-resumable-full-bootstrap-v01
BASE_HEAD: e1f75bf0e0bdff1f248070f1b954982d7d726711
CODE_AUTHORITY: f6af077717f2f507cc0fc1ab4f16a02456e99b9c

## 1. Verdict

**CORPORATE_ACTION_FULL_BOOTSTRAP_PASS** — `STATUS=FULL_BOOTSTRAP_COMPLETE`,
`FULL_BOOTSTRAP_COMPLETE=true`, final formal gate `R4A0_READY=true`, all 5456
symbols covered, duplicates/unresolved/window violations all zero, config and
protected R3 datasets unchanged. Single execution; no R4A entered; no code
change.

## 2. Pre-flight

```text
HEAD == e1f75bf... (task base)
code changes f6af077..HEAD in src/ tools/ tests/ : NONE
dry-run (manifest-derived): COVERED 4368 / REMAINING 1088 / CHUNK 24 / CHUNK_COUNT 46
RESUME_PLAN_HASH 0e5d41b1c2dd7ff4bd0745260c2a9402304f9be8b6375f7cec6f945419f28edc
FIRST_REMAINING_CHUNK = prior failed group 603321..603350 (matches expectation)
NETWORK / MANIFEST_WRITE / REAL_ROOT_WRITE NO
```

No manifest-state drift; no plan divergence.

## 3. Execution (single resume, 46 chunks)

```text
START gate PASS (identity PASS; only expected coverage-incomplete)
chunk 1 (603321..603350, the prior failed group): PILOT_COMPLETE, RUN_ID
  bab5ff14-..., RECEIPT OK, CONFIG OK, FAILED_SYMBOLS []
chunks 1..46 all PILOT_COMPLETE / RECEIPT OK; LAST REMAINING_N_AFTER 0
EXECUTED_CHUNK_N 46 ; LAST_COMPLETED_CHUNK 46
```

The prior chunk-182 provider failure was transient: the same 24-symbol chunk
succeeded on resume (evidence in this report; no further provider probes done).

## 4. Final evidence

```text
final gate (orchestrator + independent read-only re-run agree):
R4A0_READY true ; COVERED 5456 ; MISSING 0 ; PARTIAL 0
DATE_COVERAGE_PASS true ; SYMBOL_COVERAGE_PASS true ; COVERAGE_STATUS PASS
ROW_COUNT 40544 ; DUPLICATE_ACTION_N 0 ; UNRESOLVED_N 0 ; BLOCKER None
PIN_MATCH true

write boundary:
PROTECTED_HASH_BEFORE == AFTER e5504cb0...  ; CONFIG  fac5abd1... == same
CONFIG_BOUNDARY_STATUS OK ; only corporate_actions + manifest receipts changed

data summary:
ROW_COUNT 40544 ; SYMBOL_WITH_EVENT_N 5141 ; ZERO_EVENT_COVERED_SYMBOL_N 315
MIN_EX_DATE 2016-01-07 ; MAX_EX_DATE 2026-08-17
SOURCE {tdx_protocol: 40544} ; VERSION {v1: 40544}
DUPLICATE_PK_N 0 ; UNEXPECTED_SYMBOL_N 0 ; OUT_OF_WINDOW_ROW_N 0
curated partitions 11 ; chunk receipts 228
```

## 5. Handoff / no auto R4A

No preclose / turnover / price-limit / trading-status / strategy work was
started. This report and ASCII receipts are handed to Sol for the independent
final audit of the exact pushed commit. `R4A0_READY` may be confirmed only by
that independent audit.

## 6. Fixed state

```text
R3_SHSZ_CLOSEOUT=FROZEN
R4A0_GATE_CODE_AUDIT=PASS
R4A0_BOUNDED_ADAPTER_CODE_AUDIT=PASS
R4A0_REAL_PILOT_AUDIT=PASS
R4A0_FULL_BOOTSTRAP_ORCHESTRATOR_CODE_AUDIT=PASS
R4A0_READY=true (AUTHOR_RUNTIME_RESULT, pending independent Sol audit)
FULL_BOOTSTRAP_EXECUTION=COMPLETE_AUTHOR_RUNTIME
R4A_PRECLOSE_EXECUTION=FORBIDDEN_PENDING_SOL_FINAL_AUDIT
DAILY_READY=FALSE BJ_EXTENSION=DEFERRED
PRODUCTION=false FORWARD=false TRADEPLAN=false
```

No parquet / manifest.db / staging market data / cache / credentials uploaded.
