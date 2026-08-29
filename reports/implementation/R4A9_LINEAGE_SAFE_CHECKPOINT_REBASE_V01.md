# R4A9_LINEAGE_SAFE_CHECKPOINT_REBASE_V01

Offline lineage-safe checkpoint rebase. No provider execution and no canonical write were performed.

## Authority

- BASE_HEAD: `baeedc4bb17ddbd4d04b53df29bd434a2808ca0e`
- COMPATIBILITY_VERDICT: `BOUNDED_INVALIDATION`
- COMPATIBILITY_REPORT_SHA256: `7beaf08f61ed4132bc9be06fe7acdb1e8191d8e6e5a6b6b39451f9b5b2042a40`
- OLD_CHECKPOINT_SHA256: `d09c53dbca0b49ecb070b01c2740d1f122e9a327bfb9f7bdabca8739d4c17d6e`
- DAILY_INPUT_MANIFEST_HASH: `dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045`

## Rebased partition

- SAFE_REUSE_COMPLETE_N: `2139`
- RECOMPUTE_REQUIRED_N: `1` (`002087.SZ`)
- RETRY_REQUIRED_N: `1` (`300546.SZ`)
- UNVISITED_N: `3315`
- STATE_TOTAL_N: `5456`

The new checkpoint binds future resume to the current daily manifest. A live manifest mismatch is `COMPATIBILITY_REASSESSMENT_REQUIRED` and cannot become an unbound resume.

## Resume design only

1. Recompute `002087.SZ`.
2. Bounded retry `300546.SZ`.
3. Continue the remaining 3,315 UNVISITED symbols only after both bounded units pass.

No phase above was executed in this task.

## Safety

- OLD_CHECKPOINT_MUTATED: `False`
- OLD_COMPLETE_ARTIFACT_DELETED_N: `0`
- OLD_FAILED_ARTIFACT_DELETED_N: `0`
- NETWORK_PROVIDER_DATA_FETCH: `NO`
- BAOSTOCK_EXECUTED: `False`
- TDX_EXECUTED: `False`
- CANONICAL_WRITE_EXECUTED: `False`
- R4_PRECLOSE_RESULT_WRITE_EXECUTED: `False`
- R4A9_RESUME_CANDIDATE: `True`
- R4A9_RESUME_AUTHORIZED: `False`
- PRECLOSE_COMPLETE: `False`

New checkpoint: `/Users/luke808/AI/local-a-share-data-service-data/staging/r4a9-preclose-resume-post-r3-repair-v01/manifest.json`
New checkpoint SHA256: `b3cf88438c94a46d077e0d37df6b70cb45d29c741e646264a061dc0ea8d804de`
New checkpoint schema: `R4A9_LINEAGE_SAFE_RESUME_V01`
