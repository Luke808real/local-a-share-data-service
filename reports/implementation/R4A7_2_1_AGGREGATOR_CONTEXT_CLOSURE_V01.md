# R4A7.2.1 AGGREGATOR CONTEXT CLOSURE — V01 (author report)

DATE: 2026-08-24
BRANCH: codex/r4a7-2-1-aggregator-context-closure-v01
BASE_HEAD: 66350c553c3aff6a74dbd1c4f37376cfc7ce93c2
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

## AUTHOR_STATUS

```text
AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
FULL_EXTRACTION_EXECUTED=false
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false
NETWORK_PROVIDER_DATA_FETCH=NO
MARKET_DATA_WRITE=NO
```

Minimal correctness patch restricted to `aggregate_full_run()` context
handling. No BaoStock fetch, no full extraction, no market-data write, no
architecture redesign.

## BASE_HEAD / HEAD

```text
BASE_HEAD=66350c553c3aff6a74dbd1c4f37376cfc7ce93c2
HEAD=SELF — commit containing this file
REMOTE_HEAD=exact SHA returned after non-force push
```

## FILES_CHANGED

```text
M src/ashare_data/r4a7_preclose_full_extraction.py
M tests/test_r4a7_preclose_full_extraction.py
A reports/implementation/R4A7_2_1_AGGREGATOR_CONTEXT_CLOSURE_V01.md
```

## CHANGE

`aggregate_full_run` previously defaulted `execution_context` to
OFFLINE_TEST and read the manifest before validating the context. Now:

```text
execution_context allowed values: REAL, OFFLINE_TEST (exact strings)
None / omitted / unknown / arbitrary string -> UNKNOWN_EXECUTION_CONTEXT
  STATUS=UNKNOWN_EXECUTION_CONTEXT
  COVERAGE_COMPLETE=false
  PRECLOSE_COMPLETE_CANDIDATE=false
  PRECLOSE_COMPLETE=false
  FULL_MARKET_AUTHORIZED=false
  MARKET_DATA_WRITE=NO
```

The context is validated BEFORE `load_manifest()` and before any unit
receipt or staged parquet read. For an unknown context the checkpoint is
never read or trusted: a corrupt/nonexistent manifest together with
execution_context="MYSTERY" returns UNKNOWN_EXECUTION_CONTEXT, NOT
CHECKPOINT_CORRUPT.

## PRESERVED PATHS

```text
REAL:
  authoritative root required
  expected_adapter_sha / runtime_adapter_sha required
  adapter == expected == runtime, canonical 40-char lowercase hex
  frozen identity N=5456 / hash
  full query plan
  manifest contract exact
  every expected COMPLETE receipt valid (with staged parquet integrity)
  boundary / sentinel / CLEAN_NORMAL gates
  caller-supplied symbols do not define REAL coverage

OFFLINE_TEST:
  available ONLY when explicitly supplied
  bounded fixtures continue using execution_context=OFFLINE_TEST
```

## TARGETED_TESTS

```text
tests/test_r4a7_preclose_full_extraction.py = 38 passed
  (5 new R4A7.2.1 regressions A-E + 33 preserved)
tests/test_r4a_preclose_bounded_adapter.py = 64 passed (preserved)
combined = 102 passed
```

Regression map:

```text
A  aggregate_full_run(context omitted) -> UNKNOWN_EXECUTION_CONTEXT
B  aggregate_full_run(context="MYSTERY"/""/"real") -> UNKNOWN_EXECUTION_CONTEXT
C  UNKNOWN context + corrupt manifest -> UNKNOWN_EXECUTION_CONTEXT
   (proves the gate runs before checkpoint trust, not CHECKPOINT_CORRUPT)
D  positive OFFLINE_TEST candidate fixture still works (24 sentinels exact)
   when OFFLINE_TEST is explicitly supplied
E  REAL aggregator authority drift (manifest SHA vs runtime) -> fail closed
```

## VERIFY

```text
R4A7 targeted tests: 38 passed
existing 64 adapter tests: 64 passed
git diff --check: clean
NETWORK_PROVIDER_DATA_FETCH=NO
FULL_EXTRACTION_EXECUTED=false
PRECLOSE_COMPLETE=false
```

## BOUNDED_NEXT_ACTION

Sol independent audit of the exact pushed commit; full-universe execution
remains blocked until Sol authorizes a bounded real mode under the
hardened authorities.
