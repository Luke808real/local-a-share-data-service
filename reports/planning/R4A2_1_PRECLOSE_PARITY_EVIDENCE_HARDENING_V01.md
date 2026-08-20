# R4A2.1 PRECLOSE PARITY EVIDENCE HARDENING — V01

DATE: 2026-08-20
BRANCH: codex/r4a2-1-preclose-parity-hardening-v01
BASE_HEAD: 5be4d72380971336ca0f4d4ea8ce580564795c01
AS_OF: 2026-08-17
PINNED_CNEquity: a18ee0484dfb0801650175471724def3228b8a17

## Status

```text
AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
BAOSTOCK_HISTORICAL_PRECLOSE_CAPABILITY=PASS
BAOSTOCK_ROLE=INDEPENDENT_HISTORICAL_CROSSCHECK
R4A_IMPLEMENTATION_READY=false
R4_IMPLEMENTATION=FORBIDDEN_PENDING_SOL_AUDIT
MARKET_DATA_WRITE=NO
SECONDARY_AUTHORITY_SOURCES_N=0
```

This is bounded evidence hardening only. It does not implement R4A, does not
promote BaoStock to canonical data, and does not alter the frozen
`EXCHANGE_DISPLAY_PRECLOSE` semantic.

## Sample and accounting

The sample is deterministic after sorting both the exchange/year strata and
the deterministic top-up path. The manifest and detail artifacts use the same
sample hash.

```text
SAMPLE_N=260
SAMPLE_HASH=bf6399418f8e861addd490c671a1e70b46753c1fb0689a7560a40ae5ba472aac

NORMAL_N=50
EX_DATE_CASH_ONLY_N=40
EX_DATE_BONUS_ONLY_N=30
EX_DATE_ALLOTMENT_N=10
EX_DATE_MULTI_N=50
IPO_N=40
WINDOW_EDGE_N=40

COMPARABLE_N=176
UNCOMPARED_N=84
EXACT_N=155
NONZERO_WITHIN_0_01_N=11
GT_0_01_N=10
ACCOUNTING_STATUS=PASS
ACCOUNTING_FORMULA=SAMPLE_N=COMPARABLE_N+UNCOMPARED_N; COMPARABLE_N=EXACT_N+NONZERO_WITHIN_0_01_N+GT_0_01_N
```

`UNCOMPARED_N=84` is explicit: 40 IPO rows and 40 window-edge rows have no
canonical local issue-price/edge reference, three EX_DATE_BONUS_ONLY rows and
one EX_DATE_MULTI row have no local previous-effective-close reference, and
one window-edge response failed the provider row-count identity gate. No
missing reference is promoted to a parity result.

## Provider response identity gate

The probe now requires all of the following for every response before it can
be used as a comparable row:

```text
error_code == 0
AND exactly one returned row
AND returned code == requested code
AND returned date == requested date
AND preclose is finite and parseable
AND tradestatus is present
```

```text
PROVIDER_RESPONSE_IDENTITY_PASS_N=259
PROVIDER_RESPONSE_IDENTITY_FAIL_N=1
IDENTITY_FAILURE_ENUM=ROW_COUNT_NE_1
NO_BR_ZERO_SILENT_PATH=true
```

The failed response remains `comparable=false` with an explicit enum. The
detail artifact contains `local_prev_close`, `agg`, `raw_formula`,
`candidate_display`, `baostock_preclose`, `diff`, `tradestatus`, and the
uncompared reason for every sample row.

## Official authority recheck

Every current detail row with a nonzero raw difference was rechecked against a
bounded official SSE/SZSE or listed-company announcement. The mandatory
`688486.SH 2024-06-03` row was queried separately and is included in the
receipt even though it is not part of the current 260-row detail sample.

```text
CURRENT_DETAIL_NONEXACT_N=21
MANDATORY_688486_TARGETED_N=1
OFFICIAL_AUTHORITY_BOUND_N=22
OFFICIAL_ADJUSTMENT_RESOLVED_N=20
UNRESOLVED_EX_DATE_N=2
DIFFERENTIAL_DIVIDEND_CASE_N=3
```

The two unresolved rows are both `000002.SZ` cash-only cases:

| symbol/date | official cash/share | official normalized reference | BaoStock | differential dividend | status |
|---|---:|---:|---:|---|---|
| 000002.SZ / 2022-08-25 | 0.9761257 | 15.64 | 15.65 | NO | UNRESOLVED_DISPLAY_PRECISION |
| 000002.SZ / 2023-08-25 | 0.68 | 13.03 | 13.04 | NO | UNRESOLVED_DISPLAY_PRECISION |

The official notices bind the cash terms and confirm these two rows are not
differential-dividend cases. They do not, by themselves, freeze a global
display-precision rule that explains the remaining one-tick difference. The
prior generalized “cash-only mismatch is just rounding” conclusion is
superseded and is not used here.

The remaining 20 official rows close as follows:

```text
cash-only, normalized input closure                         2
ordinary bonus / special reorganization formula closure     5
effective allotment-ratio closure                           9
ordinary multi-action closure                               1
differential-dividend formula closure                       2
mandatory 688486 differential-dividend closure              1
TOTAL                                                       20
```

The complete row-level authority binding, formula, actual/virtual terms,
candidate, BaoStock value, and closure status is in
`R4A2_1_OFFICIAL_MISMATCH_RECEIPT.json`.

### Required 688486 closure

```text
SYMBOL=688486.SH
EX_DATE=2024-06-03
LOCAL_PREV_CLOSE=84.46
ACTUAL_CASH_PER_SHARE=1.40255
ACTUAL_TRANSFER_PER_SHARE=0.48
PARTICIPATING_BASE=68782767
TOTAL_BASE=69264862
VIRTUAL_CASH_PER_SHARE=1.3927880179108708
VIRTUAL_TRANSFER_PER_SHARE=0.4766591198867905
OFFICIAL_ADJUSTED_REFERENCE=56.25
BAOSTOCK_PRECLOSE=56.25
DIFFERENTIAL_DIVIDEND=YES
STATUS=RESOLVED_OFFICIAL_DIFFERENTIAL_TERMS
```

Official authority: [688486 2023 annual rights-distribution implementation
announcement](https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2024-05-25/688486_20240525_3NX7.pdf).

Other official bindings are preserved in the receipt, including the official
effective allotment ratios for 000049, 000065, 000088, 000404, 000661, 000686,
000728, 000750, and 000797; special-reorganization formulas for 600179,
600515, 603007, 000564, and 000615; and differential-dividend terms for
600089 and 600580.

## Contract matrix

```text
NORMAL_PARITY_STATUS=PASS
EX_DATE_STANDARD_PARITY_STATUS=PARTIAL_BOUNDED_OFFICIAL_EVIDENCE
EX_DATE_MULTI_PARITY_STATUS=PASS_BOUNDED_FOR_OFFICIAL_ROWS
DIFFERENTIAL_DIVIDEND_CASE_N=3
OFFICIAL_ADJUSTMENT_RESOLVED_N=20
UNRESOLVED_EX_DATE_N=2
EX_DATE_LOCAL_REFERENCE_UNAVAILABLE_N=4
PARITY_GATE_FEASIBILITY=PARTIAL_BLOCKED_UNRESOLVED_DISPLAY_PRECISION
EX_RIGHT_DIVIDEND_FEASIBILITY=PARTIAL_BOUNDED
IPO_CROSSCHECK_STATUS=AVAILABLE_NONCANONICAL
WINDOW_EDGE_CROSSCHECK_STATUS=AVAILABLE_NONCANONICAL
SPECIAL=OPEN
R4A_IMPLEMENTATION_READY=false
```

`NORMAL_PARITY_STATUS=PASS` means 50/50 exact local previous-close versus
BaoStock. `EX_DATE_MULTI_PARITY_STATUS` is bounded to the official rows: the
three current nonzero multi-action rows and the targeted 688486 row are
closed; one sample multi-action row remains uncomparable because its local
reference is absent.

## Display rule boundary

```text
DISPLAY_ROUNDING_DIAGNOSTIC=Decimal ROUND_HALF_UP to tick 0.01
DISPLAY_ROUNDING_AUTHORITY=ROW_SPECIFIC_OFFICIAL_TERMS_BOUND
GLOBAL_EXCHANGE_DISPLAY_ROUNDING_RULE=UNKNOWN
FORMULA_COMPLETE=FALSE_WHILE_GLOBAL_DISPLAY_RULE_UNKNOWN
UNKNOWN_IS_NOT_READY=true
```

The decimal normalization is only a diagnostic and row-level evidence
operation. It is not a new canonical rule and does not authorize R4A
implementation.

## Source binding examples

All sources in the receipt are official primary sources. Representative
bindings include:

- [000001.SZ 2018 annual rights-distribution implementation](https://static.cninfo.com.cn/finalpage/2019-06-20/1206367878.PDF)
- [000002.SZ 2021 annual A-share dividend implementation](https://static.cninfo.com.cn/finalpage/2022-08-18/1214319792.PDF)
- [000002.SZ 2022 annual A-share dividend implementation](https://static.cninfo.com.cn/finalpage/2023-08-21/1217577962.PDF)
- [000049.SZ rights-issue result](https://static.cninfo.com.cn/finalpage/2023-12-08/1218545401.PDF)
- [000404.SZ rights-issue result](https://static.cninfo.com.cn/finalpage/2017-05-31/1203571491.PDF)
- [600179.SH reorganization ex-right notice](https://static.sse.com.cn/disclosure/listedinfo/announcement/c/2020-12-04/600179_20201204_1.pdf)
- [600515.SH official implementation notice](https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2021-12-16/600515_20211216_1_wNefYX5E.pdf)
- [603007.SH official formula notice](https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2024-12-25/603007_20241225_RAP4.pdf)
- [000564.SZ official reorganization implementation](https://static.cninfo.com.cn/finalpage/2021-12-27/1212018431.PDF)
- [000615.SZ official reorganization formula](https://static.cninfo.com.cn/finalpage/2025-12-23/1224892491.PDF)

No secondary source is used as authority.

## Safety and next action

```text
MARKET_DATA_WRITE=NO
NETWORK_SCOPE=bounded BaoStock historical queries + bounded official announcement reads
R4A_EXECUTION=FORBIDDEN
R4A_PRECLOSE=FORBIDDEN
STRATEGY_FORWARD_TRADEPLAN=FORBIDDEN
BJ=DEFERRED
```

```text
BOUNDED_NEXT_ACTION=Sol independent audit of the exact pushed commit.
NO_R4A_IMPLEMENTATION_UNTIL_SOL_AUDIT=TRUE
```
