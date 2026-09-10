#!/usr/bin/env python3
"""Bind reviewed reference-price parameters to immutable CNInfo source receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ashare_data.local_query import DEFAULT_DATA_ROOT  # noqa: E402
from ashare_data.reference_price_evidence import FORMULA, SCHEMA, SOURCE, load_reference_price_evidence  # noqa: E402


RUN_NAME = "daily_facts_phase1_20260909_v01"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


def build(root: Path, *, parameters_path: Path, execute: bool) -> dict[str, Any]:
    root = root.resolve()
    source_receipt_path = root / "raw" / "official_disclosures" / RUN_NAME / "cninfo" / "source_receipt.json"
    parameters = json.loads(parameters_path.read_text())
    receipt = json.loads(source_receipt_path.read_text())
    if parameters.get("schema") != "ASL_DAILY_FACTS_REFERENCE_PRICE_PARAMETERS_V01" or receipt.get("target_symbol_n") != len(receipt.get("records", [])):
        raise ValueError("REFERENCE_PRICE_EVIDENCE_INPUT_SCHEMA_INVALID")
    sources = {row["symbol"]: row for row in receipt["records"]}
    output: list[dict[str, Any]] = []
    for row in parameters.get("records", []):
        source = sources.get(row.get("symbol"))
        if source is None:
            raise ValueError("REFERENCE_PRICE_EVIDENCE_SOURCE_RECEIPT_MISSING_SYMBOL")
        source_date = datetime.fromtimestamp(int(source["announcement_time_ms"]) / 1000, tz=ZoneInfo("Asia/Shanghai")).date().isoformat()
        output.append({
            **row, "schema_version": SCHEMA, "source": SOURCE, "formula": FORMULA,
            "announcement_id": source["announcement_id"], "announcement_date": source_date,
            "source_url": source["source_url"], "source_hash": source["source_hash"],
            "source_relative_path": source["source_relative_path"], "retrieved_at": source["retrieved_at"],
        })
    document = {"schema": SCHEMA, "run_name": RUN_NAME, "records": output}
    document["manifest_hash"] = _sha(json.dumps(output, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode())
    if execute:
        destination = root / "staging" / RUN_NAME / "reference_price_evidence.json"
        _atomic_json(destination, document)
        # Verify through the same loader used by certification before reporting success.
        if len(load_reference_price_evidence(root, destination)) != len(output):
            raise ValueError("REFERENCE_PRICE_EVIDENCE_VERIFY_FAILURE")
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.data_root, parameters_path=args.parameters, execute=args.execute), ensure_ascii=False, sort_keys=True))
