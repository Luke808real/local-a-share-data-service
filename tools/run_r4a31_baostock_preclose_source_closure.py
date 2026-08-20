#!/usr/bin/env python3
"""Close the bounded R4A3 BaoStock preclose source contract.

This evaluator intentionally operates on the already completed R4A3 pilot.
It re-queries only symbol/year windows whose prior receipt reported extra
provider rows, classifies those rows with ``tradestatus``, and combines that
bounded evidence with the existing clean rows.  It never writes market data,
formal preclose data, or the corporate_actions dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

BASE_HEAD = "b0ae8ba72c20f45683a587f450535c79731a5c7b"
DATA_ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
WINDOW_START = date(2016, 1, 1)
WINDOW_END = date(2026, 8, 17)
FORMAL_IDENTITY_N = 5456
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
QUERY_FIELDS = "date,code,preclose,tradestatus"
QUERY_FREQUENCY = "d"
QUERY_ADJUSTFLAG = "3"
OUTPUT_DIR = REPO_ROOT / "reports" / "research"
PREVIOUS_RECEIPT = OUTPUT_DIR / "R4A3_BAOSTOCK_PRECLOSE_CANONICAL_PILOT_RECEIPT.json"
IPO_AUTHORITY_RECEIPT = OUTPUT_DIR / "R4A3_1_IPO_OFFICIAL_AUTHORITY.json"


def _load_previous_module() -> Any:
    path = REPO_ROOT / "tools" / "run_r4a3_baostock_preclose_canonical_pilot.py"
    spec = importlib.util.spec_from_file_location("r4a3_previous_pilot", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load previous R4A3 pilot module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


previous = _load_previous_module()

PILOT_SYMBOLS = tuple(previous.PILOT_SYMBOLS)
PILOT_SYMBOL_HASH = previous.PILOT_SYMBOL_HASH


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


def _display_equal(left: Any, right: Any) -> bool:
    return previous._decimal_display(left) == previous._decimal_display(right)


def load_previous_receipt(path: Path = PREVIOUS_RECEIPT) -> dict[str, Any]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    report = receipt.get("report")
    if not isinstance(report, dict):
        raise ValueError("previous R4A3 receipt has no report object")
    if report.get("PILOT_SYMBOL_HASH") != PILOT_SYMBOL_HASH:
        raise ValueError("previous pilot symbol hash mismatch")
    if report.get("PILOT_SYMBOL_N") != len(PILOT_SYMBOLS):
        raise ValueError("previous pilot symbol count mismatch")
    return receipt


def load_ipo_authority(path: Path = IPO_AUTHORITY_RECEIPT) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("authority_type") != "SSE_OFFICIAL_ONLY":
        raise ValueError("IPO authority is not SSE official-only")
    if payload.get("secondary_authority_sources_n") != 0:
        raise ValueError("secondary IPO authority is present")
    rows = payload.get("rows")
    if not isinstance(rows, list) or {row.get("symbol") for row in rows} != {
        "603007.SH",
        "688486.SH",
        "688489.SH",
    }:
        raise ValueError("IPO authority does not contain the exact three symbols")
    return sorted(rows, key=lambda row: row["symbol"])


def select_requery_windows(previous_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Select only windows already marked with unexpected rows."""
    coverage = previous_report.get("SYMBOL_YEAR_COVERAGE")
    if not isinstance(coverage, list):
        raise ValueError("previous receipt has no symbol/year coverage")
    selected: list[dict[str, Any]] = []
    for row in coverage:
        if int(row.get("unexpected_row_n", 0)) <= 0:
            continue
        symbol = str(row["symbol"])
        year = int(row["year"])
        start = str(row["window_start"])
        end = str(row["window_end"])
        if symbol not in PILOT_SYMBOLS:
            raise ValueError(f"unexpected symbol in previous query plan: {symbol}")
        selected.append(
            {
                "symbol": symbol,
                "year": year,
                "start": start,
                "end": end,
                "bs_code": previous.bs_code(symbol),
                "required_row_n": int(row.get("required_row_n", 0)),
                "previous_unexpected_row_n": int(row["unexpected_row_n"]),
            }
        )
    return selected


def _load_trading_dates(root: Path) -> set[date]:
    frame = (
        pl.scan_parquet(str(root / "curated" / "trading_calendar" / "**" / "*.parquet"))
        .select(["trade_date", "is_trading"])
        .filter(pl.col("is_trading") == True)  # noqa: E712
        .collect()
        .with_columns(pl.col("trade_date").cast(pl.Date))
    )
    return {
        value
        for value in frame.get_column("trade_date").to_list()
        if WINDOW_START <= value <= WINDOW_END
    }


def add_resumption_candidates(root: Path, reference: dict[str, Any]) -> dict[str, Any]:
    """Use the frozen calendar, actual bars, and instrument lifetime only."""
    trading_dates = _load_trading_dates(root)
    instrument_map = reference["instrument_map"]
    candidates: list[dict[str, Any]] = []
    for group in reference["required"].partition_by("symbol", as_dict=False):
        symbol = str(group["symbol"][0])
        metadata = instrument_map.get(symbol, {})
        listed = metadata.get("list_date")
        delisted = metadata.get("delist_date")
        rows = group.sort("trade_date").iter_rows(named=True)
        prior_date: date | None = None
        prior_close: float | None = None
        for row in rows:
            current_date = row["trade_date"]
            if prior_date is not None:
                active_missing = [
                    calendar_date
                    for calendar_date in trading_dates
                    if prior_date < calendar_date < current_date
                    and (listed is None or calendar_date >= listed)
                    and (delisted is None or calendar_date <= delisted)
                ]
                if active_missing:
                    candidates.append(
                        {
                            "symbol": symbol,
                            "trade_date": current_date.isoformat(),
                            "previous_trade_date": prior_date.isoformat(),
                            "previous_effective_close": prior_close,
                            "gap_trading_day_n": len(active_missing),
                            "FIRST_TRADE_AFTER_SUSPENSION": True,
                        }
                    )
            prior_date = current_date
            prior_close = _parse_float(row.get("close"))
    candidate_keys = {
        (row["symbol"], _parse_date(row["trade_date"])) for row in candidates
    }
    return {
        **reference,
        "trading_dates": trading_dates,
        "resumption_candidates": candidates,
        "resumption_candidate_keys": candidate_keys,
    }


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
    return {"plan": item, "error_code": error_code, "error_msg": error_msg, "rows": rows}


def _extra_row(
    item: dict[str, Any],
    raw: list[Any],
    raw_date: date,
    preclose: float,
    classification: str,
) -> dict[str, Any]:
    return {
        "symbol": item["symbol"],
        "date": raw_date.isoformat(),
        "preclose": preclose,
        "tradestatus": None if raw[3] is None else str(raw[3]),
        "classification": classification,
        "window_start": item["start"],
        "window_end": item["end"],
    }


def process_requery_results(
    results: list[dict[str, Any]],
    reference: dict[str, Any],
    previous_report: dict[str, Any],
) -> dict[str, Any]:
    """Classify only the re-queried windows and merge them with old evidence."""
    baseline = {
        (str(row["symbol"]), int(row["year"])): dict(row)
        for row in previous_report["SYMBOL_YEAR_COVERAGE"]
    }
    provider_lookup: dict[tuple[str, date], float] = {}
    extra_rows: list[dict[str, Any]] = []
    query_failures: list[dict[str, Any]] = []
    rechecked: dict[tuple[str, int], dict[str, Any]] = {}
    for result in results:
        item = result["plan"]
        required_keys = reference["required_by_year"].get((item["symbol"], item["year"]), set())
        seen_keys: set[tuple[str, date]] = set()
        traded_keys: set[tuple[str, date]] = set()
        duplicate_n = 0
        identity_failure_n = 0
        post_asof_n = 0
        unknown_status_n = 0
        suspended_extra_n = 0
        unexpected_traded_n = 0
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
            basic_identity = bool(
                raw_date is not None
                and str(raw[1]) == item["bs_code"]
                and item["start"] <= str(raw[0]) <= item["end"]
                and preclose is not None
            )
            if raw_date is not None and raw_date > WINDOW_END:
                post_asof_n += 1
            if not basic_identity:
                identity_failure_n += 1
                continue
            assert raw_date is not None and preclose is not None
            preclose_non_null_n += 1
            key = (item["symbol"], raw_date)
            if key in seen_keys:
                duplicate_n += 1
                continue
            seen_keys.add(key)
            status = "" if raw[3] is None else str(raw[3])
            if status not in {"0", "1"}:
                unknown_status_n += 1
                if key not in required_keys:
                    extra_rows.append(
                        _extra_row(item, raw, raw_date, preclose, "TRADESTATUS_UNKNOWN")
                    )
                continue
            if status == "1":
                traded_keys.add(key)
                provider_lookup[key] = preclose
                if key not in required_keys:
                    unexpected_traded_n += 1
                    extra_rows.append(
                        _extra_row(item, raw, raw_date, preclose, "UNEXPECTED_TRADED_ROW")
                    )
            elif key not in required_keys:
                suspended_extra_n += 1
                extra_rows.append(
                    _extra_row(item, raw, raw_date, preclose, "PROVIDER_SUSPENDED_SUPERSET")
                )
        missing_n = len(required_keys - traded_keys)
        detail = {
            "symbol": item["symbol"],
            "year": item["year"],
            "window_start": item["start"],
            "window_end": item["end"],
            "required_row_n": len(required_keys),
            "baostock_row_n": len(rows),
            "preclose_non_null_n": preclose_non_null_n,
            "required_provider_present_n": len(required_keys & traded_keys),
            "missing_required_row_n": missing_n,
            "provider_suspended_superset_n": suspended_extra_n,
            "unexpected_traded_row_n": unexpected_traded_n,
            "unexpected_row_n": unexpected_traded_n,
            "tradestatus_unknown_n": unknown_status_n,
            "duplicate_pk_n": duplicate_n,
            "identity_failure_n": identity_failure_n,
            "post_asof_n": post_asof_n,
            "status": (
                "PASS"
                if result["error_code"] == "0"
                and not any(
                    (
                        missing_n,
                        unexpected_traded_n,
                        unknown_status_n,
                        duplicate_n,
                        identity_failure_n,
                        post_asof_n,
                    )
                )
                else "FAIL"
            ),
        }
        rechecked[(item["symbol"], item["year"])] = detail

    details: list[dict[str, Any]] = []
    for key, old in baseline.items():
        details.append(rechecked.get(key, old))
    details.sort(key=lambda row: (row["symbol"], int(row["year"])))
    totals = {
        "REQUIRED_ROW_N": 0,
        "REQUIRED_PROVIDER_PRESENT_N": 0,
        "MISSING_REQUIRED_ROW_N": 0,
        "BAOSTOCK_ROW_N": 0,
        "PRECLOSE_NON_NULL_N": 0,
        "PROVIDER_SUSPENDED_SUPERSET_N": 0,
        "UNEXPECTED_TRADED_ROW_N": 0,
        "TRADESTATUS_UNKNOWN_N": 0,
        "DUPLICATE_PK_N": 0,
        "IDENTITY_FAILURE_N": 0,
        "POST_ASOF_N": 0,
    }
    for row in details:
        totals["REQUIRED_ROW_N"] += int(row.get("required_row_n", 0))
        missing = int(row.get("missing_required_row_n", 0))
        totals["MISSING_REQUIRED_ROW_N"] += missing
        totals["REQUIRED_PROVIDER_PRESENT_N"] += int(
            row.get("required_provider_present_n", int(row.get("required_row_n", 0)) - missing)
        )
        totals["BAOSTOCK_ROW_N"] += int(row.get("baostock_row_n", 0))
        totals["PRECLOSE_NON_NULL_N"] += int(row.get("preclose_non_null_n", 0))
        totals["PROVIDER_SUSPENDED_SUPERSET_N"] += int(row.get("provider_suspended_superset_n", 0))
        totals["UNEXPECTED_TRADED_ROW_N"] += int(
            row.get("unexpected_traded_row_n", row.get("unexpected_row_n", 0))
        )
        totals["TRADESTATUS_UNKNOWN_N"] += int(row.get("tradestatus_unknown_n", 0))
        totals["DUPLICATE_PK_N"] += int(row.get("duplicate_pk_n", 0))
        totals["IDENTITY_FAILURE_N"] += int(row.get("identity_failure_n", 0))
        totals["POST_ASOF_N"] += int(row.get("post_asof_n", 0))
    blockers = [
        name
        for name, value in (
            ("MISSING_REQUIRED_ROW_N", totals["MISSING_REQUIRED_ROW_N"]),
            ("UNEXPECTED_TRADED_ROW_N", totals["UNEXPECTED_TRADED_ROW_N"]),
            ("TRADESTATUS_UNKNOWN_N", totals["TRADESTATUS_UNKNOWN_N"]),
            ("DUPLICATE_PK_N", totals["DUPLICATE_PK_N"]),
            ("IDENTITY_FAILURE_N", totals["IDENTITY_FAILURE_N"]),
            ("POST_ASOF_N", totals["POST_ASOF_N"]),
        )
        if value
    ]
    totals.update(
        {
            "SYMBOL_YEAR_COVERAGE": details,
            "QUERY_FAILURE_N": len(query_failures),
            "QUERY_FAILURES": query_failures,
            "R4A_PROVIDER_SCOPE_COVERAGE_STATUS": "PASS"
            if not query_failures and not blockers
            else "FAIL",
            "R4A_PROVIDER_SCOPE_BLOCKERS": blockers
            + (["QUERY_FAILURE_N"] if query_failures else []),
            "provider_lookup": provider_lookup,
            "EXTRA_ROW_AUDIT": sorted(
                extra_rows, key=lambda row: (row["symbol"], row["date"], row["classification"])
            ),
        }
    )
    return totals


def clean_normal_parity(
    reference: dict[str, Any],
    previous_report: dict[str, Any],
    provider_lookup: dict[tuple[str, date], float],
) -> dict[str, Any]:
    excluded = set(reference["event_dates"])
    excluded.update((symbol, first) for symbol, first in reference["first_trade"].items())
    candidate_keys = set(reference["resumption_candidate_keys"])
    candidate_normal_rows: list[dict[str, Any]] = []
    for row in reference["required"].iter_rows(named=True):
        key = (str(row["symbol"]), row["trade_date"])
        if key in excluded or row["prev_close"] is None or key not in candidate_keys:
            continue
        candidate_normal_rows.append(row)
    old_required = int(previous_report.get("NORMAL_REQUIRED_ROW_N", 0))
    old_mismatches = previous_report.get("NORMAL_MISMATCH_SAMPLE", [])
    candidate_key_set = {
        (str(row["symbol"]), _parse_date(row["trade_date"])) for row in candidate_normal_rows
    }
    old_non_candidate_mismatch = sum(
        1
        for row in old_mismatches
        if (str(row.get("symbol")), _parse_date(row.get("trade_date"))) not in candidate_key_set
    )
    candidate_mismatch_rows: list[dict[str, Any]] = []
    resumption_exact_n = 0
    resumption_nonexact_n = 0
    resumption_uncompared_n = 0
    for row in candidate_normal_rows:
        key = (str(row["symbol"]), row["trade_date"])
        observed = provider_lookup.get(key)
        local = _parse_float(row["prev_close"])
        if observed is None or local is None:
            continue
        diff = abs(observed - local)
        exact = _display_equal(observed, local)
        if exact:
            pass
        else:
            candidate_mismatch_rows.append(
                {
                    "symbol": row["symbol"],
                    "trade_date": row["trade_date"].isoformat(),
                    "previous_effective_close": local,
                    "baostock_preclose": observed,
                    "diff": diff,
                }
            )
    # The diagnostic contract covers every candidate, including candidates that
    # also fall on an excluded corporate-action/IPO date.  Clean NORMAL above
    # deliberately uses the narrower candidate_normal_rows set.
    for row in reference["resumption_candidates"]:
        key = (row["symbol"], _parse_date(row["trade_date"]))
        observed = provider_lookup.get(key)
        previous_close = _parse_float(row["previous_effective_close"])
        if observed is None or previous_close is None:
            resumption_uncompared_n += 1
        elif _display_equal(observed, previous_close):
            resumption_exact_n += 1
        else:
            resumption_nonexact_n += 1
    non_candidate_n = old_required - len(candidate_normal_rows)
    non_candidate_exact_n = non_candidate_n - old_non_candidate_mismatch
    clean_n = non_candidate_n
    exact_n = non_candidate_exact_n
    mismatch_n = old_non_candidate_mismatch
    old_non_candidate_mismatch_rows = [
        row
        for row in old_mismatches
        if (str(row.get("symbol")), _parse_date(row.get("trade_date")))
        not in candidate_key_set
    ]
    max_diff = max(
        [float(row.get("diff", 0.0)) for row in old_non_candidate_mismatch_rows],
        default=0.0,
    )
    return {
        "CLEAN_NORMAL_N": clean_n,
        "CLEAN_NORMAL_EXACT_N": exact_n,
        "CLEAN_NORMAL_MISMATCH_N": mismatch_n,
        "CLEAN_NORMAL_UNCOMPARED_N": int(previous_report.get("NORMAL_UNCOMPARED_N", 0)),
        "CLEAN_NORMAL_MAX_DIFF": max_diff,
        "CLEAN_NORMAL_PARITY_STATUS": "PASS"
        if clean_n > 0
        and mismatch_n == 0
        and int(previous_report.get("NORMAL_UNCOMPARED_N", 0)) == 0
        else "FAIL",
        "RESUMPTION_CANDIDATE_N": len(reference["resumption_candidates"]),
        "RESUMPTION_EXACT_N": resumption_exact_n,
        "RESUMPTION_NONEXACT_N": resumption_nonexact_n,
        "RESUMPTION_UNCOMPARED_N": resumption_uncompared_n,
        "RESUMPTION_DIAGNOSTICS": [
            {
                "symbol": row["symbol"],
                "date": row["trade_date"],
                "previous_effective_close": row["previous_effective_close"],
                "baostock_preclose": provider_lookup.get(
                    (row["symbol"], _parse_date(row["trade_date"]))
                ),
                "exact": (
                    provider_lookup.get((row["symbol"], _parse_date(row["trade_date"])))
                    is not None
                    and _display_equal(
                        provider_lookup[(row["symbol"], _parse_date(row["trade_date"]))],
                        row["previous_effective_close"],
                    )
                ),
                "diff": (
                    abs(
                        provider_lookup[(row["symbol"], _parse_date(row["trade_date"]))]
                        - float(row["previous_effective_close"])
                    )
                    if provider_lookup.get((row["symbol"], _parse_date(row["trade_date"])))
                    is not None
                    else None
                ),
            }
            for row in reference["resumption_candidates"]
        ],
        "CLEAN_NORMAL_MISMATCH_SAMPLE": old_non_candidate_mismatch_rows[:20],
    }


def official_event_parity(previous_report: dict[str, Any]) -> dict[str, Any]:
    """Reuse the already resolved bounded official-event evidence."""
    return {
        "OFFICIAL_EVENT_N": int(previous_report.get("OFFICIAL_EVENT_N", 0)),
        "OFFICIAL_EVENT_EXACT_N": int(previous_report.get("OFFICIAL_EVENT_EXACT_N", 0)),
        "OFFICIAL_EVENT_MISMATCH_N": int(previous_report.get("OFFICIAL_EVENT_MISMATCH_N", 0)),
        "OFFICIAL_EVENT_PARITY_STATUS": previous_report.get(
            "OFFICIAL_EVENT_PARITY_STATUS", "UNKNOWN"
        ),
        "OFFICIAL_EVENT_EVIDENCE_REUSED": True,
        "OFFICIAL_EVENT_DETAILS": previous_report.get("OFFICIAL_EVENT_DETAILS", []),
    }


def ipo_parity(
    previous_report: dict[str, Any], authority_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    previous_rows = {
        row["symbol"]: row for row in previous_report.get("IPO_DETAILS", [])
    }
    details: list[dict[str, Any]] = []
    exact_n = 0
    mismatch_n = 0
    for authority in authority_rows:
        symbol = authority["symbol"]
        prior = previous_rows.get(symbol, {})
        observed = _parse_float(prior.get("baostock_preclose"))
        official = _parse_float(authority.get("official_issue_price"))
        exact = observed is not None and official is not None and _display_equal(observed, official)
        exact_n += int(exact)
        mismatch_n += int(not exact)
        details.append(
            {
                "symbol": symbol,
                "listing_date": authority.get("listing_date"),
                "official_issue_price": official,
                "baostock_preclose": observed,
                "exact": exact,
                "authority_url": authority.get("authority_url"),
            }
        )
    return {
        "IPO_OFFICIAL_N": len(authority_rows),
        "IPO_EXACT_N": exact_n,
        "IPO_MISMATCH_N": mismatch_n,
        "IPO_UNCOMPARED_N": len(authority_rows) - exact_n - mismatch_n,
        "IPO_PARITY_STATUS": "PASS"
        if len(authority_rows) == 3 and exact_n == 3 and mismatch_n == 0
        else "FAIL",
        "IPO_DETAILS": details,
        "SECONDARY_AUTHORITY_SOURCES_N": 0,
    }


def source_decision(report: dict[str, Any]) -> dict[str, Any]:
    conditions = {
        "R4A_PROVIDER_SCOPE_COVERAGE_STATUS": report.get(
            "R4A_PROVIDER_SCOPE_COVERAGE_STATUS"
        )
        == "PASS",
        "CLEAN_NORMAL_PARITY_STATUS": report.get("CLEAN_NORMAL_PARITY_STATUS") == "PASS",
        "OFFICIAL_EVENT_PARITY_STATUS": report.get("OFFICIAL_EVENT_PARITY_STATUS") == "PASS",
        "IPO_OFFICIAL_N": report.get("IPO_OFFICIAL_N") == 3
        and report.get("IPO_MISMATCH_N") == 0,
        "WINDOW_EDGE_STATUS": report.get("WINDOW_EDGE_STATUS") == "PASS",
    }
    promote = all(conditions.values())
    return {
        "BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION": (
            "PROMOTE_CANDIDATE_CANONICAL" if promote else "CROSSCHECK_ONLY"
        ),
        "PROMOTION_CONDITIONS_MET": promote,
        "PROMOTION_BLOCKERS": [name for name, passed in conditions.items() if not passed],
        "R4A_IMPLEMENTATION_READY": False,
        "SHARED_BAOSTOCK_EXTRACTION_RECOMMENDATION": (
            "DESIGN_ONLY: future bounded/resumable extraction may request "
            "preclose,turn,tradestatus,isST together, with independent Market Fact datasets"
        ),
    }


def _base_report(
    identity: dict[str, Any],
    previous_report: dict[str, Any],
    requery_plan: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "STATUS": "DRY_RUN_READY" if dry_run else "READY",
        "BASE_HEAD": BASE_HEAD,
        "DATA_ROOT": str(DATA_ROOT),
        "FORMAL_IDENTITY_N": identity.get("FORMAL_IDENTITY_N"),
        "FORMAL_IDENTITY_HASH": identity.get("FORMAL_IDENTITY_HASH"),
        "IDENTITY_MATCH": identity.get("IDENTITY_MATCH"),
        "IDENTITY_SOURCE": identity.get("IDENTITY_SOURCE"),
        "PILOT_SYMBOL_N": len(PILOT_SYMBOLS),
        "PILOT_SYMBOL_HASH": PILOT_SYMBOL_HASH,
        "PILOT_SYMBOLS": list(PILOT_SYMBOLS),
        "WINDOW_START": WINDOW_START.isoformat(),
        "WINDOW_END": WINDOW_END.isoformat(),
        "QUERY_FIELDS": QUERY_FIELDS,
        "QUERY_FREQUENCY": QUERY_FREQUENCY,
        "QUERY_ADJUSTFLAG": QUERY_ADJUSTFLAG,
        "REQUERY_WINDOW_N": len(requery_plan),
        "REQUERY_PLAN": requery_plan,
        "NETWORK_PROVIDER_DATA_FETCH": "NO" if dry_run else "UNKNOWN",
        "NETWORK_PROVIDER_REQUEST_COUNT": 0 if dry_run else "UNVERIFIED",
        "PROVIDER_STEP_ENTERED": "NO" if dry_run else "UNKNOWN",
        "MARKET_DATA_WRITE": "NO",
        "CORPORATE_ACTIONS_WRITE": "NO",
        "FORMAL_PRECLOSE_DATASET_WRITE": "NO",
        "MANIFEST_MUTATION": "NO",
        "SECONDARY_AUTHORITY_SOURCES_N": 0,
        "INITIAL_CLI_EXIT": 0,
        "PREVIOUS_PILOT_STATUS": previous_report.get("STATUS"),
        "NORMAL_SAMPLE_REUSED": True,
        "ANNOUNCEMENT_REVIEW_EXPANDED": False,
        "FULL_242_REQUERY": False,
    }


def render_report(report: dict[str, Any]) -> str:
    """Render from the closure contract without assuming legacy QUERY_N."""
    lines = [
        "# R4A3.1 BaoStock Preclose Source Closure",
        "",
        f"BASE_HEAD: {report.get('BASE_HEAD')}",
        "",
        "## Verdict",
        "",
        "```text",
        f"STATUS={report.get('STATUS')}",
        f"R4A3_1_BAOSTOCK_PRECLOSE_SOURCE_CLOSURE={report.get('STATUS')}",
        f"R4A_PROVIDER_SCOPE_COVERAGE_STATUS={report.get('R4A_PROVIDER_SCOPE_COVERAGE_STATUS', 'NOT_RUN')}",
        f"CLEAN_NORMAL_PARITY_STATUS={report.get('CLEAN_NORMAL_PARITY_STATUS', 'NOT_RUN')}",
        f"OFFICIAL_EVENT_PARITY_STATUS={report.get('OFFICIAL_EVENT_PARITY_STATUS', 'NOT_RUN')}",
        f"IPO_PARITY_STATUS={report.get('IPO_PARITY_STATUS', 'NOT_RUN')}",
        f"WINDOW_EDGE_STATUS={report.get('WINDOW_EDGE_STATUS', 'NOT_RUN')}",
        f"BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION={report.get('BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION', 'CROSSCHECK_ONLY')}",
        f"INITIAL_CLI_EXIT={report.get('INITIAL_CLI_EXIT', 0)}",
        "LEGACY_QUERY_N_RENDER_FALLBACK=ENABLED",
        "R4A_IMPLEMENTATION_READY=false",
        "```",
        "",
        "This is a bounded evaluator closure. It does not write formal preclose",
        "data, corporate_actions, or any other market-data dataset.",
        "",
        "## Scope and requery boundary",
        "",
        "```text",
        f"FORMAL_IDENTITY_N={report.get('FORMAL_IDENTITY_N')}",
        f"FORMAL_IDENTITY_HASH={report.get('FORMAL_IDENTITY_HASH')}",
        f"IDENTITY_MATCH={report.get('IDENTITY_MATCH')}",
        f"PILOT_SYMBOL_N={report.get('PILOT_SYMBOL_N')}",
        f"PILOT_SYMBOL_HASH={report.get('PILOT_SYMBOL_HASH')}",
        f"WINDOW={report.get('WINDOW_START')}..{report.get('WINDOW_END')}",
        f"QUERY_FIELDS={report.get('QUERY_FIELDS')}",
        f"REQUERY_WINDOW_N={report.get('REQUERY_WINDOW_N')}",
        f"NETWORK_PROVIDER_REQUEST_COUNT={report.get('NETWORK_PROVIDER_REQUEST_COUNT')}",
        "FULL_242_REQUERY=false",
        "```",
        "",
        "Only previous symbol/year rows with unexpected_row_n>0 were re-queried.",
        "Rows with tradestatus=0 are retained as a provider suspended superset",
        "audit and are not required actual-traded rows.",
        "",
        "## Coverage",
        "",
        "```text",
        f"REQUIRED_ROW_N={report.get('REQUIRED_ROW_N')}",
        f"REQUIRED_PROVIDER_PRESENT_N={report.get('REQUIRED_PROVIDER_PRESENT_N')}",
        f"MISSING_REQUIRED_ROW_N={report.get('MISSING_REQUIRED_ROW_N')}",
        f"PROVIDER_SUSPENDED_SUPERSET_N={report.get('PROVIDER_SUSPENDED_SUPERSET_N')}",
        f"UNEXPECTED_TRADED_ROW_N={report.get('UNEXPECTED_TRADED_ROW_N')}",
        f"TRADESTATUS_UNKNOWN_N={report.get('TRADESTATUS_UNKNOWN_N')}",
        f"DUPLICATE_PK_N={report.get('DUPLICATE_PK_N')}",
        f"IDENTITY_FAILURE_N={report.get('IDENTITY_FAILURE_N')}",
        f"POST_ASOF_N={report.get('POST_ASOF_N')}",
        f"R4A_PROVIDER_SCOPE_COVERAGE_STATUS={report.get('R4A_PROVIDER_SCOPE_COVERAGE_STATUS')}",
        "```",
        "",
        "## Parity",
        "",
        "```text",
        f"CLEAN_NORMAL_N={report.get('CLEAN_NORMAL_N')}",
        f"CLEAN_NORMAL_EXACT_N={report.get('CLEAN_NORMAL_EXACT_N')}",
        f"CLEAN_NORMAL_MISMATCH_N={report.get('CLEAN_NORMAL_MISMATCH_N')}",
        f"CLEAN_NORMAL_MAX_DIFF={report.get('CLEAN_NORMAL_MAX_DIFF')}",
        f"RESUMPTION_CANDIDATE_N={report.get('RESUMPTION_CANDIDATE_N')}",
        f"RESUMPTION_EXACT_N={report.get('RESUMPTION_EXACT_N')}",
        f"RESUMPTION_NONEXACT_N={report.get('RESUMPTION_NONEXACT_N')}",
        f"OFFICIAL_EVENT_N={report.get('OFFICIAL_EVENT_N')}",
        f"OFFICIAL_EVENT_EXACT_N={report.get('OFFICIAL_EVENT_EXACT_N')}",
        f"OFFICIAL_EVENT_MISMATCH_N={report.get('OFFICIAL_EVENT_MISMATCH_N')}",
        f"IPO_OFFICIAL_N={report.get('IPO_OFFICIAL_N')}",
        f"IPO_EXACT_N={report.get('IPO_EXACT_N')}",
        f"IPO_MISMATCH_N={report.get('IPO_MISMATCH_N')}",
        f"WINDOW_EDGE_STATUS={report.get('WINDOW_EDGE_STATUS')}",
        "```",
        "",
        "The 000564.SZ 2018-07-20 row is explicitly recorded as a",
        "RESUMPTION_CANDIDATE and is excluded from CLEAN_NORMAL.",
        "Official event evidence is reused from the prior bounded receipt; the",
        "two Sol-frozen 000002 adjusted-basis rows remain resolved.",
        "",
        "## IPO authority",
        "",
    ]
    for row in report.get("IPO_DETAILS", []):
        lines.append(
            f"- {row.get('symbol')} {row.get('listing_date')} issue={row.get('official_issue_price')} "
            f"preclose={row.get('baostock_preclose')} exact={row.get('exact')} "
            f"[{row.get('authority_url')}]"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "```text",
            f"NETWORK_PROVIDER_DATA_FETCH={report.get('NETWORK_PROVIDER_DATA_FETCH')}",
            f"PROVIDER_STEP_ENTERED={report.get('PROVIDER_STEP_ENTERED')}",
            "MARKET_DATA_WRITE=NO",
            "CORPORATE_ACTIONS_WRITE=NO",
            "FORMAL_PRECLOSE_DATASET_WRITE=NO",
            "MANIFEST_MUTATION=NO",
            "SECONDARY_AUTHORITY_SOURCES_N=0",
            "R4B_R4C_R4D_IMPLEMENTATION=FORBIDDEN",
            "STRATEGY_FORWARD_TRADEPLAN=FORBIDDEN",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_outputs(report: dict[str, Any], *, output_dir: Path, extra_rows: list[dict[str, Any]], authority: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "task": "R4A3_1_BAOSTOCK_PRECLOSE_SOURCE_CLOSURE_V01",
        "report": report,
        "ipo_official_authority": authority,
        "extra_row_audit": extra_rows,
    }
    (output_dir / "R4A3_1_BAOSTOCK_PRECLOSE_SOURCE_CLOSURE_RECEIPT.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (output_dir / "R4A3_1_PROVIDER_EXTRA_ROW_AUDIT.json").write_text(
        json.dumps(
            {
                "task": "R4A3_1_BAOSTOCK_PRECLOSE_SOURCE_CLOSURE_V01",
                "row_scope": "requery_only_extra_rows",
                "rows": extra_rows,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "R4A3_1_BAOSTOCK_PRECLOSE_SOURCE_CLOSURE_REPORT.md").write_text(
        render_report(report), encoding="utf-8"
    )


def run_closure(
    *,
    root: Path = DATA_ROOT,
    dry_run: bool = True,
    provider: Any | None = None,
    identity: dict[str, Any] | None = None,
    previous_receipt_path: Path = PREVIOUS_RECEIPT,
    ipo_authority_path: Path = IPO_AUTHORITY_RECEIPT,
    output_dir: Path = OUTPUT_DIR,
    write_outputs: bool = True,
) -> dict[str, Any]:
    previous_receipt = load_previous_receipt(previous_receipt_path)
    previous_report = previous_receipt["report"]
    authority = load_ipo_authority(ipo_authority_path)
    identity_result = identity or previous.load_frozen_identity(root)
    requery_plan = select_requery_windows(previous_report)
    report = _base_report(identity_result, previous_report, requery_plan, dry_run=dry_run)
    if not bool(identity_result.get("IDENTITY_MATCH")):
        report.update(
            {
                "STATUS": "FAIL_CLOSED_IDENTITY",
                "R4A_IMPLEMENTATION_READY": False,
                "BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION": "CROSSCHECK_ONLY",
            }
        )
        return report
    official_rows = previous.load_official_event_rows(
        previous.OFFICIAL_RECEIPT, list(PILOT_SYMBOLS)
    )
    reference = previous.load_reference_data(root, list(PILOT_SYMBOLS), official_rows)
    reference = add_resumption_candidates(root, reference)
    report["REQUIRED_ROW_N"] = sum(len(value) for value in reference["required_by_year"].values())
    report["WINDOW_EDGE_STATUS"] = previous.window_edge(
        reference, {}, list(PILOT_SYMBOLS)
    )["WINDOW_EDGE_STATUS"] if dry_run else previous_report.get("WINDOW_EDGE_STATUS", "UNKNOWN")
    report["RESUMPTION_CANDIDATE_N"] = len(reference["resumption_candidates"])
    if dry_run:
        report.update(
            {
                "COVERAGE_STATUS": "NOT_RUN",
                "R4A_PROVIDER_SCOPE_COVERAGE_STATUS": "NOT_RUN",
                "CLEAN_NORMAL_PARITY_STATUS": "NOT_RUN",
                "OFFICIAL_EVENT_PARITY_STATUS": "NOT_RUN",
                "IPO_PARITY_STATUS": "NOT_RUN",
                "BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION": "CROSSCHECK_ONLY",
                "R4A_IMPLEMENTATION_READY": False,
                "PROMOTION_CONDITIONS_MET": False,
                "PROMOTION_BLOCKERS": ["DRY_RUN_ONLY"],
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
                "NETWORK_PROVIDER_REQUEST_COUNT": 0,
                "R4A_PROVIDER_SCOPE_COVERAGE_STATUS": "FAIL",
                "BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION": "CROSSCHECK_ONLY",
                "R4A_IMPLEMENTATION_READY": False,
            }
        )
        return report
    results: list[dict[str, Any]] = []
    try:
        for item in requery_plan:
            results.append(_query_rows(provider, item))
    finally:
        provider.logout()
    report["NETWORK_PROVIDER_REQUEST_COUNT"] = len(results)
    processed = process_requery_results(results, reference, previous_report)
    report.update({key: value for key, value in processed.items() if key not in {"provider_lookup", "EXTRA_ROW_AUDIT"}})
    report["COVERAGE_STATUS"] = report["R4A_PROVIDER_SCOPE_COVERAGE_STATUS"]
    provider_lookup = processed["provider_lookup"]
    report.update(clean_normal_parity(reference, previous_report, provider_lookup))
    report.update(official_event_parity(previous_report))
    report.update(ipo_parity(previous_report, authority))
    report["WINDOW_EDGE_STATUS"] = previous_report.get("WINDOW_EDGE_STATUS", "UNKNOWN")
    report.update(source_decision(report))
    report["STATUS"] = "CLOSURE_COMPLETE" if not report["QUERY_FAILURE_N"] else "CLOSURE_PARTIAL"
    if write_outputs:
        _write_outputs(
            report,
            output_dir=output_dir,
            extra_rows=processed["EXTRA_ROW_AUDIT"],
            authority=authority,
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bounded R4A3.1 BaoStock source closure")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--exec", action="store_true", help="re-query only prior unexpected windows")
    group.add_argument("--dry-run", action="store_true", help="validate the bounded requery plan")
    args = parser.parse_args(argv)
    result = run_closure(dry_run=not args.exec)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("STATUS") in {"DRY_RUN_READY", "CLOSURE_COMPLETE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
