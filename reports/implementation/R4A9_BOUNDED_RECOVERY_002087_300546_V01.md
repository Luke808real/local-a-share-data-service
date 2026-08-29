# R4A9_BOUNDED_RECOVERY_002087_300546_V01

Bounded R4A9 recovery was attempted only for 002087.SZ. The frozen Phase 1
quality gate failed, so Phase 2 was not executed.

- BASE_HEAD: `5f4f509615cb99419029792fdb5c48b3bc591b76`
- START_CHECKPOINT_HASH: `b3cf88438c94a46d077e0d37df6b70cb45d29c741e646264a061dc0ea8d804de`
- END_CHECKPOINT_HASH: `b3cf88438c94a46d077e0d37df6b70cb45d29c741e646264a061dc0ea8d804de`
- CHECKPOINT_PATH: `/Users/luke808/AI/local-a-share-data-service-data/staging/r4a9-preclose-resume-post-r3-repair-v01/manifest.json`
- DAILY_INPUT_MANIFEST_HASH: `dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045`
- PHASE_1_STATUS: `FAILED`; required/formal `2026/2025`
- PHASE_1_STATUS_DETAIL: `FAILED_QUALITY_GATE`; `MISSING_REQUIRED_N=1`
- PHASE_2_STATUS: `NOT_EXECUTED`; required/formal `None/None`
- FINAL_COMPLETE_N: `2139`
- RECOMPUTE_REQUIRED_N: `1`
- RETRY_REQUIRED_N: `1`
- UNVISITED_N: `3315`
- NETWORK_SYMBOLS: `002087.SZ`
- NETWORK_REQUEST_N: `22` total across two bounded Phase 1 attempts; latest attempt `11`

## Safety

- CANONICAL_WRITE_EXECUTED: `False`
- CANONICAL_BYTES_MUTATED: `False`
- OLD_R4A9_CHECKPOINT_MUTATED: `False`
- OLD_R4A9_ARTIFACT_DELETED_N: `0`
- R3_DATA_MUTATED: `False`
- DAILY_MANIFEST_STABLE_DURING_RECOVERY: `True`
- UNVISITED_EXECUTION_N: `0`
- FULL_R4A9_CONTINUATION_CANDIDATE: `True`
- FULL_R4A9_CONTINUATION_AUTHORIZED: `False`
- PRECLOSE_COMPLETE: `False`
- FACTS_READY: `False`

Phase 2 was not eligible until Phase 1 passed. The remaining 3,315 symbols remain UNVISITED.
