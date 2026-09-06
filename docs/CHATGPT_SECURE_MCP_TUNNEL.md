# ChatGPT → Mac local K-line access via Secure MCP Tunnel

## Goal

Keep the ASL market-data MCP private on the Mac while allowing ChatGPT to call
its read-only tools (`bars`, `latest`, `instrument`, `status`). No public inbound
port is required.

The existing local MCP endpoint stays:

```text
http://127.0.0.1:8766/mcp
```

The bridge is OpenAI Secure MCP Tunnel:

```text
ChatGPT
  -> OpenAI-hosted tunnel endpoint
  -> outbound HTTPS poll from tunnel-client on the Mac
  -> 127.0.0.1:8766/mcp
  -> LocalQuery
  -> published CNEquity/Parquet data only
```

## Security boundary

- Do not expose port 8766 to the public internet.
- Do not commit a runtime API key or tunnel credential.
- The runtime key must have Tunnels Read + Use only for this runtime path.
- The MCP remains read-only; the tunnel does not add provider, SQL, shell,
  filesystem, canonical-write, or strategy capabilities.
- Publication gating remains in `LocalQuery`; physical unpublished Parquet files
  remain excluded.

## One-time OpenAI account setup

1. In OpenAI Platform tunnel settings, create/choose a tunnel associated with the
   ChatGPT workspace that will use it.
2. Create a tunnel runtime API key with Tunnels Read + Use.
3. Install the latest official `openai/tunnel-client` binary on the Mac.
4. Keep the values local:

```bash
export ASL_OPENAI_TUNNEL_ID='tunnel_...'
export CONTROL_PLANE_API_KEY='sk-...'
```

Never add either value to `.env` files tracked by git.

## Start the managed runtime

The local ASL MCP LaunchAgent must already be healthy at
`http://127.0.0.1:8766/mcp`.

```bash
cd /Users/luke808/ASL
.venv-mcp/bin/python tools/chatgpt_secure_mcp_tunnel.py connect
```

The wrapper delegates to the official long-lived runtime flow:

```text
tunnel-client runtimes connect
  --alias asl-market-data
  --tunnel-id <ASL_OPENAI_TUNNEL_ID>
  --runtime-api-key env:CONTROL_PLANE_API_KEY
  --mcp-server-url http://127.0.0.1:8766/mcp
```

It then checks `tunnel-client runtimes status asl-market-data --json` and fails
closed unless `process_running`, `healthy`, and `ready` are all true.

Check later:

```bash
.venv-mcp/bin/python tools/chatgpt_secure_mcp_tunnel.py status
```

Stop only the local tunnel runtime:

```bash
.venv-mcp/bin/python tools/chatgpt_secure_mcp_tunnel.py stop
```

Stopping the runtime does not mutate CNEquity data or the remote tunnel object.

## Connect ChatGPT

In ChatGPT Developer mode, create a private MCP connection using **Connection:
Tunnel**, select the associated tunnel (or paste its `tunnel_id`), and review the
discovered four tools.

End-to-end acceptance requires an actual ChatGPT tool call, not copied CLI
output. Run these in a fresh chat with the connection enabled:

1. `Use ASL Market Data status and report published_as_of and physical_as_of.`
2. `Use ASL Market Data latest for 002165.SZ and return the last 5 published daily bars.`
3. `Use ASL Market Data bars for 600519.SH from 2026-08-01 through 2026-08-17.`

Acceptance is PASS only when ChatGPT itself invokes the tools and the returned
rows remain capped by the published authority.
