# AGENTS.md

## Mandatory read order
Before any implementation or research action in this repository, read:
1. `docs/MASTER_SPEC.md`
2. `docs/PROJECT_STATE.md`
3. `docs/ROADMAP.md`
4. the explicitly assigned phase plan/task contract

## Authority
Current user instruction > MASTER_SPEC > PROJECT_STATE > ROADMAP > phase plan > historical reports > agent assumptions.

## Superpowers workflow
Use the relevant Superpowers process skill before acting. Design changes require brainstorming; multi-step implementation requires writing-plans before code.

## Hard boundaries
- CNEquity is the authoritative database foundation.
- Do not add B1/B2, strategy scores, TradePlan, forward, backtest, or trading logic to this repository.
- Query paths must remain local-only; network access belongs to update/backfill phases only.
- Legacy roots are read-only unless an explicit migration task authorizes reads and writes only to the new target root.
- Do not bypass quality/publish gates.
- Do not run full-market work unless the task says `FULL_MARKET_AUTHORIZED = YES`.
- Do not broaden repository scans when `READ_FIRST` identifies a bounded scope.
- If implementation conflicts with the frozen Spec, return `DESIGN_DECISION_REQUIRED`; do not redesign inside the task.

## Review rule
Codex `PASS` is author status only. Formal progress requires independent audit of the exact pushed commit.
