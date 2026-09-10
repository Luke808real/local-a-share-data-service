# PROJECT_STATE

## Current operational snapshot — 2026-09-10

This section supersedes the historical 2026-08-18 snapshot below. It records
local observations, not a new independent audit PASS. Original R2 history is
preserved below.

- CURRENT_WORK: R3 daily incremental maintenance and read-only market-data MCP.
- BASE_CODE_COMMIT: `25f4f5434b7b99a1461cf56343552c9f50100695`
  (`feat: add local A-share read query MVP`).
- DATA_ROOT: `/Users/luke808/AI/local-a-share-data-service-data`.
- FOUNDATION: CNEquity; Parquet is the existing store, DuckDB is the read engine.
  The query runtime does not replace or update the ingestion runtime.
- DAILY_PHYSICAL_FILE_N: 2598; DAILY_PHYSICAL_ROW_N: 10803654
  (Parquet metadata, not a new historical quality scan).
- DAILY_PHYSICAL_LATEST: 2026-09-10.
- DAILY_PHYSICAL_MANIFEST_HASH:
  `140197cd3c95a3f8e1c9f5750f117c66eafc09d9113757e511f31e46848bf66b`.
- R3_PROMOTED_DAILY_FILE_N: 2598; R3_PROMOTED_DAILY_AS_OF: 2026-09-10.
- R3_PROMOTED_DAILY_MANIFEST_HASH:
  `140197cd3c95a3f8e1c9f5750f117c66eafc09d9113757e511f31e46848bf66b`.
- DAILY_COVERAGE_STATUS: PARTIAL; FULL_HISTORY_CERTIFIED: false.
- KNOWN_HISTORICAL_QUALITY_EXCEPTION_N: 15047 (prior authority, not re-audited).
- FORMAL_IDENTITY_N: 5456 (SH/SZ formal R3 scope; not all instrument rows).
- Incremental dates 2026-08-18 through 2026-09-10 have committed quality,
  provenance, and coverage receipts. The 2026-09-08 run classified nine
  requested-but-unobserved primary keys as same-date BaoStock suspensions; the
  2026-09-09 and 2026-09-10 runs observed every one of their 5,208 eligible
  frozen symbols with zero unresolved keys and zero source errors, so neither
  needed a secondary classification. The authority pointer binds the complete
  2,598-file manifest; the query runtime verifies it before use.
- QUERY_BASELINE: use the exact pointer-bound promoted file universe only.
- R4A9: prior summary COMPLETE=2146, UNVISITED=3310. The original checkpoint
  hash was rechecked unchanged:
  `d013e171734d9c688e8c370163a617f35ce45c55c134ca6cee655b2a2c1e8f7b`.
- PRECLOSE_COMPLETE: false; FACTS_READY: false.
- FULL_R4A9_CONTINUATION_AUTHORIZED: false; R7_FIRST_PUBLISH_PASS: false.
- DAILY_FACTS_PHASE1_STATUS: FULL_ELIGIBLE_ONE_DAY_PUBLISHED;
  DAILY_FACTS_PHASE1_SCOPE: `2026-09-09_FULL_ELIGIBLE`. The independent Facts
  pointer binds the vertical slice plus a 5,208-row 2026-09-09 partition at
  manifest hash `9b4f474ebcb0db97d9dbfdb824eb6b7e17f017a6ac8957ed3d6a8fb5e3ca21ac`.
  The 16 exact-date preclose exceptions are bound to hash-verified official
  CNInfo implementation announcements and reconcile as reference-price
  exceptions; they do not change the R3 daily authority.
- DAILY_FACTS_2026_09_10_STATUS: NOT_STARTED. A single-date ledger
  (`staging/daily_facts_phase1_20260910_v01/progress.sqlite`, 5,208 units) was
  created and then stopped by operator instruction before any RAW evidence was
  persisted; no curated partition, manifest, or pointer change exists for
  2026-09-10 Daily Facts. The R3 2026-09-10 publication above is complete and
  unaffected.
- MCP_STATUS: `status`, `instrument`, `bars`, `latest`, and `facts` are
  read-only published-authority tools. Local JSON-RPC validation succeeded for
  the 2026-09-10 R3 authority and the scoped 2026-09-09 Daily Facts authority;
  this is operational evidence, not an independent audit PASS.
- CURRENT_READY:
  - R3 Daily RAW: READY (published through 2026-09-10; coverage remains
    PARTIAL).
  - Identity: READY (5456 formal SH/SZ instruments).
  - Publication Authority: READY (pointer-bound 2,597-file manifest).
  - LocalQuery: READY (published-manifest allowlist only).
  - MCP: READY (read-only `status`/`instrument`/`bars`/`latest` plus scoped
    `facts` authority reads).
- CURRENT_NOT_READY:
  - Formal Preclose: NOT READY.
  - Daily Facts: NOT READY globally; the 2026-09-09 full-eligible one-day
    scope is separately published and queryable through `facts`.
  - Price Limit Facts: NOT READY.
  - Turnover Facts: NOT READY.
  - ST / Trade Status Facts: NOT READY.
  - Share Capital Facts: NOT READY.
  - 5m Publication: NOT READY.
  - Limit Event Facts: NOT READY.
- NEXT_GATE: a separately authorized bounded Daily Facts expansion/backfill.
  Do not set global readiness until full intended scope has an independent
  authority and certification.
- DAILY_FACTS_2026_09_09_OFFLINE_CERTIFICATION: PASS. The frozen full eligible
  one-day ledger has 5,208 persisted RAW and 5,208 normalized facts. Ten
  BaoStock `SUSPENDED` values align with R3 zero-volume/zero-amount
  carry-forward bars. Sixteen preclose mismatches have exact-date,
  hash-verified official reference-price evidence and were published only
  after pointer-last certification; global Facts readiness is unchanged.
- ACTIVE_BACKGROUND_JOB: login LaunchAgent `io.asl.market-data-mcp`, serving
  read-only MCP at `http://127.0.0.1:8766/mcp`. The private Secure MCP Tunnel
  runtime targets this loopback endpoint; its connectivity is operationally
  separate from R3 publication.
- NETWORK_PROVIDER_JOB: the one-shot BaoStock acquisition for the 2026-09-09
  bounded scope completed with 5,208 persisted RAW units and zero provider
  failures. Its staging output is not a publication authority.
- DATA_CLEANUP_OR_REPAIR: preserve all physical incremental partitions and
  their audit evidence; do not treat physical files as authority without the
  corresponding committed receipt and pointer switch.

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
