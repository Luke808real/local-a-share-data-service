# R4A3 BaoStock Preclose Canonical Source Pilot — V01

DATE: 2026-08-20
BASE_HEAD: 44c9d758b2006c1ace8294fccebdc33e1b8640fe
PILOT_SYMBOL_N: 24
PILOT_SYMBOL_HASH: 5fa9f5c9ef376f0c453d3f543dc3a8ee9d61f73cec3a0fd35a9bea5081e17843

## Verdict

```text
STATUS=PILOT_COMPLETE
BAOSTOCK_PRECLOSE_FULL_HISTORY_PILOT=PILOT_COMPLETE
PROVIDER_EXECUTION_COUNT=1
AUTO_RETRY=NO
POSTRUN_ARTIFACT_REBUILD=YES
COVERAGE_STATUS=FAIL
NORMAL_PARITY_STATUS=FAIL
OFFICIAL_EVENT_PARITY_STATUS=PASS
IPO_PARITY_STATUS=UNKNOWN_OFFICIAL_SUBSET_EMPTY
WINDOW_EDGE_STATUS=PASS
BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION=CROSSCHECK_ONLY
R4A_IMPLEMENTATION_READY=false
```

This is a bounded source-authority pilot only. It does not write a formal
preclose dataset or corporate_actions and does not modify the R4 frozen plan.

## Frozen scope

```text
FORMAL_IDENTITY_N=5456
FORMAL_IDENTITY_HASH=2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f
IDENTITY_MATCH=True
PILOT_SYMBOL_N=24
PILOT_SYMBOL_HASH=5fa9f5c9ef376f0c453d3f543dc3a8ee9d61f73cec3a0fd35a9bea5081e17843
WINDOW=2016-01-01..2026-08-17
QUERY_FIELDS=date,code,preclose,tradestatus
QUERY_FREQUENCY=d
QUERY_ADJUSTFLAG=3
SAMPLE_DETERMINISTIC=true
SAMPLE_BALANCED_STRATIFIED=false
```

The prior 260-row parity evidence was reused. No parity sample was
re-run and no announcement review was expanded.

## Coverage and response identity

```text
REQUIRED_ROW_N=55053
BAOSTOCK_ROW_N=56035
PRECLOSE_NON_NULL_N=56035
MISSING_REQUIRED_ROW_N=0
UNEXPECTED_ROW_N=982
DUPLICATE_PK_N=0
IDENTITY_FAILURE_N=0
POST_ASOF_N=0
QUERY_N=242
QUERY_FAILURE_N=0
PROVIDER_EXECUTION_COUNT=1
AUTO_RETRY=NO
POSTRUN_ARTIFACT_REBUILD=YES
```

Per-symbol/year coverage is retained in the JSON receipt. Provider rows
were queried only with date, code, preclose, and tradestatus; no OHLCV,
valuation, or bulk provider dataset was requested.
The 982 unexpected rows are BaoStock rows outside the local authoritative
daily_bars actual-session key set; they are reported as a coverage failure
and are not silently promoted into the required scope.

## Parity results

```text
NORMAL_REQUIRED_ROW_N=54819
NORMAL_COMPARABLE_N=54819
EXACT_MATCH_N=54818
MISMATCH_N=1
MAX_DIFF=0.010000000000000675
OFFICIAL_EVENT_N=20
OFFICIAL_EVENT_EXACT_N=20
OFFICIAL_EVENT_MISMATCH_N=0
IPO_OFFICIAL_N=0
IPO_EXACT_N=0
IPO_MISMATCH_N=0
WINDOW_EDGE_N=21
WINDOW_EDGE_NON_NULL_N=21
```

Sol-frozen official adjusted-basis corrections reused in this pilot:

```text
000002.SZ 2022-08-25 CASH_PER_SHARE=0.968802 OFFICIAL_DISPLAY_PRECLOSE=15.65
000002.SZ 2023-08-25 CASH_PER_SHARE=0.674898 OFFICIAL_DISPLAY_PRECLOSE=13.04
STATUS=RESOLVED_OFFICIAL_ADJUSTED_BASIS
```

IPO official issue-price authority was not present in the existing bounded
evidence, so the IPO subset remains UNKNOWN rather than being inferred
from BaoStock itself. This is a source-decision blocker.

## Source decision

```text
BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION=CROSSCHECK_ONLY
PROMOTION_CONDITIONS_MET=False
PROMOTION_BLOCKERS=COVERAGE_STATUS,NORMAL_PARITY_STATUS,IPO_PARITY_STATUS
SHARED_BAOSTOCK_EXTRACTION_RECOMMENDATION=DESIGN_ONLY: consider one bounded/resumable request carrying preclose,turn,tradestatus,isST, with independent Market Fact datasets
R4A_IMPLEMENTATION_READY=false
```

The shared extraction idea is design-only: future bounded/resumable
requests may carry preclose, turn, tradestatus, and isST, but each
field must land in an independent Market Fact dataset. No implementation
is included in this task.

## Safety

```text
NETWORK_PROVIDER_DATA_FETCH=YES
PROVIDER_STEP_ENTERED=YES
MARKET_DATA_WRITE=NO
CORPORATE_ACTIONS_WRITE=NO
FORMAL_PRECLOSE_DATASET_WRITE=NO
R4B_R4C_R4D_IMPLEMENTATION=FORBIDDEN
STRATEGY_FORWARD_TRADEPLAN=FORBIDDEN
```
