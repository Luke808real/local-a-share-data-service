#!/usr/bin/env python3
"""Persist a read-only structural receipt for a curated CNEquity 5m tip."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ashare_data.five_minute_audit import audit_rows  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    directory = args.data_root / "curated/minute_bars_5m" / f"trade_date={args.trade_date.isoformat()}"
    files = sorted(directory.glob("*.parquet"))
    if len(files) != 1:
        raise RuntimeError("TIP_PARQUET_FILE_COUNT_NOT_ONE")
    parquet = files[0]
    columns = "symbol,cast(trade_date as varchar) trade_date,bar_time,frequency,open,high,low,close,volume,amount,source,data_version,cast(fetched_at as varchar) fetched_at"
    with duckdb.connect(":memory:") as con:
        cursor = con.execute(f"select {columns} from read_parquet(?)", [str(parquet)])
        names = [item[0] for item in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    receipt = audit_rows(rows, trade_date=args.trade_date)
    receipt.update({
        "schema": "ASL_NATIVE_5M_TIP_AUDIT_V01", "run_id": args.run_id,
        "trade_date": args.trade_date.isoformat(), "curated_path": str(parquet),
        "parquet_sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
        "fetched_at_min": min((str(row["fetched_at"]) for row in rows), default=None),
        "fetched_at_max": max((str(row["fetched_at"]) for row in rows), default=None),
        "publication_authority": False,
    })
    output = args.data_root / "meta/asl/audits/5m" / f"trade_date={args.trade_date.isoformat()}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
