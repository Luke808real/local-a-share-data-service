# R4A7 PRECLOSE RESUMABLE FULL EXTRACTION — V01 (author report)

DATE: 2026-08-23
BRANCH: codex/r4a7-preclose-resumable-full-extraction-v01
BASE_HEAD: 549e19f4b6ff5df92ac477d9d739481ef69296db
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

## AUTHOR_STATUS

```text
AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
FULL_EXTRACTION_EXECUTED=false
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false
```

Implementation-only task. No real full-market BaoStock execution, no
5456-symbol provider fetch, no automatic promotion.

## BASE_HEAD / HEAD

```text
BASE_HEAD=549e19f4b6ff5df92ac477d9d739481ef69296db
HEAD=SELF — commit containing this file
REMOTE_HEAD=exact SHA returned after non-force push
```

## FILES_CHANGED

```text
A src/ashare_data/r4a7_preclose_full_extraction.py
A tests/test_r4a7_preclose_full_extraction.py
A reports/implementation/R4A7_PRECLOSE_RESUMABLE_FULL_EXTRACTION_V01.md
```

No other production file modified. No provider network call in this task.

## DESIGN

Unit = symbol. Every unit execution reuses the audited bounded adapter
(`run_bounded_adapter`) as the ONLY execution primitive, preserving its
exact REAL 40-char SHA authority, BaoStock field identity, identity/window
gates, global provider duplicate gate, and quality-gate fail-closed
semantics. No looser reimplementation.

```text
STAGING_BOUNDARY = <staging_root>/units/<symbol>.parquet (formal rows only)
MANIFEST        = <staging_root>/manifest.json (atomic tmp+rename)
```

Staging is generation-bounded; canonical datasets are never mutated.

## FULL_PLAN_IDENTITY

Derived read-only from the frozen 5456-symbol authoritative identity
(CURATED_DAILY_BARS_UNIQUE_SYMBOLS, FAIL CLOSED on drift against
formal_identity_hash):

```text
FULL_SYMBOL_N=5456
FULL_SYMBOL_HASH=2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f
FULL_QUERY_WINDOW_N=60016
FULL_QUERY_PLAN_HASH=9773875fbae9494bc1d9477cd18633dbccb92112733d9a1077fc3a43bcc38a60
```

Same frozen inputs -> identical hashes (verified by building twice).
Query windows follow the frozen contract (fields=date,code,preclose,
tradestatus; frequency=d; adjustflag=3; per (symbol,year) window).

## CHECKPOINT_SCHEMA

```text
manifest.json:
  schema_version: R4A7_PRECLOSE_V01
  contract: full executable identity (AS_OF, WINDOW_START, source,
            source_version, query_contract_version, fields/frequency/
            adjustflag, CNEQUITY_PIN, identity n/hash, adapter authority
            SHA, FULL_QUERY_PLAN_HASH)
  units: {symbol: receipt | {STATE: FAILED, error}}

unit receipt (COMPLETE only):
  STATE=COMPLETE
  symbol, unit_query_window_n, unit_query_plan_hash
  adapter_version, REQUIRED_ROW_N, FORMAL_FACT_ROW_N, MISSING_REQUIRED_N,
  PROVIDER_SUSPENDED_SUPERSET_N, UNEXPECTED/UNKNOWN/IDENTITY/WINDOW_SCOPE/
  DUPLICATE/POST_ASOF/INVALID counts, FORMAL_FACT_HASH, formal_path,
  contract, completion_utc
```

UNKNOWN != COMPLETE. An existing output file alone is never proof of
completion.

## RESUME_RULE

```text
Skip a unit ONLY when:
  receipt STATE == COMPLETE
  AND symbol matches
  AND receipt.contract == current contract identity (exact)
  AND formal parquet exists and is readable
  AND read-back FORMAL_FACT_HASH == receipt hash
  AND read-back row count == receipt FORMAL_FACT_ROW_N
otherwise: rerun (PENDING), or stay FAILED (no silent retry).
```

Contract drift (different AS_OF, different adapter SHA, different plan
hash) -> CHECKPOINT_CONTRACT_DRIFT, checkpoint cannot be reused (fail
closed). Corrupted manifest -> CHECKPOINT_CORRUPT.

## ATOMIC_COMPLETION_RULE

```text
unit becomes COMPLETE only after:
  provider normalized by audited adapter
  quality gates pass
  formal output persisted (tmp -> rename)
  read-back integrity verified (rows + content hash)
  receipt persisted (manifest atomic write)
A crash before final receipt leaves the unit PENDING/FAILED, never
COMPLETE. Partial/temp artifacts are never authoritative.
```

## FAILURE_SEMANTICS

```text
login failure / error_code / field identity mismatch / query-window
identity mismatch / duplicate provider PK / identity failure / post-ASOF /
unexpected traded / unknown tradestatus / invalid preclose / formal hash
mismatch / checkpoint corruption
-> relevant unit FAILED or run STOPPED; no silent retry; no fallback
provider; no conversion of failed/unknown units into COMPLETE.
```

## FINAL AGGREGATION

`aggregate_full_run` derives REQUIRED_ROW_N / FORMAL_FACT_ROW_N /
MISSING_REQUIRED_N / PROVIDER_SUSPENDED_SUPERSET_N / UNEXPECTED_TRADED_N /
TRADESTATUS_UNKNOWN_N / IDENTITY_FAILURE_N / WINDOW_SCOPE_FAILURE_N /
DUPLICATE_N / POST_ASOF_N / INVALID_PRECLOSE_N / FULL_FORMAL_FACT_HASH,
plus WINDOW_BOUNDARY_*, FROZEN_OFFICIAL_SENTINEL_*, CLEAN_NORMAL_* when
the frozen classification context is supplied. It may calculate
PRECLOSE_COMPLETE_CANDIDATE but never promotes state:

```text
PRECLOSE_COMPLETE=false   (always, this task)
FULL_MARKET_AUTHORIZED=false
```

## TARGETED_TESTS

```text
tests/test_r4a7_preclose_full_extraction.py = 9 passed
A  deterministic plan twice -> identical hashes
B  resume simulation: completed valid unit skips; incomplete reruns
B2 corrupted receipt -> CHECKPOINT_CORRUPT fail closed
B3 tampered output -> read-back hash mismatch -> not reusable
C  interrupted write -> no false COMPLETE (FAILED unit, resume reruns)
D  provider error -> no silent retry (exactly 1 attempt, unit FAILED)
E  contract drift (AS_OF / adapter SHA) -> CHECKPOINT_CONTRACT_DRIFT
F  R4A6 24-symbol pilot -> identical QUERY_PLAN_HASH=1228af76... golden
   (plan + formal-hash determinism, no network)
aggregate: counts / coverage / candidate stays false / no promotion
```

Existing R4A5.2.1 adapter targeted tests still pass (64): combined
`test_r4a_preclose_bounded_adapter.py + test_r4a7...` = 73 passed.

## FULL_SUITE

Not run in this task (implementation + targeted + bounded dry-run scope).
No provider network call was made.

## REAL_ROOT_BOUNDED_DRY_RUN

Read-only plan derivation against the real root (no network, no write):

```text
FULL_SYMBOL_N=5456
FULL_SYMBOL_HASH=2b1e7202... (matches frozen identity)
FULL_QUERY_WINDOW_N=60016
FULL_QUERY_PLAN_HASH=9773875fbae9494bc1d9477cd18633dbccb92112733d9a1077fc3a43bcc38a60
determinism=OK (identical on second build)
dry-run with empty staging -> DRY_RUN_OK, PENDING_N=5456, COMPLETE_N=0
NETWORK_PROVIDER_DATA_FETCH=NO
MARKET_DATA_WRITE=NO
```

## SAFETY

```text
NETWORK_PROVIDER_DATA_FETCH=NO
MARKET_DATA_WRITE=NO
FULL_EXTRACTION_EXECUTED=false
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false
```

## BOUNDED_NEXT_ACTION

Sol independent audit of the exact pushed commit; upon code-audit approval
the resumable orchestrator may be exercised in a bounded real mode under
the audited gates before any full-universe execution decision.
