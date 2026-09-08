# ASL Market Facts Next Phase V01

## Status and scope

This is an implementation plan, not a publication authority and not an audit
PASS. It starts from the pointer-bound R3 Daily RAW baseline at 2026-09-07.
R3 retains TDX daily OHLCV as its primary RAW source and remains unchanged.

In scope for Daily Facts Phase 1 only:

- `preclose`
- `pct_chg`
- `turnover_rate`
- `trade_status`
- `is_st`

Out of scope: price-limit facts, limit events, 5m publication, share capital,
fund flow, concepts, Strategy, B1/B2, Watchlist, and Forward.

## Baseline

| Capability | State |
| --- | --- |
| R3 Daily RAW | READY — 2,595 published files through 2026-09-07; `PARTIAL` coverage |
| Formal identity | READY — 5,456 SH/SZ symbols |
| R3 publication authority | READY — manifest hash `97aa4d16c82abfa144ab6c2d8fd2d9cde6dadb1e82e78889136fa5d950e56c7f` |
| LocalQuery and MCP | READY — read-only R3 allowlist |
| Preclose / Daily Facts / price-limit / turnover / ST / 5m | NOT READY |

Physical availability is not publication. `UNKNOWN`, `SOURCE_ERROR`, and
`PARTIAL` are never converted into PASS, false, zero, or a ready fact.

## Phase 1 data contract

The implementation adds a separate `daily_facts` physical/certification/
publication boundary. It must not add columns to R3 `daily_bars` or alter the
R3 daily pointer. A future Daily Facts pointer must bind its own manifest,
quality receipt, provenance receipt, and immutable fact files before
LocalQuery/MCP reads them.

The normalized fact primary key is `(symbol, trade_date)`. A candidate row
contains the following semantic fields plus immutable provenance fields:

| Field | Phase 1 contract |
| --- | --- |
| `preclose` | BaoStock display preclose, nullable only with a non-PASS row status |
| `pct_chg` | BaoStock percent change, reconciled against published TDX close and normalized preclose |
| `turnover_rate` | BaoStock `turn` in percentage points (`3.25` means `3.25%`); null is not zero |
| `trade_status` | explicit enum `TRADING` / `SUSPENDED`; anything else is `UNKNOWN` |
| `is_st` | only an explicit provider value maps to true or false; absent/unrecognized is `UNKNOWN` |
| provenance | provider, provider fields, source date, fetch time, raw values, normalized values, source/data/adapter version, and quality status |

BaoStock is an evidence provider, not a replacement for TDX OHLCV. Provider
responses must have the expected schema, code/date identity, a unique primary
key, typed values, and a recorded source error when unavailable.

## Preclose reconciliation

For ordinary consecutive `TRADING` dates, BaoStock `preclose` must equal the
previous published TDX close at exchange display precision. The comparison is
evidence only: it must not overwrite either source. A missing predecessor,
unexplained mismatch, duplicate provider row, invalid numeric value, provider
identity/date mismatch, or provider failure is `UNKNOWN`/`UNRESOLVED` and
blocks publication for that fact scope.

Corporate-action and other reference-price exceptions require explicit,
versioned evidence before a mismatch can be classified. They are not silently
treated as continuity PASS.

## Vertical slice before any backfill

The initial implementation must use small, separately recorded windows for:

- `002580.SZ` (圣阳股份), including 2026-08-21 through 2026-08-24;
- `601888.SH` (中国中免);
- `000001.SZ` (平安银行);
- `600519.SH` (贵州茅台);
- one identity-verified ST sample;
- one identity-verified historical-suspension sample; and
- one corporate-action sample selected from the local corporate-actions data.

The selection evidence must be retained. A Chinese name is never used as a
symbol key without an `instrument` identity check; in particular `002165.SZ`
is 红宝丽, not 圣阳股份.

The vertical slice order is:

```
BaoStock provider RAW -> normalization -> R3 continuity/reconciliation
-> quality + provenance receipts -> Daily Facts manifest/pointer
-> LocalQuery -> MCP
```

Until every row in a proposed slice has a positive, scoped quality decision,
the data stays physical/staging only and MCP returns `FACT_NOT_READY` for all
Phase 1 facts. Passing a vertical slice authorizes a separately bounded
backfill decision; it does not imply `PRECLOSE_COMPLETE=true` or
`FACTS_READY=true`.

## Required tests and gates

Tests must cover provider schema and typed errors, null handling,
`SOURCE_ERROR`, normal preclose continuity, missing predecessor, suspension,
provider mismatch, valid/null/zero/unavailable turnover, explicit ST/trading
status mapping, and denial of unpublished facts through LocalQuery/MCP.

Publication requires no duplicate fact PKs, valid types and domains, complete
provenance, stable manifest binding, and zero unresolved/source-error rows in
the exact promoted scope. An authority switch writes all evidence first and
atomically switches its pointer last.

## Deferred provider work

CNEquity configuration names BaoStock, EastMoney, and Sina, but this root has
no production Daily Facts adapter or published facts authority. EastMoney/
THS research for limit pools/reasons belongs to a later `limit_events` phase.
The separate legacy 5m lake is crosscheck-only and is not part of this plan.
