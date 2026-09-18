# CNEquity 0.9.0 upgrade execution report

STATUS: **PASS_WITH_NON_BLOCKING_FINDINGS**. Author validation only; independent review of the pushed commit is still required. No merge or production runtime cutover was performed.

## Identity and reproducibility

Base branch: `codex/r3-incremental-hardening-daily-facts-phase1-v01`. Local HEAD and freshly fetched remote tracking HEAD both matched `b8ec628404d5fa816c56c9258fcbe947f9acf887` before the upgrade branch was created. Upgrade branch: `codex/cnequity-v090-upgrade-v01`.

Production remains CNEquity **0.8.0**, commit `d453853da766b3ba3e44489c0fb6e0089243fa25`, with patches 0001–0003. The isolated `.venv-cne090` reports **0.9.0** with direct_url commit `ca5c568f52a4cc1fad8bd812c3c406802c39d2fc`. See [runtime evidence](cnequity_v090_evidence/runtime.json). The requested SHA is used despite the upstream tag discrepancy listed below; neither floating main nor the excluded upstream HEAD was used.

`FINAL_HEAD` means the commit containing this report; `REMOTE_HEAD` means `refs/heads/codex/cnequity-v090-upgrade-v01` on origin. These are explicit resolvable Git references in the JSON because a commit cannot contain its own literal SHA. Resolve at the reviewed checkout with `git rev-parse HEAD` and `git ls-remote origin refs/heads/codex/cnequity-v090-upgrade-v01`; the execution handoff supplies the exact matching post-push hashes. No earlier validation commit is mislabeled as the final commit.

`pyproject.toml` and `uv.lock` pin the exact revision. An optional `query-test` dependency group pins MCP 1.29.1 for the full test environment without moving MCP into writer runtime dependencies. Installation and ordered dry-run/application commands are in [patch README](../../patches/cnequity/README.md). Use a pristine isolated environment; ordinary uv installation alone does not apply ASL patches. Production `.venv` is unchanged.

## Patch audit

| Patch | Result | Pristine evidence and preserved behavior |
|---|---|---|
| 0001 | STILL_REQUIRED | Pristine fields omit f8; schema/unit contract omit turnover_rate. Patch requests f8 and retains Float64 nullable turnover in PERCENT; missing values remain null. |
| 0002 | STILL_REQUIRED | Pristine lacks f124 snapshot session guard. Patched adapter validates payload timestamps against target date before stamping rows. |
| 0003 | STILL_REQUIRED | Required with 0002: cutoff is target session date plus settled time in Asia/Shanghai, allowing a settled previous session after midnight while rejecting future sessions. |

Each patch passed `patch --dry-run` then actual application in sequence against pristine 0.9.0. A second pristine replay reproduced the installed four touched files byte-for-byte. Original patch files were not rewritten. [Replay hashes](cnequity_v090_evidence/patch-replay.json) and individual dry/apply logs are committed. Existing upstream contract artifacts were preserved.

## Contract, config and API compatibility

PATCHED_CONTRACT_RESULT: PASS. CONFIG_COMPATIBILITY_RESULT: PASS. API_COMPATIBILITY_RESULT: PASS.

Native registry validation and config validation return no errors. The old patched/new patched comparison shows **29 metadata changes**, all `pit_quality: strict -> not_applicable`, and **zero storage schema changes**. `pit` and `pit_grade`, units, primary keys and dataset schemas remain compatible. ASL has no old-vocabulary consumer requiring repair: **NO_ASL_CONSUMER_IMPACT**. No history was refetched or lake rebuilt for this metadata change. The `version: 1` field in contract evidence denotes the contract format, not the package version.

[API comparison](cnequity_v090_evidence/api-comparison.json) records signatures and source hashes for fetch_daily_bars, normalize_with_source, load_config, StagingWriter, write_parquet_atomic, Manifest, JobEngine, clist, em_auth and BaoStock _session. Existing call shapes remain accepted. Manifest adds an optional replacement_pending argument; current calls remain compatible. Private BaoStock session behavior is retained. Native EastMoney configuration has additional options, but direct fallback stays false and THS official backfill stays false. TDX pacing stays 0.1 seconds, concurrency 1; mock data stays disabled.

Behavioral checks go beyond imports: native TDX returns Polars data; normalization keeps v2 share volume without a second multiplier; missing required columns raise schema errors; staging uses the native run/batch layout; atomic write, failed/running/success manifest transitions, successful reuse and compact/read behavior are exercised. Live native TDX data for Sept 17 exactly matches three existing published samples, including OHLC, volume and amount. The R3 targeted tests exercise its actual fetch/normalize/StagingWriter/Manifest path without altering its production-root guard. Snapshot exception behavior is verified both by unit tests and real-payload negative probes.

## Existing lake and MCP read-only validation

EXISTING_LAKE_READ_ONLY_RESULT: PASS. Production remains published through **2026-09-17**, 2603 daily files, zero pending daily files, Facts schema ASL_DAILY_FACTS_V02. [Read-only evidence](cnequity_v090_evidence/readonly.json) records LocalQuery and actual in-process MCP status/instrument/bars/latest/facts calls. The process was denied both outbound network and production writes by macOS sandbox-exec.

Samples include normal 002580.SZ, ST 000010.SZ, suspended 600301.SH, ST+suspended 000016.SZ, and corporate-action 002311.SZ. Old V1 Sept 9 and V02 Sept 17 read together correctly. Symbol/date predicates, DuckDB union_by_name, DOUBLE numeric types and sparse nullable metadata pass. Native 0.9.0 reader separately reads existing daily_bars, trading_status, valuation_metrics and corporate_actions. This is local MCP compatibility validation, not a new external ChatGPT connector deployment.

PRODUCTION_DATA_MUTATED = false. PRODUCTION_AUTHORITY_CHANGED = false.

R3_MANIFEST_BEFORE = R3_MANIFEST_AFTER = `e5364e8237e0776b139477f2284b93587bee8b0b5232b0122c583a2b28600a2b`.

FACTS_MANIFEST_BEFORE = FACTS_MANIFEST_AFTER = `6126f20103eae277f5e23f95f2e314a26f8b6c90b37cfb3e292e01f3dc36710b`.

[Integrity evidence](cnequity_v090_evidence/production-integrity.json) compares every regular-file path, size and mtime_ns: **146647 lake files**, **6312 production environment files**, zero differences. Authority pointers and their referenced plan/receipt also have identical SHA256. Sandbox write denial was active for tests and provider canaries. This is an inventory plus authority-content check, not a claim to have independently hashed every raw lake payload. The inventory fingerprint is `66120ebb2e694aacc346a3448ce3a0a18c6c39d2a1d5a553051b5b76958c593c` before and after.

## Test results and repairs

| Suite | Tests | PASS | FAIL | SKIP |
|---|---:|---:|---:|---:|
| TARGETED_TESTS, exact ten requested files | 188 | 188 | 0 | 0 |
| FULL_TEST_SUITE, pytest -q | 275 | 275 | 0 | 0 |

The full suite passed again after final canary repair. [Counts](cnequity_v090_evidence/tests.json), [targeted log](cnequity_v090_evidence/targeted-final.log), and [full log](cnequity_v090_evidence/full-final.log) are committed. One Starlette httpx TestClient deprecation warning is non-blocking.

Two strict 0.8 pin assertions were updated to the exact 0.9 version/commit; they were not loosened. The user-named, previously untracked test_cnequity_v080_daily_reuse.py had a fixture lifecycle error reproducible under both 0.8 and 0.9: finish_batch only finalizes a running batch, while the fixture attempted to turn a failed batch directly into success. The repair asserts that terminal failure stays failed, explicitly restarts the batch, and then finalizes success. The repaired test passes under both runtimes and is included as upgrade-related work. Other preexisting untracked files remain untouched. No tests were deleted, assertions weakened, or quality gates disabled.

## Real provider canary

PROVIDER_CANARY_RESULT: PASS. After offline suites passed, three symbols were fetched for the previous complete session Sept 17 and compared exactly with published rows. The settled Sept 18 canary in `.runtime/cne090/final-canary/lake` then produced **14 daily bars, 14 trading status rows, 10 valuation rows, and 30 single-day corporate-action events**. Native JobEngine, Manifest, StagingWriter and compact succeeded. Source/data_version, symbols, dates, finite OHLC/volume/amount, nullable turnover, risk_warning and trading status were checked. No duplicate primary keys or future dates were accepted. [Receipt](cnequity_v090_evidence/provider-canary.json) and [raw public sample](cnequity_v090_evidence/provider-live-page.json) are committed.

The valuation transport is explicitly bounded: fetch one real ten-row clist page and replay those unchanged payload rows through the native valuation adapter and guard. Only the pager is substituted; normalization, schema, snapshot guard, provenance and compact remain native. This does not certify whole-market turnover or coverage. A cloned all-zero payload is rejected, as are wrong-session and future-session payloads. The earlier real intraday probe was correctly rejected as unsettled. Production snapshots were never mislabeled or republished.

Initial settled canary inspection caught a test scope error: native corporate_actions daily processing intentionally reconciles a 30-day tail, so a watermark did not restrict it to one day. That isolated attempt fetched 307 recent event rows and failed the strict exact-date assertion. The final script calls the native exact-date corporate_actions adapter, validates native schema/provenance, and stages/compacts via native Manifest/JobEngine. It retains the exact-date assertion and does not modify the registry lookback or turn off quality gates. The daily corporate-action orchestration itself is therefore not certified as a one-day operation. See [repair evidence](cnequity_v090_evidence/canary-scope-repair.json). No full-history acquisition or per-symbol full-market sweep was performed.

The primary EastMoney clist host disconnected; native push2delay host succeeded. No ASL provider retry, normalization or failover replacement was added.

## Ownership review (analysis only)

A. Retain LocalQuery, read-only MCP, ASL Market Facts/reference-price semantics, frozen eligible scope, and publication/authority safety boundaries. CNEquity decides what data represents; ASL decides how local data is consumed. No strategy, trading, backtest, B1/B2 or scoring code was added.

B. Gradually return provider acquisition, pacing/retry, source failover, basic normalization, schema/PK quality, revision/snapshot infrastructure and foundational provenance to CNEquity. R3-specific acquisition/bisection logic can shrink only after native output demonstrably satisfies its frozen publication requirements; this upgrade does not perform that refactor.

C. `src/ashare_data/cnequity_bridge.py` still depends on private `cnequity.adapters.baostock._session` for existing functionality. It remains compatible and is retained now. Retire it once curated CNEquity datasets cover the required historical/semantic contract, so ASL no longer invokes the private provider API. Legacy V1 acquisition/maintenance paths can retire after V02 lifecycle migration; current maintenance still contains V1 assumptions and must not be mistaken for V02 publication certification.

## Remaining findings and handoff

- Requested immutable ca5c568f commit reports version 0.9.0; observed remote v0.9.0 tag peels to 44601b17c448de68c04f7cf9390f33e8881b5cec. User exact SHA was honored. No floating main or excluded HEAD used.
- All three local CNEquity patches remain required and must be applied after a pristine install; uv sync alone does not install patches.
- Provider canary is scoped: one genuine ten-row valuation page is replayed through the native adapter; it does not certify whole-market snapshot coverage. Corporate actions use the native exact-date feed rather than the daily step 30-day reconciliation window.
- Native EastMoney primary clist endpoint failed; native push2delay alternate succeeded. No direct fallback or custom retry policy enabled.
- Existing daily coverage remains PARTIAL, FACTS_READY=false and PRECLOSE_COMPLETE=false. Sept 10-16 Facts gap is unchanged; no readiness promotion.
- Preexisting daily maintenance V1 discovery/acquisition path is not migrated to the V02 publication lifecycle. This upgrade does not certify using that legacy orchestration to publish V02.
- One Starlette TestClient httpx deprecation warning; zero skipped tests.
- Production writer/MCP environments were intentionally not switched or restarted. Independent audit and merge/cutover remain pending.

FINAL_GIT_STATUS: tracked working tree clean after commit; unrelated untracked files retained and excluded. Push only the upgrade branch, verify local and remote HEAD equality, and do not merge. Literal final hashes are provided in the execution handoff.

## Files changed

- `.gitignore`
- `docs/contracts/R2_CNEQUITY_BASELINE_CONTRACT.md`
- `docs/plans/CNEQUITY_V090_UPGRADE_V01.md`
- `patches/cnequity/README.md`
- `pyproject.toml`
- `reports/implementation/CNEQUITY_V090_UPGRADE_EXECUTION_REPORT.json`
- `reports/implementation/CNEQUITY_V090_UPGRADE_EXECUTION_REPORT.md`
- `reports/implementation/cnequity_v090_evidence/api-comparison.json`
- `reports/implementation/cnequity_v090_evidence/canary-scope-repair.json`
- `reports/implementation/cnequity_v090_evidence/contract-config-result.json`
- `reports/implementation/cnequity_v090_evidence/contract-diff.json`
- `reports/implementation/cnequity_v090_evidence/full-final.log`
- `reports/implementation/cnequity_v090_evidence/initial-failures.json`
- `reports/implementation/cnequity_v090_evidence/intraday-guard.json`
- `reports/implementation/cnequity_v090_evidence/native-reader.json`
- `reports/implementation/cnequity_v090_evidence/patch-0001-apply.log`
- `reports/implementation/cnequity_v090_evidence/patch-0001-dry.log`
- `reports/implementation/cnequity_v090_evidence/patch-0002-apply.log`
- `reports/implementation/cnequity_v090_evidence/patch-0002-dry.log`
- `reports/implementation/cnequity_v090_evidence/patch-0003-apply.log`
- `reports/implementation/cnequity_v090_evidence/patch-0003-dry.log`
- `reports/implementation/cnequity_v090_evidence/patch-replay.json`
- `reports/implementation/cnequity_v090_evidence/production-integrity.json`
- `reports/implementation/cnequity_v090_evidence/provider-canary.json`
- `reports/implementation/cnequity_v090_evidence/provider-canary.log`
- `reports/implementation/cnequity_v090_evidence/provider-live-page.json`
- `reports/implementation/cnequity_v090_evidence/readonly.json`
- `reports/implementation/cnequity_v090_evidence/runtime.json`
- `reports/implementation/cnequity_v090_evidence/targeted-final.log`
- `reports/implementation/cnequity_v090_evidence/tdx-previous-canary.json`
- `reports/implementation/cnequity_v090_evidence/tests.json`
- `tests/test_cnequity_v080_daily_reuse.py`
- `tests/test_cnequity_v090_compatibility.py`
- `tests/test_daily_facts_v02.py`
- `tests/test_r2_baseline_contract.py`
- `tools/audits/cnequity_v090_canary.py`
- `tools/audits/cnequity_v090_readonly.py`
- `tools/verify_r2_baseline.py`
- `uv.lock`
