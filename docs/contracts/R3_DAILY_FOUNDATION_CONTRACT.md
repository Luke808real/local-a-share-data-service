# R3 DAILY FOUNDATION CONTRACT

**SPEC_VERSION:** `V1.0 FROZEN`
**PLAN:** `docs/plans/R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN.md`
**PLAN_SHA (audited):** `d13e2ecefbb66250b73aca4312dc8706a4d2b7a3`
**STATUS:** frozen contract for the R3 DAILY FOUNDATION phase

## Window and provenance

- `R3_HISTORY_START = 2016-01-01`, `R3_DAILY_AS_OF = 2026-08-17`, inclusive.
- Same backup `daily_bars` RAW/unadjusted prices. Volume is shares, amount is CNY
  when present, per the pinned `v2` schema; `trade_date: date`; canonical symbol
  `{code}.{SH|SZ|BJ}`; UTC `fetched_at`.
- Sole runtime authority: CNEquity `0.7.2` at Git SHA
  `a18ee0484dfb0801650175471724def3228b8a17`, CPython 3.12, committed `uv.lock`
  (`5f233fa9434624391c06e56a4596edfd52c1ec596d66688753b78f424dd571ac`), config
  (`fac5abd136cb2ae00c07d7ca408eb1d47eed69c26c3547a0547ef9d214063fb5`).
- Market dates are never taken from wall clock. The security-master observation
  (`R3_UNIVERSE_OBSERVED_AT`, reference date, catalog/metadata hashes) is
  recorded separately and is not a historical as-of snapshot.

## Scope

R3 builds only these Parquet datasets in the clean root:

```text
curated/instruments
curated/trading_calendar
curated/daily_bars
```

plus `meta/asl/r3/*` receipts. No `derived/`, no raw payload, no published
state. `LATEST_GOOD_AS_OF` stays `NOT_PUBLISHED`.

## Universe

- Active SH/SZ/BJ stock+CDR must all be present; BJ requires verified nonblank
  `name` and `list_date` (`BLOCKED_ALL_A_METADATA` otherwise).
- Discovery (`discover_delisted`) must finish with `complete=true/failed=0/
  remaining=0`.
- Delisted recovery targets = Baostock formal delisted ∪ discovery
  classified-delisted; `live_missing`/`never_issued`/active names are excluded.
  Post-C2 `live_missing` BJ rows are ACTIVE members in the daily fetch set.
- No symbol is dropped by board, ST, liquidity, or strategy use.

## Daily fetch

- Generic `step_daily_bars`, `cne init/run/retry/audit/query/mcp/demo`, and
  automatic EastMoney gap-fill are never executed by R3. `failover_enabled=false`.
- SH/SZ: `fetch_daily_bars_parallel(config, symbols=[], start=R3_HISTORY_START,
  end=R3_DAILY_AS_OF, run_id, batch_specs=deterministic chunks)`, controller-owned
  retries with strictly decreasing failed scope.
- Non-TDX (BJ): partitioned by identical effective span
  `[max(list_date, start), min(delist_date, as_of)]`; `fetch_bars_via_sina` per
  span with unique deterministic `batch_prefix` per span+attempt; per-symbol
  EastMoney `fetch_daily_bars` fallback for date gaps only (never co-existing
  same PK with Sina rows), with unique controller batch id.
- Coverage requires at least one positive-volume in-window row. Zero-volume
  placeholder rows are not coverage evidence.

## Quality classification

```text
EXPECTED
OBSERVED
EXPLAINED_MISSING        (only structural/pre-list/effective-span exclusions)
PENDING_R4_STATUS_EXPLANATION  (interior/bounded gap with an observed row)
UNEXPLAINED_MISSING      (zero in-window rows; blocks DAILY_READY)
```

Missing sessions are never asserted normal without a status dataset; R4 owns
status/turnover/price-limit facts. Sina null amount is
`EXPLAINED_MISSING_SOURCE_FIELD` (verified source limitation), split by
exchange/ownership/date/rows/hash; any non-Sina null amount blocks the gate.
Sina volume is cross-checked against EastMoney kline on a deterministic sample
(>=20 BJ symbols, >=20 overlapping traded dates) with close within 1 bp and
volume ratio in `[0.99, 1.01]`.

## Receipts and lineage

- Service ledger (jsonl) plus manifest controller batches
  (`blocks_compaction=True`) for every scope without an upstream worker batch.
- Compact only after zero incomplete scopes; compact input inventory/hash and a
  curated PK/provenance post-proof are recorded. Staging is never deleted; a
  retry writes a new staging file.
- `DAILY_READY` means all gates above pass; it is author status until an
  independent review of the exact pushed R3 commit records `AUDIT_PASS`.
