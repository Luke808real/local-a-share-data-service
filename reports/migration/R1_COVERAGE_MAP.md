# R1 Legacy Coverage Map

**SPEC_VERSION:** `V1.0 FROZEN`
**PHASE:** `R1 — LOCAL ASSET AUDIT`
**STATUS:** `EVIDENCE ONLY — NOT A COMPATIBILITY OR REUSE DECISION`

## Status semantics

- `COMPLETE_EVIDENCE`: expected universe, dates, schema, and required semantics are proved.
- `PARTIAL_EVIDENCE`: some bounded storage/coverage facts are proved, but one or more required facts remain unproved.
- `ABSENT`: no asset for the dataset was found in the approved inspectable scope.
- `UNKNOWN`: the approved evidence cannot establish whether an asset exists or satisfies the stated fact.

No V1 dataset receives `COMPLETE_EVIDENCE` in R1 Task 3. Expected full-universe/calendar coverage, units, and semantic contracts remain outside the evidence gathered here.

## `READONLY_BREACH_INDETERMINATE`: ASL shared

The `/Users/luke808/AI/asl-shared` audit plausibly caused SQLite read-only sidecars (`meta/manifest.db-wal`, 0 B; `manifest.db-shm`, 32,768 B). The database mtime was unchanged and no handle remained, but strict read-only `PASS` cannot be claimed. The sidecars are untouched. Every `asl-shared` contribution below remains usable only as `READONLY_BREACH_INDETERMINATE` evidence.

## V1 dataset coverage matrix

| V1 dataset | Status | Exact observed evidence | Material gaps |
|---|---|---|---|
| instruments | `PARTIAL_EVIDENCE` | `asl-shared`: 1 Parquet / 7,736 footer rows | Date range, schema, symbol identity, provenance, and expected universe `UNKNOWN`; no inspected VFlash/r8 instrument asset |
| daily_bars | `PARTIAL_EVIDENCE` | `asl-shared`: 732 / 4,887,134 footer rows, 2023-08-07 to 2026-08-13; VFlash canonical maximum snapshot: 3,327,462 footer rows, 2024-01-02 to 2026-08-13 | Units, V1 symbol mapping, canonical source provenance reconciliation, expected-calendar completeness, and authoritative publish status not proved |
| adj_factors | `PARTIAL_EVIDENCE` | `asl-shared`: 732 / 4,866,238, 2023-08-07 to 2026-08-13; VFlash raw Tushare: 35 / 3,391,611, 2024-01-02 to 2026-07-31; raw ASL: 2 / 186,974, 2026-07-08 to 2026-07-24 | No canonical VFlash adjustment dataset; factor semantic/PK/expected coverage not proved |
| trading_status | `PARTIAL_EVIDENCE` | `asl-shared`: 37 / 371,778, 2023-08-08 to 2026-08-13; VFlash raw suspension: 13 / 3,786, 2024-01-02 to 2026-04-30 | VFlash Boolean `trade_status` and suspension fields do not prove the V1 historical enum or full ST/suspension coverage |
| minute_bars_5m | `PARTIAL_EVIDENCE` | r8 curated: 40 Parquet / 270,000 footer rows, 2026-06-04 to 2026-07-30; all footer `frequency=5m`, `source=tdx_protocol`, `data_version=v1` | Timestamp left/right label, session/time-zone semantics, volume/amount units, PK, distinct symbols, and expected coverage not proved |
| turnover / float_shares | `PARTIAL_EVIDENCE` | VFlash raw Tushare daily_basic: 35 / 3,415,290, 2024-01-02 to 2026-07-31; canonical daily has `turnover_rate` but 3,216,568 nulls of 3,327,462 latest footer values | Float shares absent; canonical percentage/ratio semantic, source, and expected coverage not proved; `asl-shared` has no supplied turnover/float evidence |
| industry (primary SW membership) | `ABSENT` | No dedicated SW membership dataset in supplied `asl-shared` or approved VFlash canonical/raw scope | Optional VFlash limit-up-pool `industry` field is not a primary SW membership dataset; warehouse contents intentionally uninspected |
| index | `PARTIAL_EVIDENCE` | `asl-shared`: 4 Parquet / 5,856 footer rows, 2023-08-07 to 2026-08-13 | Index identity/set, schema, provenance, and expected coverage not proved; no approved VFlash index asset observed |

## Supporting non-V1-core evidence

| Dataset | Status | Evidence | Limitation |
|---|---|---|---|
| trading_calendar | `PARTIAL_EVIDENCE` | `asl-shared`: 5 Parquet / 1,462 footer rows, 2023-08-07 to 2027-08-07 | Calendar semantic and its relationship to all observed dataset dates not proved |
| corporate_actions | `PARTIAL_EVIDENCE` | `asl-shared`: 37 Parquet / 70,455 footer rows, 1990-03-01 to 2026-08-17 | No adjustment/reconciliation conclusion |
| price_limits | `PARTIAL_EVIDENCE` | VFlash raw Tushare: 3 Parquet / 44,279 footer rows, 2024-01-02 to 2026-07-31 | Expected universe/board-rule coverage and raw-price semantic not proved |
| limit_up_pool | `PARTIAL_EVIDENCE` | VFlash canonical maximum snapshot: 1,627 footer rows, 2026-07-13 to 2026-08-13 | Candidate base-market fact only; not a substitute for historical price-limit validation |

## Cross-root interpretation limits

- Footers establish storage observations, not cross-root equality. No daily↔5m, bars↔status, or price-limit reconciliation scan ran.
- VFlash `SCREEN_READY`, `RESEARCH_READY`, and validation labels are not V1 Publish/Acceptance labels.
- `warehouse.duckdb` was not opened. Thus `ABSENT` means absent from approved inspectable files, not absent from all possible database contents.
- This map must not be read as a `DIRECT_REUSE`, `MIGRATE_AFTER_NORMALIZATION`, `CROSSCHECK_ONLY`, or `REJECT` decision.
