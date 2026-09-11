# CNEquity 0.8.0 turnover_rate additive field (EastMoney clist f8)

## 0. Scope

Closes the one field the 2026-09-09 V02 shadow left uncertified. Production
pins were already moved to 0.8.0 in the previous round; this round adds one
additive provider field and re-validates turnover. Nothing was published and no
pointer moved.

```text
BASE_HEAD = 1f00e832bd5c94ca7e841023c6f3ae2d11d0feb1
BRANCH    = codex/r3-incremental-hardening-daily-facts-phase1-v01
```

## 1. The decision

```text
REJECTED  turnover_rate = R3 volume / share_structure.float_shares * 100
ACCEPTED  turnover_rate = CNEquity valuation_metrics.turnover_rate  (clist f8)
```

The rejected derivation produced 275 of 5198 comparable keys one-directionally
larger than the BaoStock-certified V1 value, because EastMoney's share-structure
report and the exchange's turnover denominator are different bases for part of
the market. No amount of local reverse engineering resolves that; the exchange's
own published rate does not have the problem at all.

## 2. EastMoney field evidence

The field `f8` is the turnover rate in PERCENT. Three independent angles:

1. **Existing adapter convention.** `adapters/eastmoney/rotation.py` already
   reads the same field off the same clist endpoint as `turnover_pct`. The
   sibling kline adapter requests `f61`, the kline-side equivalent.
2. **Bounded live sample.** A 100-row page returned `f8` as a numeric float for
   every row, range 0.62-80.84, with no `-`, null or empty values. A
   5,909-symbol capture behaved identically.
3. **Cross-vendor same-session agreement.** f8 was compared against
   `TDX volume / BaoStock-implied float * 100` - a numerator from TDX and a
   denominator from BaoStock, i.e. two vendors that are not EastMoney.

```text
comparable keys             5196
within 1% relative          5069  (97.56%)
within 2% relative          5164  (99.38%)
median relative difference  0.131%
```

EastMoney and BaoStock therefore measure the same quantity on the same
denominator basis, which is the property the rejected derivation lacked.

### Unit

```text
f8 = 2.35  ->  2.35 percent
CANONICAL UNIT = PERCENT   (never divided by 100 into a ratio)
```

PERCENT matches the frozen V1 turnover contract, so the semantics are unchanged
across the source swap.

## 3. The additive patch

CNEquity is a git dependency resolved into `.venv`, not a checkout, so the patch
is applied to the installed package with its provenance recorded in
`patches/cnequity/`.

```text
CNEQUITY_UPSTREAM_BASE = 0.8.0 @ d453853da766b3ba3e44489c0fb6e0089243fa25
PATCH                  = patches/cnequity/0001-valuation-metrics-turnover-rate.patch
UPSTREAM_PR            = not submitted
```

| File | Change |
|---|---|
| `adapters/eastmoney/valuation.py` | `_VALUATION_FIELDS` gains `f8`; maps `f8` to `turnover_rate` |
| `domain/schemas.py` | `VALUATION_METRICS_SCHEMA` gains `turnover_rate: pl.Float64` |
| `domain/datasets.py` | `unit_contract` gains `turnover_rate: PERCENT` |

```text
VALUATION_SCHEMA_BEFORE = symbol, trade_date, pe_ttm, pb, ps_ttm, total_mv,
                          float_mv, source, data_version, fetched_at
VALUATION_SCHEMA_AFTER  = (same) + turnover_rate: Float64 (nullable)
```

Diff of the generated registry contract against the shipped
`contracts/v0.8.0.json`: one added column, no removed columns, one unit entry.
`cne contract validate --against-registry` returns Contract OK. The shipped
v0.8.0 contract file was deliberately not rewritten - it documents the upstream
release and editing it would forge an upstream artifact.

### Missing and invalid values

The adapter's existing `_to_float` already maps `None`, empty string, `-`, junk,
`nan` and `inf` to **null**, while a genuine observed `0` stays `0`. No new
handling was needed and nothing is coerced to zero. `load_sources` stores only
values that are present and finite, so a null never enters the mapping as a zero.

## 4. Measured provider cost

The EastMoney client was instrumented to count real page requests:

```text
VALUATION_REQUEST_N    = 61 clist page requests (instrumented)
VALUATION_ROW_N        = 5453 rows
TURNOVER_NON_NULL_N    = 5453
PER_SYMBOL_REQUEST_N   = 0
TDX_REQUEST_N          = 0
BAOSTOCK_REQUEST_N     = 0
OFFICIAL_DISCLOSURE_REQUEST_N = 0
```

61 page requests is the same page-walk the adapter already performed before the
patch (`page_size=100` over the all-A universe); `f8` rides along in the same
response. **Adding turnover_rate adds no provider call.**

## 5. Two findings that changed the plan

### 5.1 The clist is a live snapshot, and its effective session is not the requested one

The `valuation_metrics` rows built in the previous round for 2026-09-09 and
2026-09-10 were **byte-identical in all five business columns**. They were one
live snapshot stamped with two dates. `f124` (the vendor's own last-update
timestamp) read `2026-09-10 15:34:09` Asia/Shanghai, confirming the snapshot
describes the 2026-09-10 session.

CNEquity declares `valuation_metrics` as `fetch_semantics = snapshot` and warns
that historical replay would forge rows, so only the run day is ever fetched.
Passing `trade_date=2026-09-09` produced exactly the forged row that warning
describes. The 09-09 partition was therefore invalid and was not carried forward.

### 5.2 f8 resets before the next session opens

At 08:47 CST `f8` returned real values for the 09-10 session. At 08:56 CST the
same request returned **0.0 for all 5,913 symbols**. EastMoney zeroes the
turnover field as it rolls into the next trading day, while price fields still
show the previous close.

Consequences, both measured:

```text
post-close fetch     -> f8 is that session's turnover (the supported path)
pre-open fetch       -> f8 is 0 while prices are stale; the row is a
                        mixed-dated artifact and must not be published
next-morning catch-up -> cannot recover the missed session's turnover
```

CNEquity's own schedule already runs this step after the 16:00 close, so the
daily path is unaffected. The consequence is that clist **cannot backfill a
missed turnover day** - which is precisely why the registry pairs a `snapshot`
daily source with `backfill_source = baostock`.

The bounded rebuild for 09-10 therefore produced 5,453 rows whose
`turnover_rate` was uniformly `0.0`. That is a real observed zero, not a missing
value, and `_to_float` correctly preserved it - but as a turnover series for
09-10 it is wrong. It was **quarantined rather than published**, and the dataset
was left empty. The correct post-close run repopulates it.

## 6. Turnover result

### 6.1 The requested 09-09 comparison is not producible

```text
EXPECTED_N         = 5208
V02_ROW_N          = 5208
COMPARABLE_N       = 0
SUSPENDED_N        = 10  (null by contract)
UNRESOLVED_N       = 5198
ROOT_CAUSE         = MISSING_PROVIDER_VALUE  (DATE_ALIGNMENT, section 5.1)
```

The clist cannot describe 2026-09-09, so no 09-09 turnover candidate exists to
compare. This is a property of the source, not a disagreement between vendors.

### 6.2 Same-session full-market validation

The equivalence question was answered on the session the snapshot actually
describes, using TDX for the numerator and BaoStock for the denominator:

```text
COMPARABLE_N    = 5196
MATCH within 1% = 5069  (97.56%)
MATCH within 2% = 5164  (99.38%)
exact match     = 0     (f8 publishes 2 decimals, BaoStock 4)
MAX_ABS_DIFF    = 35.852871   (301575.SZ, a share-count change case)
P50_ABS_DIFF    = 0.002520
P95_ABS_DIFF    = 0.004780
P99_ABS_DIFF    = 0.004982
```

### 6.3 Root causes - no UNKNOWN

| Class | Count | Share |
|---|---|---|
| `MATCH_WITHIN_1PCT` | 5069 | 97.56% |
| `LOW_TURNOVER_DISPLAY_ROUNDING` | 118 | 2.27% |
| `SHARE_DENOMINATOR_MOVED` | 9 | 0.17% |
| `UNKNOWN` | **0** | 0.00% |

`LOW_TURNOVER_DISPLAY_ROUNDING` is dominated by large low-turnover banks
(`601998.SH` 0.08 vs 0.0751, `601288.SH` 0.09 vs 0.0858): at two decimals the
representation error reaches 0.005 absolute, which is 5-6% relative at a value
of 0.08. 25 of the 32 keys above the 2% relative threshold have an absolute
difference at or below 0.01 - they are precision, not disagreement.

`SHARE_DENOMINATOR_MOVED` is a real float change between the two sessions, so a
denominator implied from the earlier session is simply stale for the later one.
`301575.SZ` is the extreme case: a 24.4M-share float showing 18.05% turnover,
implying the float grew roughly threefold by the second session.

```text
DISPLAY_PRECISION_EQUIVALENCE = accepted, demonstrated by the distribution
                                above rather than asserted
```

## 7. Full V02 shadow after the change

```text
preclose      MATCH 5208 / MISMATCH 0     PASS
pct_chg       MATCH 5208 / MISMATCH 0     PASS
trade_status  MATCH 5208 / MISMATCH 0     PASS
is_st         MATCH 5208 / MISMATCH 0     PASS
turnover_rate COMPARABLE 0                NOT PRODUCIBLE for 2026-09-09
```

The four previously certified fields are unchanged by this round: no other V02
field logic was touched, and the ST-while-suspended guard still holds
(`V02_RISK_WARNING_TRUE_ON_SUSPENDED` = `ST_WHILE_SUSPENDED_IN_V1` = 3).

```text
V02_SHADOW_CERTIFICATION = PASS_WITH_LIMITATIONS
   four fields certified equivalent; turnover's canonical source is now defined
   and cross-validated, but its 2026-09-09 key is not producible from a
   snapshot source, so the historical comparison itself cannot be run.
```

## 8. Upstreamability

```text
IS_PATCH_UPSTREAMABLE = YES
```

```text
files changed         : 3 (adapter, schema, dataset unit contract)
schema change         : + turnover_rate: Float64, nullable
adapter field         : f8 added to _VALUATION_FIELDS, mapped to turnover_rate
tests                 : parse / null / invalid, unit contract, schema backward
                        compatibility, no-extra-request assertion
backward compatibility: additive; PK, existing columns, semantics unchanged
upstream caveat       : validate_dataframe requires every schema column, so
                        existing partitions need a rebuild - upstream may want
                        a legacy-normalisation path as it did for the
                        trading_status risk_warning split
```

No pull request was opened.

## 9. Lake changes

```text
curated/valuation_metrics : left EMPTY (see section 5.2)
quarantine : staging/valuation_metrics_quarantine_v01_pre_turnover/
    trade_date=2026-09-09       pre-patch, and a forged stamp of the 09-10 snapshot
    trade_date=2026-09-10       pre-patch, correct session but no turnover_rate
    zero_build_preopen_20260911 post-patch rebuild taken pre-open: turnover 0.0
    meta_revisions/*            superseded committed generations and pointers
    staging_runs/*              two pre-patch staging runs
```

Nothing was deleted; every superseded artifact is preserved in quarantine.

```text
R3_AUTHORITY_UNCHANGED       = 140197cd3c95a3f8e1c9f5750f117c66eafc09d9113757e511f31e46848bf66b
FACTS_V1_AUTHORITY_UNCHANGED = 9b4f474ebcb0db97d9dbfdb824eb6b7e17f017a6ac8957ed3d6a8fb5e3ca21ac
V02_POINTER                  = none created
2026-09-10_FACTS_PUBLISHED   = no
```

Both pointers were re-verified against their own manifests.

## 10. Tests

```text
.venv     : 186 passed, 1 failed (pre-existing untracked file, unchanged)
.venv-mcp : 88 passed
new       : tests/test_valuation_turnover_rate_v01.py - 18 passed
py_compile: passed
cne config validate : Configuration OK
cne contract validate --against-registry : Contract OK
```

New coverage: f8 requested by the adapter, f8 numeric parsing, f8 null / empty /
dash / nan / inf all staying null while a genuine 0 is preserved, the row
mapping, the schema type, unchanged existing columns, unchanged primary key, the
PERCENT unit contract, the additive compatibility class, the registry contract
validating, the bulk-only acquisition shape (no `query_history_k_data_plus`, no
`fetch_per_symbol`), the V02 rule id swap with the old derivation retained as
audit-only, the source bundle carrying the field, null-never-zero in
`load_sources`, the unresolved-blocker path, suspended rows keeping null turnover,
the four other fields still matching, and no stale pre-patch partition remaining
in the curated tree.

## 11. Known limitations

```text
1. The 2026-09-09 turnover comparison the task asked for cannot be produced:
   EastMoney's clist is a snapshot of the current cycle and cannot describe a
   past session. The equivalence evidence is same-session (09-10) instead.
2. valuation_metrics is currently empty. The only in-scope rebuild window this
   round ran pre-open and returned zeros; it was quarantined rather than
   published. The next post-close run repopulates it.
3. f8 publishes 2 decimals against BaoStock's 4, so exact match is 0 by
   construction and the meaningful threshold is the relative one.
4. The cross-vendor validation uses a BaoStock denominator implied from the
   previous session, which is stale for the 9 keys whose float changed; those
   are classified, not corrected.
5. The added column makes validate_dataframe reject pre-patch partitions, so any
   other deployment holding valuation_metrics history needs a rebuild.
6. tests/test_cnequity_v080_daily_reuse.py still fails; it is untracked, was not
   authored here, and is unrelated.
```

## 12. Next gate

```text
NEXT_GATE = V02 authority migration + the first production publication

Turnover becomes shadowable end-to-end only once valuation_metrics is populated
by a post-close run, which yields the session it describes and can then be
compared against that same session's BaoStock backfill.
```
