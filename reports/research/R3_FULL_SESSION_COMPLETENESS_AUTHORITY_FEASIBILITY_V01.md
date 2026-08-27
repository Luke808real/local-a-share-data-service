# R3_FULL_SESSION_COMPLETENESS_AUTHORITY_FEASIBILITY_V01

Bounded provider-feasibility research only. No canonical write, repair, TDX execution, full extraction, or R4A9 resume was performed.

- BASE_HEAD: `7bb4e4ea1898a138aa400d9ea1778066004c8b63`
- INPUT_FILE_N / INPUT_MANIFEST_HASH: `2580` / `ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731`
- FULL_SESSION_AUTHORITY_ALREADY_EXISTS: `False`
- PILOT_SYMBOL_N / PILOT_REQUEST_N: `39` / `40`
- REQUEST_MANIFEST_HASH: `3fa1b84597b81374ecc58037bb1e7850a42a6252f77d82aaffc45ff228af39e4`
- RAW_RECEIPT_HASH: `ae941d998e67ce98a534b4287983f656f9a1f0732cb56e7e4b8c17d60c10baaa`
- NORMALIZED_RECEIPT_HASH: `f74ed04058d1d79b5c1573cf3a7d4ec355315cb3bcc1363e4a59c1ef065ce276`

## Contract

For an exchange trading date, `tradestatus=1` is `EXPECTED_BAR`, `tradestatus=0` is `NOT_EXPECTED_BAR`, and an absent provider row inside the independent list/delist lifetime is `UNKNOWN`. Pre-listing and post-delisting are `NOT_EXPECTED_BAR` only because of the independent lifecycle contract; absence alone is never downgraded.

- EXPECTED_BAR_CASE_N: `234`
- NOT_EXPECTED_BAR_CASE_N: `460`
- UNKNOWN_CASE_N: `0`
- TRADESTATUS_UNKNOWN_N: `0`
- PROVIDER_FAILED_REQUEST_N: `0`

## Empirical semantics

- A exchange-open/suspension row returned: `True`
- B suspension `tradestatus=0` stable in tested long-gap slices: `True`
- C `tradestatus=1` contract: `EXPECTED_BAR`; canonical present/missing: `234`/`0`
- D provider row absence inside lifetime: `UNKNOWN_IN_LIFETIME`
- E lifecycle/IPO edge contract: `INDEPENDENT_LIST_DATE_AND_DELIST_DATE_IDENTITY_CONTRACT`
- F long-suspension status0/status1/unknown: `222`/`0`/`0`
- G adjustflag changes session coverage: `False`
- SUSPENSION_SEMANTICS: BaoStock returned explicit tradestatus=0 rows for all in-lifetime exchange trading dates in the bounded long-gap slices.
- LIFECYCLE_OUTSIDE_CASE_N: `156`
- ADJUSTFLAG_DATE_STATUS_EQUAL: `True`; OHLC changed rows: `0`

## Known-gap cross-check

- Gap discovery was selection-only and is not expected-key authority; sampled cases: `49`.
- 300546 repaired dates classified: `[('2016-09-29', 'EXPECTED_BAR'), ('2016-10-10', 'EXPECTED_BAR')]`

## Cost model

- one query per symbol: `5456` requests
- calendar-year windowed: `48345` requests
- modeled raw rows: `10897229`
- No full extraction was executed; request estimates are scenarios, not an execution cap.

## Decision

- FEASIBILITY: `FEASIBLE`
- NEXT_RECOMMENDATION: `R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_V01`

## Safety

- CANONICAL_WRITE_EXECUTED: `false`
- CANONICAL_BYTES_MUTATED: `false`
- R4A9_CHECKPOINT_MUTATED: `false`
- R4A9_RESUME_AUTHORIZED: `false`
- PRECLOSE_COMPLETE: `false`
- PRODUCTION: `false`
- FORWARD: `false`
- TRADEPLAN: `false`
- FULL_MARKET_EXTRACTION_EXECUTED: `false`

## Verification

- TEST_RESULT: `PASS`
- PY_COMPILE: `PASS`
- GIT_DIFF_CHECK: `PASS`
