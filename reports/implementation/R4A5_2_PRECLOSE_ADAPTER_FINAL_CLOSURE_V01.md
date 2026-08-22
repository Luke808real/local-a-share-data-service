# R4A5_2 PRECLOSE ADAPTER FINAL CLOSURE — V01 (author report)

DATE: 2026-08-22
BRANCH: codex/r4a5-2-preclose-adapter-final-closure-v01
CONTRACT_HEAD: f957ceae731e7e77915747e6e74eb86a3b349c46
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

## AUTHOR_STATUS

```text
AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
R4A4_SOURCE_CONTRACT_STATUS=FROZEN_V01_2_PENDING_SOL_AUDIT
R4A5_2_STATUS=PASS_PENDING_SOL_AUDIT
R4A_REAL_PILOT=FORBIDDEN_PENDING_SOL_AUDIT
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false
```

AUTHOR_STATUS is author only. It is not SOL_AUDIT_PASS, not
FINAL_PASS, and not an authorization for any real pilot.

## BASE_HEAD / HEAD

```text
BASE_HEAD=f957ceae731e7e77915747e6e74eb86a3b349c46
HEAD=SELF — commit containing this file
REMOTE_HEAD=exact SHA returned after non-force push (see task handback)
```

## FILES_CHANGED

```text
M docs/plans/R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md
M reports/planning/R4A4_PRECLOSE_SOURCE_CONTRACT_RECEIPT.json
M src/ashare_data/r4a_preclose_bounded_adapter.py
M tools/run_r4a_preclose_bounded_pilot.py
M tests/test_r4a_preclose_bounded_adapter.py
?? reports/implementation/R4A5_PRECLOSE_BOUNDED_ADAPTER_V01_2.md (dry-run tool output)
?? reports/implementation/R4A5_2_PRECLOSE_ADAPTER_FINAL_CLOSURE_V01.md (this report)
```

No other production file was modified. No provider fetch, no market-data
write, no full-market execution.

## CONTRACT_AMENDMENT

V01.2 minimal amendment to the frozen R4A4 canonical source contract
(docs only for the contract file; adapter code implements the frozen
classification without changing the canonical source role):

```text
WINDOW_BOUNDARY_EDGE frozen classification:
  (symbol, trade_date) is the symbol's FIRST required actual-traded row
  inside WINDOW_START..AS_OF
  AND instrument.list_date < WINDOW_START
  AND authoritative local R3 daily_bars has no predecessor before
    WINDOW_START

WINDOW_BOUNDARY_EDGE is NOT part of CLEAN_NORMAL parity scope.
It remains a formal required preclose row (tradestatus=1 + finite
positive preclose + identity/ASOF pass -> canonical preclose).
IPO within window (list_date >= WINDOW_START) stays IPO_FIRST_LISTING_DAY.
```

Receipt `reports/planning/R4A4_PRECLOSE_SOURCE_CONTRACT_RECEIPT.json` was
updated to the matching V01.2 fields (SOURCE_CONTRACT_STATUS,
window_boundary_edge, window_boundary_gate).

## WINDOW_BOUNDARY_SEMANTIC

```text
WINDOW_BOUNDARY_REQUIRED_N
WINDOW_BOUNDARY_PRESENT_N
WINDOW_BOUNDARY_VALID_N
WINDOW_BOUNDARY_MISSING_N
WINDOW_BOUNDARY_INVALID_N

WINDOW_BOUNDARY_PASS=true only if
  PRESENT_N == REQUIRED_N
  AND VALID_N == REQUIRED_N
  AND MISSING_N == 0
  AND INVALID_N == 0
```

Previous-close parity for window-boundary rows is never proven from
BaoStock itself. Unknown/missing -> FAIL CLOSED.

## ADAPTER_AUTHORITY_GATE

```text
adapter_authority_status(adapter_version, expected_sha, runtime_sha,
execution_mode)

REAL mode (the only mode permitted for dry_run=false):
  PASS only if adapter_version == expected_sha == runtime_sha,
  all non-None valid SHAs -> ADAPTER_AUTHORITY_MODE=EXACT_SHA.
  adapter_version="TEST" -> FAIL.
  runtime_sha is None -> FAIL. expected_sha is None -> FAIL.

OFFLINE_TEST mode:
  adapter_version="TEST" allowed -> OFFLINE_FIXTURE.

run_bounded_adapter(dry_run=false) hard-forces execution_mode=REAL;
the caller cannot choose OFFLINE_TEST for real runs.
```

Old `EXPECTED_ADAPTER_SHA=710a7c80...` constant was removed from real-gate
defaults; it appears only as historical metadata in regression tests.

## PROVIDER_FIELD_IDENTITY_GATE

```text
BaostockSessionProvider.query_history_k_data_plus(...) verifies
result.fields exactly equals ["date","code","preclose","tradestatus"]
in name, order, and count.

missing / extra / renamed / reordered field -> FAIL CLOSED
  PROVIDER_FIELD_IDENTITY_FAILURE
row-length checks remain but never replace field-name identity.
```

Provider result contract retained: error_code=="0", requested code exact,
requested start <= returned date <= end; exceptions never become empty
successful results.

## TARGETED_TESTS

```text
tests/test_r4a_preclose_bounded_adapter.py
55 passed (= 40 kept + 15 new A-P coverage incl. M missing/extra and P)
```

New coverage includes:

```text
A  REAL mode adapter_version="TEST" -> FAIL
B  REAL mode runtime_sha=None -> FAIL
C  REAL mode expected_sha=None -> FAIL
D  REAL mode exact valid SHA all equal -> PASS (EXACT_SHA)
E  old 710a7c80... adapter on new runtime -> FAIL
F  listed-before-window first in-window required row -> WINDOW_BOUNDARY_EDGE
G  IPO (list_date >= WINDOW_START) -> NOT WINDOW_BOUNDARY_EDGE
H  WINDOW_BOUNDARY_EDGE excluded from CLEAN_NORMAL
I  missing window-edge formal fact -> WINDOW_BOUNDARY_PASS=false
J  valid window-edge formal fact -> PASS
K  provider result.fields reordered -> PROVIDER_FIELD_IDENTITY_FAILURE
L  provider result.fields renamed -> FAIL
M  provider result.fields missing/extra -> FAIL
N  provider exact field identity -> PASS
O  CLEAN_NORMAL missing predecessor but NOT window-boundary -> UNCOMPARED -> FAIL
P  window-boundary predecessor absent -> does NOT increase CLEAN_NORMAL_UNCOMPARED
```

## FULL_SUITE

```text
464 total: 463 passed + 1 pre-existing unrelated failure
FAILED: tests/test_project_docs_contract.py::test_project_state_activates_r3_after_r2_audit_pass
  (hard-coded AS_OF 2026-08-18 vs PROJECT_STATE actual 2026-08-19;
   exists at BASE_HEAD, unrelated to R4A5.2; not fixed in scope)
```

## REAL_ROOT_DRY_RUN

Read-only dry-run against `/Users/luke808/AI/local-a-share-data-service-data`.

```text
DRY_RUN_STATUS=OK
R4A0_READY=true        (r4a0 run_gate, coverage 5456/5456)
R3_IDENTITY_MATCH=true
FORMAL_IDENTITY_N=5456
FORMAL_IDENTITY_HASH=2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f
PILOT_SYMBOL_N=24
PILOT_SYMBOL_HASH=5fa9f5c9ef376f0c453d3f543dc3a8ee9d61f73cec3a0fd35a9bea5081e17843
QUERY_WINDOW_N=264
QUERY_PLAN_HASH=1228af76ccdfee032de437c0f47ab343248f9c94b0a15961bd6fc18fd99e2e01
WINDOW_BOUNDARY_REQUIRED_N=21   (= 24 pilot - 3 in-window IPO; self-consistent)
WINDOW_BOUNDARY_PASS_DRY_RUN_STATUS=NOT_RUN_DRY_RUN
FROZEN_SENTINEL_EXPECTED_N=24
FROZEN_OFFICIAL_SENTINEL_RUNTIME_STATUS=NOT_RUN_DRY_RUN
NETWORK_PROVIDER_DATA_FETCH=NO
MARKET_DATA_WRITE=NO
```

## WRITE_BOUNDARY

```text
FORMAL_PRECLOSE_DATASET_WRITE=NO
MARKET_DATA_WRITE=NO
MANIFEST_MUTATION=NO
REAL_ROOT_ACCESS=READ_ONLY
PROVIDER_DATA_FETCH=NO
```

## KNOWN_UNIMPLEMENTED

```text
full resumable orchestrator
full 5456 extraction
PRECLOSE_COMPLETE promotion
R4B / turnover / isST / shared provider extraction
real BaoStock pilot (forbidden until Sol audit)
```

## BOUNDED_NEXT_ACTION

Sol independent audit of the exact pushed commit. Upon code-audit
approval, a separate bounded real pilot may reuse this adapter with a
real BaoStock session (provider fetch) under the hardened gates, with the
expected implementation SHA injected from the audited HEAD.
