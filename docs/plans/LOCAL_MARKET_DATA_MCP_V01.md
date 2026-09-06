# Local market data MCP V01

Date: 2026-09-06. User authorization: implement publication consistency, reuse
the CNEquity-backed Python query core, provide read-only ChatGPT access, and use
subagents for bounded implementation. Parent agent owns architecture/review.

## Decisions

- Preserve CNEquity, existing Parquet, and existing data root. No provider fetch,
  canonical repair, incremental restart, R4 execution, or database migration.
- Physically present incremental files are not publication proof. Audit only
  nine appended daily partitions and their existing receipts.
- Retain the four-key promotion's exact 2,580-file published universe if its
  payload/hash and every retained file still verify. Extra partitions are
  excluded before SQL, not merely filtered after reading results. Any mutation
  or disappearance inside the published universe blocks queries.
- Show latest published date and latest physical date separately. New days
  remain pending quality/publication closure; do not rehash them into authority.
- CLI and MCP share LocalQuery and the same gate. Preserve RAW OHLCV, provenance,
  PARTIAL coverage, UNKNOWN/FACT_NOT_READY. No inferred preclose/status/limits.
- MCP is tool-only with bars/latest/instrument/status; bounded financial time
  series queries are not a document-search connector. No UI, arbitrary SQL,
  filesystem tools, provider actions, or generic execution tools.
- Follow official Python MCP SDK Streamable HTTP examples, bound to loopback.
  A temporary HTTPS connection may expose only these public-market-data tools;
  never expose local file paths, secrets, or operational control endpoints.
  Private production access needs managed authentication or Secure MCP Tunnel.
- Runtime access is independent of Git worktree and bound to a configured data
  root. ChatGPT-side acceptance must be distinguished from local MCP tests.

## Work and ownership

1. Read-only subagent: audit incremental receipts and original coverage gates.
2. Query subagent: publication file-universe guard, bounded reads, fixtures/tests.
3. MCP subagent: thin tools/entry point, schema/bounds/error tests; no query logic.
4. Parent: integration, dependencies, state/runbook, real data smoke, live MCP
   protocol validation and deployment handoff.

## Acceptance

Baseline files hash-verified before use; extra/unpublished partitions excluded;
baseline drift/missing/symlink escape fail closed; AS_OF capped explicitly;
all four tools only read local published facts; provider imports absent;
three real symbols; HTTP initialize/list/call; output bounds; no canonical or
checkpoint writes; targeted tests, py_compile, git diff --check.

## Documentation sources

- https://developers.openai.com/plugins/build/mcp-server
- https://developers.openai.com/plugins/plan/tools
- https://developers.openai.com/plugins/deploy/connect-chatgpt
- https://developers.openai.com/plugins/build/auth
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels

Official tool-only server pattern is preferred to the unrelated Pizzaz UI demo.
Superpowers skill files were not available; this document records the design
and ordered implementation plan before code changes.
