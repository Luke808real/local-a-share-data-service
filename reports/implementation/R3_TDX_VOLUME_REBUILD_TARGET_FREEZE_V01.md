# R3 TDX VOLUME REBUILD TARGET FREEZE - V01 (author report)

DATE: 2026-08-25
BASE_HEAD: d716918658b9c3e7fa253fdaf4a01322d772aaa8
AUTHOR_STATUS: PASS_PENDING_SOL_AUDIT

Companion artifacts:

- `R3_TDX_VOLUME_REBUILD_INPUT_MANIFEST_V01.json/.md`
- `R3_TDX_VOLUME_REBUILD_TARGET_MANIFEST_V01.json/.md`
- `R3_TDX_VOLUME_REBUILD_TARGET_FREEZE_V01.json` (machine-readable, incl.
  30 per-key provider receipts)

## 0/1. Entering authority and input provenance

Counts reproduced from the exact frozen input (not hardcoded):

| metric | expected | reproduced |
|---|---|---|
| TDX_ROW_N | 10,325,793 | 10,325,793 |
| PROVABLY_AFFECTED_N | 32 | 32 |
| AMBIGUOUS_N | 29,267 | 29,267 |
| PROVABLY_UNAFFECTED_N | 10,296,494 | 10,296,494 |

No authoritative existing manifest covered the parquet set, so a
deterministic read-only input manifest was created:

- INPUT_FILE_N = 2580
- INPUT_MANIFEST_HASH = `f9025a5cbc52d757fdc05d9e6ebb5f3c75c1cd93414a2f6c51bb83314594a6ec`

## 2/3. Frozen key manifest + HARD contract

- AFFECTED_KEY_N = 32, AMBIGUOUS_KEY_N = 29,267, REPAIR_SUPERSET_KEY_N = 29,299
- AFFECTED_KEY_HASH = `131754e2...`
- AMBIGUOUS_KEY_HASH = `be1911a4...`
- REPAIR_SUPERSET_KEY_HASH = `1d579988...` (all three recomputed exactly)

HARD contract: KNOWN_HARD_N=1110; HARD_AND_AFFECTED=0;
HARD_AND_AMBIGUOUS=1110; **HARD_UNAFFECTED_N=0** (contract PASS, fail-closed
otherwise).

## 4. Execution target

NETWORK_REPAIR_SUPERSET = PROVABLY_AFFECTED ∪ AMBIGUOUS = **29,299 keys**.
No repair executed in this task.

## 5. TDX request cost (engineering estimate)

Audited `fetch_bars_paginated()` starts at offset=0 (latest) and pages
backwards by 800 daily-K rows.  Per-symbol pages = ceil(local TDX daily rows
in [oldest target date, latest local date] / 800):

- TDX_TARGET_SYMBOL_N = 4,577
- TDX_PAGE_REQUEST_ESTIMATE = 11,600
- TDX_MAX_PAGES_PER_SYMBOL = 4
- TDX_MEDIAN_PAGES_PER_SYMBOL = 3

## 6. BaoStock cost (secondary / cross-check only)

- BAOSTOCK_REQUEST_ESTIMATE = 4,577 (one bounded
  `query_history_k_data_plus` per target symbol over its exact oldest..newest
  target date span)
- BaoStock is NOT promoted to primary repair authority.

## 7. Bounded provider validation (30 unique keys)

Deterministic selection (sorted by symbol/trade_date, first 10 of each
bucket): 10 PROVABLY_AFFECTED + 10 HARD AMBIGUOUS + 10 NON-HARD AMBIGUOUS.

Corrected TDX runtime (installed fork @ `ecf57023`, category=9 float32):

- 30/30 keys OK (94 page requests), all BaoStock tradestatus = 1
- 10/10 PROVABLY_AFFECTED: fresh TDX volume == predicted corrected volume
  exactly (3300)
- 10/10 HARD AMBIGUOUS: fresh volume != retained (all resolve to corrected
  values, e.g. 002778.SZ 2016-01-07: 11200 -> 3700)
- 10/10 NON-HARD AMBIGUOUS: fresh volume == retained exactly (normal-band
  raws; live runtime resolves what candidate-set math cannot prove)

BaoStock cross-check (30 bounded queries): 16/30 exact; 14/30 small positive
share-count rounding deltas (+1..+73 shares), the documented float32-wire vs
integer-share-count rounding class.

## 8. Provider execution decision

**TDX_PRIMARY_TARGETED_REFETCH** — the corrected TDX runtime is validated as
the primary refetch authority for the 29,299-key superset.  BaoStock remains
an audit cross-check source unless separately promoted.

## 9. 300546 missing days

2016-09-29 / 2016-10-10 stay a separate authority decision and are NOT in the
volume manifest.

## Safety

R3_MARKET_DATA_WRITE=NO
R3_DATA_REBUILD_EXECUTED=false
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false
