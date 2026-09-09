#!/usr/bin/env python3
"""Pointer-last publication for a fully certified bounded Daily Facts run."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src")); sys.path.insert(0, str(REPO / "tools"))

from ashare_data.daily_facts_phase1 import DailyFactsError  # noqa: E402
from ashare_data.local_query import DEFAULT_DATA_ROOT  # noqa: E402
from certify_daily_facts_phase1_v01 import certify  # noqa: E402
from run_daily_facts_phase1_full_market import _atomic_json, _sha, _write_parquet, run_paths  # noqa: E402


RUN = "daily_facts_phase1_20260909_v01"
SCOPE = "2026-09-09_FULL_ELIGIBLE"


def _record(root: Path, path: Path) -> dict[str, Any]:
    return {"relative_path": path.relative_to(root).as_posix(), "file_size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def publish(root: Path, *, run_name: str, day: date, execute: bool) -> dict[str, Any]:
    root = root.resolve()
    certification = certify(root, run_name=run_name, start=day, end=day, execute=True)
    if certification.get("PASS") is not True:
        raise DailyFactsError("CERTIFICATION_NOT_PASSED", "Daily Facts staging cannot be published")
    staging, _raw, _ledger = run_paths(root, run_name)
    target = root / "curated/daily_facts" / f"trade_date={day.isoformat()}" / "part-full-eligible-v01.parquet"
    if target.exists():
        raise DailyFactsError("TARGET_FACTS_PARTITION_EXISTS", "refuse to overwrite published facts")
    normalized = [root / row["relative_path"] for row in certification["normalized_manifest"]["files"]]
    import duckdb
    with duckdb.connect(":memory:") as con:
        rows = [dict(row) for row in con.execute("select * from read_parquet(?, union_by_name=true) order by symbol, trade_date", [[str(p) for p in normalized]]).fetchdf().to_dict("records")]
    for row in rows:
        value = row.get("trade_date")
        if hasattr(value, "date"):
            row["trade_date"] = value.date().isoformat()
    # Existing vertical-slice facts remain part of the authority; the new day
    # has no vertical file and therefore cannot create a duplicate primary key.
    prior_pointer = root / "meta/asl/daily_facts/published-daily-facts-authority.json"
    prior_files: list[dict[str, Any]] = []
    if prior_pointer.exists():
        pointer = json.loads(prior_pointer.read_text())
        prior_plan = json.loads((root / pointer["plan"]).read_text())
        prior_files = list(prior_plan["manifest"]["files"])
        if any(item["relative_path"] == target.relative_to(root).as_posix() for item in prior_files):
            raise DailyFactsError("TARGET_FACTS_PARTITION_EXISTS", "authority already contains target facts date")
    if not execute:
        return {"PASS": True, "publication": "READY", "scope": SCOPE, "candidate_file": target.relative_to(root).as_posix(),
                "expected_key_n": certification["expected_key_n"]}
    _write_parquet(target, rows)
    files = sorted([*prior_files, _record(root, target)], key=lambda item: item["relative_path"])
    manifest = {"schema": "ASL_DAILY_FACTS_MANIFEST_V01", "file_n": len(files), "files": files, "manifest_hash": _sha(files)}
    plan = {"schema": "ASL_DAILY_FACTS_PUBLICATION_PLAN_V01", "scope": SCOPE, "eligible_n": certification["requested_symbol_n"],
            "published_as_of": day.isoformat(), "manifest": manifest, "certification_receipt": (staging / "certification_receipt.json").relative_to(root).as_posix(),
            "r3_daily_manifest_hash": certification["daily_manifest_hash"]}
    receipt = {"schema": "ASL_DAILY_FACTS_PROMOTION_RECEIPT_V01", "STATE": "COMMITTED", "status": "FULL_ELIGIBLE_ONE_DAY_PUBLISHED",
               "scope": SCOPE, "manifest_hash": manifest["manifest_hash"], "quality": certification,
               "provider_network_request_n": 0}
    _atomic_json(staging / "promotion_plan.json", plan)
    _atomic_json(staging / "promotion_receipt.json", receipt)
    # The independent pointer is switched only after the candidate parquet,
    # certification and immutable evidence have all been persisted.
    _atomic_json(prior_pointer, {"schema": "ASL_PUBLISHED_DAILY_FACTS_AUTHORITY_V01",
                                 "plan": (staging / "promotion_plan.json").relative_to(root).as_posix(),
                                 "receipt": (staging / "promotion_receipt.json").relative_to(root).as_posix(),
                                 "manifest_hash": manifest["manifest_hash"]})
    return {"PASS": True, "publication": "PUBLISHED", "scope": SCOPE, "manifest_hash": manifest["manifest_hash"],
            "eligible_n": certification["requested_symbol_n"], "provider_network_request_n": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--run-name", default=RUN); parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 9, 9)); parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try: print(json.dumps(publish(args.data_root, run_name=args.run_name, day=args.date, execute=args.execute), ensure_ascii=False, sort_keys=True))
    except DailyFactsError as exc: print(json.dumps({"error": exc.code}), file=sys.stderr); raise SystemExit(2)
