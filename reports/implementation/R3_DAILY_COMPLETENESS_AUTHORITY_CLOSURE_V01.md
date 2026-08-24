# R3 DAILY COMPLETENESS AUTHORITY CLOSURE — V01 (author report)

DATE: 2026-08-24
BASE_HEAD: 3455cacfdbf38ca4dc88bdd018886810c9613b2a
TASK: bounded research / contract design (no data mutation, no R4A9 resume, no full-market refetch)

## A. TDX SOURCE CLOSURE (bounded, 300546.SZ 2016-09-28..2016-10-11)

TDX_SOURCE_CLOSURE=HEALTHY_ENDPOINT_FOUND (120.76.1.198:7709, auto-selected
by pinned cnequity tdx_protocol; served real OHLCV data twice)

Returned rows (identical in two independent auto-mode fetches):

2016-09-28  O=24.52 H=29.42 L=24.52 C=29.42  V=11200   A=105179.0
2016-09-30  O=35.60 H=35.60 L=35.60 C=35.60  V=205600 A=56960.0
2016-10-11  O=43.08 H=43.08 L=43.08 C=43.08  V=22400  A=189552.0

TDX_2016_09_29_PRESENT=false
TDX_2016_10_10_PRESENT=false

Direct per-host probes flaked simultaneously while auto mode succeeded twice;
the single healthy endpoint suffices for classification.

TDX_CLASSIFICATION=ALL_HEALTHY_TDX_MISS_ROWS

## A. ROOT-CAUSE RESOLUTION

A currently healthy TDX endpoint returns exactly the same 3-row series as
staged and curated R3 for 300546.SZ; both missing dates absent from the
TDX-returned frame. This resolves the formal root cause toward
TDX_SOURCE_HISTORICAL_GAP (task §2 interpretation: ALL_MISS_ROWS makes a
TDX historical coverage gap strongly supported).

PRIOR_AUTHOR_CLASSIFICATION=TDX_SOURCE_HISTORICAL_GAP
SOL_AUDIT_OVERRIDE=UNRESOLVED_AT_TDX_INGESTION_BOUNDARY (entering this task)
OVERRIDE_RESOLUTION=TDX_SOURCE_HISTORICAL_GAP (new healthy-TDX evidence legitimately confirms it)

## B. NON-CIRCULAR COMPLETENESS CONTRACT

Question: for every formal SH/SZ symbol and historical date, was the stock
expected to have a traded daily bar?

PROPOSED_EXPECTED_KEY_AUTHORITY = INDEPENDENT_TRADING_STATUS_AUTHORITY
  Primary candidate: BaoStock historical tradestatus per (symbol, date)
  (query_history_k_data_plus; already proven on 300546.SZ both dates).
  NOT derived from curated daily_bars (removes the self-referential blind spot).

Options evaluated:
  A. BaoStock tradestatus  -> available (primary candidate)
  B. other in-repo source  -> partial (market-level calendar only; no local per-symbol trading-status dataset)
  C. exchange/official     -> not practical now (no local exchange status data)

ARCHITECTURE=SEPARATE_DATA_AUTHORITY_FROM_COVERAGE_AUTHORITY
  TDX -> canonical active-stock OHLCVA (data authority; unchanged)
  independent trading-status authority -> expected traded-key coverage validator
  Do NOT replace TDX OHLCV with BaoStock merely because BaoStock detects missing dates.

Proposed exact gates:

EXPECTED_TRADED_KEY_N       = per (symbol,date) expected via trading-status authority
OBSERVED_TRADED_KEY_N       = per (symbol,date) observed in canonical daily_bars
MISSING_EXPECTED_TRADED_KEY_N = expected and not observed
UNEXPECTED_OBSERVED_KEY_N   = observed and not expected
DUPLICATE_KEY_N             = duplicate (symbol,date) in canonical bars
DAILY_HISTORY_COMPLETE      = identity exact AND missing_expected==0 AND
                              unexpected==0 AND duplicates==0 AND authority
                              coverage complete; UNKNOWN authority -> false

Not promoted in this task (contract design only).

## B. BLAST-RADIUS PLAN (no full-market scan run)

- Reuse R4A preclose provider observations as coverage evidence WITHOUT
  circularity: only the provider-observed (symbol,date,tradestatus) map is
  used as expected-key authority; the R4A required-key set (derived from
  curated bars) is never reused as the expected-key authority.
- Symbol batching + checkpoint/resume exactly as the R4A7 orchestrator.
- Bounded validation first (e.g. 24-symbol pilot) before any full run.
- Provider fetch cost can be shared with R4A preclose extraction.

REUSE_NON_CIRCULAR=true

## 300546 REPAIR DECISION

REPAIR_AUTHORITY_DECISION=TARGETED_TDX_REPAIR_ALLOWED

TDX is healthy and confirms the gap is TDX historical coverage, not R3
handling. A targeted single-symbol repair may re-source the two confirmed
trading days from a secondary OHLCV authority under a versioned normalizer
with explicit provenance, IF Sol authorizes R3 data mutation. NO repair was
executed in this task and none may be executed without that authorization.

## SAFETY

R3_MARKET_DATA_WRITE=NO
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false
FULL_MARKET_REFETCH=NO

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
