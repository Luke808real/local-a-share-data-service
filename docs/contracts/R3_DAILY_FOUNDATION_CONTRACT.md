# R3 DAILY FOUNDATION CONTRACT

**SPEC_VERSION:** `V1.0 FROZEN`
**PLAN:** `docs/plans/R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN_V07.md` (V07.2)
**PLAN_SHA (audited):** `3ab1f184edeea1d0e408c45df4a706248b6558d0`
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
- Stage B is V07.2 identity completion — NO Sina issued-code sweep.
  - SH/SZ formal historical identity authority: Baostock `stock_basic`.
  - SH/SZ closure evidence: Baostock `roster_on` with a receipt
    (`expected/success/failed_dates_n`, `union_symbol_n/hash`,
    `stock_basic_vs_roster_diff`, `unresolved_n`); `failed_dates_n > 0 =>
    NOT CLOSED` -> stage fails closed.
  - BJ current: EastMoney clist (f12/f13/f14/f26).
  - BJ historical: `BJ_HISTORICAL_AUTHORITY = UNPROVABLE_BOUNDED_RESEARCH`;
    `HISTORICAL_DELISTED_BJ = UNKNOWN_CARRIED`.
- Delisted recovery targets: SH/SZ = Baostock formal + roster-closed delisted;
  BJ = any authority-proven target only (none today). `live_missing`/current
  active names are excluded. `never_issued` classification is not a completion
  gate (MASTER_SPEC requires delisted presence/resolvability, not enumeration
  of non-existent codes).
- No symbol is dropped by board, ST, liquidity, or strategy use.

## Daily fetch

- Generic `step_daily_bars`, `cne init/run/retry/audit/query/mcp/demo`, and
  automatic EastMoney gap-fill are never executed by R3. `failover_enabled=false`.
- SH/SZ: `fetch_daily_bars_parallel(config, symbols=[], start=R3_HISTORY_START,
  end=R3_DAILY_AS_OF, run_id, batch_specs=deterministic chunks)`, controller-owned
  retries with strictly decreasing failed scope.
- Non-TDX (BJ): partitioned by identical effective span
  `[max(list_date, start), min(delist_date, as_of)]`; EastMoney is PRIMARY
  through the service-owned tri-state wrapper with unique controller batch ids
  and exact retry lineage. Sina is OPTIONAL crosscheck only and never a
  completion/retry/DAILY_READY gate.
- Coverage requires at least one positive-volume in-window row. Zero-volume
  placeholder rows are not coverage evidence.

## Provider vs coverage enum separation

```text
provider wrapper (EastMoney BJ):
  EXISTS / NOT_EXISTS / SOURCE_ERROR
  known BJ empty     -> SOURCE_ERROR (EMPTY_KNOWN_SYMBOL)
  known BJ invalid   -> SOURCE_ERROR (INVALID_KNOWN_SYMBOL_RESPONSE)
  known BJ NEVER     -> NOT_EXISTS

coverage classifier (after exact retries/fallback/reconciliation only):
  OBSERVED / EXPLAINED_MISSING / UNEXPLAINED_MISSING /
  PENDING_R4_STATUS_EXPLANATION
```

The two enums are never mixed; `UNEXPLAINED_MISSING` is produced only by the
classifier.

## Daily-close gate (V07.2)

```text
if BJ_HISTORICAL_AUTHORITY != PROVEN or BJ_HISTORICAL_UNRESOLVED_N != 0:
    DAILY_READY = FALSE
    R3_EXIT      = BLOCKED_BJ_HISTORICAL_IDENTITY
    R4_EXECUTION = FORBIDDEN
```

`UNKNOWN_CARRIED` / null unresolved is never treated as 0. Author DAILY_READY is
not written solely because SH/SZ + current BJ daily are complete.

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
