# R4A9.1 300546.SZ PRECLOSE QUALITY DIAGNOSIS — V01 (author report)

DATE: 2026-08-24
REPORT_BASE_HEAD: e8c097db5375fb2db1a422e298c67facc2dcf6b3
DIAGNOSTIC_RUNTIME_HEAD: 795b1b8f6b688ecc2e94f85c09d80c365e648920 (detached)
TARGET: 300546.SZ (SZ ChiNext)

## EVIDENCE SOURCE

DIAGNOSTIC_EVIDENCE_SOURCE=TARGETED_SINGLE_SYMBOL_REFETCH
Original R4A9 execution stdout did not preserve the per-unit bounded-adapter
counters (only the FAILED receipt text), so exactly ONE bounded refetch of
300546.SZ (11 yearly windows, in-memory normalized via the audited adapter)
was performed on a detached worktree at exact 795b1b8f6b688ecc2e94f85c09d80c365e648920.
NETWORK_PROVIDER_DATA_FETCH=YES_BOUNDED_1_SYMBOL_11_WINDOWS (+1 diagnostic
cross-check window 2016-09-01..2016-10-15 for the same symbol; total 12 queries, all 300546.SZ)

## COUNTERS (in-memory bounded adapter, frozen contract)

REQUIRED_ROW_N=2397
PROVIDER_ROW_N=2399
FORMAL_FACT_ROW_N=2397
MISSING_REQUIRED_N=0
PROVIDER_SUSPENDED_SUPERSET_N=0
UNEXPECTED_TRADED_N=2
TRADESTATUS_UNKNOWN_N=0
IDENTITY_FAILURE_N=0
WINDOW_SCOPE_FAILURE_N=0
DUPLICATE_N=0
POST_ASOF_N=0
INVALID_PRECLOSE_N=0
QUALITY_GATE_PASS=false

## BLOCKING ROWS

2016-09-29 provider_tradestatus=1 preclose=29.42 R3_BAR_PRESENT=false R3_REQUIRED=false
  previous R3 traded: 2016-09-28, next R3 traded: 2016-09-30
2016-10-10 provider_tradestatus=1 preclose=35.60 R3_BAR_PRESENT=false R3_REQUIRED=false
  previous R3 traded: 2016-09-30, next R3 traded: 2016-10-11

## R3 SIDE

instrument list_date=2016-09-28 (IPO first listing day)
first R3 trade_date=2016-09-28
last R3 trade_date=2026-08-17
required key count=2397
R3 daily_bars missing exactly: 2016-09-29 and 2016-10-10

## CROSS-CHECK (provider vs R3 previous close)

provider 2016-09-29 preclose 29.42 == R3 2016-09-28 close 29.42  (exact)
provider 2016-10-10 preclose 35.60 == R3 2016-09-30 close 35.60  (exact)
=> BaoStock rows are exchange-calendar-consistent; the gap is on the R3 side.

## ROOT CAUSE CLASSIFICATION

ROOT_CAUSE_CLASSIFICATION=R3_REQUIRED_KEY_MISMATCH
NOT_CODE_DEFECT=true (audited adapter failed closed exactly as designed)
NOT_PROVIDER_ANOMALY=true (BaoStock tradestatus/preclose consistent with exchange)
The frozen R3 required universe omits two real traded days of 300546.SZ;
the provider rows are UNEXPECTED_TRADED blockers by the frozen contract.
No one-off exception created; quality_gate_pass untouched; UNKNOWN/UNEXPECTED
not downgraded.

## R4A9 CHECKPOINT

R4A9_COMPLETE_N=2140
R4A9_FAILED_N=1
R4A9_UNVISITED_N=3315
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false

## SAFETY

NETWORK_PROVIDER_DATA_FETCH=YES_BOUNDED_1_SYMBOL_11_WINDOWS (+1 diagnostic window, same symbol)
CANONICAL_MARKET_DATA_WRITE=NO
R4A9_STAGING_MUTATION=NO
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
