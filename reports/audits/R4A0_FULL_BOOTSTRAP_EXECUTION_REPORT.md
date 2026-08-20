# R4A0 RESUMABLE FULL BOOTSTRAP — EXECUTION REPORT (FAIL, CODE_CHANGE_REQUIRED)

DATE: 2026-08-20
BRANCH: codex/r4a0-resumable-full-bootstrap-v01
BASE_HEAD: 5aea707c8b02b799436e1092d1e67cd69b765c10

## 1. Verdict

**STATUS = CODE_CHANGE_REQUIRED** — the real execution stopped on chunk 1
due to an orchestrator↔adapter identity-dict contract bug. No market data was
written; the site is preserved for Sol audit. Per task section 7 no code was
modified in this task and no automatic re-run occurred.

## 2. Pre-flight (passed)

```text
GIT_HEAD == BASE_HEAD 5aea707...  true
PIN_MATCH true
FORMAL_IDENTITY_N 5456
FORMAL_IDENTITY_HASH 2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f
dry-run: EXPECTED 5456 / COVERED 24 / REMAINING 5432 / CHUNK 24 / CHUNK_COUNT 227
CHUNK_PLAN_HASH 9d34dd4b214945948edee3a1d584b31c7c5124c92545e354b9af41c049da6348
NETWORK / MANIFEST_WRITE / REAL_ROOT_WRITE NO
```

## 3. Real execution (single run, stopped at chunk 1)

```text
STATUS FULL_BOOTSTRAP_STOPPED
EXECUTION_STARTED true
START gate passed (R4A0_READY=false expected; blocker SYMBOL_COVERAGE_INCOMPLETE;
no correctness blockers)
stop_chunk_index 1
stop_reason CONFIG_UNKNOWN_OR_CHANGED
adapter chunk-1 result: STATUS=FORMAL_IDENTITY_MISMATCH, run_id=null
progress[0]: CHUNK_INDEX 1 RUN_ID null PILOT_COMPLETE false COVERED_N_AFTER 24
```

### Root cause

The adapter's identity gate checks `ident.get("IDENTITY_MATCH", False)`
(`r4a0_bounded_adapter.py`). The orchestrator passes the dict produced by
`r4a0_corporate_actions_gate.load_expected_identity`, whose keys are
`IDENTITY_STATUS / EXPECTED_SYMBOL_N / EXPECTED_SYMBOL_HASH / IDENTITY_SOURCE /
identity_ok / symbols` — **no `IDENTITY_MATCH`**. So the real path mis-fails
with `FORMAL_IDENTITY_MISMATCH` before `start_run`: no run, no market data, no
receipt.

Test gap: orchestrator tests inject a fake adapter (never the audited
`run_bounded_pilot`), and adapter tests inject identities that DO contain
`IDENTITY_MATCH` — the shape mismatch was not exercised.

## 4. Write boundary / site preservation

```text
PROTECTED_HASH_BEFORE == AFTER  e5504cb0...eef010  (unchanged)
CONFIG_SHA_BEFORE == AFTER      fac5abd136cb2ae0... (unchanged)
new manifest run                false
new corporate_actions rows      0
new corporate_actions parquet   0
```

The failure was fail-safe: only a read-only START gate had run. Refused to
auto-rerun; one execution attempt only.

## 5. Bounded next action (for Sol)

1. Adjudicate the small orchestrator/adapter identity-contract fix
   (recommendations recorded in the failure receipt), then
2. re-audit the fix commit, then
3. re-run the single `--exec` (same frozen plan / scope; resume authority is
   the manifest, which is unchanged at 24 covered).

Until then: FULL_BOOTSTRAP_EXECUTION stays forbidden by policy, R4A0_READY
remains false, and no R4A preclose is entered.
