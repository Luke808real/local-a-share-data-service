# Daily Facts V02 source contract

Status: `CANDIDATE / SHADOW`. This contract does not replace the Daily Facts V1
authority, and no V02 pointer exists. It records the source decisions a V02
implementation must follow.

```text
SCHEMA              = ASL_DAILY_FACTS_V02
CNEQUITY_RUNTIME    = 0.8.0 @ d453853da766b3ba3e44489c0fb6e0089243fa25
                      + ASL local additive patches 0001 and 0002
V1_AUTHORITY        = 9b4f474ebcb0db97d9dbfdb824eb6b7e17f017a6ac8957ed3d6a8fb5e3ca21ac  (unchanged)
R3_AUTHORITY        = 140197cd3c95a3f8e1c9f5750f117c66eafc09d9113757e511f31e46848bf66b  (advances normally)
```

## Admissible inputs

A V02 candidate row may be derived only from:

```text
R3 daily_bars                     (published pointer-bound partitions)
CNEquity trading_status           (0.8.0: status + risk_warning)
CNEquity valuation_metrics        (turnover_rate, the exchange's own f8)
CNEquity corporate_actions        (event detection / derivation input)
official reference-price evidence (certification authority for exceptions)
identity / lifecycle authority    (instruments, frozen R3 lifecycle records)
```

Forbidden as candidate inputs, enforced by `tests/test_daily_facts_v02.py`:

```text
BaoStock Daily Facts V1 RAW                (raw/baostock/daily_facts/*)
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

1. If a key's CNEquity status is `delisted` and the identity/lifecycle authority
   shows `trade_date` beyond the valid listed lifecycle, the key is classified
   `OUTSIDE_ELIGIBLE_LIFECYCLE` and **no Daily Facts row is generated for it**.
2. If a `delisted` key nevertheless appears inside the date's frozen eligible
   universe, that is a contradiction between the eligibility authority and the
   status dataset, and certification fails closed:

```text
CERTIFICATION BLOCKER = ELIGIBILITY_LIFECYCLE_CONFLICT
```

The 5,208-key eligible universe is fixed by the R3 authority, so decision A
decides nothing about membership there and is instead a **guard**: any
`delisted` status found inside those keys is a blocker rather than a row value.

## Decision B -- turnover comes from the exchange's own published rate

```text
ACCEPTED  turnover_rate = CNEquity valuation_metrics.turnover_rate
                          (EastMoney clist f8, unit PERCENT)

REJECTED  turnover_rate = R3 volume / share_structure.float_shares * 100
```

The rejected derivation was measured on the 2026-09-09 full-market shadow: 275
of 5198 comparable keys diverged from the certified value, strictly
one-directionally larger, because EastMoney's share-structure report and the
exchange's turnover denominator are different bases for part of the market. No
local reverse engineering resolves that, and the exchange's own rate does not
have the problem.

The rejected rule id `VOLUME_OVER_PIT_FLOAT_SHARES_V02` is retained in the module
as `RULE_TURNOVER_DERIVED_AUDIT_ONLY` so historical records that named it stay
interpretable. It must not be used as a fact source.

```text
unit               PERCENT (f8 value 2.35 means 2.35%), matching the frozen V1
                   turnover contract; never divided by 100 into a ratio
suspended session  turnover_rate = null, never 0, even if the provider returns 0
missing value      TURNOVER_UNRESOLVED; no fallback to a future or derived value
```

## Decision C -- a snapshot source must describe the session it is written under

`valuation_metrics` is declared `fetch_semantics = snapshot`: the vendor serves a
live page stamped with whatever date the caller asks for, and a past date can
never be replayed. Two failure modes follow, both observed live:

```text
forged stamping   passing trade_date=2026-09-09 while the page describes the
                  2026-09-10 close writes a partition that is simply false
intraday capture  during the session the page keeps updating, so the values are
                  partial and the session has not settled
```

The snapshot-date guard (CNEquity local patch 0002) makes a caller unable to
*declare* a session. It resolves the session from the vendor's own `f124`
last-update epoch, requested on the same response, and requires it to match the
requested trade date from a settled window. It runs inside
`fetch_valuation_metrics` before any row is returned, so a refusal cannot leave a
partial partition, and it costs no extra provider request.

```text
SNAPSHOT_UNSTAMPED          no row carries an update epoch
SNAPSHOT_SESSION_MISMATCH   the payload describes a different session
SNAPSHOT_SESSION_INCOHERENT fewer than 90% of stamps share one session
SNAPSHOT_NOT_SETTLED        the latest stamp is before 15:05 Asia/Shanghai
SNAPSHOT_WINDOW_NOT_OPEN    the local clock is before the settlement cutoff
SNAPSHOT_TURNOVER_ABSENT    no row carries a turnover value
SNAPSHOT_TURNOVER_RESET     fewer than 50% non-zero, i.e. the next-session reset
```

The session-date check is not optional: writing values under a date they do not
describe is incorrect data rather than a policy choice. Only the settle
requirement can be relaxed, and only deliberately.

A date-only guard is insufficient and was rejected during development: during
market hours `f124` tracks live ticks, so the date is correct while the session
is incomplete.

## Field sources

| Fact | Source | Rule id |
|---|---|---|
| `preclose` | prior published R3 close, or official reference-price evidence on an exact-date corporate action | `PRIOR_PUBLISHED_CLOSE_V02` / `EXCHANGE_REFERENCE_PRICE_V02` |
| `pct_chg` | local derivation `(close / preclose - 1) * 100` | `CLOSE_OVER_PRECLOSE_V02` |
| `turnover_rate` | CNEquity `valuation_metrics.turnover_rate` | `CNEQUITY_VALUATION_METRICS_TURNOVER_V02` |
| `trade_status` | CNEquity `status` | `CNEQUITY_TRADING_STATUS_V02` |
| `is_st` | CNEquity `risk_warning` | `CNEQUITY_TRADING_STATUS_V02` |

### Prior close and suspension

The prior close is the last **observed** close, not merely the previous calendar
session's row. The R3 authority encodes a suspension as an *absent* row, so
reading only the immediately preceding session loses the reference price for a
name that was halted. The generator walks back over prior sessions (bounded at
12) and takes the last real close.

```text
SUSPENDED -> pct_chg = null, turnover_rate = null   (never 0)
```

## Status and risk-warning normalization

```text
status = normal     -> trade_status = TRADING
status = suspended  -> trade_status = SUSPENDED
status = delisted   -> lifecycle rule (Decision A), never a daily value
status absent       -> trade_status = UNKNOWN

risk_warning = true  -> is_st = TRUE
risk_warning = false -> is_st = FALSE
risk_warning = null  -> is_st = UNKNOWN
```

`UNKNOWN` never passes certification. The nullable `risk_warning` is what makes
this possible: a derived bar-gap suspension has no ST evidence either way, and
0.8.0 records that as `null` rather than fabricating `false`. Under the 0.7.2
single-column encoding a halt silently dropped the ST label; the two-column form
keeps both, which is the property the 2026-09-09 shadow verified on the three
names that were halted and under risk warning on the same day.

## Reference-price exceptions

The 16 certified 2026-09-09 exceptions keep their existing official CNInfo
evidence as the certification authority. `corporate_actions` may detect that an
exact-date event exists, but it must not overwrite the frozen official evidence,
and it must not be used to back out an adjustment from a provider preclose.

```text
corporate_actions     = event detection / derivation input
official disclosures  = reference-price certification authority
```

Rounding contract, verified against all 16 fixtures:

```text
P_ref = (P_prev - cash_dividend + allotment_price * allotment_ratio)
        / (1 + bonus_ratio + transfer_ratio + allotment_ratio)
then quantize to the 0.01 display tick, ROUND_HALF_UP
```

`ROUND_HALF_UP` to 0.01 is not cosmetic: `603993.SH` moves 18.845 to 18.85 and a
truncating rule would publish 18.84. All 16 fixtures reproduce exactly under this
rule, including the two whose effective adjustment differs from the nominal
declaration (`002322.SZ` 0.3251058, `300196.SZ` 0.1974617).

## Daily chain

```text
resolve trade date
-> trading-day gate
-> snapshot-window gate (Decision C)
-> R3 incremental update + publication
-> trading_status bulk
-> valuation_metrics bulk snapshot
-> local V02 derivation
-> certification
-> publication          (not authorized; no V02 pointer exists)
```

Target for the normal path:

```text
BAOSTOCK_PER_SYMBOL_DAILY_REQUEST_N = 0
```
