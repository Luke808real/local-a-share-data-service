# R2 CNEquity Baseline Author Report

## AUTHOR_STATUS

AUTHOR_STATUS: PASS — author-only verification. This is not AUDIT_PASS, is not
an independent audit, and does not authorize R3.

## SPEC_VERSION

V1.0 FROZEN.

## BASE_HEAD

`a21614f825e07adb69cd0269ee786bdf0ad0f5c1`

## HEAD_BEFORE_FINAL_COMMIT

`0263ecb7b6c446b47bc082e86ca72379a8c3eca2`

## UPSTREAM_CNEQUITY

The authoritative runtime is `rootSunc/CNEquity` release `v0.7.2`, pinned to
Git commit `a18ee0484dfb0801650175471724def3228b8a17`. Installed distribution
metadata and `cnequity.__version__` are `0.7.2`; installed `direct_url.json`
records VCS Git repository `https://github.com/rootSunc/CNEquity.git` and the
same immutable `commit_id`.

## PYTHON_RUNTIME

CPython 3.12.13, selected by `.python-version` (`3.12`).

## LOCK_STATUS

ASL-owned `uv.lock` SHA-256:
`5f233fa9434624391c06e56a4596edfd52c1ec596d66688753b78f424dd571ac`.
The frozen offline lock check passed, and the lock hash was unchanged across
the final verification sequence.

## CONFIG_STATUS

`config/cnequity.toml` SHA-256:
`fac5abd136cb2ae00c07d7ca408eb1d47eed69c26c3547a0547ef9d214063fb5`.
Frozen offline `cne config validate` passed. The configuration is scoped to the
V1 foundation and does not select a provider or authorize collection.

## DATA_ROOT

The sole authoritative root is
`/Users/luke808/AI/local-a-share-data-service-data`. It is outside the Git
worktree and was initialized only through the authorized layout-only action.

## LAYOUT_STATUS

The root has exactly 18 entries: the documented layout directories plus
`meta/manifest.db` (28,672 B) and `duckdb/cnequity.duckdb` (274,432 B). The
verified tree snapshot SHA-256 is
`ddcf9dc509b6bfb0cea8bd27511360ba6d1b4151b4a745f3e0fcb230ecd43dd5`.
No manifest, DuckDB, or other writable sidecars were observed.

## ZERO_MARKET_DATA

The manifest has `ingestion_runs=0` and `ingestion_batches=0`. DuckDB has 43 views and 0 physical tables. The verifier found no Parquet, CSV, JSON, cache,
staging payload, source snapshot, on-demand payload, unknown data file,
published state, or latest-good state. The baseline remains zero market data.

## TRADING_STATUS_CONTRACT

The strict point-in-time `(symbol, trade_date)` mapping contract is frozen
fail-closed: `normal`/`NORMAL`, `suspended`/`SUSPENDED`, `st`/`ST`,
`*st`/`STAR_ST`, and unknown or `DELISTING` as `UNKNOWN` until an explicit R4
rule. `is_trading` is insufficient. Same-PK source disagreement is
`DATA_CONFLICT`. No provider, coverage claim, or implementation is accepted
before R4.

## TURNOVER_CONTRACT

The frozen future extension has PK `(symbol, trade_date)` and explicit
provenance/coverage fields. `turnover_rate` uses percentage points (`3.25`
means `3.25%`); missing remains null and `TURNOVER_PARTIAL`. Provider selection,
coverage proof, ingestion, and implementation remain R4 work.

## TESTS

The author verification sequence passed: frozen offline lock check, frozen
offline config validation, runtime version check, read-only baseline verifier,
and frozen offline R2 plus project-document contract tests. `git diff --check`
passed. The verifier's before/after snapshots are identical.

## BLOCKERS

None within author scope. Independent audit of the exact pushed final review
commit remains required before an R2 audit pass or any R3 work.

## PROHIBITIONS_CONFIRMED

No legacy root was accessed. Only the Task 3 layout-only initialization and
the authorized transient doctor probe wrote under the new target root; the
probe left no file. No market data, query, status, demo, run, backfill,
provider selection, or legacy migration was performed. The pinned
`on_demand.enabled=false` setting is not an enforceable network guard, so all
upstream query/MCP/live interfaces remain excluded until R8.

## REPORT_PATH

`reports/audits/R2_CNEQUITY_BASELINE_AUTHOR_REPORT.md`
