# ASL CNEquity 0.7.2 -> 0.8.0 side-by-side compatibility audit

## 0. Scope and method

Side-by-side audit. Production `.venv` was not modified; the audit ran in an
isolated `.venv-cne080`. No lake write, no compaction, no migration, no
publication. The only provider traffic issued was read-only probing used to
establish whether the incumbent version can still reach its sources.

```text
CURRENT_ASL_HEAD          = 2342aba990e8bd506f0ae0908278962ad903117c
BRANCH                    = codex/r3-incremental-hardening-daily-facts-phase1-v01
PRODUCTION_VENV           = /Users/luke808/ASL/.venv          (unchanged)
AUDIT_VENV                = /Users/luke808/ASL/.venv-cne080   (new, isolated)
PRODUCTION_DATA_MUTATIONS = NONE
```

## 1. The two runtimes

| | 0.7.2 (production) | 0.8.0 (audit) |
|---|---|---|
| version | `0.7.2` | `0.8.0` |
| commit | `a18ee0484dfb0801650175471724def3228b8a17` | `d453853da766b3ba3e44489c0fb6e0089243fa25` |
| tag | `v0.7.2^{}` | `v0.8.0^{}` (identical to `refs/heads/main`) |
| install | `.venv/lib/python3.12/site-packages/cnequity` | `.venv-cne080/lib/python3.12/site-packages/cnequity` |
| python | 3.12.13 | 3.12.13 |
| polars | 1.43.2 | 1.44.2 |
| httpx | 0.28.1 | 0.28.1 |
| socksio | 1.0.0 | 1.0.0 (installed for the audit; ASL needs it for the SOCKS proxy) |

The v0.8.0 tag is an annotated tag whose dereferenced commit equals
`refs/heads/main`, so the exact pin is unambiguous. `direct_url.json` in the
audit venv records `requested_revision = commit_id =
d453853da766b3ba3e44489c0fb6e0089243fa25`. The 0.7.2 tag dereferences to
`a18ee048...`, matching ASL's existing `pyproject.toml` pin. No floating
`main` was installed.

The range spans 499 commits and 412 changed files.

## 2. Dataset registry diff

```text
0.7.2 registered datasets = 42
0.8.0 registered datasets = 42
added   = none
removed = none
```

No dataset was added or retired, so nothing ASL consumes disappears. What
0.8.0 adds is **contract metadata on every one of the 42 entries**:

```text
contract_level                 stable
compatibility                  additive          (all 42)
schema_version                 1
availability_col               e.g. trade_date, ex_date, announce_date
session_scope                  session
unit_contract                  declared per dataset (or "canonical")
pit_grade                      none | partial | strict
pit_quality                    strict | snapshot_only | reconstructed
negative_evidence_ttl_days     7
reconciliation_lookback_mode   calendar | trading_day
reconciliation_lookback_days   5 / 30
```

0.8.0 also ships a machine-readable declaration at `contracts/v0.8.0.json`
(163 KB) carrying every dataset's column dtypes, compatibility class and
metadata. That file is directly usable as an upgrade gate: a future version
bump can be diffed against it instead of re-reading source.

## 3. Schema diffs

| Dataset | 0.7.2 -> 0.8.0 | Class |
|---|---|---|
| `trading_status` | **added `risk_warning` (Boolean, nullable); `status` vocabulary narrowed** | behavioural + additive column |
| `daily_bars` | unchanged | none |
| `instruments` | unchanged | none |
| `corporate_actions` | unchanged | none |
| `share_structure` | unchanged | none |
| `valuation_metrics` | unchanged | none |
| `trading_calendar` | unchanged | none |

`DATASET_DATA_VERSION` is unchanged in both (`{'daily_bars': 'v2'}`).

```text
# 0.7.2
TRADING_STATUS_SCHEMA = {symbol, trade_date, is_trading, status, source, data_version, fetched_at}
#   status encoded two orthogonal facts: normal | st | *st | suspended

# 0.8.0
TRADING_STATUS_SCHEMA = {symbol, trade_date, is_trading, status, risk_warning, source, data_version, fetched_at}
#   status       = normal | suspended | delisted   (trading state only)
#   risk_warning = Boolean, nullable               (ST / *ST designation)
```

0.8.0 states the reason in `domain/trading_status.py`: one column cannot hold
two independent facts, and the old writer resolved the conflict with an
`if/elif` that let suspension win. The concrete case recorded upstream:

```text
000711.SZ (ST京蓝)  2026-08-27  status=st
                    2026-08-28  status=suspended
```

The company did not leave risk warning that day; it halted. The old encoding
silently dropped the designation for every suspended session, and
`market_breadth` read it to pick the 5% limit band, so a halted ST name was
priced with the 10% band. `delisted` addresses the second half: 611 symbols
carrying a `delist_date` (one since 1999) were previously published as normally
trading every session, because neither the halt board nor the risk board lists
them.

## 4. trading_status: the one behavioural change that matters to ASL

New vocabulary and the legacy read path, read from 0.8.0 source and tests:

```text
STATUS_NORMAL / STATUS_SUSPENDED / STATUS_DELISTED
TRADING_STATES     = {normal, suspended, delisted}
NON_TRADING_STATES = {suspended, delisted}
LEGACY_ST_STATUSES = {st, *st}                 # read-side only; nothing writes these
EXCLUDED_STATUSES  = {st, *st, delisted, suspended}   # was {st, *st, suspended}
```

Verified behaviour (synthetic legacy frame, executed under 0.8.0):

```text
symbol       is_trading  status_in   status_out  risk_warning
000711.SZ    True        st          normal      True
000711.SZ    False       suspended   suspended   False      <- legacy loss, unrecoverable
600519.SH    True        normal      normal      False
000005.SZ    True        *st         normal      True
idempotent: True
```

Two properties ASL should rely on:

1. **Reads are correct before any migration.** `validate_dataframe` calls
   `normalize_legacy`, and `risk_warning_expr` consults the legacy `status`
   encoding even when the new column is absent, or present and null. A lake
   mid-migration therefore answers correctly instead of reporting every old
   ST day as clean.
2. **The suspended-while-ST loss is not recoverable.** The legacy encoding
   stored only one of the two facts, so the migration cannot restore the
   designation for those rows; `risk_warning=False` on a legacy suspended row
   means *not recorded*, not *not warned*. Going forward the writer records
   both, and derived bar-gap rows carry `risk_warning=null` rather than a
   fabricated `False`.

A nullable Boolean is exactly what ASL's tri-state `is_st` needs: a derived
suspension has no ST evidence either way, and `null` says so.

## 5. Critical operational finding: 0.7.2 is already broken against its own dependency floor

This is not a 0.8.0 feature; it is the strongest reason to move. The
production `.venv` carries **httpx 0.28.1**. CNEquity 0.7.2's EastMoney client
merges request params via httpx, and httpx 0.28 replaced the caller's query
string instead of merging into it. 0.8.0 documents and fixes exactly this:

> httpx <= 0.27 merged it into an existing query; 0.28 replaced the query
> outright. clist.py builds a fully-formed query string and relies on this call
> only to add `ut`, so under 0.28 every clist request degenerated to `?ut=...`
> losing fs, fields, pn, pz and fid, and silently under-fetching fund_flow, the
> ST board, instruments, rotation and valuation.

Measured in this audit, same machine, same httpx 0.28.1, read-only:

| Probe | 0.7.2 | 0.8.0 |
|---|---|---|
| EastMoney ST board (`_fetch_st_symbols`) | **FAIL** - all hosts, 5 of 5 attempts | **OK** - 201 ST symbols |
| `fetch_valuation_metrics(2026-09-10)` | **FAIL** - 0 rows | **OK** - 5,909 rows, 5,563 with `float_mv` |

The 0.7.2 failure message is diagnostic: `push2` and `40.push2` disconnect, and
`push2delay` answers `EastMoney clist response has no data object` because the
request no longer carries `fs` or `fields`.

A second, independent 0.7.2 breakage: the suspension report behind
`trading_status` has been retired by the vendor.

```text
GET datacenter-web.eastmoney.com/api/data/v1/get
    reportName=RPT_CUSTOM_SUSPEND_DATA_INTERFACE ...
-> HTTP 200  {"success":false,"code":9501,"message":"filter parameter must include DATETIME"}

GET datapc.eastmoney.com/emdatacenter/tfg/list2   (0.8.0's replacement)
-> HTTP 200  {"success":true,"result":{"pages":1,"data":[...]}}
```

0.8.0 comments the same discovery: the old datacenter report now rejects
otherwise valid requests with a server-side 9501 contract requiring
undocumented MARKET/DATETIME values.

Consequence for the paused V02 work: **the two CNEquity datasets the V02 design
depends on most, `trading_status` (ST board) and `valuation_metrics` (clist),
cannot be built on 0.7.2 in this environment at all.** The migration is not a
preference; the incumbent cannot reach its sources.

## 6. Other contract diffs ASL should know about

### 6.1 corporate_actions provider order swapped

```text
0.7.2  primary=tdx_protocol   backup=eastmoney     (no backfill_source)
0.8.0  primary=eastmoney      backup=tdx_protocol  backfill_source=tdx_protocol
```

The schema is unchanged, but new rows will carry different `source` values.
ASL must not assume `tdx_protocol` provenance on `corporate_actions`. This
matters directly to the V02 preclose reference-price path, which reads
`cash_dividend` / `bonus_ratio` / `transfer_ratio` and previously could assume a
TDX owner. 0.8.0 also adds `eastmoney/corporate_actions_migration.py` and
`ths/corporate_actions.py`, i.e. new provider routes for the same dataset.

### 6.2 PIT grading of share_structure (affects the V02 turnover join)

```text
0.7.2  share_structure  pit=true                                   (ungraded)
0.8.0  share_structure  pit=true  pit_grade=partial
                                  pit_quality=reconstructed
                                  availability_col=announce_date
```

CNEquity now grades this dataset's point-in-time quality explicitly, and names
the **announcement** date as the availability column. The paused V02 module
filtered on `change_date <= trade_date`, which is the economically-correct
denominator but not the declared *availability* semantics: a restructuring
effective before its disclosure could not have been known on the earlier
session. A V02 turnover fact that claims to be replayable must state which of
the two it uses. Distribution across all 42 datasets:

```text
pit_grade   : none 37 | partial 4 | strict 1
pit datasets: announcement_index (strict); financial_statement_items,
              share_structure, shareholder_counts, top_holders (partial)
```

### 6.3 New write-side guard on bar datasets

`sanitize_dataset_rows` (new in 0.8.0) drops weekend rows and known holiday
rows from `daily_bars` / `index_bars` before persistence, and masks
`is_trading` on closed dates in `trading_calendar`. Applied to the existing
2026-09-10 R3 partition it removed **0 of 5,208 rows**, so the current
published data already satisfies the stricter rule.

### 6.4 New storage and evidence subsystems

```text
src/cnequity/storage/snapshots.py      +3305   (new)
src/cnequity/storage/raw_archive.py     +912   (new)
src/cnequity/storage/revisions.py       +842   (new)
src/cnequity/quality/                  +1783 / -287 across 14 modules
  quality/st_coverage.py                +688
```

`storage/raw_archive.py` plus `adapters/eastmoney/raw.py` mean 0.8.0 can
persist the exact provider bytes behind a fetched page, which is relevant if
ASL ever needs to re-derive without re-fetching.

### 6.5 New adapters

```text
adapters/tushare/st_history.py         ST history incl. Beijing exchange (2016+)
adapters/exchange/daily_quotes.py      exchange-sourced daily quotes
adapters/exchange/margin_trading.py    exchange-sourced margin data
adapters/bse/daily_quotes.py           Beijing exchange quotes
adapters/ths/corporate_actions.py      THS corporate actions
adapters/baostock/corporate_actions.py
```

`tushare/st_history.py` is a genuinely second ST evidence route (Tushare
`stock_st` from 2017, `bak_basic` names for the 2016 slice) that does not depend
on EastMoney boards.

## 7. Existing lake read compatibility (0.8.0, read-only)

Every check below ran under `.venv-cne080` against the live ASL/CNEquity lake.
Nothing was written or compacted.

| Check | Result |
|---|---|
| `daily_bars` 2026-09-09 / 2026-09-10 | 5,208 rows each, 11 columns, no schema gap |
| `validate_dataframe(df, 'daily_bars')` | PASS both days; `data_version=v2` preserved |
| `instruments` (7,757 rows) | PASS |
| `corporate_actions` (40,694 rows, max ex_date 2026-09-08) | PASS |
| `share_structure` (166,911 rows) | PASS |
| `trading_calendar` (4,269 rows) | PASS |
| `StateStore.get_date()` watermarks | readable: `daily_bars` 2026-09-09, `trading_calendar` 2027-09-08, `corporate_actions` 2026-09-08 |
| `meta/manifest.db` | readable, 259 runs |
| ASL production R3 updater, dry-run | **PASS** - `READY`, frozen_n 5456, eligible_n 5208, network_request_n 0 |

ASL's R3 updater depends on six CNEquity entry points. All six exist in 0.8.0:

```text
cnequity.adapters.tdx_protocol.client.fetch_daily_bars      OK
cnequity.adapters.tdx_protocol.client.normalize_with_source  OK
cnequity.storage.parquet.StagingWriter                      OK
cnequity.storage.atomic.write_parquet_atomic                 OK
cnequity.orchestrator.manifest.Manifest                      OK
cnequity.config.loader.load_config                           OK
```

`share_structure`, `valuation_metrics` and `trading_status` have no physical
files in this lake, so no legacy-encoded `trading_status` partition exists to
migrate.

## 8. Migration tooling (read only, not executed)

```text
scripts/migrate_trading_status_risk_warning.py    (the one relevant here)
scripts/migrate_daily_bars_volume_v2.py
scripts/migrate_pit_vintages.py
```

Contract of `migrate_trading_status_risk_warning.py`, from its source:

```text
input      : curated/trading_status/**/*.parquet
output     : same files rewritten in place; adds risk_warning from legacy
             status=st|*st, and rewrites those rows' status to normal
dry-run    : DEFAULT. Only --apply writes.
idempotent : yes; a file already on the new encoding is not rewritten
rollback   : none built in; the script edits curated files in place
mutations  : per-file atomic write via write_parquet_atomic
does NOT   : invent delisted rows for history, which would back-stamp today's
             delist dates onto past sessions
```

**Not needed for this lake today**: there are zero `trading_status` files. It
becomes relevant only if a legacy-encoded `trading_status` is ever imported.
Because reads are already correct without it, running it is a physical-schema
uniformity step rather than a correctness step, and it is the only migration in
the list that would touch ASL's existing data.

## 9. Breaking changes, required and optional migrations

```text
BREAKING_CHANGES (for code that reads trading_status)
  1. trading_status gains a logical column risk_warning; status no longer
     carries st / *st.
  2. EXCLUDED_STATUSES now also contains delisted, so a universe filter that
     copied the old set silently starts excluding delisted names.
  3. status may now take the value delisted, which 0.7.2 consumers do not know.
  4. corporate_actions primary provider moved to eastmoney: new rows carry a
     different source value.

NOT breaking (verified by executing 0.8.0 against the existing lake)
  - daily_bars, instruments, corporate_actions, share_structure,
    trading_calendar, valuation_metrics schema: unchanged.
  - DATASET_DATA_VERSION unchanged; existing data_version=v2 rows validate.
  - watermarks, meta/state and meta/manifest.db are read-compatible.
  - ASL R3 updater dry-run, and all six of its CNEquity entry points import.
```

```text
REQUIRED_MIGRATIONS   = NONE for this lake
   no trading_status files exist, so no legacy-encoded partition needs
   rewriting; every other dataset ASL holds is byte-compatible.

OPTIONAL_MIGRATIONS
  1. scripts/migrate_trading_status_risk_warning.py, only after a legacy
     trading_status is imported. Reads are already correct without it, so
     this is physical uniformity only.
  2. Re-derive the absent datasets under 0.8.0 rather than importing them:
     trading_status, valuation_metrics, share_structure.
```

## 10. Daily Facts V02 impact

The paused V02 design was written against the 0.7.2 contract. Under 0.8.0 the
field sources change as follows:

| Fact | 0.7.2-based V02 plan | 0.8.0 revision |
|---|---|---|
| `preclose` | derived: prior R3 close, or prior close + corporate actions + tick rounding | **unchanged**; 0.8.0 adds no native preclose |
| `pct_chg` | derived `(close/preclose - 1) * 100` | **unchanged** |
| `turnover_rate` | `volume / share_structure.float_shares * 100`, filtered on `change_date <= day` | formula unchanged, but the join must choose **`announce_date`** (declared availability) vs `change_date` (economic effect) and state which; the dataset self-grades `pit_quality=reconstructed` |
| `trade_status` | map `{normal, st, *st, suspended}` | map **`{normal, suspended, delisted}`**; `st`/`*st` no longer occur in `status`; ASL must decide how `delisted` maps onto its `{TRADING, SUSPENDED, UNKNOWN}` tri-state |
| `is_st` | map `status in {st, *st}` | map **`risk_warning`**, a nullable Boolean: `True` -> TRUE, `False` -> FALSE, **`null` -> UNKNOWN** |

The `is_st` change is a net improvement for ASL. Under 0.7.2 the ST fact was
unrecoverable for any suspended session; under 0.8.0 the two facts are
independent columns and an unevidenced suspension says `null` instead of
fabricating `False`. ASL's existing `IS_ST_UNKNOWN_N` gate maps onto it
directly.

Two V02 decisions must be re-opened:

```text
1. trade_status: does delisted map to ASL SUSPENDED, or does the ASL contract
   need a fourth value? MASTER_SPEC section 10 already names DELISTING in the
   query contract, so the vocabulary exists upstream of Daily Facts; the
   Daily Facts Phase 1 tri-state does not. This is a contract question.
2. turnover_rate: which date column is authoritative for the denominator.
```

The preclose contract conflict recorded in the previous audit is unchanged by
0.8.0: `DAILY_FACTS_PHASE1_PRECLOSE_AUTHORITY_V01.md` still freezes
`CANONICAL_SOURCE = BAOSTOCK_HISTORY_K_PRECLOSE`, so a locally derived preclose
still requires an explicit contract revision.

## 11. Safe upgrade sequence (proposed, NOT executed)

```text
STEP 0  freeze the current authority
        R3    = 140197cd3c95a3f8e1c9f5750f117c66eafc09d9113757e511f31e46848bf66b
        FACTS = 9b4f474ebcb0db97d9dbfdb824eb6b7e17f017a6ac8957ed3d6a8fb5e3ca21ac
        satisfied at the time of this audit.
STEP 1  resolve the Daily Facts contract questions in section 10 before any
        dataset is built, so the build target is not a moving one.
STEP 2  bump the pin in pyproject.toml to the v0.8.0 commit
        d453853da766b3ba3e44489c0fb6e0089243fa25, re-lock, and keep
        .venv-cne080 until the new .venv verifies.
STEP 3  read-only verification on the upgraded environment, in this order:
        cne status --datasets ; cne verify ; cne doctor ; the ASL R3 dry-run.
        Expected: every existing dataset reads and nothing is rewritten.
STEP 4  build the three absent datasets from 0.8.0 bulk paths
        (trading_status, valuation_metrics, share_structure). No migration
        script is needed because none of them physically exists yet.
STEP 5  only then resume a Daily Facts V02 shadow under the new contract, and
        do not publish. The shadow stays a comparison artifact.
```

Steps 2 and 4 both change production state, so both are outside this round's
authorization.

## 12. Rollback boundary

```text
ROLLBACK TARGET       : pyproject.toml pin + uv.lock -> a18ee048 (0.7.2)
ROLLBACK IS SAFE FOR  : reading. Every dataset ASL holds validates against
                        both versions, and no schema in use changed.
ROLLBACK DOES NOT     : restore fetch capability. 0.7.2 cannot reach the ST
                        board, the suspension report, or any clist dataset
                        under httpx 0.28.1, so a rollback returns an
                        environment that can read the lake but cannot refresh
                        trading_status or valuation_metrics.
IRREVERSIBLE STEP     : running migrate_trading_status_risk_warning.py --apply
                        rewrites curated files in place with no built-in undo.
                        It is unnecessary here, which removes the only
                        irreversible action from the upgrade path.
```

## 13. What the paused task had reached when this instruction arrived

Recorded so the interrupted work stays distinguishable from this audit.

```text
provider_requests_completed
  - EastMoney ST board probe (read-only diagnostic): succeeded under 0.8.0,
    failed under 0.7.2.
  - EastMoney valuation probe (read-only diagnostic): same split.
  - Baostock ST-history probe: attempted, throttled, ABORTED before writing.
  - EastMoney share_structure backfill: COMPLETED as one atomic run
    (run e9afd938-7426-4888-aee0-0f86be7520c6, 26 windows, 166,911 rows,
    compacted, status success).
  - No full-market per-symbol BaoStock Daily Facts acquisition was running;
    that was stopped in an earlier round.

datasets_created
  - curated/share_structure: NEW. 26 files, 166,911 rows, 5,919 symbols,
    change_date 2001-01-03..2026-09-09, source=eastmoney, 162,854 rows with
    float_shares. Fetched by 0.7.2, so it is migration-compatibility evidence
    and NOT a frozen V02 source contract.
  - trading_status / valuation_metrics: still absent (0 files).

shadow_rows_generated = 0
  no V02 shadow row was produced; the module was written but never executed
  against the lake.

files_changed
  - src/ashare_data/daily_facts_v02.py (NEW, untracked, 467 lines).
    Derivation module for the 0.7.2-based design. NEVER EXECUTED and NOT
    production-ready: it encodes the 0.7.2 status vocabulary and the
    change_date join, both of which section 10 revises. Left in the worktree,
    uncommitted, deliberately not deleted.
  - tests/test_cnequity_v080_daily_reuse.py (pre-existing untracked file, not
    authored in this round).

data_changed        = curated/share_structure only
publication_changed = NONE
  R3 pointer    = 140197cd... (unchanged)
  FACTS pointer = 9b4f474e... (unchanged)
  V1 Daily Facts authority untouched; no V02 authority exists.
```

## 14. Limitations

```text
1. The ASL R3 updater dry-run does not import cnequity, because those imports
   sit below the execute branch. Compatibility therefore rests on two separate
   facts: the dry-run passes, and all six entry points import successfully
   under 0.8.0. A full execute-path smoke was not run; it needs provider
   traffic and is outside this round.
2. No dataset was BUILT under 0.8.0, so bulk call patterns here are read from
   code and confirmed only for the two probes that ran (ST board: 201 symbols;
   valuation: 5,909 rows).
3. share_structure was populated by 0.7.2 before this instruction, through the
   EastMoney datacenter path, which is not clist-based and so was not affected
   by the httpx defect. It reads and validates under 0.8.0, but that is not
   evidence that 0.8.0 builds it identically.
4. The migration script was read, not dry-run. Running it is safe by default
   (dry-run) but unnecessary while no trading_status files exist.
5. delisted handling was read from 0.8.0 source and its unit tests, not
   observed on a live run.
6. The two provider probes were single observations; a transient vendor
   outage could in principle explain the 0.7.2 failures. The code-level cause
   (httpx>=0.28 replacing rather than merging query params) is documented in
   0.8.0's own source and is consistent with the observed symptom, so the
   attribution is strong but rests on one session's measurements.
```

## 15. Next gate

```text
NEXT_GATE = a separately authorized task that either
  (a) resolves the two Daily Facts contract questions in section 10, or
  (b) performs the pinned 0.8.0 upgrade plus the read-only verification in
      section 11, steps 2-3.
This round stops at compatibility audit plus recommended plan, by instruction.
```
