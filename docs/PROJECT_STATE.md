# PROJECT_STATE

AS_OF: 2026-08-29
SPEC_VERSION: V1.0 FROZEN

## CURRENT_PHASE

CURRENT_PHASE: R3 DAILY_USABLE REPAIR — PENDING INDEPENDENT AUDIT

- HISTORICAL_R3_SHSZ_DAILY_FOUNDATION = PASS
- HISTORICAL_R3_SHSZ_CLOSEOUT = FROZEN
- DAILY_USABLE_CANDIDATE = TRUE
- DAILY_COVERAGE_STATUS = PARTIAL
- FULL_HISTORY_CERTIFIED = FALSE
- R4_EXECUTION_AUTHORIZED = FALSE
- R4_EXECUTION_AUTHORIZATION_STATUS = PENDING_INDEPENDENT_AUDIT
- BJ_EXTENSION = DEFERRED

The former R3 SH/SZ MVP (V08) closeout is historical evidence. The bounded
four-key repair has closed the four proven material daily defects, but the
current daily readiness remains a candidate pending independent audit. BJ
current/historical is a deferred extension and BJ_HISTORICAL_AUTHORITY is not
proven.

## CURRENT R3 DAILY USABILITY REPAIR

- PRE_INPUT_MANIFEST_HASH: `ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731`
- POST_INPUT_MANIFEST_HASH: `dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045`
- PROVEN_MATERIAL_DAILY_DEFECT_N_AFTER: `0`
- KNOWN_HISTORICAL_QUALITY_EXCEPTION_N: `15047`
- KNOWN_HISTORICAL_QUALITY_EXCEPTION_KEYSET_HASH: `4f2832a314d34dc0eb797d56ef69dcee12c977621febe838b2f046acd75ad195`
- DAILY_USABLE_CANDIDATE: `true`
- DAILY_COVERAGE_STATUS: `PARTIAL`
- FULL_HISTORY_CERTIFIED: `false`
- NEXT_ACTION: `SOL_INDEPENDENT_AUDIT_THEN_R4_RESUME_COMPATIBILITY`

The four repaired keys are the only material daily-defect repair scope in this
phase. The 15,047 historical quality exceptions remain tracked and unresolved;
they are not promoted to full-history certification.

## HISTORICAL READINESS REALIGNMENT (D026 PRE-REPAIR SNAPSHOT)

The following pre-repair snapshot is retained for lineage only. It does not
override the current readiness fields above and does not certify full historical
session coverage:

```text
DAILY_USABLE=false
DAILY_USABLE_BLOCKER=4_PROVEN_MISSING_TRADED_BARS
DAILY_COVERAGE_STATUS=PARTIAL
FULL_HISTORY_CERTIFIED=false
HISTORICAL_FORENSICS_STATUS=OPEN_NONBLOCKING
R4_EXECUTION_AUTHORIZED=false
```

Current frozen evidence references, without copying provider receipt corpora:

- `PROVEN_MATERIAL_DAILY_DEFECT_N=4`; keyset hash
  `49fd7d316e2a09bbb18f0b840d4a5034f3efb2dbba57e9f60255c7a8910b2663`, from
  `reports/implementation/R3_STATUS0_SECONDARY_AUTHORITY_PILOT_V01.json`
  (`ADJUDICATION_MANIFEST_HASH=fbfbd2dd29c35ca686bf3373c2b70aa24ff615f1d2c853542fa7ba3d334a3a30`).
- `STATUS_CONTRADICTION_UNKNOWN_N=4`, keyset hash
  `9c864bd40fad37bc5952b9ab12fc3ff805278aae5b942986c95a7ea7521580b7`.
- `STATUS0_DOUBLE_BLANK_UNKNOWN_N=15004`, keyset hash
  `006d47931ab65628abc6825e86baa2c1659e3661ab54b6fa386d796d4fd8960d`.
- `PROVIDER_ROW_ABSENT_UNKNOWN_N=39`, keyset hash
  `fff37a6ac7cff43ad1a9b6ce9265851f56ee9248f10a1f5544ec9cceb633a852`.

The three unresolved historical exception scopes are disjoint in the current
evidence, so `KNOWN_HISTORICAL_QUALITY_EXCEPTION_N=15047` (4 + 15004 + 39).
The four proven material defect keys are tracked separately as the current
hard blocker; the total tracked exception-related key count is 15051. The
known-exception count alone does not make `DAILY_USABLE=false`; the four
concrete material missing bars are the current hard blocker. The remaining
historical forensic evidence is
`HISTORICAL_FORENSIC_EVIDENCE` and `NONBLOCKING_FOR_DAILY_USABLE` unless it
proves another concrete material daily-bar defect.

## HISTORICAL V08 CODE METADATA (AS OF 2026-08-19)

BRANCH: codex/r3-v08-shsz-closeout-r4-handoff-v01
HEAD: SELF — commit containing this file
CODE_HEAD: 3914b7a4988f3d202eba5b6b81b3069aec78bd4e
PLAN_SHA: 3ab1f184edeea1d0e408c45df4a706248b6558d0
V08_SCOPE_DECISION_SHA: 00085fed36f50312b6a5475dc26f0c5e347c6768
WORKTREE: TRACKED_CLEAN

## CODE

BRANCH: codex/r3-proven-missing-4key-repair-v01
HEAD: SELF — commit containing this file
BASE_HEAD: c820d5897720e24d9cd65a61523016fb8d581292
WORKTREE: TRACKED_CLEAN_AT_COMMIT

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
daily_bars: BUILT (10,709,995 rows; SH/SZ MVP: 5208 active + 248 formal delisted) — R3 Stages E/F plus bounded four-key repair
trading_status: NOT_BUILT — CONTRACT_FROZEN_R4_IMPLEMENTATION_REQUIRED
turnover: NOT_BUILT — CONTRACT_FROZEN_R4_IMPLEMENTATION_REQUIRED
5m: NOT_BUILT
adj: NOT_BUILT
industry: NOT_BUILT
index: NOT_BUILT

## HISTORICAL R3 SH/SZ MVP FINAL STATE (AS_OF 2026-08-17)

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
R3 SH/SZ MVP CLOSEOUT AUDIT_PASS — exact pushed commit
15f1960e3cfb0ec6bda82052828e4dcf31e935c1
independently reviewed via GitHub by GPT-5.6 Sol on 2026-08-19
(R3_V08_SHSZ_CLOSEOUT_FACT_FIX01; exact single child of 1d52ace9;
 F-history correction PASS; R3_SHSZ_CLOSEOUT = FROZEN).

R3 SH/SZ MVP runtime/author: staged executions were completed on the real root;
G PASS was author status and has since received the independent audit PASS
recorded above. R3_SHSZ_CLOSEOUT is FROZEN at the audited exact commit.

## LINEAGE (R3 SH/SZ MVP)

- IDENTITY_RECEIPT_SHA: 51a302b1d6273e8dc40b9f6b69e75a4176e08821caa0a138a3ab6467a974e946
- E_RECEIPT_SHA: f58d98c08a07e4cebd21fceac74fdd722cbd98eb7afa2f4f2d02099263a36744
- V073_CHECKPOINT_SHA: 09e741137a3ed5a33571d7c77acb8c9e3ebcc42eff68fd5c679f23e6aa979638
- FINAL_F_RUN: fe498fbb-8a00-480c-8ac5-a715cd02200b

## NEXT_ACTION

SOL_INDEPENDENT_AUDIT_THEN_R4_RESUME_COMPATIBILITY

R4 execution remains unauthorized until the independent audit of this bounded
repair candidate passes. No R4/R4A9 resume has been authorized by this state
update; BJ extension remains deferred.
