# R3_FULL_SESSION_COMPLETENESS_RECONCILIATION_V01

Offline exact-key reconciliation between the frozen BaoStock session authority and the current canonical daily-bars dataset.

## Dataset and grain

- Session authority grain: `(symbol, trade_date)`; rows=`10897229`; dataset hash=`0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47`.
- Canonical grain: `(symbol, trade_date)`; rows=`10709991`; unique keys=`10709991`; duplicate keys=`0`.
- Frozen daily input: files=`2580`; manifest hash=`ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731`.

## Authority partition

- EXPECTED_KEY_N / NOT_EXPECTED_KEY_N / UNKNOWN_KEY_N: `10709989` / `187201` / `39`.
- UNKNOWN_BASIS_COUNTS: `{"PROVIDER_ROW_ABSENT_IN_LIFETIME": 39}`.
- UNKNOWN_KEYSET_HASH: `fff37a6ac7cff43ad1a9b6ce9265851f56ee9248f10a1f5544ec9cceb633a852`.

## Exact reconciliation

- EXPECTED_PRESENT_KEY_N / MISSING_EXPECTED_KEY_N: `10709989` / `0`.
- CANONICAL_ON_NOT_EXPECTED_KEY_N: `2`.
- CANONICAL_ON_UNKNOWN_KEY_N: `0`.
- CANONICAL_OUTSIDE_SESSION_AUTHORITY_KEY_N: `0`.
- UNEXPECTED_CANONICAL_TOTAL_N (specified cancellation formula): `2`.
- EXPECTED_KEY_N == CANONICAL_ROW_N: `false`; this is not a completeness PASS because the exact key gate remains authoritative.

## 300546 audit anchors

- 300546.SZ/20160929: status=`EXPECTED_BAR`, basis=`PROVIDER_TRADESTATUS_1`, canonical_present=`true`
- 300546.SZ/20161010: status=`EXPECTED_BAR`, basis=`PROVIDER_TRADESTATUS_1`, canonical_present=`true`

These two keys are present in the current frozen post-repair canonical input and are classified EXPECTED_BAR by the independently extracted authority; no special-case mutation was applied by this reconciliation.

## Gate and safety

- R3_COMPLETENESS_RECONCILIATION_PASS: `false` (UNKNOWN_KEY_N=39 is retained, not converted to PASS).
- NETWORK_PROVIDER_DATA_FETCH: `NO`
- BAOSTOCK_EXECUTED: `false`
- TDX_EXECUTED: `false`
- CANONICAL_WRITE_EXECUTED: `false`
- CANONICAL_BYTES_MUTATED: `false`
- R4A9_CHECKPOINT_MUTATED: `false`
- R4A9_RESUME_AUTHORIZED: `false`
- PRECLOSE_COMPLETE: `false`
- PRODUCTION: `false`
- FORWARD: `false`
- TRADEPLAN: `false`

## Verification

- TEST_RESULT: `90 passed in 6.44s`
- PY_COMPILE: `PASS`
- GIT_DIFF_CHECK: `PASS`

The session Parquet, provider receipts, and canonical Parquet remain outside Git in local staging/data-root paths.
