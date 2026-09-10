# ASL Daily Facts Phase 1 — Continuation Handoff

## 1. Exact starting point

- ROOT: `/Users/luke808/ASL`
- DATA_ROOT: `/Users/luke808/AI/local-a-share-data-service-data`
- BRANCH: `codex/r3-incremental-hardening-daily-facts-phase1-v01`
- HEAD / pushed remote: `b7ff27a5ef77b33847e764fec5974985c5e12053`
- FOUNDATION: CNEquity; TDX R3 Daily remains the primary OHLCV authority.
- Current task result is author execution status, not an independent audit.

Before any further work, read in order:

1. `docs/MASTER_SPEC.md`
2. `docs/PROJECT_STATE.md`
3. `docs/ROADMAP.md`
4. this handoff and the newly assigned bounded task contract.

## 2. Published R3 Daily baseline

R3 Daily is unchanged by the Daily Facts work.

- `LATEST_PUBLISHED_TRADE_DATE`: `2026-09-09`
- `DAILY_PUBLISHED_AS_OF`: `2026-09-09`
- R3 published manifest file count: `2597`
- R3 published manifest hash:
  `91848857b69115679bcabf730265a1de1b2f1d8f466a8bd80b5da7dd01e3188e`
- Formal SH/SZ identity count: `5456`
- Daily coverage remains `PARTIAL`; no full-history claim is authorized.

The R3 pointer and its canonical Daily Parquet files must not be rewritten by
future Daily Facts work.

## 3. What is now published: a bounded Daily Facts authority

Daily Facts is independent from R3 Daily. It has its own manifest, quality
receipt, promotion receipt, and pointer-last authority:

- pointer:
  `meta/asl/daily_facts/published-daily-facts-authority.json`
- scope: `2026-09-09_FULL_ELIGIBLE`
- status: `FULL_ELIGIBLE_ONE_DAY_PUBLISHED`
- published Facts manifest hash:
  `9b4f474ebcb0db97d9dbfdb824eb6b7e17f017a6ac8957ed3d6a8fb5e3ca21ac`
- scope row count: `5208`
- publication plan / receipt:
  `staging/daily_facts_phase1_20260909_v01/promotion_plan.json`
  and `staging/daily_facts_phase1_20260909_v01/promotion_receipt.json`

This authority contains exactly the Phase 1 fields:

- `preclose`
- `pct_chg`
- `turnover_rate` (canonical unit: `PERCENT`)
- `trade_status` (`TRADING`, `SUSPENDED`, or `UNKNOWN`)
- `is_st` (`TRUE`, `FALSE`, or `UNKNOWN`)

It does **not** make Daily Facts globally ready. These remain mandatory:

```text
FACTS_READY = false
PRECLOSE_COMPLETE = false
DAILY_FACTS scope = 2026-09-09 only
```

Any fact key outside the independent manifest must fail closed with
`OUTSIDE_PUBLISHED_FACT_SCOPE`; physical/staging files are never query
authority.

## 4. Certified 2026-09-09 evidence and gates

The frozen full-eligible one-day run is:

```text
run: daily_facts_phase1_20260909_v01
eligible / terminal RAW / normalized: 5208 / 5208 / 5208
provider network requests during certification/publication: 0
```

Final certification accounting:

```text
NORMAL_CONTINUITY_N = 5182
REFERENCE_PRICE_EXCEPTION_N = 16
SUSPENDED_N = 10

DUPLICATE_N = 0
MISSING_PK_N = 0
EXTRA_PK_N = 0
SOURCE_ERROR_N = 0
UNKNOWN_N = 0
IDENTITY_MISMATCH_N = 0
DATE_MISMATCH_N = 0
PRECLOSE_UNRESOLVED_N = 0
PCT_CHG_UNRESOLVED_N = 0
TRADE_STATUS_CONFLICT_N = 0
IS_ST_UNKNOWN_N = 0
INVALID_NUMERIC_N = 0
INVALID_DOMAIN_N = 0
PROVENANCE_FAILURE_N = 0
```

`NULL_NUMERIC_N = 10` is informational and corresponds to the certified
`SUSPENDED` rows: BaoStock `tradestatus=0` plus R3 same-date zero-volume and
zero-amount carry-forward bars. It is not an unresolved or publication blocker.

## 5. Exact-date reference-price exception contract

Sixteen 2026-09-09 preclose deviations are now resolved strictly by
hash-verified official CNInfo implementation-announcement PDFs. The contract
is intentionally narrow:

```text
ordinary day: BaoStock preclose ~= prior published R3 close
cash-dividend ex-date: BaoStock preclose ~= prior R3 close - official effective adjustment
```

Only exact `(symbol, ex_dividend_date)` evidence is accepted. The evidence
loader fails closed on malformed schema, duplicate key, unsupported formula,
wrong date, non-official source URL, or PDF SHA-256 mismatch. A corporate event
date alone is never enough.

The exceptional-symbol set is:

```text
001400.SZ  002073.SZ  002315.SZ  002322.SZ
002441.SZ  002833.SZ  002841.SZ  300196.SZ
300622.SZ  301151.SZ  600114.SH  603992.SH
603993.SH  605377.SH  688128.SH  688271.SH
```

For `300196.SZ`, the official effective adjustment is `0.1974617`, not the
nominal `0.20`; this treasury-share-adjusted case is a regression fixture.

Persistent evidence locations:

- official PDFs and source receipt:
  `raw/official_disclosures/daily_facts_phase1_20260909_v01/cninfo/`
- bound evidence document:
  `staging/daily_facts_phase1_20260909_v01/reference_price_evidence.json`
- machine-readable 16-row reconciliation table:
  `staging/daily_facts_phase1_20260909_v01/reference_price_exception_audit.json`
- certification receipt:
  `staging/daily_facts_phase1_20260909_v01/certification_receipt.json`

The evidence fetcher is intentionally bounded to those sixteen symbols:
`tools/fetch_daily_facts_reference_price_evidence_v01.py`.

## 6. Local query and MCP state

The local read-only MCP LaunchAgent is active:

```text
label: io.asl.market-data-mcp
endpoint: http://127.0.0.1:8766/mcp
data root: /Users/luke808/AI/local-a-share-data-service-data
```

Available tools: `status`, `instrument`, `bars`, `latest`, `facts`.

Local JSON-RPC was verified after publication for:

```text
facts(002580.SZ, 2026-09-09)
facts(601888.SH, 2026-09-09)
facts(002315.SZ, 2026-09-09)
facts(300196.SZ, 2026-09-09)
facts(688271.SH, 2026-09-09)
```

The three exception samples return
`preclose_quality=PASS_REFERENCE_PRICE_EXCEPTION`, official provenance, and
the published Facts manifest hash. The MCP is local-only/read-only; tunnel or
ChatGPT Web connectivity is a separate operational concern and must be tested
from the user-facing client after any tunnel/session restart.

Identity guardrail remains in effect:

```text
002580.SZ = 圣阳股份
002165.SZ = 红宝丽
```

Always resolve Chinese name to `instrument` before a stock-specific query.

## 7. Implementation and regression coverage

Relevant implementation:

- `src/ashare_data/reference_price_evidence.py`
- `src/ashare_data/daily_facts_phase1.py`
- `tools/certify_daily_facts_phase1_v01.py`
- `tools/publish_daily_facts_phase1_v01.py`
- `tools/build_daily_facts_reference_price_evidence_v01.py`
- `tools/audit_daily_facts_reference_price_v01.py`

Publication writer note: the first 2026-09-09 candidate partition exposed a
real serialization defect (`pct_chg` and `turnover_rate` inferred as JSON
strings). Its pointer was immediately restored to the prior vertical-slice
authority, the bad candidate was retained under staging audit artifacts, and
the writer was fixed to fail closed/normalize canonical numerics before the
final pointer-last publication. The final published parquet has `DOUBLE` types
for `preclose`, `pct_chg`, and `turnover_rate`.

Last relevant validation at `b7ff27a`:

```text
.venv Daily Facts + LocalQuery subset: 53 passed
.venv-mcp MCP suite: 51 passed
py_compile: passed
```

## 8. Strict continuation boundary

Do not automatically start any of the following:

- 2026-08-18..2026-09-09 Daily Facts historical backfill
- new provider acquisition or re-fetch of the frozen 2026-09-09 RAW
- R3 authority rewrite
- price-limit engine, 5m, fund flow, industry/concept, scanner
- R4A9, Strategy, B1/B2, Watchlist, Forward
- changing `FACTS_READY` or `PRECLOSE_COMPLETE` to true

The next task must explicitly authorize a bounded scope. A suitable next task
is either:

1. a new exact-date Daily Facts incremental run for the next published R3
   trading day, preserving independent Facts authority and all gates; or
2. a separately planned, resumable bounded historical Facts backfill with
   independent certification before any global readiness decision.

In either case, retain the invariant:

```text
UNKNOWN != PASS
PARTIAL != PASS
UNVERIFIED != READY
physical availability != published authority
```

## 9. Repository hygiene

The worktree intentionally still contains unrelated untracked historical
handoff/spec files and `tmp/`. Do not add, delete, or bulk-clean them as part
of a Daily Facts task. Inspect and stage only files within the newly authorized
scope.
