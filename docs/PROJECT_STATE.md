# PROJECT_STATE

AS_OF: 2026-08-18
SPEC_VERSION: V1.0 FROZEN

CURRENT_PHASE: R3 — DAILY FOUNDATION
R3_EXECUTION: NOT_STARTED

## CODE

BRANCH: main
HEAD: SELF — commit containing this file
WORKTREE: DIRTY (pre-existing local-only untracked files; tracked tree clean)

## UPSTREAM_CNEQUITY

STATUS: BASELINE_PINNED
VERSION: v0.7.2
SHA: a18ee0484dfb0801650175471724def3228b8a17
LOCAL_PACKAGE: INSTALLED_EXACT_GIT_PIN
PYTHON: CPython 3.12.13
LOCK_SHA256: 5f233fa9434624391c06e56a4596edfd52c1ec596d66688753b78f424dd571ac
DIRECT_URL: git+https://github.com/rootSunc/CNEquity.git @ a18ee0484dfb0801650175471724def3228b8a17

## DATA_ROOT

STATUS: INITIALIZED_LAYOUT_ONLY_ZERO_DATA
PATH: /Users/luke808/AI/local-a-share-data-service-data
CONFIG_PATH: config/cnequity.toml
CONFIG_SHA256: fac5abd136cb2ae00c07d7ca408eb1d47eed69c26c3547a0547ef9d214063fb5
TREE_SNAPSHOT_SHA256: ddcf9dc509b6bfb0cea8bd27511360ba6d1b4151b4a745f3e0fcb230ecd43dd5
LAYOUT_ENTRIES: 18
MANIFEST_DB_BYTES: 28672
DUCKDB_BYTES: 274432
SIDECARS: NONE_OBSERVED

## DATASET_STATUS

instruments: NOT_BUILT
daily: NOT_BUILT
adj: NOT_BUILT
trading_status: NOT_BUILT — CONTRACT_FROZEN_R4_IMPLEMENTATION_REQUIRED
turnover: NOT_BUILT — CONTRACT_FROZEN_R4_IMPLEMENTATION_REQUIRED
5m: NOT_BUILT
industry: NOT_BUILT
index: NOT_BUILT

## LATEST_GOOD_AS_OF

NOT_PUBLISHED

## R1_READONLY_INCIDENT

STATUS: READONLY_BREACH_INDETERMINATE — historical classification retained.
OWNER_RECOVERY: AUTHORIZED_AND_COMPLETED on 2026-08-18.
The exact 0 B WAL and 32,768 B SHM sidecars were moved to macOS Trash with
matching inodes; `manifest.db` size, inode, mtime, and ctime remained unchanged.
This recovery does not convert the R1 audit into strict read-only PASS.

## CROSS_PHASE_CARRY_FORWARD

1. on_demand.enabled=false is not an enforceable network guard at this pin;
   upstream query, MCP, and live interfaces remain excluded until R8 installs
   and tests a local-only guard.
2. Trading-status and turnover contracts are frozen fail-closed. Provider selection, coverage proof, and implementation remain R4 work.
3. No legacy migration input is authorized. R1 assets remain `CROSSCHECK_ONLY`
   or `REJECT`; R3 must not copy or normalize them as canonical rows.
4. The initialized root is layout-only zero-data: manifest ingestion runs and
   batches are zero, and DuckDB contains metadata views only.

## R2_AUDIT

R2_AUTHOR_STATUS: PASS — AUTHOR_ONLY
R2_AUDIT_STATUS: AUDIT_PASS
AUDITED_COMMIT: e354f59297cc2cf9722304f39a315712761d4b91
AUTHOR_REPORT: reports/audits/R2_CNEQUITY_BASELINE_AUTHOR_REPORT.md
INDEPENDENT_REPORT: reports/audits/R2_CNEQUITY_BASELINE_INDEPENDENT_REPORT.md

LAST_AUDIT:
R0 AUDIT_PASS — exact commit
0a96271b1a62cf1e2ab4e6eae48b3905c3601414
independently reviewed via GitHub by GPT-5.6 Sol on 2026-08-17.
R1 AUDIT_PASS — exact pushed commit
09e9254042ad747983d40d794595135fb58e2d80
independently reviewed by GPT-5.6 Terra/max and adjudicated by the GPT-5.6 Sol root on 2026-08-18.
R2 AUDIT_PASS — exact pushed commit
e354f59297cc2cf9722304f39a315712761d4b91
independently reviewed by GPT-5.6 Terra/max and adjudicated by the GPT-5.6 Sol root on 2026-08-18.

NEXT_ACTION:
Prepare and independently audit the R3 DAILY FOUNDATION implementation plan,
then execute R3 without treating R1 crosscheck evidence as migration input.
Do not execute R4 before R3 AUDIT_PASS.
