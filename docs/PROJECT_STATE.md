# PROJECT_STATE

AS_OF: 2026-08-19
SPEC_VERSION: V1.0 FROZEN

## CURRENT_PHASE

CURRENT_PHASE: R3 SH/SZ MVP COMPLETE — R4 SH/SZ PLANNING NEXT

- R3_SHSZ_DAILY_FOUNDATION = PASS
- ALL_A_DAILY_READY = FALSE
- DAILY_READY = FALSE
- BJ_EXTENSION = DEFERRED

This records the R3 SH/SZ MVP (V08) closeout only. The full all-A R3 / DAILY_READY
exit (ROADMAP semantics) is NOT complete; BJ current/historical is a deferred
extension and BJ_HISTORICAL_AUTHORITY is not proven.

## CODE

BRANCH: codex/r3-v08-shsz-closeout-r4-handoff-v01
HEAD: SELF — commit containing this file
CODE_HEAD: 3914b7a4988f3d202eba5b6b81b3069aec78bd4e
PLAN_SHA: 3ab1f184edeea1d0e408c45df4a706248b6558d0
V08_SCOPE_DECISION_SHA: 00085fed36f50312b6a5475dc26f0c5e347c6768
WORKTREE: TRACKED_CLEAN

## UPSTREAM_CNEQUITY

STATUS: BASELINE_PINNED
VERSION: v0.7.2
SHA: a18ee0484dfb0801650175471724def3228b8a17
LOCAL_PACKAGE: INSTALLED_EXACT_GIT_PIN
PYTHON: CPython 3.12.13
LOCK_SHA256: 5f233fa9434624391c06e56a4596edfd52c1ec596d66688753b78f424dd571ac
DIRECT_URL: git+https://github.com/rootSunc/CNEquity.git @ a18ee0484dfb0801650175471724def3228b8a17

## DATA_ROOT

STATUS: R3_SHSZ_MVP_BUILT
PATH: /Users/luke808/AI/local-a-share-data-service-data
CONFIG_PATH: config/cnequity.toml
CONFIG_SHA256: fac5abd136cb2ae00c07d7ca408eb1d47eed69c26c3547a0547ef9d214063fb5

## DATASET_STATUS

instruments: BUILT (7757 rows) — R3 Stage A
trading_calendar: BUILT (4247 rows) — R3 Stage D
daily_bars: BUILT (10,709,989 rows; SH/SZ MVP: 5208 active + 248 formal delisted) — R3 Stages E/F
trading_status: NOT_BUILT — CONTRACT_FROZEN_R4_IMPLEMENTATION_REQUIRED
turnover: NOT_BUILT — CONTRACT_FROZEN_R4_IMPLEMENTATION_REQUIRED
5m: NOT_BUILT
adj: NOT_BUILT
industry: NOT_BUILT
index: NOT_BUILT

## R3 SH/SZ MVP FINAL STATE (AS_OF 2026-08-17)

### Scope / Authority

- FORMAL_IDENTITY_AUTHORITY: BAOSTOCK_QUERY_STOCK_BASIC (scope SH_SZ_MVP)
- FORMAL_IDENTITY_N: 5456
- FORMAL_IDENTITY_HASH: 2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f
- ACTIVE_REQUIRED_N: 5208
- ACTIVE_OBSERVED_N: 5208
- FORMAL_DELISTED_N: 248
- E_RECOVERED_N: 248
- E_UNRESOLVED_N: 0

### Daily dataset

- DAILY_ROWS: 10,709,989
- DAILY_SYMBOL_N: 5456
- TDX_ROWS: 10,325,794
- BAOSTOCK_ROWS: 384,195
- MIN_TRADE_DATE: 2016-01-04
- MAX_TRADE_DATE: 2026-08-17

### QA

duplicate_pk=0 · null_pk=0 · post_asof=0 · invalid_ohlc=0 ·
negative_volume=0 · negative_amount=0 · missing_required=0 ·
without_positive_volume=0 · out_of_effective_span=0

### Stage G closeout

- G_REPORT_SHA: 2e843c72bd0b32ea36b84dd8b6277a4e4e2a292fd39fe61c625bd5a6bedf67fe
- G_SCOPE: SH_SZ_MVP
- G_CLAIM: formal_identity_survivorship_coverage
- G_AUTHORITY: BAOSTOCK_QUERY_STOCK_BASIC
- R3_SHSZ_VERIFIED: true
- KNOWN_COVERAGE_COMPLETE: true
- HARD_BLOCKERS: 0
- LEGACY_DISCOVERY_COMPLETE: false (preserved; NOT written as complete)
- LEGACY_PENDING_PROBE: 30582
- LEGACY_DISCOVERY_STATUS: DEFERRED_NON_AUTHORITY

Note: `r3-delisted-coverage.json` records both the R3 verdict and the untouched
upstream verdict (verified=false) separately. Legacy Sina discovery is a
non-authority observation, never a completion gate.

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
4. BJ current/historical identity is DEFERRED_EXTENSION; BJ_HISTORICAL_AUTHORITY
   is not proven (UNKNOWABLE under the frozen V08 bounded research), so any
   all-A daily readiness remains FALSE.

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

R3 SH/SZ MVP runtime/author: staged executions were completed on the real root;
G PASS is author status. A formal independent audit of the exact pushed R3
closeout commit remains pending with GPT-5.6 Sol.

## LINEAGE (R3 SH/SZ MVP)

- IDENTITY_RECEIPT_SHA: 51a302b1d6273e8dc40b9f6b69e75a4176e08821caa0a138a3ab6467a974e946
- E_RECEIPT_SHA: f58d98c08a07e4cebd21fceac74fdd722cbd98eb7afa2f4f2d02099263a36744
- V073_CHECKPOINT_SHA: 09e741137a3ed5a33571d7c77acb8c9e3ebcc42eff68fd5c679f23e6aa979638
- FINAL_F_RUN: fe498fbb-8a00-480c-8ac5-a715cd02200b

## NEXT_ACTION

R4 SH/SZ PLANNING handoff:

1. Prepare the R4 Stable Market Facts implementation plan (SH/SZ scope).
2. Priority facts:
   preclose · trading_status / ST / STAR_ST / suspension ·
   turnover_rate · high_limit / low_limit · is_limit_up / is_limit_down.
3. Prefer existing CNEquity contracts/providers first.
4. No R4 real execution until the R4 plan passes independent audit.
5. BJ extension remains deferred.

- R4_SHSZ_PLANNING = AUTHORIZED
- R4_SHSZ_REAL_EXECUTION = FORBIDDEN_PENDING_PLAN_AUDIT

Do not execute R4 real stages before the independent R4 plan audit PASS.
