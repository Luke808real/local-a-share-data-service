# R3_BAOSTOCK_TRADESTATUS0_SEMANTIC_REFINEMENT_V01

Offline semantic refinement of the frozen 187,201-key BaoStock tradestatus=0 evidence.

## Frozen input and receipt verification

- `BASE_HEAD=08548237664a48238dea088768a9d552bb2c8318`; `INPUT_MANIFEST_HASH=ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731`.
- `SESSION_AUTHORITY_DATASET_HASH=0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47`; `REQUEST_RECEIPT_INDEX_HASH=0884112d77b75469bc0e4145dfde8ad749b4ae16b7d6974e365029bca799ab8b`.
- full receipt pairs=`48345`; full aggregate hash=`19404ce35db37169c3ec4e2a16dba900979c1b6d74743956c952e440fc234358`.
- relevant receipt pairs=`7053`; relevant aggregate hash=`ce506b42726a70a55bccb4c8bc9426c62f5cec1ebbfe373e112ef1f6127dafdd`.

## Exact three-way partition

- `STATUS0_KEY_N=187201`; zero-zero=`172189`; contradiction=`8`; numeric-indeterminate=`15004`.
- partition arithmetic=`172189 + 8 + 15004 = 187201`.

## Invalid numeric and OHLC profiles

- both blank=`15004`; volume-only blank=`0`; amount-only blank=`0`; nonblank-invalid=`0`.
- indeterminate OHLC all blank/populated=`3/15001`.
- indeterminate preclose blank/populated=`3/15001`.

## Canonical 3x3 cross-check

- `STATUS0_ZERO_ZERO`: positive=0, zero=1, absent=172188.
- `STATUS0_CONTRADICTION`: positive=1, zero=0, absent=7.
- `STATUS0_NUMERIC_INDETERMINATE`: positive=0, zero=0, absent=15004.

## Observation-only clustering

- contradiction: `8` symbols, dates `2020-06-17`..`2024-06-13`, longest frozen-session run=`1`.
- numeric-indeterminate: `1328` symbols, dates `2016-01-04`..`2026-08-17`, longest frozen-session run=`337`.
- These are observations only; no suspension, listing, or delisting interpretation is inferred.

## Refined research contract

- `STATUS0_ZERO_ZERO -> NOT_EXPECTED_SUPPORTED` provisionally; `STATUS0_CONTRADICTION -> UNKNOWN`; `STATUS0_NUMERIC_INDETERMINATE -> UNKNOWN`.
- provisional counts: expected=`10709989`, not-expected=`172189`, unknown=`15051`.
- This is research-only reclassification evidence; session_authority.parquet is unchanged and no promotion/refreeze is performed.

## Safety

- `NETWORK_PROVIDER_DATA_FETCH=NO`
- `BAOSTOCK_EXECUTED=false`
- `TDX_EXECUTED=false`
- `CANONICAL_WRITE_EXECUTED=false`
- `CANONICAL_BYTES_MUTATED=false`
- `R4A9_RESUME_AUTHORIZED=false`
- `PRODUCTION=false`
- `FORWARD=false`
- `TRADEPLAN=false`

- secondary scope=`52` keys / `756258b487c409843a4accdf42a5a12b6ca5f87a117fb9ac35d55ef74ab60971`; network requests=`0`.
- `TEST_RESULT=TARGETED_TESTS_PASS`; `PY_COMPILE=PASS`; `GIT_DIFF_CHECK=PASS`.
- exact cross-canonical manifests: `R3_BAOSTOCK_TRADESTATUS0_SEMANTIC_REFINEMENT_V01_CROSS_CANONICAL_MATRIX.json`; secondary scope: `R3_BAOSTOCK_TRADESTATUS0_SEMANTIC_REFINEMENT_V01_SECONDARY_SCOPE.json`.
