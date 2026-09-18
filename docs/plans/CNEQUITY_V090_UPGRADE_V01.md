# CNEquity 0.9.0 isolated upgrade execution

User contract: upgrade from b8ec628404d5fa816c56c9258fcbe947f9acf887 on a new
codex/cnequity-v090-upgrade-v01 branch to exact official release
ca5c568f52a4cc1fad8bd812c3c406802c39d2fc. Production .venv and lake are immutable.
No merge, production migration, full-history acquisition or guard bypass.

1. Verify base refs; record production authority, filesystem and runtime baseline.
2. Create .venv-cne090; update pin and lock. Inspect pristine contracts/APIs;
   dry-run and apply each necessary patch, recording outcomes.
3. Exercise actual API behavior, config, isolated staging/compact and schemas;
   classify pit_quality metadata changes without rewriting historical data.
4. Run requested targeted and full test suites with production writes denied;
   repair genuine issues without weakening assertions. Read published V1/V02
   through LocalQuery and MCP, including predicates and numeric/null types.
5. After offline success, use a small explicit provider scope in an isolated
   lake. Keep snapshot guards active; canary must not publish production data.
6. Recheck unchanged production baseline, write ownership review and machine
   readable report, commit and push upgrade branch, verify exact remote HEAD.

The required Superpowers process skills were not found in available skill
locations. This explicit execution plan supplies the staged workflow; no
unavailable skill is claimed as executed.

Final report must distinguish author validation from independent audit.
