#!/usr/bin/env python3
"""Close the R3 session-authority key-universe provenance gap offline.

V01 froze a 48,345-request annual plan and a count-based coverage proof.  This
V01.1 layer binds that unchanged request-row baseline to three independently
serialized authorities:

* the exact sorted exchange trading-date set;
* the formal identity's lifecycle metadata;
* a streaming hash of every exact lifecycle session key.

The module is provider-free by construction.  It reads only local receipts,
instruments, trading-calendar parquet, and the frozen daily input manifest. It
does not import or call BaoStock/TDX and never writes the data root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
OUTPUT_DIR = REPO_ROOT / "reports" / "implementation"

BASE_HEAD = "2cf9abefbf6a2696f06fb85ba62cbc0e0f2c0dc8"
FEASIBILITY_COMMIT = "9d7baf69ccdaf323c3221a62cf0487378373e95f"
BRANCH = "codex/r3-full-session-completeness-authority-extraction-plan-v01-1"
WINDOW_START = date(2016, 1, 1)
WINDOW_END = date(2026, 8, 17)
FORMAL_SYMBOL_N = 5456
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
INPUT_FILE_N = 2580
INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"

PROVIDER = "baostock"
PROVIDER_RUNTIME = "baostock-0.9.3"
QUERY_FIELDS = "date,code,open,high,low,close,volume,amount,preclose,tradestatus"
QUERY_FREQUENCY = "d"
QUERY_ADJUSTFLAG = "3"
EXECUTION_BASELINE = "CALENDAR_YEAR_WINDOWED"

MAX_RETRY = 2
REQUEST_INTERVAL_SECONDS = 1.0
BATCH_SIZE = 50
CHECKPOINT_INTERVAL_REQUESTS = 1
CONCURRENCY = 1
RETRY_BACKOFF_SECONDS = [1.0, 2.0]

TASK_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1"
OLD_TASK_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01"
MANIFEST_NAME = f"{TASK_NAME}_REQUEST_MANIFEST.json"
LIFECYCLE_NAME = f"{TASK_NAME}_LIFECYCLE_MANIFEST.json"
TRADING_DATESET_NAME = f"{TASK_NAME}_TRADING_DATESET.json"
CALENDAR_FILES_NAME = f"{TASK_NAME}_TRADING_CALENDAR_FILE_MANIFEST.json"
REPORT_NAME = f"{TASK_NAME}.json"
REPORT_MD_NAME = f"{TASK_NAME}.md"
MANIFEST_PATH = OUTPUT_DIR / MANIFEST_NAME
LIFECYCLE_PATH = OUTPUT_DIR / LIFECYCLE_NAME
TRADING_DATESET_PATH = OUTPUT_DIR / TRADING_DATESET_NAME
CALENDAR_FILES_PATH = OUTPUT_DIR / CALENDAR_FILES_NAME
REPORT_PATH = OUTPUT_DIR / REPORT_NAME
REPORT_MD_PATH = OUTPUT_DIR / REPORT_MD_NAME
OLD_MANIFEST_PATH = OUTPUT_DIR / f"{OLD_TASK_NAME}_REQUEST_MANIFEST.json"

SESSION_KEY_SERIALIZATION = "UTF-8 bytes of sorted lines: symbol + '\\t' + YYYY-MM-DD + '\\n'"
LIFECYCLE_SERIALIZATION = "canonical JSON rows sorted by symbol; date fields are YYYY-MM-DD strings"
DATESET_SERIALIZATION = "canonical JSON array of sorted YYYY-MM-DD strings"
FILE_MANIFEST_SERIALIZATION = "canonical JSON rows sorted by relative_path with relative_path/file_size/sha256"

CHECKPOINT_STATES = ("PENDING", "RUNNING", "COMPLETE", "FAILED")
REQUIRED_RECEIPT_FIELDS = (
    "request_id",
    "request_order",
    "request_manifest_hash",
    "lifecycle_session_keyset_hash",
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
    """Fail-closed authority or plan error."""


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


def _one_day() -> timedelta:
    return timedelta(days=1)


def validate_formal_scope(symbols: list[str], instruments: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Enforce the frozen formal SH/SZ stock/CDR universe before planning."""

    if len(symbols) != len(set(symbols)):
        raise PlanError("FORMAL_IDENTITY_SYMBOL_DUPLICATE")
    missing = sorted(set(symbols) - set(instruments))
    if missing:
        raise PlanError(f"FORMAL_SYMBOL_MISSING_INSTRUMENT_METADATA:{missing[:5]}")
    bad_scope = [
        symbol
        for symbol in symbols
        if instruments[symbol].get("exchange") not in {"SH", "SZ"}
        or instruments[symbol].get("asset_type") not in {"stock", "cdr"}
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
    return {"INPUT_FILE_N": len(rows), "INPUT_MANIFEST_HASH": sha256_json(rows), "FILES": rows}


def build_trading_calendar_file_manifest(data_root: Path) -> dict[str, Any]:
    root = data_root / "curated" / "trading_calendar"
    files = sorted(path for path in root.rglob("*.parquet") if path.is_file())
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
        "TRADING_CALENDAR_FILE_N": len(rows),
        "TRADING_CALENDAR_FILE_MANIFEST_HASH": sha256_json(rows),
        "SERIALIZATION": FILE_MANIFEST_SERIALIZATION,
        "FILES": rows,
    }


def load_trading_dates(data_root: Path) -> list[date]:
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
    if len(dates) != len(set(dates)) or dates != sorted(dates):
        raise PlanError("TRADING_DATESET_NOT_SORTED_UNIQUE")
    if not dates:
        raise PlanError("TRADING_DATESET_EMPTY")
    return dates


def build_trading_dateset(dates: list[date]) -> tuple[dict[str, Any], str]:
    if dates != sorted(set(dates)):
        raise PlanError("TRADING_DATESET_NOT_SORTED_UNIQUE")
    values = [value.isoformat() for value in dates]
    payload = {
        "TRADING_DATE_N": len(values),
        "WINDOW_START": WINDOW_START,
        "WINDOW_END": WINDOW_END,
        "SERIALIZATION": DATESET_SERIALIZATION,
        "TRADE_DATES": values,
    }
    # The hash binds the exact list, not the metadata wrapper.
    digest = sha256_json(values)
    payload["TRADING_DATESET_HASH"] = digest
    return payload, digest


def load_instruments(data_root: Path) -> dict[str, dict[str, Any]]:
    frame = pl.read_parquet(data_root / "curated" / "instruments" / "part-merged.parquet").select(
        ["symbol", "exchange", "asset_type", "list_date", "delist_date"]
    )
    result: dict[str, dict[str, Any]] = {}
    for row in frame.iter_rows(named=True):
        symbol = str(row["symbol"])
        if symbol in result:
            raise PlanError(f"DUPLICATE_INSTRUMENT_SYMBOL:{symbol}")
        result[symbol] = {
            "symbol": symbol,
            "exchange": str(row["exchange"]),
            "asset_type": str(row["asset_type"]),
            "list_date": parse_date(row.get("list_date")),
            "delist_date": parse_date(row.get("delist_date")),
        }
    return result


def lifecycle_bounds(meta: dict[str, Any]) -> tuple[date, date] | None:
    start = max(meta.get("list_date") or WINDOW_START, WINDOW_START)
    end = min(meta.get("delist_date") or WINDOW_END, WINDOW_END)
    return (start, end) if start <= end else None


def build_lifecycle_manifest(
    symbols: list[str], instruments: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    for symbol in sorted(symbols):
        meta = instruments.get(symbol)
        if meta is None:
            raise PlanError(f"FORMAL_SYMBOL_MISSING_INSTRUMENT_METADATA:{symbol}")
        if meta["exchange"] not in {"SH", "SZ"} or meta["asset_type"] not in {"stock", "cdr"}:
            raise PlanError(f"FORMAL_SCOPE_NOT_SHSZ_STOCK_CDR:{symbol}")
        bounds = lifecycle_bounds(meta)
        rows.append(
            {
                "symbol": symbol,
                "list_date": meta.get("list_date"),
                "delist_date": meta.get("delist_date"),
                "effective_start": None if bounds is None else bounds[0],
                "effective_end": None if bounds is None else bounds[1],
            }
        )
    if len(rows) != FORMAL_SYMBOL_N or identity_hash([row["symbol"] for row in rows]) != FORMAL_IDENTITY_HASH:
        raise PlanError("LIFECYCLE_FORMAL_SCOPE_MISMATCH")
    return rows, lifecycle_authority_hash(rows)


def lifecycle_authority_hash(rows: list[dict[str, Any]]) -> str:
    """Hash the exact compact lifecycle rows using the frozen serialization."""

    return sha256_json(rows)


def session_keyset_hash(keys: Iterable[tuple[str, date]]) -> str:
    """Hash sorted exact session keys as UTF-8 ``symbol<TAB>date<LF>`` lines."""

    digest = hashlib.sha256()
    for symbol, trading_date in keys:
        digest.update(f"{symbol}\t{trading_date.isoformat()}\n".encode("utf-8"))
    return digest.hexdigest()


def _serialize_request_rows(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return json.loads(canonical_json_bytes(requests).decode("utf-8"))


def load_previous_baseline_rows() -> list[dict[str, Any]]:
    try:
        payload = json.loads(OLD_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise PlanError(f"V01_REQUEST_MANIFEST_UNREADABLE:{exc}") from exc
    rows = payload.get("requests")
    if not isinstance(rows, list):
        raise PlanError("V01_REQUEST_MANIFEST_REQUESTS_MISSING")
    return rows


def _import_v01_plan_module() -> Any:
    sys.path.insert(0, str(REPO_ROOT / "tools" / "audits"))
    import r3_full_session_completeness_authority_extraction_plan_v01 as v01  # type: ignore

    return v01


def build_request_manifest_v01_1(
    symbols: list[str],
    instruments: dict[str, dict[str, Any]],
    trading_dates: list[date],
    input_manifest: dict[str, Any],
    trading_dateset_hash: str,
    lifecycle_hash: str,
    calendar_file_manifest_hash: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Reuse V01's request rows and add exact authority bindings."""

    v01 = _import_v01_plan_module()
    old_manifest, _old_hash, old_coverage = v01.build_full_request_manifest(
        symbols, instruments, trading_dates, input_manifest
    )
    request_rows = _serialize_request_rows(old_manifest["requests"])
    old_rows = load_previous_baseline_rows()
    if request_rows != old_rows:
        raise PlanError("BASELINE_REQUEST_PLAN_NOT_EQUIVALENT")
    manifest = {
        "TASK": TASK_NAME,
        "BASE_HEAD": BASE_HEAD,
        "FEASIBILITY_COMMIT": FEASIBILITY_COMMIT,
        "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
        "FORMAL_SYMBOL_N": len(symbols),
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "EXECUTION_BASELINE": EXECUTION_BASELINE,
        "TRADING_DATE_N": len(trading_dates),
        "TRADING_DATESET_HASH": trading_dateset_hash,
        "LIFECYCLE_AUTHORITY_HASH": lifecycle_hash,
        "LIFECYCLE_SESSION_KEYSET_HASH": "BOUND_AFTER_EXACT_KEY_SCAN",
        "TRADING_CALENDAR_FILE_MANIFEST_HASH": calendar_file_manifest_hash,
        "authority_contract": {
            "tradestatus_1": "EXPECTED_BAR",
            "tradestatus_0": "NOT_EXPECTED_BAR",
            "row_absent_inside_lifetime": "UNKNOWN",
            "outside_lifecycle": "NOT_EXPECTED_BAR via frozen identity",
            "UNKNOWN_NOT_PASS": True,
            "UNKNOWN_NOT_NOT_EXPECTED_BAR": True,
        },
        "provider_contract": {
            "provider": PROVIDER,
            "runtime": PROVIDER_RUNTIME,
            "fields": QUERY_FIELDS,
            "frequency": QUERY_FREQUENCY,
            "adjustflag": QUERY_ADJUSTFLAG,
        },
        "requests": request_rows,
    }
    # The keyset hash is computed before the request hash; binding it after
    # construction makes the final manifest hash deterministic and closed.
    manifest["LIFECYCLE_SESSION_KEYSET_HASH"] = ""
    return manifest, sha256_json(manifest), {
        "V01_REQUEST_N": len(old_rows),
        "V01_REQUEST_MANIFEST_HASH": _old_hash,
        "V01_COVERAGE": old_coverage,
    }


def exact_session_key_scan(
    symbols: list[str],
    instruments: dict[str, dict[str, Any]],
    trading_dates: list[date],
    requests: list[dict[str, Any]],
) -> tuple[str, dict[str, int]]:
    """Stream exact lifecycle keys and assign each to exactly one request."""

    request_by_symbol_year: dict[tuple[str, int], list[dict[str, Any]]] = {}
    request_ids: set[str] = set()
    request_orders: set[int] = set()
    for request in requests:
        request_id = str(request.get("request_id"))
        order = int(request.get("request_order"))
        if request_id in request_ids or order in request_orders:
            raise PlanError("REQUEST_ID_OR_ORDER_DUPLICATE")
        request_ids.add(request_id)
        request_orders.add(order)
        symbol = str(request.get("symbol"))
        year = int(request.get("calendar_year"))
        request_by_symbol_year.setdefault((symbol, year), []).append(request)
    if request_orders != set(range(1, len(requests) + 1)):
        raise PlanError("REQUEST_ORDER_NOT_CONTIGUOUS")

    digest = hashlib.sha256()
    lifecycle_key_n = 0
    missing_n = 0
    duplicate_n = 0
    for symbol in sorted(symbols):
        bounds = lifecycle_bounds(instruments[symbol])
        if bounds is None:
            continue
        start, end = bounds
        lo = bisect_left(trading_dates, start)
        hi = bisect_right(trading_dates, end)
        for trading_date in trading_dates[lo:hi]:
            digest.update(f"{symbol}\t{trading_date.isoformat()}\n".encode("utf-8"))
            lifecycle_key_n += 1
            candidates = request_by_symbol_year.get((symbol, trading_date.year), [])
            matching = [
                request
                for request in candidates
                if parse_date(request["start_date"]) <= trading_date <= parse_date(request["end_date"])
            ]
            if not matching:
                missing_n += 1
            elif len(matching) > 1:
                duplicate_n += len(matching) - 1
    return digest.hexdigest(), {
        "LIFECYCLE_SESSION_KEY_N": lifecycle_key_n,
        "REQUEST_COVERAGE_MISSING_KEY_N": missing_n,
        "REQUEST_COVERAGE_DUPLICATE_KEY_N": duplicate_n,
    }


def verify_request_row_counts(
    requests: list[dict[str, Any]],
    symbols: list[str],
    instruments: dict[str, dict[str, Any]],
    trading_dates: list[date],
) -> None:
    if len(requests) != 48345:
        raise PlanError(f"FULL_REQUEST_N_CHANGED:{len(requests)}")
    formal = set(symbols)
    for request in requests:
        symbol = str(request.get("symbol"))
        if symbol not in formal:
            raise PlanError(f"REQUEST_SYMBOL_OUTSIDE_FORMAL_SCOPE:{symbol}")
        bounds = lifecycle_bounds(instruments[symbol])
        if bounds is None:
            raise PlanError(f"REQUEST_FOR_EMPTY_LIFETIME:{symbol}")
        start = parse_date(request.get("start_date"))
        end = parse_date(request.get("end_date"))
        expected = 0 if start is None or end is None else calendar_count(trading_dates, start, end)
        if request.get("calendar_trading_date_n") != expected:
            raise PlanError(f"REQUEST_DATE_COUNT_MISMATCH:{request.get('request_id')}")
        if request.get("adjustflag") != QUERY_ADJUSTFLAG or request.get("frequency") != QUERY_FREQUENCY:
            raise PlanError(f"REQUEST_CONTRACT_MISMATCH:{request.get('request_id')}")


def calendar_count(trading_dates: list[date], start: date, end: date) -> int:
    return max(0, bisect_right(trading_dates, end) - bisect_left(trading_dates, start))


def classify_session_key(provider_row: dict[str, Any] | None, *, inside_lifetime: bool) -> dict[str, str]:
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


def validate_future_extraction_authority(
    *,
    input_manifest: dict[str, Any],
    trading_dateset_hash: str,
    lifecycle_hash: str,
    formal_identity_hash: str,
    request_manifest_hash: str,
    frozen_request_manifest_hash: str,
    expected_trading_dateset_hash: str,
    expected_lifecycle_hash: str,
    expected_formal_identity_hash: str,
) -> dict[str, Any]:
    failures: list[str] = []
    if (
        input_manifest.get("INPUT_FILE_N") != INPUT_FILE_N
        or input_manifest.get("INPUT_MANIFEST_HASH") != INPUT_MANIFEST_HASH
    ):
        failures.append("DAILY_INPUT_MANIFEST_HASH_DRIFT")
    if trading_dateset_hash != expected_trading_dateset_hash:
        failures.append("TRADING_DATESET_HASH_DRIFT")
    if lifecycle_hash != expected_lifecycle_hash:
        failures.append("LIFECYCLE_AUTHORITY_HASH_DRIFT")
    if (
        formal_identity_hash != FORMAL_IDENTITY_HASH
        or formal_identity_hash != expected_formal_identity_hash
        or expected_formal_identity_hash != FORMAL_IDENTITY_HASH
    ):
        failures.append("FORMAL_IDENTITY_HASH_DRIFT")
    if request_manifest_hash != frozen_request_manifest_hash:
        failures.append("FULL_REQUEST_MANIFEST_HASH_DRIFT")
    if failures:
        raise PlanError("EXTRACTION_AUTHORITY_DRIFT:" + ",".join(failures))
    return {"AUTHORITY_GATE_PASS": True, "NETWORK_REQUEST_N": 0, "FAILURES": []}


def is_valid_complete_receipt_v01_1(
    request: dict[str, Any],
    receipt: dict[str, Any] | None,
    *,
    request_manifest_hash: str,
    lifecycle_session_keyset_hash: str,
    raw_payload: Any | None,
    normalized_payload: Any | None,
) -> bool:
    if not isinstance(receipt, dict) or receipt.get("lifecycle_session_keyset_hash") != lifecycle_session_keyset_hash:
        return False
    if any(field not in receipt for field in REQUIRED_RECEIPT_FIELDS):
        return False
    if receipt.get("request_manifest_hash") != request_manifest_hash:
        return False
    if raw_payload is None or normalized_payload is None:
        return False
    if receipt.get("raw_sha256") != sha256_json(raw_payload):
        return False
    if receipt.get("normalized_sha256") != sha256_json(normalized_payload):
        return False
    if receipt.get("status") != "COMPLETE" or str(receipt.get("provider_error_code")) != "0":
        return False
    return (
        receipt.get("request_id") == request.get("request_id")
        and receipt.get("request_order") == request.get("request_order")
        and parse_int(receipt.get("attempt_n")) is not None
        and parse_int(receipt.get("attempt_n")) >= 1
        and parse_int(receipt.get("raw_row_n")) is not None
        and parse_int(receipt.get("raw_row_n")) >= 0
        and parse_int(receipt.get("normalized_row_n")) is not None
        and parse_int(receipt.get("normalized_row_n")) >= 0
        and bool(str(receipt.get("completed_at", "")).strip())
    )


def quality_gate(request_receipts: list[dict[str, Any]], *, unknown_case_n: int) -> dict[str, Any]:
    """Describe the future extraction gate while preserving UNKNOWN cases."""

    def counter(row: dict[str, Any], field: str) -> int:
        value = parse_int(row.get(field, 0))
        if value is None or value < 0:
            raise PlanError(f"INVALID_QUALITY_GATE_COUNTER:{field}")
        return value

    provider_failed_n = sum(str(row.get("provider_error_code")) != "0" for row in request_receipts)
    duplicate_n = sum(counter(row, "duplicate_provider_key_n") for row in request_receipts)
    invalid_n = sum(counter(row, "invalid_provider_row_n") for row in request_receipts)
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
        "QUALITY_GATE_READY_FOR_RECONCILIATION": (
            all_complete and provider_failed_n == 0 and duplicate_n == 0 and invalid_n == 0
        ),
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
            "lifecycle_session_keyset_hash equals current exact keyset hash",
            "raw_sha256 and normalized_sha256 recompute exactly from payloads",
            "attempt_n >= 1 and completed_at is non-empty",
        ],
        "resume_rule": "skip only a receipt satisfying the complete predicate; missing, FAILED, RUNNING, or corrupt receipts rerun",
        "checkpoint_write_frequency": "after every request; batch boundary also observed",
    }


def build_report(
    *,
    input_manifest: dict[str, Any],
    identity_source: dict[str, str],
    lifecycle_rows: list[dict[str, Any]],
    trading_dateset: dict[str, Any],
    calendar_file_manifest: dict[str, Any],
    lifecycle_hash: str,
    keyset_hash: str,
    key_counts: dict[str, int],
    request_manifest_hash: str,
    baseline_info: dict[str, Any],
    test_result: str,
    py_compile_result: str,
    diff_check_result: str,
) -> dict[str, Any]:
    grid_n = FORMAL_SYMBOL_N * int(trading_dateset["TRADING_DATE_N"])
    lifecycle_key_n = key_counts["LIFECYCLE_SESSION_KEY_N"]
    outside_n = grid_n - lifecycle_key_n
    if outside_n != 3179251 or lifecycle_key_n != 10897229:
        raise PlanError("SESSION_KEY_COUNT_CONTRACT_MISMATCH")
    report = {
        "REPORT": TASK_NAME,
        "AUTHOR_STATUS": "PASS_PENDING_INDEPENDENT_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "FEASIBILITY_COMMIT": FEASIBILITY_COMMIT,
        "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
        "FORMAL_SYMBOL_N": FORMAL_SYMBOL_N,
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "FORMAL_IDENTITY_SOURCE": identity_source,
        "EXECUTION_BASELINE": EXECUTION_BASELINE,
        "TRADING_DATE_N": trading_dateset["TRADING_DATE_N"],
        "TRADING_DATESET_HASH": trading_dateset["TRADING_DATESET_HASH"],
        "TRADING_DATESET_SERIALIZATION": DATESET_SERIALIZATION,
        "TRADING_DATESET_PATH": str(TRADING_DATESET_PATH),
        "TRADING_CALENDAR_FILE_N": calendar_file_manifest["TRADING_CALENDAR_FILE_N"],
        "TRADING_CALENDAR_FILE_MANIFEST_HASH": calendar_file_manifest["TRADING_CALENDAR_FILE_MANIFEST_HASH"],
        "TRADING_CALENDAR_FILE_MANIFEST_SERIALIZATION": FILE_MANIFEST_SERIALIZATION,
        "TRADING_CALENDAR_FILE_MANIFEST_PATH": str(CALENDAR_FILES_PATH),
        "LIFECYCLE_AUTHORITY_HASH": lifecycle_hash,
        "LIFECYCLE_AUTHORITY_SERIALIZATION": LIFECYCLE_SERIALIZATION,
        "LIFECYCLE_AUTHORITY_PATH": str(LIFECYCLE_PATH),
        "LIFECYCLE_SESSION_KEY_N": lifecycle_key_n,
        "LIFECYCLE_SESSION_KEYSET_HASH": keyset_hash,
        "LIFECYCLE_SESSION_KEYSET_SERIALIZATION": SESSION_KEY_SERIALIZATION,
        "OUTSIDE_LIFETIME_SESSION_KEY_N": outside_n,
        "ALL_SYMBOL_CALENDAR_GRID_KEY_N": grid_n,
        "LIFECYCLE_PLUS_OUTSIDE_EQUALS_GRID": lifecycle_key_n + outside_n == grid_n,
        "FULL_REQUEST_N": baseline_info["V01_REQUEST_N"],
        "FULL_REQUEST_MANIFEST_HASH": request_manifest_hash,
        "FULL_REQUEST_MANIFEST_PATH": str(MANIFEST_PATH),
        "BASELINE_REQUEST_PLAN_EQUIVALENT": True,
        "V01_REQUEST_MANIFEST_HASH": baseline_info["V01_REQUEST_MANIFEST_HASH"],
        "REQUEST_COVERAGE_MISSING_KEY_N": key_counts["REQUEST_COVERAGE_MISSING_KEY_N"],
        "REQUEST_COVERAGE_DUPLICATE_KEY_N": key_counts["REQUEST_COVERAGE_DUPLICATE_KEY_N"],
        "REQUEST_COVERAGE_PROOF": "exact streaming symbol/date key assignment, not count-only",
        "AUTHORITY_CONTRACT": {
            "tradestatus_1": "EXPECTED_BAR",
            "tradestatus_0": "NOT_EXPECTED_BAR",
            "row_absent_inside_lifetime": "UNKNOWN",
            "outside_lifecycle": "NOT_EXPECTED_BAR via frozen identity",
            "UNKNOWN_NOT_PASS": True,
            "UNKNOWN_NOT_NOT_EXPECTED_BAR": True,
        },
        "FUTURE_EXTRACTION_INPUT_GATE": {
            "DAILY_INPUT_MANIFEST_HASH": INPUT_MANIFEST_HASH,
            "TRADING_DATESET_HASH": trading_dateset["TRADING_DATESET_HASH"],
            "LIFECYCLE_AUTHORITY_HASH": lifecycle_hash,
            "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
            "FULL_REQUEST_MANIFEST_HASH": request_manifest_hash,
            "DRIFT_ACTION": "FAIL CLOSED; NETWORK_REQUEST_N=0",
        },
        "CHECKPOINT_CONTRACT": checkpoint_contract(),
        "QUALITY_GATE": {
            "ALL_REQUESTS_COMPLETE_REQUIRED": True,
            "PROVIDER_FAILED_REQUEST_N_REQUIRED": 0,
            "DUPLICATE_PROVIDER_KEY_N_REQUIRED": 0,
            "INVALID_PROVIDER_ROW_N_REQUIRED": 0,
            "UNKNOWN_CASE_N_MAY_BE_NONZERO": True,
            "UNKNOWN_KEY_PASS": False,
        },
        "RATE_RETRY_CONTRACT": {
            "MAX_RETRY": MAX_RETRY,
            "MAX_ATTEMPT_N": MAX_RETRY + 1,
            "REQUEST_INTERVAL_SECONDS": REQUEST_INTERVAL_SECONDS,
            "BATCH_SIZE": BATCH_SIZE,
            "CHECKPOINT_INTERVAL_REQUESTS": CHECKPOINT_INTERVAL_REQUESTS,
            "CONCURRENCY": CONCURRENCY,
            "RETRY_BACKOFF_SECONDS": RETRY_BACKOFF_SECONDS,
        },
        "ONE_QUERY_PER_SYMBOL": {
            "REQUEST_N": FORMAL_SYMBOL_N,
            "ROLE": "ALTERNATIVE_UNVERIFIED_OPTIMIZATION",
            "FULL_RANGE_RESPONSE_UNCAPPED_PROVEN": False,
            "FORMAL_BASELINE": False,
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
        "TEST_RESULT": test_result,
        "PY_COMPILE": py_compile_result,
        "GIT_DIFF_CHECK": diff_check_result,
    }
    return report


def report_markdown(report: dict[str, Any]) -> str:
    rate = report["RATE_RETRY_CONTRACT"]
    safety = report["SAFETY"]
    lines = [
        f"# {TASK_NAME}",
        "",
        "Offline provenance/key-universe closure only. No provider request, extraction, canonical write, or R4A9 resume.",
        "",
        f"- AUTHOR_STATUS: `{report['AUTHOR_STATUS']}`",
        f"- BASE_HEAD: `{report['BASE_HEAD']}`",
        f"- FEASIBILITY_COMMIT: `{report['FEASIBILITY_COMMIT']}`",
        f"- INPUT_FILE_N / INPUT_MANIFEST_HASH: `{report['INPUT_FILE_N']}` / `{report['INPUT_MANIFEST_HASH']}`",
        f"- FORMAL_SYMBOL_N / FORMAL_IDENTITY_HASH: `{report['FORMAL_SYMBOL_N']}` / `{report['FORMAL_IDENTITY_HASH']}`",
        f"- TRADING_DATE_N / TRADING_DATESET_HASH: `{report['TRADING_DATE_N']}` / `{report['TRADING_DATESET_HASH']}`",
        f"- TRADING_CALENDAR_FILE_N / FILE_MANIFEST_HASH: `{report['TRADING_CALENDAR_FILE_N']}` / `{report['TRADING_CALENDAR_FILE_MANIFEST_HASH']}`",
        f"- LIFECYCLE_AUTHORITY_HASH: `{report['LIFECYCLE_AUTHORITY_HASH']}`",
        f"- LIFECYCLE_SESSION_KEY_N / KEYSET_HASH: `{report['LIFECYCLE_SESSION_KEY_N']}` / `{report['LIFECYCLE_SESSION_KEYSET_HASH']}`",
        f"- OUTSIDE_LIFETIME_SESSION_KEY_N: `{report['OUTSIDE_LIFETIME_SESSION_KEY_N']}`",
        f"- FULL_REQUEST_N / FULL_REQUEST_MANIFEST_HASH: `{report['FULL_REQUEST_N']}` / `{report['FULL_REQUEST_MANIFEST_HASH']}`",
        f"- REQUEST_COVERAGE_MISSING_KEY_N / DUPLICATE_KEY_N: `{report['REQUEST_COVERAGE_MISSING_KEY_N']}` / `{report['REQUEST_COVERAGE_DUPLICATE_KEY_N']}`",
        "",
        "## Frozen authority contract",
        "",
        "`tradestatus=1` -> `EXPECTED_BAR`; `tradestatus=0` -> `NOT_EXPECTED_BAR`; row absent inside lifecycle -> `UNKNOWN`; outside lifecycle -> `NOT_EXPECTED_BAR` through frozen identity. UNKNOWN is neither PASS nor NOT_EXPECTED_BAR.",
        "",
        "The session-key hash is a streaming SHA-256 over exact sorted `symbol<TAB>YYYY-MM-DD<LF>` lines. The exact key scan assigns every lifecycle key to one request window; it does not infer coverage from row-count sums.",
        "",
        "## Request/checkpoint contract",
        "",
        f"- EXECUTION_BASELINE: `{report['EXECUTION_BASELINE']}`",
        f"- MAX_RETRY / REQUEST_INTERVAL_SECONDS / BATCH_SIZE / CHECKPOINT_INTERVAL_REQUESTS / CONCURRENCY: `{rate['MAX_RETRY']}` / `{rate['REQUEST_INTERVAL_SECONDS']}` / `{rate['BATCH_SIZE']}` / `{rate['CHECKPOINT_INTERVAL_REQUESTS']}` / `{rate['CONCURRENCY']}`",
        "- Missing, FAILED, RUNNING, or corrupt receipts rerun; only hash-verified COMPLETE receipts may be skipped.",
        "- One-query-per-symbol remains an unverified optimization and is not the V01.1 baseline.",
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
            f"- TEST_RESULT: `{report['TEST_RESULT']}`",
            f"- py_compile: `{report['PY_COMPILE']}`",
            f"- git diff --check: `{report['GIT_DIFF_CHECK']}`",
            "",
        ]
    )
    return "\n".join(lines)


def run_plan(
    data_root: Path,
    *,
    test_result: str = "PENDING_TARGETED_TESTS",
    py_compile_result: str = "PENDING",
    diff_check_result: str = "PENDING",
) -> dict[str, Any]:
    input_manifest = build_input_file_manifest(data_root)
    if input_manifest["INPUT_FILE_N"] != INPUT_FILE_N or input_manifest["INPUT_MANIFEST_HASH"] != INPUT_MANIFEST_HASH:
        raise PlanError("INPUT_MANIFEST_DRIFT")
    symbols, identity_source = load_formal_identity_symbols(data_root)
    instruments = load_instruments(data_root)
    validate_formal_scope(symbols, instruments)
    lifecycle_rows, lifecycle_hash = build_lifecycle_manifest(symbols, instruments)
    trading_dates = load_trading_dates(data_root)
    trading_dateset, trading_dateset_hash = build_trading_dateset(trading_dates)
    calendar_file_manifest = build_trading_calendar_file_manifest(data_root)
    request_manifest, _unused_hash, baseline_info = build_request_manifest_v01_1(
        symbols,
        instruments,
        trading_dates,
        input_manifest,
        trading_dateset_hash,
        lifecycle_hash,
        calendar_file_manifest["TRADING_CALENDAR_FILE_MANIFEST_HASH"],
    )
    verify_request_row_counts(request_manifest["requests"], symbols, instruments, trading_dates)
    keyset_hash, key_counts = exact_session_key_scan(
        symbols, instruments, trading_dates, request_manifest["requests"]
    )
    request_manifest["LIFECYCLE_SESSION_KEYSET_HASH"] = keyset_hash
    request_manifest_hash = sha256_json(request_manifest)
    report = build_report(
        input_manifest=input_manifest,
        identity_source=identity_source,
        lifecycle_rows=lifecycle_rows,
        trading_dateset={**trading_dateset, "TRADING_DATESET_HASH": trading_dateset_hash},
        calendar_file_manifest=calendar_file_manifest,
        lifecycle_hash=lifecycle_hash,
        keyset_hash=keyset_hash,
        key_counts=key_counts,
        request_manifest_hash=request_manifest_hash,
        baseline_info=baseline_info,
        test_result=test_result,
        py_compile_result=py_compile_result,
        diff_check_result=diff_check_result,
    )
    write_json(MANIFEST_PATH, {**request_manifest, "FULL_REQUEST_MANIFEST_HASH": request_manifest_hash})
    write_json(
        LIFECYCLE_PATH,
        {
            "TASK": TASK_NAME,
            "FORMAL_SYMBOL_N": FORMAL_SYMBOL_N,
            "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
            "SERIALIZATION": LIFECYCLE_SERIALIZATION,
            "LIFECYCLE_AUTHORITY_HASH": lifecycle_hash,
            "ROWS": lifecycle_rows,
        },
    )
    write_json(TRADING_DATESET_PATH, trading_dateset)
    write_json(CALENDAR_FILES_PATH, calendar_file_manifest)
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
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "TRADING_DATE_N",
                    "TRADING_DATESET_HASH",
                    "TRADING_CALENDAR_FILE_MANIFEST_HASH",
                    "FORMAL_SYMBOL_N",
                    "LIFECYCLE_AUTHORITY_HASH",
                    "LIFECYCLE_SESSION_KEY_N",
                    "LIFECYCLE_SESSION_KEYSET_HASH",
                    "OUTSIDE_LIFETIME_SESSION_KEY_N",
                    "FULL_REQUEST_N",
                    "FULL_REQUEST_MANIFEST_HASH",
                    "REQUEST_COVERAGE_MISSING_KEY_N",
                    "REQUEST_COVERAGE_DUPLICATE_KEY_N",
                )
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
