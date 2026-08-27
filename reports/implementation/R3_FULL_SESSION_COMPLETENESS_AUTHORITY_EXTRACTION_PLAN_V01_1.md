# R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1

Offline provenance/key-universe closure only. No provider request, extraction, canonical write, or R4A9 resume.

- AUTHOR_STATUS: `PASS_PENDING_INDEPENDENT_AUDIT`
- BASE_HEAD: `2cf9abefbf6a2696f06fb85ba62cbc0e0f2c0dc8`
- FEASIBILITY_COMMIT: `9d7baf69ccdaf323c3221a62cf0487378373e95f`
- INPUT_FILE_N / INPUT_MANIFEST_HASH: `2580` / `ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731`
- FORMAL_SYMBOL_N / FORMAL_IDENTITY_HASH: `5456` / `2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f`
- TRADING_DATE_N / TRADING_DATESET_HASH: `2580` / `51d80929eb493d83b709cd667e44809d285bb3e61e7ebfab87e6b677d61fc72d`
- TRADING_CALENDAR_FILE_N / FILE_MANIFEST_HASH: `12` / `579c4efe449cbedc15db793b1ccff14a46e88ea60087d31cb6ff9426860dfc97`
- LIFECYCLE_AUTHORITY_HASH: `9987c05d2b424bd667e9ff339ca7ef9c712fb42ac9b781a27b72b2ee03337c93`
- LIFECYCLE_SESSION_KEY_N / KEYSET_HASH: `10897229` / `eacb645dc8ac6273458fe3a59ea46b6be5072a234f0acf3fabbe2dd7463f2a65`
- OUTSIDE_LIFETIME_SESSION_KEY_N: `3179251`
- FULL_REQUEST_N / FULL_REQUEST_MANIFEST_HASH: `48345` / `4654e4282d5191cf680dc6b539025c816bc4d21028a4f2e200230209348412ed`
- REQUEST_COVERAGE_MISSING_KEY_N / DUPLICATE_KEY_N: `0` / `0`

## Frozen authority contract

`tradestatus=1` -> `EXPECTED_BAR`; `tradestatus=0` -> `NOT_EXPECTED_BAR`; row absent inside lifecycle -> `UNKNOWN`; outside lifecycle -> `NOT_EXPECTED_BAR` through frozen identity. UNKNOWN is neither PASS nor NOT_EXPECTED_BAR.

The session-key hash is a streaming SHA-256 over exact sorted `symbol<TAB>YYYY-MM-DD<LF>` lines. The exact key scan assigns every lifecycle key to one request window; it does not infer coverage from row-count sums.

## Request/checkpoint contract

- EXECUTION_BASELINE: `CALENDAR_YEAR_WINDOWED`
- MAX_RETRY / REQUEST_INTERVAL_SECONDS / BATCH_SIZE / CHECKPOINT_INTERVAL_REQUESTS / CONCURRENCY: `2` / `1.0` / `50` / `1` / `1`
- Missing, FAILED, RUNNING, or corrupt receipts rerun; only hash-verified COMPLETE receipts may be skipped.
- One-query-per-symbol remains an unverified optimization and is not the V01.1 baseline.

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

- TEST_RESULT: `PASS`
- py_compile: `PASS`
- git diff --check: `PASS`
