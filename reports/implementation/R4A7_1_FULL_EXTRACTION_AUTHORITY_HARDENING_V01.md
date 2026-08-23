# R4A7.1 FULL EXTRACTION AUTHORITY HARDENING — V01 (author report)

DATE: 2026-08-24
BRANCH: codex/r4a7-1-full-extraction-authority-hardening-v01
BASE_HEAD: 4910a4639f5170d11bd93c0508bdade098083138
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

## AUTHOR_STATUS

```text
AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
FULL_EXTRACTION_EXECUTED=false
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false
```

Minimal hardening patch only. No real BaoStock full extraction, no
market-data write outside tmp fixtures, no R4B/R4C/R4D, no
strategy/B1/B2/Forward/TradePlan.

## BASE_HEAD / HEAD

```text
BASE_HEAD=4910a4639f5170d11bd93c0508bdade098083138
HEAD=SELF — commit containing this file
REMOTE_HEAD=exact SHA returned after non-force push
```

## FILES_CHANGED

```text
M src/ashare_data/r4a7_preclose_full_extraction.py
M tests/test_r4a7_preclose_full_extraction.py
A reports/implementation/R4A7_1_FULL_EXTRACTION_AUTHORITY_HARDENING_V01.md
```

## REAL_RUNTIME_AUTHORITY_TEST

`run_full_extraction(execution_context=REAL)` now runs the frozen adapter
authority gate BEFORE any checkpoint read/reuse or provider fetch:

```text
adapter_version == expected_adapter_sha == runtime_adapter_sha
AND every value is a canonical 40-char lowercase hex Git SHA
```

Runtime-only drift (`adapter=A, expected=A, runtime=B`) fails closed even
against a fully COMPLETE checkpoint -> ADAPTER_AUTHORITY_FAILED_BEFORE_RESUME
with NETWORK_PROVIDER_DATA_FETCH=NO and zero provider calls; SKIPPED_N is
never treated as successful resume.

## REAL_IDENTITY_INJECTION_TEST

```text
REAL non-dry-run: identity injection FORBIDDEN
  -> REAL_IDENTITY_INJECTION_FORBIDDEN
REAL identity loaded only via load_expected_identity
  (FORMAL_IDENTITY_N=5456, FORMAL_IDENTITY_HASH=
   2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f)
  drift -> REAL_IDENTITY_DRIFT
OFFLINE_TEST keeps injectable small identities for tests
```

A 1-symbol REAL run can therefore never produce COMPLETE_ALL_UNITS under a
contract claiming the 5456 identity.

## AGGREGATOR_INTEGRITY_TESTS

`aggregate_full_run` is hardened:

```text
REAL: expected universe derived from frozen authority (5456 / frozen hash),
      never from a caller-supplied subset; N!=5456 -> REAL_IDENTITY_N_MISMATCH
contract_exact: manifest contract must equal the exact frozen execution
      contract in REAL mode
every expected symbol: exactly one COMPLETE receipt passing the SAME
      resume integrity check (state, symbol, contract, parquet exists/
      readable, row count, content hash)
FAILED_N=0 and PENDING_N=0 required
any invalid/missing unit -> COVERAGE_COMPLETE=false and
      PRECLOSE_COMPLETE_CANDIDATE=false (missing formal files never ignored)
OFFLINE_TEST keeps the small fixture aggregation path
```

## WINDOW_BOUNDARY_CLEAN_NORMAL_TEST

The frozen WINDOW_BOUNDARY_EDGE key set is computed once from authoritative
R3 data and passed to BOTH `verify_window_boundary_rows(...)` and
`verify_clean_normal_parity(...)`. CLEAN_NORMAL excludes
WINDOW_BOUNDARY_EDGE. Regression: a listed-before-window symbol with no
authoritative pre-window predecessor has its first in-window row classified
EDGE and does NOT increase CLEAN_NORMAL_UNCOMPARED_N (2 comparable rows,
UNCOMPARED=0, NORMAL_FULL_PARITY_PASS=true).

## POSITIVE_CANDIDATE_TEST

Bounded 24-symbol offline fixture where every required gate genuinely
passes:

```text
COVERAGE_COMPLETE=true
24/24 WINDOW_BOUNDARY present+valid -> WINDOW_BOUNDARY_PASS=true
24/24 frozen sentinels exact -> FROZEN_OFFICIAL_SENTINEL_PASS=true
CLEAN_NORMAL: 48 comparable rows, UNCOMPARED=0, EXACT=48,
      NORMAL_FULL_PARITY_PASS=true
all blocking counts = 0
PRECLOSE_COMPLETE_CANDIDATE=true
PRECLOSE_COMPLETE=false          (never promoted)
FULL_MARKET_AUTHORIZED=false
```

## TARGETED_TESTS

```text
tests/test_r4a7_preclose_full_extraction.py = 19 passed
  (10 new R4A7.1 regressions A..I/I2 + 9 preserved R4A7 tests)
tests/test_r4a_preclose_bounded_adapter.py = 64 passed (preserved)
combined = 83 passed
```

Regression map:

```text
A  runtime-only SHA drift blocks even fully COMPLETE checkpoint (0 provider calls)
B  REAL non-dry-run injected identity fails
C  OFFLINE_TEST injected identity remains usable
D  subset symbols cannot make REAL aggregate COVERAGE_COMPLETE
E  missing COMPLETE parquet -> candidate false
F  tampered COMPLETE parquet -> candidate false
G  manifest contract drift -> resume integrity fail / candidate false
H  WINDOW_BOUNDARY_EDGE passed into CLEAN_NORMAL (no UNCOMPARED increase)
I  bounded positive full-validator fixture, all gates run
I2 all required gates genuinely PASS -> PRECLOSE_COMPLETE_CANDIDATE=true,
   PRECLOSE_COMPLETE=false
```

## NETWORK / WRITE BOUNDARY

```text
NETWORK_PROVIDER_DATA_FETCH=NO
MARKET_DATA_WRITE=NO
FULL_EXTRACTION_EXECUTED=false
```

## PRESERVED (no weakening)

atomic tmp->rename, readback verification, no silent retry, FAILED
semantics, audited run_bounded_adapter, exact field identity, global
duplicate gate, R4A0 prerequisite, query-plan determinism, staging-only
write boundary.

## BOUNDED_NEXT_ACTION

Sol independent audit of the exact pushed commit; upon code-audit approval
the resumable orchestrator may be exercised in bounded real mode under the
hardened authorities before any full-universe execution decision.
