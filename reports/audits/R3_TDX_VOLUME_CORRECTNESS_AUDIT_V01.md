# R3 TDX VOLUME CORRECTNESS AUDIT - V01 (author report)

DATE: 2026-08-24
BASE_HEAD: 85036c0f8e1abfef178ee6488ff0254364d8c306

## INPUT (canonical daily_bars, read-only)

DAILY_ROW_N=10,709,989  SYMBOL_N=5,456
source: tdx_protocol 10,325,794 / baostock 384,195 (all v2)

## PINNED CONTRACT (CNEquity a18ee0484...) - CODE EVIDENCE

TDX_NATIVE_DAILY_VOLUME_UNIT=hand (lots) per cnequity.domain.units
TDX_ADAPTER_VOLUME_TRANSFORM=lots_to_shares x100 in bars._parse_bar_rows
  (volume_in_lots=not is_index -> True for stocks)
R3_CANONICAL_VOLUME_UNIT=shares (v2)
BAOSTOCK normalizer: volume pass-through, no transform

## VWAP INVARIANT (tdx_protocol, v>0, a>0, finite)

TDX_POSITIVE_ROW_N=10,325,793
VWAP_IN_RANGE_N=10,306,877  BELOW_LOW=1,307  ABOVE_HIGH=17,609
VWAP_IN_RANGE_RATE=0.998168

## UNIT-RATIO DISTRIBUTION (R_CLOSE = amount/(close*volume))

p01=0.966 p05=0.983 p25=0.995 p50=1.0001 p75=1.006 p95=1.019 p99=1.036
cluster ~1: 10,324,680   cluster ~100: 0
=> x100 transform IS applied; canonical volume is share-scaled globally.

## STRATIFICATION

SH median 1.0001 / SZ median 1.0002; years 2016-2026.
Extreme rows (R_CLOSE<0.5): 1,113 concentrated 2016(467)/2017(603).
Top VWAP failures: 000908.SZ(42) 000609.SZ(39) 600289.SH(39) 600734.SH(36)
603843.SH(36) 603007.SH(33) 603389.SH(32) 603779.SH(31).

## ONE-PRICE STRONG CHECK

ONE_PRICE_ROW_N=30,909  EXACT=13,693  MISMATCH=17,216
mismatch clusters: ~0.01 x181, ~1 x16,205, ~100 x0

## CONFIRM 300546.SZ (canonical)

2016-09-28  V11200  A105179   A/V=9.391  PASS
2016-09-30  V205600 A56960    A/V=0.277  FAIL
2016-10-11  V22400  A189552   A/V=8.462  FAIL
300546 has consecutive FAIL days (09-30, 10-11, 10-13..10-18): volume
~100x too large while amount matches BaoStock.

## SOURCE COMPARISON

BAOSTOCK_POSITIVE_ROW_N=384,194  VWAP_FAILURE_N=110  ONE_PRICE_MISMATCH_N=21
BaoStock rc p01=0.963 p50=1.000 p99=1.038 (near perfect)
=> anomaly is TDX-source-specific.

## ROOT CAUSE

ROOT_CAUSE_CLASSIFICATION=MIXED_TDX_VOLUME_SEMANTICS
Global x100 is correct (median 1.0). A bounded minority (1,113 rows,
mostly 2016-2017 IPO consecutive-limit periods) has internally
inconsistent TDX wire volume/amount (amount==BaoStock, volume ~100x).
A missing unit transform would show median ~100; it does not.

## CORRECTNESS DECISION

R3_TDX_VOLUME_CORRECTNESS_PASS=false
R3_DAILY_FOUNDATION_CORRECTNESS_PASS=false

## REPAIR SCOPE

REPAIR_SCOPE_DECISION=FURTHER_DIAGNOSIS_REQUIRED
Bounded wire-level TDX vs BaoStock comparison on affected IPO rows
before choosing 300546-only / migration / adapter+rebuild; x100 toggle
is NOT implied (overlap ratios 3.03/128.5/5.09).

## STRATEGY IMPACT

Affected: volume ratios, shrinkage, triple-volume, turnover inputs,
B1/B2 volume features (IPO-period SH/SZ rows). Research not rerun.

## SAFETY

NETWORK_PROVIDER_DATA_FETCH=NO
R3_MARKET_DATA_WRITE=NO
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
