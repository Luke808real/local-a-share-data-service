# ASL local market data MCP

## Purpose and data boundary

CNEquity remains the data foundation. This service reads existing Parquet with
DuckDB through `ashare_data.local_query.LocalQuery`. It does not contain an
ingestion pipeline and does not install or upgrade the CNEquity writer.

Only the exact verified R3 promoted daily file set is queryable. Physical
incremental files beyond it remain on disk but are not opened by daily queries.
The baseline is daily-only; this does not certify R7 first publication, R4
facts, full-history completeness, BJ coverage, or the complete product MVP.

As of the 2026-09-06 inspection, baseline daily runs through 2026-08-17. Nine
additional partitions run through 2026-08-28; their 42 unreturned requested
symbol/date keys lack sufficient explanation. Those keys are neither proven
missing traded bars nor proven suspensions. Do not replace the baseline hash
with the physical hash to suppress a drift error.

## Runtime

From the repository root, with CPython 3.12 available:

```bash
uv venv .venv-mcp --python 3.12
uv pip install --python .venv-mcp/bin/python -r requirements-mcp.txt
.venv-mcp/bin/python tools/serve_local_a_share_mcp.py --help
```

The runtime dependencies are separate from the pinned CNEquity update runtime.
Market data stays at `/Users/luke808/AI/local-a-share-data-service-data`; code
worktree changes do not create another database.

## Installed Mac service (2026-09-06)

The login LaunchAgent is installed at
`/Users/luke808/Library/LaunchAgents/io.asl.market-data-mcp.plist`.
It starts at user login and restarts after an unexpected process exit; the Mac
must remain awake for requests. It runs the separate `.venv-mcp` interpreter.
Endpoint: `http://127.0.0.1:8766/mcp`. Logs: `.runtime/mcp.*.log` (Git-ignored).
No public tunnel is active. This fixed checkout must not be moved while the
service is configured to use it.

```bash
launchctl list io.asl.market-data-mcp
# Stop and unload:
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/io.asl.market-data-mcp.plist
# Start again:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.asl.market-data-mcp.plist
# Direct CLI usage:
.venv-mcp/bin/python tools/query_local_a_share.py latest --symbol 002165.SZ --limit 20
```

## Tool reference

| Tool | Purpose | Bounds |
|---|---|---|
| bars | RAW daily OHLCV/amount for one symbol/date range | At most 1096 days |
| latest | Recent published daily rows for one symbol | At most 250 rows |
| instrument | Existing basic identity | One uniquely resolved symbol |
| status | Readiness, publication boundary, hashes | No raw files or secrets |

Bare six-digit symbols must resolve uniquely; exchange is never guessed.
Preclose, trading status, turnover and limits are not derived implicitly.
`FACT_NOT_READY` and missing/unpublished dates must remain visible to callers.
The service does not support arbitrary SQL, shell commands, provider requests,
filesystem reads, canonical writes, or checkpoint operations.

## Connecting ChatGPT

First validate local MCP initialization, tool listing and all four tool calls.
For a personal development connection the endpoint can be forwarded through a
temporary HTTPS tunnel. The service binds only to loopback and its allowed
Host list must include that exact public host. This development mode provides
anonymous access to the bounded public-market-data tools to anyone with the
URL; it is not an authenticated private production service. Do not place
private portfolios or credentials in this tool surface.

For a durable private connection, use managed OAuth or OpenAI Secure MCP Tunnel.
The latter requires a Platform tunnel identity, runtime credentials, and the
correct ChatGPT workspace association. Do not treat a temporary tunnel as a
permanent address or invent an API credential.

In ChatGPT's plugin/app settings, enable Developer mode if available, add the
MCP connection, then inspect its four tools. Test:

> Use ASL Market Data to report the latest published date and physical date.
> Then retrieve the last five published daily bars for 002165.SZ, including
> source, volume, amount and any coverage warning. Do not infer missing facts.

ChatGPT must actually call the tools; copying CLI output is not end-to-end
acceptance. Account UI and availability vary; connection consent remains with
the account owner. MCP network traffic transports local query results only;
there is no market-provider network access.

## References

- [Official tool-only MCP server guidance](https://developers.openai.com/plugins/build/mcp-server)
- [Connect and test](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [Authentication](https://developers.openai.com/plugins/build/auth)
- [Private Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Python SDK v1.29.1](https://github.com/modelcontextprotocol/python-sdk/tree/v1.29.1)
