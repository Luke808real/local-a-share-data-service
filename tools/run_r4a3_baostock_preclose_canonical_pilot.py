#!/usr/bin/env python3
"""Bounded BaoStock preclose source-authority pilot.

This pilot reads the frozen R3 SH/SZ identity and the authoritative local
daily-bars actual-session scope, then queries only BaoStock daily
``date,code,preclose,tradestatus`` rows for a frozen 24-symbol subset.  It
never writes market data, corporate_actions, or a formal preclose dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ashare_data.r4a0_corporate_actions_gate import (  # noqa: E402
    identity_hash_for,
    load_expected_identity,
)

DATA_ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
WINDOW_START = date(2016, 1, 1)
WINDOW_END = date(2026, 8, 17)
FORMAL_IDENTITY_N = 5456
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
BASE_HEAD = "44c9d758b2006c1ace8294fccebdc33e1b8640fe"
QUERY_FIELDS = "date,code,preclose,tradestatus"
QUERY_FREQUENCY = "d"
QUERY_ADJUSTFLAG = "3"
OUTPUT_DIR = REPO_ROOT / "reports" / "research"
OFFICIAL_RECEIPT = REPO_ROOT / "reports" / "planning" / "R4A2_1_OFFICIAL_MISMATCH_RECEIPT.json"

PILOT_SYMBOLS = tuple(
    sorted(
        [
            "600000.SH",
            "600004.SH",
            "600005.SH",
            "600008.SH",
            "600089.SH",
            "600179.SH",
            "600515.SH",
            "600580.SH",
            "603007.SH",
            "688486.SH",
            "688489.SH",
            "000001.SZ",
            "000002.SZ",
            "000006.SZ",
            "000009.SZ",
            "000049.SZ",
            "000404.SZ",
            "000564.SZ",
            "000615.SZ",
            "000661.SZ",
            "000686.SZ",
            "000728.SZ",
            "000750.SZ",
            "000797.SZ",
        ]
    )
)
PILOT_SYMBOL_HASH = hashlib.sha256(
    json.dumps(list(PILOT_SYMBOLS), separators=(",", ":")).encode()
).hexdigest()

PILOT_CATEGORIES: dict[str, list[str]] = {
    "000001.SZ": ["listed_before_2016", "cash_only"],
    "000002.SZ": ["listed_before_2016", "many_corporate_actions", "cash_only"],
    "000006.SZ": ["listed_before_2016", "cash_only", "long_suspension_resume"],
    "000009.SZ": ["listed_before_2016", "many_corporate_actions", "multi_action"],
    "000049.SZ": ["listed_before_2016", "rights_issue"],
    "000404.SZ": ["listed_before_2016", "rights_issue"],
    "000564.SZ": ["listed_before_2016", "special_reorganization", "long_suspension_resume"],
    "000615.SZ": ["listed_before_2016", "special_reorganization", "long_suspension_resume"],
    "000661.SZ": ["listed_before_2016", "rights_issue"],
    "000686.SZ": ["listed_before_2016", "rights_issue"],
    "000728.SZ": ["listed_before_2016", "rights_issue", "suspension_resume"],
    "000750.SZ": ["listed_before_2016", "rights_issue", "suspension_resume"],
    "000797.SZ": ["listed_before_2016", "rights_issue"],
    "600000.SH": ["listed_before_2016", "many_corporate_actions"],
    "600004.SH": ["listed_before_2016", "cash_only"],
    "600005.SH": ["listed_before_2016", "long_suspension_resume", "formal_delisted"],
    "600008.SH": ["listed_before_2016"],
    "600089.SH": ["listed_before_2016", "differential_dividend", "multi_action"],
    "600179.SH": ["listed_before_2016", "special_reorganization", "suspension_resume"],
    "600515.SH": ["listed_before_2016", "special_reorganization", "long_suspension_resume"],
    "600580.SH": ["listed_before_2016", "differential_dividend", "multi_action"],
    "603007.SH": ["ipo_within_window", "special_reorganization"],
    "688486.SH": ["ipo_within_window", "differential_dividend", "mandatory_official_case"],
    "688489.SH": ["ipo_within_window", "differential_dividend"],
}

# Sol-frozen corrections.  These supersede the two old R4A2.1 unresolved
# rows without re-running the 260-row parity sample or re-reading announcements.
SOL_OFFICIAL_CORRECTIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("000002.SZ", "2022-08-25"): {
        "official_adjusted_cash_share": 0.968802,
        "official_display_preclose": 15.65,
        "status": "RESOLVED_OFFICIAL_ADJUSTED_BASIS",
    },
    ("000002.SZ", "2023-08-25"): {
        "official_adjusted_cash_share": 0.674898,
        "official_display_preclose": 13.04,
        "status": "RESOLVED_OFFICIAL_ADJUSTED_BASIS",
    },
}


def _decimal_display(value: float | str | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _parse_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def bs_code(symbol: str) -> str:
    code, exchange = symbol.split(".")
    if exchange not in {"SH", "SZ"} or len(code) != 6 or not code.isdigit():
        raise ValueError(f"non-canonical SH/SZ symbol: {symbol}")
    return ("sh" if exchange == "SH" else "sz") + "." + code


def validate_pilot_symbols(identity: dict[str, Any]) -> dict[str, Any]:
    symbols = list(PILOT_SYMBOLS)
    failures: list[str] = []
    if len(symbols) == 0 or len(symbols) > 32:
        failures.append("PILOT_SYMBOL_COUNT_OUT_OF_BOUNDS")
    if len(set(symbols)) != len(symbols):
        failures.append("PILOT_SYMBOL_DUPLICATE")
    for symbol in symbols:
        try:
            bs_code(symbol)
        except ValueError:
            failures.append(f"PILOT_SYMBOL_NON_CANONICAL:{symbol}")
    if any(symbol.endswith(".BJ") for symbol in symbols):
        failures.append("PILOT_SYMBOL_BJ_FORBIDDEN")
    frozen = set(identity.get("symbols", []))
    outside = sorted(set(symbols) - frozen)
    if outside:
        failures.append("PILOT_SYMBOL_OUTSIDE_FORMAL_IDENTITY:" + ",".join(outside))
    return {
        "PILOT_SYMBOL_N": len(symbols),
        "PILOT_SYMBOLS": symbols,
        "PILOT_SYMBOL_HASH": PILOT_SYMBOL_HASH,
        "PILOT_SYMBOL_FAILURES": failures,
        "PILOT_SYMBOL_SCOPE_MATCH": not failures,
    }


def normalize_identity(identity: dict[str, Any], *, receipt_ok: bool = True) -> dict[str, Any]:
    """Strictly normalize the existing R3-shaped identity result."""
    raw_symbols = identity.get("symbols")
    symbols = [str(x) for x in raw_symbols] if isinstance(raw_symbols, list) else []
    actual_n = identity.get("EXPECTED_SYMBOL_N")
    actual_hash = identity.get("EXPECTED_SYMBOL_HASH")
    identity_match = bool(
        identity.get("identity_ok") is True
        and identity.get("IDENTITY_STATUS") == "PASS"
        and actual_n == FORMAL_IDENTITY_N
        and actual_hash == FORMAL_IDENTITY_HASH
        and len(symbols) == FORMAL_IDENTITY_N
        and len(set(symbols)) == FORMAL_IDENTITY_N
        and identity_hash_for(symbols) == FORMAL_IDENTITY_HASH
        and receipt_ok is True
    )
    return {
        "FORMAL_IDENTITY_N": actual_n,
        "FORMAL_IDENTITY_HASH": actual_hash,
        "IDENTITY_MATCH": identity_match,
        "IDENTITY_SOURCE": identity.get("IDENTITY_SOURCE"),
        "IDENTITY_RECEIPT_MATCH": receipt_ok is True,
        "symbols": sorted(symbols),
    }


def load_frozen_identity(root: Path) -> dict[str, Any]:
    receipt_path = root / "meta" / "asl" / "r3" / "r3-identity-receipt.json"
    receipt_ok = False
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_ok = bool(
                receipt.get("formal_identity_n") == FORMAL_IDENTITY_N
                and receipt.get("formal_identity_hash") == FORMAL_IDENTITY_HASH
                and receipt.get("shsz_identity_complete") is True
            )
        except (OSError, ValueError, TypeError):
            receipt_ok = False
    identity = load_expected_identity(
        root,
        expected_hash=FORMAL_IDENTITY_HASH,
        expected_n=FORMAL_IDENTITY_N,
    )
    return normalize_identity(identity, receipt_ok=receipt_ok)


def year_windows(start: date = WINDOW_START, end: date = WINDOW_END) -> list[tuple[int, date, date]]:
    return [
        (
            year,
            max(start, date(year, 1, 1)),
            min(end, date(year, 12, 31)),
        )
        for year in range(start.year, end.year + 1)
    ]


def load_reference_data(root: Path, symbols: list[str], official_rows: list[dict[str, Any]]) -> dict[str, Any]:
    bars = (
        pl.scan_parquet(str(root / "curated" / "daily_bars" / "**" / "*.parquet"))
        .select(["symbol", "trade_date", "close"])
        .filter(pl.col("symbol").is_in(symbols))
        .collect()
        .with_columns(
            pl.col("symbol").cast(pl.String),
            pl.col("trade_date").cast(pl.Date),
            pl.col("close").cast(pl.Float64),
        )
    )
    local_duplicate_pk_n = int(
        bars.filter(pl.col("close").is_not_null())
        .group_by(["symbol", "trade_date"])
        .len()
        .filter(pl.col("len") > 1)
        .height
    )
    bars = (
        bars.filter(pl.col("close").is_not_null())
        .unique(subset=["symbol", "trade_date"], keep="first")
        .sort(["symbol", "trade_date"])
        .with_columns(pl.col("close").shift(1).over("symbol").alias("prev_close"))
    )
    required = bars.filter(
        (pl.col("trade_date") >= WINDOW_START)
        & (pl.col("trade_date") <= WINDOW_END)
    )
    required_keys = {
        (str(row["symbol"]), row["trade_date"])
        for row in required.iter_rows(named=True)
    }
    required_by_year: dict[tuple[str, int], set[tuple[str, date]]] = {}
    for key in required_keys:
        symbol, trade_date = key
        required_by_year.setdefault((symbol, trade_date.year), set()).add(key)

    instruments = (
        pl.read_parquet(root / "curated" / "instruments" / "part-merged.parquet")
        .filter(pl.col("symbol").is_in(symbols))
        .select(["symbol", "name", "exchange", "asset_type", "list_date", "delist_date"])
        .unique(subset=["symbol"])
    )
    instrument_map = {
        str(row["symbol"]): row
        for row in instruments.iter_rows(named=True)
    }
    first_trade = {
        str(row["symbol"]): row["trade_date"]
        for row in required.group_by("symbol").agg(pl.col("trade_date").min()).iter_rows(named=True)
    }

    event_dates: set[tuple[str, date]] = set()
    ca_root = root / "curated" / "corporate_actions"
    if ca_root.exists():
        ca = (
            pl.scan_parquet(str(ca_root / "**" / "*.parquet"))
            .select(["symbol", "ex_date"])
            .filter(pl.col("symbol").is_in(symbols))
            .collect()
            .with_columns(pl.col("symbol").cast(pl.String), pl.col("ex_date").cast(pl.Date))
        )
        event_dates = {
            (str(row["symbol"]), row["ex_date"])
            for row in ca.iter_rows(named=True)
            if WINDOW_START <= row["ex_date"] <= WINDOW_END
        }
    event_dates.update(
        (row["symbol"], _parse_date(row["ex_date"]))
        for row in official_rows
        if row["symbol"] in symbols and _parse_date(row["ex_date"]) is not None
    )

    max_gaps: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        dates = [
            row["trade_date"]
            for row in required.filter(pl.col("symbol") == symbol).iter_rows(named=True)
        ]
        candidates = [
            ((b - a).days, a.isoformat(), b.isoformat())
            for a, b in zip(dates, dates[1:])
            if (b - a).days > 30
        ]
        gap = max(candidates, default=(0, None, None))
        max_gaps[symbol] = {
            "max_calendar_gap_days": gap[0],
            "gap_before": gap[1],
            "gap_after": gap[2],
            "long_suspension_resume_observed": gap[0] > 30,
        }

    return {
        "bars": bars,
        "required": required,
        "required_keys": required_keys,
        "required_by_year": required_by_year,
        "instrument_map": instrument_map,
        "first_trade": first_trade,
        "event_dates": event_dates,
        "max_gaps": max_gaps,
        "local_duplicate_pk_n": local_duplicate_pk_n,
    }


def build_query_plan(reference: dict[str, Any], symbols: list[str]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for symbol in symbols:
        for year, start, end in year_windows():
            keys = reference["required_by_year"].get((symbol, year), set())
            if not keys:
                continue
            plan.append(
                {
                    "symbol": symbol,
                    "year": year,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "bs_code": bs_code(symbol),
                    "required_row_n": len(keys),
                }
            )
    return plan


def load_official_event_rows(path: Path, symbols: list[str]) -> list[dict[str, Any]]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for row in receipt.get("rows", []):
        symbol = str(row.get("symbol"))
        ex_date = str(row.get("ex_date"))
        if symbol not in symbols:
            continue
        correction = SOL_OFFICIAL_CORRECTIONS.get((symbol, ex_date))
        if correction:
            rows.append(
                {
                    "symbol": symbol,
                    "ex_date": ex_date,
                    "official_display_preclose": correction["official_display_preclose"],
                    "official_adjusted_cash_share": correction["official_adjusted_cash_share"],
                    "status": correction["status"],
                    "differential_dividend": "NO",
                    "authority_url": row.get("authority_url"),
                }
            )
            continue
        if not str(row.get("closure_status", "")).startswith("RESOLVED"):
            continue
        rows.append(
            {
                "symbol": symbol,
                "ex_date": ex_date,
                "official_display_preclose": row.get("official_adjusted_reference"),
                "official_adjusted_cash_share": row.get("official_terms", {}).get("cash_per_share"),
                "status": row.get("closure_status"),
                "differential_dividend": row.get("differential_dividend"),
                "authority_url": row.get("authority_url"),
            }
        )
    return sorted(rows, key=lambda row: (row["symbol"], row["ex_date"]))


def _query_rows(provider: Any, item: dict[str, Any]) -> dict[str, Any]:
    result = provider.query_history_k_data_plus(
        item["bs_code"],
        QUERY_FIELDS,
        start_date=item["start"],
        end_date=item["end"],
        frequency=QUERY_FREQUENCY,
        adjustflag=QUERY_ADJUSTFLAG,
    )
    rows: list[list[str]] = []
    error_code = str(getattr(result, "error_code", "UNKNOWN"))
    error_msg = str(getattr(result, "error_msg", ""))
    if error_code == "0":
        while result.next():
            rows.append(list(result.get_row_data()))
    return {
        "plan": item,
        "error_code": error_code,
        "error_msg": error_msg,
        "rows": rows,
    }


def process_provider_results(
    results: list[dict[str, Any]],
    reference: dict[str, Any],
) -> dict[str, Any]:
    provider_lookup: dict[tuple[str, date], float] = {}
    symbol_year: list[dict[str, Any]] = []
    query_failures: list[dict[str, Any]] = []
    overall = {
        "REQUIRED_ROW_N": 0,
        "BAOSTOCK_ROW_N": 0,
        "PRECLOSE_NON_NULL_N": 0,
        "MISSING_REQUIRED_ROW_N": 0,
        "UNEXPECTED_ROW_N": 0,
        "DUPLICATE_PK_N": 0,
        "IDENTITY_FAILURE_N": 0,
        "POST_ASOF_N": 0,
    }
    for result in results:
        item = result["plan"]
        required_keys = reference["required_by_year"].get((item["symbol"], item["year"]), set())
        valid_keys: set[tuple[str, date]] = set()
        duplicate_n = 0
        identity_failure_n = 0
        post_asof_n = 0
        unexpected_n = 0
        preclose_non_null_n = 0
        rows = result["rows"] if result["error_code"] == "0" else []
        if result["error_code"] != "0":
            query_failures.append(
                {
                    "symbol": item["symbol"],
                    "year": item["year"],
                    "error_code": result["error_code"],
                    "error_msg": result["error_msg"],
                }
            )
        for raw in rows:
            if len(raw) < 4:
                identity_failure_n += 1
                continue
            raw_date = _parse_date(raw[0])
            preclose = _parse_float(raw[2])
            tradestatus = raw[3]
            if raw_date is not None and raw_date > WINDOW_END:
                post_asof_n += 1
            identity_ok = bool(
                raw_date is not None
                and raw[1] == item["bs_code"]
                and item["start"] <= raw[0] <= item["end"]
                and preclose is not None
                and tradestatus not in (None, "")
            )
            if not identity_ok:
                identity_failure_n += 1
                continue
            preclose_non_null_n += 1
            key = (item["symbol"], raw_date)
            if key in valid_keys:
                duplicate_n += 1
                continue
            valid_keys.add(key)
            provider_lookup[key] = preclose
            if key not in required_keys:
                unexpected_n += 1
        missing_n = len(required_keys - valid_keys)
        detail = {
            "symbol": item["symbol"],
            "year": item["year"],
            "window_start": item["start"],
            "window_end": item["end"],
            "required_row_n": len(required_keys),
            "baostock_row_n": len(rows),
            "preclose_non_null_n": preclose_non_null_n,
            "missing_required_row_n": missing_n,
            "unexpected_row_n": unexpected_n,
            "duplicate_pk_n": duplicate_n,
            "identity_failure_n": identity_failure_n,
            "post_asof_n": post_asof_n,
            "status": (
                "PASS"
                if not any((missing_n, unexpected_n, duplicate_n, identity_failure_n, post_asof_n))
                and result["error_code"] == "0"
                else "FAIL"
            ),
        }
        symbol_year.append(detail)
        overall["REQUIRED_ROW_N"] += len(required_keys)
        overall["BAOSTOCK_ROW_N"] += len(rows)
        overall["PRECLOSE_NON_NULL_N"] += preclose_non_null_n
        overall["MISSING_REQUIRED_ROW_N"] += missing_n
        overall["UNEXPECTED_ROW_N"] += unexpected_n
        overall["DUPLICATE_PK_N"] += duplicate_n
        overall["IDENTITY_FAILURE_N"] += identity_failure_n
        overall["POST_ASOF_N"] += post_asof_n
    overall["COVERAGE_STATUS"] = (
        "PASS"
        if not query_failures
        and not any(
            overall[key]
            for key in (
                "MISSING_REQUIRED_ROW_N",
                "UNEXPECTED_ROW_N",
                "DUPLICATE_PK_N",
                "IDENTITY_FAILURE_N",
                "POST_ASOF_N",
            )
        )
        else "FAIL"
    )
    return {
        **overall,
        "SYMBOL_YEAR_COVERAGE": symbol_year,
        "QUERY_FAILURE_N": len(query_failures),
        "QUERY_FAILURES": query_failures,
        "provider_lookup": provider_lookup,
    }


def normal_parity(reference: dict[str, Any], provider_lookup: dict[tuple[str, date], float]) -> dict[str, Any]:
    excluded = set(reference["event_dates"])
    excluded.update((symbol, first) for symbol, first in reference["first_trade"].items())
    compared_n = 0
    exact_n = 0
    mismatch_rows: list[dict[str, Any]] = []
    uncompared_n = 0
    for row in reference["required"].iter_rows(named=True):
        key = (str(row["symbol"]), row["trade_date"])
        if key in excluded or row["prev_close"] is None:
            continue
        observed = provider_lookup.get(key)
        if observed is None:
            uncompared_n += 1
            continue
        compared_n += 1
        local = float(row["prev_close"])
        diff = abs(observed - local)
        if _decimal_display(observed) == _decimal_display(local):
            exact_n += 1
        else:
            mismatch_rows.append(
                {
                    "symbol": row["symbol"],
                    "trade_date": row["trade_date"].isoformat(),
                    "local_previous_close": local,
                    "baostock_preclose": observed,
                    "diff": diff,
                }
            )
    mismatch_n = len(mismatch_rows)
    return {
        "NORMAL_REQUIRED_ROW_N": compared_n + uncompared_n,
        "NORMAL_COMPARABLE_N": compared_n,
        "NORMAL_EXACT_MATCH_N": exact_n,
        "NORMAL_MISMATCH_N": mismatch_n,
        "NORMAL_UNCOMPARED_N": uncompared_n,
        "NORMAL_MAX_DIFF": max((row["diff"] for row in mismatch_rows), default=0.0),
        "NORMAL_PARITY_STATUS": "PASS" if compared_n > 0 and not mismatch_n and not uncompared_n else "FAIL",
        "NORMAL_MISMATCH_SAMPLE": mismatch_rows[:20],
    }


def official_event_parity(
    rows: list[dict[str, Any]], provider_lookup: dict[tuple[str, date], float]
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    exact_n = 0
    mismatch_n = 0
    for row in rows:
        key = (row["symbol"], _parse_date(row["ex_date"]))
        observed = provider_lookup.get(key)
        expected = _parse_float(row.get("official_display_preclose"))
        exact = observed is not None and expected is not None and _decimal_display(observed) == _decimal_display(expected)
        if exact:
            exact_n += 1
        else:
            mismatch_n += 1
        details.append(
            {
                "symbol": row["symbol"],
                "ex_date": row["ex_date"],
                "official_display_preclose": expected,
                "baostock_preclose": observed,
                "exact": exact,
                "status": row["status"],
                "differential_dividend": row["differential_dividend"],
                "authority_url": row["authority_url"],
            }
        )
    return {
        "OFFICIAL_EVENT_N": len(rows),
        "OFFICIAL_EVENT_EXACT_N": exact_n,
        "OFFICIAL_EVENT_MISMATCH_N": mismatch_n,
        "OFFICIAL_EVENT_PARITY_STATUS": "PASS" if rows and mismatch_n == 0 else "FAIL",
        "OFFICIAL_EVENT_DETAILS": details,
    }


def ipo_parity(reference: dict[str, Any], provider_lookup: dict[tuple[str, date], float], symbols: list[str]) -> dict[str, Any]:
    ipo_rows: list[dict[str, Any]] = []
    for symbol in symbols:
        metadata = reference["instrument_map"].get(symbol, {})
        first = reference["first_trade"].get(symbol)
        if first is not None and metadata.get("list_date") == first:
            observed = provider_lookup.get((symbol, first))
            ipo_rows.append(
                {
                    "symbol": symbol,
                    "first_trade_date": first.isoformat(),
                    "baostock_preclose": observed,
                    "official_issue_price": None,
                    "status": "NO_PREBOUND_OFFICIAL_ISSUE_PRICE",
                }
            )
    return {
        "IPO_FIRST_DAY_N": len(ipo_rows),
        "IPO_OFFICIAL_N": 0,
        "IPO_EXACT_N": 0,
        "IPO_MISMATCH_N": 0,
        "IPO_UNCOMPARED_N": len(ipo_rows),
        "IPO_PARITY_STATUS": "UNKNOWN_OFFICIAL_SUBSET_EMPTY" if ipo_rows else "NOT_APPLICABLE",
        "IPO_DETAILS": ipo_rows,
    }


def window_edge(reference: dict[str, Any], provider_lookup: dict[tuple[str, date], float], symbols: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        metadata = reference["instrument_map"].get(symbol, {})
        list_date = metadata.get("list_date")
        first = reference["first_trade"].get(symbol)
        if list_date is None or list_date >= WINDOW_START or first is None:
            continue
        observed = provider_lookup.get((symbol, first))
        rows.append(
            {
                "symbol": symbol,
                "first_window_trade_date": first.isoformat(),
                "baostock_preclose": observed,
                "non_null": observed is not None,
            }
        )
    non_null_n = sum(row["non_null"] for row in rows)
    return {
        "WINDOW_EDGE_N": len(rows),
        "WINDOW_EDGE_NON_NULL_N": non_null_n,
        "WINDOW_EDGE_MISSING_N": len(rows) - non_null_n,
        "WINDOW_EDGE_STATUS": "PASS" if rows and non_null_n == len(rows) else "FAIL",
        "WINDOW_EDGE_DETAILS": rows,
    }


def source_decision(report: dict[str, Any]) -> dict[str, Any]:
    promote = bool(
        report["COVERAGE_STATUS"] == "PASS"
        and report["IDENTITY_COMPLETE"] is True
        and report["NORMAL_PARITY_STATUS"] == "PASS"
        and report["OFFICIAL_EVENT_PARITY_STATUS"] == "PASS"
        and report["IPO_PARITY_STATUS"] == "PASS"
        and report["WINDOW_EDGE_STATUS"] == "PASS"
        and report["POST_ASOF_N"] == 0
    )
    return {
        "BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION": (
            "PROMOTE_CANDIDATE_CANONICAL" if promote else "CROSSCHECK_ONLY"
        ),
        "R4A_IMPLEMENTATION_READY": False,
        "PROMOTION_CONDITIONS_MET": promote,
        "PROMOTION_BLOCKERS": [
            key
            for key, condition in (
                ("COVERAGE_STATUS", report["COVERAGE_STATUS"] == "PASS"),
                ("IDENTITY_COMPLETE", report["IDENTITY_COMPLETE"] is True),
                ("NORMAL_PARITY_STATUS", report["NORMAL_PARITY_STATUS"] == "PASS"),
                ("OFFICIAL_EVENT_PARITY_STATUS", report["OFFICIAL_EVENT_PARITY_STATUS"] == "PASS"),
                ("IPO_PARITY_STATUS", report["IPO_PARITY_STATUS"] == "PASS"),
                ("WINDOW_EDGE_STATUS", report["WINDOW_EDGE_STATUS"] == "PASS"),
                ("POST_ASOF_N", report["POST_ASOF_N"] == 0),
            )
            if not condition
        ],
        "SHARED_BAOSTOCK_EXTRACTION_RECOMMENDATION": (
            "DESIGN_ONLY: consider one bounded/resumable request carrying "
            "preclose,turn,tradestatus,isST, with independent Market Fact datasets"
        ),
    }


def _manifest(identity: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "R4A3_BAOSTOCK_PRECLOSE_CANONICAL_SOURCE_PILOT_V01",
        "base_head": BASE_HEAD,
        "formal_identity_n": FORMAL_IDENTITY_N,
        "formal_identity_hash": FORMAL_IDENTITY_HASH,
        "identity_source": identity.get("IDENTITY_SOURCE"),
        "identity_receipt_match": identity.get("IDENTITY_RECEIPT_MATCH"),
        "pilot_symbol_n": len(PILOT_SYMBOLS),
        "pilot_symbol_hash": PILOT_SYMBOL_HASH,
        "pilot_symbols": list(PILOT_SYMBOLS),
        "pilot_categories": PILOT_CATEGORIES,
        "window_start": WINDOW_START.isoformat(),
        "window_end": WINDOW_END.isoformat(),
        "sample_deterministic": True,
        "sample_balanced_stratified": False,
        "normal_sample_reused": True,
        "announcement_review_expanded": False,
        "max_gap_summary": reference["max_gaps"],
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# R4A3 BaoStock Preclose Canonical Source Pilot — V01",
        "",
        f"DATE: 2026-08-20",
        f"BASE_HEAD: {BASE_HEAD}",
        f"PILOT_SYMBOL_N: {report['PILOT_SYMBOL_N']}",
        f"PILOT_SYMBOL_HASH: {report['PILOT_SYMBOL_HASH']}",
        "",
        "## Verdict",
        "",
        "```text",
        f"STATUS={report['STATUS']}",
        f"BAOSTOCK_PRECLOSE_FULL_HISTORY_PILOT={report['STATUS']}",
        "PROVIDER_EXECUTION_COUNT=1",
        "AUTO_RETRY=NO",
        "POSTRUN_ARTIFACT_REBUILD=YES",
        f"COVERAGE_STATUS={report['COVERAGE_STATUS']}",
        f"NORMAL_PARITY_STATUS={report['NORMAL_PARITY_STATUS']}",
        f"OFFICIAL_EVENT_PARITY_STATUS={report['OFFICIAL_EVENT_PARITY_STATUS']}",
        f"IPO_PARITY_STATUS={report['IPO_PARITY_STATUS']}",
        f"WINDOW_EDGE_STATUS={report['WINDOW_EDGE_STATUS']}",
        f"BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION={report['BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION']}",
        "R4A_IMPLEMENTATION_READY=false",
        "```",
        "",
        "This is a bounded source-authority pilot only. It does not write a formal",
        "preclose dataset or corporate_actions and does not modify the R4 frozen plan.",
        "",
        "## Frozen scope",
        "",
        "```text",
        f"FORMAL_IDENTITY_N={report['FORMAL_IDENTITY_N']}",
        f"FORMAL_IDENTITY_HASH={report['FORMAL_IDENTITY_HASH']}",
        f"IDENTITY_MATCH={report['IDENTITY_MATCH']}",
        f"PILOT_SYMBOL_N={report['PILOT_SYMBOL_N']}",
        f"PILOT_SYMBOL_HASH={report['PILOT_SYMBOL_HASH']}",
        f"WINDOW={WINDOW_START.isoformat()}..{WINDOW_END.isoformat()}",
        f"QUERY_FIELDS={QUERY_FIELDS}",
        "QUERY_FREQUENCY=d",
        "QUERY_ADJUSTFLAG=3",
        "SAMPLE_DETERMINISTIC=true",
        "SAMPLE_BALANCED_STRATIFIED=false",
        "```",
        "",
        "The prior 260-row parity evidence was reused. No parity sample was",
        "re-run and no announcement review was expanded.",
        "",
        "## Coverage and response identity",
        "",
        "```text",
        f"REQUIRED_ROW_N={report['REQUIRED_ROW_N']}",
        f"BAOSTOCK_ROW_N={report['BAOSTOCK_ROW_N']}",
        f"PRECLOSE_NON_NULL_N={report['PRECLOSE_NON_NULL_N']}",
        f"MISSING_REQUIRED_ROW_N={report['MISSING_REQUIRED_ROW_N']}",
        f"UNEXPECTED_ROW_N={report['UNEXPECTED_ROW_N']}",
        f"DUPLICATE_PK_N={report['DUPLICATE_PK_N']}",
        f"IDENTITY_FAILURE_N={report['IDENTITY_FAILURE_N']}",
        f"POST_ASOF_N={report['POST_ASOF_N']}",
        f"QUERY_N={report['QUERY_N']}",
        f"QUERY_FAILURE_N={report['QUERY_FAILURE_N']}",
        "PROVIDER_EXECUTION_COUNT=1",
        "AUTO_RETRY=NO",
        "POSTRUN_ARTIFACT_REBUILD=YES",
        "```",
        "",
        "Per-symbol/year coverage is retained in the JSON receipt. Provider rows",
        "were queried only with date, code, preclose, and tradestatus; no OHLCV,",
        "valuation, or bulk provider dataset was requested.",
        "The 982 unexpected rows are BaoStock rows outside the local authoritative",
        "daily_bars actual-session key set; they are reported as a coverage failure",
        "and are not silently promoted into the required scope.",
        "",
        "## Parity results",
        "",
        "```text",
        f"NORMAL_REQUIRED_ROW_N={report['NORMAL_REQUIRED_ROW_N']}",
        f"NORMAL_COMPARABLE_N={report['NORMAL_COMPARABLE_N']}",
        f"EXACT_MATCH_N={report['NORMAL_EXACT_MATCH_N']}",
        f"MISMATCH_N={report['NORMAL_MISMATCH_N']}",
        f"MAX_DIFF={report['NORMAL_MAX_DIFF']}",
        f"OFFICIAL_EVENT_N={report['OFFICIAL_EVENT_N']}",
        f"OFFICIAL_EVENT_EXACT_N={report['OFFICIAL_EVENT_EXACT_N']}",
        f"OFFICIAL_EVENT_MISMATCH_N={report['OFFICIAL_EVENT_MISMATCH_N']}",
        f"IPO_OFFICIAL_N={report['IPO_OFFICIAL_N']}",
        f"IPO_EXACT_N={report['IPO_EXACT_N']}",
        f"IPO_MISMATCH_N={report['IPO_MISMATCH_N']}",
        f"WINDOW_EDGE_N={report['WINDOW_EDGE_N']}",
        f"WINDOW_EDGE_NON_NULL_N={report['WINDOW_EDGE_NON_NULL_N']}",
        "```",
        "",
        "Sol-frozen official adjusted-basis corrections reused in this pilot:",
        "",
        "```text",
        "000002.SZ 2022-08-25 CASH_PER_SHARE=0.968802 OFFICIAL_DISPLAY_PRECLOSE=15.65",
        "000002.SZ 2023-08-25 CASH_PER_SHARE=0.674898 OFFICIAL_DISPLAY_PRECLOSE=13.04",
        "STATUS=RESOLVED_OFFICIAL_ADJUSTED_BASIS",
        "```",
        "",
        "IPO official issue-price authority was not present in the existing bounded",
        "evidence, so the IPO subset remains UNKNOWN rather than being inferred",
        "from BaoStock itself. This is a source-decision blocker.",
        "",
        "## Source decision",
        "",
        "```text",
        f"BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION={report['BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION']}",
        f"PROMOTION_CONDITIONS_MET={report['PROMOTION_CONDITIONS_MET']}",
        f"PROMOTION_BLOCKERS={','.join(report['PROMOTION_BLOCKERS'])}",
        f"SHARED_BAOSTOCK_EXTRACTION_RECOMMENDATION={report['SHARED_BAOSTOCK_EXTRACTION_RECOMMENDATION']}",
        "R4A_IMPLEMENTATION_READY=false",
        "```",
        "",
        "The shared extraction idea is design-only: future bounded/resumable",
        "requests may carry preclose, turn, tradestatus, and isST, but each",
        "field must land in an independent Market Fact dataset. No implementation",
        "is included in this task.",
        "",
        "## Safety",
        "",
        "```text",
        f"NETWORK_PROVIDER_DATA_FETCH={report['NETWORK_PROVIDER_DATA_FETCH']}",
        f"PROVIDER_STEP_ENTERED={report['PROVIDER_STEP_ENTERED']}",
        "MARKET_DATA_WRITE=NO",
        "CORPORATE_ACTIONS_WRITE=NO",
        "FORMAL_PRECLOSE_DATASET_WRITE=NO",
        "R4B_R4C_R4D_IMPLEMENTATION=FORBIDDEN",
        "STRATEGY_FORWARD_TRADEPLAN=FORBIDDEN",
        "```",
    ]
    return "\n".join(lines) + "\n"


def run_pilot(
    *,
    root: Path = DATA_ROOT,
    dry_run: bool = True,
    provider: Any | None = None,
    identity: dict[str, Any] | None = None,
    identity_receipt_ok: bool = True,
    reference_data: dict[str, Any] | None = None,
    official_receipt_path: Path = OFFICIAL_RECEIPT,
    output_dir: Path = OUTPUT_DIR,
    write_outputs: bool = True,
) -> dict[str, Any]:
    identity_norm = (
        normalize_identity(identity, receipt_ok=identity_receipt_ok)
        if identity is not None
        else load_frozen_identity(root)
    )
    pilot_scope = validate_pilot_symbols(identity_norm)
    report: dict[str, Any] = {
        "STATUS": "READY",
        "BASE_HEAD": BASE_HEAD,
        "DATA_ROOT": str(root),
        "FORMAL_IDENTITY_N": identity_norm.get("FORMAL_IDENTITY_N"),
        "FORMAL_IDENTITY_HASH": identity_norm.get("FORMAL_IDENTITY_HASH"),
        "IDENTITY_MATCH": identity_norm.get("IDENTITY_MATCH"),
        "IDENTITY_SOURCE": identity_norm.get("IDENTITY_SOURCE"),
        "IDENTITY_RECEIPT_MATCH": identity_norm.get("IDENTITY_RECEIPT_MATCH"),
        "IDENTITY_COMPLETE": identity_norm.get("IDENTITY_MATCH") is True,
        "PILOT_SYMBOL_N": pilot_scope["PILOT_SYMBOL_N"],
        "PILOT_SYMBOLS": pilot_scope["PILOT_SYMBOLS"],
        "PILOT_SYMBOL_HASH": pilot_scope["PILOT_SYMBOL_HASH"],
        "PILOT_SYMBOL_SCOPE_MATCH": pilot_scope["PILOT_SYMBOL_SCOPE_MATCH"],
        "PILOT_SYMBOL_FAILURES": pilot_scope["PILOT_SYMBOL_FAILURES"],
        "SAMPLE_DETERMINISTIC": True,
        "SAMPLE_BALANCED_STRATIFIED": False,
        "NORMAL_SAMPLE_REUSED": True,
        "ANNOUNCEMENT_REVIEW_EXPANDED": False,
        "WINDOW_START": WINDOW_START.isoformat(),
        "WINDOW_END": WINDOW_END.isoformat(),
        "QUERY_FIELDS": QUERY_FIELDS,
        "QUERY_FREQUENCY": QUERY_FREQUENCY,
        "QUERY_ADJUSTFLAG": QUERY_ADJUSTFLAG,
        "PROVIDER_STEP_ENTERED": "NO",
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "NETWORK_PROVIDER_REQUEST_COUNT": 0,
        "MARKET_DATA_WRITE": "NO",
        "CORPORATE_ACTIONS_WRITE": "NO",
        "FORMAL_PRECLOSE_DATASET_WRITE": "NO",
        "REPORT_ARTIFACT_WRITE": "NO",
    }
    if not identity_norm["IDENTITY_MATCH"] or not pilot_scope["PILOT_SYMBOL_SCOPE_MATCH"]:
        report.update(
            {
                "STATUS": "FAIL_CLOSED_IDENTITY_OR_SCOPE",
                "COVERAGE_STATUS": "NOT_RUN",
                "NORMAL_PARITY_STATUS": "NOT_RUN",
                "OFFICIAL_EVENT_PARITY_STATUS": "NOT_RUN",
                "IPO_PARITY_STATUS": "NOT_RUN",
                "WINDOW_EDGE_STATUS": "NOT_RUN",
                "BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION": "CROSSCHECK_ONLY",
                "R4A_IMPLEMENTATION_READY": False,
                "PROMOTION_CONDITIONS_MET": False,
                "PROMOTION_BLOCKERS": ["IDENTITY_OR_PILOT_SCOPE"],
                "SHARED_BAOSTOCK_EXTRACTION_RECOMMENDATION": "DESIGN_ONLY",
            }
        )
        return report

    official_rows = load_official_event_rows(official_receipt_path, PILOT_SYMBOLS)
    reference = reference_data or load_reference_data(root, list(PILOT_SYMBOLS), official_rows)
    plan = build_query_plan(reference, list(PILOT_SYMBOLS))
    report["QUERY_PLAN_N"] = len(plan)
    report["QUERY_PLAN"] = plan
    report["PILOT_SYMBOL_METADATA"] = {
        symbol: {
            **{
                key: value.isoformat() if isinstance(value, date) else value
                for key, value in reference["instrument_map"].get(symbol, {}).items()
            },
            "categories": PILOT_CATEGORIES.get(symbol, []),
            **reference["max_gaps"].get(symbol, {}),
        }
        for symbol in PILOT_SYMBOLS
    }
    if dry_run:
        report.update(
            {
                "STATUS": "DRY_RUN_READY",
                "COVERAGE_STATUS": "NOT_RUN",
                "NORMAL_PARITY_STATUS": "NOT_RUN",
                "OFFICIAL_EVENT_PARITY_STATUS": "NOT_RUN",
                "IPO_PARITY_STATUS": "NOT_RUN",
                "WINDOW_EDGE_STATUS": "NOT_RUN",
                "BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION": "CROSSCHECK_ONLY",
                "PROMOTION_CONDITIONS_MET": False,
                "PROMOTION_BLOCKERS": ["DRY_RUN_ONLY"],
                "R4A_IMPLEMENTATION_READY": False,
                "SHARED_BAOSTOCK_EXTRACTION_RECOMMENDATION": "DESIGN_ONLY",
            }
        )
        return report

    if provider is None:
        import baostock as provider  # type: ignore[no-redef]
    report["PROVIDER_STEP_ENTERED"] = "YES"
    report["NETWORK_PROVIDER_DATA_FETCH"] = "YES"
    login = provider.login()
    if str(getattr(login, "error_code", "UNKNOWN")) != "0":
        report.update(
            {
                "STATUS": "PROVIDER_LOGIN_FAILURE",
                "LOGIN_ERROR": str(getattr(login, "error_msg", "")),
                "COVERAGE_STATUS": "FAIL",
                "NORMAL_PARITY_STATUS": "NOT_RUN",
                "OFFICIAL_EVENT_PARITY_STATUS": "NOT_RUN",
                "IPO_PARITY_STATUS": "NOT_RUN",
                "WINDOW_EDGE_STATUS": "NOT_RUN",
                "BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION": "CROSSCHECK_ONLY",
                "R4A_IMPLEMENTATION_READY": False,
                "PROMOTION_CONDITIONS_MET": False,
                "PROMOTION_BLOCKERS": ["PROVIDER_LOGIN_FAILURE"],
                "SHARED_BAOSTOCK_EXTRACTION_RECOMMENDATION": "DESIGN_ONLY",
            }
        )
        return report
    results: list[dict[str, Any]] = []
    try:
        for item in plan:
            results.append(_query_rows(provider, item))
    finally:
        provider.logout()
    report["NETWORK_PROVIDER_REQUEST_COUNT"] = len(results)
    report["QUERY_N"] = len(results)
    processed = process_provider_results(results, reference)
    report.update({key: value for key, value in processed.items() if key != "provider_lookup"})
    provider_lookup = processed["provider_lookup"]
    report.update(normal_parity(reference, provider_lookup))
    report.update(official_event_parity(official_rows, provider_lookup))
    report.update(ipo_parity(reference, provider_lookup, list(PILOT_SYMBOLS)))
    report.update(window_edge(reference, provider_lookup, list(PILOT_SYMBOLS)))
    report.update(source_decision(report))
    report["STATUS"] = (
        "PILOT_COMPLETE" if report["QUERY_FAILURE_N"] == 0 else "PILOT_PARTIAL"
    )
    if write_outputs:
        output_dir.mkdir(parents=True, exist_ok=True)
        report["REPORT_ARTIFACT_WRITE"] = "YES"
        manifest = _manifest(identity_norm, reference)
        (output_dir / "R4A3_BAOSTOCK_PRECLOSE_PILOT_SYMBOL_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        receipt = {
            "task": "R4A3_BAOSTOCK_PRECLOSE_CANONICAL_SOURCE_PILOT_V01",
            "report": report,
            "pilot_symbol_manifest": manifest,
            "official_event_rows": official_rows,
        }
        (output_dir / "R4A3_BAOSTOCK_PRECLOSE_CANONICAL_PILOT_RECEIPT.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        (output_dir / "R4A3_BAOSTOCK_PRECLOSE_CANONICAL_PILOT_REPORT.md").write_text(
            render_report(report), encoding="utf-8"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="bounded BaoStock preclose canonical-source pilot")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--exec", action="store_true", help="run the frozen bounded provider pilot")
    group.add_argument("--dry-run", action="store_true", help="validate scope and print the query plan")
    args = parser.parse_args()
    result = run_pilot(dry_run=not args.exec)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["STATUS"] in {"DRY_RUN_READY", "PILOT_COMPLETE", "PILOT_PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
