# R3 TDX VOLUME REBUILD SCOPE CONTRACT - V01 (author report)

DATE: 2026-08-25
BASE_HEAD: 947735b0c75300637793a79ae253c8e052fe62ab

## Old pipeline (documented)

raw float32 bits -> old packed get_volume(raw) -> decoded_quantity (zero
snap) -> finite_int64 (int truncation) -> lots_to_shares x100 -> staging
-> curated.

OLD_PIPELINE_INFORMATION_LOSS=PARTIAL
(int truncation drops the fractional part; raw->int(get_volume(raw)) is
not injective, so raw cannot be recovered from canonical)

OLD_CANONICAL_TO_RAW_INVERTIBILITY=FINITE_AMBIGUOUS

## Decoder divergence (mathematical)

- lp >= 0x43 (quantities >= 256 lots): int(OLD) == int(NEW) for every raw
  (dense in integers >= 256) -> normal band
- lp 0x3c..0x42: divergence band; preimage table built by full 24-bit
  mantissa enumeration
- values >= 256 with only an anomalous table entry may still be produced by
  a normal-band raw (IEEE == old_int), so they are AMBIGUOUS (not affected)
- values < 256 with a unique anomalous candidate are PROVABLY_AFFECTED

Row-level amount-VWAP validation: PROVABLY_AFFECTED is accepted only when
OLD VWAP (amount/old_shares) is outside [low,high] AND corrected VWAP is
inside; if the old VWAP is already plausible the row is economically
self-consistent and treated unaffected.

## Full local scan (10.33M rows, offline)

TDX_ROW_N            = 10,325,793
PROVABLY_AFFECTED_N  = 201   (159 symbols)
PROVABLY_UNAFFECTED_N= 10,295,597
AMBIGUOUS_N          = 29,995

Affected by year: 2016=82 2017=113 2018=4 2019=2 (IPO early dominance)
Affected by days-from-listing: 0-5=133 6-20=67 >250=1
Exchange: SZ=142 SH=59

## HARD 1110 intersection

HARD 1110: affected 177, ambiguous 933, outside superset 0
Requirement (all HARD rows subset of affected-or-ambiguous) satisfied.

KNOWN_HARD_1110_COMPLETE_SET=false
ADDITIONAL_NON_HARD_AFFECTED_N=24

Known HARD 1110 is NOT the complete set: 24 additional rows are provably
affected offline, plus 29,995 ambiguous rows require network or raw bytes.

## Rebuild decision

REBUILD_DECISION=OFFLINE_DETERMINISTIC_REBUILD_POSSIBLE

201 rows can be deterministically corrected offline (unique table
candidate + amount VWAP validation). The 29,995 ambiguous rows cannot be
classified offline; they require bounded network refetch / raw bytes.

## Cost estimate (engineering, no execution)

- offline path: 201 rows, 159 symbols, 0 provider requests
- ambiguous network path: ~29,995 keys across many symbols; each affected
  symbol requires paginated TDX full history (~800 rows/page; 2-4 pages for
  2016-2017 window) or BaoStock per-symbol window (~11 requests/symbol);
  exact scope pending Sol decision

## 300546 missing days

2016-09-29 / 2016-10-10 confirmed real; corrected runtime fetches them.
Insertion authority remains separate from volume-rebuild approval.

## R4A9 compatibility

R4A9_EXISTING_COMPLETE_N=2140
R4A9_RESUME_AUTHORIZED=false
Proposed R3 repair changes ONLY volume fields; R4 required-key membership
and required-row counts are unaffected (R4 keys derive from bar presence
not volume).

## Secondary authority feasibility

BAOSTOCK_REPAIR_AUTHORITY=VALIDATED_BOUNDED
(20-key sample: shares unit, OHLC/amount reconciliation validated;
14/16 exact + 2/16 bounded TDX rounding + 4/4 controls)

## Safety

NETWORK_PROVIDER_DATA_FETCH=NO
R3_MARKET_DATA_WRITE=NO
R3_DATA_REBUILD_EXECUTED=false
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
