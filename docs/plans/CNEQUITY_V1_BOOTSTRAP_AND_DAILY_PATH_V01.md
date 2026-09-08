# CNEquity V1 bootstrap and daily path V01

This is an execution plan, not a publication decision.  CNEquity remains the
only acquisition/data-lake foundation at
`/Users/luke808/AI/local-a-share-data-service-data`; ASL owns formal facts,
certification, publication authority, LocalQuery, and MCP only.

## Observed coverage

| V1 dataset | CNEquity catalog coverage | Next action |
| --- | --- | --- |
| instruments | present; 7,757 rows | reuse curated |
| daily_bars | 2016-01-04 to 2026-09-07; 10,788,039 rows | reuse; do not backfill |
| trading_calendar | 2016-01-01 to 2027-08-17 | reuse curated |
| corporate_actions | 2016-01-07 to 2026-08-17 | incremental native maintenance only |
| adj_factors | absent | native derive after daily/corporate action audit |
| trading_status | absent; snapshot-with-backfill via BaoStock | dataset-specific backfill |
| valuation_metrics | absent; snapshot-with-backfill via BaoStock | dataset-specific backfill |
| minute_bars_5m | absent; source horizon 491 days | native bounded backfill |
| share_structure | absent | native dataset-specific backfill |
| industry_members | absent; SW snapshot-with-backfill | native backfill |
| index_bars / index_constituents | absent | native incremental/backfill |
| fund_flow / margin_trading / market_breadth | absent | native date-walking backfill where supported |

## Responsibility matrix

| Concern | Responsible layer | V1 treatment |
| --- | --- | --- |
| Provider sessions, retries, watchdogs, pacing and data-lake writes | CNEquity | reuse the pinned implementation; ASL must not own a parallel session loop |
| instruments, daily bars, calendar and current corporate-actions lake | CNEquity curated lake | reuse unchanged |
| trading status, valuation, minute bars, capital, industry, index, fund-flow and margin acquisition | CNEquity datasets/adapters | native dataset-specific bootstrap; absent datasets are not ASL facts |
| BaoStock preclose, pct change, turnover, trade status and ST evidence | CNEquity BaoStock session through ASL bridge | thin bridge only; ASL retains raw provenance and fact semantics |
| price-limit rules, limit-up/down classification and reconciliation policy | ASL formal facts | ASL-owned, later phase; not derived by CNEquity acquisition alone |
| manifests, certification, publication authority, LocalQuery and MCP | ASL | formal read-only layer; never infer readiness from physical lake files |
| EastMoney fast review | CNEquity EastMoney client and `clist` | review cache/evidence only; it cannot publish facts |

## Daily two-speed path

```text
Fast review: CNEquity EastMoney adapter → complete clist snapshot →
review cache/evidence → joins to local curated history → REVIEW_READY

Canonical maintenance: cne run daily → staging → compact → watermark/state →
audit → ASL fact certification/publication (separate authorization)
```

The fast review cache is never a publication authority and cannot set
`PRECLOSE_COMPLETE` or `FACTS_READY`.

## Exact supported commands for the next authorized bootstrap

```text
.venv/bin/cne config validate --config config/cnequity.toml
.venv/bin/cne backfill trading_status --config config/cnequity.toml
.venv/bin/cne backfill minute_bars --config config/cnequity.toml --start YYYY-MM-DD
.venv/bin/cne backfill margin_trading --config config/cnequity.toml --start YYYY-MM-DD --end YYYY-MM-DD
.venv/bin/cne run daily --config config/cnequity.toml --group <supported-group>
```

Each command requires a dataset-specific coverage target, source-health check,
and a separate execution authorization.  Do not run `cne init` over the
existing daily lake; do not use an ASL full-history BaoStock loop as a daily
updater.
