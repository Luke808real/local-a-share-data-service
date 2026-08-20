# R4A2 BAOSTOCK HISTORICAL PRECLOSE PROBE — V01

DATE: 2026-08-20
BRANCH: codex/r4a2-baostock-preclose-probe-v01
BASE_HEAD: 31fa2b02baab006394d00bc4a41275bf35979c74
PINNED CNEquity: a18ee0484dfb0801650175471724def3228b8a17
AS_OF: 2026-08-17

## 1. Verdict

**BAOSTOCK_HISTORICAL_PRECLOSE_CAPABILITY = PASS** — BaoStock historical daily
`preclose` (frequency=d, adjustflag=3 unadjusted; date,code,preclose,tradestatus)
is a usable INDEPENDENT historical CROSSCHECK source. NORMAL parity is EXACT
(50/50). EX_DATE official-formula parity is high (96/100 standard, 45/50
multi-action within 0.01) with residual mismatches that need Sol adjudication.
**R4A_IMPLEMENTATION_READY stays false** (UNKNOWN != READY).

## 2. Capability probe

```text
2 SH + 2 SZ x 2017/2020/2023/2026  = 16 queries; 16/16 success
preclose non-null on every queried trading day; tradestatus=1; date/symbol
identity correct.
```

## 3. Deterministic sample (<= 300)

```text
NORMAL              50
EX_DATE standard    100
EX_DATE multi        50
IPO_FIRST_DAY        40
WINDOW_EDGE          40
total               280   (deterministic, year/exchange/action stratified; no random seed)
SAMPLE_HASH (NORMAL) c5e42e0d25cda937f54880de3219c64f6c24c0b84081bf3dfd5891e38f248f0e
SAMPLE_HASH (extras) 724fd9d17980425108d0f591aa9f8fb942d23f0c58f42a9e46d8408405857e31
```

## 4. NORMAL parity

```text
N             50
EXACT_MATCH_N 50
WITHIN_0_01_N 50
MISMATCH_N    0
MAX_ABS_DIFF  0.0
NORMAL_PARITY_STATUS PASS
```

Local previous-effective-close derivation and BaoStock historical preclose
agree exactly — Baostock is a sound independent CROSSCHECK for NORMAL.

## 5. EX_DATE parity (official formula candidate vs BaoStock)

Candidate = `(prev_close - cash + allotment_price*allotment_ratio) /
(1 + bonus_ratio + allotment_ratio)`, Decimal ROUND_HALF_UP to 0.01.

```text
STANDARD:       N 100  EXACT 88  WITHIN_0_01 96  MISMATCH 3  MAX_DIFF 0.02
MULTI-ACTION:   N  50  EXACT 44  WITHIN_0_01 45  MISMATCH 4  MAX_DIFF 0.13

EX_DATE_STANDARD_PARITY_STATUS        PARTIAL
EX_DATE_MULTI_ACTION_PARITY_STATUS    PARTIAL
```

### 5.1 Mismatch diagnosis (7)

- 3 cash-dividend-only mismatches, diff 0.01–0.02: rounding/precision of the
  source (e.g. 002304.SZ cash 3.00 -> 195.18 vs 195.20). Not a formula error.
- 4 cash+bonus combinations, diff 0.02–0.13 (e.g. 688486.SH 0.13): candidate
  differential-dividend / per-share basis issue. **Not auto-claimed.**

```text
DIFFERENTIAL_DIVIDEND_COVERAGE_STATUS = CANDIDATE
EX_DATE_MISMATCH_N = 7 (examples preserved in probe detail JSON)
```

Official announcements would be required to confirm; no bulk announcement
scrape (handed to Sol).

## 6. IPO first day

```text
IPO_SAMPLE_N            40
PRECLOSE_NON_NULL_N     40
PRECLOSE_NULL_N          0
IPO_CROSSCHECK_REFERENCE_AVAILABLE true
```

BaoStock preclose is NOT promoted to canonical issue price; it is a usable
crosscheck. Canonical issue-price authority remains an open decision (R4A1
blocker).

## 7. Window edge

```text
WINDOW_EDGE_SAMPLE_N            40
WINDOW_EDGE_PRECLOSE_NON_NULL_N 40
WINDOW_EDGE_CROSSCHECK_AVAILABLE true
```

Crosscheck available; not canonical promotion.

## 8. Special

Only recorded if naturally encountered in the samples (none specifically
special-tagged). No gap/price-based special inference was used; the SPECIAL
blocker remains open by decision, not by this probe.

## 9. Write boundary

```text
MARKET_DATA_WRITE = NO
probe scope: read-only polars over daily_bars/corporate_actions/instruments +
bounded Baostock queries (adjustflag=3 unadjusted; date,code,preclose,tradestatus);
outputs only /tmp JSON + report/receipt. Market lake untouched.
```

## 10. Result contract

```text
BAOSTOCK_HISTORICAL_PRECLOSE_CAPABILITY   PASS
NORMAL_PARITY_STATUS                      PASS        (50/50 EXACT)
EX_DATE_STANDARD_PARITY_STATUS            PARTIAL     (96/100 within 0.01; 3 cash-only 0.01-0.02)
EX_DATE_MULTI_ACTION_PARITY_STATUS        PARTIAL     (45/50 within 0.01; 4 combos 0.02-0.13 -> Sol)
IPO_CROSSCHECK_REFERENCE_AVAILABLE        true
WINDOW_EDGE_CROSSCHECK_AVAILABLE          true
PARITY_GATE_FEASIBILITY                   PARTIAL
EX_RIGHT_DIVIDEND_FEASIBILITY             PARTIAL
R4A_IMPLEMENTATION_READY                  false       (UNKNOWN != READY)
```

## 11. R4A1 correction

The probe evidence supersedes two earlier R4A1 statements:

```text
OLD  PARITY historical reference NOT_AVAILABLE
NEW  BaoStock historical preclose IS an independent historical CROSSCHECK
     source (NORMAL EXACT; EX_DATE high parity) -> PARITY_GATE_FEASIBILITY PARTIAL

OLD  EX_RIGHT_DIVIDEND FEASIBLE (assumed)
NEW  formula (OFFICIAL_RULE) verified on the sample; residual mismatches
     pending differential-dividend / basis adjudication -> PARTIAL
```

The frozen R4 V01.2 `EXCHANGE_DISPLAY_PRECLOSE` semantic is NOT modified.

## 12. Blockers / next

```text
BLOCKERS
 - EX_DATE residual mismatches (cash+bonus, up to 0.13) need official
   differential-dividend verification (Sol decision)
 - IPO canonical issue-price authority still missing locally
 - SPECIAL recognition blocker still open

BOUNDED_NEXT_ACTION
 - Sol: decide differential-dividend basis vs accept 0.02-0.13 residual as
   UNKNOWN bucket; IPO issue-price authority approach; SPECIAL source decision
 - then R4A implementation may proceed
```

Probe tools: `tools/run_r4a2_preclose_probe.py` (NORMAL),
`tools/run_r4a2_exdate_probe.py` (EX_DATE/IPO/EDGE). No market-data write, no
bulk provider pull, no R4A/Strategy/Forward/TradePlan.
