# R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01

Offline execution-plan freeze only. No BaoStock request, no TDX request, no extraction, no canonical write, and no R4A9 resume.

- BASE_HEAD: `9d7baf69ccdaf323c3221a62cf0487378373e95f`
- FEASIBILITY_COMMIT: `9d7baf69ccdaf323c3221a62cf0487378373e95f`
- INPUT_FILE_N / INPUT_MANIFEST_HASH: `2580` / `ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731`
- FORMAL_SYMBOL_N / FORMAL_IDENTITY_HASH: `5456` / `2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f`
- EXECUTION_BASELINE: `CALENDAR_YEAR_WINDOWED`
- FULL_REQUEST_N / FULL_REQUEST_MANIFEST_HASH: `48345` / `c3c90ca30738a06f5c375ea6441323bb69179954597566647e9822ec2a18dc2c`
- LIFECYCLE_SESSION_KEY_N: `10897229`
- REQUEST_COVERAGE_MISSING_KEY_N / DUPLICATE_KEY_N: `0` / `0`

## Frozen contract

`tradestatus=1` maps to `EXPECTED_BAR`; `tradestatus=0` maps to `NOT_EXPECTED_BAR`; provider row absence inside independent lifecycle maps to `UNKNOWN`; outside lifecycle maps to `NOT_EXPECTED_BAR` only through frozen identity. UNKNOWN is never PASS and never NOT_EXPECTED_BAR.

## Request and checkpoint plan

The formal baseline is one request per symbol/year intersection after clipping to `[max(list_date, 2016-01-01), min(delist_date, 2026-08-17)]`. It is ordered by `(symbol, calendar_year)` and covers each lifecycle exchange trading date exactly once. Current canonical gaps are not an input to request creation.

- MAX_RETRY / MAX_ATTEMPT_N: `2` / `3`
- REQUEST_INTERVAL_SECONDS: `1.0`
- BATCH_SIZE / CHECKPOINT_INTERVAL_REQUESTS: `50` / `1`
- Resume skips only hash-verified COMPLETE receipts; missing, FAILED, RUNNING, or corrupt receipts rerun.
- One-query-per-symbol is retained only as an unverified 5,456-request optimization scenario.

## Coverage

- LIFECYCLE_SESSION_KEY_N: `10897229`
- OUTSIDE_LIFETIME_SESSION_KEY_N: `3179251`
- REQUEST_COVERAGE_EXACT: `True`

## Safety

- NETWORK_PROVIDER_DATA_FETCH: `NO`
- FULL_EXTRACTION_EXECUTED: `false`
- CANONICAL_WRITE_EXECUTED: `false`
- CANONICAL_BYTES_MUTATED: `false`
- R4A9_CHECKPOINT_MUTATED: `false`
- R4A9_RESUME_AUTHORIZED: `false`
- PRECLOSE_COMPLETE: `false`
- PRODUCTION: `false`
- FORWARD: `false`
- TRADEPLAN: `false`

## Verification

- AUTHOR_STATUS: `PASS_PENDING_INDEPENDENT_AUDIT`
- TEST_RESULT: `PASS`
- PY_COMPILE: `PASS`
- GIT_DIFF_CHECK: `PASS`
