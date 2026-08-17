# PROJECT_STATE

AS_OF: 2026-08-18
SPEC_VERSION: V1.0 FROZEN

CURRENT_PHASE: R2 — CNEQUITY BASELINE
R2_EXECUTION: NOT_STARTED

## CODE
BRANCH: main
HEAD: SELF — commit containing this file
WORKTREE: DIRTY (pre-existing untracked local-only files; tracked tree clean)

## UPSTREAM_CNEQUITY
STATUS: CONTRACT_AUDITED_WITH_BLOCKERS
VERSION: v0.7.2
SHA: a18ee0484dfb0801650175471724def3228b8a17
LOCAL_PACKAGE: NOT_INSTALLED

## DATA_ROOT
STATUS: NOT_INITIALIZED
PATH: NOT_SELECTED

## DATASET_STATUS
instruments: CROSSCHECK_ONLY
daily: CROSSCHECK_ONLY
adj: CROSSCHECK_ONLY
trading_status: CROSSCHECK_ONLY — UPSTREAM_CONTRACT_CONFLICT
turnover: CROSSCHECK_ONLY — NOT_PRESENT_AT_PIN
5m: CROSSCHECK_ONLY
industry: REJECT — ABSENT_PRIMARY_SW_INDUSTRY
index: CROSSCHECK_ONLY

## LATEST_GOOD_AS_OF
NOT_PUBLISHED

## R1_READONLY_INCIDENT
STATUS: READONLY_BREACH_INDETERMINATE — historical classification retained.
OWNER_RECOVERY: AUTHORIZED_AND_COMPLETED on 2026-08-18.
The exact 0 B WAL and 32,768 B SHM sidecars were moved to macOS Trash with
matching inodes; `manifest.db` size, inode, mtime, and ctime remained unchanged.
This recovery does not convert the R1 audit into strict read-only PASS.

## R2_CARRY_FORWARD
1. UPSTREAM_CONTRACT_CONFLICT: freeze one evidence-backed historical `trading_status` contract before R2 exit; do not silently choose among conflicting upstream declarations.
2. NOT_PRESENT_AT_PIN: define the thin V1 stock-level turnover extension contract before R2 exit; no provider has been selected or accepted.
3. No migration-ready legacy dataset: seven datasets remain CROSSCHECK_ONLY and primary SW industry remains REJECT; no legacy row is authorized for migration.
4. All data acquisition, data-root initialization, and package installation remain R2 task-scoped actions requiring the approved R2 plan.

LAST_AUDIT:
R0 AUDIT_PASS — exact commit
0a96271b1a62cf1e2ab4e6eae48b3905c3601414
independently reviewed via GitHub by GPT-5.6 Sol on 2026-08-17.
R1 AUDIT_PASS — exact pushed commit
09e9254042ad747983d40d794595135fb58e2d80
independently reviewed by GPT-5.6 Terra/max and adjudicated by the GPT-5.6 Sol root on 2026-08-18.

NEXT_ACTION:
Prepare and independently review the R2 CNEQUITY BASELINE implementation plan,
then execute R2 without weakening the carry-forward contracts.
Do not execute R3 before R2 AUDIT_PASS.
