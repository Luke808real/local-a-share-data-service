#!/usr/bin/env python3
"""Build non-authoritative 5m-derived review evidence from curated CNEquity bars."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))
from ashare_data.five_minute_review import SCHEMA, SCOPE, derive_review_rows  # noqa: E402
from ashare_data.local_query import LocalQuery, QueryError  # noqa: E402


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-5m-review-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def build(data_root: Path, trade_date: date, run_id: str) -> dict:
    glob = data_root / "curated/minute_bars_5m" / f"trade_date={trade_date.isoformat()}" / "*.parquet"
    if not list(glob.parent.glob("*.parquet")):
        raise RuntimeError("REVIEW_5M_NOT_CURATED")
    prior_as_of = trade_date - timedelta(days=1)
    with LocalQuery(data_root) as query:
        # Scope is the pointer-bound R3 SH/SZ membership, never the whole
        # historical CNEquity instrument catalog.
        formal_relation = (
            "select distinct upper(i.symbol) symbol from " + query._instrument_relation + " i "
            "where i.exchange in ('SH','SZ') "
            "and regexp_matches(upper(i.symbol), '^[0-9]{6}\\.(SH|SZ)$') "
            "and exists (select 1 from " + query._daily_relation + " d where d.symbol=i.symbol)"
        )
        columns = "m.symbol,cast(m.trade_date as varchar) trade_date,cast(m.bar_time as varchar) bar_time,m.frequency,m.open,m.high,m.low,m.close,m.volume,m.amount,m.source,m.data_version"
        with duckdb.connect(":memory:") as con:
            cursor = con.execute(
                "select " + columns + " from read_parquet(?) m join (" + formal_relation + ") f using(symbol)",
                [str(glob)],
            )
            names = [item[0] for item in cursor.description]
            bars = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        prior_rows = query._execute(
            "select symbol,arg_max(close, trade_date) as prior_close from " + query._daily_allowlist(end=prior_as_of)
            + " where trade_date <= ? and symbol in (select symbol from (" + formal_relation + ")) group by symbol",
            [prior_as_of],
        )
        prior_by_symbol = {row["symbol"]: float(row["prior_close"]) for row in prior_rows if row["prior_close"] is not None}

        def prior_close(symbol: str) -> float | None:
            return prior_by_symbol.get(symbol)
        rows = derive_review_rows(bars, trade_date=trade_date, prior_close=prior_close)
    payload = {
        "schema": SCHEMA, "scope": SCOPE, "publication_authority": False,
        "trade_date": trade_date.isoformat(), "source": "CNEQUITY_MINUTE_BARS_5M", "cnequity_run_id": run_id,
        "row_n": len(rows), "ready_row_n": sum(row["row_state"] == "REVIEW_ROW_READY" for row in rows),
        "noncomparable_row_n": sum(row["row_state"] == "REVIEW_ROW_NON_COMPARABLE" for row in rows),
        "needs_formal_preclose_row_n": sum(row["row_state"] == "REVIEW_ROW_NEEDS_FORMAL_PRECLOSE" for row in rows),
        "invalid_row_n": sum(row["row_state"] == "REVIEW_ROW_INVALID" for row in rows), "rows": rows,
    }
    _atomic_json(data_root / "meta/asl/fast_review/5m" / f"trade_date={trade_date.isoformat()}.json", payload)
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("/Users/luke808/AI/local-a-share-data-service-data"))
    parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.data_root, args.trade_date, args.run_id), sort_keys=True))
