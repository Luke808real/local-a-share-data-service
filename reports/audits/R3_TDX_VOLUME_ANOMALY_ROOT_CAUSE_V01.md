# R3 TDX VOLUME ANOMALY ROOT CAUSE - V01 (author report)

DATE: 2026-08-24
BASE_HEAD: 487204dde84040cd3532c199f0a42cd3414b6b3d

## TOOL

tools/audits/r3_tdx_volume_anomaly_audit_v01.py (research/audit only; no
production import path depends on it) + tests/test_r3_tdx_volume_anomaly_
audit_v01.py (9 passed).

HARD anomaly contract (deliberately conservative to eliminate float noise):

```text
VWAP = amount / volume
HARD_BELOW = VWAP <  low * 0.5
HARD_ABOVE = VWAP >  high * 2.0
one-price rows: R = amount / (close * volume); HARD when R < 0.5 or R > 2.0
ordinary outside-[low,high] rows stay SOFT diagnostics only
```

## FULL LOCAL COUNTS (read-only, no network)

```text
TDX_POSITIVE_ROW_N         = 10,325,793
TDX_HARD_ANOMALY_N         = 1,110
TDX_HARD_ANOMALY_SYMBOL_N  = 466
BAOSTOCK_POSITIVE_ROW_N    = 384,194
BAOSTOCK_HARD_ANOMALY_N    = 0

by year: 2016=467, 2017=603, 2018=29, 2019=9, 2020=1, 2025=1
by exchange: SH=255, SZ=855
top symbols: 300560.SZ(10) 300585.SZ(8) 300562.SZ(8) 300700.SZ(7)
             300539.SZ(7) 300557.SZ(7) 300546.SZ(7) 300563.SZ(7)
```

## LISTING-DISTANCE TEST

```text
0-5 days   : 762 (68.6%)
6-20 days  : 342 (30.8%)
21-60      : 0
61-250     : 0
>250       : 6 (0.5%)
```

IPO_EARLY_PERIOD_CONCENTRATION=SUPPORTED (99.4% within 20 days of listing).

## 300546 ERRATUM (R3_TDX_VOLUME_AUDIT_V01_ERRATUM=true)

```text
2016-09-28  VWAP=9.39  < low*0.5=12.26  -> HARD_BELOW
2016-09-30  VWAP=0.277 < 17.80          -> HARD_BELOW
2016-10-11  VWAP=8.46  < 21.54          -> HARD_BELOW
```

## BOUNDED SAMPLE (deterministic, persisted algorithm)

16 anomalous keys: symbols sorted by anomaly count desc (stable), top 8,
plus 300546.SZ, first 2 anomalous dates ascending. 8 healthy controls by
same year/exchange (largest-volume rows). Full key list in JSON receipt.

## TDX WIRE-LAYER DIAGNOSIS (bounded, pinned CNEquity a18ee0484...)

3 independently healthy TDX endpoints probed (120.76.1.198:7709,
1.202.143.37:7709, 111.203.134.118:7709). Every sampled anomalous key
returned IDENTICAL native volumes across all 3 endpoints:

```text
300546.SZ 2016-09-30  native=2,056手 -> canonical=205,600 (x100 exact)
300546.SZ 2016-10-11  native=224手   -> canonical=22,400  (x100 exact)
300539.SZ 2016-08-30  native=176手   -> canonical=17,600  (x100 exact)
300700.SZ 2017-09-12  native=384手   -> canonical=38,400  (x100 exact)
300562.SZ 2016-11-16  native=272手   -> canonical=27,200  (x100 exact)
```

TDX amount equals BaoStock amount exactly (e.g. 300546 09-30: 56,960 both);
TDX volume disagrees with BaoStock volume by NON-CONSTANT factors
(e.g. 205,600 vs 1,600). BaoStock volume is close-consistent
(56,960/1,600 = 35.60 = close); TDX volume is not.

Healthy controls (2023-2025) confirm TDX native x100 == canonical and
TDX volume ~ BaoStock volume (e.g. 300539 2025-07-29: 497,648手 -> 49,764,800
vs BaoStock 49,764,874 -- consistent).

## CLASSIFICATION

TDX_ENDPOINTS_AGREE=true (all 3 endpoints identical)
TDX_NATIVE_VOLUME -> TDX_CANONICAL_VOLUME: exact x100 (adapter transform correct)
TDX_AMOUNT == BAOSTOCK_AMOUNT (exact)
TDX_VOLUME != BAOSTOCK_VOLUME (non-constant factor)

PER_KEY_CLASSIFICATION=TDX_VENDOR_HISTORICAL_VOLUME_ANOMALY
ROOT_CAUSE_CLASSIFICATION=TDX_VENDOR_HISTORICAL_VOLUME_ANOMALY

The TDX vendor historical volume field is wrong for IPO early-period rows
(internally inconsistent with its own amount, and disagrees with a
close-consistent secondary source). NOT endpoint inconsistency, NOT decoder
defect, NOT R3 adapter transform defect.

## DECISIONS

```text
R3_TDX_VOLUME_CORRECTNESS_PASS = false
R3_DAILY_FOUNDATION_CORRECTNESS_PASS = false
REPAIR_SCOPE_DECISION = BOUNDED_ANOMALOUS_ROWS_SECONDARY_REPAIR_CANDIDATE
  (1,110 rows / 466 symbols, 99.4% within 20 days of listing; BaoStock
   provides close-consistent rows; repair NOT executed; Sol approval +
   versioned normalizer + explicit volume-unit contract required)
```

## STRATEGY IMPACT

Affected: volume ratios, shrinkage, triple-volume, turnover inputs, B1/B2
volume features for IPO early-period SH/SZ rows (~1,110 canonical rows).
Research not rerun.

## SAFETY

```text
NETWORK_PROVIDER_DATA_FETCH = YES_BOUNDED_SAMPLE_ONLY (3 endpoints, 21 keys)
R3_MARKET_DATA_WRITE = NO
R4A9_CHECKPOINT_MUTATED = false
R4A9_RESUME_AUTHORIZED = false
PRECLOSE_COMPLETE = false
```

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
