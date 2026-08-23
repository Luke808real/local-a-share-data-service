# R4A7.2 EXECUTION COMPLETENESS HARDENING — V01 (author report)

DATE: 2026-08-24
BRANCH: codex/r4a7-2-execution-completeness-hardening-v01
BASE_HEAD: f255ff1b923ceaec44bc936514386c4aec788287
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

## AUTHOR_STATUS

```text
AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
FULL_EXTRACTION_EXECUTED=false
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false
NETWORK_PROVIDER_DATA_FETCH=NO
```

Minimal hardening patch only. No real BaoStock fetch, no full extraction,
no canonical market-data write, no R4B/R4C/R4D, no
Strategy/Forward/TradePlan.

## BASE_HEAD / HEAD

```text
BASE_HEAD=f255ff1b923ceaec44bc936514386c4aec788287
HEAD=SELF — commit containing this file
REMOTE_HEAD=exact SHA returned after non-force push
```

## FILES_CHANGED

```text
M src/ashare_data/r4a7_preclose_full_extraction.py
M tests/test_r4a7_preclose_full_extraction.py
A reports/implementation/R4A7_2_EXECUTION_COMPLETENESS_HARDENING_V01.md
```

## 1. EXECUTION CONTEXT FAIL CLOSED

```text
execution_context allowed values: REAL, OFFLINE_TEST (exact strings)
None / unknown / arbitrary string -> UNKNOWN_EXECUTION_CONTEXT
  - no checkpoint trust
  - no provider fetch (NETWORK_PROVIDER_DATA_FETCH=NO)
```

`execution_context` has NO implicit default anymore: the previous
`OFFLINE_TEST` default was removed, so a non-dry-run call omitting the
argument cannot silently degrade into OFFLINE_TEST (regression B asserts
the omitted-context call returns UNKNOWN_EXECUTION_CONTEXT with zero
provider calls).

## 2. PARTIAL != COMPLETE

```text
COMPLETE_ALL_UNITS only if:
  COMPLETE_N == FULL_SYMBOL_N
  AND FAILED_N == 0
  AND PENDING_N == 0

limit stops execution while units remain ->
  STATUS=PARTIAL_LIMIT_REACHED
  (COMPLETE_N < FULL_SYMBOL_N, PENDING_N > 0, STATUS != COMPLETE_ALL_UNITS)

A later resume may continue normally (regression D: partial run then
resume -> COMPLETE_ALL_UNITS with the already-complete unit skipped as
valid).
```

## 3. STAGED FORMAL ROW INTEGRITY

New explicit verifier `verify_staged_formal_rows(...)`; every formal row
must satisfy:

```text
symbol == expected symbol
trade_date <= AS_OF
preclose finite positive
provider_tradestatus == 1
coverage_status == "COVERED"
source == BAOSTOCK_HISTORY_K_PRECLOSE
source_version == baostock-0.9.3
query_contract_version == frozen contract
adapter_version == current exact adapter authority SHA
```

Used in BOTH `unit_complete_and_valid()` and `aggregate_full_run()`; the
(symbol, trade_date, preclose) hash alone is never sufficient.
`STAGED_FORMAL_CONTENT_HASH` (new, separate) additionally covers all
canonical formal columns; the frozen R4A6-compatible `FORMAL_FACT_HASH`
semantics are unchanged.

## 4. UNIT EXECUTABLE IDENTITY

On resume `unit_complete_and_valid()` rederives and verifies:

```text
unit_query_window_n     (build_query_plan for expected_symbol/AS_OF/window)
unit_query_plan_hash    (same deterministic plan)
receipt.adapter_version == contract.ADAPTER_AUTHORITY_SHA
```

A corrupted unit query-plan hash makes the unit non-reusable (regression
I).

## TARGETED_TESTS

```text
tests/test_r4a7_preclose_full_extraction.py = 33 passed
  (14 new R4A7.2 regressions A..J + 19 preserved R4A7/R4A7.1 tests)
tests/test_r4a_preclose_bounded_adapter.py = 64 passed (preserved)
combined = 97 passed
```

Regression map:

```text
A  unknown execution_context (None/MYSTERY/real/"") fails closed, 0 provider calls
B  dry_run=false with omitted context cannot silently use OFFLINE_TEST
C  limit=1 with N=2 -> PARTIAL_LIMIT_REACHED (COMPLETE_N=1 < FULL_N, PENDING_N>0)
D  later resume completes remaining units (COMPLETE_ALL_UNITS, valid skip)
E  tamper provider_tradestatus only -> unit invalid
E2 tamper provider_tradestatus -> aggregate candidate false
F  tamper coverage_status only -> unit invalid
F2 tamper coverage_status -> aggregate candidate false
G  tamper source/source_version -> unit invalid
G2 tamper source -> aggregate candidate false
H  tamper adapter_version -> unit invalid
I  corrupt unit_query_plan_hash -> unit invalid
I2 corrupt unit_query_plan_hash -> aggregate invalid
J  clean COMPLETE staged unit remains reusable
```

## PRESERVED (no weakening)

```text
REAL pre-resume SHA authority
REAL frozen identity (injection forbidden, load_expected_identity 5456)
atomic tmp -> rename
read-back verification
no silent retry
FAILED semantics
checkpoint contract drift
frozen-universe aggregator
window-boundary wiring into CLEAN_NORMAL
positive candidate gate (PRECLOSE_COMPLETE_CANDIDATE only)
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false
```

## VERIFY

```text
R4A7 targeted tests: 33 passed
existing 64 adapter tests: 64 passed
git diff --check: clean
NETWORK_PROVIDER_DATA_FETCH=NO
FULL_EXTRACTION_EXECUTED=false
```

## BOUNDED_NEXT_ACTION

Sol independent audit of the exact pushed commit; the resumable
orchestrator remains blocked from any full-universe execution until Sol
authorizes a bounded real mode under the hardened authorities.
