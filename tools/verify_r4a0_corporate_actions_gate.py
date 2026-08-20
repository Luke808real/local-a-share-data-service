#!/usr/bin/env python3
"""R4A0 corporate_actions availability gate — read-only verifier CLI.

Usage (authorized, frozen offline venv):
  /Users/luke808/ASL-r3-daily-foundation-v01/.venv/bin/python \
      tools/verify_r4a0_corporate_actions_gate.py --config config/cnequity.toml

The gate never writes to the data root, never calls a provider, and never
bootstraps/backfills corporate_actions. Exit code is 0 only when
R4A0_READY=true.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ashare_data.r4a0_corporate_actions_gate import (  # noqa: E402
    CNEQUITY_PIN_SHA,
    CONTRACT,
    FORMAL_IDENTITY_HASH,
    FORMAL_IDENTITY_N,
    contract_required,
    evaluate_pin_contract,
    run_gate,
)


def contract_check() -> dict:
    """Verify the inlined contract matches the pinned CNEquity origin."""
    try:
        import cnequity  # noqa: F401
        from cnequity import domain
        from cnequity.domain import datasets as specs_ds
    except Exception as exc:  # pragma: no cover - environment probe
        return {
            "cnequity_package_checked": False,
            "error": str(exc),
            "match": False,
        }

    pin = None
    try:
        from importlib.metadata import distribution

        dm = distribution("cnequity").read_text("direct_url.json")
        if dm:
            pin = json.loads(dm)
    except Exception:
        pin = None

    schema_cols = list(domain.schemas.CORPORATE_ACTIONS_SCHEMA.keys())
    spec = None
    if hasattr(specs_ds, "DATASETS"):
        spec = specs_ds.DATASETS.get(DATASET_NAME) if isinstance(
            specs_ds.DATASETS, dict
        ) else None
    spec_primary = getattr(spec, "primary_source", None) if spec else None
    spec_backup = getattr(spec, "backup_source", None) if spec else None
    ev = evaluate_pin_contract(
        pin,
        schema_cols,
        spec_primary,
        spec_backup,
        pin_expected=CNEQUITY_PIN_SHA,
    )
    return {
        "cnequity_package_checked": True,
        "direct_url": pin,
        **ev,
        "missing_from_pin_schema": sorted(
            set(CONTRACT.required_fields) - set(schema_cols)
        ),
    }


DATASET_NAME = CONTRACT.dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="R4A0 corporate_actions gate")
    parser.add_argument("--config", default="config/cnequity.toml")
    parser.add_argument(
        "--contract-check",
        action="store_true",
        help="Deprecated compatibility no-op: pinned upstream validation is "
        "now ALWAYS enforced and cannot be skipped.",
    )
    args = parser.parse_args()

    config_path = (REPO_ROOT / args.config).resolve()
    with config_path.open("rb") as fh:
        config = tomllib.load(fh)
    root = Path(config["data"]["root"])

    # PINED UPSTREAM CHECK IS MANDATORY for formal execution. There is no
    # skip-pin path; --contract-check above is a no-op kept for compatibility.
    cc = contract_check()
    report = run_gate(
        root,
        expected_identity_hash=FORMAL_IDENTITY_HASH,
        expected_identity_n=FORMAL_IDENTITY_N,
    )
    report["config"] = {
        "path": str(config_path),
        "sha256": __import__("hashlib").sha256(config_path.read_bytes()).hexdigest(),
        "root": str(root),
    }
    report["upstream"] = {
        "cnequity_pin_sha": CNEQUITY_PIN_SHA,
        "contract_check": cc,
    }
    report["PIN_CHECK_MANDATORY"] = True
    report["PIN_BYPASS_AVAILABLE"] = False
    if not contract_required(cc):
        print(
            json.dumps(
                {
                    "error": (
                        "R4A0 upstream validation failed "
                        "(SCHEMA_MATCH and SOURCE_MATCH and PIN_MATCH required)"
                    ),
                    "contract_check": cc,
                },
                indent=2,
            )
        )
        return 2

    print(json.dumps(report, indent=2, default=str))
    return 0 if report["R4A0_READY"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
