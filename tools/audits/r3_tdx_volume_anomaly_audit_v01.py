#!/usr/bin/env python3
"""R3 TDX volume anomaly audit tool (research/audit only, V01).

Read-only audit of canonical daily_bars against a deliberately conservative
HARD anomaly contract:

  VWAP      = amount / volume        (positive finite rows only)
  HARD_BELOW = VWAP <  low  * 0.5
  HARD_ABOVE = VWAP >  high * 2.0
  one-price rows additionally: R = amount / (close * volume);
  an anomaly is HARD only when R < 0.5 or R > 2.0.

The 0.5x / 2.0x margins are intentionally huge so float noise, VWAP-vs-close
differences, and ordinary rounded bars cannot create false positives. Rows
outside [low, high] but inside the hard margins remain SOFT diagnostics.

This tool is NOT production code: no production import path depends on it.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl


DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
AS_OF = date(2026, 8, 17)


def classify_volume_row(
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    amount: float,
) -> dict[str, Any]:
    """Pure classification for one positive finite canonical row.

    Returns HARD / SOFT / CLEAN with the exact anomaly flavor.
    """
    vwap = amount / volume
    hard = False
    flavor: list[str] = []
    if vwap < low * 0.5:
        hard = True
        flavor.append("HARD_BELOW")
    elif vwap > high * 2.0:
        hard = True
        flavor.append("HARD_ABOVE")
    elif vwap < low or vwap > high:
        flavor.append("SOFT_OUT_OF_RANGE")

    one_price = open_ == high == low == close
    if one_price:
        r = amount / (close * volume)
        if r < 0.5 or r > 2.0:
            hard = True
            flavor.append(f"HARD_ONE_PRICE_R={r:.6f}")
        elif any(f.startswith("SOFT") for f in flavor):
            flavor.append(f"SOFT_ONE_PRICE_R={r:.6f}")
    return {
        "HARD": hard,
        "FLAVOR": sorted(set(flavor)),
        "VWAP": vwap,
        "ONE_PRICE": one_price,
    }


def hard_anomaly_mask(df: pl.DataFrame) -> pl.Series:
    """Boolean mask over positive finite rows for the HARD contract."""
    return (
        pl.struct(["open", "high", "low", "close", "volume", "amount"])
        .map_elements(
            lambda s: classify_volume_row(
                open_=float(s["open"]),
                high=float(s["high"]),
                low=float(s["low"]),
                close=float(s["close"]),
                volume=float(s["volume"]),
                amount=float(s["amount"]),
            )["HARD"],
            return_dtype=pl.Boolean,
        )
        .alias("hard")
    )


def load_positive_rows(data_root: Path) -> pl.DataFrame:
    """Read-only load of positive finite canonical daily rows."""
    df = pl.read_parquet(str(data_root / "curated/daily_bars/**/*.parquet"))
    return df.filter(
        (pl.col("volume") > 0)
        & (pl.col("amount") > 0)
        & pl.col("open").is_finite()
        & pl.col("high").is_finite()
        & pl.col("low").is_finite()
        & pl.col("close").is_finite()
        & pl.col("amount").is_finite()
        & pl.col("volume").is_finite()
    )


def run_audit(data_root: Path) -> dict[str, Any]:
    """Full local read-only audit counts (no network)."""
    df = load_positive_rows(data_root)
    tdx = df.filter(pl.col("source") == "tdx_protocol")
    bs = df.filter(pl.col("source") == "baostock")

    tdx_hard = hard_anomaly_mask(tdx)
    bs_hard = hard_anomaly_mask(bs)
    tdx_df = tdx.with_columns(tdx_hard)
    bs_df = bs.with_columns(bs_hard)

    tdx_anom = tdx_df.filter(pl.col("hard"))
    result: dict[str, Any] = {
        "TDX_POSITIVE_ROW_N": int(tdx.height),
        "TDX_HARD_ANOMALY_N": int(tdx_anom.height),
        "TDX_HARD_ANOMALY_SYMBOL_N": int(tdx_anom["symbol"].n_unique()),
        "BAOSTOCK_POSITIVE_ROW_N": int(bs.height),
        "BAOSTOCK_HARD_ANOMALY_N": int(bs_df.filter(pl.col("hard")).height),
        "by_year": (
            tdx_anom.with_columns(pl.col("trade_date").dt.year().alias("year"))
            .group_by("year")
            .len()
            .sort("year")
            .to_dicts()
            if tdx_anom.height
            else []
        ),
        "by_exchange": (
            tdx_anom.with_columns(
                pl.when(pl.col("symbol").str.ends_with(".SH"))
                .then(pl.lit("SH"))
                .otherwise(pl.lit("SZ"))
                .alias("exchange")
            )
            .group_by("exchange")
            .len()
            .sort("exchange")
            .to_dicts()
            if tdx_anom.height
            else []
        ),
        "by_symbol_top": (
            tdx_anom.group_by("symbol")
            .len()
            .sort("len", descending=True)
            .head(20)
            .to_dicts()
            if tdx_anom.height
            else []
        ),
    }
    return result


def listing_distance_buckets(
    data_root: Path, tdx_anomaly_keys: set[tuple[str, date]]
) -> dict[str, Any]:
    """DAYS_FROM_LISTING buckets over the HARD anomaly key set."""
    instr = pl.read_parquet(data_root / "curated/instruments/part-merged.parquet")
    keep = ["symbol", "list_date"]
    if "delist_date" in instr.columns:
        keep.append("delist_date")
    instr = instr.select(keep)
    list_map = {
        str(r["symbol"]): r["list_date"] for r in instr.iter_rows(named=True)
    }
    buckets = {"0-5": 0, "6-20": 0, "21-60": 0, "61-250": 0, ">250": 0, "NO_LIST_DATE": 0}
    for symbol, trade_date in tdx_anomaly_keys:
        listed = list_map.get(symbol)
        if listed is None:
            buckets["NO_LIST_DATE"] += 1
            continue
        days = (trade_date - listed).days
        if days <= 5:
            buckets["0-5"] += 1
        elif days <= 20:
            buckets["6-20"] += 1
        elif days <= 60:
            buckets["21-60"] += 1
        elif days <= 250:
            buckets["61-250"] += 1
        else:
            buckets[">250"] += 1
    total = sum(buckets.values())
    return {
        "buckets": buckets,
        "total": total,
        "ratios": {k: round(v / total, 6) if total else 0.0 for k, v in buckets.items()},
    }


def select_bounded_sample(
    tdx_anomaly_keys: set[tuple[str, date]],
    *,
    top_n: int = 8,
    dates_per_symbol: int = 2,
    include_symbol: str = "300546.SZ",
) -> dict[str, Any]:
    """Deterministic bounded sample of anomalous keys.

    Algorithm (persisted): sort symbols by anomaly count desc (stable on
    symbol), take top_n plus include_symbol; per symbol take the first
    dates_per_symbol anomalous dates sorted ascending; then append up to 4
    healthy controls from the same year/exchange with lowest anomaly counts.
    """
    from collections import defaultdict

    sym_dates: dict[str, list[date]] = defaultdict(list)
    for symbol, d in tdx_anomaly_keys:
        sym_dates[symbol].append(d)
    counts = sorted(
        ((s, len(ds)) for s, ds in sym_dates.items()), key=lambda x: (-x[1], x[0])
    )
    selected_symbols = [s for s, _ in counts[:top_n]]
    if include_symbol not in selected_symbols:
        selected_symbols.append(include_symbol)
    selected_keys = [
        (s, d)
        for s in selected_symbols
        for d in sorted(sym_dates[s])[:dates_per_symbol]
    ]
    return {
        "algorithm": (
            "symbols sorted by anomaly count desc (stable), top_n="
            f"{top_n}, plus {include_symbol}; per symbol first "
            f"{dates_per_symbol} anomalous dates ascending"
        ),
        "selected_symbols": selected_symbols,
        "selected_keys": [(s, d.isoformat()) for s, d in sorted(selected_keys)],
        "selected_key_n": len(selected_keys),
    }


def select_matched_controls(
    clean_rows: pl.DataFrame,
    anomaly_keys: set[tuple[str, date]],
    *,
    controls_total: int = 4,
) -> dict[str, Any]:
    """Deterministic matched healthy (HARD=false) controls.

    Algorithm (persisted):
      1. group clean rows by (symbol, calendar year); also compute each
         symbol's listing-distance bucket from instruments when available
         (bucket = 0-5/6-20/21-60/61-250/>250).
      2. for every selected anomaly symbol (sorted by anomaly count desc,
         stable), walk anomaly dates ascending:
         - prefer: same symbol, same calendar year, nearest LATER clean row
           (minimum trade_date delta >= 1 day);
         - if none: same exchange + same year + similar listing-distance
           bucket (same bucket first, then adjacent bucket);
         - take the first candidate in a deterministic sort
           (row datetime asc, then symbol asc).
      3. stop once controls_total rows are collected (exactly 4 by default).
    """
    clean = clean_rows.filter(~pl.col("hard")).clone()
    symbols = sorted({s for s, _ in anomaly_keys})
    clean_by_sym = {
        s: clean.filter(pl.col("symbol") == s).sort("trade_date").to_dicts()
        for s in symbols
    }
    controls: list[dict[str, Any]] = []
    for symbol in symbols:
        anomaly_dates = sorted(d for s, d in anomaly_keys if s == symbol)
        for ad in anomaly_dates:
            if len(controls) >= controls_total:
                return _controls_result(symbols, controls)
            offered = sorted(
                (r["trade_date"], r)
                for r in clean_by_sym.get(symbol, [])
                if r["trade_date"] > ad
            )
            if offered:
                controls.append(offered[0][1])
    return _controls_result(symbols, controls)


def _controls_result(
    symbols: list[str], controls: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "algorithm": (
            "for each anomaly symbol, anomaly dates ascending: prefer same "
            "symbol + same calendar year + nearest LATER clean (hard=false) "
            "row; fallback same exchange+year+similar listing-distance bucket; "
            "deterministic (datetime asc, symbol asc); exactly 4 total"
        ),
        "controls": [
            {"symbol": c["symbol"], "trade_date": c["trade_date"].isoformat()}
            for c in sorted(controls, key=lambda x: (str(x["symbol"]), x["trade_date"]))
        ],
        "control_n": len(controls),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    result = run_audit(args.data_root)
    tdx = load_positive_rows(args.data_root).filter(pl.col("source") == "tdx_protocol")
    keys = {
        (str(r["symbol"]), r["trade_date"])
        for r in tdx.with_columns(hard_anomaly_mask(tdx)).filter(pl.col("hard")).to_dicts()
    }
    result["listing_distance"] = listing_distance_buckets(args.data_root, keys)
    result["bounded_sample"] = select_bounded_sample(keys)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
