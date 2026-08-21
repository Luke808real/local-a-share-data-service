# R4A4 PRECLOSE CANONICAL SOURCE CONTRACT — V01

DATE: 2026-08-21
BRANCH: codex/r4a4-preclose-source-contract-v01
BASE_HEAD: 9f72519e09389ce97c49d79ebabe182dc3c56f48
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

Task: `R4A4_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01`
Mode: `contract freeze / implementation plan` — **docs only**.
No provider fetch, no preclose dataset write, no R4A full execution.

---

## 0. Status

```text
SOURCE_CONTRACT_STATUS=FROZEN
AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
PRECLOSE_SEMANTIC=EXCHANGE_DISPLAY_PRECLOSE
PRIMARY_CANONICAL_SOURCE=BAOSTOCK_HISTORY_K_PRECLOSE
SOURCE_TIER=T2_VALIDATED_PROVIDER_CANONICAL
EXCHANGE_PRIMARY_CLAIM=false
CORPORATE_ACTIONS_ROLE=VALIDATION_DIAGNOSTIC_ONLY
R4A_IMPLEMENTATION_READY_FOR_CODE=true
PRECLOSE_COMPLETE=false
R4A_PRECLOSE_EXECUTION=FORBIDDEN_PENDING_SOL_AUDIT
MARKET_DATA_WRITE=NO
SECONDARY_AUTHORITY_SOURCES_N=0
SHARED_RAW_PROVIDER_EXTRACTION=DESIGN_OPTION
FULL_MARKET_AUTHORIZED=NO
```

`R4A_IMPLEMENTATION_READY_FOR_CODE=true` is the author decision that a **bounded
implementation-code task** for the R4A preclose dataset may be designed and
executed under this frozen contract. It is not `PRECLOSE_COMPLETE`, is not an
authoritative audit, and does not authorize R4A full-market execution.

---

## 1. Semantic — unchanged

```text
PRECLOSE_SEMANTIC=EXCHANGE_DISPLAY_PRECLOSE
```

The semantic is unchanged and must not be reverted to simple previous close.
NORMAL, IPO first-listing-day, EX_DATE, RESUMPTION, and SPECIAL all output the
same Market Fact `preclose`. Source-specific interpretation is carried only in
provenance and validation, never in the fact itself.

Frozen case definitions (unchanged from the R4 plan / R4A1 feasibility):

| Case | Preclose definition |
|---|---|
| NORMAL | previous effective symbol close |
| IPO_FIRST_LISTING_DAY | issue price (canonical candidate via BaoStock first-listing-day preclose under this contract) |
| EX_RIGHT_EX_DIVIDEND_DAY | exchange ex-right/ex-dividend reference price |
| SPECIAL_RELISTING_OR_RESUMPTION | exchange-defined reference when an authoritative rule exists; otherwise UNKNOWN + explicit reason |

`NO_LIMIT_DAY != NULL_PRECLOSE`. NULL preclose is allowed only when the
exchange display preclose itself is not authoritative (SPECIAL without an
authoritative reference) and is then persisted with an explicit reason.

---

## 2. Source role

```text
PRIMARY_CANONICAL_SOURCE=BAOSTOCK_HISTORY_K_PRECLOSE
API=query_history_k_data_plus
FREQUENCY=d
ADJUSTFLAG=3
MINIMUM_FIELDS=date,code,preclose,tradestatus
```

### 2.1 Source tier

The contract explicitly defines the source tier. It does **not** claim
exchange-primary status.

```text
T1_EXCHANGE_PRIMARY            = not claimed, not available for full history
T2_VALIDATED_PROVIDER_CANONICAL= BaoStock historical preclose
T3_VALIDATION_DIAGNOSTIC       = corporate_actions + bounded official announcements
```

`BaoStock historical preclose` is frozen on tier T2 as the authoritative
provider source for the canonical Market Fact `preclose`, constrained by the
frozen validation gates in sections 3-9 and 11 below. It is an **authoritative
provider source under gate**, not an exchange primary and not a fallback-only
crosscheck.

### 2.2 corporate_actions role

`corporate_actions` is frozen as `VALIDATION / DIAGNOSTIC` authority only. It is
no longer treated as the sole or primary reconstruction path for full-history
preclose. The canonical preclose value comes from the frozen provider source;
corporate_actions and official announcements cross-check and explain ex-date /
special rows.

---

## 3. Required row scope

```text
REQUIRED_KEY_AUTHORITY=R3 authoritative daily_bars ACTUAL_TRADED sessions
REQUIRED_PK=(symbol, trade_date)
FORMAL_ROW_SCOPE=only required keys receive formal preclose
```

Only `(symbol, trade_date)` keys that exist in the R3 authoritative
`daily_bars` actual-traded session set are produced as formal preclose rows.

Coverage classification of provider rows for each required key:

```text
tradestatus=1  -> ELIGIBLE_REQUIRED_ROW_OBSERVATION  -> formal preclose row
tradestatus=0  -> PROVIDER_SUSPENDED_SUPERSET        -> NOT a required row;
                                                       audit evidence only
tradestatus UNKNOWN/MISSING -> TRADESTATUS_UNKNOWN   -> FAIL CLOSED
```

Provider rows outside the required key set are classified by section 9.

---

## 4. Identity / AS_OF gate

Every formal preclose row must satisfy all of the following, otherwise the row
(and the extraction) fails:

```text
provider code exact
AND provider date exact
AND preclose finite AND positive
AND tradestatus exact enum (0/1)
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

## 5. First listing day

```text
IMPLEMENTATION_REDOWNLOAD_2645_ISSUE_PRICES=NO
FIRST_LISTING_DAY_PRECLOSE_SOURCE=BaoStock first-listing-day preclose
FIRST_LISTING_DAY_STATUS=CANONICAL_CANDIDATE_OBSERVATION
```

The implementation does not re-fetch the historical 2,645 issue prices. BaoStock
first-listing-day `preclose` is frozen as the canonical candidate observation.

Validation evidence (frozen, bounded, official-only):

```text
IPO_OFFICIAL_N=3
IPO_EXACT_N=3
IPO_PARITY_STATUS=PASS
```

- 603007.SH 2016-08-26 issue=11.66 preclose=11.66 exact
- 688486.SH 2023-02-21 issue=64.76 preclose=64.76 exact
- 688489.SH 2022-12-02 issue=78.89 preclose=78.89 exact

Validation hook: any future IPO parity mismatch in the bounded or full
extraction must `FAIL CLOSED` and trigger a source investigation; it must never
be silently accepted.

---

## 6. NORMAL validation

Clean NORMAL is defined by **excluding**:

```text
corporate-action event (ex-date)
first listing day
resumption candidate
known special
```

For every clean NORMAL row the parity contract is:

```text
PRECLOSE == PREVIOUS_EFFECTIVE_CLOSE  (must be exact)
```

Current bounded evidence (frozen):

```text
CLEAN_NORMAL_N=54756
CLEAN_NORMAL_EXACT_N=54756
CLEAN_NORMAL_MISMATCH_N=0
CLEAN_NORMAL_MAX_DIFF=0.0
CLEAN_NORMAL_PARITY_STATUS=PASS
```

---

## 7. EVENT / SPECIAL validation

`corporate_actions` plus bounded official authority remain the cross-check for
event and special rows:

```text
ordinary ex-date
differential dividend
effective rights ratio
special reorganization
suspension-resume adjustment
```

Official event evidence (frozen, reused from prior bounded receipt):

```text
OFFICIAL_EVENT_N=20
OFFICIAL_EVENT_EXACT_N=20
OFFICIAL_EVENT_MISMATCH_N=0
OFFICIAL_EVENT_PARITY_STATUS=PASS
```

### 7.1 Frozen special case — 000564.SZ 2018-07-20

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

This row is frozen as `RESUMPTION_CANDIDATE` and must be excluded from
`CLEAN_NORMAL`. Future verifiers must not re-classify it as a NORMAL mismatch.

---

## 8. Dataset contract

Proposed formal dataset schema (R4A preclose canonical fact):

```text
DATASET=preclose_facts

symbol            string   canonical symbol, e.g. 000001.SZ
trade_date        date     R3 required actual-traded session date
preclose          decimal  finite positive exchange-display preclose

source            string   BAOSTOCK_HISTORY_K_PRECLOSE
source_version    string   provider query contract version (frequency=d, adjustflag=3)
fetched_at        datetime provenance collection timestamp

provider_tradestatus  int   0|1  exact provider enum

coverage_status   string  COVERED | PROVIDER_SUSPENDED_SUPERSET | MISSING |
                          UNEXPECTED_TRADED | TRADESTATUS_UNKNOWN | SPECIAL_UNKNOWN
reason            string  nullable; required for any non-COVERED status
```

Additional provenance fields (recommended):

```text
provider_code
provider_query_window_start
provider_query_window_end
validation_status
parity_reference
parity_diff
authority_url          (nullable, official announcement binding)
```

The dataset must not contain:

```text
T0/B1/B2
strategy scores
TradePlan
forward/backtest semantics
```

---

## 9. Extra provider rows

Provider rows that fall outside the required key set or are not status-1 are
classified exactly one of:

```text
PROVIDER_SUSPENDED_SUPERSET  tradestatus=0 outside required keys
                             -> does NOT block R4A coverage
                             -> retained as audit evidence

UNEXPECTED_TRADED_ROW        tradestatus=1 outside required keys
                             -> BLOCKS coverage

TRADESTATUS_UNKNOWN          tradestatus missing/unknown on any row
                             -> BLOCKS coverage
```

Only `PROVIDER_SUSPENDED_SUPERSET` is non-blocking. `UNEXPECTED_TRADED_ROW` and
`TRADESTATUS_UNKNOWN` are hard blockers (`FAIL CLOSED`).

---

## 10. Implementation plan

The implementation plan is staged and must not jump to full-market:

```text
1. BOUNDED_ADAPTER
   - reuse existing BaoStock session/query capability (runtime, login, throttling,
     query_history_k_data_plus with date,code,preclose,tradestatus;
     frequency=d, adjustflag=3)
   - no new downloader, no direct TDX/EastMoney, no pinned CNEquity mutation

2. SMALL_REAL_PILOT
   - deterministic bounded pilot (already validated: PILOT_SYMBOL_N=24)
   - identity gate, coverage accounting, parity gates per sections 3-9

3. INDEPENDENT_AUDIT
   - Sol independent audit of the exact pushed commit
   - no execution beyond this stage until audit

4. RESUMABLE_FULL_EXTRACTION
   - manifest/receipt authority, deterministic sorted chunk plan
   - skip completed symbols, no auto-retry, stop immediately on any gate failure
   - requirement remains: only required keys become formal preclose rows

5. FINAL_GATE
   - section 11 gate; PRECLOSE_COMPLETE only when all conditions hold
```

### 10.1 Shared extraction evaluation

```text
SHARED_RAW_PROVIDER_EXTRACTION=DESIGN_OPTION
SHARED_FIELDS_EVALUATED=preclose,turn,tradestatus,isST
```

Evaluating whether `preclose`, `turn`, `tradestatus`, and `isST` are worth one
network fetch is a design option for cost reduction. It is **not** authorized
to automatically promote `turn` or `isST` to canonical facts. Each Market Fact
dataset must have its own independent quality gate.

---

## 11. R4A final gate

```text
PRECLOSE_COMPLETE=true  only if:
  R4A0_READY=true
  AND required coverage complete
  AND missing=0
  AND unexpected_traded=0
  AND unknown_status=0
  AND duplicate=0
  AND identity_failure=0
  AND post_asof=0
  AND preclose finite/valid on every formal row
  AND bounded parity gates PASS
  AND protected R3 boundary unchanged

otherwise: PRECLOSE_COMPLETE=false  (FAIL CLOSED)
```

`UNKNOWN != PASS`. The final gate is the only authority for
`PRECLOSE_COMPLETE`. Reaching this contract does not make any execution
complete.

---

## 12. Output

Two task deliverables:

```text
docs/plans/R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md
reports/planning/R4A4_PRECLOSE_SOURCE_CONTRACT_RECEIPT.json
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
be written"; it is not `PRECLOSE_COMPLETE`.

---

## 13. Remote audit / safety

```text
MODE=docs only
COMMIT=docs: freeze R4A canonical preclose source contract
PUSH=non-force
EXACT_SHA=required
MARKET_DATA_WRITE=NO
PROVIDER_FETCH=NO
R4A_FULL_EXECUTION=NO
```

All evidence citations in this contract are bound to the frozen R4A2/R4A2.1/
R4A3/R4A3.1 receipts and the frozen R4 plan. No secondary authority source is
used as authority.
