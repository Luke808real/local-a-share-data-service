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
  re-run `--stage E_delisted`, but ONLY AFTER `finish_run("failed")` has been
  successfully persisted (never speculative). Pre-run failures and failed /
  success manifest terminalization failures raise
  `E_MANIFEST_FAILURE_TERMINALIZATION_FAILED` / `E_MANIFEST_SUCCESS_TERMINALIZATION_FAILED`,
  keep `current = E_delisted`, and forbid abandon and ordinary retry; automatic
  retry is disallowed and crash / interruption never auto-abandons or
  blind-restarts. The BJ gate runs before the E
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
- F ASOF scope is centralized in one shared planner used by both the normal
  route and the reuse route. Non-NULL curated `list_date` keeps
  `effective_span(list_date, delist_date, R3_HISTORY_START, R3_DAILY_AS_OF)`;
  `list_date > R3_DAILY_AS_OF` is `expected_no_data`
  (`LIST_DATE_AFTER_ASOF`, no provider call). A curated `list_date=NULL`
  candidate NEVER defaults to the 2016-01-01 full-window obligation: it must be
  bound to the frozen Stage-B authority — the `r3-identity-receipt.json` is a
  completed `SH_SZ_MVP` identity, and ONE `fetch_instrument_basics()` call
  (only when a NULL candidate exists) recomputes the ASOF formal identity whose
  hash MUST equal the receipt's frozen `formal_identity_hash`
  (`F_FORMAL_IDENTITY_DRIFT` otherwise; `F_FORMAL_AUTHORITY_UNAVAILABLE` when
  the receipt is missing/not completed). Per candidate: resolved bounded span
  from `stock_basic.list_date <= ASOF`; `PRELISTING_CONFIRMED` when
  `list_date > ASOF`; `OUTSIDE_FORMAL_ASOF_IDENTITY` when absent from the
  verified identity; `F_NULL_LIST_DATE_UNRESOLVED` when in the identity but
  list_date is still NULL (never `effective_span(None, ...)`). `roster_on(day)`
  is never a production F membership gate.
  F manifest run is always terminalized: `finish_run("success")` only after TDX
  PASS + compact success, rows = SUM of the run's final successful daily_bars
  manifest batches (never `rows_written_last`); run-scoped terminal failures
  (`F1_STRICT_DECREASE`, `F1_FAILED_AFTER`, compact failure) abandon the F marker
  ONLY after confirmed `finish_run("failed")` (replacement =
  `F_daily_operator_retry`); pre-run failures (`NO_INSTRUMENTS`) never abandon;
  `F_MANIFEST_FAILURE_TERMINALIZATION_FAILED` / `F_MANIFEST_SUCCESS_TERMINALIZATION_FAILED`
  keep `current = F_daily` and forbid abandon/ordinary retry; crash never
  auto-abandons or blind-restarts. Operator retry is explicit with a new run_id
  and old failed evidence preserved. F compact merges staged rows into existing
  curated daily rows, preserving Stage-E recovered delisted bars.
- Explicit failed-run recovery (`--stage F_daily --f-reuse-run-id <RUN_ID>`) is
  a narrow operator capability on a brand-new run; normal `--stage F_daily` is
  unchanged and the flag is rejected elsewhere. Before the new run: persisted
  state must be pending/current=null/through E_delisted with an
  `F_daily_operator_retry` abandon (checked before enter); the source run must
  be a failed `r3_daily_bars` run whose error is `F1_STRICT_DECREASE` or
  `F1_FAILED_AFTER`. Plan parity is a **safe contraction** against the ASOF
  scope: every current required symbol/window must exist exactly in the source;
  source SUCCESS scope must be a subset of the current plan; source-only extras
  are allowed only for current `expected_no_data` symbols confined to final
  FAILED batches with no staging (recorded `REUSE_DROPPED_EXPECTED_NO_DATA`);
  anything else `F_REUSE_PLAN_MISMATCH`. Every final-success batch must have
  a non-empty staging file and no failed batch may (`F_REUSE_STAGING_INCOMPLETE`
  otherwise). The source run is never mutated. Successful source batches are
  copied to the new run's staging with zero provider calls and recorded in the
  ledger as `REUSED_SUCCESS_BATCH`; the failed scope is expanded to one
  batch-per-symbol `f-recovery-single-<hash>` TDX singletons using the current
  effective span, ≤3 attempts, strict-decrease on the singleton failed set,
  `failover_enabled=false`. The singleton recovery scope is
  `source failed symbols ∩ CURRENT_REQUIRED_SCOPE` — a failed symbol proven
  `expected_no_data` by the ASOF authority never gets a TDX fetch. Compact only
  when reused + singletons all succeed
  and blocking incomplete batches == 0 (`F_INCOMPLETE_BEFORE_COMPACT` otherwise,
  treated as a run-scoped terminal failure through `_fail_f_run`); success
  totals = SUM of the new run's final successful daily_bars batches. Failed
  recovery runs end terminal-failed with safe abandon so an explicit operator
  retry (new run id) is available; chained recovery may point at the latest
  failed F recovery run so only the still-missing singleton scope is refetched.
  This route never calls EastMoney or Sina.
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

## Stage G survivorship gate (V08 formal authority)

```text
r3_shsz_verified =
  formal_identity_authority_complete     # Stage-B receipt: SH_SZ_MVP + identity_complete
  AND formal_recovery_complete           # formal delisted set/hash == E targets/recovered, unresolved==0
  AND known_coverage_complete            # upstream known_coverage_complete
  AND all upstream hard blockers == 0    # missing_bars/unknown_overlap/terminal_mismatch/
                                         # recent_quarantined/formal_unresolved/
                                         # missing_instrument/invalid_delist_date
```

The pinned upstream `delisted_coverage_report` is invoked once, never modified,
and its verdict (e.g., `verified=false`) is preserved verbatim in
`upstream_report`. The legacy Sina issued-code discovery
(`legacy_discovery_complete` / `legacy_pending_probe`, e.g., 30582) is a
`DEFERRED_NON_AUTHORITY` observation and does NOT gate R3 SH/SZ;
`terminal_nonprinting` is observation-only and `formal_no_overlap` is allowed.
On success (`r3_shsz_verified=true`) the report is written to
`r3-delisted-coverage.json` and G completes; any expected R3Error after
`enter("G_coverage")` abandons append-only (replacement
`G_coverage_operator_retry`; abandon failure -> `G_FAILURE_TERMINALIZATION_FAILED`
with current stays G_coverage/running). No automatic G retry.

Wedged `running/current=G_coverage` (completed through F_daily) is recovered by
`--recover-interrupted-control-plane` with an append-only abandon (replacement
`G_coverage_operator_retry`); no market-data write, no G re-verification.

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
