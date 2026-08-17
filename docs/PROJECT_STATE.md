# PROJECT_STATE

AS_OF: 2026-08-18
SPEC_VERSION: V1.0 FROZEN

CURRENT_PHASE: R1 — LOCAL ASSET AUDIT

## CODE
BRANCH: research/r1-local-asset-audit
HEAD: SELF — commit containing this file
WORKTREE: DIRTY (expected only untracked `tests/__pycache__/`)

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

## BLOCKERS
1. READONLY_BREACH_INDETERMINATE: `/Users/luke808/AI/asl-shared/meta/manifest.db-wal` (0 B) and `manifest.db-shm` (32,768 B) appeared after mode=ro audit access; cleanup requires owner authorization.
2. UPSTREAM_CONTRACT_CONFLICT: trading_status source and semantics are inconsistent across the pinned upstream registry, executable step, and catalog.
3. NOT_PRESENT_AT_PIN: the target stock-level turnover contract is absent at pinned CNEquity v0.7.2.
4. No migration-ready legacy dataset: seven datasets are CROSSCHECK_ONLY and primary SW industry is REJECT.

LAST_AUDIT:
R0 AUDIT_PASS — exact commit
0a96271b1a62cf1e2ab4e6eae48b3905c3601414
independently reviewed via GitHub by GPT-5.6 Sol on 2026-08-17.
R1 AUTHOR_STATUS: BLOCKED — independent audit pending.

NEXT_ACTION:
Owner decision on the sidecars; do not delete, repair, or reopen them.
Then perform independent audit of the exact R1 commit.
DO_NOT_ACTIVATE_R2.
