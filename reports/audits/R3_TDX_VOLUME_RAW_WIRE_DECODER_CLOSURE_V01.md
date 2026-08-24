# R3 TDX VOLUME RAW WIRE DECODER CLOSURE - V01 (author report)

DATE: 2026-08-25
BASE_HEAD: b30ff3ec438563472a9775f8f38cb7b84ad7284e

## TOOLS (research only; no production changes)

- tools/audits/r3_tdx_volume_anomaly_audit_v01.py - controls selector fixed
  (same symbol, same calendar year, nearest LATER clean row; deterministic;
  exactly 4 matched controls).
- tools/audits/r3_tdx_raw_wire_decoder_v01.py - research-only raw-body
  capture (subclass of pinned GetSecurityBarsCmd, manual byte-offset parse)
  plus an independent IEEE-754 decoder.
- tests: 17 passed (11 audit-tool + 6 wire-decoder).

## SAMPLE RECONCILIATION

```text
ANOMALY_SAMPLE_KEY_N = 16   (top-8 symbols x <=2 dates + 300546.SZ)
CONTROL_SAMPLE_KEY_N = 4    (deterministically matched)
NETWORK_UNIQUE_KEY_N = 20   (16 + 4, all unique)
```

## RAW WIRE CAPTURE (3 independently healthy endpoints)

Raw 20-byte daily-K records parsed from the response body; volume and amount
4-byte fields captured verbatim.

```text
RAW_BYTES_ENDPOINTS_EQUAL     = true (120.76.1.198 / 1.202.143.37 / 111.203.134.118)
DECODED_VALUES_ENDPOINTS_EQUAL= true
```

## ANOMALY RAW EVIDENCE (300546.SZ)

| date       | vol_raw hex | pinned decode | independent IEEE-754 | BaoStock vol |
|------------|-------------|---------------|----------------------|--------------|
| 2016-09-28 | 0x42140000  | 112.0         | 37.0                 | 3700         |
| 2016-09-29 | 0x3f800000  | 32768.5       | 1.0                  | 100          |
| 2016-09-30 | 0x41800000  | 2056.0        | 16.0                 | 1600         |
| 2016-10-10 | 0x3f800000  | 32768.5       | 1.0                  | 100          |
| 2016-10-11 | 0x42300000  | 224.0         | 44.0                 | 4400         |

Independent IEEE-754 lots x 100 == BaoStock shares on EVERY row
(37x100=3700, 1x100=100, 16x100=1600, 44x100=4400). The pinned packed
decoder is the source of the inflated canonical volumes.

## INDEPENDENT DECODER CHECK

```text
authority      = IEEE-754 float32 (struct <f) of the raw 4 bytes
provenance     = raw capture + float32 reinterp + BaoStock cross-check
DECODER_EQUAL_ON_ANOMALY  = false  (pinned 2056 vs 16; 32768.5 vs 1.0; 224 vs 44)
DECODER_EQUAL_ON_MODERN_HEALTHY = true (0x4949d320 -> 826674 both)
```

## ROOT CAUSE

ROOT_CAUSE_CLASSIFICATION = TDX_PACKED_VOLUME_DECODER_DEFECT

Raw wire volume is IEEE-754 float32. The pinned get_volume packed-decoder
inflates values on exponent/high-byte rows (exactly the canonical anomaly
rows). BaoStock is the independent control and matches the float32 lots.
This is a decoder defect, not a vendor raw-payload anomaly.

## PRIOR RECEIPT CLOSURE

```text
PRIOR_AUTHOR_ROOT_CAUSE = TDX_VENDOR_HISTORICAL_VOLUME_ANOMALY
SOL_AUDIT_OVERRIDE     = TDX_WIRE_OR_DECODER_HISTORICAL_VOLUME_ANOMALY_UNRESOLVED
PRIOR_SAMPLE_COUNTS_CLAIMED = 16 anomaly / 8 controls / 24 network (prior tool)
CORRECTED_SAMPLE_COUNTS     = 16 anomaly / 4 controls / 20 network
```

Recorded explicitly; history is not silently rewritten.

## DECISIONS

```text
R3_TDX_VOLUME_CORRECTNESS_PASS = false
R3_DAILY_FOUNDATION_CORRECTNESS_PASS = false
REPAIR_SCOPE_DECISION = TDX_DECODER_FIX_AND_DATA_REBUILD_REQUIRED
  (pinned decoder fix + canonical daily_bars volume rebuild for TDX rows;
   bounded secondary repair alone is insufficient)
```

## SAFETY

```text
NETWORK_PROVIDER_DATA_FETCH = YES_BOUNDED_SAMPLE_ONLY
R3_MARKET_DATA_WRITE = NO
R4A9_CHECKPOINT_MUTATED = false
R4A9_RESUME_AUTHORIZED = false
PRECLOSE_COMPLETE = false
```

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
