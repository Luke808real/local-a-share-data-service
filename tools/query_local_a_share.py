#!/usr/bin/env python3
"""CLI for read-only queries against the existing ASL local data root."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ashare_data.local_query import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    LocalQuery,
    QueryError,
    _as_json_value,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read published ASL daily facts without network or writes."
    )
    parser.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="authoritative local data root (default: %(default)s)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bars = subparsers.add_parser("bars", help="query daily bars in a date range")
    bars.add_argument("--symbol", required=True)
    bars.add_argument("--start", required=True)
    bars.add_argument("--end", required=True)
    bars.add_argument("--format", choices=("table", "json"), default="table")

    latest = subparsers.add_parser("latest", help="query the latest N daily bars")
    latest.add_argument("--symbol", required=True)
    latest.add_argument("--limit", type=int, default=20)
    latest.add_argument("--format", choices=("table", "json"), default="table")

    instrument = subparsers.add_parser("instrument", help="query formal instrument identity")
    instrument.add_argument("--symbol", required=True)
    instrument.add_argument("--format", choices=("table", "json"), default="table")

    status = subparsers.add_parser("status", help="show current published data readiness")
    status.add_argument("--format", choices=("table", "json"), default="table")
    return parser


def _format_value(value: Any) -> str:
    value = _as_json_value(value)
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return format(value, ".15g")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _print_table(value: dict[str, Any]) -> None:
    if value.get("command") in {"bars", "latest"}:
        rows = value.get("rows", [])
        if not rows:
            print(f"{value.get('symbol')}: no rows")
            return
        columns = list(rows[0])
        rendered = [[_format_value(row.get(column)) for column in columns] for row in rows]
        widths = [
            max(len(column), *(len(row[index]) for row in rendered))
            for index, column in enumerate(columns)
        ]
        print("  ".join(column.ljust(widths[index]) for index, column in enumerate(columns)))
        print("  ".join("-" * width for width in widths))
        for row in rendered:
            print("  ".join(row[index].ljust(widths[index]) for index in range(len(columns))))
        return

    if value.get("command") == "instrument":
        pairs = value.get("instrument", {})
    else:
        pairs = value
    if not isinstance(pairs, dict):
        print(_format_value(pairs))
        return
    width = max((len(str(key)) for key in pairs), default=0)
    for key, item in pairs.items():
        print(f"{str(key).ljust(width)}  {_format_value(item)}")


def _emit(value: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(_as_json_value(value), ensure_ascii=False, sort_keys=True, indent=2))
    else:
        _print_table(value)


def main(argv: Iterable[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    output_format = getattr(args, "format", "table")
    try:
        with LocalQuery(args.data_root) as query:
            if args.command == "bars":
                value = query.bars(args.symbol, args.start, args.end)
            elif args.command == "latest":
                value = query.latest(args.symbol, args.limit)
            elif args.command == "instrument":
                value = query.instrument(args.symbol)
            else:
                value = query.status()
        _emit(value, output_format)
        return 0
    except QueryError as exc:
        error = {"error": {"code": exc.code, "message": exc.message}}
        if exc.details is not None:
            error["error"]["details"] = _as_json_value(exc.details)
        if output_format == "json":
            print(json.dumps(error, ensure_ascii=False, sort_keys=True, indent=2))
        else:
            print(f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
