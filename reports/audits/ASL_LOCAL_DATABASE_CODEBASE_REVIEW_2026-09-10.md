# ASL local database and codebase review — 2026-09-10

## Technical summary

ASL currently has a stable, pointer-bound R3 daily OHLCV authority through
**2026-09-09** and a read-only local MCP query service. A bounded BaoStock
Daily Facts acquisition for the same date has completed for all **5,208** R3
eligible SH/SZ symbols: each request has persisted immutable provider RAW and
there were zero provider or quality failures at acquisition time.

This is **not yet a formal Daily Facts publication**. The required offline
normalization, exact-key certification, preclose/pct-change reconciliation,
facts manifest, and pointer-last Daily Facts authority switch have not run.
Accordingly, `FACTS_READY=false` and `PRECLOSE_COMPLETE=false` remain correct;
ChatGPT/MCP must not receive the new 2026-09-09 facts until those gates pass.

## Review decision requested

Please audit whether the following bounded next action is safe and sufficient:

1. Run postprocess against only the persisted 2026-09-09 BaoStock RAW.
2. Run the offline certification gate against the exact 5,208 R3 keys.
3. Publish only if every blocker count is zero, using the separate Daily Facts
   manifest and an atomic pointer-last switch.
4. Verify `facts(symbol, trade_date)` through local MCP after publication.

Do not approve a broader historical backfill, R3 rewrite, price-limit work,
5m, strategy, or any readiness-flag upgrade as part of this review.

## Published R3 database baseline

| Item | Verified value |
|---|---:|
| R3 latest published trade date | 2026-09-09 |
| R3 daily published AS OF | 2026-09-09 |
| R3 manifest file count | 2,597 |
| R3 manifest SHA-256 | `91848857b69115679bcabf730265a1de1b2f1d8f466a8bd80b5da7dd01e3188e` |
| Pending physical daily files | 0 |
| Formal R3 identity universe | 5,456 SH/SZ symbols |
| Daily coverage classification | `PARTIAL` |
| Global Facts readiness | `false` |
| Global preclose completeness | `false` |

The R3 query path reads only files listed in its published manifest. The
incremental publication path has a calendar guard, non-blocking writer lock,
frozen SH/SZ lifecycle authority, evidence-before-install, post-install
manifest validation, pointer-last switching, and fail-closed orphan recovery.
No 2026-09-10 R3 update or R3 authority mutation was performed in this work.

## Daily Facts acquisition result

### Scope and provider boundary

- Scope: `FULL_ELIGIBLE_ONE_DAY`, trade date `2026-09-09`.
- Expected key set: the R3 published daily allowlist for that one date.
- Eligible key count: 5,208.
- Provider: the pinned CNEquity BaoStock bridge.
- Storage boundary: per-symbol BaoStock RAW plus an SQLite resumable ledger.
- Network execution: one macOS user LaunchAgent process; no concurrent writer.

### Terminal ledger accounting

| State | Count |
|---|---:|
| `RAW_PERSISTED` | 5,208 |
| `FETCHING` | 0 |
| `NOT_STARTED` | 0 |
| `PROVIDER_FAIL` | 0 |
| `QUALITY_FAIL` | 0 |
| Maximum attempts for any unit | 1 |
| Total attempts | 5,208 |

The first persisted run unit started at `2026-09-09T15:52:42Z`; the last
provider request was started at `2026-09-10T02:08:52Z`. The one-shot
LaunchAgent exited with code 0 after acquisition completed. Network access is
no longer required or permitted for the certification stage.

## Daily Facts contract and required certification

The intended canonical primary key is `(symbol, trade_date)`. The only facts
in scope are:

```text
preclose
pct_chg
turnover_rate       # BaoStock turn; percentage points
trade_status        # TRADING | SUSPENDED | UNKNOWN
is_st               # TRUE | FALSE | UNKNOWN
```

Each normalized record retains provider identity, provider code, raw provider
values, fetch timestamp, provider version, and schema version. Null/empty
provider fields are not defaulted to zero or false.

Certification must require all of the following before publication:

| Gate | Required result |
|---|---|
| Expected / normalized rows | 5,208 / 5,208 |
| Duplicate, missing, extra primary keys | 0 / 0 / 0 |
| Provider source errors | 0 |
| Unknown or invalid tri-state facts | 0 |
| Identity/date mismatches | 0 / 0 |
| Numeric/provenance failures | 0 / 0 |
| Unresolved preclose reconciliation | 0 |
| Unresolved pct-change reconciliation | 0 |
| Trade-status conflict against R3 bar | 0 |

For ordinary continuity, provider `preclose` must match the previous published
R3 close at the frozen display rounding tolerance. A corporate-action or
reference-price exception is acceptable only with exact-date evidence; it may
not be inferred from a year-level event. Provider `pct_chg` must be compared
with `(R3 close / provider preclose - 1) * 100`; provider values are retained
and never silently replaced by calculated values.

## Separate authority and MCP design

Daily Facts has a separate physical/quality/manifest/authority boundary from
R3 Daily. It may not reuse the R3 pointer as evidence of Facts readiness.

When and only when certification passes, the publication tool will:

1. Write the 2026-09-09 full-eligible candidate parquet without overwriting
   existing vertical-slice files.
2. Persist the facts manifest, scope plan, provenance/quality receipt, and
   R3 manifest binding.
3. Validate the installed candidate against the facts manifest.
4. Atomically switch `published-daily-facts-authority.json` last.

`LocalQuery.facts(symbol, trade_date)` and the minimal MCP `facts` tool are
implemented as read-only published-authority queries. Before the new authority
exists, a request such as `facts(002580.SZ, 2026-09-09)` fails closed with
`OUTSIDE_PUBLISHED_FACT_SCOPE`; it does not read staging or derive facts.

After a successful scoped promotion, the expected status semantics are:

```text
DAILY_FACTS_PHASE1_STATUS = FULL_ELIGIBLE_ONE_DAY_PUBLISHED
DAILY_FACTS_PHASE1_SCOPE  = 2026-09-09_FULL_ELIGIBLE
DAILY_FACTS_PUBLISHED_AS_OF = 2026-09-09
FACTS_READY = false
PRECLOSE_COMPLETE = false
```

The final two values must remain false because this is one published date, not
a complete all-history/all-market facts authority.

## Code review snapshot

Branch under review:

```text
codex/r3-incremental-hardening-daily-facts-phase1-v01
HEAD: 89dafa67c4b0352a1813604405094d96efc7c9e4
REMOTE HEAD: same commit
```

Relevant commits:

| Commit | Purpose |
|---|---|
| `ba85153` | Harden frozen R3 incremental publication and lifecycle checks |
| `26fa0b4` | Add bounded Daily Facts staging and offline certification tooling |
| `922a7b1` | Reject unbound R3 promotion candidates |
| `9121547` | Canonicalize staged facts dates for certification |
| `89dafa6` | Add published-only LocalQuery/MCP `facts` tool |

Core implementation locations:

```text
tools/run_daily_facts_phase1_full_market.py
tools/certify_daily_facts_phase1_v01.py
tools/publish_daily_facts_phase1_v01.py
src/ashare_data/daily_facts_phase1.py
src/ashare_data/local_query.py
src/ashare_data/mcp_server.py
```

## Test evidence

The latest targeted regression run before acquisition completion recorded:

- LocalQuery and Daily Facts tests: 39 passed.
- MCP tests: 51 passed.
- Broader relevant R3/Daily Facts subset: 66 passed.
- `py_compile`: passed.
- CNEquity configuration validation: passed.

The tests cover fail-closed authority validation, facts-outside-scope rejection,
MCP tool surface validation, frozen R3 lifecycle guard, writer lock, calendar
guard, and promotion recovery. They do not establish the result of the pending
5,208-row certification; that must be executed and recorded separately.

## Known limitations and explicit non-claims

- No formal 2026-09-09 Daily Facts authority exists yet.
- No 2026-09-09 Daily Facts manifest hash exists yet.
- No real MCP facts smoke test can yet be called for the full-eligible scope.
- The existing published Daily Facts authority remains vertical-slice-only.
- `FACTS_READY` and `PRECLOSE_COMPLETE` are false by design.
- `DAILY_COVERAGE_STATUS=PARTIAL` remains unchanged.
- This report does not assert independent-audit PASS.

## Audit checklist for ChatGPT Web

1. Confirm the proposed postprocess performs zero provider/network calls.
2. Confirm the expected R3 key set is read through LocalQuery's current
   published manifest, not a physical parquet glob.
3. Confirm every nonzero certification blocker prevents publication.
4. Confirm existing vertical-slice Facts remain pointer-bound and no duplicate
   `(symbol, trade_date)` key can enter the union manifest.
5. Confirm pointer-last ordering and that no R3 daily pointer/file is changed.
6. After execution, inspect the certification receipt, facts promotion receipt,
   and real MCP responses for 002580.SZ, 601888.SH, 000001.SZ, 600519.SH plus
   a verified ST sample and a non-ST sample.
