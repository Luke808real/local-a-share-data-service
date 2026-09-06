#!/usr/bin/env python3
"""Loopback-only, unauthenticated development MCP server for local ASL facts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Works from any cwd; ASL_DATA_ROOT is read only by LocalQuery at call time.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ashare_data.mcp_server import create_server  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("streamable-http", "stdio"), default="streamable-http")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    try:
        server = create_server(port=args.port)
    except ValueError as exc:
        parser.error(str(exc))
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
