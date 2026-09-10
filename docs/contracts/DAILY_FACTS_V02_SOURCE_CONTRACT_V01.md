# Daily Facts V02 source contract

Status: `CANDIDATE / SHADOW`. This contract does not replace the Daily Facts V1
authority, and no V02 pointer exists. It records two source decisions that a
V02 implementation must follow.

```text
SCHEMA              = ASL_DAILY_FACTS_V02
CNEQUITY_RUNTIME    = 0.8.0 @ d453853da766b3ba3e44489c0fb6e0089243fa25
V1_AUTHORITY        = 9b4f474ebcb0db97d9dbfdb824eb6b7e17f017a6ac8957ed3d6a8fb5e3ca21ac  (unchanged)
R3_AUTHORITY        = 140197cd3c95a3f8e1c9f5750f117c66eafc09d9113757e511f31e46848bf66b  (unchanged)
```

## Admissible inputs

A V02 candidate row may be derived only from:

```text
R3 daily_bars                    (published pointer-bound partitions)
CNEquity trading_status          (0.8.0: status + risk_warning)
CNEquity corporate_actions       (event detection / derivation input)
CNEquity share_structure         (PIT float shares)
CNEquity valuation_metrics       (float_mv crosscheck only)
official reference-price evidence (certification authority for exceptions)
identity / lifecycle authority   (instruments, frozen R3 lifecycle records)
```

Forbidden as candidate inputs, enforced by `tests/test_daily_facts_v02.py`:

```text
BaoStock Daily Facts V1 RAW             (raw/baostock/daily_facts/*)
Daily Facts V1 normalized / published facts
V1 preclose, pct_chg, turnover_rate, trade_status, is_st
```

These may only enter the later shadow comparison.

## Decision A -- delisted is never a daily fact

CNEquity 0.8.0 `trading_status.status` is one of `normal | suspended | delisted`.
A `delisted` row states that the security had left the market by that date; it
is a **lifecycle** fact, not a same-day trading fact.

```text
FORBIDDEN  delisted -> SUSPENDED
FORBIDDEN  delisted -> TRADING
REQUIRED   delisted -> OUTSIDE_ELIGIBLE_LIFECYCLE
```

Rule:

1. If a key's CNEquity status is `delisted` and the identity/lifecycle
   authority shows `trade_date` beyond the valid listed lifecycle, the key is
   classified `OUTSIDE_ELIGIBLE_LIFECYCLE` and **no Daily Facts row is
   generated for it**.
2. If a `delisted` key nevertheless appears inside the date's frozen eligible
   universe, that is a contradiction between the eligibility authority and the
   status dataset, and certification fails closed:

```text
CERTIFICATION BLOCKER = ELIGIBILITY_LIFECYCLE_CONFLICT
```

Consequence for the 2026-09-09 shadow: the 5,208-key eligible universe is
fixed by the R3 authority, so decision A decides nothing about membership there
and is instead a **guard**: any `delisted` status found inside those 5,208 keys
is a blocker rather than a row-level value.

## Decision B -- turnover uses a strict PIT float-share denominator

```text
turnover_rate = R3 volume / PIT float_shares * 100
```

NaN
NaN
0
NaN
NaN
NaN
NaN
NaN
NaN
0
NaN
0
NaN
NaN
NaN
NaN
NaN
0
NaN
0
NaN
NaN
NaN
0
NaN
NaN
0
NaN
NaN
NaN
NaN
NaN
NaN
0
NaN
0
NaN
NaN
NaN
NaN

NaN
0
NaN
NaN
NaN
NaN
NaN
NaN
NaN
0
NaN
0
NaN
NaN
NaN
NaN
NaN
0
NaN
NaN
NaN
NaN
0
NaN
0
NaN
0
NaN
NaN
NaN
NaN
0
NaN
NaN
NaN
NaN

NaN
0
NaN
NaN
NaN
NaN
NaN

NaN
NaN
NaN
NaN
