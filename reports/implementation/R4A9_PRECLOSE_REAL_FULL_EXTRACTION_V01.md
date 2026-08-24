# R4A9 PRECLOSE REAL FULL EXTRACTION — V01 (author report)

DATE: 2026-08-24
BRANCH: codex/r4a9-preclose-real-full-extraction-v01
BASE_HEAD: 795b1b8f6b688ecc2e94f85c09d80c365e648920
RUNTIME_HEAD: 795b1b8f6b688ecc2e94f85c09d80c365e648920
AS_OF: 2026-08-17

## AUTHORITY

adapter == expected == runtime == 795b1b8f6b688ecc2e94f85c09d80c365e648920
execution_context=REAL, HEAD/worktree verified clean before network

## FROZEN GATES (all passed)

FORMAL_IDENTITY_N=5456 HASH=2b1e7202...
FULL_SYMBOL_N=5456 HASH=2b1e7202...
FULL_QUERY_WINDOW_N=60016 PLAN_HASH=9773875f...

## PREFETCH TEST GATE (executed before network)

R4A7_TARGETED_TESTS_N=38 PASS=38
ADAPTER_TARGETED_TESTS_N=64 PASS=64
TOTAL_PREFETCH_TESTS_N=102 PASS=102 STATUS=PASS

## EXTRACTION RESULT: STOPPED_UNIT_FAILED

RUN STATUS=STOPPED_UNIT_FAILED (fail-closed per contract; no patch, no silent retry)
COMPLETE_N=2140  PENDING_N=0  FAILED_N=1  EXECUTED_N=2140  SKIPPED_N=0
NETWORK_PROVIDER_QUERY_COUNT=23551 (derived: 2140x11 + 300546.SZ 11 windows fully fetched; latest log: [query 23500/60016 symbols=2137])
PROVIDER_FETCHED_SYMBOL_N=2141

## FAILED UNIT

symbol=300546.SZ
error=RuntimeError: unit failed for 300546.SZ: FAILED_QUALITY_GATE
failed_utc=2026-08-24T04:50:42.386459+00:00
stage=post-fetch normalize/quality gate (all 11 provider windows already fetched)

## EXTENDED PARTIAL EVIDENCE (read-only, over the 2140 COMPLETE units)

All 2140 COMPLETE receipts passed the audited resume/unit-integrity check
(state, symbol, contract, unit query identity, adapter SHA, parquet 
readable, row count, FORMAL_FACT_HASH, STAGED_FORMAL_CONTENT_HASH, staged 
formal verifier).
PARTIAL_REQUIRED_ROW_N=4914227
PARTIAL_FORMAL_FACT_ROW_N=4914227 (equal)
PARTIAL_PROVIDER_SUSPENDED_SUPERSET_N=124585
PARTIAL_FORMAL_FACT_HASH=9d9c90f87490cf4614c0500c988a65c8ebacdca100c615f3a2a0446c4a836bc0

Full 5456-universe window-boundary / sentinel / CLEAN_NORMAL gates were
NOT run: they are defined only for complete coverage; partial 2140/5456
cannot satisfy COVERAGE_COMPLETE and the aggregator correctly stays
fail-closed (never promote partial as complete).

## DECISION

R4A9_REAL_FULL_EXTRACTION_PASS=false
DATA_VALIDATION_REQUIRED=true
CODE_FIX_REQUIRED=NOT_CLAIMED (no code defect demonstrated; 102 prefetch
tests + 2140 real units passed; single provider-data quality-gate failure)
checkpoint_preserved=true (staging/manifest retained for a future
resume under the SAME runtime contract, per task rules)
NETWORK_PROVIDER_DATA_FETCH=YES (authorized full plan, stopped at unit 2141)
CANONICAL_MARKET_DATA_WRITE=NO
PRECLOSE_FACTS_WRITE=NO
READINESS_MUTATION=NO
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false

## NEXT (NOT executed here)

Data-side validation of 300546.SZ provider rows (identity/unexpected/
tradestatus classification) under Sol guidance; resume from the same
checkpoint is possible only after the failing unit is understood.
