# R3 V08 SH/SZ DAILY FOUNDATION — FINAL AUTHOR REPORT

AS_OF: 2026-08-19 (data AS_OF 2026-08-17)
AUTHOR_STATUS: PASS — AUTHOR ONLY (independent audit pending)
CODE_HEAD: 3914b7a4988f3d202eba5b6b81b3069aec78bd4e
PLAN_SHA: 3ab1f184edeea1d0e408c45df4a706248b6558d0
V08_SCOPE_DECISION_SHA: 00085fed36f50312b6a5475dc26f0c5e347c6768

## A. Scope

SH/SZ MVP daily foundation. BJ = DEFERRED_EXTENSION (current and historical).

## B. Authority

- SH/SZ historical identity: Baostock stock_basic (frozen Stage-B receipt)
- recovery target: Stage-B formal delisted map (SH/SZ)
- daily bars: TDX (active SH/SZ), Baostock (formal delisted recovery)
- G completion authority: formal identity + E closure + known coverage

## C. Final data counts

- FORMAL_IDENTITY_N = 5456 · ACTIVE_REQUIRED_N = 5208 · ACTIVE_OBSERVED_N = 5208
- FORMAL_DELISTED_N = 248 · E_RECOVERED_N = 248 · E_UNRESOLVED_N = 0
- DAILY_ROWS = 10,709,989 · DAILY_SYMBOL_N = 5456 (TDX 10,325,794 + Baostock 384,195)
- MIN_TRADE_DATE = 2016-01-04 · MAX_TRADE_DATE = 2026-08-17

## D. Final QA

duplicate_pk=0 · null_pk=0 · post_asof=0 · invalid_ohlc=0 ·
negative_volume=0 · negative_amount=0 · missing_required=0 ·
without_positive_volume=0 · out_of_effective_span=0

## E. F recovery history summary

- First F attempt: 136 of 5215 SH/SZ active symbols failed. Diagnostic singleton
  probes showed 128 were batch collateral (valid as singletons) and 8 were
  persistent transport failures.
- Fix: failed-run staging reuse with singleton TDX recovery (one symbol per
  batch) instead of a whole-market refetch.
- Prelisting contamination: the 8 persistent symbols had NULL curated list_date
  and were pre-listing as of 2026-08-17 (Baostock proves 688826 listed
  2026-08-18; the other 7 absent from the ASOF formal identity and roster).
- Fix: F ASOF scope planner binds NULL-list-date candidates to the frozen
  Stage-B authority; they are resolved or excluded (never 2016 full-window).
- Fix: safe reuse of the failed run — copy 1626 successful staging batches,
  drop 7 authoritative expected_no_data, singleton scope 0, zero TDX calls.
- Final F: PASS (F run fe498fbb-8a00-480c-8ac5-a715cd02200b, 10,325,794 rows).

## F. G authority decision

- Legacy Sina full-code discovery was incompatible with R3 SH/SZ completion
  (30,582 pending probes, HTTP 456) and is downgraded to a
  DEFERRED_NON_AUTHORITY observation, preserved verbatim (upstream
  verified=false unchanged).
- G completion authority = Baostock formal identity (frozen hash
  2b1e720…) + E recovery closure (248/248/0) + upstream known-coverage checks
  with hard blockers all zero.
- Wedged `running/current=G_coverage` was recovered via the strict control-plane
  recovery (RECOVER_INTERRUPTED_G_COVERAGE), then G re-ran once.
- G: R3_SHSZ_VERIFIED = true. Report SHA 2e843c72bd0b32ea36b84dd8b6277a4e4
  e2a292fd39fe61c625bd5a6bedf67fe.

## G. Deferred items

- BJ current membership and BJ historical identity (DEFERRED_EXTENSION /
  UNKNOWN_CARRIED) — blocks all-A READY.
- trading_status / turnover facts (R4).
- 5m / minute, adj, industry, index datasets.
- Legacy Sina issued-code discovery (non-authority observation).

## H. Final verdict

R3_SHSZ_DAILY_FOUNDATION = PASS
ALL_A_DAILY_READY = FALSE
DAILY_READY = FALSE
R4_EXECUTION = FORBIDDEN_PENDING_PLAN_AUDIT

## I. Next action

R4 SH/SZ Stable Market Facts planning:
preclose · trading_status / ST / STAR_ST / suspension · turnover_rate ·
high_limit / low_limit · is_limit_up / is_limit_down — prefer existing CNEquity
contracts/providers; no real R4 execution before independent plan audit PASS;
BJ extension remains deferred.
