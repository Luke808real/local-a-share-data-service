# R4A5.1 PRECLOSE BOUNDED ADAPTER HARDENING — V01_1 (author report)

DATE: 2026-08-21
BRANCH: codex/r4a5-1-preclose-adapter-hardening-v01
CONTRACT_HEAD: e0b6c9325c2a2951c4c51dff4c2ee2332115d48c
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

## ADAPTER_STATUS=IMPLEMENTED_DRY_RUN_ONLY

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
