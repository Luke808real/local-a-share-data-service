#!/usr/bin/env python3
"""R4A0 resumable full-bootstrap orchestrator CLI.

Default is dry-run (manifest-derived resume plan, zero
provider/manifest/parquet write). Real --exec is FORBIDDEN until Sol audits the
orchestrator (FULL_BOOTSTRAP_EXECUTION=FORBIDDEN_PENDING_SOL_ORCHESTRATOR_AUDIT),
so it is refused here.
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

from ashare_data.r4a0_full_bootstrap_orchestrator import (  # noqa: E402
    CHUNK_SIZE,
    first_last_chunks,
    run_full_bootstrap,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="R4A0 resumable full bootstrap plan")
    parser.add_argument("--config", default="config/cnequity.toml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Build the resume plan only (default).",
    )
    mode.add_argument(
        "--exec",
        action="store_true",
        help="REAL full bootstrap. FORBIDDEN until Sol orchestrator audit.",
    )
    args = parser.parse_args()

    dry_run = not args.exec  # default mode is dry-run

    config_path = (REPO_ROOT / args.config).resolve()
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    root = Path(raw["data"]["root"])
    cfg = load_config(str(config_path))
    manifest_path = root / "meta" / "manifest.db"
    report = run_full_bootstrap(
        root,
        cfg=cfg,
        dry_run=dry_run,
        manifest_path=manifest_path,
        config_path=config_path,
    )
    chunks = report.pop("chunks", [])
    sel = first_last_chunks({"chunks": chunks})
    report.update(sel)
    report["FIRST_3_CHUNK_DETAIL"] = chunks[:3] if chunks else []
    report["LAST_3_CHUNK_DETAIL"] = chunks[-3:] if chunks else []
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("STATUS") == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
