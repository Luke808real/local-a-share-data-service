# ASL CNEquity 0.8.0 pin upgrade + Daily Facts V02 2026-09-09 full-market shadow certification

## 0. Scope

This round performed the authorized production pin upgrade and produced the
2026-09-09 full-market V02 shadow. It did **not** publish V02, switch any
pointer, or touch the R3 / V1 authorities.

```text
BASE_HEAD = 9ad068e48899209ba531b66d9f3aa50dce8c48d0
BRANCH    = codex/r3-incremental-hardening-daily-facts-phase1-v01
```

## 1. Production pin upgrade

| | before | after |
|---|---|---|
| version | `0.7.2` | `0.8.0` |
| commit | `a18ee0484dfb0801650175471724def3228b8a17` | `d453853da766b3ba3e44489c0fb6e0089243fa25` |
| import path | `.venv/lib/python3.12/site-packages/cnequity` | unchanged |

Changed: `pyproject.toml` (exact commit pin, not `main`), `uv.lock`
(`rev=d453853d...`), plus the R2 baseline constants in
`tools/verify_r2_baseline.py` and `tests/test_r2_baseline_contract.py`, and the
`phase5_derive_and_publish` init phase that 0.8.0 added. Runtime provenance is
verified from `direct_url.json`
(`requested_revision = commit_id = d453853da766...`), not from the text pin.
`.venv-mcp` deliberately still has no CNEquity installed.

### Read-only verification after the upgrade

```text
cne doctor --config config/cnequity.toml        -> all clear
cne config validate --config config/cnequity.toml -> Configuration OK
cne status --datasets                            -> every dataset readable
cne verify                                       -> readable, gaps as expected
ASL R3 hardened updater dry-run (2026-09-11)     -> READY
    frozen_n = 5456, eligible_n = 5208, network_request_n = 0
```

The dry-run's parameters are identical to the pre-upgrade run, so the upgrade is
transparent to the R3 path. Existing `daily_bars` (through 2026-09-10),
`instruments`, `corporate_actions`, `share_structure`, `trading_calendar`, the
watermarks and `meta/manifest.db` all read normally.

`migrate_trading_status_risk_warning.py --apply` was **not** run: this lake has
no `trading_status` files to migrate, and it is an in-place rewrite with no
built-in rollback.

## 2. Datasets built, and one reused

All through CNEquity's native steps (`JobEngine.run_job(..., steps=[...])`),
never an ASL per-symbol fetcher.

| Dataset | Action | Provider | Rows | Step time | Files | Watermark |
|---|---|---|---|---|---|---|
| `trading_status` | built for 2026-09-09 and 2026-09-10 | eastmoney bulk + `derived_delisted` | 15,492 | 2.7 s / 5.4 s | 1 | 2026-09-09 |
| `valuation_metrics` | built for the same two dates | eastmoney clist bulk | 10,906 | 31.5 s / 33.4 s | 2 | 2026-09-09 |
| `share_structure` | **reused**, not re-fetched | eastmoney (pre-existing) | 166,911 | 0 | 26 | n/a (PIT) |
| `corporate_actions` | **not re-fetched** | pre-existing | 40,694 | 0 | 11 | 2026-09-08 |

`share_structure` validates against the 0.8.0 schema unchanged
(`SHARE_STRUCTURE_REUSED = true`), so no coverage gap justified a re-fetch.

### trading_status semantics, as actually produced

```text
status        : normal 14,796 | suspended 22 | delisted 674   (two dates)
risk_warning  : True 640 | False 14,852 | null 0
source        : eastmoney 14,818 | derived_delisted 674
on 2026-09-09 : normal 7,399 | suspended 10 | delisted 337
```

## 3. The V02 candidate

`src/ashare_data/daily_facts_v02.py` was re-reviewed against the 0.8.0 contract
rather than trusted because it already existed. It reads only admissible inputs
and its input guard refuses any path containing a forbidden token.

### Decisions frozen before implementation

Recorded in `docs/contracts/DAILY_FACTS_V02_SOURCE_CONTRACT_V01.md`.

**Decision A - a `delisted` status is a lifecycle fact, never a daily value.**
`delisted -> SUSPENDED` and `delisted -> TRADING` are both forbidden. The key
belongs to `OUTSIDE_ELIGIBLE_LIFECYCLE`, and if a `delisted` key appears inside
the date's frozen eligible universe that is
`ELIGIBILITY_LIFECYCLE_CONFLICT` and certification fails closed.

**Decision B - strict point-in-time turnover denominator.** A record must satisfy
`announce_date <= trade_date` **and** `change_date <= trade_date`; the latest such
record wins. A future-dated value is never a fallback. This is stricter than
filtering on `change_date` alone, and it costs nothing here: all 5,208 eligible
symbols have a strict-PIT record (`TURNOVER_UNRESOLVED_N = 0`).

### Two defects found and fixed during the run

1. **Prior-session walk-back.** The first shadow could not derive `preclose` for
   9 symbols, because the R3 authority encodes a suspension as an *absent* row:
   those nine were suspended on 2026-09-08 and have no 2026-09-08 row at all.
   Reading only the immediately preceding session therefore lost the last
   observed close. The generator now walks back over prior sessions (bounded at
   12) and takes the last actual close, which is the same basis the exchange
   uses on a resumption. This resolved all 9.
2. **Suspended rows must publish null, not zero.** One halted symbol derived
   `pct_chg = 0.0` because its zero-volume carry-forward bar made
   `close == preclose`. The frozen V1 semantics publish null for a halted
   session, and a derived `0.0` would claim a flat close the market never
   printed. `pct_chg` and `turnover_rate` are now null whenever
   `trade_status = SUSPENDED`. This resolved the last pct_chg mismatch.

## 4. Full-market shadow comparison

```text
EXPECTED_N      = 5208
V02_ROW_N       = 5208
MISSING_PK_N    = 0
EXTRA_PK_N      = 0
DUPLICATE_PK_N  = 0
rows_hash       = 358132ce95846191bb410735fd75dc9e9bf6ca03bebcbd6c76905be059f0a778
```

| Field | Match | Mismatch | Unknown | Max abs diff |
|---|---|---|---|---|
| `preclose` | 5,208 | **0** | - | 0.0 |
| `pct_chg` | 5,208 | **0** | - | 4.9999e-05 |
| `trade_status` | 5,208 | **0** | 0 | - |
| `is_st` | 5,208 | **0** | 0 | - |
| `turnover_rate` | 4,923 | 275 | 0 | 6.4138 |

```text
turnover: COMPARABLE_N 5198 | MATCH_N 4923 | MISMATCH_N 275
          NULL_EXPECTED_N 10 | UNRESOLVED_N 0
          MAX 6.4138 | P50 3.0071e-05 | P95 0.02297 | P99 0.53207
```

`preclose`, `pct_chg`, `trade_status` and `is_st` reproduce the certified V1
values exactly for all 5,208 keys. `preclose` matches to `0.0` absolute
difference, including all 16 reference-price exceptions.

## 5. Suspension and reference-price verification

```text
V1_SUSPENDED_N  = 10    V02_SUSPENDED_N = 10
V1_ONLY         = []    V02_ONLY        = []
every suspended row: pct_chg = null, turnover_rate = null, R3 volume 0, amount 0
```

CNEquity 0.8.0's suspension classification agrees with V1 on all ten keys, and
the V02 rows keep the frozen null semantics. No silent repair was needed and
none was applied.

```text
V02_REFERENCE_PRICE_EXCEPTION_N = 16    V1 = 16    symbol sets identical
```

All sixteen were derived from the already-certified official evidence
(`reference_price_evidence_n = 16`), with the ROUND_HALF_UP-to-0.01 tick rule,
and reproduced the V1 preclose exactly. The regression cases held:
`002322.SZ` (0.3251058), `300196.SZ` (0.1974617), `688271.SH`. No network request
was issued to re-obtain them.

## 6. The ST redesign validated

This is the concrete payoff of the 0.8.0 two-column redesign.

```text
V1_IS_ST_TRUE_N  = 201    V02_IS_ST_TRUE_N = 201
V1_ONLY = []             V02_ONLY = []
ST_WHILE_SUSPENDED_IN_V1              = 3
V02_RISK_WARNING_TRUE_ON_SUSPENDED    = 3
```

Three names were both halted and under risk warning on 2026-09-09. Under the
old single-column encoding a halt dropped the ST label; under 0.8.0
`status=suspended` and `risk_warning=true` coexist and the V02 mapping preserves
both. The three symbols match the same three in V1, and no suspended row lost
its label.

## 7. Turnover: semantic equivalence is NOT proven

This is the one field that does not certify, and the finding is precise.

```text
275 / 5198 comparable  (5.3%)  diverge
direction: strictly one-way - the V02 denominator is always the larger one
  ratio > 1 : 0
  ratio < 1 : 275
```

Root-cause classification, computed in the comparison:

```text
FREE_FLOAT_BASIS              = 13   (free_float_shares reproduces V1's basis)
UNRESOLVED_DENOMINATOR_BASIS  = 262
```

Evidence gathered to decide which side is wrong:

1. **R3's volume unit is not the problem.** `amount / (close * volume)` has
   median 0.9990 in the mismatch group and 1.0020 in the match group; the lake
   volume is shares and is internally consistent everywhere.
2. **EastMoney's own same-day quote disagrees with the V02 denominator for these
   symbols.** `valuation_metrics.float_mv / close` reproduces V1's implied
   denominator to a median 2.6% relative difference, while the V02
   `float_shares` sits at a median 1.56x of it. In the match group both agree at
   ~1.015x. So for the mismatch group the V02 denominator is the outlier.
3. **`float_shares` is still the best available column.** Scored across all
   comparables: `float_shares` 4,923/5,198 within 0.1%, `free_float_shares`
   213/5,189. Replacing the column wholesale would break far more than it fixes.
4. **No share-structure record explains the 262.** For sampled symbols the target
   lies below *every* record's `float_shares` and no earlier record matches, so it
   is not a wrong-record selection.

The most plausible single cause is that EastMoney's share-structure report
carries a consolidated all-share-class unrestricted count for multi-class
issuers (`A+H`, `A+B`) while the exchange's turnover denominator and EastMoney's
own `float_mv` use the A-share tradable float. That would explain the
one-directional bias (consolidated >= A-only) and why the 4,923 single-class
names agree. **It could not be confirmed from local data**, because the
`instruments` catalog holds A-shares only — there are zero `200xxx` / `900xxx`
rows to test sibling share classes against.

```text
TURNOVER_SEMANTIC_EQUIVALENCE = NOT_PROVEN
   The field is derivable, unit-correct and identical for 94.7% of the market,
   but it must not become the canonical published fact on this denominator
   until the basis question in (5) is settled.
```

## 8. Source independence and PIT

```text
SOURCE_INDEPENDENCE = PASS
PIT_LEAKAGE_N       = 0
```

Enforced three ways, all covered by tests:

1. `load_sources` refuses any dataset path containing a forbidden token
   (`raw/baostock`, `daily_facts_phase1`, `part-full-eligible`, ...), so a lake
   laid out that way raises `V02_SOURCE_DEPENDENCE_VIOLATION`.
2. `generate()` and `compare()` are separate functions; a test asserts the
   generator's source contains neither the V1 reader nor the V1 pointer, while
   the comparator does.
3. The generator module neither imports nor references the V1 producer.

PIT is enforced by construction: only records with both
`announce_date <= trade_date` and `change_date <= trade_date` are eligible, and a
record with no `announce_date` is dropped rather than assumed available. Tests
cover the record-A/record-B case (effective but not yet disclosed loses to a
fully available one), the future-`change_date` rejection, and the no-fallback
path.

## 9. Provider request discipline

```text
BAOSTOCK_PER_SYMBOL_DAILY_REQUEST_N = 0
BAOSTOCK_REQUEST_N                  = 0
TDX_REQUEST_N                       = 0   (no R3 fetch this round)
OFFICIAL_DISCLOSURE_REQUEST_N       = 0   (2026-09-09 evidence already certified)
```

EastMoney is the only source contacted, and only through bulk endpoints:

| Call | Bootstrap (2 dates) | Normal future day |
|---|---|---|
| ST board `clist` (`page_size=100`, 205 rows) | ~6 | ~3 |
| suspension list `tfc/list2` | ~2 | ~1 |
| `valuation_metrics` `clist` (5,453 rows) | ~110 | ~60 |
| **total** | **~118** | **~64** |

These are derived from the adapters' pagination arithmetic and the observed row
counts rather than from a request counter; the push2 host also needed the
documented `push2delay` failover on some pages. `share_structure` and
`corporate_actions` were not fetched at all.

## 10. Runtime, measured

```text
TRADING_STATUS_RUNTIME       = 2.7 s / 5.4 s per date (step; compact ~30 s shared)
VALUATION_METRICS_RUNTIME    = 31.5 s / 33.4 s per date
SHARE_STRUCTURE_RUNTIME      = 0   (reused)
V02_LOCAL_DERIVATION_RUNTIME = 0.51 s for 5,208 keys
SHADOW_CERTIFICATION_RUNTIME = 0.7 s generate + compare
```

```text
EXPECTED_NORMAL_DAILY_REQUEST_PATTERN = ~64 EastMoney bulk requests + the existing
                                        105 TDX batches (R3); zero per-symbol calls
EXPECTED_NORMAL_DAILY_FACTS_RUNTIME   = ~1-2 min of provider time + ~0.5 s local
                                        derivation, versus the ~10 h per-symbol
                                        BaoStock sweep it replaces
```

## 11. Tests

```text
.venv     : 168 passed, 1 failed (a pre-existing untracked file, see below)
.venv-mcp : 88 passed  (MCP + LocalQuery suites)
py_compile: passed (module, shadow tool, R2 verifier)
cne config validate: Configuration OK
new       : tests/test_daily_facts_v02.py — 27 passed
```

`tests/test_daily_facts_v02.py` covers: the 0.8.0 pin (both the text pin and the
installed runtime's `direct_url` commit), the two-column schema and legacy
normalization, ordinary and reference-price preclose, half-up rounding, the
treasury-share and differential-distribution cases, `pct_chg` derivation,
turnover unit contract and its failure modes, the full status / `risk_warning`
mapping, strict PIT acceptance and both leakage rejections, the lifecycle rule
including the delist-date boundary, source independence, and two lake-backed
checks asserting the 5,208-row shadow and the ST-while-suspended preservation.

### One pre-existing failure, not fixed

`tests/test_cnequity_v080_daily_reuse.py::test_v080_partial_stage_narrow_retry_and_cross_run_reuse`
fails at `assert reused == set(symbols)`. The file is **untracked and was not
authored in this round**; it exercises the upstream internal
`_reuse_successful_daily_bars`, and the staged data and manifest batch it sets up
are correct up to that final assertion. It was deliberately left in place rather
than deleted or guessed at, per the instruction not to remove the user's
untracked files. It is unrelated to the Daily Facts V02 candidate.

Two stale assertions in `tests/test_local_query.py` were updated, because they
hardcoded the pre-2026-09-10 R3 publication (2,597 files and the old manifest
hash). They now assert consistency with the published pointer, so the next
date advance cannot make them stale again.

## 12. Authorities

```text
R3_AUTHORITY_UNCHANGED     = 140197cd3c95a3f8e1c9f5750f117c66eafc09d9113757e511f31e46848bf66b
FACTS_V1_AUTHORITY_UNCHANGED = 9b4f474ebcb0db97d9dbfdb824eb6b7e17f017a6ac8957ed3d6a8fb5e3ca21ac
V02_POINTER                = none created
DAILY_FACTS_09_10_PUBLISHED = no
```

Both pointers were re-verified against their own manifests after every mutation.
`FACTS_READY` and `PRECLOSE_COMPLETE` remain false.

## 13. Certification

```text
5208 structural completeness ......... PASS
preclose zero unexplained ............ PASS  (0 mismatches)
pct_chg zero beyond tolerance ........ PASS  (0 mismatches)
trade_status zero unexplained ........ PASS  (0 mismatches)
is_st zero unexplained ............... PASS  (0 mismatches)
turnover semantic equivalence ........ NOT PROVEN (275/5198)
UNKNOWN_N = 0 ........................ PASS
SOURCE_ERROR_N = 0 ................... PASS
PIT_LEAKAGE_N = 0 .................... PASS
SOURCE_INDEPENDENCE .................. PASS

V02_SHADOW_CERTIFICATION = PASS_WITH_LIMITATIONS
```

Four of the five fields are certified equivalent to the BaoStock-certified V1
facts across the whole eligible market, so the source architecture does replace
the acquisition architecture for them. Turnover is the exception and is
recorded as such rather than rounded up.

## 14. Known limitations

```text
1. turnover_rate is not certified (5.3% denominator-basis divergence, section 7);
   the A+H / A+B hypothesis could not be tested because the catalog holds only
   A-share instruments.
2. The two EastMoney boards are current-state reads stamped onto a past session.
   CNEquity ranks the ST board as restated evidence; the 2026-09-09 rows are
   therefore correct as observed today, and the ST comparison is only as strong
   as the assumption that the board did not move between 09-09 and today.
3. corporate_actions still has no 2026-09-09 rows, so the exception *trigger* is
   the certified official evidence rather than the event dataset. Detection from
   corporate_actions alone is untested for a date whose events it holds.
4. Provider request counts are code-derived, not counted by an instrumented client.
5. The 2026-09-09 trading_status was built for that date only; no history was
   backfilled, and the one file carries two dates.
6. tests/test_cnequity_v080_daily_reuse.py still fails (section 11).
```

## 15. Next gate

```text
NEXT_GATE = resolve the turnover denominator basis, then a separate small gate to
            migrate the V02 authority. Neither is authorized in this round.

Concretely, the basis question needs one of:
  (a) an official per-symbol A-share float for multi-class issuers, or
  (b) a decision to publish a documented A+B/A+H consolidated basis, or
  (c) acceptance of the divergence as a known 5.3% limitation.
```
