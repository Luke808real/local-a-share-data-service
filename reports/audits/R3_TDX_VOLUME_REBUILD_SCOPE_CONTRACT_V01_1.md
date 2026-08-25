# R3 TDX VOLUME REBUILD SCOPE CONTRACT - V01.1 (author report)

DATE: 2026-08-25
BASE_HEAD: 68e34a0ce60741851169d634cd6a5564cd642398
REPORT_GENERATED_AT_HEAD: 68e34a0ce60741851169d634cd6a5564cd642398 (pre-commit worktree HEAD)
AUTHOR_STATUS: PASS_PENDING_SOL_AUDIT

Tool: `tools/audits/r3_tdx_volume_rebuild_scope_v01.py` (research-only)
Runtime dependency: Luke808real/CNEquity@ecf57023d57dcf925e9da3aa0e023492abb1221d

## Sol audit blockers closed

1. **NORMAL_BAND_FLOOR=256 was wrong** -> re-proven floor is **128**
   (`2^7`; `0x43000000 == 128.0` lots).  Beyond that, the decoder equality
   is exact, not just int-equal: for every raw with lp >= 0x43,
   `get_volume(raw) == float32(raw)` (three-case proof: hleax <= 0x7F,
   hleax == 0x80, hleax >= 0x81; lp >= 0x43 implies dw_edx >= 0, so the
   reciprocal branch is never taken).  Integer preimages 128..255 therefore
   always have a normal-band raw with IEEE == old_int and can never be
   PROVABLY_AFFECTED.
2. **VWAP plausibility cannot overwrite decoder mathematics** -> removed.
   Classification is pure candidate-set; amount/OHLC is reported only as
   SUPPORTING_DIAGNOSTIC.
3. **Amount global correctness unverified** -> `TDX_AMOUNT_GLOBAL_CORRECTNESS
   = UNPROVEN` (retained amount is the untruncated float64 output of the same
   old `get_volume()` path; raw amount bytes are not retained, so the decoder
   path cannot be proven offline).  Amount never changes a class.
4. **OFFLINE_DETERMINISTIC_REBUILD_POSSIBLE while unresolved rows remain** ->
   rebuild decision now fails closed: `AMBIGUOUS_N > 0` yields
   `TARGETED_NETWORK_REFETCH_COMPLETE_SET`.

## 1. VALID RAW DOMAIN (formal)

- Minimum positive volume = 1 share; native daily unit = lots, so minimum
  positive native quantity = 0.01 lots.
- Round-to-nearest is monotone, therefore every valid positive raw satisfies
  `float32(raw) >= RN(0.01)` = `0x3C23D70A` (`0.009999999776482582`).
- Every lp < 0x3C decodes below `2^-7` lots = 0.78125 shares < 1 share and is
  excluded.  Complete positive-finite high-byte range: 0x3C .. 0x7F, with
  the divergence band exactly lp 0x3C..0x42 (full 24-bit mantissa
  enumeration, domain-filtered).

## 2. COMPLETE PREIMAGE MODEL

`retained_old_int -> { candidate corrected quantities }` over the complete
valid raw domain:

- every divergence-band raw (lp 0x3C..0x42, validated domain) with
  `int(old(raw)) == k` contributes `int(float32(raw))`;
- if k >= 128 and the integer k is float32-representable
  (`k < 2^24` or `k % 2^(bit_length(k)-1-23) == 0`), the normal band
  contributes candidate k.

Explicit normal-band preimages: 128 -> `0x43000000`, 156 -> `0x431C0000`,
208 -> `0x43500000`, 255 -> `0x437F0000`, 256 -> `0x43800000` (all decode
`int(old) == int(ieee) == k`).

## 3. CLASSIFICATION (candidate-set only)

- PROVABLY_UNAFFECTED: every valid candidate yields the retained k.
- PROVABLY_AFFECTED: every valid candidate yields the same c != k.
- AMBIGUOUS: multiple distinct candidates, or empty candidate set
  (model gap -> fail closed).

## 4. AMOUNT GATE

`TDX_AMOUNT_GLOBAL_CORRECTNESS = UNPROVEN`.  Amount/OHLC values are persisted
as SUPPORTING_DIAGNOSTIC only (retained/corrected VWAP in-range flags); they
never change AMBIGUOUS -> UNAFFECTED / AFFECTED.

## 6. FULL OFFLINE SCAN (single run, 10,325,793 rows)

| metric | V01 | V01.1 |
|---|---|---|
| TDX_ROW_N | 10,325,793 | 10,325,793 |
| PROVABLY_AFFECTED_N | 201 | **32** (31 symbols) |
| PROVABLY_UNAFFECTED_N | 10,295,597 | **10,296,494** |
| AMBIGUOUS_N | 29,995 | **29,267** (4,577 symbols) |

SUM_CHECK: 32 + 10,296,494 + 29,267 = 10,325,793 == TDX_ROW_N (true).

## 7. DIFF VS V01

ROWS_CHANGED_CLASSIFICATION_N = **1,306**

| transition | N |
|---|---|
| AMBIGUOUS -> AMBIGUOUS | 28,978 |
| AMBIGUOUS -> PROVABLY_AFFECTED | 8 |
| AMBIGUOUS -> PROVABLY_UNAFFECTED | 1,009 |
| PROVABLY_AFFECTED -> AMBIGUOUS | 177 |
| PROVABLY_AFFECTED -> PROVABLY_AFFECTED | 24 |
| PROVABLY_UNAFFECTED -> AMBIGUOUS | 112 |
| PROVABLY_UNAFFECTED -> PROVABLY_UNAFFECTED | 10,295,485 |

Effects:
- corrected 128 floor: **177** rows moved AFFECTED -> AMBIGUOUS (k in
  [128, 255] with a divergent anomaly candidate);
- VWAP override removal: **112** rows moved UNAFFECTED -> AMBIGUOUS
  (old viable-VWAP demotion no longer overrides the decoder math);
- domain filter: 1,009 rows moved AMBIGUOUS -> UNAFFECTED and 8 rows moved
  AMBIGUOUS -> AFFECTED (spurious sub-share candidates removed).

The 32 remaining PROVABLY_AFFECTED rows are all `k=48 -> 33` IPO-early
one-price rows (retained 4,800 shares; corrected 3,300 shares); amount/close
reconciliation confirms (e.g. 300508.SZ 2016-04-20 amount 105,004.8125 /
3,300 = 31.82 ~ close 31.81).

## 8. KNOWN HARD 1110

- HARD_AND_AFFECTED_N = 0
- HARD_AND_AMBIGUOUS_N = 1,110
- HARD_AND_UNAFFECTED_N = 0
- ALL_HARD_IN_AFFECTED_OR_AMBIGUOUS = true
- KNOWN_HARD_1110_COMPLETE_SET = **FALSE** (1110 is a heuristic subset;
  PROVABLY_AFFECTED contains non-HARD rows and AMBIGUOUS rows may be
  affected; containment in AFFECTED u AMBIGUOUS is necessary, not
  sufficient).

## 9. REBUILD DECISION

**TARGETED_NETWORK_REFETCH_COMPLETE_SET**

- AMBIGUOUS_N (29,267) > 0, so OFFLINE_DETERMINISTIC_REBUILD_POSSIBLE is
  excluded.
- The affected/ambiguous candidate set is mathematically complete and
  finite over the proven valid raw domain, so a complete bounded external
  refresh is well-defined.
- TARGET_KEY_N = 29,267; TARGET_SYMBOL_N = 4,577.

## 10. NETWORK COST PLAN (no execution)

- Shape A (TDX corrected runtime): ~11,004 paginated daily-K requests over
  the exact per-symbol ambiguous date spans (800 rows/page; bounded windows
  only).
- Shape B (BaoStock secondary): ~4,577 bounded queries (one per ambiguous
  symbol over its exact min..max span).
- Affected-only refresh (optional): ~31 requests either shape; the 32
  affected rows are deterministically correctable offline.

## 11. 300546 missing days

2016-09-29 / 2016-10-10 remain a separate authority decision; no insertion.

## 12. R4A9

No action. R4A9_RESUME_AUTHORIZED=false.

## Safety

NETWORK_PROVIDER_DATA_FETCH=NO
R3_MARKET_DATA_WRITE=NO
R3_DATA_REBUILD_EXECUTED=false
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false
