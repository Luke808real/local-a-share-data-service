# R4A0 CORPORATE_ACTION REAL PILOT — REPORT

DATE: 2026-08-20
BRANCH: codex/r4a0-corporate-actions-bootstrap-pilot-v01
BASE_HEAD: 54e616fc0cc3e3e42103e3de3f9c80cb62aaf810
UPSTREAM_CNEQUITY: v0.7.2 @ a18ee0484dfb0801650175471724def3228b8a17

## 1. Verdict

**PILOT_VERDICT = PASS** (bounded pilot itself), with
**R4A0_READY = false** (NO promotion — the formal gate requires all 5456
symbols, and only 24 were queried).

## 2. Pre-flight

```text
GIT_HEAD == BASE_HEAD 54e616f...  true
PILOT_SYMBOL_N          24 ; unique 24 ; 0 BJ
PILOT_SYMBOL_HASH       e846331eae6cc090eb8b4f9109f0f25f2692e7ad759acd41b3eb4d62241f3661  (match)
DRY_RUN_STATUS OK       STATUS READY
MANIFEST_WRITE NO       REAL_ROOT_WRITE NO   PROVIDER_STEP_ENTERED NO
NETWORK_PROVIDER_DATA_FETCH NO
CONFIG_INTEGRITY_STATUS OK  PERSISTENT_CONFIG_CHANGED false
PIN_MATCH true          IDENTITY_MATCH true (N=5456, hash 2b1e7202...)
```

## 3. Real execution (single run)

```text
RUN_ID       374be7c9-de36-4c89-a1d6-e9e9f58e473c
STATUS       PILOT_COMPLETE
CORPORATE_STATUS success (rows_fetched/written 142)
COMPACT_STATUS success
FINAL_STATUS  success
failed_symbols []
PROVIDER_STEP_ENTERED YES
NETWORK_PROVIDER_DATA_FETCH UNKNOWN (no precise provider-call count)
NETWORK_PROVIDER_REQUEST_COUNT UNVERIFIED
CONFIG_INTEGRITY_STATUS OK   PERSISTENT_CONFIG_CHANGED false
receipt_post_check STATUS OK (no unexpected / all receipted / window exact)
```

Canonical source: TDX protocol (`source=tdx_protocol`, 142 rows;
`data_version=v1`). EastMoney unbounded backup was disabled in-memory for the
run; persistent config untouched.

## 4. Receipt validation (real manifest)

```text
SUCCESSFULLY_RECEIPTED_SYMBOL_N 24   (one success chunk receipt covering all 24)
MISSING_RECEIPT_SYMBOL_N        0
FAILED_SYMBOL_N                 0
window on every success chunk   2016-01-01 .. 2026-08-17 (exact)
```

## 5. Data validation

```text
EVENT_ROW_N                142
UNIQUE_SYMBOL_WITH_EVENT_N 19
ZERO_EVENT_SYMBOL_N        5          (legal: receipted, query returned 0 events)
MIN_EX_DATE                2016-04-15 MAX_EX_DATE 2026-07-24 (both in window)
SOURCE_COUNTS              {tdx_protocol: 142}
DATA_VERSION_COUNTS        {v1: 142}
DUPLICATE_PK_N             0          (pk symbol, ex_date, action_type)
UNEXPECTED_SYMBOL_N        0
OUT_OF_WINDOW_ROW_N        0
```

## 6. Parquet artifact receipt

11 curated `corporate_actions/ex_date=YYYY/part-merged.parquet` files
(2016..2026) + 1 staging run snapshot; path/size/SHA-256 in
`R4A0_CORPORATE_ACTION_REAL_PILOT_VALIDATION_RECEIPT.json` (SHA-256 permitted:
bounded artifact count).

## 7. Write boundary post-check

```text
PROTECTED_DATASETS_INVENTORY_HASH_BEFORE e5504cb0...eef010
PROTECTED_DATASETS_INVENTORY_HASH_AFTER  e5504cb0...eef010
  (curated+staging daily_bars / instruments / trading_calendar; path+size+mtime_ns
   inventory of 7592 entries — identical)
config SHA-256 before == after  fac5abd136cb2ae0...
manifest.db SHA-256 changed 8ea64c83... -> 73034e44... (allowed runtime
  metadata/receipts only)
```

**WRITE_BOUNDARY_STATUS = OK** — only corporate_actions (curated + staging)
and manifest/runtime metadata changed.

## 8. Formal R4A0 gate (post-pilot, no promotion)

```text
EXPECTED_SYMBOL_N              5456
SUCCESSFULLY_COVERED_SYMBOL_N  24
MISSING_SYMBOL_N               5432
PARTIAL_SYMBOL_N               0
DATE_COVERAGE_PASS             true
SYMBOL_COVERAGE_PASS           false
R4A0_READY                     false
R4A0_BLOCKER                   SYMBOL_COVERAGE_INCOMPLETE
```

Pilot PASS != R4A0 READY; R4A0 stays false by design.

## 9. Fixed state

```text
R3_SHSZ_CLOSEOUT=FROZEN
R4A0_GATE_CODE_AUDIT=PASS
R4A0_BOUNDED_ADAPTER_CODE_AUDIT=PASS
R4A0_READY=false
BOUNDED_PILOT_EXECUTION=AUTHORIZED
FULL_CORPORATE_ACTION_BOOTSTRAP=FORBIDDEN_PENDING_SOL_PILOT_AUDIT
R4A_PRECLOSE_EXECUTION=FORBIDDEN
DAILY_READY=FALSE  BJ_EXTENSION=DEFERRED
PRODUCTION=false FORWARD=false TRADEPLAN=false
```

No market parquet / manifest.db content is uploaded to GitHub.
