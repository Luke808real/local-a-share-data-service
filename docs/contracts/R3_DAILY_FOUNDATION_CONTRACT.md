# R3 DAILY FOUNDATION CONTRACT

**SPEC_VERSION:** `V1.0 FROZEN`
**PLAN:** `docs/plans/R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN_V07.md` (V07.2)
**PLAN_SHA (audited):** `3ab1f184edeea1d0e408c45df4a706248b6558d0`
**V08_SCOPE_DECISION_SHA (audited):** `00085fed36f50312b6a5475dc26f0c5e347c6768`
**STATUS:** frozen contract for the R3 DAILY FOUNDATION phase

`V08_SCOPE_DECISION_SHA` is the independently reviewed authority point for the
current SH/SZ MVP scope decision (BJ = `DEFERRED_EXTENSION`). Every executing
HEAD must contain both `PLAN_SHA` and `V08_SCOPE_DECISION_SHA` as ancestors.

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

- **CURRENT R3 MVP SCOPE (V08, user/architect decision): SH/SZ.** BJ is
  `DEFERRED_EXTENSION` — not deleted, not fabricated, not claimed complete.
  Rationale: quick reliable local data for ChatGPT Web; SH/SZ identity +
  quarterly audit already have complete successful evidence; EastMoney BJ
  current (clist) failed twice consecutively at provider level; no new akshare
  dependency for BJ. **FULL ALL-A DAILY_READY remains FALSE**; the MVP only
  forms the intermediate fact `R3_SHSZ_SCOPE = ACTIVE`.
- Stage B is V07.4 upstream-aligned identity completion — NO Sina issued-code
  sweep, and NO 2,580-date full daily roster closure.
  - SH/SZ formal historical identity authority: Baostock `query_stock_basic`
    (`fetch_instrument_basics`), SH/SZ stock+CDR; Stage-A formal drift
    (`missing_n == extra_n == date_mismatch_n == 0`) fails closed.
  - Roster is a QUARTERLY last-trading-day AUDIT / CROSSCHECK only (~43
    samples, 2016 Q1 .. 2026 Q3, deterministic from the trading-day list).
    Hard gates: `successful_sample_n == sample_dates_n`, `failed_sample_n == 0`,
    `roster_extra_vs_formal_n == 0`
    (`QUARTERLY_ROSTER_AUTHORITY_CONFLICT`), `roster_span_conflict_n == 0`
    (`ROSTER_SPAN_CONFLICT`); the superseded V07.3 union crosscheck must also be
    clean (`AUTHORITY_CONFLICT` otherwise). `formal_not_seen_in_quarterly_sample_n`
    is OBSERVATION ONLY and never blocks Stage B.
  - Single shared Baostock session; no concurrent connections; max 3 in-place
    retries per sampled date with bounded backoff; exhausted sample stops
    fail-closed (`ROSTER_DATE_RETRY_EXHAUSTED`). Progress checkpointed
    atomically per sample in `r3-quarterly-roster-audit-progress-v074.json`;
    the V07.3 closure checkpoint is preserved as superseded-execution evidence
    (bytes never modified, never reused as V07.4 progress) and the transition is
    recorded in `r3-b-v074-transition-receipt.json`.
  - `shsz_identity_complete = true` only when all SH/SZ hard gates pass; receipt
    records `scope = SH_SZ_MVP`, `shsz_identity_authority =
    Baostock_stock_basic`, `bj_scope = DEFERRED_EXTENSION`, `bj_current_status =
    NOT_EVALUATED`, `bj_historical_status = UNKNOWN_CARRIED`. Stage B does NOT
    call EastMoney clist and never fabricates BJ current membership/hash as 0,
    empty-universe, or PASS.
- Stage C (C_merge) and Stage C2 (C2_enrich) under SH/SZ MVP are bounded
  DEFERRED stages: NO network/provider call, no instruments re-run/mutation, no
  compact, no market dataset write; each emits minimal evidence
  (`scope = SH_SZ_MVP`, `status = DEFERRED`,
  `reason = BJ_EXTENSION_OUTSIDE_CURRENT_MVP`) and completes so the frozen stage
  order (A, B, C, C2, D, ...) stays intact and `D_calendar` can proceed later.
  No new stage or BJ subsystem.
- E SCOPE = SH/SZ MVP: delisted recovery targets = Baostock formal
  `.SH`/`.SZ` only (via `fetch_delisted_bars`). A BJ delisted target under this
  scope fails closed `E_UNEXPECTED_BJ_TARGET_IN_SHSZ_MVP`; EastMoney
  (`em_daily_tristate`) is never called in the MVP; the E receipt records
  `scope=SH_SZ_MVP`, `bj_scope=DEFERRED_EXTENSION`, `bj_execution=NOT_RUN`.
  SH/SZ recovery is ONE bulk `fetch_delisted_bars(sh_sz, …)` invocation; retry,
  relogin, batching and pacing authority is the pinned CNEquity
  `fetch_per_symbol` (service_provider_invocations = 1, outer attempt = 1,
  adapter_retry_owner = cnequity.fetch_per_symbol); the service never nests a
  second per-symbol retry, and a bulk exception terminalizes every running
  batch and fails closed before compact.
  E manifest run is always terminalized (`finish_run("success")` only after
  bulk + unresolved==0 + incomplete==0 + compact success; `finish_run("failed")`
  before every terminal-failure raise; receipt `manifest_run_status`). On an
  in-process explicit terminal E failure the E marker is append-only abandoned
  (`replacement = E_delisted_operator_retry`) so an operator can explicitly
  re-run `--stage E_delisted`; automatic retry is disallowed and crash /
  interruption never auto-abandons or blind-restarts (`current` stays
  E_delisted, next run fails closed on entrance). The BJ gate runs before the E
  manifest run is created.
  `live_missing`/current active names are excluded; `never_issued` is not a
  completion gate.
- No symbol is dropped by board, ST, liquidity, or strategy use.

## Daily fetch

- Generic `step_daily_bars`, `cne init/run/retry/audit/query/mcp/demo`, and
  automatic EastMoney gap-fill are never executed by R3. `failover_enabled=false`.
- SH/SZ: `fetch_daily_bars_parallel(config, symbols=[], start=R3_HISTORY_START,
  end=R3_DAILY_AS_OF, run_id, batch_specs=deterministic chunks)`, controller-owned
  retries with strictly decreasing failed scope.
- F SCOPE = SH/SZ MVP: F plans only `spans_shsz` and executes only the TDX route
  (`_tdx_route`). The EastMoney BJ primary route (`f2_em_primary` /
  `em_daily_tristate`) and Sina are NOT executed under V08; they are retained as
  future BJ-extension design. F receipt records `scope=SH_SZ_MVP`,
  `bj_scope=DEFERRED_EXTENSION`, `bj_execution=NOT_RUN`, and
  `f2_em_primary = {status: DEFERRED, reason: BJ_EXTENSION_OUTSIDE_CURRENT_MVP}`
  — never a symbols=0 success.
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
