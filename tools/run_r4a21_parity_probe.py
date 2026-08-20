#!/usr/bin/env python3
"""R4A2.1 preclose parity evidence hardening probe (read-only lake + bounded
BaoStock). Stratified sample (exchange x year x case x action composition),
provider response identity gate, strict parity accounting, persistent
SAMPLE_MANIFEST.json and R4A2_1_PARITY_DETAIL.json. Market lake never written.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import polars as pl

ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
OUT = Path("reports/planning")
CASES = ["NORMAL", "EX_DATE_CASH_ONLY", "EX_DATE_BONUS_ONLY", "EX_DATE_ALLOTMENT",
         "EX_DATE_MULTI", "IPO", "WINDOW_EDGE"]
CASE_BUDGET = {"NORMAL": 50, "EX_DATE_CASH_ONLY": 40, "EX_DATE_BONUS_ONLY": 30,
               "EX_DATE_ALLOTMENT": 10, "EX_DATE_MULTI": 50, "IPO": 40, "WINDOW_EDGE": 40}
TOTAL_BUDGET = sum(CASE_BUDGET.values())
A_ACTION = {"cash_dividend": "cash", "bonus": "bonus", "allotment": "allot"}


def sha_rows(rows) -> str:
    return hashlib.sha256(
        json.dumps([sorted(map(str, r)) for r in rows], separators=(",", ":")).encode()
    ).hexdigest()


def stratified(pool, budget: int, key_fn) -> tuple[list, list[str]]:
    """Deterministic (exchange, year) stratified sampling. Returns sample + shortfalls."""
    pool = sorted(pool)
    by_cell = {}
    for r in pool:
        by_cell.setdefault(key_fn(*r), []).append(r)
    cells = sorted(by_cell)
    per_cell = budget // max(1, len(cells))
    out = []
    shortfalls = []
    for cell in cells:
        items = sorted(by_cell[cell])
        take = min(per_cell, len(items))
        out.extend(items[:take])
        if len(items) < per_cell:
            shortfalls.append(f"{cell}: have {len(items)} need {per_cell}")
        if len(out) >= budget:
            break
    # top up to budget deterministically
    if len(out) < budget:
        selected = set(out)
        extras = [r for r in pool if r not in selected][: budget - len(out)]
        out.extend(extras)
    return out[:budget], shortfalls


def main() -> int:
    import baostock as bs

    db = (
        pl.scan_parquet(str(ROOT / "curated/daily_bars/**/*.parquet"))
        .select("symbol", "trade_date", "close")
        .collect()
        .with_columns(pl.col("symbol").cast(str), pl.col("close").cast(pl.Float64))
        .sort(["symbol", "trade_date"])
    )
    db = db.with_columns(pl.col("close").shift(1).over("symbol").alias("prev_close"))
    lst = (
        db.group_by("symbol")
        .agg(pl.col("trade_date").min().alias("first_date"))
        .with_columns(pl.col("symbol").cast(str))
    )
    inst = (
        pl.read_parquet(ROOT / "curated/instruments/part-merged.parquet")
        .select(["symbol", "list_date"])
        .with_columns(pl.col("symbol").cast(str))
        .unique(subset=["symbol"])
    )
    lst = lst.join(inst, on="symbol", how="left")
    ca = pl.concat([pl.read_parquet(p) for p in sorted((ROOT / "curated/corporate_actions").rglob("*.parquet"))])
    ca = ca.with_columns(pl.col("symbol").cast(str))

    # case pools
    known = db.filter(pl.col("prev_close").is_not_null()).select("symbol", "trade_date").unique()
    normal_pool = [(r[0], r[1].isoformat()) for r in known.iter_rows()]
    agg = (
        ca.group_by(["symbol", "ex_date"])
        .agg(
            pl.col("cash_dividend").max().alias("cash"),
            pl.col("bonus_ratio").max().alias("bonus"),
            pl.col("allotment_ratio").max().alias("aratio"),
            pl.col("allotment_price").max().alias("aprice"),
            pl.len().alias("n"),
        )
        .with_columns(pl.col("ex_date").cast(pl.Date))
    )
    single = agg.filter(pl.col("n") == 1)
    cash_pool = [(str(r[0]), r[1].isoformat()) for r in
                 single.filter(pl.col("cash") > 0).iter_rows()]
    bonus_pool = [(str(r[0]), r[1].isoformat()) for r in
                  single.filter(pl.col("bonus") > 0).iter_rows()]
    allot_pool = [(str(r[0]), r[1].isoformat()) for r in
                  single.filter(pl.col("aratio") > 0).iter_rows()]
    multi_pool = [(str(r[0]), r[1].isoformat()) for r in agg.filter(pl.col("n") > 1).iter_rows()]
    ipo_pool = [(str(r[0]), r[2].isoformat()) for r in lst.filter(pl.col("first_date") == pl.col("list_date")).iter_rows()]
    edge_pool = [(str(r[0]), r[2].isoformat()) for r in
                 lst.filter(pl.col("list_date").is_not_null() & (pl.col("first_date") > pl.col("list_date"))).iter_rows()]

    def cell(s, d):
        return (s.split(".")[1], d[:4])

    s_normal, sf_normal = stratified(normal_pool, CASE_BUDGET["NORMAL"], cell)
    s_cash, sf_cash = stratified(cash_pool, CASE_BUDGET["EX_DATE_CASH_ONLY"], cell)
    s_bonus, sf_bonus = stratified(bonus_pool, CASE_BUDGET["EX_DATE_BONUS_ONLY"], cell)
    s_allot, sf_allot = stratified(allot_pool, CASE_BUDGET["EX_DATE_ALLOTMENT"], cell)
    s_multi, sf_multi = stratified(multi_pool, CASE_BUDGET["EX_DATE_MULTI"], cell)
    s_ipo, sf_ipo = stratified(ipo_pool, CASE_BUDGET["IPO"], cell)
    s_edge, sf_edge = stratified(edge_pool, CASE_BUDGET["WINDOW_EDGE"], cell)

    sample = []
    for case, rows, in (
        ("NORMAL", s_normal), ("EX_DATE_CASH_ONLY", s_cash), ("EX_DATE_BONUS_ONLY", s_bonus),
        ("EX_DATE_ALLOTMENT", s_allot), ("EX_DATE_MULTI", s_multi), ("IPO", s_ipo),
        ("WINDOW_EDGE", s_edge),
    ):
        for (s, d) in rows:
            code_part, ex = s.split(".")
            actions = []
            if case.startswith("EX_DATE"):
                r = agg.filter((pl.col("symbol") == s) & (pl.col("ex_date") == date.fromisoformat(d)))
                for col, act in (("cash", "cash"), ("bonus", "bonus"), ("aratio", "allot")):
                    if float(r[col][0] or 0) > 0:
                        actions.append(act)
            sample.append(
                {
                    "symbol": s,
                    "trade_date": d,
                    "exchange": ex,
                    "year": d[:4],
                    "case": case,
                    "action_types": actions,
                    "bs_code": ("sh" if ex == "SH" else "sz") + "." + code_part,
                }
            )
    sample_hash = sha_rows([(r["symbol"], r["trade_date"], r["case"]) for r in sample])
    shortfalls = {
        "NORMAL": sf_normal, "EX_DATE_CASH_ONLY": sf_cash, "EX_DATE_BONUS_ONLY": sf_bonus,
        "EX_DATE_ALLOTMENT": sf_allot, "EX_DATE_MULTI": sf_multi, "IPO": sf_ipo,
        "WINDOW_EDGE": sf_edge,
    }
    manifest = {
        "SAMPLE_N": len(sample),
        "SAMPLE_HASH": sample_hash,
        "stratification": "exchange x year x case x action composition",
        "case_budget": CASE_BUDGET,
        "shortfalls": {k: v for k, v in shortfalls.items() if v},
        "shortfall_note": "cell shortfalls reported explicitly; no silent rebalance",
        "rows": sample,
    }
    (OUT / "SAMPLE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, default=str))

    def prev_close(symbol: str, d: str) -> float | None:
        r = db.filter(
            (pl.col("symbol") == symbol)
            & (pl.col("trade_date") == date.fromisoformat(d))
        )
        return float(r["prev_close"][0]) if r.height and r["prev_close"][0] is not None else None

    def local_ref(row) -> dict:
        s, d = row["symbol"], row["trade_date"]
        pc = prev_close(s, d)
        if row["case"] == "NORMAL":
            return {"local_prev_close": pc, "candidate_display": None, "raw_formula": None,
                    "agg": None}
        r = agg.filter(
            (pl.col("symbol") == s) & (pl.col("ex_date") == date.fromisoformat(d))
        )
        if r.height == 0 or pc is None:
            return {"local_prev_close": pc, "candidate_display": None, "raw_formula": None,
                    "agg": None}
        cash, bonus, aratio, aprice = (
            float(r["cash"][0] or 0), float(r["bonus"][0] or 0),
            float(r["aratio"][0] or 0), float(r["aprice"][0] or 0),
        )
        denom = Decimal(1) + Decimal(str(bonus)) + Decimal(str(aratio))
        raw = None
        cand = None
        if denom != 0:
            raw = (Decimal(str(pc)) - Decimal(str(cash)) + Decimal(str(aprice)) * Decimal(str(aratio))) / denom
            cand = float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return {"local_prev_close": pc, "candidate_display": cand, "raw_formula": float(raw) if raw else None,
                "agg": {"cash": cash, "bonus": bonus, "aratio": aratio, "aprice": aprice}}

    def query(code: str, d: str) -> tuple[str, str, list[str]]:
        rs = bs.query_history_k_data_plus(
            code, "date,code,preclose,tradestatus",
            start_date=d, end_date=d, frequency="d", adjustflag="3",
        )
        rows = []
        if rs.error_code == "0":
            while rs.next():
                rows.append(rs.get_row_data())
        return rs.error_code, rs.error_msg, rows

    def parse_preclose(value):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"bs.login failed: {lg.error_code} {lg.error_msg}")
    details = []
    try:
        for idx, row in enumerate(sample):
            err, err_msg, br = query(row["bs_code"], row["trade_date"])
            if idx % 50 == 0 and idx:
                print(f"progress {idx}/{len(sample)}", flush=True)
            bsp = parse_preclose(br[0][2]) if len(br) == 1 else None
            tradestatus = br[0][3] if len(br) == 1 else None
            identity_ok = (
                err == "0"
                and len(br) == 1
                and br[0][1] == row["bs_code"]
                and br[0][0] == row["trade_date"]
                and bsp is not None
                and tradestatus not in (None, "")
            )
            if not identity_ok:
                reason = (
                    "ERR_NOT_ZERO" if err != "0"
                    else "ROW_COUNT_NE_1" if len(br) != 1
                    else "CODE_MISMATCH" if (br and br[0][1] != row["bs_code"])
                    else "DATE_MISMATCH" if (br and br[0][0] != row["trade_date"])
                    else "PRECLOSE_NOT_PARSEABLE" if bsp is None
                    else "TRADESTATUS_MISSING" if tradestatus in (None, "")
                    else "UNKNOWN"
                )
                details.append(
                    {
                        **row,
                        "comparable": False,
                        "uncompared_reason": reason,
                        "err_msg": err_msg,
                        "local_prev_close": prev_close(row["symbol"], row["trade_date"]),
                        "candidate_display": None,
                        "raw_formula": None,
                        "baostock_preclose": None,
                        "diff": None,
                        "tradestatus": tradestatus,
                    }
                )
                continue
            import time

            time.sleep(0.08)
            lr = local_ref(row)
            cand = lr.get("candidate_display")
            lr_cmp = lr["local_prev_close"] if row["case"] == "NORMAL" else cand
            if lr_cmp is None:
                details.append(
                    {
                        **row,
                        "comparable": False,
                        "uncompared_reason": "LOCAL_REFERENCE_UNAVAILABLE",
                        "err_msg": err_msg,
                        "local_prev_close": lr["local_prev_close"],
                        "raw_formula": lr["raw_formula"],
                        "candidate_display": lr["candidate_display"],
                        "agg": lr["agg"],
                        "baostock_preclose": bsp,
                        "diff": None,
                        "tradestatus": tradestatus,
                    }
                )
                continue
            diff = abs(lr_cmp - bsp) if lr_cmp is not None and bsp is not None else None
            details.append(
                {
                    **row,
                    "comparable": True,
                    "uncompared_reason": None,
                    "err_msg": err_msg,
                    "local_prev_close": lr["local_prev_close"],
                    "raw_formula": lr["raw_formula"],
                    "candidate_display": lr["candidate_display"],
                    "agg": lr["agg"],
                    "baostock_preclose": bsp,
                    "diff": diff,
                    "tradestatus": tradestatus,
                }
            )
    finally:
        bs.logout()

    def acct(rows):
        comparable = [r for r in rows if r["comparable"] and r["diff"] is not None]
        exact = [r for r in comparable if r["diff"] < 1e-9]
        within = [r for r in comparable if 1e-9 <= r["diff"] <= 0.01]
        gt = [r for r in comparable if r["diff"] > 0.01]
        return {
            "SAMPLE_N": len(rows),
            "COMPARABLE_N": len(comparable),
            "UNCOMPARED_N": len(rows) - len(comparable),
            "EXACT_N": len(exact),
            "NONZERO_WITHIN_0_01_N": len(within),
            "GT_0_01_N": len(gt),
            "MAX_ABS_DIFF": max((r["diff"] for r in comparable), default=None),
        }

    summary = {}
    for case in CASES:
        summary[case] = acct([r for r in details if r["case"] == case])
    # accounting self-check
    bad = {}
    for case, s in summary.items():
        if (s["SAMPLE_N"] != s["COMPARABLE_N"] + s["UNCOMPARED_N"]
                or s["COMPARABLE_N"] != s["EXACT_N"] + s["NONZERO_WITHIN_0_01_N"] + s["GT_0_01_N"]):
            bad[case] = s
    (OUT / "R4A2_1_PARITY_DETAIL.json").write_text(
        json.dumps({"sample_hash": sample_hash, "rows": details}, indent=2, default=str)
    )
    print(json.dumps({"SAMPLE_N": len(sample), "SAMPLE_HASH": sample_hash,
                      "accounting_error": bad if bad else None, "summary": summary},
                     indent=2, default=str))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
