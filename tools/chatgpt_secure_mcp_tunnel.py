#!/usr/bin/env python3
"""Manage the private ChatGPT -> Mac ASL Secure MCP Tunnel runtime.

The ASL MCP server stays on loopback. This wrapper delegates the outbound-only
connection and process supervision to OpenAI's official ``tunnel-client``.
Literal runtime API keys are never accepted as command-line arguments or written
to repository files; tunnel-client receives only ``env:CONTROL_PLANE_API_KEY``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any


DEFAULT_ALIAS = "asl-market-data"
DEFAULT_MCP_SERVER_URL = "http://127.0.0.1:8766/mcp"
TUNNEL_ID_ENV = "ASL_OPENAI_TUNNEL_ID"
RUNTIME_KEY_ENV = "CONTROL_PLANE_API_KEY"
TUNNEL_CLIENT_ENV = "TUNNEL_CLIENT_BIN"


class TunnelSetupError(RuntimeError):
    """Fail-closed local tunnel setup error."""


def _client_binary() -> str:
    configured = os.environ.get(TUNNEL_CLIENT_ENV)
    candidate = configured or shutil.which("tunnel-client")
    if not candidate:
        raise TunnelSetupError(
            "tunnel-client not found; install the official OpenAI tunnel-client "
            "binary and put it on PATH or set TUNNEL_CLIENT_BIN"
        )
    return candidate


def _tunnel_id(explicit: str | None) -> str:
    value = explicit or os.environ.get(TUNNEL_ID_ENV)
    if not value:
        raise TunnelSetupError(
            f"missing tunnel id; pass --tunnel-id or set {TUNNEL_ID_ENV}"
        )
    if not value.startswith("tunnel_") or len(value) < 16:
        raise TunnelSetupError("invalid OpenAI tunnel_id")
    return value


def _require_runtime_key() -> None:
    if not os.environ.get(RUNTIME_KEY_ENV):
        raise TunnelSetupError(
            f"missing {RUNTIME_KEY_ENV}; create a tunnel runtime key with "
            "Tunnels Read + Use and export it in the local environment"
        )


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture,
    )


def _status_payload(client: str, alias: str) -> dict[str, Any]:
    completed = _run(
        [client, "runtimes", "status", alias, "--json"],
        capture=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TunnelSetupError("tunnel-client returned invalid status JSON") from exc
    if not isinstance(payload, dict):
        raise TunnelSetupError("tunnel-client status must be a JSON object")
    return payload


def _ready(payload: dict[str, Any]) -> bool:
    return all(payload.get(field) is True for field in ("process_running", "healthy", "ready"))


def connect(
    *,
    client: str,
    alias: str,
    tunnel_id: str,
    mcp_server_url: str,
) -> dict[str, Any]:
    _require_runtime_key()
    # Secret reference only. Never place the literal runtime key in argv or repo state.
    _run(
        [
            client,
            "runtimes",
            "connect",
            "--alias",
            alias,
            "--tunnel-id",
            tunnel_id,
            "--runtime-api-key",
            f"env:{RUNTIME_KEY_ENV}",
            "--mcp-server-url",
            mcp_server_url,
        ]
    )
    payload = _status_payload(client, alias)
    if not _ready(payload):
        raise TunnelSetupError(
            "tunnel runtime launched but is not fully ready; inspect "
            f"`tunnel-client runtimes status {alias} --json`"
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("connect", "status", "stop"))
    parser.add_argument("--alias", default=DEFAULT_ALIAS)
    parser.add_argument("--tunnel-id")
    parser.add_argument("--mcp-server-url", default=DEFAULT_MCP_SERVER_URL)
    args = parser.parse_args(argv)

    try:
        client = _client_binary()
        if args.command == "connect":
            payload = connect(
                client=client,
                alias=args.alias,
                tunnel_id=_tunnel_id(args.tunnel_id),
                mcp_server_url=args.mcp_server_url,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "status":
            payload = _status_payload(client, args.alias)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0 if _ready(payload) else 3

        _run([client, "runtimes", "stop", args.alias])
        return 0
    except (TunnelSetupError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
