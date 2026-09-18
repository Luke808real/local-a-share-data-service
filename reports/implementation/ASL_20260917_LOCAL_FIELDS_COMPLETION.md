# 2026-09-17 local fields completion

Status: AUTHOR / OPERATIONAL PASS on 2026-09-18. No independent audit,
commit/push, formal phase promotion, or global readiness assertion.
Base commit: 99b9326952ae96855c8c642007a8beaef030d182; changes remain local.

## Published result

The independent Facts authority now publishes 5,208 frozen SH/SZ eligible
2026-09-17 keys with preclose, pct_chg, turnover_rate (percent), trade_status,
and is_st. All coverage, duplicate, numeric, provenance, reference-price,
turnover and trade-status conflict counters are zero. Ordinary references use
prior published R3 closes; 24 exact-date corporate actions use hash-verified
CNInfo official implementation announcements and effective cash adjustments.
There are 12 SUSPENDED rows (pct_chg and turnover_rate are null) and 201 ST rows,
including four suspended ST names.

- R3 as-of: 2026-09-17; manifest:
  `e5364e8237e0776b139477f2284b93587bee8b0b5232b0122c583a2b28600a2b`.
- Facts scope: `2026-09-17_FULL_ELIGIBLE_V02`; row schema: `ASL_DAILY_FACTS_V02`.
- Facts manifest:
  `6126f20103eae277f5e23f95f2e314a26f8b6c90b37cfb3e292e01f3dc36710b`.
- Existing V01 pointer/manifest envelope retained; old V1 files are unchanged.
- FACTS_READY=false, PRECLOSE_COMPLETE=false, daily coverage PARTIAL remain
  correct: this is a certified single-date addition, not full historical Facts.
  Facts dates 2026-09-10 through 2026-09-16 have not been backfilled.

## Sources and execution

Data root: `/Users/luke808/AI/local-a-share-data-service-data`.
Evidence base relative to root: `staging/asl_20260917_minimal_update_v01/`.
CNEquity 0.8.0 native source/compact jobs:

| Dataset | Run | Result |
|---|---|---|
| valuation_metrics | 23b9c7f4-bd9f-45e6-ae8b-21942180a918 | 5,453 rows; settled Sept17 snapshot guarded before overnight ingestion |
| trading_status | dcfabac3-3478-47f9-9f59-01db41e7967a | 7,746 source rows; final Facts restricted to 5,208 keys |
| corporate_actions | d0258c78-05bd-4e26-b741-897a22a6b5d4 | 323 incremental event rows; 25 target-date events, 24 within SH/SZ Facts scope |

The overnight snapshot cutoff compares the clock with the target session's
full settlement datetime. Vendor date, settled timestamp, coherence and reset
checks remain active. The installable change is recorded in patch 0003.
No BaoStock per-symbol Facts sweep, strategy data, 5m or historical Facts
backfill was run.

## Validation and compatibility repairs

99 targeted tests passed: V02 derivation/publication, snapshot guards,
publication numeric types and local query regression suites. `git diff --check`
passed. Certification binds source files, official PDFs, exact R3 authority and
prior Facts pointer, then installs a new file and switches the pointer last.

Actual query readback passed for ordinary, adjusted-reference, suspended and
ST examples; all 24 announcement hashes survived serialization. The retained
2026-09-09 Facts key is readable; an unpublished 2026-09-16 key correctly returns
OUTSIDE_PUBLISHED_FACT_SCOPE. Local read-only MCP status and facts also returned
the new authority and a Sept17 reference-price record. No ChatGPT browser
verification was attempted in this local-database continuation.

Readback exposed and repaired three serialization compatibility issues: sparse
late-row evidence columns required full-schema inference; computed_at must be
ISO text to avoid an undeclared timezone Python dependency; all-null blockers
requires explicit String type to avoid a DuckDB filtered Parquet read error.
Each repair installed a new immutable file and exact-hash-guarded authority;
older artifacts remain for traceability. Final publication is `facts_publication_r4`.
Pre-promotion validation now exercises unioned old/new schemas and predicate
queries for all action/suspension keys plus distributed ordinary keys.
The old shadow comparator was also corrected to select the requested date
before indexing by symbol, preventing later published dates from hiding V1.

## Durable evidence

Relative to the evidence base:

- `valuation_run.json`, `trading_status_run.json`, `corporate_actions_run.json`
- `reference_price_evidence.json` and `official_sources_fetch.json`
- `facts_publication_r4/certification_receipt.json`
- `facts_publication_r4/promotion_plan.json` and `promotion_receipt.json`
- `local_readback.json`, `local_mcp_readback.json`

Operational publication is complete for the requested date and five fields.
