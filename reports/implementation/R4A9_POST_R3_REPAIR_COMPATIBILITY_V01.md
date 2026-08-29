# R4A9 Post-R3 Repair Compatibility V01

AUTHOR_STATUS: `PASS_PENDING_SOL_AUDIT`
BASE_HEAD: `b7739d8dfbf9438821c11e97c6147f7807a3375b`
BRANCH: `codex/r4a9-post-r3-repair-compatibility-v01`

## Verdict

COMPATIBILITY_VERDICT: `BOUNDED_INVALIDATION`
INVALIDATE_SYMBOL_N: `1`
INVALIDATE_SYMBOLS: `002087.SZ`
INVALIDATE_RESULT_KEY_N: `2`
INVALIDATE_RESULT_KEYS: `002087.SZ:2024-06-13, 002087.SZ:2024-06-14`

The two invalidated result keys are the affected COMPLETE 002087.SZ scope: the newly required 2024-06-13 key and the existing 2024-06-14 row whose local predecessor context changed.  The other three repaired symbols were UNVISITED and have no old R4A9 result to invalidate.

The R4A9 result is provider preclose data.  For CLEAN_NORMAL rows, the frozen implementation validates that provider preclose equals the same symbol's previous effective local close.  The R3 insertion therefore adds one required preclose key and changes the predecessor context for the next traded key; it does not make unrelated symbols dependent on the changed partition.

## Daily lineage

OLD_DAILY_MANIFEST_HASH: `ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731`
NEW_DAILY_MANIFEST_HASH: `dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045`
R4A9_INPUT_DAILY_MANIFEST_HASH: `UNBOUND`
R4A9_INPUT_DAILY_MANIFEST_STATUS: `UNBOUND_IN_R4A9_ARTIFACTS`
AFFECTED_CANONICAL_FILE_N: `1`
INSERTED_KEY_N: `4`
DELETED_KEY_N: `0`
MODIFIED_EXISTING_KEY_N: `0`

## Checkpoint

R4A9_CHECKPOINT_PATH: `/Users/luke808/AI/local-a-share-data-service-data/staging/r4a9-preclose-real-full-extraction-v01/manifest.json`
R4A9_CHECKPOINT_HASH: `d09c53dbca0b49ecb070b01c2740d1f122e9a327bfb9f7bdabca8739d4c17d6e`
COMPLETE_SYMBOL_N: `2140`
FAILED_SYMBOL_N: `1`
UNVISITED_SYMBOL_N: `3315`
TARGET_COMPLETE_N: `1`
TARGET_FAILED_N: `0`
TARGET_UNVISITED_N: `3`
COMPLETE_SYMBOL_DIRECTLY_AFFECTED_N: `1`
COMPLETE_SYMBOL_SAFE_REUSE_N: `2139`

## Predecessor impact

AFFECTED_PRECLOSE_KEY_N: `4`
AFFECTED_PRECLOSE_KEYSET_HASH: `44bee8917fc485d65db619dca194c34fa0805db11736d72cf9ef33ca1b192808`
- `002087.SZ:2024-06-13` -> predecessor `002087.SZ:2024-06-13` for next `002087.SZ:2024-06-14`; existing_result=True; impact=`RECOMPUTE_REQUIRED`
- `600647.SH:2024-06-13` -> predecessor `600647.SH:2024-06-13` for next `600647.SH:2024-06-14`; existing_result=False; impact=`NO_IMPACT`
- `600766.SH:2024-06-13` -> predecessor `600766.SH:2024-06-13` for next `600766.SH:2024-06-14`; existing_result=False; impact=`NO_IMPACT`
- `603133.SH:2024-06-13` -> predecessor `603133.SH:2024-06-13` for next `603133.SH:2024-06-14`; existing_result=False; impact=`NO_IMPACT`

## 300546 failure

R4A9_300546_FAILURE_REASON: R3_REQUIRED_KEY_MISMATCH: BaoStock tradestatus=1 on 2016-09-29 and 2016-10-10 while the pre-repair R3 required-key universe omitted both bars.
R3_REPAIR_ADDRESSES_300546_FAILURE: `True`

## Recommended resume design (not executed)

1. 等待 Sol 对本 compatibility commit 做独立审计；本任务不执行 resume。
2. 保留 2,139 个未受影响的 COMPLETE symbols 原样复用，不改旧 checkpoint。
3. 以 NEW_DAILY_MANIFEST_HASH 重新计算 002087.SZ 的完整 preclose unit，覆盖新增 2024-06-13 并重新验证 2024-06-14 predecessor parity。
4. 在单独授权后 bounded retry 300546.SZ；当前 R3 repair 已补齐其 2016-09-29 与 2016-10-10 blocker keys。
5. 随后以新 manifest 继续 3,315 个 UNVISITED symbols（含另外三个 repaired symbols）；任何失败继续 fail closed。

## Safety

NETWORK_PROVIDER_DATA_FETCH: `NO`
R4_DATA_WRITE_EXECUTED: `False`
R4A9_CHECKPOINT_MUTATED: `False`
R4A9_RESUME_AUTHORIZED: `False`
PRECLOSE_COMPLETE: `False`
R3_DAILY_USABLE: `True`
R4_EXECUTION_AUTHORIZED: `True` (entry status only; no resume)

No R4A9 execution, provider request, checkpoint mutation, or data-root write was performed by this audit.
