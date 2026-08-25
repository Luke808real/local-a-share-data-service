# R3 TDX RAW FIELD POSITION FIX - V01 (author report)

DATE: 2026-08-25
BASE_HEAD: 728c1225d5273fb4f4f310308cfe9ecf40c35e24

## SOL AUDIT BLOCKER - ACCEPTED AND FIXED

PRIOR_RAW_WIRE_REPORT_STATUS=INVALID_RAW_POSITION_ASSUMPTION
PRIOR_FIXED_RECORD_BYTES=20  PINNED_RECORD_LAYOUT=VARIABLE_LENGTH

Pinned production daily-K record is variable length:
  ret_count(2B) + date(4B YYYYMMDD for category=9) + open/close/high/low
  diffs via get_price() (each 1..N bytes: bit6 sign, bit7 continuation,
  7 data bits per continuation byte) + volume 4B + amount 4B.

## TRACE PARSER (research-only)

tools/audits/r3_tdx_raw_wire_decoder_v01.py now follows the exact pinned
field positioning (pinned get_datetime / get_price semantics) solely to
advance positions; raw volume/amount bytes are captured before the pinned
get_volume decode. Fixed pos+=20 removed. Per record: record_index,
record_start_pos, volume_pos, amount_pos, record_end_pos, trade_date,
raw volume/amount hex+uint32.

## INDEPENDENT POSITION TESTS

5 tests pass, including synthetic records with 1-byte, 2-byte, and 3-byte
price fields and three consecutive mixed-length records, proving record
N+1 stays aligned (record N+1 start == record N end).

## REAL BODY CROSS-CHECK (300546.SZ)

2016-09-28 / 2016-09-29 / 2016-09-30 / 2016-10-10 / 2016-10-11

TRACE_DATE == PINNED_DATE on all 5 keys
TRACE_OHLC == PINNED_OHLC on all 5 keys
TRACE_ALIGNMENT_PASS=true

## BOUNDED SAMPLE (reused corrected sample)

ANOMALY_SAMPLE_KEY_N=16  CONTROL_SAMPLE_KEY_N=4  NETWORK_UNIQUE_KEY_N=20
Endpoints: 120.76.1.198:7709, 1.202.143.37:7709, 111.203.134.118:7709.

## RAW ENDPOINT EQUALITY (after correct positioning)

RAW_VOLUME_BYTES_ENDPOINTS_EQUAL=20/20 keys
RAW_AMOUNT_BYTES_ENDPOINTS_EQUAL=20/20 keys

## DECODER COMPARISON

### volume

PINNED_GET_VOLUME vs IEEE754: differ on 16 of 20 keys - exactly the HARD
anomaly rows.
IEEE754*100 == BaoStock shares on 14/16 significant anomaly rows and on all
4 healthy controls. Two edge keys (300539.SZ 2016-09-01, 300557.SZ
2016-11-01) show ~1.4% residual TDX source rounding (IEEE 40 lots vs
BaoStock 4057/4039); those rows are HARD in canonical but IEEE is far
closer to truth than the pinned 160-lot decode.

### amount

PINNED_AMOUNT_DECODER_PASS=true
IEEE754_AMOUNT_DECODER_PASS=true

amount raw pinned get_volume == IEEE754 on all 20 keys, and equals
BaoStock amount. Amount decoding is NOT affected by the volume decoder
defect. A volume-only decoder patch therefore does not contaminate amount.

## ROOT CAUSE

ROOT_CAUSE_CLASSIFICATION=TDX_PACKED_VOLUME_DECODER_DEFECT

All requirements met: correct raw field alignment proven; identical raw
bytes across 3 endpoints; independent IEEE754 interpretation reproducibly
matches BaoStock volume authority on anomaly + matched controls; pinned
get_volume disagrees specifically on anomaly rows; healthy controls decode
correctly; no evidence of source raw corruption.

## DECISIONS

TDX_DECODER_FIX_AUTHORIZED=false (Sol decision required)
R3_DATA_REBUILD_AUTHORIZED=false (Sol decision required)
R4A9_RESUME_AUTHORIZED=false
R3_MARKET_DATA_WRITE=NO
PRODUCTION_SOURCE_CHANGE=NO
R4A9_CHECKPOINT_MUTATED=false
PRECLOSE_COMPLETE=false

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
