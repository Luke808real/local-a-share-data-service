#!/usr/bin/env python3
"""R4A0 bounded corporate_actions pilot — adapter CLI.

This task implemented and tests the adapter only. --dry-run is the default and
performs zero manifest / provider / real-root writes. Real execution
(--exec) is FORBIDDEN until Sol audits the adapter
(BOUNDED_PILOT_EXECUTION=FORBIDDEN_PENDING_SOL_ADAPTER_AUDIT).
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from cnequity.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ashare_data.r4a0_bounded_adapter import run_bounded_pilot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="R4A0 bounded corporate actions pilot")
    parser.add_argument("--config", default="config/cnequity.toml")
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated canonical SH/SZ symbols, 1..24",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Validate gates + print execution plan; no manifest/provider/root write.",
    )
    parser.add_argument(
        "--exec",
        action="store_true",
        help="REAL bounded execution. FORBIDDEN until Sol adapter audit. "
        "Requires explicitly passing --exec (and --dry-run stays off).",
    )
    args = parser.parse_args()

    if args.exec and args.dry_run:
        parser.error("--exec and --dry-run are mutually exclusive")

    config_path = (REPO_ROOT / args.config).resolve()
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    root = Path(raw["data"]["root"])
    cfg = load_config(str(config_path))
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    report = run_bounded_pilot(
        symbols,
        root=root,
        cfg=cfg,
        dry_run=not args.exec,
        config_path=config_path,
    )
    print(json.dumps(report, indent=2, default=str))
    if args.exec:
        print(
            "NOTE: real execution is forbidden until Sol adapter audit; "
            "this run did not execute.",
            file=sys.stderr,
        )
    return 0 if report.get("STATUS") in ("READY", "PILOT_COMPLETE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
