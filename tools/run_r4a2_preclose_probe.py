#!/usr/bin/env python3
"""R4A2 bounded Baostock historical preclose probe (read-only vs market lake).

Probes whether BaoStock historical daily `preclose` (frequency=d,
adjustflag=3 unadjusted, fields date,code,preclose,tradestatus) is a usable
independent historical CROSSCHECK source for R4A NORMAL / EX_DATE / IPO /
WINDOW_EDGE, on a deterministic sample (<= 300 symbol-days). The market lake is
never written. This is a bounded provider probe authorized by the task.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[1]
ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
ASOF = date(2026, 8, 17)


def _sha(rows) -> str:
    return hashlib.sha256(
        json.dumps([sorted(map(str, r)) for r in rows], separators=(",", ":")).encode()
    ).hexdigest()


def load_daily_bars() -> pl.DataFrame:
    lf = pl.scan_parquet(str(ROOT / "curated/daily_bars/**/*.parquet"))
    df = lf.select("symbol", "trade_date", "close").collect()
    df = df.with_columns(pl.col("symbol").cast(str), pl.col("close").cast(pl.Float64))
    df = df.sort(["symbol", "trade_date"])
    df = df.with_columns(pl.col("close").shift(1).over("symbol").alias("prev_close"))
    return df


def sample_pool(df: pl.DataFrame, *, kind: str, n: int) -> list[tuple[str, str]]:
    if kind == "NORMAL":
        rows = (
            df.filter(pl.col("prev_close").is_not_null())
            .select(["symbol", "trade_date", "prev_close"])
            .unique(subset=["symbol", "trade_date"])
        )
    else:
        raise ValueError(kind)
    out: list[tuple[str, str]] = []
    for year in sorted({r["trade_date"].year for r in rows.iter_rows(named=True)}):
        yr = rows.filter(pl.col("trade_date").dt.year() == year)
        step = max(1, yr.height // max(1, n // max(1, 6)))
        picked = yr.select(["symbol", "trade_date"]).unique().sort(["symbol", "trade_date"])
        for idx in range(0, picked.height, step):
            row = picked.row(idx)
            out.append((str(row[0]), row[1].isoformat()))
            if len(out) >= n:
                return out
    return out[:n]


def local_ref(df: pl.DataFrame, symbol: str, trade_date: str) -> float | None:
    d = date.fromisoformat(trade_date)
    r = df.filter(
        (pl.col("symbol") == symbol) & (pl.col("trade_date") == d)
    )
    return float(r["prev_close"][0]) if r.height else None


def main() -> int:
    import baostock as bs

    df = load_daily_bars()
    sample = sample_pool(df, kind="NORMAL", n=50)
    results = []
    bs.login()
    try:
        for symbol, d in sample:
            code_part, ex = symbol.split(".")
            code = ("sh" if ex == "SH" else "sz") + "." + code_part
            rs = bs.query_history_k_data_plus(
                code,
                "date,code,preclose,tradestatus",
                start_date=d,
                end_date=d,
                frequency="d",
                adjustflag="3",
            )
            bs_rows = []
            if rs.error_code == "0":
                while rs.next():
                    bs_rows.append(rs.get_row_data())
            bs_preclose = None
            if bs_rows:
                try:
                    bs_preclose = float(bs_rows[0][2])
                except (TypeError, ValueError):
                    bs_preclose = None
            local = local_ref(df, symbol, d)
            results.append(
                {
                    "symbol": symbol,
                    "date": d,
                    "local_prev_close": local,
                    "bs_preclose": bs_preclose,
                    "bs_err": rs.error_code,
                    "tradestatus": bs_rows[0][3] if bs_rows else None,
                }
            )
    finally:
        bs.logout()

    complete = [
        r
        for r in results
        if r["local_prev_close"] is not None and r["bs_preclose"] is not None
    ]
    exact = sum(1 for r in complete if abs(r["local_prev_close"] - r["bs_preclose"]) < 1e-9)
    within = sum(1 for r in complete if abs(r["local_prev_close"] - r["bs_preclose"]) <= 0.01)
    mismatch = sum(1 for r in complete if abs(r["local_prev_close"] - r["bs_preclose"]) > 0.01)
    maxdiff = max((abs(r["local_prev_close"] - r["bs_preclose"]) for r in complete), default=None)
    out = {
        "SAMPLE_N": len(sample),
        "SAMPLE_HASH": _sha(sample),
        "COMPARABLE_N": len(complete),
        "EXACT_MATCH_N": exact,
        "WITHIN_0_01_N": within,
        "MISMATCH_N": mismatch,
        "MAX_ABS_DIFF": maxdiff,
        "NORMAL_PARITY_STATUS": (
            "PASS" if mismatch == 0 else ("WITHIN_0_01" if within == len(complete) else "FAIL")
        ),
        "samples": results,
    }
    Path("/tmp/r4a2_normal_probe.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({k: v for k, v in out.items() if k != "samples"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
