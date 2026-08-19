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
        ],
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--recover-interrupted-control-plane",
        action="store_true",
        help="Run the fail-closed interrupted-state recovery (mutually "
        "exclusive with --stage / --preflight-only).",
    )
    parser.add_argument(
        "--f-reuse-run-id",
        default=None,
        help="Explicit operator F recovery: reuse the given FAILED r3_daily_bars "
        "run's successful staging (no provider refetch), isolate its failed scope "
        "into singleton symbol batches and refetch only those. ONLY valid with "
        "--stage F_daily.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.recover_interrupted_control_plane and (args.stage or args.preflight_only):
        parser.error(
            "--recover-interrupted-control-plane is mutually exclusive with "
            "--stage and --preflight-only"
        )
    if args.f_reuse_run_id:
        if args.stage != "F_daily":
            parser.error("--f-reuse-run-id is only valid with --stage F_daily")
        if args.preflight_only:
            parser.error("--f-reuse-run-id cannot be combined with --preflight-only")
        if args.recover_interrupted_control_plane:
            parser.error("--f-reuse-run-id is mutually exclusive with "
                         "--recover-interrupted-control-plane")

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    runner = R3Runner(
        Path(args.config),
        repo_root=REPO_ROOT,
        plan_sha=args.plan_sha,
        f_reuse_run_id=args.f_reuse_run_id,
    )
    try:
        if args.recover_interrupted_control_plane:
            prepare = runner.preflight()
            result = runner.recover_interrupted_control_plane()
            print(json.dumps({"recovery": result}, indent=2, default=str))
            return 0
        if args.preflight_only or args.stage == "preflight":
            receipt = runner.preflight()
            print(json.dumps(receipt, indent=2, default=str))
            return 0
        if not args.stage:
            raise R3Error("STAGE_REQUIRED", "provide --stage or --preflight-only")

        prepare = runner.preflight()
        result = runner.run_writer_stage(args.stage)
        print(json.dumps({"stage": args.stage, **result}, indent=2, default=str))
        return 0
    except R3Error as exc:
        print(json.dumps({"error_code": exc.code, "message": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
