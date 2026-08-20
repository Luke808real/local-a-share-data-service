#!/usr/bin/env python3
"""R4A2 EX_DATE / IPO / WINDOW_EDGE parity probe (bounded Baostock, read-only).

EX_DATE: compares the official ex-right reference-price formula candidate
(freeze semantics, OFFICIAL_RULE) against BaoStock historical preclose, for
standard single-action and same-day multi-action samples. IPO / WINDOW_EDGE:
probe only BaoStock preclose availability (no canonical promotion). Market
lake is never written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import polars as pl

ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")


def sha_of(rows) -> str:
    return hashlib.sha256(
        json.dumps([sorted(map(str, r)) for r in rows], separators=(",", ":")).encode()
    ).hexdigest()


def strat_sample(rows: list[tuple], n: int) -> list[tuple]:
    rows = sorted(set(rows))
    step = max(1, len(rows) // max(1, n))
    return rows[::step][:n]


def main() -> int:
    import baostock as bs

    # --- local inputs (read-only) ---
    df = pl.scan_parquet(str(ROOT / "curated/daily_bars/**/*.parquet"))
    db = (
        df.select("symbol", "trade_date", "close")
        .collect()
        .with_columns(pl.col("symbol").cast(str), pl.col("close").cast(pl.Float64))
        .sort(["symbol", "trade_date"])
    )
    db = db.with_columns(pl.col("close").shift(1).over("symbol").alias("prev_close"))
    ca = pl.concat(
        [pl.read_parquet(p) for p in sorted((ROOT / "curated/corporate_actions").rglob("*.parquet"))]
    )
    ca = ca.with_columns(pl.col("symbol").cast(str))
    inst = (
        pl.read_parquet(ROOT / "curated/instruments/part-merged.parquet")
        .select(["symbol", "list_date"])
        .with_columns(pl.col("symbol").cast(str))
        .unique(subset=["symbol"])
    )

    def prev_close(symbol: str, d: date) -> float | None:
        r = db.filter((pl.col("symbol") == symbol) & (pl.col("trade_date") == d))
        return float(r["prev_close"][0]) if r.height and r["prev_close"][0] is not None else None

    # --- EX_DATE pools ---
    agg = (
        ca.group_by(["symbol", "ex_date"])
        .agg(
            pl.col("cash_dividend").max().alias("cash"),
            pl.col("bonus_ratio").max().alias("bonus"),
            pl.col("allotment_ratio").max().alias("aratio"),
            pl.col("allotment_price").max().alias("aprice"),
            pl.len().alias("n_actions"),
        )
        .with_columns(pl.col("symbol").cast(str), pl.col("ex_date").cast(pl.Date))
    )
    standard = agg.filter(pl.col("n_actions") == 1)
    multi = agg.filter(pl.col("n_actions") > 1)
    std_rows = [(str(r[0]), r[1].isoformat()) for r in standard.iter_rows()]
    multi_rows = [(str(r[0]), r[1].isoformat()) for r in multi.iter_rows()]
    std_s = strat_sample(std_rows, 100)
    multi_s = strat_sample(multi_rows, 50)

    # --- IPO / EDGE pools ---
    first = (
        db.group_by("symbol")
        .agg(pl.col("trade_date").min().alias("first_date"))
        .join(inst, on="symbol", how="left")
    )
    ipo = first.filter(pl.col("first_date") == pl.col("list_date"))
    edge = first.filter(
        pl.col("list_date").is_not_null()
        & (pl.col("first_date") > pl.col("list_date"))
    )
    ipo_s = strat_sample([(str(r[0]), r[2].isoformat()) for r in ipo.iter_rows()], 40)
    edge_s = strat_sample([(str(r[0]), r[2].isoformat()) for r in edge.iter_rows()], 40)

    def bs_code(symbol: str) -> str:
        code_part, ex = symbol.split(".")
        return ("sh" if ex == "SH" else "sz") + "." + code_part

    def query(code: str, d: str) -> tuple[str, list[str]]:
        rs = bs.query_history_k_data_plus(
            code,
            "date,code,preclose,tradestatus",
            start_date=d,
            end_date=d,
            frequency="d",
            adjustflag="3",
        )
        rows = []
        if rs.error_code == "0":
            while rs.next():
                rows.append(rs.get_row_data())
        return rs.error_code, rows

    bs.login()
    try:
        # EX_DATE
        ex_results = []
        for symbol, ds in std_s + multi_s:
            d = date.fromisoformat(ds)
            pc = prev_close(symbol, d)
            row = agg.filter(
                (pl.col("symbol") == symbol) & (pl.col("ex_date") == d)
            )
            cash, bonus, aratio, aprice = (
                float(row["cash"][0] or 0),
                float(row["bonus"][0] or 0),
                float(row["aratio"][0] or 0),
                float(row["aprice"][0] or 0),
            )
            raw = None
            cand = None
            if pc is not None:
                denom = Decimal(1) + Decimal(str(bonus)) + Decimal(str(aratio))
                if denom != 0:
                    raw = (
                        Decimal(str(pc)) - Decimal(str(cash))
                        + Decimal(str(aprice)) * Decimal(str(aratio))
                    ) / denom
                    cand = float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            err, br = query(bs_code(symbol), ds)
            bsp = float(br[0][2]) if br and br[0][2] not in ("", "None") else None
            diff = abs(cand - bsp) if cand is not None and bsp is not None else None
            ex_results.append(
                {
                    "symbol": symbol,
                    "ex_date": ds,
                    "n_actions": int(row["n_actions"][0]),
                    "cash": cash,
                    "bonus": bonus,
                    "aratio": aratio,
                    "aprice": aprice,
                    "local_prev_close": pc,
                    "raw_formula": float(raw) if raw is not None else None,
                    "candidate_0_01": cand,
                    "bs_preclose": bsp,
                    "diff": diff,
                    "bs_err": err,
                    "tradestatus": br[0][3] if br else None,
                }
            )
        # IPO / EDGE availability
        ipo_res, edge_res = [], []
        for symbol, ds in ipo_s:
            err, br = query(bs_code(symbol), ds)
            ipo_res.append(
                {
                    "symbol": symbol,
                    "date": ds,
                    "bs_preclose": float(br[0][2]) if br and br[0][2] not in ("", "None") else None,
                    "bs_err": err,
                }
            )
        for symbol, ds in edge_s:
            err, br = query(bs_code(symbol), ds)
            edge_res.append(
                {
                    "symbol": symbol,
                    "date": ds,
                    "bs_preclose": float(br[0][2]) if br and br[0][2] not in ("", "None") else None,
                    "bs_err": err,
                }
            )
    finally:
        bs.logout()

    def summarize(rows, key="n") -> dict:
        return {
            "N": len(rows),
            "EXACT": sum(1 for r in rows if r["diff"] is not None and r["diff"] < 1e-9),
            "WITHIN_0_01": sum(1 for r in rows if r["diff"] is not None and r["diff"] <= 0.01),
            "MISMATCH": sum(1 for r in rows if r["diff"] is not None and r["diff"] > 0.01),
            "MAX_DIFF": max((r["diff"] for r in rows if r["diff"] is not None), default=None),
        }

    std = [r for r in ex_results if r["n_actions"] == 1]
    mu = [r for r in ex_results if r["n_actions"] > 1]
    out = {
        "EX_DATE_STANDARD": summarize(std),
        "EX_DATE_MULTI": summarize(mu),
        "IPO_SAMPLE_N": len(ipo_s),
        "IPO_PRECLOSE_NON_NULL_N": sum(1 for r in ipo_res if r["bs_preclose"] is not None),
        "IPO_PRECLOSE_NULL_N": sum(1 for r in ipo_res if r["bs_preclose"] is None),
        "IPO_CROSSCHECK_REFERENCE_AVAILABLE": sum(1 for r in ipo_res if r["bs_preclose"] is not None) > 0,
        "WINDOW_EDGE_SAMPLE_N": len(edge_s),
        "WINDOW_EDGE_PRECLOSE_NON_NULL_N": sum(1 for r in edge_res if r["bs_preclose"] is not None),
        "WINDOW_EDGE_CROSSCHECK_AVAILABLE": sum(1 for r in edge_res if r["bs_preclose"] is not None) > 0,
        "SAMPLE_HASH": sha_of(std_s + multi_s + ipo_s + edge_s),
        "detail_ex": ex_results,
        "detail_ipo": ipo_res,
        "detail_edge": edge_res,
    }
    Path("/tmp/r4a2_exdate_probe.json").write_text(json.dumps(out, indent=2, default=str))
    print(
        json.dumps(
            {
                k: v
                for k, v in out.items()
                if not k.startswith("detail")
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
