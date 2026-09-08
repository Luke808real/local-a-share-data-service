# PROJECT_STATE

## Current operational snapshot — 2026-09-08

This section supersedes the historical 2026-08-18 snapshot below. It records
local observations, not a new independent audit PASS. Original R2 history is
preserved below.

- CURRENT_WORK: read-only daily publication boundary and market-data MCP.
- BASE_CODE_COMMIT: `25f4f5434b7b99a1461cf56343552c9f50100695`
  (`feat: add local A-share read query MVP`).
- DATA_ROOT: `/Users/luke808/AI/local-a-share-data-service-data`.
- FOUNDATION: CNEquity; Parquet is the existing store, DuckDB is the read engine.
  The query runtime does not replace or update the ingestion runtime.
- DAILY_PHYSICAL_FILE_N: 2595; DAILY_PHYSICAL_ROW_N: 10788039
  (Parquet metadata, not a new historical quality scan).
- DAILY_PHYSICAL_LATEST: 2026-09-07.
- DAILY_PHYSICAL_MANIFEST_HASH:
  `97aa4d16c82abfa144ab6c2d8fd2d9cde6dadb1e82e78889136fa5d950e56c7f`.
- R3_PROMOTED_DAILY_FILE_N: 2595; R3_PROMOTED_DAILY_AS_OF: 2026-09-07.
- R3_PROMOTED_DAILY_MANIFEST_HASH:
  `97aa4d16c82abfa144ab6c2d8fd2d9cde6dadb1e82e78889136fa5d950e56c7f`.
- DAILY_COVERAGE_STATUS: PARTIAL; FULL_HISTORY_CERTIFIED: false.
- KNOWN_HISTORICAL_QUALITY_EXCEPTION_N: 15047 (prior authority, not re-audited).
- FORMAL_IDENTITY_N: 5456 (SH/SZ formal R3 scope; not all instrument rows).
- Incremental dates 2026-08-18 through 2026-09-07 have committed quality,
  provenance, and coverage receipts. The authority pointer binds the complete
  2,595-file manifest; the query runtime verifies it before use.
- QUERY_BASELINE: use the exact pointer-bound promoted file universe only.
- R4A9: prior summary COMPLETE=2146, UNVISITED=3310. The original checkpoint
  hash was rechecked unchanged:
  `d013e171734d9c688e8c370163a617f35ce45c55c134ca6cee655b2a2c1e8f7b`.
- PRECLOSE_COMPLETE: false; FACTS_READY: false.
- FULL_R4A9_CONTINUATION_AUTHORIZED: false; R7_FIRST_PUBLISH_PASS: false.
- MCP_STATUS: `status`, `instrument`, `bars`, and `latest` are read-only
  published-R3 tools. Local validation and a user-observed ChatGPT/MCP query
  both succeeded for the 2026-09-07 authority; this is operational evidence,
  not an independent audit PASS.
- CURRENT_READY:
  - R3 Daily RAW: READY (published through 2026-09-07; coverage remains
    PARTIAL).
  - Identity: READY (5456 formal SH/SZ instruments).
  - Publication Authority: READY (pointer-bound 2,595-file manifest).
  - LocalQuery: READY (published-manifest allowlist only).
  - MCP: READY (read-only `status`/`instrument`/`bars`/`latest`).
- CURRENT_NOT_READY:
  - Formal Preclose: NOT READY.
  - Daily Facts: NOT READY.
  - Price Limit Facts: NOT READY.
  - Turnover Facts: NOT READY.
  - ST / Trade Status Facts: NOT READY.
  - Share Capital Facts: NOT READY.
  - 5m Publication: NOT READY.
  - Limit Event Facts: NOT READY.
- NEXT_GATE: Daily Facts Phase 1 vertical slice under
  `docs/plans/ASL_MARKET_FACTS_NEXT_PHASE_V01.md`; no R3 authority rewrite,
  no facts exposure, and no readiness flag change before its own quality and
  publication authority are verified.
- ACTIVE_BACKGROUND_JOB: login LaunchAgent `io.asl.market-data-mcp`, serving
  read-only MCP at `http://127.0.0.1:8766/mcp`; no public tunnel configured.
- NETWORK_PROVIDER_JOB: none authorized or running.
- DATA_CLEANUP_OR_REPAIR: not authorized; preserve all physical incremental
  partitions and their audit evidence.

No provider catch-up or canonical/checkpoint mutation is authorized by this
read-access task. The incomplete incremental execution requires a separate
bounded quality/publication decision. R4/5m/industry/strategy work is not a
prerequisite for exposing the verified R3 RAW daily subset.

## Historical R2 snapshot — retained verbatim

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
