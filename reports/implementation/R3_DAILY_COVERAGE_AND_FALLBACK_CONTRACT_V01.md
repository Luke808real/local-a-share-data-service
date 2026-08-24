# R3 DAILY COVERAGE AND FALLBACK CONTRACT - V01 (author report)

DATE: 2026-08-24
BASE_HEAD: 27f813598c09f48caf56b82073cd0b28b9461756

## EXPECTED-KEY AUTHORITY PILOT

Scope: frozen 24-symbol R4A pilot + 300546.SZ (25 symbols, 275 BaoStock
query windows). No full-market scan.

EXPECTED_TRADED_KEYS = BaoStock rows with tradestatus==1 within instrument
lifetime and AS_OF (independent of curated bars).

Result: 24/24 pilot symbols exact match (expected == observed). Only
300546.SZ has discrepancies:

300546.SZ: EXPECTED=2399 OBSERVED=2397 MISSING=2 UNEXPECTED=0
  missing = 2016-09-29, 2016-10-10

EXPECTED_KEY_AUTHORITY_DECISION=BAOSTOCK_TRADESTATUS_AUTHORITY_VALIDATED_BOUNDED

## DISCREPANCY CLASSIFICATION

300546.SZ 2016-09-29 / 2016-10-10 -> R3_REAL_MISSING_BAR
  BaoStock tradestatus=1, healthy TDX endpoint absent on both dates,
  staging & curated absent, both dates are exchange trading days.
Other 24 pilot symbols: no discrepancies (no provider anomaly, no lifetime
boundary issue, no suspension semantics observed in sample).

## SECONDARY OHLCV (300546.SZ, bounded two dates)

BaoStock daily OHLCV (query_history_k_data_plus d/adjustflag=3):

2016-09-29  O=32.36 H=32.36 L=32.36 C=32.36  V=100(unit TBD)  A=3236
2016-10-10  O=39.16 H=39.16 L=39.16 C=39.16  V=100(unit TBD)  A=3916

Cross-source consistency on overlapping TDX dates (close + amount):

2016-09-28  TDX C=29.42 == BaoStock C=29.42  amount 105179 equal
2016-09-30  TDX C=35.60 == BaoStock C=35.60  amount 56960  equal
2016-10-11  TDX C=43.08 == BaoStock C=43.08  amount 189552 equal

close prices and amounts match exactly on all overlaps; volume numeric
scale differs (TDX vs BaoStock), so volume-unit compatibility is NOT
silently assumed and requires an explicit unit contract before any
fallback write.

SECONDARY_OHLCV_SOURCE_DECISION=BAOSTOCK (validated bounded)

## FALLBACK CONTRACT CANDIDATE

PRIMARY_OHLCV_SOURCE = TDX
SECONDARY_OHLCV_SOURCE = BaoStock daily OHLCV (unadjusted)

Fallback permitted ONLY when:
  - external expected-key authority says traded,
  - canonical TDX bar absent,
  - secondary row exists,
  - secondary row passes field/unit/provenance gates (explicit
    volume-unit contract required),
  - no conflicting TDX row exists.

Every fallback row carries explicit provenance. UNKNOWN -> no repair.

FALLBACK_CONTRACT_DECISION=CANDIDATE (Sol audit required)

## REPAIR DECISION

REPAIR_AUTHORITY_DECISION=R3_TARGETED_SECONDARY_REPAIR_AUTHORIZED

Secondary authority provides both missing rows; close/amount parity proven
on all three overlapping dates. Repair remains unexecuted here and requires
Sol approval plus a versioned normalizer with explicit volume-unit contract.

## COMPLETENESS CONTRACT CANDIDATE

DAILY_HISTORY_COMPLETE = identity exact AND EXPECTED_TRADED_KEY_N == OBSERVED_TRADED_KEY_N AND MISSING_EXPECTED_TRADED_KEY_N == 0 AND UNEXPECTED_OBSERVED_KEY_N == 0 AND DUPLICATE_KEY_N == 0 AND authority coverage complete (UNKNOWN authority -> false)

CANDIDATE only; old R3 frozen state untouched; no promotion.

## SAFETY

R3_MARKET_DATA_WRITE=NO
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false
FULL_MARKET_SCAN=NO

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
