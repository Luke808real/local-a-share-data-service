# R1 Legacy Inventory

**SPEC_VERSION:** `V1.0 FROZEN`
**PHASE:** `R1 — LOCAL ASSET AUDIT`
**MODE:** `RESEARCH / READ_ONLY`
**STATUS:** `EVIDENCE ONLY — NOT A REUSE OR MIGRATION DECISION`

## Scope and evidence rules

This inventory records only legacy-root metadata, supplied/footer evidence, bounded manifest/lineage/validation metadata, and bounded Parquet-footer observations. It does not authorize migration, normalization, deletion, repair, or a reuse decision; Task 4 owns those decisions.

- `FOOTER_FACT` means Parquet footer metadata proved the stated rows, schema, or min/max.
- `MANIFEST_FACT` means a manifest, lineage, or validation artifact stated the fact.
- `UNKNOWN` means the evidence did not prove it. Numeric values, storage paths, and field names are never used to infer units or semantics.
- SQLite and DuckDB contents are not used as dataset evidence unless explicitly described below from a prior safe metadata observation.

## Read-only incident: `READONLY_BREACH_INDETERMINATE`

The `/Users/luke808/AI/asl-shared` audit encountered a read-only breach concern: opening `meta/manifest.db` with SQLite `mode=ro` plausibly created `manifest.db-wal` (0 B) and `manifest.db-shm` (32,768 B). The database mtime was unchanged and no open handles remained, but strict read-only `PASS` cannot be claimed for that root.

The sidecars were left untouched. No deletion, repair, compaction, or follow-up database access is authorized. All `asl-shared` evidence below is labeled `READONLY_BREACH_INDETERMINATE`.

## Root summaries

| Legacy root | Root metadata | Scoped storage observations | Read-only result |
|---|---:|---|---|
| `/Users/luke808/AI/asl-shared` | 9,208 files / 266,997,438 B | Dataset paths not supplied; bounded footer evidence listed below | `READONLY_BREACH_INDETERMINATE` |
| `/Users/luke808/AI/asl-r8-5m-lake` | 85 files / 10,449,522 B | Curated 5m Parquet, staging 5m Parquet, metadata SQLite, DuckDB file | Root/direct-child mtimes unchanged; no audit write observed |
| `/Users/luke808/AI/V flash/data` | 166,226 files / 36,891,393,771 B | Allowed base-market scope: canonical/raw/manifests/lineage/validation plus metadata-only `warehouse.duckdb` | Root/direct-child mtimes unchanged; no audit write observed |

For VFlash, the allowed base-market scope totals 4,317,696,946 B: canonical 30 files / 2,657,351,314 B; raw 297 / 860,109,734 B; manifests 15 / 208,984 B; lineage 7 / 23,272,600 B; validation 7 / 9,354 B; `warehouse.duckdb` 776,744,960 B (not opened).

## `/Users/luke808/AI/asl-shared`

All rows in this section carry `READONLY_BREACH_INDETERMINATE`.

| Dataset | Storage / footer facts | Date coverage proved | Schema / provenance facts | Limits |
|---|---|---|---|---|
| instruments | 1 Parquet / 7,736 footer rows | `UNKNOWN` | Schema, source, data-version, distinct symbols `UNKNOWN` | No identity/coverage proof |
| trading_calendar | 5 Parquet / 1,462 footer rows | 2023-08-07 to 2027-08-07 | Schema and calendar semantic `UNKNOWN` | Does not establish observed daily/5m completeness |
| daily_bars | 732 Parquet / 4,887,134 footer rows | 2023-08-07 to 2026-08-13 | `symbol,date,OHLC,volume,amount,source,data_version,fetched_at` | Units, PK, distinct symbols, expected coverage `UNKNOWN` |
| adj_factors | 732 Parquet / 4,866,238 footer rows | 2023-08-07 to 2026-08-13 | Schema/source/data-version `UNKNOWN` | Factor semantic, PK, coverage `UNKNOWN` |
| corporate_actions | 37 Parquet / 70,455 footer rows | 1990-03-01 to 2026-08-17 | Schema/provenance `UNKNOWN` | No adjustment-contract conclusion |
| trading_status | 37 Parquet / 371,778 footer rows | 2023-08-08 to 2026-08-13 | Schema/status semantic/provenance `UNKNOWN` | No mapping to V1 historical status enum |
| index_bars | 4 Parquet / 5,856 footer rows | 2023-08-07 to 2026-08-13 | Schema/provenance/index universe `UNKNOWN` | Expected index set not proved |
| minute_bars_5m | Absent in supplied evidence | `UNKNOWN` | `UNKNOWN` | No 5m asset evidence |
| turnover / float_shares | Absent in supplied evidence | `UNKNOWN` | `UNKNOWN` | No turnover or float-share evidence |
| SW industry membership | Absent in supplied evidence | `UNKNOWN` | `UNKNOWN` | No primary-industry membership evidence |

## `/Users/luke808/AI/asl-r8-5m-lake`

### Curated 5m asset

`FOOTER_FACT`: `curated/minute_bars_5m/trade_date=*/part-merged.parquet` contains 40 Parquet files / 5,758,460 B / 270,000 footer rows. Hive partitions and footer `trade_date` agree 40/40, covering 2026-06-04 through 2026-07-30.

The identical footer schema is:

```text
symbol, trade_date, bar_time, frequency,
open, high, low, close, volume, amount,
source, data_version, fetched_at
```

`trade_date` is Parquet `DATE`. `bar_time` is microsecond timestamp with `isAdjustedToUTC=false`; every row-group footer endpoint is naive `09:35:00` to `15:00:00`, and its endpoint dates match the partition. This does **not** prove left/right bar labeling, time-zone/business-session semantics, or an intra-session/lunch schedule.

All curated footer statistics show non-null `frequency=5m`, `source=tdx_protocol`, and `data_version=v1`. Curated `fetched_at` is `2026-08-08T19:11:17.023044+00:00`. Footer fields prove numeric types for `volume` and `amount`, not their units. Exact distinct symbols and `(symbol, bar_time)` uniqueness are `UNKNOWN`; no row-level duplicate scan or formal constraint query ran.

### Supporting metadata and staging

| Path | Fact | Limitation |
|---|---|---|
| `staging/minute_bars_5m/` | 2 Parquet / 4,313,693 B / 296,880 footer rows | Staging is not the curated query target; row identity/deduplication not proved |
| `meta/manifest.db` | Prior immutable read reported two successful 5m batches: 26,880 and 270,000 rows, retry count 0 | Not used to prove PK/units/semantics |
| `meta/state/minute_bars_5m.json` | `last_success_trade_date=2026-07-30` | Not an expected-calendar completeness proof |
| `duckdb/ashare-lake.duckdb` | 274,432 B; raw catalog bytes identified a `minute_bars_5m` view over curated Parquet | DuckDB reader/catalog query unavailable; byte-string observation is not a formal catalog query |

The root's `backups/`, `derived/`, and `raw/` directories contained no files in the bounded inventory. Current reader limitation: no installed DuckDB/PyArrow reader was available; an in-memory footer parser read no row pages and no packages were installed.

## `/Users/luke808/AI/V flash/data` base-market scope only

The VFlash audit included only `canonical/`, `raw/`, metadata in `manifests/`, `lineage/`, `validation/`, and filesystem metadata for `warehouse.duckdb`. The database was not opened.

### Canonical assets

| Dataset / path | Storage facts | Coverage / schema facts | Provenance and limitations |
|---|---|---|---|
| `canonical/daily_bars/` | 15 Parquet / 2,656,081,602 B | Maximum observed snapshot `snap-2026-08-13-bed1fd379696`: 3,327,462 footer rows, 2024-01-02 to 2026-08-13 | Fields include `code,trade_date,OHLC,preclose,volume,amount,turnover_rate,pct_change,trade_status,is_st,selected_provider,reconciliation_status,source_row_hash,dataset_snapshot_id`; `code` is not a proved V1 symbol mapping |
| `canonical/limit_up_pool/` | 15 Parquet / 1,269,712 B | Maximum observed snapshot: 1,627 footer rows, 2026-07-13 to 2026-08-13 | Base-market fact candidate only; not a primary SW industry dataset |
| `manifests/` | 15 JSON / 208,984 B | Manifest `snap-2026-08-13-bed1fd379696` says `RAW_UNADJUSTED`, `TDX/TENCENT v1`, and two canonical file hashes | `SCREEN_READY` is not V1 publication/acceptance |
| `lineage/` | 7 JSONL / 23,272,600 B | Bounded first/last records for the 2026-08-13 artifact had `selected_provider=TDX`, date 2026-08-13 | Latest canonical footer also contains at least `AKSHARE` and `TUSHARE` selected-provider values; historical provenance relationship remains partial |
| `validation/` | 7 JSON / 9,354 B | Latest validation reports zero duplicate `(code,trade_date)`, OHLC, negative-volume/amount, unexplained-missing, and traceability failures | VFlash validator evidence only; corporate-action detection is `NOT_IMPLEMENTED` |

Latest canonical `turnover_rate` has 3,216,568 nulls of 3,327,462 footer values. Its percentage/ratio semantic remains `UNKNOWN`. Canonical fields do not carry source-unit/normalized-unit fields, so raw source tags below must not be used to infer canonical volume/amount units.

### Raw provider assets

| Path | Files / footer rows | Footer date range | Constant provider metadata observed |
|---|---:|---|---|
| `raw/akshare/daily_bars/` | 103 / 2,006,589 | 2024-01-02 to 2026-07-31 | `AKSHARE 1.18.81`; source/normalized tag `yuan;shares;yuan` |
| `raw/baostock/daily_bars/` | 67 / 2,007,131 | 2024-01-02 to 2026-07-31 | `BAOSTOCK 0.9.3`; source/normalized tag `yuan;shares;yuan` |
| `raw/asl/daily_bars/` | 2 / 76,538 | 2026-07-09 to 2026-07-24 | `ASL ba5681a`; source/normalized tag `yuan;shares;yuan` |
| `raw/tushare/daily_bars/` | 35 / 3,223,148 | 2024-01-02 to 2026-07-31 | `TUSHARE 1.4.29`; source `yuan;lots(shou);thousand_yuan`, normalized `yuan;shares;yuan` |
| `raw/asl/adjustment_factor/` | 2 / 186,974 | 2026-07-08 to 2026-07-24 | `ASL ba5681a`; `raw_factor` |
| `raw/tushare/adjustment_factor/` | 35 / 3,391,611 | 2024-01-02 to 2026-07-31 | `TUSHARE 1.4.29`; `raw_factor` |
| `raw/tushare/daily_basic/` | 35 / 3,415,290 | 2024-01-02 to 2026-07-31 | `TUSHARE 1.4.29`; source `percent;wan_yuan`, normalized `percent;yuan` |
| `raw/tushare/price_limits/` | 3 / 44,279 | 2024-01-02 to 2026-07-31 | `TUSHARE 1.4.29`; `yuan` |
| `raw/tushare/suspension/` | 13 / 3,786 | 2024-01-02 to 2026-04-30 | `TUSHARE 1.4.29`; fields include `suspend_type,suspend_timing` |

The tag strings are exact footer metadata, not a proved mapping from each tag position to a V1 output field. Footer row sums across raw files may include multiple batches and are not asserted as deduplicated datasets.

### Excluded VFlash scopes

`screen/`, `forward-paper/`, and `tmp/` were explicitly excluded and never traversed. Literal top-level `states/`, `generations/`, `forward/`, `TradePlan/`, `research/`, and `cache/` were not present and were not searched elsewhere. `outcome-study/`, `quarantine/`, top-level logs, and supervisor scripts were outside the allowed scope and uninspected.

## Reader limitations and next boundary

- No full-row/full-market scan ran.
- No legacy SQLite/DuckDB was opened by this Task 3 synthesis.
- The Task 3 utility is explicit-root only, bounded by depth/footer-file limits, and reports `READER_UNAVAILABLE` rather than installing a Parquet reader.
- This report intentionally does not assign `DIRECT_REUSE`, `MIGRATE_AFTER_NORMALIZATION`, `CROSSCHECK_ONLY`, or `REJECT`; those are Task 4 decisions.
