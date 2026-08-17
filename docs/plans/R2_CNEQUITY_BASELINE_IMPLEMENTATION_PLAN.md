# R2 CNEQUITY BASELINE IMPLEMENTATION PLAN

**TASK_CONTRACT:** `R2-CNEQUITY-BASELINE-V01`
**SPEC_VERSION:** `V1.0 FROZEN`
**BASE_HEAD:** `84eb5b33c0fb7175db08618bf593c89674348c09`
**PHASE:** `R2 — CNEQUITY BASELINE`
**MODE:** `IMPLEMENTATION / VALIDATION`
**FULL_MARKET_AUTHORIZED:** `NO`
**SUPERPOWERS:** `NOT_USED — current user override`

## Goal

Establish a reproducible, exact-SHA CNEquity runtime and a clean authoritative
data-root layout for this service without fetching any market data. Freeze the
baseline storage/configuration contracts needed by R3–R8, while keeping all R1
legacy assets read-only and all unresolved data semantics fail-closed.

## Entry gate

- `main` and `origin/main` equal
  `84eb5b33c0fb7175db08618bf593c89674348c09`.
- `docs/PROJECT_STATE.md` says `CURRENT_PHASE: R2 — CNEQUITY BASELINE` and
  `R2_EXECUTION: NOT_STARTED`.
- R1 exact author commit
  `09e9254042ad747983d40d794595135fb58e2d80` has `AUDIT_PASS`.
- No R2 implementation may start from an unreviewed version of this plan.

## Frozen R2 choices

| Item | R2 choice | Reason |
|---|---|---|
| CNEquity upstream | `rootSunc/CNEquity` v0.7.2 at immutable Git SHA `a18ee0484dfb0801650175471724def3228b8a17` | R1 audited this exact source; version alone is not immutable enough. |
| Dependency manager | `uv` with committed `uv.lock` | Locks the Git commit and all transitive packages. |
| Python | CPython `3.12` (`.python-version`; expected installed runtime 3.12.13) | Pinned upstream explicitly classifies 3.12; the host default 3.14 is not classified. |
| Authoritative data root | `/Users/luke808/AI/local-a-share-data-service-data` | New, outside Git, outside every legacy root, and absent at plan time. |
| R2 initialization | `cne init --layout-only` exactly once after preflight | Creates only lake layout, manifest, and DuckDB views; does not run init phases or download data. |
| macOS workers | `1` | Required by the pinned upstream validator because the TDX client is not fork-safe on macOS. |
| Query/network defaults | `on_demand.enabled=false`; no MCP `--live`; `tdx_protocol.allow_mock=false` | Query paths must be local-only and fake rows are forbidden. |
| Minute config | disabled in R2; future frequency contract restricted to `5m` | R2 must not fetch data; R5 owns explicit full-market enablement/backfill authorization. |

These choices implement the frozen Spec and do not change D001–D025.

## Global prohibitions

- Do not access, stat, list, open, query, hash, copy, move, delete, repair, or
  rewrite any legacy root during R2 implementation:
  - `/Users/luke808/AI/asl-shared`
  - `/Users/luke808/AI/asl-r8-5m-lake`
  - `/Users/luke808/AI/V flash/data`
- Do not run `cne demo`, ordinary `cne init`, `cne run`, `cne backfill`,
  `cne retry`, `cne derive`, `cne compact`, `cne sources`, `cne servers test`,
  `cne status`, `cne query`, `cne audit`, `cne repartition`, `cne stats`,
  `cne verify`, or any repair/cleanup command. Several read-looking commands
  create manifest/state/lock files or writable DuckDB views at this pin.
- Do not download, migrate, normalize, publish, or query market data.
- Do not add B1/B2, strategy, backtest, Forward, TradePlan, MCP, scheduler, or
  R3 implementation.
- Do not modify `docs/MASTER_SPEC.md` or `docs/DECISIONS.md`.
- Do not force push, amend an audited commit, or delete evidence branches.
- Do not treat an R1 `CROSSCHECK_ONLY` asset as migration input.

## Expected R2 file map

Create:

- `.gitignore`
- `.python-version`
- `pyproject.toml`
- `uv.lock`
- `config/cnequity.toml`
- `docs/contracts/R2_CNEQUITY_BASELINE_CONTRACT.md`
- `tools/verify_r2_baseline.py`
- `tests/test_r2_baseline_contract.py`
- `reports/audits/R2_CNEQUITY_BASELINE_AUTHOR_REPORT.md`

Modify only at final handoff:

- `docs/PROJECT_STATE.md`
- `tests/test_project_docs_contract.py`

The implementation may reduce this file set if a file is demonstrably
unnecessary, but it may not broaden scope without review.

## Task 1 — Runtime pin and reproducible environment

### 1.1 Preflight

Verify exact Git state, installed uv version, installed Python candidates, free
space, and that the chosen data-root path does not exist. Do not inspect any
legacy root. Stop on a SHA mismatch or an existing target path.

### 1.2 Write RED runtime tests

Create `tests/test_r2_baseline_contract.py` with assertions that:

- `.python-version` selects 3.12;
- `pyproject.toml` pins CNEquity by the exact immutable Git SHA;
- `uv.lock` resolves the same SHA and package version 0.7.2;
- installed distribution `direct_url.json` records the pinned Git URL and
  `commit_id = a18ee0484dfb0801650175471724def3228b8a17`;
- project runtime metadata contains no strategy/MCP/scheduler dependency;
- the configured data root is absolute, outside the repository, and is not any
  legacy root or descendant of one.

Run only the new test and record the expected RED result before implementation.

### 1.3 Add runtime files and lock

Use a minimal PEP 621 project. Add CNEquity from the exact Git SHA and a dev
dependency group containing pytest. Pin CPython 3.12 with `.python-version`.
Generate `uv.lock`, then install only with:

```bash
uv sync --frozen
```

This network access is dependency installation only; it is not market-data
access. Never install or import from an unpinned CNEquity checkout.

### 1.4 Verify runtime

```bash
uv run python --version
uv run python -c "import importlib.metadata as m; print(m.version('cnequity'))"
uv run cne --version
```

Expected: CPython 3.12.x and CNEquity 0.7.2. Verify the lock source and installed
`direct_url.json` both contain the exact Git SHA; do not infer the SHA from the
version string or import path.

### 1.5 Commit Task 1

Commit only runtime, lock, ignore, and focused test files:

```text
build: pin CNEquity baseline runtime
```

## Task 2 — Configuration and storage contracts

### 2.1 Extend RED tests

Tests must parse the committed TOML and prove:

- `data.root` equals the frozen absolute target;
- `orchestrator.workers = 1`;
- `tdx_protocol.allow_mock = false`;
- `universe.default = "all_a"`;
- `on_demand.enabled = false`;
- minute bars are disabled and frequencies are exactly `["5m"]`;
- no trade-tick, sentiment/news, strategy, or non-V1 collection group is
  enabled;
- daily/init step references are accepted by pinned CNEquity validation;
- the DuckDB path resolves under the authoritative root.

### 2.2 Create minimal service-owned config

Create `config/cnequity.toml` from the pinned upstream contract, not from local
legacy settings. Keep only configuration required by the V1 foundation and
future approved phases. Do not include credentials or a proxy secret.

The config may declare future network sources and rate limits, but R2 does not
run them. `allow_mock` remains false. 5m remains disabled until R5.

### 2.3 Freeze baseline contract

Create `docs/contracts/R2_CNEQUITY_BASELINE_CONTRACT.md` recording:

- upstream repo, tag, exact SHA, Python/runtime/lock authority;
- absolute data-root path and exact code-created lake layout (record that
  upstream prose additionally names `logs/`, while pinned
  `init_data_layout()` does not create it);
- manifest SQLite WAL behavior and expected runtime sidecars;
- DuckDB path and view-only baseline state;
- config hash/lock hash receipt requirements;
- local-only query boundary and forbidden `--live` behavior;
- zero-market-data invariant for R2;
- no-legacy-reuse invariant.

Resolve the R1 carry-forwards at the contract level only:

1. **Trading status:** freeze target PK `(symbol, trade_date)`, strict
   point-in-time date semantics, and this V1 mapping: upstream `normal` →
   `NORMAL`, `suspended` → `SUSPENDED`, `st` → `ST`, `*st` →
   `STAR_ST`; unrecognized or missing values → `UNKNOWN`. `DELISTING`
   requires an explicit R4 rule/source and remains `UNKNOWN` until then.
   `is_trading` alone never determines ST or delisting status. Row-level
   `source` provenance outranks conflicting registry/catalog labels, but no
   provider is accepted as complete in R2. Preserve current-snapshot EastMoney
   execution, Baostock historical ST backfill, and derived bar-gap suspension
   as distinct observed routes. Same-PK source disagreement is `DATA_CONFLICT`
   and fails closed; there is no first-non-null precedence. Provider selection,
   historical coverage proof, and implementation remain R4 work.
2. **Turnover:** define the thin project extension schema and semantic without
   selecting a provider: PK `(symbol, trade_date)`, `turnover_rate` in
   percentage points (for example 3.25 means 3.25%), explicit
   `turnover_source`, `turnover_semantic`, coverage, `source`, `data_version`,
   and `fetched_at`; missing stays null and surfaces `TURNOVER_PARTIAL`. Provider
   selection, ingestion, and reconciliation belong to R4.

If exact upstream source contradicts either rule, stop with
`DESIGN_DECISION_REQUIRED`; do not change the Spec inside R2.

### 2.4 Offline config verification

Before the root exists, run only:

```bash
uv run cne config validate --config config/cnequity.toml
uv run cne doctor --config config/cnequity.toml --json
```

`config validate` is configuration-only. With a nonexistent root, `doctor`
must only report that the root does not yet exist; it must not create it.
Capture a before/after existence check.

### 2.5 Commit Task 2

```text
docs: freeze R2 CNEquity baseline contracts
```

## Task 3 — Initialize the clean authoritative layout

### 3.1 Exact write authorization boundary

This reviewed plan authorizes creation and writes only under:

```text
/Users/luke808/AI/local-a-share-data-service-data
```

It does not authorize writes to any legacy root. Immediately before execution,
reconfirm the target is absent, the parent is writable, disk space is adequate,
and no path component resolves through a symlink into the repository or a
legacy root. Stop if any check fails.

### 3.2 Initialize layout only

Run exactly:

```bash
uv run cne init --config config/cnequity.toml --layout-only
```

Do not omit `--layout-only`. Expected writes are directories, an empty-schema
SQLite manifest using WAL mode, and a DuckDB file/views. No ingestion phase,
network request, market-data row, staging batch, or published state is allowed.

### 3.3 Verify zero-data baseline

Implement `tools/verify_r2_baseline.py` as a bounded verifier for the new root.
It may read/open only the new target root. It must prove and print JSON for:

- expected directory layout;
- config and lock SHA-256;
- manifest schema exists and ingestion run/batch counts are zero;
- DuckDB file exists and has no market-data rows;
- no Parquet file exists under staging/curated/derived/raw;
- no `latest_good_as_of` or published batch exists;
- no path escapes the target root;
- package version and locked Git SHA match the contract;
- installed `direct_url.json` proves the immutable Git commit rather than an
  editable, local-path, or PyPI-only origin.

Tests must use temporary roots and must not reference or inspect legacy paths.
The verifier must not create, repair, checkpoint, vacuum, compact, or delete.

### 3.4 Doctor and sidecar evidence

Run `cne doctor --json` once against the initialized target. Pinned upstream
implements writability checking by creating and removing `.cne_write_probe`;
this plan explicitly authorizes that temporary probe only inside the new target.
Record any SQLite `manifest.db-wal`/`manifest.db-shm` files as expected writable
runtime state, not as a legacy-root incident. Do not delete them manually.

### 3.5 Commit Task 3

Commit only verifier, tests, and repository-local contract updates:

```text
feat: establish clean CNEquity baseline layout
```

The external data root is never added to Git.

## Task 4 — R2 author verification and review publication

### 4.1 Targeted verification

Run:

```bash
uv sync --frozen
uv run cne --version
uv run cne config validate --config config/cnequity.toml
uv run cne doctor --config config/cnequity.toml --json
uv run python tools/verify_r2_baseline.py --config config/cnequity.toml
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider \
  tests/test_r2_baseline_contract.py tests/test_project_docs_contract.py
git diff --check
```

Also verify no Parquet/market-data artifact exists in the target root and no
network/update command appears in the executed command log.

### 4.2 Author report

Create `reports/audits/R2_CNEQUITY_BASELINE_AUTHOR_REPORT.md` containing:

```text
AUTHOR_STATUS
SPEC_VERSION
BASE_HEAD
HEAD_BEFORE_FINAL_COMMIT
UPSTREAM_CNEQUITY
PYTHON_RUNTIME
LOCK_STATUS
CONFIG_STATUS
DATA_ROOT
LAYOUT_STATUS
ZERO_MARKET_DATA
TRADING_STATUS_CONTRACT
TURNOVER_CONTRACT
TESTS
BLOCKERS
PROHIBITIONS_CONFIRMED
REPORT_PATH
```

Author `PASS` is not `AUDIT_PASS`.

### 4.3 Conservative state handoff

Update `docs/PROJECT_STATE.md` only after all verification passes:

- keep `CURRENT_PHASE: R2 — CNEQUITY BASELINE`;
- set `R2_EXECUTION: AUTHOR_VERIFICATION_COMPLETE`;
- record exact runtime, config, and initialized data-root evidence;
- keep `LATEST_GOOD_AS_OF: NOT_PUBLISHED` and every market dataset
  `NOT_BUILT`/carry-forward state;
- set next action to independent audit of the exact pushed R2 commit;
- explicitly prohibit R3 until `R2 AUDIT_PASS`.

Update the docs contract test accordingly.

### 4.4 Final commit and push

Stage only the planned R2 repository files. Commit:

```text
build: finalize R2 CNEquity baseline
```

Push a non-force review branch. Verify local and remote 40-character SHAs are
identical. Do not update main and do not execute R3.

## Exit gate

R2 author handoff may be published only when all are true:

1. Exact CNEquity Git SHA and all dependencies are locked reproducibly.
2. CPython 3.12 runtime and CNEquity 0.7.2 are verified.
3. Config is valid, local-query safe, secret-free, and macOS-safe.
4. The new authoritative root exists outside Git and legacy roots.
5. Layout/manifest/DuckDB exist with zero ingestion runs and zero market data.
6. Trading-status and turnover baseline contracts are explicit and fail-closed.
7. No legacy root was accessed or modified during R2.
8. Targeted tests, verifier, doctor, config validation, and diff checks pass.
9. Exact review commit is pushed without force.
10. `PROJECT_STATE` remains R2 pending independent audit.

Only an independent `AUDIT_PASS` of the exact pushed R2 commit may authorize an
administrative R2→R3 state transition. This plan does not authorize R3.
