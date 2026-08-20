# R4A PRECLOSE CONTRACT FEASIBILITY — V01 (READ-ONLY)

DATE: 2026-08-20
BRANCH: codex/r4a1-preclose-contract-feasibility-v01
BASE_HEAD: 9d3d25eb7dca0544f1520728e73218f95bce3e70
R4_PLAN_V01.2: ab150e171e4aae69d0e48b055b994c10f12abc0b
UPSTREAM_CNEQUITY: v0.7.2 @ a18ee0484dfb0801650175471724def3228b8a17

## 0. Authority / recheck

```text
R4A0_READY_RECHECK  true   (independent read-only gate: COVERED 5456, MISSING 0,
                            PARTIAL 0, DUPLICATE_ACTION_N 0, UNRESOLVED_N 0)
ASOF              2026-08-17
PRECLOSE_SEMANTIC EXCHANGE_DISPLAY_PRECLOSE
```

R4A0 full bootstrap evidence (frozen) referenced; no market data written.

## 1. NORMAL case — FEASIBLE

NORMAL preclose = the symbol's own previous **effective** traded close. Since
rows are per symbol and per trade date, every daily row except each symbol's
first bar has a previous effective close (cross-calendar-day gaps / suspensions
need no special handling: the prior traded close is the effective reference).
No cross-symbol / placeholder / volume=0 inference is used. Ex-date rows are
handled by the ex-right case, not NORMAL.

```text
NORMAL_REQUIRED_ROW_N     10,709,989   (= required daily-bar rows)
NORMAL_DERIVABLE_ROW_N    10,704,533
NORMAL_UNRESOLVED_ROW_N   5,456        (= per-symbol first bars; see below)
```

First-bar classification:

```text
IPO_FIRST_LISTING_DAY                   2,645   -> handled by IPO case (issue price)
LISTED_BEFORE_WINDOW_OR_RELIST_CANDIDATE 2,811  -> list_date before window edge; no
                                                   in-window prior close; preclose
                                                   would need an explicit edge/UNKNOWN
                                                   reason (WINDOW_BOUNDARY_EDGE).
```

## 2. IPO first-listing-day — BLOCKED (issue price authority missing)

Frozen semantic: IPO_FIRST_LISTING_DAY preclose = issue price. Investigation of
curated/instruments, daily_bars, pinned CNEquity schemas/adapters, and local
receipts found **no historical issue-price field** anywhere in the local /
pinned assets.

```text
IPO_FIRST_DAY_N           2,645
ISSUE_PRICE_AVAILABLE_N   0
ISSUE_PRICE_MISSING_N     2,645
ISSUE_PRICE_SOURCE        PINNED_LOCAL_NOT_AVAILABLE
                          (CSRC pricing-mechanism rule exists as official
                           authority, but no local historical issue-price value)
```

IPO_PRECLOSE_FEASIBILITY = BLOCKED for first-listing days. No inference from
first-day open/close is allowed. A bounded issue-price authority (acquisition or
historical third-party issuance-price dataset) is a followup decision for Sol.

## 3. Ex-right / ex-dividend — FEASIBLE (OFFICIAL_RULE)

### 3.1 Pinned schema semantics (verified)

`adapters/tdx_protocol/corporate_actions.py` `_rows_from_xdxr`:

```text
cash_dividend    per share CNY (feng hong per-10 / 10)
bonus_ratio      per share, COMBINED 送+转 (TDX xdxr does not split; total
                 multiplier is exact). transfer_ratio rows are always 0.
allotment_ratio  per share (pei gu per-10 / 10)
allotment_price  per share CNY, NOT a ratio
```

Same-ex-date multiple action types are separate rows (PK
`(symbol, ex_date, action_type)`), 4,493 same-day multi-action groups — they
combine additively in the reference-price formula (no double counting).

### 3.2 Formula authority

No pinned reference-price / preclose helper exists (grep confirms no
`reference_price` / xdxr-calculation helper). The canonical exchange rule is
used as OFFICIAL_RULE:

```text
reference_preclose(ex-day) =
    (prev_close - cash_dividend + allotment_price * allotment_ratio)
    / (1 + bonus_ratio + allotment_ratio)

Authorities (official, not market-data fetch):
  - SSE 2026 Trading Rules (上证发〔2026〕41号/SSE rules): ex-right reference price
    formula page https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10817739.shtml
  - SZSE same ex-right/ex-dividend reference-price formula (per-share cash,
    bonus/transfer, allotment share-ratio basis)
```

```text
EX_DATE_SYMBOL_DAY_N             36,051
MULTI_ACTION_SAME_DAY_N          4,493
FORMULA_AUTHORITY                OFFICIAL_RULE
FORMULA_FEASIBILITY              FEASIBLE
rounding                         ROUND_HALF_UP to tick 0.01 (to freeze at
                                 implementation; aligned with price-limit engine)
```

Open decision: differential-dividend (差异化分红) per-share basis must be
re-verified at implementation; it does not change the formula structure.

## 4. SPECIAL relisting / resumption — BLOCKED

No authoritative local/pinned evidence distinguishes ordinary suspension
resume from a special first-day reference-price event. No gap/price inference
is used (explicitly disallowed). Frozen behavior must therefore be:

```text
SPECIAL_RELISTING_OR_RESUMPTION -> preclose = NULL
                                   coverage_status = UNKNOWN
                                   reason = explicit enum
                                   (SPECIAL_RELIST_UNVERIFIED /
                                    NO_AUTHORITATIVE_REFERENCE)
SPECIAL_REFERENCE_AUTHORITY      NOT_AVAILABLE
```

SPECIAL_FEASIBILITY = BLOCKED (recognition impossible without an authoritative
reference source; NULL+UNKNOWN behavior can still be frozen).

## 5. Required row scope

```text
REQUIRED_ROW_N     10,709,989   (R4 stable_market_facts actual-traded sessions)
DAILY_BAR_ROW_N    10,709,989
PK                 (symbol, trade_date)
DUPLICATE_PK_N     0
MISSING_REQUIRED_ROW_N  5,456   (= per-symbol first bars; preclose requires
                                 issue price (2,645) or explicit edge/UNKNOWN
                                 handling (2,811))
```

## 6. Ex-date parity gate design — evidence-backed, BLOCKED

Available references:

```text
CANONICAL_SOURCE    derived NORMAL previous-close (no independent archive)
CROSSCHECK_ONLY     TDX security-list pre_close (live snapshot)
LIVE_ONLY           TDX security-list pre_close (current day only)
NOT_AVAILABLE       independent historical pre_close reference
```

Because there is no independent historical `pre_close` archive, a historical
full-window parity gate cannot be honestly run. A live-only sample must never be
described as historical full coverage. Proposed (future) gate design, subject
to a bounded historical reference becoming available:

```text
PARITY_SAMPLE_SCOPE   per-year stratified ex-date sample of SH/SZ symbols
PARITY_SAMPLE_N       ~2,000 ex-date symbol-days (of 36,051) by year
sampling rule         deterministic year-stratified subset (sorted symbol/date)
comparison            derived reference_preclose vs reference preclose
tick tolerance        0.01 (ROUND_HALF_UP), no wide tolerance to force PASS
rounding              ROUND_HALF_UP to 0.01
mismatch handling     any mismatch -> PARITY_MISMATCH (fail closed); reported
```

Until an independent historical reference exists: PARITY_GATE_FEASIBILITY =
BLOCKED; parity is not fabricated.

## 7. AS_OF / PIT

```text
ASOF_SAFE_DESIGN  PASS
max daily trade_date 2026-08-17; no 2026-08-18+ market facts used. Rules/code
may read current repo, but historical derivation inputs stay <= ASOF; the
corporate_actions max ex_date is 2026-08-17.
```

## 8. Feasibility matrix

```text
NORMAL            FEASIBLE
IPO               BLOCKED   (issue price authority missing)
EX_RIGHT_DIVIDEND FEASIBLE  (OFFICIAL_RULE)
SPECIAL           BLOCKED   (no authoritative reference; NULL+UNKNOWN frozen)
PARITY_GATE       BLOCKED   (no independent historical reference)

R4A_IMPLEMENTATION_READY = false   (any required case / blocking parity unclosed)
```

UNKNOWN != READY. This author result must not self-promote.

## 9. Blockers / decisions needed

BLOCKERS:
1. IPO issue-price authority absent from local/pinned assets (2,645 days).
2. SPECIAL relist/resumption recognition has no authoritative reference.
3. PARITY gate has no independent historical pre_close reference
   (TDX security-list is live-only).

DECISIONS_NEEDED (for Sol):
1. Introduce a bounded issue-price authority, or accept UNKNOWN for
   first-listing days.
2. Differential-dividend per-share computation basis.
3. Confirm ROUND_HALF_UP / 0.01 tick for reference price.
4. SPECIAL: accept NULL+UNKNOWN+reason, or introduce a bounded identification
   source.
5. PARITY: accept a live-only + formula self-consistency transition, or acquire
   a bounded historical pre_close reference.
6. Accept WINDOW_BOUNDARY_EDGE UNKNOWN for the 2,811 listed-before-window
   first bars.

BOUNDED_NEXT_ACTION:
- Sol adjudicates BLOCKERS/DECISIONS.
- Issue-price authority and/or historical pre_close acquisition would be
  separate bounded followup tasks.
- R4A implementation proceeds only after closure.

No code, no preclose dataset, no provider fetch, no market-data write.
