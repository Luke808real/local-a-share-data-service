#!/usr/bin/env python3
"""R3 DAILY FOUNDATION execution entrypoint (approved plan SHA only).

Usage (authorized launch shape):
  uv run --frozen --no-sync --offline python tools/run_r3_daily_foundation.py \
      --config config/cnequity.toml --stage <stage> [--preflight-only]

This script is the only R3 data-execution surface. `--preflight-only` is
read-only w.r.t. market data and writes only the meta receipt. All other stages
execute the pinned raw adapters through ashare_data.r3_daily.R3Runner.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ashare_data.r3_daily import (  # noqa: E402
    PLAN_SHA,
    R3Error,
    R3Runner,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="R3 daily foundation runner")
    parser.add_argument("--config", default="config/cnequity.toml")
    parser.add_argument("--plan-sha", default=PLAN_SHA)
    parser.add_argument(
        "--stage",
        choices=[
            "preflight",
            "A_instruments",
            "B_discovery",
            "C_merge",
            "C2_enrich",
            "D_calendar",
            "E_delisted",
            "F_daily",
            "G_coverage",
            "quality",
        ],
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    runner = R3Runner(
        Path(args.config),
        repo_root=REPO_ROOT,
        plan_sha=args.plan_sha,
    )
    try:
        if args.preflight_only or args.stage == "preflight":
            receipt = runner.preflight()
            print(json.dumps(receipt, indent=2, default=str))
            return 0
        if not args.stage:
            raise R3Error("STAGE_REQUIRED", "provide --stage or --preflight-only")

        prepare = runner.preflight()
        stage_map = {
            "A_instruments": runner.stage_instruments,
            "B_discovery": runner.stage_discovery,
            "C_merge": runner.stage_merge,
            "C2_enrich": runner.stage_enrich,
            "D_calendar": runner.stage_calendar,
            "E_delisted": runner.stage_delisted,
            "F_daily": runner.stage_daily,
            "G_coverage": runner.stage_coverage,
        }
        result = stage_map[args.stage]()
        print(json.dumps({"stage": args.stage, **result}, indent=2, default=str))
        return 0
    except R3Error as exc:
        print(json.dumps({"error_code": exc.code, "message": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
