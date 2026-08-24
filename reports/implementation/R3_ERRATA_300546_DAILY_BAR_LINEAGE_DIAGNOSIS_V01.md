# R3 ERRATA 300546.SZ DAILY BAR LINEAGE DIAGNOSIS — V01 (author report)

DATE: 2026-08-24
REPORT_BASE_HEAD: d73952360c8e568c045d9cfa61c63c84cd444148
TARGET: 300546.SZ  DATES: 2016-09-29, 2016-10-10

## LINEAGE MATRIX

| date | source/raw | staging | curated |
|------|-----------|---------|---------|
| 2016-09-29 | UNKNOWN_RAW_NOT_RETAINED | ABSENT | ABSENT |
| 2016-10-10 | UNKNOWN_RAW_NOT_RETAINED | ABSENT | ABSENT |

Raw layer is empty/absent; all 3 TDX staging runs inspected contain the same
2397-row series as curated for 300546.SZ, with both dates missing in BOTH
layers. Staging is the normalized TDX fetch output -> the rows were already
absent from the TDX response at ingestion time (not a merge/drop defect).

## TDX REPRODUCTION (bounded, single symbol, read-only)

Attempted 300546.SZ window 2016-09-28..2016-10-11 via the exact pinned
cnequity tdx_protocol entry point.
Result: TDX_CALL_FAILED - no TDX server responded (probed 16 host(s); network
down or all feeds degraded).
TDX_2016_09_29_PRESENT=UNKNOWN_NETWORK_UNAVAILABLE
TDX_2016_10_10_PRESENT=UNKNOWN_NETWORK_UNAVAILABLE

## BAOSTOCK CROSS-CHECK (reuse of established R4A9.1 evidence)

BAOSTOCK_2016_09_29_PRESENT=true (tradestatus=1, preclose=29.42)
BAOSTOCK_2016_10_10_PRESENT=true (tradestatus=1, preclose=35.60)
Both preclose values exactly equal R3 previous close; vendor calendar-consistent.

## R3 SIDE

list_date=2016-09-28  first bar=2016-09-28  last bar=2026-08-17
required key count=2397
2016-09-29 in trading_calendar=true  in required_keys=false
2016-10-10 in trading_calendar=true  in required_keys=false

## ROOT-CAUSE CLASSIFICATION

ROOT_CAUSE_CLASSIFICATION=TDX_SOURCE_HISTORICAL_GAP (qualified by
TDX_NETWORK_UNAVAILABLE: cannot yet separate a true TDX historical gap from
transient feed unavailability because live TDX is unreachable)

## R3 COMPLETENESS GATE BLIND SPOT

R3_COMPLETENESS_GATE_BLIND_SPOT=true
Required keys are derived from observed curated daily_bars (self-referential):
a day missing from curated bars is automatically missing from the required set,
so R3's missing_required=0 could not detect holes in the source. The trading
calendar and G_coverage delisted-survivorship gate do not cross-check per-symbol
traded-day presence against an external trading-status authority.

## REPAIR AUTHORITY DECISION

REPAIR_AUTHORITY_DECISION=NO_REPAIR_YET
TDX live validation is blocked by network; BaoStock confirms the two days are
trading days (tradestatus=1) but no authority has yet supplied OHLCV for these
two rows. Options A (TARGETED_TDX_REPAIR_ALLOWED) and B (R3_SOURCE_CONTRACT_AMENDMENT)
can only be decided after a live TDX or secondary OHLCV validation.

## RECEIPT ERRATA

R4A9_1_ERRATUM_RECORDED=true
CORRECTION: R4A9.1 '294 missing bars' -> '2 missing bars' (2016-09-29, 2016-10-10)
Recorded here; R4A9.1 history is not silently rewritten.

## SAFETY

R3_MARKET_DATA_WRITE=NO
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
CANONICAL_MARKET_DATA_WRITE=NO
PRECLOSE_COMPLETE=false

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
