# R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_V01

Frozen full BaoStock session-authority extraction. Provider receipts and the session authority remain in isolated local staging; canonical and R4A9 state are not written.

- AUTHOR_STATUS: PASS_PENDING_INDEPENDENT_AUDIT
- EXECUTION_BASE_HEAD: ada99d214a87c34664dc4d1a6b2746cac062fbbe
- PLAN_COMMIT / CANARY_COMMIT: c9b1fc7bd99d9a7c20df350efeb8fd7f88714321 / ada99d214a87c34664dc4d1a6b2746cac062fbbe
- FULL_REQUEST_N / FULL_REQUEST_MANIFEST_HASH: 48345 / 4654e4282d5191cf680dc6b539025c816bc4d21028a4f2e200230209348412ed
- NETWORK_REQUEST_N: 48570
- COMPLETE_REQUEST_N / FAILED_REQUEST_N: 48345 / 0
- EXPECTED_BAR_CASE_N / NOT_EXPECTED_BAR_CASE_N / UNKNOWN_CASE_N: 10709989 / 187201 / 39
- PROVIDER_FAILED_REQUEST_N / DUPLICATE_PROVIDER_KEY_N / INVALID_PROVIDER_ROW_N: 0 / 0 / 0
- CANARY_CACHE_CANDIDATE_N / REUSED_N / REJECTED_N: 100 / 0 / 100
- REQUEST_RECEIPT_INDEX_HASH: 0884112d77b75469bc0e4145dfde8ad749b4ae16b7d6974e365029bca799ab8b
- FULL_EXTRACTION_COMPLETE: true

## Frozen semantics

tradestatus=1 -> EXPECTED_BAR; tradestatus=0 -> NOT_EXPECTED_BAR; row absent inside lifetime -> UNKNOWN; invalid status -> UNKNOWN with durable reason. UNKNOWN is not converted to NOT_EXPECTED_BAR and blocks the later session-key pass.

## Cache and resume

The 100 existing canary receipt pairs are only cache candidates. A candidate is reusable only when the raw and normalized pair binds the full request-manifest hash and exact lifecycle keyset hash, both payload hashes recompute, and the complete predicate passes. Missing, corrupt, FAILED, RUNNING, or mismatched receipts are refetched.

## Session authority

- SESSION_AUTHORITY_KEY_N: 10897229
- SESSION_AUTHORITY_DATASET_HASH: 0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47
- MISSING_SESSION_AUTHORITY_KEY_N / DUPLICATE_SESSION_AUTHORITY_KEY_N: 0 / 0
- SESSION_AUTHORITY_FILE_SHA256: 33df049921b8a9158f5910d58020707a39d9493a50ddf252aab6c49ebee85cd8

## Safety

- CANONICAL_BYTES_MUTATED: false
- CANONICAL_WRITE_EXECUTED: false
- FORWARD: false
- FULL_EXTRACTION_EXECUTED: true
- NETWORK_PROVIDER_DATA_FETCH: YES
- PRECLOSE_COMPLETE: false
- PRODUCTION: false
- R4A9_CHECKPOINT_MUTATED: false
- R4A9_RESUME_AUTHORIZED: false
- TRADEPLAN: false

## Verification

- Targeted tests and static checks are recorded in the report generated after the extraction.
