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

---

## 7. EXECUTION HARDENING V01 (post re-review of 1531da8)

Seven correctness fixes from the independent Sol re-review, all applied:

### 7.1 CLI exec mode

```text
CLI_EXEC_REACHABLE  true
DEFAULT_MODE        dry-run
```

- Removed the broken `--dry-run store_true default=True` that made `--exec`
  always error. Now `--dry-run` / `--exec` are a mutually exclusive group and
  `dry_run = not args.exec` (no flag => dry-run). Conflicting flags are
  rejected with exit 2. The contradictory "this run did not execute" message
  behind `--exec` was deleted.
- CLI tests: default dry-run / `--dry-run` dry-run / `--exec` reachable with
  `dry_run=False` and adapter called once / conflicting flags rejected.

### 7.2 PILOT_COMPLETE includes compaction and final status

```text
PILOT_COMPLETE =
  corporate_status == success
  AND failed_symbols empty
  AND compact_status == success
  AND final_status == success
  AND NOT persistent-config mutation
```

Invariant: `PILOT_COMPLETE=true => final_status=="success"`. Compact
failed/warning => `PILOT_INCOMPLETE` (tested).

### 7.3 Real write telemetry is truthful

```text
dry-run:            MANIFEST_WRITE=NO  REAL_ROOT_WRITE=NO
real execution:     MANIFEST_WRITE=YES REAL_ROOT_WRITE=YES
                    (manifest is real-root runtime state)
MARKET_DATA_WRITE_STATUS  YES/NO/UNKNOWN, judged from actual pre/post
                    corporate_actions artifact evidence, never from
                    final_status. PILOT_INCOMPLETE != MARKET_DATA_WRITE=NO
                    (pinned lifecycle compacts partial/failed sweeps).
```

### 7.4 Network telemetry is not fabricated

`24 if complete else 0` was deleted. Now:

```text
NETWORK_PROVIDER_DATA_FETCH  NO      (dry-run only)
                             YES     (real execution entered provider step)
                             UNKNOWN (failed before provider path)
NETWORK_PROVIDER_REQUEST_COUNT     UNVERIFIED
```

REQUESTED_SYMBOL_N is never conflated with provider request count (tested for
partial/failed execution reporting a truthful non-NO value).

### 7.5 In-memory config restored

`failover_enabled` / `_backfill` / `_backfill_start` / `_backfill_end` are
snapshotted; the execution-local override (failover_enabled=false during the
bounded run) is restored for success / failure / exception / dry-run (all
tested). The original "failover disabled after run" assertion was corrected to
the right semantics: disabled DURING, restored AFTER.

### 7.6 Config hash is a real check

```text
CONFIG_SHA_BEFORE / CONFIG_SHA_AFTER  real SHA-256 of the config file
PERSISTENT_CONFIG_CHANGED  = before != after   (never hard-coded false)
config path unknown/missing  -> CONFIG_INTEGRITY_STATUS=UNKNOWN
```

A detected mutation is `WRITE_BOUNDARY_BREACH` and `PILOT_COMPLETE=false`
even if provider/compact succeeded (tested).

### 7.7 Receipt post-check (best-effort, no fabricated COMPLETE)

After real execution the adapter reads the run's `corporate_actions_chunk`
success receipts and reports requested vs covered: no unexpected symbol, each
requested symbol receipted, window exact, failed chunks never contribute. If
unverifiable it reports UNKNOWN; it never invents an unverified COMPLETE. It
does not restructure the R4A0 gate.

### 7.8 Tests and real-config dry-run (2026-08-20)

`tests/test_r4a0_bounded_adapter.py` grew to **34 targeted tests** (offline;
fake engine/manifest, mocked pin, injected identity; no provider/network):
original 20 kept (corrected semantics) + CLI-mode (4) + execution hardening
(10). Existing `tests/test_r4a0_corporate_actions_gate.py` (23) re-run clean.
Total: **57 passed**.

Real-config `--dry-run` (read-only):

```text
STATUS READY / DRY_RUN_STATUS OK / DRY_RUN_SYMBOL_N 24
MANIFEST_WRITE NO  REAL_ROOT_WRITE NO
NETWORK_PROVIDER_DATA_FETCH NO  NETWORK_PROVIDER_REQUEST_COUNT UNVERIFIED
MARKET_DATA_WRITE_STATUS NO
PERSISTENT_CONFIG_CHANGED false  CONFIG_INTEGRITY_STATUS OK
CONFIG_STATE_RESTORED true
sidecar -wal/-shm mtime unchanged
```

```text
TARGETED_TESTS  34 (adapter) + 23 (gate) = 57 passed
GIT_DIFF_CHECK  CLEAN
```

---

## 8. FINAL RECEIPT / RUNTIME-CONTRACT FIX V02 (post re-review of e6efe1a)

### 8.1 Real manifest row contract

`Manifest.get_batches_for_run()` returns `list[sqlite3.Row]`; `receipt_post_check`
no longer assumes `dict.get()`. Every row is normalized via `dict(row)` before
reads. A test with the REAL pinned `Manifest` + a tmp `manifest.db`, actual
`corporate_actions_chunk` receipts, then `receipt_post_check` passing on real
`sqlite3.Row` rows was added (no FakeManifest-only proof).

```text
REAL_MANIFEST_ROW_CONTRACT_TEST  passed (real pinned Manifest)
```

### 8.2 Receipt check gates COMPLETE

```text
PILOT_COMPLETE =
  corporate_status == success
  AND failed_symbols empty
  AND compact_status == success
  AND final_status == success
  AND CONFIG_INTEGRITY_STATUS == "OK"
  AND receipt_post_check.STATUS == "OK"
```

Receipt UNKNOWN / MISMATCH / missing requested symbol / unexpected symbol /
wrong window all yield `PILOT_COMPLETE=false` and `PILOT_INCOMPLETE` — no
promotion. Enforced and tested (missing / unexpected / wrong-window /
manifest-read-exception -> UNKNOWN).

### 8.3 CONFIG UNKNOWN fails closed

When the config hash cannot be produced on either side,
`CONFIG_INTEGRITY_STATUS=UNKNOWN` and complete is forbidden
(`PILOT_COMPLETE=false`). Formal COMPLETE only allows `CONFIG_INTEGRITY_STATUS
== "OK"`. New test: config row unknown -> not complete.

### 8.4 Network telemetry wording

Split into:

```text
PROVIDER_STEP_ENTERED         YES / NO   (was the pinned step invoked?)
NETWORK_PROVIDER_DATA_FETCH   NO         (dry-run)
                              UNKNOWN    (real execution, no precise provider
                                          evidence)
                              YES        (only with real provider-call evidence;
                                          none available to the adapter -> UNKNOWN)
NETWORK_PROVIDER_REQUEST_COUNT            UNVERIFIED (never guessed as 24)
```

"About to enter run_step" is reported as `PROVIDER_STEP_ENTERED`, never as a
fabricated provider fetch count. REQUESTED_SYMBOL_N stays separate.

### 8.5 Exception lifecycle

If a fresh run was started before a failure, the adapter finalizes that run as
`FAILED` (best effort) so no silent RUNNING orphan remains — without
refactoring JobEngine. Reported via `EXCEPTION_RUN_FINALIZATION`
(FAILED / FAILED_ATTEMPT_ERROR / NOT_STARTED). Test: exception after run start
-> the manifest run ends `failed`, config restored.

### 8.6 Tests / real-config dry-run

`tests/test_r4a0_bounded_adapter.py` now has **42 targeted tests**:
prior 34 (semantics corrected: PROVIDER_STEP_ENTERED / network UNKNOWN /
receipt gating) + real-row contract + receipt missing / unexpected /
wrong-window / read-exception / zero-event-complete + config-UNKNOWN +
exception-finalize. Existing `tests/test_r4a0_corporate_actions_gate.py` (23)
re-run clean. Total: **65 passed**.

Real-config `--dry-run` (read-only):

```text
DRY_RUN_STATUS OK   STATUS READY
MANIFEST_WRITE NO   REAL_ROOT_WRITE NO
PROVIDER_STEP_ENTERED NO
NETWORK_PROVIDER_DATA_FETCH NO
NETWORK_PROVIDER_REQUEST_COUNT UNVERIFIED
CONFIG_INTEGRITY_STATUS OK
PERSISTENT_CONFIG_CHANGED false
CONFIG_STATE_RESTORED true
sidecar -wal/-shm mtime unchanged
```

```text
TARGETED_TESTS  42 (adapter) + 23 (gate) = 65 passed
GIT_DIFF_CHECK  CLEAN
```
