#!/usr/bin/env python3
"""Produce a reproducible, network-free table for certified reference-price exceptions."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src")); sys.path.insert(0, str(REPO / "tools"))

from ashare_data.daily_facts_phase1 import reconcile  # noqa: E402
from ashare_data.local_query import DEFAULT_DATA_ROOT, LocalQuery  # noqa: E402
from ashare_data.reference_price_evidence import load_reference_price_evidence  # noqa: E402
from certify_daily_facts_phase1_v01 import _reconciliation_bars, _rows  # noqa: E402
from run_daily_facts_phase1_full_market import _atomic_json, run_paths  # noqa: E402

RUN = "daily_facts_phase1_20260909_v01"
DAY = date(2026, 9, 9)
TOLERANCE = 0.01


def audit(root: Path, *, run_name: str = RUN, day: date = DAY, execute: bool = False) -> dict[str, Any]:
    root = root.resolve()
    staging, _raw, ledger = run_paths(root, run_name)
    import sqlite3
    with sqlite3.connect(ledger) as con:
        paths = [root / row[0] for row in con.execute("select normalized_path from units order by symbol") if row[0]]
    facts = _rows(paths)
    evidence_path = staging / "reference_price_evidence.json"
    evidence = load_reference_price_evidence(root, evidence_path)
    evidence_document = json.loads(evidence_path.read_text())
    evidence_records = {(str(row["symbol"]), str(row["ex_dividend_date"])): row for row in evidence_document["records"]}
    bars = _reconciliation_bars(root, day, day)
    reconcile(facts, bars, reference_price_evidence=evidence)
    prior = {(str(row["symbol"]), str(row["trade_date"])): row for row in bars}
    names: dict[str, str] = {}
    with LocalQuery(root) as query:
        for row in query._execute("select symbol,name from " + query._instrument_relation):
            names[str(row["symbol"])] = str(row["name"])
    report_rows: list[dict[str, Any]] = []
    for fact in facts:
        if fact.get("preclose_reconciliation") != "MATCH_REFERENCE_PRICE_EXCEPTION":
            continue
        symbol = str(fact["symbol"])
        event = evidence[(symbol, day.isoformat())]
        source = evidence_records[(symbol, day.isoformat())]
        previous = prior[(symbol, event.record_date)]
        actual = float(fact["preclose"])
        expected = float(fact["reference_price_expected"])
        report_rows.append({
            "symbol": symbol, "name": names.get(symbol), "trade_date": day.isoformat(),
            "r3_2026_09_08_close": float(previous["close"]), "baostock_preclose_2026_09_09": actual,
            "official_ex_date": event.ex_dividend_date,
            "declared_dividend_per_share": source["cash_dividend_per_share_declared"],
            "effective_reference_adjustment": event.effective_cash_dividend_per_share,
            "calculated_reference_price": expected, "difference_vs_baostock": actual - expected,
            "tolerance": TOLERANCE, "evidence_source": event.source_url,
            "evidence_hash": event.source_hash, "announcement_id": event.announcement_id,
            "classification": "PASS_REFERENCE_PRICE_EXCEPTION",
            "calculation_provenance": event.provenance,
        })
    document = {"schema": "ASL_DAILY_FACTS_REFERENCE_PRICE_AUDIT_V01", "run": run_name,
                "trade_date": day.isoformat(), "reference_price_exception_n": len(report_rows),
                "rows": report_rows}
    if execute:
        _atomic_json(staging / "reference_price_exception_audit.json", document)
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--run-name", default=RUN); parser.add_argument("--date", type=date.fromisoformat, default=DAY)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(audit(args.data_root, run_name=args.run_name, day=args.date, execute=args.execute), ensure_ascii=False, sort_keys=True))
