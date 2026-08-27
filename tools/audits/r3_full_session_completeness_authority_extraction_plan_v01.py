#!/usr/bin/env python3
"""Freeze the offline R3 full-session authority extraction request plan.

This module is intentionally provider-free.  It reads the already frozen
formal identity, instruments, trading calendar, and canonical input manifest,
then emits a deterministic calendar-year request manifest and an execution
contract for a future isolated extraction.  It never imports BaoStock, TDX,
network clients, or data-root writers.

The formal request universe is independent of current canonical gaps:

    formal identity x exchange trading dates x independent lifecycle
    -> outside lifetime OR exactly one clipped calendar-year request.

The future extraction contract preserves UNKNOWN.  An absent provider row
inside lifetime is not converted to NOT_EXPECTED_BAR and cannot pass a
completeness key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from bisect import bisect_left, bisect_right
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
OUTPUT_DIR = REPO_ROOT / "reports" / "implementation"

BASE_HEAD = "9d7baf69ccdaf323c3221a62cf0487378373e95f"
BRANCH = "codex/r3-full-session-completeness-authority-extraction-plan-v01"
FEASIBILITY_COMMIT = "9d7baf69ccdaf323c3221a62cf0487378373e95f"
WINDOW_START = date(2016, 1, 1)
WINDOW_END = date(2026, 8, 17)
FORMAL_SYMBOL_N = 5456
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
INPUT_FILE_N = 2580
INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"

PROVIDER = "baostock"
PROVIDER_DISTRIBUTION_VERSION = "0.9.3"
PROVIDER_RUNTIME = "baostock-0.9.3"
QUERY_FIELDS = "date,code,open,high,low,close,volume,amount,preclose,tradestatus"
QUERY_FREQUENCY = "d"
QUERY_ADJUSTFLAG = "3"
EXECUTION_BASELINE = "CALENDAR_YEAR_WINDOWED"

MAX_RETRY = 2  # additional retries; at most MAX_RETRY + 1 attempts
REQUEST_INTERVAL_SECONDS = 1.0
BATCH_SIZE = 50
CHECKPOINT_INTERVAL_REQUESTS = 1
RETRY_BACKOFF_SECONDS = [1.0, 2.0]

TASK_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01"
MANIFEST_NAME = f"{TASK_NAME}_REQUEST_MANIFEST.json"
REPORT_NAME = f"{TASK_NAME}.json"
REPORT_MD_NAME = f"{TASK_NAME}.md"
MANIFEST_PATH = OUTPUT_DIR / MANIFEST_NAME
REPORT_PATH = OUTPUT_DIR / REPORT_NAME
REPORT_MD_PATH = OUTPUT_DIR / REPORT_MD_NAME

CHECKPOINT_STATES = ("PENDING", "RUNNING", "COMPLETE", "FAILED")
REQUIRED_RECEIPT_FIELDS = (
    "request_id",
    "request_order",
    "request_manifest_hash",
    "attempt_n",
    "provider_error_code",
    "raw_row_n",
    "raw_sha256",
    "normalized_row_n",
    "normalized_sha256",
    "status",
    "completed_at",
)


class PlanError(RuntimeError):
    """Fail-closed plan or authority error."""


def canonical_json_bytes(value: Any) -> bytes:
    def default(item: Any) -> str:
        if isinstance(item, date):
            return item.isoformat()
        raise TypeError(f"not JSON serializable: {type(item)!r}")

    return json.dumps(
        value,
        default=default,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def parse_int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        if not text or any(char in text for char in (".", "e", "E")):
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def identity_hash(symbols: Iterable[str]) -> str:
    return sha256_bytes(json.dumps(sorted(symbols), separators=(",", ":")).encode("utf-8"))


def bs_code(symbol: str) -> str:
    code, exchange = str(symbol).split(".")
    if exchange not in {"SH", "SZ"} or len(code) != 6 or not code.isdigit():
        raise PlanError(f"NON_SHSZ_FORMAL_SYMBOL:{symbol}")
    return ("sh" if exchange == "SH" else "sz") + "." + code


def load_formal_identity_symbols(data_root: Path) -> tuple[list[str], dict[str, str]]:
    identity_path = data_root / "meta" / "asl" / "r3" / "r3-identity-receipt.json"
    progress_path = data_root / "meta" / "asl" / "r3" / "r3-quarterly-roster-audit-progress-v074.json"
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise PlanError(f"FORMAL_IDENTITY_RECEIPT_UNREADABLE:{exc}") from exc
    if (
        identity.get("formal_identity_n") != FORMAL_SYMBOL_N
        or identity.get("formal_identity_hash") != FORMAL_IDENTITY_HASH
        or identity.get("shsz_identity_complete") is not True
        or progress.get("formal_identity_hash") != FORMAL_IDENTITY_HASH
    ):
        raise PlanError("FORMAL_IDENTITY_RECEIPT_MISMATCH")
    roster_symbols = progress.get("roster_union_symbols")
    not_observable = identity.get("roster_not_observable_identity_sample")
    if not isinstance(roster_symbols, list) or not isinstance(not_observable, list):
        raise PlanError("FORMAL_IDENTITY_SYMBOL_LIST_UNAVAILABLE")
    symbols = sorted({str(value) for value in [*roster_symbols, *not_observable]})
    if len(symbols) != FORMAL_SYMBOL_N or identity_hash(symbols) != FORMAL_IDENTITY_HASH:
        raise PlanError("FORMAL_IDENTITY_RECONSTRUCTION_MISMATCH")
    for symbol in symbols:
        bs_code(symbol)
    return symbols, {
        "identity_receipt": str(identity_path),
        "roster_progress_receipt": str(progress_path),
        "authority": str(identity.get("shsz_identity_authority", "UNKNOWN")),
    }


def build_input_file_manifest(data_root: Path) -> dict[str, Any]:
    """Reproduce the frozen R3 daily input manifest without writing files."""

    base = data_root / "curated" / "daily_bars"
    files = sorted(path for path in base.rglob("*.parquet") if path.is_file())
    rows: list[dict[str, Any]] = []
    for path in files:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        rows.append(
            {
                "relative_path": str(path.relative_to(data_root)),
                "file_size": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return {
        "INPUT_FILE_N": len(rows),
        "INPUT_MANIFEST_HASH": sha256_json(rows),
        "FILES": rows,
    }


def load_calendar(data_root: Path) -> list[date]:
    frame = (
        pl.scan_parquet(str(data_root / "curated" / "trading_calendar" / "**" / "*.parquet"))
        .filter(pl.col("is_trading") == True)  # noqa: E712
        .select("trade_date")
        .collect()
        .with_columns(pl.col("trade_date").cast(pl.Date))
        .filter((pl.col("trade_date") >= WINDOW_START) & (pl.col("trade_date") <= WINDOW_END))
        .unique()
        .sort("trade_date")
    )
    dates = [value for value in frame["trade_date"].to_list() if isinstance(value, date)]
    if not dates:
        raise PlanError("TRADING_CALENDAR_EMPTY")
    return dates


def load_instruments(data_root: Path) -> dict[str, dict[str, Any]]:
    frame = pl.read_parquet(data_root / "curated" / "instruments" / "part-merged.parquet").select(
        ["symbol", "exchange", "asset_type", "list_date", "delist_date"]
    )
    records: dict[str, dict[str, Any]] = {}
    for row in frame.iter_rows(named=True):
        symbol = str(row["symbol"])
        if symbol in records:
            raise PlanError(f"DUPLICATE_INSTRUMENT_SYMBOL:{symbol}")
        records[symbol] = {
            "symbol": symbol,
            "exchange": str(row["exchange"]),
            "asset_type": str(row["asset_type"]),
            "list_date": parse_date(row.get("list_date")),
            "delist_date": parse_date(row.get("delist_date")),
        }
    return records


def validate_formal_scope(symbols: list[str], instruments: dict[str, dict[str, Any]]) -> dict[str, Any]:
    missing = sorted(set(symbols) - set(instruments))
    if missing:
        raise PlanError(f"FORMAL_SYMBOL_MISSING_INSTRUMENT_METADATA:{missing[:5]}")
    bad_scope = [
        symbol
        for symbol in symbols
        if instruments[symbol]["exchange"] not in {"SH", "SZ"}
        or instruments[symbol]["asset_type"] not in {"stock", "cdr"}
    ]
    if bad_scope:
        raise PlanError(f"FORMAL_SCOPE_NOT_SHSZ_STOCK_CDR:{bad_scope[:5]}")
    return {
        "FORMAL_SYMBOL_N": len(symbols),
        "FORMAL_IDENTITY_HASH": identity_hash(symbols),
        "SH_SYMBOL_N": sum(instruments[symbol]["exchange"] == "SH" for symbol in symbols),
        "SZ_SYMBOL_N": sum(instruments[symbol]["exchange"] == "SZ" for symbol in symbols),
        "SHSZ_ONLY": True,
        "MISSING_INSTRUMENT_METADATA_N": 0,
    }


def lifecycle_bounds(meta: dict[str, Any]) -> tuple[date, date] | None:
    start = max(meta.get("list_date") or WINDOW_START, WINDOW_START)
    end = min(meta.get("delist_date") or WINDOW_END, WINDOW_END)
    return (start, end) if start <= end else None


def calendar_count(calendar_dates: list[date], start: date, end: date) -> int:
    return max(0, bisect_right(calendar_dates, end) - bisect_left(calendar_dates, start))


def build_full_request_manifest(
    symbols: list[str],
    instruments: dict[str, dict[str, Any]],
    calendar_dates: list[date],
    input_manifest: dict[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Construct the full deterministic annual request plan and prove coverage."""

    requests: list[dict[str, Any]] = []
    lifecycle_key_n = 0
    outside_key_n = 0
    all_calendar_key_n = len(symbols) * len(calendar_dates)
    request_order = 0
    per_symbol_request_n: dict[str, int] = {}
    for symbol in sorted(symbols):
        bounds = lifecycle_bounds(instruments[symbol])
        if bounds is None:
            outside_key_n += len(calendar_dates)
            per_symbol_request_n[symbol] = 0
            continue
        effective_start, effective_end = bounds
        lifecycle_n = calendar_count(calendar_dates, effective_start, effective_end)
        lifecycle_key_n += lifecycle_n
        outside_key_n += len(calendar_dates) - lifecycle_n
        first_year = effective_start.year
        last_year = effective_end.year
        symbol_request_n = 0
        for year in range(first_year, last_year + 1):
            start = max(effective_start, date(year, 1, 1))
            end = min(effective_end, date(year, 12, 31))
            if start > end:
                continue
            request_order += 1
            symbol_request_n += 1
            requests.append(
                {
                    "request_id": f"R3SAC-{request_order:06d}",
                    "request_order": request_order,
                    "symbol": symbol,
                    "bs_code": bs_code(symbol),
                    "calendar_year": year,
                    "list_date": instruments[symbol].get("list_date"),
                    "delist_date": instruments[symbol].get("delist_date"),
                    "start_date": start,
                    "end_date": end,
                    "calendar_trading_date_n": calendar_count(calendar_dates, start, end),
                    "adjustflag": QUERY_ADJUSTFLAG,
                    "fields": QUERY_FIELDS,
                    "frequency": QUERY_FREQUENCY,
                    "provider": PROVIDER,
                    "provider_runtime": PROVIDER_RUNTIME,
                }
            )
        per_symbol_request_n[symbol] = symbol_request_n
    if request_order != len(requests):
        raise PlanError("REQUEST_ORDER_NONCONTIGUOUS")
    coverage = verify_request_coverage(symbols, instruments, calendar_dates, requests)
    if coverage["REQUEST_COVERAGE_MISSING_KEY_N"] != 0 or coverage["REQUEST_COVERAGE_DUPLICATE_KEY_N"] != 0:
        raise PlanError("REQUEST_COVERAGE_NOT_EXACT")
    if coverage["LIFECYCLE_SESSION_KEY_N"] != lifecycle_key_n:
        raise PlanError("LIFECYCLE_KEY_COUNT_RECONCILIATION_FAILED")
    manifest = {
        "TASK": TASK_NAME,
        "BASE_HEAD": BASE_HEAD,
        "FEASIBILITY_COMMIT": FEASIBILITY_COMMIT,
        "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
        "FORMAL_SYMBOL_N": len(symbols),
        "FORMAL_IDENTITY_HASH": identity_hash(symbols),
        "EXECUTION_BASELINE": EXECUTION_BASELINE,
        "window": {"start": WINDOW_START, "end": WINDOW_END},
        "authority_contract": {
            "tradestatus_1": "EXPECTED_BAR",
            "tradestatus_0": "NOT_EXPECTED_BAR",
            "row_absent_inside_lifetime": "UNKNOWN",
            "outside_lifecycle": "NOT_EXPECTED_BAR via frozen identity",
            "unknown_not_pass": True,
            "unknown_not_not_expected": True,
        },
        "provider_contract": {
            "provider": PROVIDER,
            "distribution_version": PROVIDER_DISTRIBUTION_VERSION,
            "runtime": PROVIDER_RUNTIME,
            "fields": QUERY_FIELDS,
            "frequency": QUERY_FREQUENCY,
            "adjustflag": QUERY_ADJUSTFLAG,
        },
        "requests": requests,
    }
    manifest_hash = sha256_json(manifest)
    coverage["REQUEST_N"] = len(requests)
    coverage["LIFECYCLE_SESSION_KEY_N"] = lifecycle_key_n
    coverage["OUTSIDE_LIFETIME_SESSION_KEY_N"] = outside_key_n
    coverage["ALL_SYMBOL_CALENDAR_GRID_KEY_N"] = all_calendar_key_n
    coverage["PER_SYMBOL_REQUEST_N_HASH"] = sha256_json(per_symbol_request_n)
    return manifest, manifest_hash, coverage


def verify_request_coverage(
    symbols: list[str],
    instruments: dict[str, dict[str, Any]],
    calendar_dates: list[date],
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prove lifecycle dates are covered once by year-clipped intervals."""

    by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    for request in requests:
        if request["symbol"] not in by_symbol:
            raise PlanError(f"REQUEST_SYMBOL_OUTSIDE_FORMAL_SCOPE:{request['symbol']}")
        by_symbol[request["symbol"]].append(request)
    missing_n = 0
    duplicate_n = 0
    lifecycle_n = 0
    request_n = len(requests)
    for symbol in sorted(symbols):
        bounds = lifecycle_bounds(instruments[symbol])
        expected = calendar_count(calendar_dates, *bounds) if bounds else 0
        lifecycle_n += expected
        rows = sorted(by_symbol[symbol], key=lambda row: (row["start_date"], row["end_date"], row["request_order"]))
        assigned = sum(int(row.get("calendar_trading_date_n", 0)) for row in rows)
        if assigned < expected:
            missing_n += expected - assigned
        elif assigned > expected:
            duplicate_n += assigned - expected
        if bounds is None:
            if rows:
                raise PlanError(f"REQUEST_FOR_EMPTY_LIFETIME:{symbol}")
            continue
        effective_start, effective_end = bounds
        if not rows:
            missing_n += expected
            continue
        first_start = parse_date(rows[0]["start_date"])
        last_end = parse_date(rows[-1]["end_date"])
        if first_start is None or last_end is None:
            raise PlanError(f"REQUEST_DATE_INVALID:{symbol}")
        missing_n += calendar_count(calendar_dates, effective_start, min(effective_end, first_start - _one_day()))
        missing_n += calendar_count(calendar_dates, max(effective_start, last_end + _one_day()), effective_end)
        for previous, current in zip(rows, rows[1:]):
            previous_end = parse_date(previous["end_date"])
            current_start = parse_date(current["start_date"])
            if previous_end is None or current_start is None:
                raise PlanError(f"REQUEST_DATE_INVALID:{symbol}")
            overlap_start = max(parse_date(previous["start_date"]), current_start)
            overlap_end = min(previous_end, parse_date(current["end_date"]))
            if overlap_start <= overlap_end:
                duplicate_n += calendar_count(calendar_dates, overlap_start, overlap_end)
            gap_start = previous_end + _one_day()
            gap_end = current_start - _one_day()
            missing_n += calendar_count(calendar_dates, gap_start, gap_end)
    return {
        "REQUEST_N": request_n,
        "FORMAL_SYMBOL_N": len(symbols),
        "LIFECYCLE_SESSION_KEY_N": lifecycle_n,
        "REQUEST_COVERAGE_MISSING_KEY_N": missing_n,
        "REQUEST_COVERAGE_DUPLICATE_KEY_N": duplicate_n,
        "REQUEST_COVERAGE_EXACT": missing_n == 0 and duplicate_n == 0,
    }


def _one_day() -> Any:
    from datetime import timedelta

    return timedelta(days=1)


def classify_session_key(provider_row: dict[str, Any] | None, *, inside_lifetime: bool) -> dict[str, Any]:
    """Apply the frozen three-way contract without collapsing UNKNOWN."""

    if not inside_lifetime:
        return {"classification": "NOT_EXPECTED_BAR", "basis": "OUTSIDE_LIFETIME"}
    if provider_row is None:
        return {"classification": "UNKNOWN", "basis": "PROVIDER_ROW_ABSENT_IN_LIFETIME"}
    status = parse_int(provider_row.get("tradestatus"))
    if status == 1:
        return {"classification": "EXPECTED_BAR", "basis": "PROVIDER_TRADESTATUS_1"}
    if status == 0:
        return {"classification": "NOT_EXPECTED_BAR", "basis": "PROVIDER_TRADESTATUS_0"}
    return {"classification": "UNKNOWN", "basis": "TRADESTATUS_INVALID"}


def is_valid_complete_receipt(
    request: dict[str, Any],
    receipt: dict[str, Any] | None,
    *,
    request_manifest_hash: str,
    raw_payload: Any | None = None,
    normalized_payload: Any | None = None,
) -> bool:
    """Return true only for a hash-verified COMPLETE request receipt."""

    if not isinstance(receipt, dict) or any(field not in receipt for field in REQUIRED_RECEIPT_FIELDS):
        return False
    if (
        receipt.get("request_id") != request.get("request_id")
        or receipt.get("request_order") != request.get("request_order")
        or receipt.get("request_manifest_hash") != request_manifest_hash
        or receipt.get("status") != "COMPLETE"
        or str(receipt.get("provider_error_code")) != "0"
        or not str(receipt.get("completed_at", "")).strip()
    ):
        return False
    attempt_n = parse_int(receipt.get("attempt_n"))
    raw_row_n = parse_int(receipt.get("raw_row_n"))
    normalized_row_n = parse_int(receipt.get("normalized_row_n"))
    if attempt_n is None or attempt_n < 1 or raw_row_n is None or raw_row_n < 0 or normalized_row_n is None or normalized_row_n < 0:
        return False
    # The resume caller must load both receipt payloads and verify their bytes;
    # a hash string in an otherwise detached receipt is not sufficient proof.
    if raw_payload is None or normalized_payload is None:
        return False
    if receipt.get("raw_sha256") != sha256_json(raw_payload):
        return False
    if receipt.get("normalized_sha256") != sha256_json(normalized_payload):
        return False
    return True


def quality_gate(request_receipts: list[dict[str, Any]], *, unknown_case_n: int) -> dict[str, Any]:
    provider_failed_n = sum(str(row.get("provider_error_code")) != "0" for row in request_receipts)
    duplicate_n = sum(int(row.get("duplicate_provider_key_n", 0)) for row in request_receipts)
    invalid_n = sum(int(row.get("invalid_provider_row_n", 0)) for row in request_receipts)
    complete_n = sum(row.get("status") == "COMPLETE" for row in request_receipts)
    all_complete = complete_n == len(request_receipts) and len(request_receipts) > 0
    return {
        "ALL_REQUESTS_COMPLETE": all_complete,
        "COMPLETE_REQUEST_N": complete_n,
        "PROVIDER_FAILED_REQUEST_N": provider_failed_n,
        "DUPLICATE_PROVIDER_KEY_N": duplicate_n,
        "INVALID_PROVIDER_ROW_N": invalid_n,
        "UNKNOWN_CASE_N": unknown_case_n,
        "UNKNOWN_CASE_ALLOWED": True,
        "UNKNOWN_BLOCKS_KEY_PASS": unknown_case_n > 0,
        "QUALITY_GATE_READY_FOR_RECONCILIATION": all_complete and provider_failed_n == 0 and duplicate_n == 0 and invalid_n == 0,
    }


def checkpoint_contract() -> dict[str, Any]:
    return {
        "state_enum": list(CHECKPOINT_STATES),
        "initial_state": "PENDING",
        "staging_root_relative": "staging/r3_full_session_completeness_authority_v01/",
        "files": {
            "request_manifest": "request_manifest.json",
            "checkpoint": "checkpoint.json",
            "raw_receipts": "raw_receipts/{request_id}.json",
            "normalized_receipts": "normalized_receipts/{request_id}.json",
            "session_authority": "session_authority.parquet",
            "execution_receipt": "execution_receipt.json",
            "quality_report": "quality_report.json",
        },
        "receipt_required_fields": list(REQUIRED_RECEIPT_FIELDS),
        "complete_predicate": [
            "status == COMPLETE",
            "provider_error_code == 0",
            "request_id/order/manifest hash equal current manifest",
            "raw_sha256 and normalized_sha256 recompute exactly",
            "attempt_n >= 1 and completed_at is non-empty",
        ],
        "resume_rule": "skip only a receipt satisfying the complete predicate; missing, FAILED, RUNNING, or corrupt receipts rerun",
        "checkpoint_write_frequency": "after every request; batch boundary also observed",
    }


def build_report(
    *,
    input_manifest: dict[str, Any],
    identity_source: dict[str, str],
    scope: dict[str, Any],
    manifest_hash: str,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "REPORT": TASK_NAME,
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "FEASIBILITY_COMMIT": FEASIBILITY_COMMIT,
        "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
        "FORMAL_SYMBOL_N": scope["FORMAL_SYMBOL_N"],
        "FORMAL_IDENTITY_HASH": scope["FORMAL_IDENTITY_HASH"],
        "FORMAL_IDENTITY_SOURCE": identity_source,
        "EXECUTION_BASELINE": EXECUTION_BASELINE,
        "FULL_REQUEST_N": coverage["REQUEST_N"],
        "FULL_REQUEST_MANIFEST_HASH": manifest_hash,
        "LIFECYCLE_SESSION_KEY_N": coverage["LIFECYCLE_SESSION_KEY_N"],
        "OUTSIDE_LIFETIME_SESSION_KEY_N": coverage["OUTSIDE_LIFETIME_SESSION_KEY_N"],
        "REQUEST_COVERAGE_MISSING_KEY_N": coverage["REQUEST_COVERAGE_MISSING_KEY_N"],
        "REQUEST_COVERAGE_DUPLICATE_KEY_N": coverage["REQUEST_COVERAGE_DUPLICATE_KEY_N"],
        "REQUEST_COVERAGE_EXACT": coverage["REQUEST_COVERAGE_EXACT"],
        "REQUEST_MANIFEST_PATH": str(MANIFEST_PATH),
        "AUTHORITY_CONTRACT": {
            "tradestatus_1": "EXPECTED_BAR",
            "tradestatus_0": "NOT_EXPECTED_BAR",
            "row_absent_inside_lifetime": "UNKNOWN",
            "outside_lifecycle": "NOT_EXPECTED_BAR via frozen identity",
            "UNKNOWN_NOT_PASS": True,
            "UNKNOWN_NOT_NOT_EXPECTED_BAR": True,
        },
        "CHECKPOINT_CONTRACT": checkpoint_contract(),
        "RATE_RETRY_CONTRACT": {
            "MAX_RETRY": MAX_RETRY,
            "MAX_ATTEMPT_N": MAX_RETRY + 1,
            "REQUEST_INTERVAL_SECONDS": REQUEST_INTERVAL_SECONDS,
            "BATCH_SIZE": BATCH_SIZE,
            "CHECKPOINT_INTERVAL_REQUESTS": CHECKPOINT_INTERVAL_REQUESTS,
            "RETRY_BACKOFF_SECONDS": RETRY_BACKOFF_SECONDS,
            "CONCURRENCY": 1,
            "ORDERING": "request_order ascending (symbol, calendar_year)",
        },
        "ONE_QUERY_PER_SYMBOL": {
            "REQUEST_N": scope["FORMAL_SYMBOL_N"],
            "ROLE": "ALTERNATIVE_UNVERIFIED_OPTIMIZATION",
            "FULL_RANGE_RESPONSE_UNCAPPED_PROVEN": False,
            "FORMAL_BASELINE": False,
        },
        "QUALITY_GATE": {
            "ALL_REQUESTS_COMPLETE_REQUIRED": True,
            "PROVIDER_FAILED_REQUEST_N_REQUIRED": 0,
            "DUPLICATE_PROVIDER_KEY_N_REQUIRED": 0,
            "INVALID_PROVIDER_ROW_N_REQUIRED": 0,
            "UNKNOWN_CASE_N_MAY_BE_NONZERO": True,
            "UNKNOWN_KEY_PASS": False,
        },
        "SAFETY": {
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "FULL_EXTRACTION_EXECUTED": False,
            "CANONICAL_WRITE_EXECUTED": False,
            "CANONICAL_BYTES_MUTATED": False,
            "R4A9_CHECKPOINT_MUTATED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRECLOSE_COMPLETE": False,
            "PRODUCTION": False,
            "FORWARD": False,
            "TRADEPLAN": False,
        },
        "AUTHOR_STATUS": "PASS_PENDING_INDEPENDENT_AUDIT",
        "TEST_RESULT": "PENDING_TARGETED_TESTS",
        "PY_COMPILE": "PENDING",
        "GIT_DIFF_CHECK": "PENDING",
    }


def report_markdown(report: dict[str, Any]) -> str:
    coverage = report
    rate = report["RATE_RETRY_CONTRACT"]
    safety = report["SAFETY"]
    lines = [
        f"# {TASK_NAME}",
        "",
        "Offline execution-plan freeze only. No BaoStock request, no TDX request, no extraction, no canonical write, and no R4A9 resume.",
        "",
        f"- BASE_HEAD: `{report['BASE_HEAD']}`",
        f"- FEASIBILITY_COMMIT: `{report['FEASIBILITY_COMMIT']}`",
        f"- INPUT_FILE_N / INPUT_MANIFEST_HASH: `{report['INPUT_FILE_N']}` / `{report['INPUT_MANIFEST_HASH']}`",
        f"- FORMAL_SYMBOL_N / FORMAL_IDENTITY_HASH: `{report['FORMAL_SYMBOL_N']}` / `{report['FORMAL_IDENTITY_HASH']}`",
        f"- EXECUTION_BASELINE: `{report['EXECUTION_BASELINE']}`",
        f"- FULL_REQUEST_N / FULL_REQUEST_MANIFEST_HASH: `{report['FULL_REQUEST_N']}` / `{report['FULL_REQUEST_MANIFEST_HASH']}`",
        f"- LIFECYCLE_SESSION_KEY_N: `{report['LIFECYCLE_SESSION_KEY_N']}`",
        f"- REQUEST_COVERAGE_MISSING_KEY_N / DUPLICATE_KEY_N: `{report['REQUEST_COVERAGE_MISSING_KEY_N']}` / `{report['REQUEST_COVERAGE_DUPLICATE_KEY_N']}`",
        "",
        "## Frozen contract",
        "",
        "`tradestatus=1` maps to `EXPECTED_BAR`; `tradestatus=0` maps to `NOT_EXPECTED_BAR`; provider row absence inside independent lifecycle maps to `UNKNOWN`; outside lifecycle maps to `NOT_EXPECTED_BAR` only through frozen identity. UNKNOWN is never PASS and never NOT_EXPECTED_BAR.",
        "",
        "## Request and checkpoint plan",
        "",
        "The formal baseline is one request per symbol/year intersection after clipping to `[max(list_date, 2016-01-01), min(delist_date, 2026-08-17)]`. It is ordered by `(symbol, calendar_year)` and covers each lifecycle exchange trading date exactly once. Current canonical gaps are not an input to request creation.",
        "",
        f"- MAX_RETRY / MAX_ATTEMPT_N: `{rate['MAX_RETRY']}` / `{rate['MAX_ATTEMPT_N']}`",
        f"- REQUEST_INTERVAL_SECONDS: `{rate['REQUEST_INTERVAL_SECONDS']}`",
        f"- BATCH_SIZE / CHECKPOINT_INTERVAL_REQUESTS: `{rate['BATCH_SIZE']}` / `{rate['CHECKPOINT_INTERVAL_REQUESTS']}`",
        "- Resume skips only hash-verified COMPLETE receipts; missing, FAILED, RUNNING, or corrupt receipts rerun.",
        "- One-query-per-symbol is retained only as an unverified 5,456-request optimization scenario.",
        "",
        "## Coverage",
        "",
        f"- LIFECYCLE_SESSION_KEY_N: `{coverage['LIFECYCLE_SESSION_KEY_N']}`",
        f"- OUTSIDE_LIFETIME_SESSION_KEY_N: `{coverage['OUTSIDE_LIFETIME_SESSION_KEY_N']}`",
        f"- REQUEST_COVERAGE_EXACT: `{coverage['REQUEST_COVERAGE_EXACT']}`",
        "",
        "## Safety",
        "",
    ]
    lines.extend(
        f"- {key}: `{str(value).lower() if isinstance(value, bool) else value}`" for key, value in safety.items()
    )
    lines.extend(
        [
            "",
            "## Verification",
            "",
            f"- AUTHOR_STATUS: `{report['AUTHOR_STATUS']}`",
            f"- TEST_RESULT: `{report['TEST_RESULT']}`",
            f"- PY_COMPILE: `{report['PY_COMPILE']}`",
            f"- GIT_DIFF_CHECK: `{report['GIT_DIFF_CHECK']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _require_repo_output(path: Path) -> None:
    if not path.resolve().is_relative_to(OUTPUT_DIR.resolve()):
        raise PlanError(f"OUTPUT_OUTSIDE_REPO_REPORTS:{path}")


def write_json(path: Path, payload: Any) -> None:
    _require_repo_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


def write_text(path: Path, payload: str) -> None:
    _require_repo_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def run_plan(data_root: Path, *, test_result: str = "PENDING_TARGETED_TESTS", py_compile_result: str = "PENDING", diff_check_result: str = "PENDING") -> dict[str, Any]:
    input_manifest = build_input_file_manifest(data_root)
    if input_manifest["INPUT_FILE_N"] != INPUT_FILE_N or input_manifest["INPUT_MANIFEST_HASH"] != INPUT_MANIFEST_HASH:
        raise PlanError("INPUT_MANIFEST_DRIFT")
    symbols, identity_source = load_formal_identity_symbols(data_root)
    instruments = load_instruments(data_root)
    scope = validate_formal_scope(symbols, instruments)
    calendar_dates = load_calendar(data_root)
    manifest, manifest_hash, coverage = build_full_request_manifest(
        symbols, instruments, calendar_dates, input_manifest
    )
    manifest_with_hash = {**manifest, "FULL_REQUEST_MANIFEST_HASH": manifest_hash}
    report = build_report(
        input_manifest=input_manifest,
        identity_source=identity_source,
        scope=scope,
        manifest_hash=manifest_hash,
        coverage={**coverage, **scope},
    )
    report["TEST_RESULT"] = test_result
    report["PY_COMPILE"] = py_compile_result
    report["GIT_DIFF_CHECK"] = diff_check_result
    write_json(MANIFEST_PATH, manifest_with_hash)
    write_json(REPORT_PATH, report)
    write_text(REPORT_MD_PATH, report_markdown(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--test-result", default="PENDING_TARGETED_TESTS")
    parser.add_argument("--py-compile", dest="py_compile_result", default="PENDING")
    parser.add_argument("--diff-check", dest="diff_check_result", default="PENDING")
    args = parser.parse_args(argv)
    try:
        report = run_plan(
            args.data_root,
            test_result=args.test_result,
            py_compile_result=args.py_compile_result,
            diff_check_result=args.diff_check_result,
        )
    except PlanError as exc:
        print(json.dumps({"AUTHOR_STATUS": "BLOCKED", "ERROR": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps({key: report[key] for key in (
        "FORMAL_SYMBOL_N",
        "FULL_REQUEST_N",
        "FULL_REQUEST_MANIFEST_HASH",
        "LIFECYCLE_SESSION_KEY_N",
        "REQUEST_COVERAGE_MISSING_KEY_N",
        "REQUEST_COVERAGE_DUPLICATE_KEY_N",
        "EXECUTION_BASELINE",
    )}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
