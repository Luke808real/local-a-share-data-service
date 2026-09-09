# ASL Daily Facts Phase 1 bounded expansion V02

## Authority boundary

R3 Daily remains the published, pointer-bound TDX OHLCV authority through
2026-09-09. Daily Facts is independent: BaoStock evidence is persisted as RAW,
normalized into staging, certified from persisted files, and only then may
receive its own manifest and authority pointer. Physical facts never widen a
LocalQuery or MCP response.

## First production-sized certification scope

- Formal SH/SZ eligible universe: the LocalQuery-published R3 scope for the
  date, expected to be 5,208 symbols on 2026-09-09.
- Date window: 2026-09-09 only.
- Provider: pinned CNEquity BaoStock bridge, one reusable session and bounded
  batches. The ledger is resumable and RAW is immutable/idempotent.
- Stop gate: duplicate keys, source errors, UNKNOWN tri-state values, missing
  expected keys, invalid facts, provenance failures, unexplained preclose
  mismatch, or pct-change mismatch are publication blockers.

## Promotion sequence after certification

1. Bind the candidate plan to the current R3 published manifest and its exact
   expected `(symbol, trade_date)` key set.
2. Persist candidate facts, quality receipt, provenance receipt and manifest.
3. Validate installed files against the manifest.
4. Atomically switch the independent Daily Facts pointer last.

Global `FACTS_READY` and `PRECLOSE_COMPLETE` remain false until a later
all-scope promotion satisfies its own complete coverage contract.
