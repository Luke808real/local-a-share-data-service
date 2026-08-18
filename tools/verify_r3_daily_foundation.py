#!/usr/bin/env python3
"""Read-only R3 DAILY FOUNDATION verifier.

Opens only the configured authoritative target root and the repository config.
Uses SQLite immutable read-only, DuckDB read_only catalog-only, and bounded lazy
Polars scans. Never imports upstream writer helpers and never writes any file.
Prints deterministic JSON to stdout.

Run (authorized launch shape):
  uv run --frozen --no-sync --offline python tools/verify_r3_daily_foundation.py \
      --config config/cnequity.toml --history-start 2016-01-01 \
      --as-of 2026-08-17 --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ashare_data.r3_daily import (  # noqa: E402
    CONFIG_SHA,
    LOCK_SHA,
    PINNED_CNEQUITY_SHA,
    R3_DAILY_AS_OF,
    R3_HISTORY_START,
    target_tree_snapshot,
)

BAR_DATASETS = ("instruments", "trading_calendar", "daily_bars")
UNITS_LOW, UNITS_HIGH = 0.8, 1.25


class VerifyError(RuntimeError):
    pass


def _fail(code: str, message: str):
    raise VerifyError(f"[{code}] {message}")


def list_curated_parquet(curated: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    if not curated.exists():
        return out
    for dataset_dir in sorted(curated.iterdir()):
        if not dataset_dir.is_dir():
            continue
        files = sorted(dataset_dir.rglob("*.parquet"))
        if files:
            out[dataset_dir.name] = files
    return out


def scan_curated(curated: Path, dataset: str) -> pl.LazyFrame:
    files = sorted((curated / dataset).rglob("*.parquet"))
    if not files:
        return pl.LazyFrame()
    return pl.scan_parquet([str(f) for f in files])


def structural_checks(curated: Path) -> dict:
    found = list_curated_parquet(curated)
    unexpected = sorted(set(found) - set(BAR_DATASETS))
    if unexpected:
        _fail("NON_R3_DATASET", f"unexpected curated dataset(s): {unexpected}")
    for dataset in BAR_DATASETS:
        if not found.get(dataset):
            _fail("MISSING_DATASET", f"no parquet for {dataset}")

    derived = curated.parent / "derived"
    derived_files = sorted(derived.rglob("*.parquet")) if derived.exists() else []
    if derived_files:
        _fail("DERIVED_PAYLOAD", f"derived parquet present: {[str(p.relative_to(derived)) for p in derived_files[:5]]}")

    out: dict = {}
    # instruments
    inst = scan_curated(curated, "instruments").collect()
    if inst.select(pl.col("symbol").n_unique().alias("n"))["n"][0] != inst.height:
        _fail("INSTRUMENTS_PK", "duplicate instruments.symbol")
    out["instruments_rows"] = inst.height

    cal = scan_curated(curated, "trading_calendar").collect()
    if cal.select(pl.col("trade_date").n_unique().alias("n"))["n"][0] != cal.height:
        _fail("CALENDAR_PK", "duplicate calendar trade_date")
    out["calendar_rows"] = cal.height

    daily = scan_curated(curated, "daily_bars").select(
        [
            pl.col("symbol"),
            pl.col("trade_date"),
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "source",
            "data_version",
            "fetched_at",
        ]
    )
    bad_dup = daily.select(
        [pl.col("symbol"), pl.col("trade_date")]
    ).group_by(["symbol", "trade_date"]).agg(pl.len().alias("n")).filter(pl.col("n") > 1).collect()
    if bad_dup.height:
        _fail("DAILY_PK", f"duplicate (symbol,trade_date): {bad_dup.height}")
    bad_bounds = (
        daily.filter(
            (pl.col("trade_date") < R3_HISTORY_START) | (pl.col("trade_date") > R3_DAILY_AS_OF)
        )
        .collect()
        .height
    )
    if bad_bounds:
        _fail("DATE_BOUNDS", f"{bad_bounds} daily rows outside frozen window")
    bad_values = (
        daily.filter(
            (pl.col("volume") < 0)
            | (pl.col("open") <= 0)
            | (pl.col("high") < pl.col("open"))
            | (pl.col("high") < pl.col("close"))
            | (pl.col("low") > pl.col("open"))
            | (pl.col("low") > pl.col("close"))
            | (pl.col("low") > pl.col("high"))
            | (pl.col("volume").is_null())
            | ((pl.col("amount").is_not_null()) & (pl.col("amount") < 0))
        )
        .collect()
        .height
    )
    if bad_values:
        _fail("VALUE_QUALITY", f"{bad_values} rows violate market-data invariants")
    versions = set(daily.select(pl.col("data_version").unique()).collect()["data_version"].to_list())
    if versions != {"v2"}:
        _fail("DATA_VERSION", f"unexpected data_version set: {versions}")
    if "close_pre_adj" in daily.collect_schema().names() or "qfq_close" in daily.collect_schema().names():
        _fail("ADJUSTED_COLUMN", "adjusted price column present in authoritative daily")
    out.update(
        {
            "daily_rows": daily.select(pl.len()).collect()["len"][0],
            "daily_duplicate": 0,
            "date_bound_violations": 0,
            "value_violations": 0,
            "data_versions": sorted(versions),
        }
    )
    return out


def unit_checks(daily: pl.LazyFrame) -> dict:
    frame = (
        daily.filter(
            (pl.col("volume") > 0)
            & pl.col("amount").is_not_null()
            & (pl.col("close") > 0)
            & (pl.col("amount") > 0)
        )
        .with_columns((pl.col("amount") / pl.col("close") / pl.col("volume")).alias("_ratio"))
        .group_by("source")
        .agg(
            pl.len().alias("rows"),
            pl.col("_ratio").median().alias("median_ratio"),
        )
        .collect()
    )
    results: dict = {}
    for row in frame.sort("source").iter_rows(named=True):
        source = str(row["source"])
        rows = int(row["rows"])
        median = float(row["median_ratio"]) if row["median_ratio"] is not None else None
        results[source] = {"rows": rows, "median_ratio": median}
        if rows >= 200 and median is not None and not (UNITS_LOW <= median <= UNITS_HIGH):
            _fail("UNIT_RATIO", f"source={source} median amount/close/volume {median:.4f} outside band")
    null_amount = (
        daily.filter(pl.col("amount").is_null())
        .group_by(["source", "trade_date"])
        .agg(pl.len().alias("n"))
        .collect()
    )
    null_sources = set(null_amount["source"].to_list())
    if null_sources - {"sina"}:
        _fail("NON_SINA_NULL_AMOUNT", f"null amount from non-Sina sources: {sorted(null_sources - {'sina'})}")
    return {
        "per_source": results,
        "sina_null_rows": int(
            null_amount.filter(pl.col("source") == "sina").select(pl.col("n").sum())[0, 0]
        ),
    }


def universe_checks(curated: Path, calendar_dates: set[date]) -> dict:
    inst = scan_curated(curated, "instruments").collect()
    ref = R3_DAILY_AS_OF
    active = inst.filter(
        (pl.col("asset_type").is_in(["stock", "cdr"]))
        & (pl.col("delist_date").is_null() | (pl.col("delist_date") >= ref))
        & (pl.col("list_date").is_null() | (pl.col("list_date") <= ref))
    )
    exchange_counts = dict(sorted(active["symbol"].str.split(".").list.get(-1).value_counts().to_dicts()[0].items()))
    for exchange in ("SH", "SZ", "BJ"):
        if exchange not in exchange_counts or exchange_counts[exchange] <= 0:
            _fail("UNIVERSE_MISSING", f"active {exchange} count is zero")
    bj = active.filter(pl.col("symbol").str.ends_with(".BJ"))
    if bj.filter(pl.col("name").is_null() | pl.col("list_date").is_null()).height:
        _fail("BJ_METADATA", "active BJ row missing name/list_date")

    daily = scan_curated(curated, "daily_bars").filter(pl.col("volume") > 0)
    per_symbol_days = (
        daily.group_by("symbol")
        .agg(pl.col("trade_date").min().alias("first_day"), pl.col("trade_date").max().alias("last_day"), pl.len().alias("n"))
        .collect()
    )
    covered = set(per_symbol_days["symbol"].to_list())
    active_symbols = set(active["symbol"].to_list())
    uncovered = sorted(active_symbols - covered)
    if uncovered:
        _fail("UNCOVERED_ACTIVE", f"{len(uncovered)} active symbols have no positive-volume row: {uncovered[:20]}")
    mapped = {row["symbol"]: (row["first_day"], row["last_day"]) for row in per_symbol_days.iter_rows(named=True)}
    return {
        "exchange_counts": exchange_counts,
        "active_symbols": len(active_symbols),
        "covered_active": len(active_symbols & covered),
        "bj_active": int(bj.height),
    }


def gap_map(curated: Path, calendar_dates: set[date]) -> dict:
    inst = scan_curated(curated, "instruments").collect()
    trading_days = sorted(calendar_dates)
    trading_index = {d: i for i, d in enumerate(trading_days)}
    daily = scan_curated(curated, "daily_bars").filter(pl.col("volume") > 0)
    per_symbol_dates = (
        daily.group_by("symbol").agg(pl.col("trade_date").unique().alias("dates")).collect()
    )
    rows = per_symbol_dates.iter_rows(named=True)
    observed = {row["symbol"]: set(row["dates"]) for row in rows}

    expected_total = 0
    missing_total = 0
    pending_r4 = 0
    unexplained_symbols: list[str] = []
    pending_symbols: list[str] = []
    for row in inst.iter_rows(named=True):
        symbol = row["symbol"]
        if row.get("asset_type") not in ("stock", "cdr"):
            continue
        list_date = row.get("list_date")
        delist_date = row.get("delist_date")
        if list_date is not None and list_date > R3_DAILY_AS_OF:
            continue
        if delist_date is not None and delist_date < R3_HISTORY_START:
            continue
        start = max(list_date or R3_HISTORY_START, R3_HISTORY_START)
        end = min(delist_date or R3_DAILY_AS_OF, R3_DAILY_AS_OF)
        expected = [d for d in trading_days if start <= d <= end]
        have = observed.get(symbol, set())
        missing = [d for d in expected if d not in have]
        expected_total += len(expected)
        missing_total += len(missing)
        if have:
            if missing:
                pending_r4 += len(missing)
                pending_symbols.append(symbol)
        else:
            if expected:
                unexplained_symbols.append(symbol)
    combined_hash = hashlib.sha256(
        json.dumps({"p": sorted(pending_symbols), "u": sorted(unexplained_symbols)}, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "expected_keys": expected_total,
        "missing_keys": missing_total,
        "pending_r4_keys": pending_r4,
        "pending_r4_symbols": len(pending_symbols),
        "unexplained_symbols": len(unexplained_symbols),
        "unexplained_sample": unexplained_symbols[:20],
        "hash": combined_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="R3 daily foundation verifier")
    parser.add_argument("--config", default="config/cnequity.toml")
    parser.add_argument("--history-start", default=R3_HISTORY_START.isoformat())
    parser.add_argument("--as-of", default=R3_DAILY_AS_OF.isoformat())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    config_path = (REPO_ROOT / args.config).resolve()
    with config_path.open("rb") as fh:
        config = tomllib.load(fh)
    root = Path(config["data"]["root"])
    curated = root / "curated"

    before = target_tree_snapshot(root, exclude="meta/asl/r3")
    try:
        structural = structural_checks(curated)
        daily_lf = scan_curated(curated, "daily_bars")
        units = unit_checks(daily_lf)
        cal = scan_curated(curated, "trading_calendar").filter(pl.col("is_trading")).select("trade_date").collect()
        calendar_dates = {row["trade_date"] for row in cal.iter_rows(named=True)}
        if R3_DAILY_AS_OF not in calendar_dates:
            _fail("CALENDAR_AS_OF", "trading calendar does not cover R3_DAILY_AS_OF")
        universe = universe_checks(curated, calendar_dates)
        gaps = gap_map(curated, calendar_dates)
    finally:
        after = target_tree_snapshot(root, exclude="meta/asl/r3")
    if before["digest"] != after["digest"]:
        _fail("VERIFIER_MUTATED", "target-root tree changed during read-only verification")

    latest_good = root / "meta" / "state"
    publish_markers = [p.name for p in sorted(latest_good.rglob("*")) if "latest_good" in p.name or "published" in p.name.lower()] if latest_good.exists() else []
    if publish_markers:
        _fail("PUBLISH_STATE", f"published/latest_good marker present: {publish_markers}")

    verified = (
        structural["daily_duplicate"] == 0
        and structural["date_bound_violations"] == 0
        and structural["value_violations"] == 0
        and gaps["unexplained_symbols"] == 0
    )
    report = {
        "verified": bool(verified),
        "root": str(root),
        "tree_digest": after["digest"],
        "tree_entries": after["entries"],
        "config": {
            "path": str(config_path),
            "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        },
        "structural": structural,
        "units": units,
        "universe": universe,
        "gaps": gaps,
        "publish_markers": publish_markers,
        "latest_good_as_of": "NOT_PUBLISHED",
    }
    print(json.dumps(report, indent=2, default=str))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
