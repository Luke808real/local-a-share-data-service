# 2026-09-17 minimal dataset update and MCP verification

User request: continue from the inspected CNEquity-based implementation and make
2026-09-17 data available through MCP and ChatGPT. The user excludes the old
long-running full-market acquisition and asks for necessary fields only.

Base: 99b9326952ae96855c8c642007a8beaef030d182. CNEquity 0.8.0 at
d453853da766b3ba3e44489c0fb6e0089243fa25 plus recorded local patches 0001/0002.

## Bounded execution

1. Correct the observed overnight snapshot-window bug: compare the local clock
   to the target session's complete settlement datetime, retaining all vendor
   session, settled timestamp, coherence and turnover-reset checks. Add offline
   regressions and a reproducible local patch; do not disable any check.
2. Preserve the 2026-09-17 bulk snapshot using CNEquity. No per-symbol BaoStock
   Facts acquisition, historical Facts backfill, universe expansion or init.
3. Use the existing R3 incremental publication path in trading-day order for
   the missing 2026-09-11, 14, 15, 16, 17 partitions. Preserve every published
   historical partition, single-writer locking and pointer-last quality gates.
4. Acquire only required 09-17 CNEquity inputs, evaluate the five V02 facts
   against exact published R3 inputs, and persist evidence. Publication requires
   complete certification and an existing compatible authority contract; if a
   source/authority decision is needed, report it instead of inventing a gate.
5. Restore the existing private read-only connection, verify real MCP status and
   latest/facts responses, then request the same reads from ChatGPT Web.

No changes to legacy roots, global readiness, R4A9, 5m, industry, strategy or
trading. Do not claim an independent audit or change authoritative phase status.
Keep operational results distinct from candidate data and webpage verification.

## 2026-09-18 bounded Facts publication execution

The user's continuation explicitly requests completing the remaining local
fields for 2026-09-17. The earlier V02 shadow contract's non-publication scope
remains the historical rehearsal state. This execution appends only the
certified 2026-09-17 date to the existing independent Facts authority using
its unchanged V01 pointer/manifest envelope, with V02 row schema. It does not
create a global V02 readiness claim or schedule future publication.

Certification must account for all 5,208 frozen eligible keys; reject missing,
extra and duplicate keys, UNKNOWN states, malformed numbers, source mismatches,
trade-status/volume conflicts and unproven corporate-action reference prices.
Bind native CNEquity source files, official PDFs, prior Facts pointer and exact
R3 manifest. Recompute adjusted references using official effective dividends
and ROUND_HALF_UP; preserve suspended pct_chg/turnover nulls. Read back numeric
Parquet types. Preserve every earlier Facts file, install verified new bytes,
and atomically switch the existing pointer last. Local query readback must
validate both the retained old date and newly published date.

Validation is author/operational status only, not independent audit or formal
phase advancement. Historical Facts dates 2026-09-10 through 2026-09-16 remain
outside this bounded addition.
