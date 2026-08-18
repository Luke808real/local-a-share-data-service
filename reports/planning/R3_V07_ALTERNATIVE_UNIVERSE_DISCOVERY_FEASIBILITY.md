# R3 V07 — Security-Universe Discovery without Sina: Technical Feasibility

**Prepared by:** local execution agent (author review only; NOT a contract or
architecture change)
**For:** GPT-5.6 Sol (external decision-maker)
**Date:** 2026-08-18
**Status:** `FEASIBILITY_INFO_ONLY` — requires explicit Sol decision before any
revision of the frozen R3 discovery/data-source contract.

## Problem

Sina `money.finance.sina.com.cn` returns HTTP 456 (WAF block) for this host
under sustained load and even for single probes after a 65 s cooldown. The
frozen R3 plan (`d13e2ecefbb66250b73aca4312dc8706a4d2b7a3`) makes Sina the
discovery probe for the full issued code space (Stage B) and the primary BJ
daily route (Stage F2), so R3 Stage B cannot complete. Evidence is preserved in
`meta/asl/r3/r3-blocker.json` and the service ledger; Stage A (instruments) is
complete and compacted; nothing is restarted from zero.

This report evaluates **already-pinned** CNEquity v0.7.2
(`a18ee0484dfb0801650175471724def3228b8a17`) capabilities only. No new
dependency is proposed.

## Current pinned discovery path (Sina)

- `discover_delisted(config, limit=...)` probes codes with
  `sina.bars.symbol_exists` / `fetch_daily_bars_sina`
  (`cnequity/steps/delisted.py:193-254`, `cnequity/adapters/sina/bars.py`).
- Purpose: classify an *issued but not currently listed* code as `delisted` vs
  `never_issued`, so the all-A universe is not survivorship-biased (Master Spec
  §5). SH/SZ/BJ codes are all walked.

## Already-pinned alternative capabilities (evidence)

### Baostock — SH/SZ identity, including delisted (no Sina)

- `baostock.instruments.fetch_instrument_basics()` returns listed **and**
  delisted SH/SZ names with `list_date`/`delist_date`
  (`adapters/baostock/instruments.py:88-131`). Stage A already used it and
  merged 337 delisted rows into `instruments`.
- Historical **per-day roster**: `baostock.delisted_bars.roster_on(day)` calls
  `query_all_stock(day)` and filters to stocks
  (`adapters/baostock/delisted_bars.py:41-95`). This is exactly the pinned
  mechanism behind `_delisted_universe` in `steps/delisted.py` (survivorship-gap
  detection), and `step_daily_bars_delisted` / `fetch_delisted_bars`
  (`adapters/baostock/delisted_bars.py`) recover those SH/SZ bars — none of
  which need Sina.
- Limitation: `_is_stock` accepts only SH `60/688` and SZ `00/30`
  (`delisted_bars.py:41-57`); **Baostock carries no BJ**.

### EastMoney clist — current BJ identity (no Sina, snapshot only)

- `eastmoney.clist.fetch_clist_pages(fs=ALL_A_FS)` includes
  `m:0+t:81+s:2048`; `symbol_from_clist` maps `f13=2` to `.BJ`
  (`adapters/eastmoney/common.py:10,117-134`). Fields `f14` (name) and `f26`
  (list date) are usable for current BJ identity (Stage C2 already uses this).
- Limitation: clist is a **current** cross-section. It cannot enumerate
  historical delisted BJ codes.

### EastMoney push2his kline — BJ daily bars (no Sina)

- `eastmoney.bars.fetch_daily_bars` maps BJ via `_MARKET["BJ"]="2"`
  (`adapters/eastmoney/bars.py:44-74`) and direct reachability was verified
  (`push2his.eastmoney.com` reachable; root 404 is normal).
- The frozen plan already permits EastMoney as the *gap fallback* route for
  non-TDX symbols; making it the BJ primary would be a provider-authority
  change (Sol decision required).

## Feasibility by concern

| Concern | Pinned alternative | Feasible without Sina? |
|---|---|---|
| SH/SZ identity incl. delisted | Baostock `stock_basic` + per-day rosters | **YES** (existing pinned mechanics) |
| SH/SZ survivorship-gap bars | Baostock `fetch_delisted_bars` | **YES** (pinned route) |
| Current BJ identity (active) | EastMoney clist (`f12/f13/f14/f26`) | **YES** (already used in C2) |
| BJ daily bars | EastMoney push2his kline | **YES as a route**, provider authority change → Sol |
| Historical delisted BJ discovery | None pinned | **NO** — no non-Sina pinned source enumerates historical BJ code space |

## Honest boundary for a V07 contract

1. SH/SZ universe completeness can be proven with Baostock identity + rosters,
   independent of Sina, using already-pinned code paths.
2. Historical delisted **BJ** cannot be proven from any pinned source without
   Sina. A V07 contract could either:
   - (a) keep BJ historical-delisted coverage as explicit
     `UNKNOWN`/fail-closed (documented limitation), while covering current BJ +
     BJ daily bars via EastMoney; or
   - (b) require a new (unpinned) discovery source — a new-dependency decision.
3. Any adoption of (1), (2a), or BJ-primary-EastMoney requires an explicit
   GPT-5.6 Sol decision that revises the frozen R3 discovery/provider
   semantics; the local agent will not self-approve it.

## Recommendation for the decision-maker (not an approval)

Adopt (1)+(2a) if the project accepts the documented BJ historical-delisted
`UNKNOWN` boundary; otherwise require a new source. Either path needs one
Sol-authorized R3 contract revision (V07) followed by the normal independent
plan audit before data execution resumes.

## Sol decision `approve-v07-revision` — evidence bullets (concise)

- **Old dependency graph:** Stage B probes the full `issued_code_space()` via
  Sina `getKLineData`; 200+data => delisted, 200+empty => never_issued,
  SOURCE_ERROR => pending (hard gate remaining=0). Stage E/F2 also hard-depend
  on Sina for BJ bars.
- **Proposed V07 dependency graph:** SH/SZ current from Stage A (TDX+EM);
  SH/SZ historical from Baostock `stock_basic` identity + per-day `roster_on`
  (positive, closed); BJ current from EM clist f12/f13/f14/f26; BJ historical
  from pinned research step or `UNKNOWN_CARRIED`; BJ daily from EM push2his;
  Sina sweep downgraded to optional crosscheck.
- **SH/SZ current authority:** TDX live security list (+EM clist date
  enrichment) — Stage A already done (7757 rows).
- **SH/SZ historical authority:** Baostock (identity + rosters + delisted
  bars) — equivalent/stronger than the Sina sweep for survivorship.
- **BJ current authority:** EastMoney clist (f13=2 -> .BJ; f14 name, f26 list
  date) — already used by Stage C2.
- **BJ historical authority:** `UNPROVABLE_PINNED` expected; no pinned
  non-Sina source enumerates historical BJ codes. Falls to
  `HISTORICAL_DELISTED_BJ = UNKNOWN_CARRIED` (explicit, hashed, fail-closed).
- **Disposition of partial Sina catalog:** preserved as CROSSCHECK /
  supplementary evidence only; never used as a completion authority.
- **NEVER_ISSUED exhaustive sweep still necessary?** No per MASTER_SPEC (which
  requires delisted presence + resolvability, not enumeration of non-existent
  codes). Downgraded HARD_GATE -> OPTIONAL_CROSSCHECK for SH/SZ because
  Baostock rosters positively close the SH/SZ traded universe; remains a real
  information gap only for BJ historical delisted (UNKNOWN_CARRIED).
- **Resume point:** from completed Stage A (7757 rows, 337 delisted; partial
  discovery catalog/ledger preserved); next ratified step = V07 Stage B
  identity completion, then C/C2/D/E/F/G per the V07 plan.
- **Remaining blockers:** (1) V07 independent plan audit; (2) BJ historical
  delisted identity (UNKNOWN_CARRIED) until a pinned source proves it;
  (3) Sina SOURCE_FRAGILE — crosscheck only, no hard gate.

Full revision contract: `docs/plans/R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN_V07.md`.
