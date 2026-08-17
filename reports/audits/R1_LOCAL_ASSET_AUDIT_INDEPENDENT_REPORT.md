# R1_LOCAL_ASSET_AUDIT_INDEPENDENT_REPORT

## VERDICT

`R1: AUDIT_PASS`

This verdict applies only to the R1 research deliverables at the exact pushed
commit below. It does not approve legacy reuse, migration, a provider contract,
R2 implementation results, or publication.

## AUDIT_AUTHORITY

- Exact author commit: `09e9254042ad747983d40d794595135fb58e2d80`
- Remote evidence branch: `origin/research/r1-local-asset-audit`
- Independent bounded reviewer: GPT-5.6 Terra / `max`
- Final phase-gate adjudicator: GPT-5.6 Sol root
- Audit date: `2026-08-18`

The local and remote branch heads matched the exact author commit. The audited
range contained nine R1-only commits based on
`6054d129d84204c7c322fd947660b9d04079e87a`.

## VERIFICATION

- `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_r1_asset_audit_readonly.py tests/test_project_docs_contract.py`
  returned `12 passed`.
- `git diff --check` passed for the exact base-to-author range and worktree.
- All eight required V1 datasets appear in Legacy Inventory, Coverage Map,
  Compatibility Matrix, and Reuse Decisions.
- Coverage, compatibility, and reuse enums are fail-closed. No dataset is
  approved for `DIRECT_REUSE` or `MIGRATE_AFTER_NORMALIZATION`.
- Static review found no repository tool path that writes a legacy root or
  opens SQLite/DuckDB.
- The independent reviewer did not access or stat any legacy root and did not
  modify the repository.

## FINDINGS

No P0, P1, or P2 defect was found in the exact R1 commit. The four R1 exit
artifacts are present, internally consistent, and preserve unknown units,
semantics, PKs, coverage, and provenance as unknown rather than inferring them.

The following are carry-forward contract gaps, not R1 deliverable defects:

1. `trading_status` has `UPSTREAM_CONTRACT_CONFLICT` at the pinned upstream.
2. A V1 stock-level turnover contract is `NOT_PRESENT_AT_PIN`.
3. Seven legacy datasets are `CROSSCHECK_ONLY`; primary SW industry is `REJECT`.

## READONLY_INCIDENT_AND_OWNER_RECOVERY

The audited R1 commit correctly retained
`READONLY_BREACH_INDETERMINATE`: strict read-only PASS was not claimed after a
SQLite `mode=ro` access plausibly created two sidecars.

After the exact R1 audit, the owner explicitly authorized moving only these
sidecars to macOS Trash:

- `/Users/luke808/AI/asl-shared/meta/manifest.db-wal` — 0 B, inode `11302937`
- `/Users/luke808/AI/asl-shared/meta/manifest.db-shm` — 32,768 B, inode `11302938`

The controller rechecked exact targets and open handles, then moved them without
overwrite to:

- `/Users/luke808/.Trash/asl-shared-manifest.db-wal.R1-authorized-20260818`
- `/Users/luke808/.Trash/asl-shared-manifest.db-shm.R1-authorized-20260818`

Post-move verification showed both source paths absent, Trash objects retaining
the same sizes and inodes, no open handles, and `manifest.db` unchanged at
397,312 B, inode `8080614`, mtime/ctime `1786669574`. The `meta/` directory
mtime/ctime changed as expected when its two directory entries were removed.
The operation is recoverable from macOS Trash. No database was opened, repaired,
checkpointed, deleted, or otherwise modified.

The recovery resolves the owner-action prerequisite. It does not erase the
historical incident or upgrade the R1 legacy-root audit to strict read-only PASS.

## PHASE_GATE

`R1_EXIT_GATE: SATISFIED`

`R2_ACTIVATION: ALLOWED_WITH_FAIL_CLOSED_CARRY_FORWARD`

R2 must preserve the unresolved trading-status and turnover-contract gaps,
must not treat any legacy dataset as migration-ready, and must receive its own
exact-commit audit before R3 activation.
