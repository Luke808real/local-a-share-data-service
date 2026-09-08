# Daily Facts Phase 1 preclose authority

## Scope and status

This is the current-branch reference for the already-frozen preclose source
decision. It does not authorize a full-market fetch, a publication, or any
readiness mutation.

```text
PRECLOSE_SEMANTIC       = EXCHANGE_DISPLAY_PRECLOSE
CANONICAL_SOURCE        = BAOSTOCK_HISTORY_K_PRECLOSE
BAOSTOCK_VERSION        = 0.9.3
QUERY_API               = query_history_k_data_plus
QUERY_FREQUENCY         = d
QUERY_ADJUSTFLAG        = 3
CORPORATE_ACTION_ROLE   = VALIDATION / SENTINEL / DIAGNOSTIC ONLY
PRECLOSE_COMPLETE       = false until the formal completeness gate passes
FACTS_READY             = false until every separate R4 fact gate passes
```

## Canonical observation

For a required R3 actual-traded `(symbol, trade_date)` key, the canonical
preclose candidate is BaoStock's `preclose` observation from the exact query
contract above. `tradestatus=1`, exact provider code/date identity, a finite
positive preclose, unique primary key, provenance, and the formal coverage
gate are required. The provider raw row and normalized value remain retained.

TDX daily OHLCV remains the R3 RAW authority. Its prior close is comparison
evidence only and must neither overwrite BaoStock display preclose nor be
treated as an equality invariant for corporate-action, resumption, IPO, or
other exchange reference-price dates.

## Corporate-action boundary

Corporate-action records and bounded official evidence may classify or
diagnose a reference-price exception. They must not reconstruct the canonical
preclose dataset and must not silently turn an unknown event into a pass.
Unresolved ordinary-continuity evidence remains fail-closed in its own formal
validation scope; an auxiliary feasibility comparison is never a publication
authority.

## Formal completion boundary

`PRECLOSE_COMPLETE=true` requires the frozen formal gate: complete required
coverage, zero missing/unexpected/unknown/duplicate/identity/post-ASOF rows,
valid provider observations, window-boundary and normal-parity gates, frozen
official sentinels, and an unchanged protected R3 boundary. Otherwise it is
false. `FACTS_READY` additionally requires complete trading-status, turnover,
price-limit, assembly, and AS_OF-safe gates; therefore a partial Daily Facts
slice cannot set either global readiness flag.

No full-market execution is authorized by this reference. A task must
explicitly set `FULL_MARKET_AUTHORIZED=YES` before it may resume the existing
R4A continuation or make a global publication.
