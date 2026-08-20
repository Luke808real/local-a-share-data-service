# R4A0 BOUNDED EXECUTION ADAPTER — AUTHOR REPORT

STATUS: R4A0_BOUNDED_ADAPTER_BUILT_AUTHOR_ONLY (independent Sol audit pending)
DATE: 2026-08-20
BRANCH: codex/r4a0-corporate-actions-bootstrap-pilot-v01
BASE_HEAD: 1cc6f1b48f496c1642b1970165efc532880688fc
UPSTREAM_CNEQUITY: v0.7.2 @ a18ee0484dfb0801650175471724def3228b8a17

## 1. Deliverable

A thin R4A0 bounded execution adapter that makes a first-time
arbitrary-symbol-subset `corporate_actions` backfill safe under the pinned
CNEquity pipeline, WITHOUT touching pinned upstream (no .venv/site-packages/
uv.lock/pyproject modification, no fork, no vendored source):

```text
src/ashare_data/r4a0_bounded_adapter.py       adapter (execution-scope layer)
tools/run_r4a0_corporate_actions_pilot.py     CLI (default --dry-run)
tests/test_r4a0_bounded_adapter.py            20 offline tests
```

The adapter is a wrapper ONLY: all real fetching happens through the pinned
registered `step_corporate_actions` -> pinned adapters. It imports no
downloader / HTTP / TDX client directly (enforced by a test).

## 2. Execution-scope design (bounded, proven against pinned primitives)

The pinned `JobEngine.run_step(name, trade_date, run_id, context)` publicly
accepts a context; `step_corporate_actions` reads `context["_retry_symbols"]`
as its explicit symbol source. The adapter defines this explicitly as
`BOUNDED_EXECUTION_SCOPE` — an explicit pilot scope, NOT a fabricated retry
(no fake failed run/batch is created to fool the retry system).

Lifecycle (identical ordering to pinned CLI `_finish_backfill_run`):

```text
fresh manifest run (metadata: dataset/backfill_scope symbols+window)
-> run_step("corporate_actions", context with exact bounded symbols)
   -> pinned step writes manifest parent + child chunk receipts (symbols_json)
-> run_step("compact")
-> manifest.finish_run
```

Gates (all fail-closed, before any engine/provider behavior):

```text
1 <= len(symbols) <= 24
window exactly 2016-01-01 .. 2026-08-17
canonical SH/SZ only (BJ rejected), unique (duplicates rejected),
every symbol inside frozen R3 formal identity (N=5456, hash 2b1e7202...)
pinned upstream commit exactly a18ee0484dfb0801650175471724def3228b8a17
```

## 3. Real-config dry-run (read-only, 2026-08-20)

Run against the real config/root, `--dry-run`:

```text
STATUS                         READY            (DRY_RUN_STATUS=OK)
DRY_RUN_SYMBOL_N               24
PIN_EXPECTED / PIN_ACTUAL      a18ee0484dfb0801650175471724def3228b8a17
PIN_MATCH                      true
FORMAL_IDENTITY_N              5456
FORMAL_IDENTITY_HASH           2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f
IDENTITY_MATCH                 true
ADAPTER_MAX_SYMBOL_N           24
FAILOVER_WAS_ENABLED_IN_CONFIG true
FAILOVER_BACKUP_ENABLED        false   (EastMoney snapshot is unbounded:
                                       no symbol parameter -> disabled in-memory)
PERSISTENT_CONFIG_CHANGED      false   (config SHA-256 unchanged)
MANIFEST_WRITE                 false
REAL_ROOT_WRITE                false
NETWORK_PROVIDER_DATA_FETCH    0
```

No real pilot, no provider fetch, no real-root manifest/parquet write, no
sidecar mutation (manifest `-wal`/`-shm` mtime unchanged).

## 4. Tests

`tests/test_r4a0_bounded_adapter.py` — 20 offline targeted tests (tmp root /
fake engine / fake manifest / mocked pin / injected identity), all PASS:

```text
 1  24-symbol valid scope accepted
 2  >24 rejected before engine
 3  empty symbol list rejected
 4  symbol outside frozen identity rejected
 5  BJ rejected
 6  duplicate symbols rejected
 7  identity hash mismatch rejected
 8  pin mismatch rejected
 9  exact requested subset reaches execution context
10  manifest run metadata records exact subset
11  parent/child receipt symbol scope remains exact subset
12  no load_symbols / full-universe fallback occurs
13  failover backup disabled for bounded run
14  persistent config unchanged
15  dry-run performs zero manifest write
16  dry-run performs zero provider call
17  step failure does not produce COMPLETE
18  compact/finish ordering matches pinned backfill lifecycle
19  zero-event result keeps successful symbol receipt semantics
20  adapter source contains no direct provider downloader path

TARGETED_TESTS 20 passed
```

## 5. Verification

```text
NETWORK_PROVIDER_DATA_FETCH   0        (dry-run; real pilot forbidden this task)
REAL_ROOT_WRITE               NO
MANIFEST_WRITE                NO
MARKET_DATA_CHANGED           NO
CODE_FILES_CHANGED            3        (adapter + CLI + tests)
REPORT_FILES_CHANGED          1
GIT_DIFF_CHECK                CLEAN
```

## 6. Bounded next action

Sol: audit the exact pushed commit. On audit pass,
`BOUNDED_PILOT_EXECUTION` may advance from FORBIDDEN to a Sol-signed pilot run
using the exact 24-symbol scope recorded here (dry-run already validated it).
Until then no real execution.
