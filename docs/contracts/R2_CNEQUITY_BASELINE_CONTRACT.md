# R2 CNEquity Baseline Contract

**SPEC_VERSION:** `V1.0 FROZEN`
**PHASE:** `R2 — CNEQUITY BASELINE`
**STATUS:** configuration and storage contract only; no market-data action

## Runtime authority

The sole upstream authority is `rootSunc/CNEquity`, tag/release `v0.7.2`, at
immutable Git commit `a18ee0484dfb0801650175471724def3228b8a17`:

```text
https://github.com/rootSunc/CNEquity.git
```

ASL uses CPython 3.12, selected by `.python-version`, with the service-owned
`uv.lock` as the full dependency authority. The current lock hash is SHA-256
`5f233fa9434624391c06e56a4596edfd52c1ec596d66688753b78f424dd571ac`.
Installed-package provenance is authoritative only when `direct_url.json`
records VCS `git`, the repository URL above, and that exact `commit_id`.
An editable checkout, local path, or PyPI-only origin is not an acceptable
runtime origin.

Every R2 receipt must record the SHA-256 config hash and lock hash, together
with the runtime version and pinned Git commit. A version string alone never
establishes the upstream identity.

## Authoritative root and layout

The only authorized authoritative root is:

```text
/Users/luke808/AI/local-a-share-data-service-data
```

It is outside the repository. R2 Task 2 records its configuration only and
does not create it.

At this pin, `init_data_layout()` code creates these directories:

```text
staging/
curated/
derived/
raw/
meta/
meta/quality/findings/
meta/quality/source_diffs/
meta/source_snapshots/
meta/state/
meta/adj_factors_cache/
meta/seeds/
meta/on_demand/
duckdb/
backups/
```

It also initializes `meta/manifest.db` and
`duckdb/cnequity.duckdb`. Upstream prose additionally names `logs/`, but
pinned `init_data_layout()` does not create `logs/`; this difference is
recorded rather than normalized away.

`Manifest` selects SQLite `WAL` mode. `manifest.db-wal` and
`manifest.db-shm` are expected writable runtime sidecars while SQLite has an
active WAL connection; their presence is runtime state, not market data.
`ensure_duckdb_views()` opens the DuckDB path writable to create or replace
empty views. Thus the DuckDB file is a writable-layout artifact during
initialization, while the zero-data R2 baseline is view-only metadata with no
market-data rows.

## R2 zero-data and no-legacy-reuse invariants

`zero-market-data` is an R2 invariant: Task 2 creates no root, invokes no
layout initializer, opens no database, performs no query, and writes no
market-data, cache, staging payload, or source snapshot.

`no-legacy-reuse` is also an R2 invariant. No legacy row is an accepted
migration input, and no R1 `CROSSCHECK_ONLY` conclusion authorizes a copy,
normalization, provider choice, or data acquisition.

The following paths are excluded/non-authoritative inputs for R2:

```text
/Users/luke808/AI/asl-shared
/Users/luke808/AI/asl-r8-5m-lake
/Users/luke808/AI/V flash/data
```

They are boundary labels only in this contract. R2 does not read, list, stat,
open, hash, copy, modify, or otherwise access them.

The TOML names only future V1-required source/rate-limit blocks. It carries no
credential, secret, or network-routing setting. Enabling a future source block
does not select it as a complete or accepted provider for any unresolved R2
contract.

## Adjustment boundary

`RAW daily authoritative` implements Master Decision D007: stored daily prices
remain raw/unadjusted market facts. The configured `HFQ factor` source is
`sina`, and pinned CNEquity stores only `hfq` adjustment factors.

Master Decision D008 keeps the `QFQ/HFQ query derivation` boundary in the
query layer: HFQ prices derive from raw prices and the stored HFQ factor, while
QFQ prices derive from raw prices, the HFQ factor, and the symbol's latest HFQ
anchor. No adjusted-price series becomes a second authoritative stored price.
There is no factor/data execution in R2.

## Local-only query boundary

`on_demand.enabled=false` with an empty dataset list is defense in depth only;
it is not an enforceable network guard at this pinned upstream version.
Specifically, `cne query --dataset` ignores that flag and can fetch/cache data.

Therefore all upstream query, MCP, and live interfaces remain excluded until
R8. `cne mcp --live` is forbidden, as are all other `--live` paths. R8 must
provide and test an enforceable local-only adapter before any such interface
can enter the executable surface.

## Configuration boundary

The committed configuration fixes the absolute root, macOS-safe
`orchestrator.workers = 1`, bounded retries, paced TDX with
`allow_mock = false`, and `universe.default = "all_a"`. It uses only the
minimal V1 daily waves, no scheduler groups, and the pinned default init phase
sequence. Every configured init phase must be checked against pinned
`INIT_PHASE_STEPS`, and every expanded step against pinned `STEP_REGISTRY`;
upstream configuration validation alone is insufficient for that phase check.

Minute bars remain disabled, with scope `all` and frequencies exactly `5m` for
the future R5 contract. Trade ticks remain disabled. The configured DuckDB
path is `{data.root}/duckdb/cnequity.duckdb`, which resolves under the
authoritative root.

## Trading-status contract

The target PK `(symbol, trade_date)` has strict point-in-time date semantics.
The frozen V1 mapping is:

| Upstream value | V1 value |
| --- | --- |
| `normal` | `NORMAL` |
| `suspended` | `SUSPENDED` |
| `st` | `ST` |
| `*st` | `STAR_ST` |

Mapping shorthand: `normal` → `NORMAL`; `suspended` → `SUSPENDED`; `st` →
`ST`; `*st` → `STAR_ST`.

Unrecognized or missing values → `UNKNOWN`. `DELISTING` remains `UNKNOWN`
until an explicit R4 rule/source exists. `is_trading` alone never determines ST
or delisting.

Row-level `source` provenance outranks conflicting registry/catalog labels,
but it does not create a provider precedence order. Current-snapshot EastMoney
execution, Baostock historical ST backfill, and derived bar-gap suspension
remain distinct observed routes. A same-PK source disagreement is
`DATA_CONFLICT` and fails closed; there is no first-non-null precedence. No
provider is accepted as complete in R2. Provider selection, historical
coverage proof, and implementation remain R4 work.

## Turnover extension contract

The R2 contract defines a thin project extension with PK `(symbol, trade_date)`
and fields:

```text
symbol
trade_date
turnover_rate
turnover_source
turnover_semantic
coverage
source
data_version
fetched_at
```

The required field names include `source`, `data_version`, and `fetched_at`;
none may be inferred or omitted.

`turnover_rate` is expressed in percentage points: `3.25` means `3.25%`, not
the ratio `0.0325`. `turnover_source`, `turnover_semantic`, and `coverage` are
always explicit. Missing turnover remains null and must surface as
`TURNOVER_PARTIAL`; it is never coerced to zero or silently accepted.

No provider is selected by this contract and no turnover data is implemented
in R2. Provider selection, ingestion, and reconciliation belong to R4.
