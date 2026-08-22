# R4A4 PRECLOSE CANONICAL SOURCE CONTRACT — V01.2

DATE: 2026-08-21
BRANCH: codex/r4a4-preclose-source-contract-v01
BASE_HEAD: 1ee343ce9791ba8d1c128c93bf343022ad6e11b7
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

Task: `R4A4_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01_2`
Mode: `docs-only contract clarification` — **docs only**.
No code change, no provider fetch, no market-data write, no R4A execution.
Supersedes: V01.1 (commit e0b6c932). V01.2 adds the formal
`WINDOW_BOUNDARY_EDGE` classification and its explicit exclusion from
CLEAN_NORMAL parity scope, plus the window-boundary gate.

---

## 0. Status

```text
SOURCE_CONTRACT_STATUS=FROZEN_V01_2_PENDING_SOL_AUDIT
AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
PRECLOSE_SEMANTIC=EXCHANGE_DISPLAY_PRECLOSE
PRIMARY_CANONICAL_SOURCE=BAOSTOCK_HISTORY_K_PRECLOSE
SOURCE_TIER=T2_VALIDATED_PROVIDER_CANONICAL
EXCHANGE_PRIMARY_CLAIM=false
CORPORATE_ACTIONS_ROLE=VALIDATION_DIAGNOSTIC_ONLY
BAOSTOCK_RUNTIME_VERSION=0.9.3
CNEQUITY_PIN=a18ee0484dfb0801650175471724def3228b8a17
R4A_IMPLEMENTATION_READY_FOR_CODE=true
PRECLOSE_COMPLETE=false
FULL_MARKET_AUTHORIZED=false
MARKET_DATA_WRITE=NO
SECONDARY_AUTHORITY_SOURCES_N=0
SHARED_RAW_PROVIDER_EXTRACTION=DESIGN_OPTION
```

`R4A_IMPLEMENTATION_READY_FOR_CODE=true` is the author decision that a bounded
implementation-code task for the R4A preclose dataset may be designed and
executed under this frozen contract. It is not `PRECLOSE_COMPLETE`, is not an
authoritative audit, and does not authorize R4A full-market execution.

---

## 1. Source decision — unchanged

This version does not re-research the source. The following remain frozen:

```text
PRECLOSE_SEMANTIC=EXCHANGE_DISPLAY_PRECLOSE
PRIMARY_CANONICAL_SOURCE=BAOSTOCK_HISTORY_K_PRECLOSE
SOURCE_TIER=T2_VALIDATED_PROVIDER_CANONICAL
EXCHANGE_PRIMARY_CLAIM=false
CORPORATE_ACTIONS_ROLE=VALIDATION_DIAGNOSTIC_ONLY
```

NORMAL, IPO first-listing-day, EX_DATE, RESUMPTION, and SPECIAL all output the
same Market Fact `preclose`. Source-specific interpretation is carried only in
provenance / sentinel / validation, never in the fact itself.

---

## 2. Formal dataset row contract

```text
DATASET=preclose_facts
```

A row may be written to `preclose_facts` only if **all** of the following hold:

```text
(symbol, trade_date) in R3 actual-traded required keys
AND provider_tradestatus = 1
AND preclose finite positive
AND coverage_status = COVERED
```

The following classifications are **forbidden** in the formal dataset:

```text
PROVIDER_SUSPENDED_SUPERSET
MISSING_REQUIRED
UNEXPECTED_TRADED
TRADESTATUS_UNKNOWN
IDENTITY_FAILURE
POST_ASOF
SPECIAL_UNKNOWN
```

These may be written only to the **quality receipt / audit artifact**, never to
the canonical facts. The formal schema does not carry audit-row enum semantics
that could mix audit rows into canonical facts.

---

## 3. SPECIAL / resumption source rule

The old conflict is deleted:

```text
OBSOLETE: SPECIAL without exchange formula -> NULL preclose
```

New frozen rule:

```text
For any required actual-traded row:
  BaoStock tradestatus = 1
  AND valid finite positive preclose
  AND identity/AS_OF gate PASS
  -> canonical preclose observation
```

This applies uniformly to:

```text
NORMAL
IPO
EX_DATE
RESUMPTION
SPECIAL
```

It is not required that every SPECIAL row first resolves an exchange formula.
Official announcements and `corporate_actions` are `VALIDATION / SENTINEL /
DIAGNOSTIC` only.

```text
frozen official sentinel mismatches BaoStock  -> FAIL CLOSED
provider observation missing/invalid          -> FAIL CLOSED
                                               -> no NULL formal fact is produced
```

`NO_LIMIT_DAY != NULL_PRECLOSE`. A day with no price limit still has an
exchange display preclose. NULL preclose is not a valid formal fact under this
contract.

---

## 4. Source version / provenance

Frozen runtime, exact resolution from the repository `uv.lock`:

```text
BAOSTOCK_RUNTIME_VERSION=0.9.3
CNEQUITY_PIN=a18ee0484dfb0801650175471724def3228b8a17
QUERY_API=query_history_k_data_plus
QUERY_FIELDS=date,code,preclose,tradestatus
FREQUENCY=d
ADJUSTFLAG=3
```

Formal provenance fields for every canonical fact:

```text
source                   = BAOSTOCK_HISTORY_K_PRECLOSE
source_version           = baostock-0.9.3
adapter/code authority   = exact implementation commit (recorded in runtime receipt)
query_contract_version   = R4A_PRECLOSE_V01
fetched_at               = actual fetch time
```

---

## 5. Required row scope

```text
REQUIRED_KEY_AUTHORITY=R3 authoritative daily_bars ACTUAL_TRADED sessions
REQUIRED_PK=(symbol, trade_date)
FORMAL_ROW_SCOPE=only required keys receive formal preclose
```

Coverage classification:

```text
tradestatus=1           -> ELIGIBLE_REQUIRED_ROW_OBSERVATION -> formal preclose row
tradestatus=0 outside   -> PROVIDER_SUSPENDED_SUPERSET        -> audit only
                           (non-blocking, never enters preclose_facts)
tradestatus UNKNOWN     -> TRADESTATUS_UNKNOWN                -> FAIL CLOSED
```

Provider rows outside the required key set are classified by section 7.

---

## 6. Identity / AS_OF gate

Every formal preclose row must satisfy all of the following, otherwise the row
(and the extraction) fails:

```text
provider code exact
AND provider date exact
AND preclose finite AND positive
AND tradestatus exactly 1
AND trade_date <= AS_OF
```

```text
unexpected tradestatus=1 row   -> FAIL
missing required row           -> FAIL
duplicate (symbol, trade_date) -> FAIL
identity mismatch              -> FAIL
UNKNOWN != PASS
```

`AS_OF` is the inclusive freeze date `2026-08-17` for the current evidence
bound; production extraction uses `trade_date <= AS_OF`.

---

## 7. Extra provider rows

```text
PROVIDER_SUSPENDED_SUPERSET  tradestatus=0 outside required keys
                             -> audit only
                             -> non-blocking
                             -> never into preclose_facts

UNEXPECTED_TRADED             tradestatus=1 outside required keys
                             -> BLOCKER
                             -> never into preclose_facts

TRADESTATUS_UNKNOWN           tradestatus missing/unknown on any row
                             -> BLOCKER
                             -> never into preclose_facts
```

Only `PROVIDER_SUSPENDED_SUPERSET` is non-blocking. `UNEXPECTED_TRADED` and
`TRADESTATUS_UNKNOWN` are hard blockers (`FAIL CLOSED`). None of these rows is
written to `preclose_facts`.

---

## 8. Full NORMAL parity gate

Frozen (not just "bounded parity gates PASS"):

```text
NORMAL_FULL_PARITY_PASS=true   only if
  in the full extraction, for ALL CLEAN_NORMAL required rows:
    BaoStock preclose == previous effective local close
    (exact display equality)
```

`CLEAN_NORMAL` excludes:

```text
first listing day
corporate-action ex-date
resumption candidate
known special
window boundary edge
```

`WINDOW_BOUNDARY_EDGE` (frozen V01.2 classification):

```text
(symbol, trade_date) is the symbol's FIRST required actual-traded row inside
  R4 WINDOW_START..AS_OF
AND instrument.list_date < WINDOW_START
AND authoritative local R3 daily_bars has no predecessor before WINDOW_START
```

```text
WINDOW_BOUNDARY_EDGE is NOT part of CLEAN_NORMAL parity scope.
It remains a formal required preclose row:
  BaoStock tradestatus=1 + finite positive preclose + identity/ASOF pass
  -> canonical preclose
```

Window-boundary gate:

```text
WINDOW_BOUNDARY_REQUIRED_N
WINDOW_BOUNDARY_PRESENT_N
WINDOW_BOUNDARY_VALID_N
WINDOW_BOUNDARY_MISSING_N
WINDOW_BOUNDARY_INVALID_N

WINDOW_BOUNDARY_PASS=true only if
  PRESENT_N == REQUIRED_N
  AND VALID_N == REQUIRED_N
  AND MISSING_N == 0
  AND INVALID_N == 0
```

Previous-close parity for window-boundary rows is never proven from BaoStock
itself.

```text
any CLEAN_NORMAL mismatch -> PRECLOSE_COMPLETE=false
```

Current bounded evidence (frozen, reusable as an early sanity check):

```text
CLEAN_NORMAL_N=54756
CLEAN_NORMAL_EXACT_N=54756
CLEAN_NORMAL_MISMATCH_N=0
CLEAN_NORMAL_MAX_DIFF=0.0
```

---

## 9. Official sentinel gate

Frozen sentinels (must all be exact):

```text
20 official event rows
3 official IPO rows
000564.SZ 2018-07-20
```

```text
FROZEN_OFFICIAL_SENTINEL_PASS=true   only if all frozen sentinels are exact
```

Future sentinels may be appended; the frozen set above must stay satisfied.
It is not required to rebuild a formula for every SPECIAL / EX_DATE.

### 9.1 Frozen special case — 000564.SZ 2018-07-20

```text
SYMBOL=000564.SZ
TRADE_DATE=2018-07-20
CLASS=RESUMPTION_CANDIDATE / SPECIAL (suspension-resume)
PREVIOUS_EFFECTIVE_CLOSE=4.78
SUSPENDED_PERIOD_EX_DATE=2018-07-13
OFFICIAL_EX_PRICE=4.77
BAOSTOCK_RESUME_PRECLOSE=4.77
```

Provider evidence for the suspension period (all `tradestatus=0`
`PROVIDER_SUSPENDED_SUPERSET`): 2018-07-02 .. 2018-07-12 preclose=4.78;
2018-07-13 .. 2018-07-19 preclose=4.77; resume day 2018-07-20 preclose=4.77.

This row is frozen as `RESUMPTION_CANDIDATE` and is an exact official sentinel
under the sentinel gate. Future verifiers must not re-classify it as a NORMAL
parity mismatch.

---

## 10. Dataset schema

Canonical `preclose_facts` schema (formal rows only — COVERED):

```text
symbol            string   canonical symbol, e.g. 000001.SZ
trade_date        date     R3 required actual-traded session date
preclose          decimal  finite positive exchange-display preclose

source            string   BAOSTOCK_HISTORY_K_PRECLOSE
source_version    string   baostock-0.9.3
adapter_version   string   exact implementation commit (runtime receipt)
query_contract_version string R4A_PRECLOSE_V01
fetched_at        datetime actual fetch time

provider_tradestatus  int   1  (formal rows only)
coverage_status       string COVERED  (formal rows only)
```

`coverage_status` in the formal dataset is restricted to `COVERED`. All audit
statuses from section 2 and section 7 are written to the quality receipt /
audit artifact only.

Recommended provenance fields in the audit artifact (not in canonical facts):

```text
provider_code
provider_query_window_start
provider_query_window_end
validation_status
parity_reference
parity_diff
authority_url       (nullable, official announcement binding)
```

The dataset must not contain:

```text
T0/B1/B2
strategy scores
TradePlan
forward/backtest semantics
```

---

## 11. Implementation plan

The implementation plan is staged and must not jump to full-market:

```text
1. BOUNDED_ADAPTER
   - reuse existing BaoStock session/query capability (runtime 0.9.3, login,
     throttling, query_history_k_data_plus with date,code,preclose,tradestatus;
     frequency=d, adjustflag=3)
   - no new downloader, no direct TDX/EastMoney, no pinned CNEquity mutation

2. SMALL_REAL_PILOT
   - deterministic bounded pilot (already validated: PILOT_SYMBOL_N=24)
   - identity gate, coverage accounting, sentinel checks

3. INDEPENDENT_AUDIT
   - Sol independent audit of the exact pushed commit
   - no execution beyond this stage until audit

4. RESUMABLE_FULL_EXTRACTION
   - manifest/receipt authority, deterministic sorted chunk plan
   - skip completed symbols, no auto-retry, stop immediately on any gate failure
   - only required keys with tradestatus=1 become formal preclose rows

5. FINAL_GATE
   - section 12 gate; PRECLOSE_COMPLETE only when all conditions hold
```

### 11.1 Shared extraction evaluation

```text
SHARED_RAW_PROVIDER_EXTRACTION=DESIGN_OPTION
SHARED_FIELDS_EVALUATED=preclose,turn,tradestatus,isST
```

Evaluating whether `preclose`, `turn`, `tradestatus`, and `isST` are worth one
network fetch is a design option for cost reduction. It is **not** authorized
to automatically promote `turn` or `isST` to canonical facts. Each Market Fact
dataset must have its own independent quality gate.

---

## 12. R4A final gate

```text
PRECLOSE_COMPLETE=true   only if:
  R4A0_READY=true
  AND required coverage complete
  AND missing=0
  AND unexpected_traded=0
  AND unknown_status=0
  AND duplicate=0
  AND identity_failure=0
  AND post_asof=0
  AND all formal rows: provider_tradestatus=1 AND preclose finite positive
  AND NORMAL_FULL_PARITY_PASS=true
  AND FROZEN_OFFICIAL_SENTINEL_PASS=true
  AND protected R3 boundary unchanged

otherwise: PRECLOSE_COMPLETE=false  (FAIL CLOSED)
```

`UNKNOWN != PASS`. The final gate is the only authority for
`PRECLOSE_COMPLETE`. Reaching this contract does not make any execution
complete.

---

## 13. Output

Only the following two files are modified in this task:

```text
docs/plans/R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md   (this document, V01.1)
reports/planning/R4A4_PRECLOSE_SOURCE_CONTRACT_RECEIPT.json (V01.1)
```

Final output fields (also in the JSON receipt):

```text
SOURCE_CONTRACT_STATUS
PRIMARY_SOURCE
REQUIRED_SCOPE
SCHEMA
PROVENANCE
QUALITY_GATES
PARITY_GATES
IMPLEMENTATION_STAGES
R4A_IMPLEMENTATION_READY_FOR_CODE
```

`R4A_IMPLEMENTATION_READY_FOR_CODE` means only "bounded implementation code may
be written"; it is `AUTHOR_ONLY`, not `PRECLOSE_COMPLETE`.

---

## 14. Remote / safety

```text
MODE=docs only
COMMIT=docs: correct R4A canonical preclose contract
PUSH=non-force
EXACT_SHA=required
MARKET_DATA_WRITE=NO
PROVIDER_FETCH=NO
R4A_FULL_EXECUTION=NO
CODE_CHANGE=NO
```

All evidence citations in this contract are bound to the frozen R4A2/R4A2.1/
R4A3/R4A3.1 receipts and the frozen R4 plan. No secondary authority source is
used as authority.
