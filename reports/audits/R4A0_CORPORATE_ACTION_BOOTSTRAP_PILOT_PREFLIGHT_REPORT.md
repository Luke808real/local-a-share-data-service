# R4A0 CORPORATE_ACTION BOOTSTRAP PILOT — PRE-FLIGHT REPORT

STATUS: BOOTSTRAP_PILOT_BLOCKED_NO_BOUNDED_SYMBOL_INTERFACE
DATE: 2026-08-20
BRANCH: codex/r4a0-corporate-actions-bootstrap-pilot-v01
BASE_HEAD: 6a7b76320f91003bd59b123d5f86c07edd599616
UPSTREAM_CNEQUITY: v0.7.2 @ a18ee0484dfb0801650175471724def3228b8a17
DATA_AS_OF: 2026-08-17
WINDOW: 2016-01-01 .. 2026-08-17

## 0. Verdict

PRE-FLIGHT FAILED. The pinned CNEquity v0.7.2 engine/CLI does NOT expose a
safe bounded-symbol interface for a first-time arbitrary-subset
`corporate_actions` backfill. Per task section 1, the pilot is STOPPED:

- NO full-market run was started or would be allowed;
- NO temporary upstream refactor or self-written downloader was introduced;
- NO real-root write occurred; NO provider fetch occurred.

## 1. Evidence (pinned package, read-only inspection)

### 1.1 `cne backfill --symbols` rejects corporate_actions

`cli/main.py` `backfill()`:

```python
if symbols_str:
    symbols = [...]
    if dataset == "trading_status":
        cfg._backfill_symbols = symbols
    else:
        _override_scope(cfg, dataset, symbols)   # corporate_actions -> raises
```

`_override_scope()`:

```python
block = SCOPED_DATASETS.get(dataset)   # {minute_bars, minute_bars_5m, trade_ticks}
if block is None:
    raise click.ClickException(
        "--symbols only applies to datasets with a configured scope ...; "
        "<dataset> takes its universe from instruments.")
```

So `cne backfill corporate_actions --symbols <24>` fails safely with an error
(never a silent full-market run), but it is impossible to target an arbitrary
subset through the CLI.

### 1.2 The step's only symbol sources are "all instruments" or "retry leftovers"

`steps/events.py` `step_corporate_actions()` (backfill branch):

```python
symbols = list(context.get("_retry_symbols") or load_symbols(config))
```

- `context["_retry_symbols"]` is set ONLY by `JobEngine._retry_run`
  (`orchestrator/engine.py`), from the failed batches' `symbols_json` of an
  EXISTING run. That is a "retry the prior failed subset" semantic, not a way
  to name an arbitrary first-time subset.
- Otherwise `load_symbols(config)` (`steps/common.py`) returns the FULL curated
  `instruments` universe (7757 rows) — the full market.

### 1.3 Window is bounded, but symbol scope is not

`config._backfill_start/_backfill_end` are recorded into each batch receipt
(`steps/events.py`), so an existing date window is respected when a run is
already scoped. Nothing, however, bounds the symbol list to an arbitrary
first-time subset; `universe.default="all_a"` (R2-frozen config) is the
default.

### 1.4 No other CLI path targets corporate_actions with arbitrary symbols

`run`, `retry`, `delisted backfill`, `init` — none provides an arbitrary
corporate_actions subset entry. `retry` regenerates symbols from a prior run.

## 2. Why it must stop here (no workaround)

- "退化成全市场运行" is explicitly forbidden; running through
  `load_symbols(config)` would be exactly that.
- "为了完成任务临时重构 upstream" is explicitly forbidden; adding an
  arbitrary-subset hook to the pinned step, or writing a bespoke downloader
  that bypasses manifest/staging/provenance/receipts/compaction, is out of
  scope for this task.

Therefore the only correct action is the fail-closed pre-flight block.

## 3. Deterministic 24-symbol pilot candidate (read-only reference, NOT run)

Computed from the frozen R3 identity only, for Sol's reference if a bounded
interface becomes available. No provider call; no re-discovery.

```text
Formal identity source   curated/daily_bars unique symbols (5456,
                         sha256 == 2b1e7202...7be2f, frozen match)
Delisted split           r3-identity-receipt shsz_formal_delisted (248)
Active split             5208 (SH 2312 / SZ 2896)
PILOT_SYMBOL_N           24
Composition              active SH 6 + active SZ 6 + delisted SH 6 + delisted SZ 6
PILOT_SYMBOLS            ["000001.SZ","000002.SZ","000004.SZ","000005.SZ",
                         "000006.SZ","000007.SZ","000008.SZ","000009.SZ",
                         "000018.SZ","000022.SZ","000023.SZ","000033.SZ",
                         "600000.SH","600004.SH","600005.SH","600006.SH",
                         "600007.SH","600008.SH","600009.SH","600068.SH",
                         "600069.SH","600070.SH","600074.SH","600077.SH"]
PILOT_SYMBOL_HASH        e846331eae6cc090eb8b4f9109f0f25f2692e7ad759acd41b3eb4d62241f3661
```

## 4. PIN / upstream verification (read-only)

```text
PIN_EXPECTED  a18ee0484dfb0801650175471724def3228b8a17
PIN_ACTUAL    a18ee0484dfb0801650175471724def3228b8a17   (direct_url.commit_id)
PIN_MATCH     true
```

## 5. Nothing changed

```text
REAL_ROOT_WRITE             NO
MARKET_DATA_CHANGED         NO   (no dataset touched)
MARKET_DATASETS_CHANGED     none
NETWORK_PROVIDER_DATA_FETCH 0
CODE_FILES_CHANGED          0
TARGETED_TESTS              n/a (no code change)
GIT_DIFF_CHECK              CLEAN (docs only)
```

## 6. Bounded next action (for Sol)

The pilot cannot proceed until a safe bounded-symbol interface for
`corporate_actions` exists. Options for Sol:

1. Authorize a minimal, audited upstream-facing extension that adds an
   arbitrary-symbol scope to the pinned `corporate_actions` backfill step
   (e.g. an explicit `_backfill_symbols` read by the step, mirroring the
   trading_status path), then re-review before any pilot run; or
2. Confirm an existing official interface I should use (with exact command and
   boundedness evidence); or
3. Close the pilot.

Until then: FULL_CORPORATE_ACTION_BOOTSTRAP=FORBIDDEN and R4A0_READY=false.
