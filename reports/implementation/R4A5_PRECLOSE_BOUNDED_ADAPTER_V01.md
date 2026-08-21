# R4A5 PRECLOSE BOUNDED ADAPTER — V01 (author report)

DATE: 2026-08-21
BRANCH: codex/r4a5-preclose-bounded-adapter-v01
CONTRACT_HEAD: e0b6c9325c2a2951c4c51dff4c2ee2332115d48c
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

## ADAPTER_STATUS=IMPLEMENTED_DRY_RUN_ONLY

## FILES_CHANGED

src/ashare_data/r4a_preclose_bounded_adapter.py
tools/run_r4a_preclose_bounded_pilot.py
tests/test_r4a_preclose_bounded_adapter.py
reports/implementation/R4A5_PRECLOSE_BOUNDED_ADAPTER_V01.md

## TESTS

TARGETED_TESTS=25 passed (offline, fake provider, zero real calls)
adapter normalization 1-11, clean-normal parity 12-17, sentinels 18-19,
query-plan determinism 20, dry-run zero-write 21, superset non-block,
required-key loader, real-root identity shape.

## DRY_RUN

DRY_RUN_STATUS=OK
PILOT_SYMBOL_N=24
PILOT_SYMBOL_HASH=5fa9f5c9ef376f0c453d3f543dc3a8ee9d61f73cec3a0fd35a9bea5081e17843
QUERY_WINDOW_N=264
QUERY_PLAN_HASH=4790866545a08b812628d4fd99bce97885de3b8c0a3ecc4beebee2acd116bc59
NETWORK_PROVIDER_DATA_FETCH=NO
MARKET_DATA_WRITE=NO

## REAL-ROOT IDENTITY

R4A0_READY=True
FORMAL_IDENTITY_N=5456
FORMAL_IDENTITY_HASH=2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f

## OFFICIAL SENTINELS (frozen evidence, no re-fetch)

SENTINEL_N=24
SENTINEL_EXACT_N=24
SENTINEL_MISMATCH_N=0
FROZEN_OFFICIAL_SENTINEL_PASS=True

## QUALITY_GATE_CONTRACT

QUALITY_GATE_PASS=true only if FORMAL_FACT_ROW_N==REQUIRED_ROW_N AND
MISSING_REQUIRED_N=0 AND UNEXPECTED_TRADED_N=0 AND TRADESTATUS_UNKNOWN_N=0
AND IDENTITY_FAILURE_N=0 AND DUPLICATE_N=0 AND POST_ASOF_N=0
AND INVALID_PRECLOSE_N=0 AND every formal row has provider_tradestatus=1,
finite positive preclose, coverage_status=COVERED.
PROVIDER_SUSPENDED_SUPERSET_N may be >0 (non-blocking).

## FORMAL_ROW_CONTRACT

formal rows = required key AND tradestatus=='1' AND preclose finite
positive AND provider code/date exact AND trade_date<=AS_OF;
source=BAOSTOCK_HISTORY_K_PRECLOSE, source_version=baostock-0.9.3,
query_contract_version=R4A_PRECLOSE_V01, provider_tradestatus=1,
coverage_status=COVERED. Audit rows never enter preclose_facts.

## WRITE_BOUNDARY

FORMAL_PRECLOSE_DATASET_WRITE=NO
MARKET_DATA_WRITE=NO
MANIFEST_MUTATION=NO
REAL_ROOT_ACCESS=READ_ONLY

## KNOWN_UNIMPLEMENTED

full resumable orchestrator / full 5456 extraction / PRECLOSE_COMPLETE
promotion / R4B / turnover / isST / shared provider extraction.

## BOUNDED_NEXT_ACTION

Sol independent audit of the exact pushed commit; upon code-audit
approval, a separate bounded real pilot may reuse this adapter with a
real BaoStock session (provider fetch).
