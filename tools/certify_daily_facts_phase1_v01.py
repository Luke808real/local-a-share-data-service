#!/usr/bin/env python3
"""Certify one bounded Daily Facts Phase 1 staging run without publishing it.

The tool consumes only the persisted ledger, BaoStock RAW and normalized
parquet from the named run.  It never imports a provider adapter and never
touches either R3 or Daily Facts authority pointer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ashare_data.daily_facts_phase1 import DailyFactsError, reconcile  # noqa: E402
from ashare_data.local_query import DEFAULT_DATA_ROOT, LocalQuery  # noqa: E402
from run_daily_facts_phase1_full_market import _atomic_json, _db, _set_metadata, _sha, published_scope, run_paths  # noqa: E402


SCHEMA = "ASL_DAILY_FACTS_PHASE1_CERTIFICATION_V01"


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"relative_path": path.relative_to(root).as_posix(), "file_size": path.stat().st_size, "sha256": digest}


def _rows(paths: list[Path]) -> list[dict[str, Any]]:
    import duckdb
    if not paths:
        return []
    with duckdb.connect(":memory:") as con:
        values = con.execute("select * from read_parquet(?, union_by_name=true) order by symbol, trade_date", [[str(p) for p in paths]]).fetchdf()
    return [dict(row) for row in values.to_dict("records")]


def certify(root: Path, *, run_name: str, start: date, end: date, execute: bool) -> dict[str, Any]:
    root = root.resolve()
    symbols, required, as_of, daily_manifest_hash, _relation = published_scope(root, start=start, end=end)
    staging, _raw, database = run_paths(root, run_name)
    if not database.is_file():
        raise DailyFactsError("RUN_LEDGER_MISSING", "bounded run ledger is missing")
    with sqlite3.connect(database) as con:
        plan_row = con.execute("select value from metadata where key='plan'").fetchone()
        if plan_row is None:
            raise DailyFactsError("RUN_PLAN_MISSING", "run plan is missing")
        plan = json.loads(plan_row[0])
        if (plan.get("symbols") != symbols or plan.get("start") != start.isoformat() or plan.get("end") != end.isoformat()
                or plan.get("daily_manifest_hash") != daily_manifest_hash):
            raise DailyFactsError("RUN_PLAN_DRIFT", "ledger is not bound to the current formal R3 scope")
        state_counts = dict(con.execute("select state,count(*) from units group by state"))
        normalized_rel = [row[0] for row in con.execute("select normalized_path from units order by symbol") if row[0]]
    terminal = sum(state_counts.get(name, 0) for name in ("QUALITY_PASS", "PROVIDER_FAIL", "QUALITY_FAIL"))
    result: dict[str, Any] = {"schema": SCHEMA, "run": run_name, "scope": "FULL_ELIGIBLE_ONE_DAY" if start == end else "RECENT_WINDOW",
        "start": start.isoformat(), "end": end.isoformat(), "as_of": as_of.isoformat(), "daily_manifest_hash": daily_manifest_hash,
        "requested_symbol_n": len(symbols), "expected_key_n": sum(required.values()), "ledger_terminal_n": terminal,
        "states": state_counts, "provider_network_request_n": 0}
    if state_counts.get("QUALITY_PASS", 0) != len(symbols) or terminal != len(symbols):
        result.update({"PASS": False, "error": "LEDGER_NOT_FULLY_QUALITY_PASS"})
    else:
        paths = [root / value for value in normalized_rel]
        if len(paths) != len(symbols) or any(not path.is_file() for path in paths):
            raise DailyFactsError("NORMALIZED_EVIDENCE_MISSING", "every quality-pass unit needs immutable normalized evidence")
        facts = _rows(paths)
        keys = {(str(row["symbol"]), str(row["trade_date"])) for row in facts}
        expected_keys = _expected_keys(root, start, end)
        if len(keys) != len(facts) or keys != expected_keys:
            result.update({"PASS": False, "error": "EXACT_KEY_COVERAGE_FAILURE", "actual_key_n": len(keys)})
        else:
            bars = _reconciliation_bars(root, start, end)
            reconcile(facts, bars)
            quality = {
                "DUPLICATE_N": len(facts) - len(keys),
                "SOURCE_ERROR_N": state_counts.get("PROVIDER_FAIL", 0),
                "UNKNOWN_N": sum(row.get("quality_status") == "UNKNOWN" for row in facts),
                "UNRESOLVED_N": sum(row.get("quality_status") == "UNRESOLVED" for row in facts),
                "PRECLOSE_MISMATCH_N": sum(row.get("preclose_reconciliation") in {"MISMATCH", "UNRESOLVED"} for row in facts),
                "PCT_CHG_MISMATCH_N": sum(row.get("pct_chg_reconciliation") == "MISMATCH" for row in facts),
                "PROVENANCE_FAILURE_N": sum(not all(row.get(k) for k in ("provider", "raw_values", "fetched_at", "provider_version", "schema_version")) for row in facts),
            }
            result.update({"actual_key_n": len(keys), "quality": quality,
                           "STRUCTURAL_PASS": quality["DUPLICATE_N"] == 0,
                           "COVERAGE_PASS": keys == expected_keys,
                           "PROVENANCE_PASS": quality["PROVENANCE_FAILURE_N"] == 0})
            result["PASS"] = all((result["STRUCTURAL_PASS"], result["COVERAGE_PASS"], result["PROVENANCE_PASS"],
                                  quality["SOURCE_ERROR_N"] == 0, quality["UNKNOWN_N"] == 0,
                                  quality["UNRESOLVED_N"] == 0, quality["PRECLOSE_MISMATCH_N"] == 0,
                                  quality["PCT_CHG_MISMATCH_N"] == 0))
            result["normalized_manifest"] = {"file_n": len(paths), "files": [_file_record(root, path) for path in paths]}
            result["normalized_manifest"]["manifest_hash"] = _sha(result["normalized_manifest"]["files"])
    if execute:
        _atomic_json(staging / "certification_receipt.json", result)
    return result


def _expected_keys(root: Path, start: date, end: date) -> set[tuple[str, str]]:
    with LocalQuery(root) as query:
        rows = query._execute("select symbol, cast(trade_date as varchar) as trade_date from " + query._daily_relation +
                              " where trade_date between ? and ?", [start, end])
    return {(str(row["symbol"]), str(row["trade_date"])) for row in rows}


def _reconciliation_bars(root: Path, start: date, end: date) -> list[dict[str, Any]]:
    # Seven calendar days include the prior published session across every
    # normal CN market weekend/holiday gap; the start boundary itself remains
    # the exact expected-set authority.
    with LocalQuery(root) as query:
        return query._execute("select symbol, cast(trade_date as varchar) as trade_date, close from " + query._daily_relation +
                              " where trade_date between ? and ?", [start - timedelta(days=7), end])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(certify(args.data_root, run_name=args.run_name, start=args.start, end=args.end, execute=args.execute), ensure_ascii=False, sort_keys=True))
    except DailyFactsError as exc:
        print(json.dumps({"error": exc.code}), file=sys.stderr)
        raise SystemExit(2)
