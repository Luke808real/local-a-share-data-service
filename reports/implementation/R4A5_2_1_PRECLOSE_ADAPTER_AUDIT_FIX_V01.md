# R4A5.2.1 PRECLOSE ADAPTER AUDIT FIX — V01 (author report)

DATE: 2026-08-23
BRANCH: codex/r4a5-2-1-preclose-adapter-audit-fix-v01
CONTRACT_HEAD: 503aa350b88ced5266bcb923c85f0cf1c4fa1fe1
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

## ADAPTER_STATUS=IMPLEMENTED_DRY_RUN_ONLY

## PRE-WINDOW PREDECESSOR AUTHORITY (V01.2.1 audit fix)

PRE_WINDOW_PREDECESSOR_AUTHORITY_SOURCE=load_required_keys derives
PRE_WINDOW_PREDECESSOR_SYMBOLS from authoritative local R3 daily_bars
bars strictly before WINDOW_START; never from window-filtered keys.
PRE_WINDOW_PREDECESSOR_SYMBOL_N=0
PRE_WINDOW_BAR_KEY_N=0

## SHA_AUTHORITY_TEST_MATRIX (V01.2.1 audit fix)

REAL mode PASS now requires adapter_version == expected_adapter_sha ==
runtime_adapter_sha AND every value matches ^[0-9a-f]{40}$ (canonical
40-char lowercase hex Git SHA). Matrix (all asserted in tests):

```text
REAL TEST/TEST/TEST                       -> FAIL (INVALID_SHA)
REAL abc/abc/abc                          -> FAIL (INVALID_SHA)
REAL 39-char / 41-char / 40-char nonhex   -> FAIL (INVALID_SHA)
REAL valid 40-char SHA all equal          -> PASS (EXACT_SHA)
REAL any mismatch                         -> FAIL (SHA_MISMATCH)
REAL None expected / runtime              -> FAIL (MISSING_SHA)
unknown execution_mode                    -> FAIL (UNKNOWN_MODE)
OFFLINE_TEST TEST (fixture path only)     -> PASS (OFFLINE_FIXTURE)
real run with TEST/TEST/TEST              -> zero provider calls
```

run_bounded_adapter(dry_run=false) continues to hard-force execution_mode
=REAL; the caller cannot select OFFLINE_TEST for real runs.

## WINDOW_BOUNDARY_PASS_CONTRACT (V01.2.1 audit fix)

```text
verify_window_boundary_rows PASS exactly when:
  PRESENT == REQUIRED
  AND VALID == REQUIRED
  AND MISSING == 0
  AND INVALID == 0
```

No undocumented REQUIRED_N > 0 condition. Empty required set with zero rows
is a trivial PASS. Missing, invalid, or duplicate boundary formal rows FAIL.

The predecessor signal for WINDOW_BOUNDARY_EDGE classification is
PRE_WINDOW_PREDECESSOR_SYMBOLS derived by load_required_keys from
authoritative local R3 daily_bars bars strictly before WINDOW_START.
It is never inferred from the already window-filtered required_keys.

## R4A0 PREREQUISITE (independent fields)

R4A0_READY=True   (r4a0 run_gate)
R3_IDENTITY_MATCH=True   (independent)
FORMAL_IDENTITY_N=5456
FORMAL_IDENTITY_HASH=2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f

## DRY_RUN

DRY_RUN_STATUS=OK
PILOT_SYMBOL_N=24
PILOT_SYMBOL_HASH=5fa9f5c9ef376f0c453d3f543dc3a8ee9d61f73cec3a0fd35a9bea5081e17843
QUERY_WINDOW_N=264
QUERY_PLAN_HASH=1228af76ccdfee032de437c0f47ab343248f9c94b0a15961bd6fc18fd99e2e01
NETWORK_PROVIDER_DATA_FETCH=NO
MARKET_DATA_WRITE=NO

## OFFICIAL SENTINELS (expected contract; runtime NOT_RUN in dry-run)

FROZEN_SENTINEL_EXPECTED_N=24
FROZEN_OFFICIAL_SENTINEL_RUNTIME_STATUS=NOT_RUN_DRY_RUN

## WINDOW BOUNDARY (V01.2 contract; runtime NOT_RUN in dry-run)

WINDOW_BOUNDARY_REQUIRED_N=21
WINDOW_BOUNDARY_PASS_DRY_RUN_STATUS=NOT_RUN_DRY_RUN

## QUERY PLAN HASH CONTRACT

QUERY_PLAN_HASH covers symbol, bs_code, year, start, end, fields,
frequency, adjustflag, source_version, query_contract_version, AS_OF,
WINDOW_START, CNEQUITY_PIN. Same executable contract -> same hash.

## QUALITY_GATE_CONTRACT

QUALITY_GATE_PASS=true only if FORMAL_FACT_ROW_N==REQUIRED_ROW_N AND
MISSING_REQUIRED_N=0 AND UNEXPECTED_TRADED_N=0 AND TRADESTATUS_UNKNOWN_N=0
AND IDENTITY_FAILURE_N=0 AND WINDOW_SCOPE_FAILURE_N=0 AND DUPLICATE_N=0
AND POST_ASOF_N=0 AND INVALID_PRECLOSE_N=0 AND every formal row has
provider_tradestatus=1, finite positive preclose, coverage_status=COVERED.
PROVIDER_SUSPENDED_SUPERSET_N may be >0 (non-blocking).

## FORMAL_ROW_CONTRACT

formal rows = required key AND tradestatus=='1' AND preclose finite
positive AND provider code/date exact AND trade_date<=AS_OF;
source=BAOSTOCK_HISTORY_K_PRECLOSE, source_version=baostock-0.9.3,
query_contract_version=R4A_PRECLOSE_V01, provider_tradestatus=1,
coverage_status=COVERED. Audit rows never enter preclose_facts.
adapter_version requires expected+runtime SHA match in real execution.

## WRITE_BOUNDARY

FORMAL_PRECLOSE_DATASET_WRITE=NO
MARKET_DATA_WRITE=NO
MANIFEST_MUTATION=NO
REAL_ROOT_ACCESS=READ_ONLY
PROVIDER_DATA_FETCH=NO

## KNOWN_UNIMPLEMENTED

full resumable orchestrator / full 5456 extraction / PRECLOSE_COMPLETE
promotion / R4B / turnover / isST / shared provider extraction.

## BOUNDED_NEXT_ACTION

Sol independent audit of the exact pushed commit; upon code-audit
approval, a separate bounded real pilot may reuse this adapter with a
real BaoStock session (provider fetch) under the hardened gates.
