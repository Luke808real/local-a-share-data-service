#!/usr/bin/env python3
"""Run the bounded R3 full-session authority extraction canary.

The engine is deliberately restricted to a deterministic 100-request canary
derived from the committed V01.1 request manifest.  Its only provider boundary
is the BaoStock query in :func:`fetch_one_request`; the preflight, staging
guard, receipt validation, checkpointing, normalization, and quality gate are
provider-independent and fail closed.

No canonical dataset is written.  The only data-root writes are under
``<data-root>/staging/r3_full_session_completeness_authority_v01``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as importlib_metadata
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
OUTPUT_DIR = REPO_ROOT / "reports" / "implementation"
sys.path.insert(0, str(REPO_ROOT / "tools" / "audits"))

TASK_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_CANARY_V01"
BRANCH = "codex/r3-full-session-completeness-authority-extraction-canary-v01"
BASE_HEAD = "c9b1fc7bd99d9a7c20df350efeb8fd7f88714321"
PLAN_COMMIT = BASE_HEAD

PLAN_REPORT_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1.json"
PLAN_REQUEST_MANIFEST_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1_REQUEST_MANIFEST.json"
PLAN_LIFECYCLE_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1_LIFECYCLE_MANIFEST.json"
PLAN_DATESET_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1_TRADING_DATESET.json"
PLAN_CALENDAR_FILES_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1_TRADING_CALENDAR_FILE_MANIFEST.json"
FEASIBILITY_REQUEST_MANIFEST_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_FEASIBILITY_V01_REQUEST_MANIFEST.json"

DAILY_INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
TRADING_DATESET_HASH = "51d80929eb493d83b709cd667e44809d285bb3e61e7ebfab87e6b677d61fc72d"
LIFECYCLE_AUTHORITY_HASH = "9987c05d2b424bd667e9ff339ca7ef9c712fb42ac9b781a27b72b2ee03337c93"
LIFECYCLE_SESSION_KEYSET_HASH = "eacb645dc8ac6273458fe3a59ea46b6be5072a234f0acf3fabbe2dd7463f2a65"
FULL_REQUEST_N = 48_345
FULL_REQUEST_MANIFEST_HASH = "4654e4282d5191cf680dc6b539025c816bc4d21028a4f2e200230209348412ed"
FORMAL_SYMBOL_N = 5_456
TRADING_DATE_N = 2_580
LIFECYCLE_SESSION_KEY_N = 10_897_229

WINDOW_START = date(2016, 1, 1)
WINDOW_END = date(2026, 8, 17)
PROVIDER = "baostock"
PROVIDER_VERSION = "0.9.3"
PROVIDER_RUNTIME = "baostock-0.9.3"
QUERY_FIELDS = "date,code,open,high,low,close,volume,amount,preclose,tradestatus"
QUERY_FREQUENCY = "d"
QUERY_ADJUSTFLAG = "3"

CANARY_REQUEST_N = 100
MAX_RETRY = 2
MAX_ATTEMPT_N = MAX_RETRY + 1
REQUEST_INTERVAL_SECONDS = 1.0
CONCURRENCY = 1
CHECKPOINT_INTERVAL = 1
RETRY_BACKOFF_SECONDS = (1.0, 2.0)
STAGING_DIRNAME = "r3_full_session_completeness_authority_v01"

CANARY_MANIFEST_NAME = f"{TASK_NAME}_REQUEST_MANIFEST.json"
CANARY_EXECUTION_RECEIPT_NAME = f"{TASK_NAME}_EXECUTION_RECEIPT.json"
CANARY_QUALITY_REPORT_NAME = f"{TASK_NAME}_QUALITY_REPORT.json"
REPORT_NAME = f"{TASK_NAME}.json"
REPORT_MD_NAME = f"{TASK_NAME}.md"

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


class CanaryError(RuntimeError):
    """Fail-closed canary, authority, provider, or staging error."""


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CanaryError(f"FILE_READ_FAILED:{path}") from exc
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise CanaryError(f"JSON_ARTIFACT_UNREADABLE:{path}") from exc


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
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
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CanaryError(message)


def _plan_module() -> Any:
    try:
        import r3_full_session_completeness_authority_extraction_plan_v01_1 as plan  # type: ignore
    except Exception as exc:  # pragma: no cover - import environment failure
        raise CanaryError("V01_1_PLAN_IMPORT_FAILED") from exc
    return plan


def require_isolated_staging_root(data_root: Path, stage_root: Path) -> Path:
    """Resolve and enforce the one writable data-root staging directory."""

    data_input = Path(data_root).expanduser()
    stage_input = Path(stage_root).expanduser()
    if not data_input.is_absolute() or not stage_input.is_absolute():
        raise CanaryError("ISOLATED_STAGE_ROOT_NOT_ABSOLUTE")
    if not data_input.is_dir():
        raise CanaryError("DATA_ROOT_NOT_DIRECTORY")
    data_resolved = data_input.resolve(strict=True)
    stage_resolved = stage_input.resolve(strict=False)
    expected = data_resolved / "staging" / STAGING_DIRNAME
    if data_input != data_resolved:
        raise CanaryError("DATA_ROOT_SYMLINK_OR_TRAVERSAL")
    if stage_input != stage_resolved or stage_resolved != expected:
        raise CanaryError("ISOLATED_STAGE_ROOT_FORBIDDEN")
    staging_parent = data_resolved / "staging"
    if staging_parent.is_symlink() or stage_input.is_symlink():
        raise CanaryError("ISOLATED_STAGE_ROOT_SYMLINK")
    if (data_resolved / "curated").is_symlink():
        raise CanaryError("CURATED_ROOT_SYMLINK")
    return stage_resolved


def _safe_stage_path(data_root: Path, stage_root: Path, relative_path: str) -> Path:
    root = require_isolated_staging_root(data_root, stage_root)
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise CanaryError("STAGING_OUTPUT_PATH_TRAVERSAL")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise CanaryError(f"STAGING_OUTPUT_SYMLINK_COMPONENT:{current}")
    resolved = (root / relative).resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise CanaryError("STAGING_OUTPUT_PATH_ESCAPE")
    return resolved


def _safe_repo_path(repo_root: Path, relative_path: str) -> Path:
    output_root = (repo_root / "reports" / "implementation").resolve(strict=True)
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise CanaryError("REPO_OUTPUT_PATH_TRAVERSAL")
    path = output_root / relative
    if path.resolve(strict=False).parent != output_root:
        raise CanaryError("REPO_OUTPUT_PATH_ESCAPE")
    return path


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise CanaryError(f"TEMP_OUTPUT_ALREADY_EXISTS:{temporary}")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise CanaryError(f"ATOMIC_OUTPUT_WRITE_FAILED:{path}") from exc


def write_stage_json(data_root: Path, stage_root: Path, relative_path: str, payload: Any) -> str:
    path = _safe_stage_path(data_root, stage_root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(payload)
    _atomic_write(path, data)
    return sha256_bytes(data)


def write_repo_json(repo_root: Path, relative_path: str, payload: Any) -> str:
    path = _safe_repo_path(repo_root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(payload) + b"\n"
    path.write_bytes(data)
    return sha256_bytes(data)


def write_repo_text(repo_root: Path, relative_path: str, content: str) -> str:
    path = _safe_repo_path(repo_root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def _load_and_verify_authority(repo_root: Path, data_root: Path) -> dict[str, Any]:
    """Recompute every V01.1 authority before any provider import or request."""

    plan = _plan_module()
    implementation = repo_root / "reports" / "implementation"
    report = load_json(implementation / PLAN_REPORT_NAME)
    expected_report = {
        "INPUT_FILE_N": 2_580,
        "INPUT_MANIFEST_HASH": DAILY_INPUT_MANIFEST_HASH,
        "FORMAL_SYMBOL_N": FORMAL_SYMBOL_N,
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "TRADING_DATE_N": TRADING_DATE_N,
        "TRADING_DATESET_HASH": TRADING_DATESET_HASH,
        "LIFECYCLE_AUTHORITY_HASH": LIFECYCLE_AUTHORITY_HASH,
        "LIFECYCLE_SESSION_KEY_N": LIFECYCLE_SESSION_KEY_N,
        "LIFECYCLE_SESSION_KEYSET_HASH": LIFECYCLE_SESSION_KEYSET_HASH,
        "FULL_REQUEST_N": FULL_REQUEST_N,
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "REQUEST_COVERAGE_MISSING_KEY_N": 0,
        "REQUEST_COVERAGE_DUPLICATE_KEY_N": 0,
    }
    for key, expected in expected_report.items():
        if report.get(key) != expected:
            raise CanaryError(f"PLAN_REPORT_AUTHORITY_MISMATCH:{key}")

    current_input = plan.build_input_file_manifest(data_root)
    if current_input["INPUT_FILE_N"] != expected_report["INPUT_FILE_N"] or current_input["INPUT_MANIFEST_HASH"] != DAILY_INPUT_MANIFEST_HASH:
        raise CanaryError("DAILY_INPUT_MANIFEST_DRIFT")

    symbols, identity_source = plan.load_formal_identity_symbols(data_root)
    if len(symbols) != FORMAL_SYMBOL_N or plan.identity_hash(symbols) != FORMAL_IDENTITY_HASH:
        raise CanaryError("FORMAL_IDENTITY_DRIFT")
    instruments = plan.load_instruments(data_root)
    plan.validate_formal_scope(symbols, instruments)

    trading_dates = plan.load_trading_dates(data_root)
    dateset, dateset_hash = plan.build_trading_dateset(trading_dates)
    if len(trading_dates) != TRADING_DATE_N or dateset_hash != TRADING_DATESET_HASH:
        raise CanaryError("TRADING_DATESET_DRIFT")
    frozen_dateset = load_json(implementation / PLAN_DATESET_NAME)
    frozen_dates = frozen_dateset.get("TRADE_DATES")
    if not isinstance(frozen_dates, list) or plan.sha256_json(frozen_dates) != TRADING_DATESET_HASH:
        raise CanaryError("TRADING_DATESET_ARTIFACT_MISMATCH")
    if [value.isoformat() for value in trading_dates] != frozen_dates:
        raise CanaryError("TRADING_DATESET_VALUES_DRIFT")

    lifecycle_rows, lifecycle_hash = plan.build_lifecycle_manifest(symbols, instruments)
    if lifecycle_hash != LIFECYCLE_AUTHORITY_HASH:
        raise CanaryError("LIFECYCLE_AUTHORITY_DRIFT")
    frozen_lifecycle = load_json(implementation / PLAN_LIFECYCLE_NAME)
    frozen_lifecycle_rows = frozen_lifecycle.get("ROWS")
    normalized_lifecycle_rows = json.loads(plan.canonical_json_bytes(lifecycle_rows).decode("utf-8"))
    if frozen_lifecycle_rows != normalized_lifecycle_rows or plan.sha256_json(frozen_lifecycle_rows) != LIFECYCLE_AUTHORITY_HASH:
        raise CanaryError("LIFECYCLE_AUTHORITY_ARTIFACT_MISMATCH")

    calendar_files = plan.build_trading_calendar_file_manifest(data_root)
    frozen_calendar_files = load_json(implementation / PLAN_CALENDAR_FILES_NAME)
    if (
        calendar_files["TRADING_CALENDAR_FILE_MANIFEST_HASH"] != frozen_calendar_files.get("TRADING_CALENDAR_FILE_MANIFEST_HASH")
        or calendar_files["FILES"] != frozen_calendar_files.get("FILES")
    ):
        raise CanaryError("TRADING_CALENDAR_FILE_MANIFEST_DRIFT")

    frozen_manifest = load_json(implementation / PLAN_REQUEST_MANIFEST_NAME)
    persisted_manifest_hash = frozen_manifest.get("FULL_REQUEST_MANIFEST_HASH")
    manifest_without_hash = dict(frozen_manifest)
    manifest_without_hash.pop("FULL_REQUEST_MANIFEST_HASH", None)
    if persisted_manifest_hash != FULL_REQUEST_MANIFEST_HASH or plan.sha256_json(manifest_without_hash) != FULL_REQUEST_MANIFEST_HASH:
        raise CanaryError("FULL_REQUEST_MANIFEST_ARTIFACT_MISMATCH")
    frozen_requests = manifest_without_hash.get("requests")
    if not isinstance(frozen_requests, list) or len(frozen_requests) != FULL_REQUEST_N:
        raise CanaryError("FULL_REQUEST_N_DRIFT")
    rebuilt_manifest, _unused_hash, _baseline = plan.build_request_manifest_v01_1(
        symbols,
        instruments,
        trading_dates,
        current_input,
        TRADING_DATESET_HASH,
        LIFECYCLE_AUTHORITY_HASH,
        calendar_files["TRADING_CALENDAR_FILE_MANIFEST_HASH"],
    )
    plan.verify_request_row_counts(rebuilt_manifest["requests"], symbols, instruments, trading_dates)
    rebuilt_keyset_hash, key_counts = plan.exact_session_key_scan(
        symbols, instruments, trading_dates, rebuilt_manifest["requests"]
    )
    if rebuilt_keyset_hash != LIFECYCLE_SESSION_KEYSET_HASH:
        raise CanaryError("LIFECYCLE_SESSION_KEYSET_DRIFT")
    if key_counts["LIFECYCLE_SESSION_KEY_N"] != LIFECYCLE_SESSION_KEY_N:
        raise CanaryError("LIFECYCLE_SESSION_KEY_N_DRIFT")
    if key_counts["REQUEST_COVERAGE_MISSING_KEY_N"] != 0 or key_counts["REQUEST_COVERAGE_DUPLICATE_KEY_N"] != 0:
        raise CanaryError("REQUEST_COVERAGE_NOT_EXACT")
    rebuilt_manifest["LIFECYCLE_SESSION_KEYSET_HASH"] = rebuilt_keyset_hash
    rebuilt_request_hash = plan.sha256_json(rebuilt_manifest)
    if rebuilt_request_hash != FULL_REQUEST_MANIFEST_HASH or rebuilt_manifest != manifest_without_hash:
        raise CanaryError("FULL_REQUEST_MANIFEST_DRIFT")

    lifecycle_by_symbol = {row["symbol"]: row for row in frozen_lifecycle_rows}
    return {
        "plan": plan,
        "plan_report": report,
        "identity_source": identity_source,
        "input_manifest": current_input,
        "symbols": symbols,
        "instruments": instruments,
        "trading_dates": trading_dates,
        "trading_dateset": dateset,
        "trading_calendar_file_manifest": calendar_files,
        "lifecycle_rows": frozen_lifecycle_rows,
        "lifecycle_by_symbol": lifecycle_by_symbol,
        "requests": frozen_requests,
        "request_manifest": manifest_without_hash,
        "request_manifest_hash": FULL_REQUEST_MANIFEST_HASH,
        "lifecycle_session_keyset_hash": LIFECYCLE_SESSION_KEYSET_HASH,
        "key_counts": key_counts,
    }


def _load_long_suspension_source(repo_root: Path) -> tuple[set[tuple[str, int]], str]:
    path = repo_root / "reports" / "research" / FEASIBILITY_REQUEST_MANIFEST_NAME
    payload = load_json(path)
    pairs: set[tuple[str, int]] = set()
    for row in payload.get("requests", []):
        if row.get("purpose") == "LONG_SUSPENSION_INTERIOR":
            symbol = str(row.get("symbol"))
            year = parse_date(row.get("start_date"))
            if year is not None:
                pairs.add((symbol, year.year))
    if not pairs:
        raise CanaryError("LONG_SUSPENSION_CANARY_SOURCE_EMPTY")
    return pairs, sha256_file(path)


def select_canary_requests(
    requests: list[dict[str, Any]],
    lifecycle_rows: list[dict[str, Any]],
    long_suspension_pairs: set[tuple[str, int]],
) -> list[dict[str, Any]]:
    """Select exactly 100 V01.1 rows without reading canonical daily gaps."""

    if len(requests) != FULL_REQUEST_N:
        raise CanaryError("FULL_REQUEST_N_NOT_48345")
    lifecycle = {str(row["symbol"]): row for row in lifecycle_rows}
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_symbol_year: dict[tuple[str, int], dict[str, Any]] = {}
    for request in requests:
        symbol = str(request.get("symbol"))
        if symbol not in lifecycle:
            raise CanaryError(f"CANARY_REQUEST_OUTSIDE_LIFECYCLE_AUTHORITY:{symbol}")
        by_symbol[symbol].append(request)
        by_symbol_year[(symbol, int(request["calendar_year"]))] = request
    for rows in by_symbol.values():
        rows.sort(key=lambda row: int(row["request_order"]))

    selected: dict[str, set[str]] = {}

    def add(request: dict[str, Any], category: str) -> None:
        selected.setdefault(str(request["request_id"]), set()).add(category)

    def first_for(symbol: str) -> dict[str, Any]:
        return by_symbol[symbol][0]

    def last_for(symbol: str) -> dict[str, Any]:
        return by_symbol[symbol][-1]

    # These anchors prove the ordered full-manifest boundary without changing
    # the request range or consulting current canonical gaps.
    add(requests[0], "EARLY_REQUEST_ORDER")
    add(requests[(len(requests) - 1) // 2], "MIDDLE_REQUEST_ORDER")
    add(requests[-1], "LATE_REQUEST_ORDER")

    def metadata_date(symbol: str, field: str) -> date | None:
        return parse_date(lifecycle[symbol].get(field))

    for exchange in ("SH", "SZ"):
        active = [
            symbol
            for symbol in sorted(by_symbol)
            if symbol.endswith(f".{exchange}")
            and metadata_date(symbol, "delist_date") is None
            and (metadata_date(symbol, "list_date") is None or metadata_date(symbol, "list_date") < date(2020, 1, 1))
        ]
        if not active:
            raise CanaryError(f"ACTIVE_{exchange}_CANARY_SCOPE_EMPTY")
        for symbol in active[:5]:
            add(first_for(symbol), f"ACTIVE_{exchange}")

        delisted = [
            symbol
            for symbol in sorted(by_symbol)
            if symbol.endswith(f".{exchange}") and metadata_date(symbol, "delist_date") is not None
        ]
        delisted.sort(key=lambda symbol: (metadata_date(symbol, "delist_date"), symbol))
        if not delisted:
            raise CanaryError(f"DELISTED_{exchange}_CANARY_SCOPE_EMPTY")
        for symbol in delisted[:3]:
            add(last_for(symbol), f"DELISTED_{exchange}")

        recent = [
            symbol
            for symbol in sorted(by_symbol)
            if symbol.endswith(f".{exchange}")
            and metadata_date(symbol, "list_date") is not None
            and metadata_date(symbol, "list_date") >= date(2020, 1, 1)
        ]
        recent.sort(key=lambda symbol: (metadata_date(symbol, "list_date"), symbol))
        if not recent:
            raise CanaryError(f"RECENT_LISTING_{exchange}_CANARY_SCOPE_EMPTY")
        for symbol in recent[:3]:
            add(first_for(symbol), f"RECENT_LISTING_{exchange}")

    for symbol, year in sorted(long_suspension_pairs):
        request = by_symbol_year.get((symbol, year))
        if request is None:
            raise CanaryError(f"LONG_SUSPENSION_REQUEST_NOT_IN_FULL_PLAN:{symbol}:{year}")
        add(request, "LONG_SUSPENSION_RELEVANT")

    known = by_symbol_year.get(("300546.SZ", 2016))
    if known is None:
        raise CanaryError("KNOWN_300546_2016_REQUEST_MISSING")
    add(known, "KNOWN_300546_HISTORY")

    # Fill to a fixed cardinality using evenly spaced request_order positions;
    # the fallback preserves deterministic ascending request order.
    if len(selected) > CANARY_REQUEST_N:
        raise CanaryError(f"CANARY_MANDATORY_SCOPE_EXCEEDS_{CANARY_REQUEST_N}")
    if len(selected) < CANARY_REQUEST_N:
        step = (len(requests) - 1) / max(1, CANARY_REQUEST_N - len(selected) - 1)
        for index in range(CANARY_REQUEST_N - len(selected)):
            candidate = requests[min(len(requests) - 1, round(index * step))]
            add(candidate, "DETERMINISTIC_STRATIFIED_FILL")
            if len(selected) >= CANARY_REQUEST_N:
                break
    if len(selected) < CANARY_REQUEST_N:
        for request in requests:
            add(request, "DETERMINISTIC_ORDER_FILL")
            if len(selected) >= CANARY_REQUEST_N:
                break
    if len(selected) != CANARY_REQUEST_N:
        raise CanaryError("CANARY_REQUEST_CARDINALITY_MISMATCH")

    selected_rows: list[dict[str, Any]] = []
    request_map = {str(row["request_id"]): row for row in requests}
    for request_id, categories in selected.items():
        row = dict(request_map[request_id])
        row["canary_categories"] = sorted(categories)
        row["canary_exchange_scope"] = "SH" if row["symbol"].endswith(".SH") else "SZ"
        selected_rows.append(row)
    selected_rows.sort(key=lambda row: int(row["request_order"]))
    categories = {category for row in selected_rows for category in row["canary_categories"]}
    required = {
        "ACTIVE_SH",
        "ACTIVE_SZ",
        "DELISTED_SH",
        "DELISTED_SZ",
        "RECENT_LISTING_SH",
        "RECENT_LISTING_SZ",
        "LONG_SUSPENSION_RELEVANT",
        "EARLY_REQUEST_ORDER",
        "MIDDLE_REQUEST_ORDER",
        "LATE_REQUEST_ORDER",
    }
    if not required.issubset(categories):
        raise CanaryError(f"CANARY_CATEGORY_COVERAGE_MISSING:{sorted(required - categories)}")
    return selected_rows


def build_canary_request_manifest(
    context: dict[str, Any], selected_requests: list[dict[str, Any]], long_source_hash: str
) -> tuple[dict[str, Any], str]:
    if len(selected_requests) != CANARY_REQUEST_N:
        raise CanaryError("CANARY_REQUEST_N_MISMATCH")
    ids = [str(row["request_id"]) for row in selected_requests]
    if len(ids) != len(set(ids)):
        raise CanaryError("CANARY_REQUEST_ID_DUPLICATE")
    full_ids = {str(row["request_id"]) for row in context["requests"]}
    if not set(ids).issubset(full_ids):
        raise CanaryError("CANARY_REQUEST_OUTSIDE_FULL_MANIFEST")
    manifest = {
        "TASK": TASK_NAME,
        "BASE_HEAD": BASE_HEAD,
        "PLAN_COMMIT": PLAN_COMMIT,
        "BRANCH": BRANCH,
        "PLAN_AUTHORITY": "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1",
        "DAILY_INPUT_MANIFEST_HASH": DAILY_INPUT_MANIFEST_HASH,
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "TRADING_DATESET_HASH": TRADING_DATESET_HASH,
        "LIFECYCLE_AUTHORITY_HASH": LIFECYCLE_AUTHORITY_HASH,
        "LIFECYCLE_SESSION_KEYSET_HASH": LIFECYCLE_SESSION_KEYSET_HASH,
        "FULL_REQUEST_N": FULL_REQUEST_N,
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "CANARY_REQUEST_N": CANARY_REQUEST_N,
        "CANARY_SELECTION_VERSION": "V01_DETERMINISTIC_SCOPE_V1",
        "CANARY_SELECTION_CONTRACT": {
            "source": "committed V01.1 request rows only",
            "current_canonical_gaps_used": False,
            "request_parameters": "copied exactly from each frozen request row",
            "required_categories": [
                "SH",
                "SZ",
                "ACTIVE",
                "DELISTED",
                "RECENT_LISTING",
                "LONG_SUSPENSION_RELEVANT",
                "EARLY_REQUEST_ORDER",
                "MIDDLE_REQUEST_ORDER",
                "LATE_REQUEST_ORDER",
            ],
        },
        "LONG_SUSPENSION_RELEVANT_SOURCE": {
            "path": f"reports/research/{FEASIBILITY_REQUEST_MANIFEST_NAME}",
            "sha256": long_source_hash,
            "role": "category-selection evidence only; no request range derivation",
        },
        "PROVIDER_CONTRACT": {
            "provider": PROVIDER,
            "distribution_version": PROVIDER_VERSION,
            "runtime": PROVIDER_RUNTIME,
            "fields": QUERY_FIELDS,
            "frequency": QUERY_FREQUENCY,
            "adjustflag": QUERY_ADJUSTFLAG,
        },
        "REQUEST_ORDERING": "ascending frozen request_order",
        "REQUEST_IDS": ids,
        "REQUESTS": selected_requests,
    }
    return manifest, sha256_json(manifest)


def _validate_request_parameters(request: dict[str, Any]) -> None:
    if (
        request.get("provider") != PROVIDER
        or request.get("provider_runtime") != PROVIDER_RUNTIME
        or request.get("fields") != QUERY_FIELDS
        or request.get("frequency") != QUERY_FREQUENCY
        or request.get("adjustflag") != QUERY_ADJUSTFLAG
    ):
        raise CanaryError(f"REQUEST_PARAMETER_CONTRACT_MISMATCH:{request.get('request_id')}")


def _result_attr(result: Any, name: str, default: str = "UNKNOWN") -> str:
    if isinstance(result, dict):
        return str(result.get(name, default))
    return str(getattr(result, name, default))


def _read_result_rows(result: Any) -> list[list[Any]]:
    rows: list[list[Any]] = []
    while result.next():
        rows.append(list(result.get_row_data()))
    return rows


def load_baostock_provider() -> tuple[Any, dict[str, Any]]:
    try:
        installed_version = importlib_metadata.version(PROVIDER)
    except importlib_metadata.PackageNotFoundError as exc:  # pragma: no cover - environment dependent
        raise CanaryError("BAOSTOCK_NOT_INSTALLED") from exc
    if installed_version != PROVIDER_VERSION:
        raise CanaryError(f"BAOSTOCK_VERSION_MISMATCH:{installed_version}")
    try:
        import baostock as provider  # type: ignore[no-redef]
    except Exception as exc:  # pragma: no cover - environment dependent
        raise CanaryError("BAOSTOCK_IMPORT_FAILED") from exc
    return provider, {
        "distribution": PROVIDER,
        "distribution_version": installed_version,
        "module_version": str(getattr(provider, "__version__", "UNKNOWN")),
        "runtime": PROVIDER_RUNTIME,
    }


def fetch_one_request(
    request: dict[str, Any],
    provider: Any,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute one exact frozen request with bounded retries."""

    _validate_request_parameters(request)
    attempts: list[dict[str, Any]] = []
    final_rows: list[list[Any]] = []
    final_code = "UNKNOWN"
    final_message = ""
    for attempt in range(1, MAX_ATTEMPT_N + 1):
        try:
            result = provider.query_history_k_data_plus(
                request["bs_code"],
                request["fields"],
                start_date=request["start_date"],
                end_date=request["end_date"],
                frequency=request["frequency"],
                adjustflag=request["adjustflag"],
            )
            final_code = _result_attr(result, "error_code")
            final_message = _result_attr(result, "error_msg", "")
            rows = _read_result_rows(result) if final_code == "0" else []
            attempts.append(
                {
                    "attempt_n": attempt,
                    "provider_error_code": final_code,
                    "provider_error_message": final_message,
                    "raw_row_n": len(rows),
                }
            )
            if final_code == "0":
                final_rows = rows
                break
        except Exception as exc:  # provider failure is retained, never hidden
            final_code = "EXCEPTION"
            final_message = f"{type(exc).__name__}:{exc}"
            attempts.append(
                {
                    "attempt_n": attempt,
                    "provider_error_code": final_code,
                    "provider_error_message": final_message,
                    "raw_row_n": 0,
                }
            )
        if attempt < MAX_ATTEMPT_N:
            sleep_fn(RETRY_BACKOFF_SECONDS[attempt - 1])
    return {
        "attempt_n": len(attempts),
        "provider_error_code": final_code,
        "provider_error_message": final_message,
        "raw_rows": final_rows,
        "attempts": attempts,
        "provider_query_attempt_n": len(attempts),
    }


def _expected_session_dates(request: dict[str, Any], context: dict[str, Any]) -> list[date]:
    start = parse_date(request.get("start_date"))
    end = parse_date(request.get("end_date"))
    if start is None or end is None or start > end:
        raise CanaryError(f"REQUEST_DATE_INVALID:{request.get('request_id')}")
    lifecycle_row = context["lifecycle_by_symbol"].get(request["symbol"])
    if lifecycle_row is None:
        raise CanaryError(f"REQUEST_SYMBOL_LIFECYCLE_MISSING:{request['symbol']}")
    lifetime_start = parse_date(lifecycle_row.get("effective_start"))
    lifetime_end = parse_date(lifecycle_row.get("effective_end"))
    if lifetime_start is None or lifetime_end is None:
        raise CanaryError(f"REQUEST_LIFETIME_EMPTY:{request['request_id']}")
    return [
        trading_date
        for trading_date in context["trading_dates"]
        if start <= trading_date <= end and lifetime_start <= trading_date <= lifetime_end
    ]


def normalize_provider_rows(
    request: dict[str, Any],
    raw_rows: list[list[Any]],
    *,
    provider_error_code: str,
    provider_error_message: str,
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Normalize rows and classify every frozen lifecycle date in the window."""

    expected_dates = _expected_session_dates(request, context)
    expected_set = set(expected_dates)
    start = parse_date(request["start_date"])
    end = parse_date(request["end_date"])
    valid_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    invalid_by_date: dict[date, list[str]] = defaultdict(list)
    normalized_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    for row_index, raw in enumerate(raw_rows):
        raw_values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        reason: str | None = None
        trade_date = parse_date(raw_values[0]) if raw_values else None
        if len(raw_values) != 10:
            reason = "INVALID_FIELD_COUNT"
        elif trade_date is None:
            reason = "INVALID_DATE"
        elif str(raw_values[1]) != str(request["bs_code"]):
            reason = "SYMBOL_CODE_MISMATCH"
        elif start is None or end is None or not start <= trade_date <= end:
            reason = "DATE_OUTSIDE_REQUEST"
        elif trade_date not in expected_set:
            reason = "DATE_NOT_FROZEN_OR_OUTSIDE_LIFECYCLE"
        else:
            status = parse_int(raw_values[9])
            if status not in {0, 1}:
                reason = "INVALID_TRADESTATUS"
            else:
                numeric: dict[str, float | None] = {}
                for index, field in enumerate(("open", "high", "low", "close", "volume", "amount", "preclose"), 2):
                    value = raw_values[index]
                    if value is None or str(value).strip() == "":
                        numeric[field] = None
                    else:
                        numeric[field] = parse_float(value)
                        if numeric[field] is None:
                            reason = "INVALID_NUMERIC_FIELD"
                            break
                if reason is None:
                    normalized = {
                        "symbol": request["symbol"],
                        "trade_date": trade_date.isoformat(),
                        "provider_code": str(raw_values[1]),
                        "tradestatus": status,
                        **numeric,
                    }
                    valid_by_date[trade_date].append(normalized)
                    normalized_rows.append(normalized)
        if reason is not None:
            invalid_rows.append(
                {
                    "row_index": row_index,
                    "trade_date": None if trade_date is None else trade_date.isoformat(),
                    "reason": reason,
                    "raw_row": raw_values,
                }
            )
            if trade_date is not None and trade_date in expected_set:
                invalid_by_date[trade_date].append(reason)

    duplicate_provider_key_n = sum(max(0, len(rows) - 1) for rows in valid_by_date.values())
    cases: list[dict[str, Any]] = []
    for trading_date in expected_dates:
        matching = valid_by_date.get(trading_date, [])
        invalid_reasons = invalid_by_date.get(trading_date, [])
        if provider_error_code != "0":
            classification, basis = "UNKNOWN", "PROVIDER_ERROR"
            provider_row_present = False
            provider_status = None
        elif len(matching) > 1:
            classification, basis = "UNKNOWN", "DUPLICATE_PROVIDER_ROW"
            provider_row_present = True
            provider_status = None
        elif len(matching) == 1:
            provider_status = matching[0]["tradestatus"]
            if provider_status == 1:
                classification, basis = "EXPECTED_BAR", "PROVIDER_TRADESTATUS_1"
            else:
                classification, basis = "NOT_EXPECTED_BAR", "PROVIDER_TRADESTATUS_0"
            provider_row_present = True
        elif invalid_reasons:
            classification, basis = "UNKNOWN", invalid_reasons[0]
            provider_row_present = True
            provider_status = None
        else:
            classification, basis = "UNKNOWN", "PROVIDER_ROW_ABSENT_IN_LIFETIME"
            provider_row_present = False
            provider_status = None
        cases.append(
            {
                "symbol": request["symbol"],
                "trade_date": trading_date.isoformat(),
                "classification": classification,
                "basis": basis,
                "provider_row_present": provider_row_present,
                "provider_tradestatus": provider_status,
            }
        )

    classification_counts = Counter(case["classification"] for case in cases)
    basis_counts = Counter(case["basis"] for case in cases)
    status_counts = Counter(
        row["tradestatus"] for row in normalized_rows if row["tradestatus"] in {0, 1}
    )
    metrics = {
        "RAW_ROW_N": len(raw_rows),
        "NORMALIZED_ROW_N": len(normalized_rows),
        "EXPECTED_SESSION_KEY_N": len(expected_dates),
        "DUPLICATE_PROVIDER_KEY_N": duplicate_provider_key_n,
        "INVALID_PROVIDER_ROW_N": len(invalid_rows),
        "EXPECTED_BAR_CASE_N": classification_counts["EXPECTED_BAR"],
        "NOT_EXPECTED_BAR_CASE_N": classification_counts["NOT_EXPECTED_BAR"],
        "UNKNOWN_CASE_N": classification_counts["UNKNOWN"],
        "TRADESTATUS_1_ROW_N": status_counts[1],
        "TRADESTATUS_0_ROW_N": status_counts[0],
    }
    normalized_payload = {
        "REQUEST_ID": request["request_id"],
        "REQUEST_ORDER": request["request_order"],
        "EXPECTED_SESSION_DATES": [value.isoformat() for value in expected_dates],
        "NORMALIZED_ROWS": normalized_rows,
        "CASES": cases,
        "INVALID_ROWS": invalid_rows,
        "METRICS": metrics,
        "BASIS_COUNTS": dict(sorted(basis_counts.items())),
        "PROVIDER_ERROR_CODE": provider_error_code,
        "PROVIDER_ERROR_MESSAGE": provider_error_message,
    }
    return normalized_payload, metrics


def _receipt_paths(data_root: Path, stage_root: Path, request_id: str) -> tuple[Path, Path]:
    safe_id = str(request_id)
    if not safe_id or "/" in safe_id or "\\" in safe_id or safe_id in {".", ".."}:
        raise CanaryError("REQUEST_ID_PATH_INVALID")
    return (
        _safe_stage_path(data_root, stage_root, f"raw_receipts/{safe_id}.json"),
        _safe_stage_path(data_root, stage_root, f"normalized_receipts/{safe_id}.json"),
    )


def write_request_receipts(
    data_root: Path,
    stage_root: Path,
    request: dict[str, Any],
    *,
    canary_manifest_hash: str,
    context: dict[str, Any],
    fetched: dict[str, Any],
    completed_at: str,
) -> dict[str, Any]:
    raw_payload = {"rows": fetched["raw_rows"]}
    normalized_payload, metrics = normalize_provider_rows(
        request,
        fetched["raw_rows"],
        provider_error_code=str(fetched["provider_error_code"]),
        provider_error_message=str(fetched["provider_error_message"]),
        context=context,
    )
    status = (
        "COMPLETE"
        if fetched["provider_error_code"] == "0"
        and metrics["DUPLICATE_PROVIDER_KEY_N"] == 0
        and metrics["INVALID_PROVIDER_ROW_N"] == 0
        else "FAILED"
    )
    metadata = {
        "request_id": request["request_id"],
        "request_order": request["request_order"],
        "request_manifest_hash": canary_manifest_hash,
        "lifecycle_session_keyset_hash": LIFECYCLE_SESSION_KEYSET_HASH,
        "attempt_n": fetched["attempt_n"],
        "provider_error_code": str(fetched["provider_error_code"]),
        "raw_row_n": len(fetched["raw_rows"]),
        "raw_sha256": sha256_json(raw_payload),
        "normalized_row_n": metrics["NORMALIZED_ROW_N"],
        "normalized_sha256": sha256_json(normalized_payload),
        "status": status,
        "completed_at": completed_at,
    }
    raw_receipt = {
        **metadata,
        "request": request,
        "attempts": fetched["attempts"],
        "provider_error_message": fetched["provider_error_message"],
        "raw_payload": raw_payload,
    }
    normalized_receipt = {
        **metadata,
        "request": request,
        "normalized_payload": normalized_payload,
    }
    raw_path, normalized_path = _receipt_paths(data_root, stage_root, str(request["request_id"]))
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(raw_path, canonical_json_bytes(raw_receipt))
    _atomic_write(normalized_path, canonical_json_bytes(normalized_receipt))
    return {
        **metadata,
        "metrics": metrics,
        "raw_receipt_file_sha256": sha256_file(raw_path),
        "normalized_receipt_file_sha256": sha256_file(normalized_path),
    }


def _load_receipt_pair(data_root: Path, stage_root: Path, request_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    raw_path, normalized_path = _receipt_paths(data_root, stage_root, request_id)
    if not raw_path.is_file() or not normalized_path.is_file():
        return None
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict) or not isinstance(normalized, dict):
        return None
    return raw, normalized


def is_valid_complete_receipt(
    data_root: Path,
    stage_root: Path,
    request: dict[str, Any],
    *,
    canary_manifest_hash: str,
    lifecycle_session_keyset_hash: str = LIFECYCLE_SESSION_KEYSET_HASH,
) -> bool:
    """Only a payload-hash-verified COMPLETE pair may be skipped on resume."""

    pair = _load_receipt_pair(data_root, stage_root, str(request["request_id"]))
    if pair is None:
        return False
    raw, normalized = pair
    for field in REQUIRED_RECEIPT_FIELDS:
        if field not in raw or field not in normalized or raw[field] != normalized[field]:
            return False
    if raw.get("request_id") != request.get("request_id") or raw.get("request_order") != request.get("request_order"):
        return False
    if raw.get("request_manifest_hash") != canary_manifest_hash:
        return False
    if raw.get("lifecycle_session_keyset_hash") != lifecycle_session_keyset_hash:
        return False
    if raw.get("status") != "COMPLETE" or str(raw.get("provider_error_code")) != "0":
        return False
    attempt_n = parse_int(raw.get("attempt_n"))
    raw_row_n = parse_int(raw.get("raw_row_n"))
    normalized_row_n = parse_int(raw.get("normalized_row_n"))
    if (
        attempt_n is None
        or attempt_n < 1
        or attempt_n > MAX_ATTEMPT_N
        or raw_row_n is None
        or raw_row_n < 0
        or normalized_row_n is None
        or normalized_row_n < 0
        or not str(raw.get("completed_at", "")).strip()
    ):
        return False
    raw_payload = raw.get("raw_payload")
    normalized_payload = normalized.get("normalized_payload")
    if (
        not isinstance(raw_payload, dict)
        or not isinstance(normalized_payload, dict)
        or not isinstance(raw_payload.get("rows"), list)
        or not isinstance(normalized_payload.get("NORMALIZED_ROWS"), list)
    ):
        return False
    if raw.get("raw_sha256") != sha256_json(raw_payload) or normalized.get("normalized_sha256") != sha256_json(normalized_payload):
        return False
    if raw_row_n != len(raw_payload.get("rows", [])):
        return False
    if normalized_row_n != len(normalized_payload.get("NORMALIZED_ROWS", [])):
        return False
    metrics = normalized_payload.get("METRICS", {})
    if not isinstance(metrics, dict):
        return False
    if metrics.get("DUPLICATE_PROVIDER_KEY_N") != 0 or metrics.get("INVALID_PROVIDER_ROW_N") != 0:
        return False
    return True


def _checkpoint_path(data_root: Path, stage_root: Path) -> Path:
    return _safe_stage_path(data_root, stage_root, "checkpoint.json")


def _initial_checkpoint(requests: list[dict[str, Any]], canary_manifest_hash: str) -> dict[str, Any]:
    return {
        "TASK": TASK_NAME,
        "CANARY_REQUEST_N": len(requests),
        "REQUEST_MANIFEST_HASH": canary_manifest_hash,
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "LIFECYCLE_SESSION_KEYSET_HASH": LIFECYCLE_SESSION_KEYSET_HASH,
        "state_enum": list(CHECKPOINT_STATES),
        "request_states": {
            str(row["request_id"]): {
                "request_order": row["request_order"],
                "status": "PENDING",
            }
            for row in requests
        },
        "history": [],
    }


def load_or_init_checkpoint(
    data_root: Path, stage_root: Path, requests: list[dict[str, Any]], canary_manifest_hash: str
) -> dict[str, Any]:
    path = _checkpoint_path(data_root, stage_root)
    if not path.exists():
        checkpoint = _initial_checkpoint(requests, canary_manifest_hash)
        write_stage_json(data_root, stage_root, "checkpoint.json", checkpoint)
        return checkpoint
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise CanaryError("CHECKPOINT_CORRUPT") from exc
    request_ids = {str(row["request_id"]) for row in requests}
    if (
        not isinstance(checkpoint, dict)
        or checkpoint.get("REQUEST_MANIFEST_HASH") != canary_manifest_hash
        or checkpoint.get("FULL_REQUEST_MANIFEST_HASH") != FULL_REQUEST_MANIFEST_HASH
        or checkpoint.get("LIFECYCLE_SESSION_KEYSET_HASH") != LIFECYCLE_SESSION_KEYSET_HASH
        or set(checkpoint.get("request_states", {})) != request_ids
        or not isinstance(checkpoint.get("history"), list)
    ):
        raise CanaryError("CHECKPOINT_AUTHORITY_MISMATCH")
    for state in checkpoint["request_states"].values():
        if state.get("status") not in CHECKPOINT_STATES:
            raise CanaryError("CHECKPOINT_STATE_INVALID")
    return checkpoint


def _write_checkpoint(data_root: Path, stage_root: Path, checkpoint: dict[str, Any]) -> None:
    write_stage_json(data_root, stage_root, "checkpoint.json", checkpoint)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_extraction_stage(
    context: dict[str, Any],
    canary_manifest: dict[str, Any],
    canary_manifest_hash: str,
    *,
    data_root: Path,
    stage_root: Path,
    provider_factory: Callable[[], Any] | None = None,
    stop_after: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute pending canary request units and checkpoint after each unit."""

    require_isolated_staging_root(data_root, stage_root)
    requests = canary_manifest["REQUESTS"]
    checkpoint = load_or_init_checkpoint(data_root, stage_root, requests, canary_manifest_hash)
    started_at = _now_utc()
    skipped_complete_n = 0
    refetched_n = 0
    provider_query_attempt_n = 0
    processed_n = 0
    last_query_at: float | None = None
    provider: Any | None = None
    provider_runtime: dict[str, Any] | None = None
    logout_error: str | None = None
    login_error: str | None = None

    def get_provider() -> Any:
        nonlocal provider, provider_runtime, login_error
        if provider is not None or login_error is not None:
            return provider
        if provider_factory is None:
            provider, provider_runtime = load_baostock_provider()
        else:
            supplied = provider_factory()
            if isinstance(supplied, tuple) and len(supplied) == 2:
                provider, provider_runtime = supplied
            else:
                provider = supplied
                provider_runtime = {"runtime": PROVIDER_RUNTIME, "distribution_version": PROVIDER_VERSION}
        try:
            login = provider.login()
            login_code = _result_attr(login, "error_code")
            if login_code != "0":
                login_error = f"{login_code}:{_result_attr(login, 'error_msg', '')}"
        except Exception as exc:  # pragma: no cover - provider dependent
            login_error = f"EXCEPTION:{type(exc).__name__}:{exc}"
        return provider

    try:
        for request in requests:
            request_id = str(request["request_id"])
            if is_valid_complete_receipt(
                data_root,
                stage_root,
                request,
                canary_manifest_hash=canary_manifest_hash,
            ):
                checkpoint["request_states"][request_id]["status"] = "COMPLETE"
                checkpoint["request_states"][request_id]["last_action"] = "SKIP_VALID_COMPLETE"
                skipped_complete_n += 1
                processed_n += 1
                _write_checkpoint(data_root, stage_root, checkpoint)
                continue
            if stop_after is not None and refetched_n >= stop_after:
                break
            checkpoint["request_states"][request_id]["status"] = "RUNNING"
            checkpoint["request_states"][request_id]["last_action"] = "EXECUTION_STARTED"
            _write_checkpoint(data_root, stage_root, checkpoint)
            refetched_n += 1
            processed_n += 1
            if login_error is None:
                active_provider = get_provider()
            else:
                active_provider = provider
            if login_error is not None or active_provider is None:
                fetched = {
                    "attempt_n": 0,
                    "provider_error_code": login_error or "PROVIDER_UNAVAILABLE",
                    "provider_error_message": login_error or "provider unavailable",
                    "raw_rows": [],
                    "attempts": [],
                    "provider_query_attempt_n": 0,
                }
            else:
                if last_query_at is not None:
                    elapsed = time.monotonic() - last_query_at
                    if elapsed < REQUEST_INTERVAL_SECONDS:
                        sleep_fn(REQUEST_INTERVAL_SECONDS - elapsed)
                last_query_at = time.monotonic()
                fetched = fetch_one_request(request, active_provider, sleep_fn=sleep_fn)
                provider_query_attempt_n += fetched["provider_query_attempt_n"]
            receipt = write_request_receipts(
                data_root,
                stage_root,
                request,
                canary_manifest_hash=canary_manifest_hash,
                context=context,
                fetched=fetched,
                completed_at=_now_utc(),
            )
            checkpoint["request_states"][request_id].update(
                {
                    "status": receipt["status"],
                    "attempt_n": receipt["attempt_n"],
                    "provider_error_code": receipt["provider_error_code"],
                    "raw_sha256": receipt["raw_sha256"],
                    "normalized_sha256": receipt["normalized_sha256"],
                    "raw_receipt_file_sha256": receipt["raw_receipt_file_sha256"],
                    "normalized_receipt_file_sha256": receipt["normalized_receipt_file_sha256"],
                    "last_action": "TERMINAL_RECEIPT_WRITTEN",
                }
            )
            _write_checkpoint(data_root, stage_root, checkpoint)
    finally:
        if provider is not None:
            try:
                logout = provider.logout()
                logout_code = _result_attr(logout, "error_code", "0")
                if logout_code != "0":
                    logout_error = f"{logout_code}:{_result_attr(logout, 'error_msg', '')}"
            except Exception as exc:  # pragma: no cover - provider dependent
                logout_error = f"EXCEPTION:{type(exc).__name__}:{exc}"

    pending_n = sum(
        state.get("status") != "COMPLETE"
        for state in checkpoint["request_states"].values()
    )
    history_entry = {
        "run_order": len(checkpoint["history"]) + 1,
        "started_at": started_at,
        "ended_at": _now_utc(),
        "stop_after": stop_after,
        "processed_n": processed_n,
        "skipped_complete_n": skipped_complete_n,
        "refetched_n": refetched_n,
        "provider_query_attempt_n": provider_query_attempt_n,
        "pending_after_n": pending_n,
        "login_error": login_error,
        "logout_error": logout_error,
        "status": "PARTIAL" if stop_after is not None else "TERMINAL",
    }
    checkpoint["history"].append(history_entry)
    checkpoint["last_run"] = history_entry
    checkpoint["updated_at"] = history_entry["ended_at"]
    _write_checkpoint(data_root, stage_root, checkpoint)
    return {
        "checkpoint": checkpoint,
        "run": history_entry,
        "provider_runtime": provider_runtime,
    }


def _receipt_index_and_summary(
    context: dict[str, Any],
    canary_manifest: dict[str, Any],
    *,
    data_root: Path,
    stage_root: Path,
    current_run: dict[str, Any],
    checkpoint: dict[str, Any],
    terminal_requested: bool,
) -> dict[str, Any]:
    index: list[dict[str, Any]] = []
    status_counts = Counter()
    case_counts = Counter()
    basis_counts = Counter()
    provider_failed_n = 0
    duplicate_n = 0
    invalid_n = 0
    missing_n = 0
    corrupt_n = 0
    for request in canary_manifest["REQUESTS"]:
        pair = _load_receipt_pair(data_root, stage_root, str(request["request_id"]))
        if pair is None:
            missing_n += 1
            index.append(
                {
                    "request_id": request["request_id"],
                    "request_order": request["request_order"],
                    "status": "MISSING_OR_UNREADABLE",
                }
            )
            continue
        raw, normalized = pair
        status = str(raw.get("status", "CORRUPT"))
        status_counts[status] += 1
        normalized_payload = normalized.get("normalized_payload")
        metrics = normalized_payload.get("METRICS", {}) if isinstance(normalized_payload, dict) else {}
        try:
            duplicate_n += int(metrics.get("DUPLICATE_PROVIDER_KEY_N", 0) or 0)
            invalid_n += int(metrics.get("INVALID_PROVIDER_ROW_N", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            corrupt_n += 1
            index.append(
                {
                    "request_id": raw.get("request_id"),
                    "request_order": raw.get("request_order"),
                    "status": "CORRUPT",
                }
            )
            continue
        if str(raw.get("provider_error_code")) != "0":
            provider_failed_n += 1
        payload = normalized_payload if isinstance(normalized_payload, dict) else {}
        for case in payload.get("CASES", []):
            case_counts[str(case.get("classification"))] += 1
            basis_counts[str(case.get("basis"))] += 1
        valid = is_valid_complete_receipt(
            data_root,
            stage_root,
            request,
            canary_manifest_hash=canary_manifest["CANARY_REQUEST_MANIFEST_HASH"],
        )
        if not valid and status == "COMPLETE":
            corrupt_n += 1
        index.append(
            {
                "request_id": raw.get("request_id"),
                "request_order": raw.get("request_order"),
                "status": status,
                "attempt_n": raw.get("attempt_n"),
                "provider_error_code": raw.get("provider_error_code"),
                "raw_row_n": raw.get("raw_row_n"),
                "normalized_row_n": raw.get("normalized_row_n"),
                "raw_sha256": raw.get("raw_sha256"),
                "normalized_sha256": raw.get("normalized_sha256"),
                "valid_complete_receipt": valid,
                "metrics": metrics,
            }
        )
    complete_n = sum(item.get("valid_complete_receipt") is True for item in index)
    failed_n = len(canary_manifest["REQUESTS"]) - complete_n
    resume_parity = _resume_parity(checkpoint)
    canary_complete = (
        terminal_requested
        and complete_n == CANARY_REQUEST_N
        and missing_n == 0
        and corrupt_n == 0
        and provider_failed_n == 0
        and duplicate_n == 0
        and invalid_n == 0
        and current_run.get("logout_error") is None
    )
    return {
        "receipt_index": index,
        "CANARY_REQUEST_N": CANARY_REQUEST_N,
        "COMPLETE_REQUEST_N": complete_n,
        "FAILED_REQUEST_N": failed_n,
        "MISSING_REQUEST_N": missing_n,
        "CORRUPT_COMPLETE_RECEIPT_N": corrupt_n,
        "PROVIDER_FAILED_REQUEST_N": provider_failed_n,
        "DUPLICATE_PROVIDER_KEY_N": duplicate_n,
        "INVALID_PROVIDER_ROW_N": invalid_n,
        "EXPECTED_BAR_CASE_N": case_counts["EXPECTED_BAR"],
        "NOT_EXPECTED_BAR_CASE_N": case_counts["NOT_EXPECTED_BAR"],
        "UNKNOWN_CASE_N": case_counts["UNKNOWN"],
        "BASIS_COUNTS": dict(sorted(basis_counts.items())),
        "STATUS_COUNTS": dict(sorted(status_counts.items())),
        "CANARY_EXECUTION_COMPLETE": canary_complete,
        "RESUME_PARITY_PASS": resume_parity,
        "RESUME_SKIPPED_COMPLETE_N": current_run["skipped_complete_n"],
        "RESUME_REFETCHED_N": current_run["refetched_n"],
        "NETWORK_REQUEST_N": sum(int(item.get("provider_query_attempt_n", 0)) for item in checkpoint["history"]),
        "CURRENT_RUN_PROVIDER_QUERY_ATTEMPT_N": current_run["provider_query_attempt_n"],
        "LOGIN_ERROR": current_run.get("login_error"),
        "LOGOUT_ERROR": current_run.get("logout_error"),
        "FULL_EXTRACTION_ENGINE_READY": bool(canary_complete and resume_parity),
    }


def _resume_parity(checkpoint: dict[str, Any]) -> bool:
    history = checkpoint.get("history", [])
    if len(history) < 2:
        return False
    first = history[0]
    for later in history[1:]:
        if (
            first.get("stop_after") is not None
            and first.get("processed_n", 0) > 0
            and later.get("skipped_complete_n", 0) > 0
            and later.get("refetched_n", 0) > 0
            and later.get("provider_query_attempt_n", 0) >= later.get("refetched_n", 0)
        ):
            return True
    return False


def _category_counts(canary_manifest: dict[str, Any]) -> dict[str, int]:
    counts = Counter()
    for row in canary_manifest["REQUESTS"]:
        for category in row.get("canary_categories", []):
            counts[category] += 1
    return dict(sorted(counts.items()))


def build_execution_receipt(
    context: dict[str, Any],
    canary_manifest: dict[str, Any],
    canary_manifest_hash: str,
    summary: dict[str, Any],
    *,
    stage_root: Path,
) -> dict[str, Any]:
    return {
        "TASK": TASK_NAME,
        "BASE_HEAD": BASE_HEAD,
        "PLAN_COMMIT": PLAN_COMMIT,
        "DAILY_INPUT_MANIFEST_HASH": DAILY_INPUT_MANIFEST_HASH,
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "TRADING_DATESET_HASH": TRADING_DATESET_HASH,
        "LIFECYCLE_AUTHORITY_HASH": LIFECYCLE_AUTHORITY_HASH,
        "LIFECYCLE_SESSION_KEYSET_HASH": LIFECYCLE_SESSION_KEYSET_HASH,
        "FULL_REQUEST_N": FULL_REQUEST_N,
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "CANARY_REQUEST_N": CANARY_REQUEST_N,
        "CANARY_REQUEST_MANIFEST_HASH": canary_manifest_hash,
        "NETWORK_REQUEST_N": summary["NETWORK_REQUEST_N"],
        "COMPLETE_REQUEST_N": summary["COMPLETE_REQUEST_N"],
        "FAILED_REQUEST_N": summary["FAILED_REQUEST_N"],
        "PROVIDER_FAILED_REQUEST_N": summary["PROVIDER_FAILED_REQUEST_N"],
        "DUPLICATE_PROVIDER_KEY_N": summary["DUPLICATE_PROVIDER_KEY_N"],
        "INVALID_PROVIDER_ROW_N": summary["INVALID_PROVIDER_ROW_N"],
        "EXPECTED_BAR_CASE_N": summary["EXPECTED_BAR_CASE_N"],
        "NOT_EXPECTED_BAR_CASE_N": summary["NOT_EXPECTED_BAR_CASE_N"],
        "UNKNOWN_CASE_N": summary["UNKNOWN_CASE_N"],
        "CANARY_EXECUTION_COMPLETE": summary["CANARY_EXECUTION_COMPLETE"],
        "FULL_EXTRACTION_ENGINE_READY": summary["FULL_EXTRACTION_ENGINE_READY"],
        "RESUME_PARITY_PASS": summary["RESUME_PARITY_PASS"],
        "RESUME_SKIPPED_COMPLETE_N": summary["RESUME_SKIPPED_COMPLETE_N"],
        "RESUME_REFETCHED_N": summary["RESUME_REFETCHED_N"],
        "REQUEST_RECEIPT_INDEX": summary["receipt_index"],
        "RUN_HISTORY": summary.get("RUN_HISTORY", []),
        "STAGING_ROOT_RELATIVE": f"staging/{STAGING_DIRNAME}/",
        "STAGING_EXECUTION_RECEIPT_RELATIVE": "execution_receipt.json",
        "STAGING_QUALITY_REPORT_RELATIVE": "quality_report.json",
    }


def build_quality_report(
    canary_manifest: dict[str, Any], summary: dict[str, Any], checkpoint: dict[str, Any]
) -> dict[str, Any]:
    return {
        "TASK": TASK_NAME,
        "CANARY_REQUEST_MANIFEST_HASH": canary_manifest["CANARY_REQUEST_MANIFEST_HASH"],
        "CANARY_REQUEST_N": CANARY_REQUEST_N,
        "CANARY_SCOPE_CATEGORY_REQUEST_N": _category_counts(canary_manifest),
        "QUALITY_GATE": {
            "ALL_CANARY_REQUESTS_COMPLETE": summary["COMPLETE_REQUEST_N"] == CANARY_REQUEST_N,
            "PROVIDER_FAILED_REQUEST_N": summary["PROVIDER_FAILED_REQUEST_N"],
            "DUPLICATE_PROVIDER_KEY_N": summary["DUPLICATE_PROVIDER_KEY_N"],
            "INVALID_PROVIDER_ROW_N": summary["INVALID_PROVIDER_ROW_N"],
            "UNKNOWN_CASE_N_ALLOWED": True,
            "UNKNOWN_CASE_N": summary["UNKNOWN_CASE_N"],
            "RESUME_PARITY_PASS": summary["RESUME_PARITY_PASS"],
            "CANARY_EXECUTION_COMPLETE": summary["CANARY_EXECUTION_COMPLETE"],
            "FULL_EXTRACTION_ENGINE_READY": summary["FULL_EXTRACTION_ENGINE_READY"],
        },
        "CLASSIFICATION_COUNTS": {
            "EXPECTED_BAR": summary["EXPECTED_BAR_CASE_N"],
            "NOT_EXPECTED_BAR": summary["NOT_EXPECTED_BAR_CASE_N"],
            "UNKNOWN": summary["UNKNOWN_CASE_N"],
        },
        "BASIS_COUNTS": summary["BASIS_COUNTS"],
        "STATUS_COUNTS": summary["STATUS_COUNTS"],
        "CHECKPOINT_HISTORY": checkpoint.get("history", []),
        "FAIL_CLOSED_RULE": "provider failure, duplicate, invalid, missing, corrupt, or authority mismatch prevents clean COMPLETE",
    }


def build_report(
    canary_manifest: dict[str, Any],
    canary_manifest_hash: str,
    summary: dict[str, Any],
    *,
    test_result: str,
    py_compile_result: str,
    diff_check_result: str,
) -> dict[str, Any]:
    return {
        "REPORT": TASK_NAME,
        "AUTHOR_STATUS": "PASS_PENDING_INDEPENDENT_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "PLAN_COMMIT": PLAN_COMMIT,
        "PLAN_AUTHORITY": "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1",
        "DAILY_INPUT_MANIFEST_HASH": DAILY_INPUT_MANIFEST_HASH,
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "TRADING_DATESET_HASH": TRADING_DATESET_HASH,
        "LIFECYCLE_AUTHORITY_HASH": LIFECYCLE_AUTHORITY_HASH,
        "LIFECYCLE_SESSION_KEYSET_HASH": LIFECYCLE_SESSION_KEYSET_HASH,
        "FULL_REQUEST_N": FULL_REQUEST_N,
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "CANARY_REQUEST_N": CANARY_REQUEST_N,
        "CANARY_REQUEST_MANIFEST_HASH": canary_manifest_hash,
        "NETWORK_REQUEST_N": summary["NETWORK_REQUEST_N"],
        "COMPLETE_REQUEST_N": summary["COMPLETE_REQUEST_N"],
        "FAILED_REQUEST_N": summary["FAILED_REQUEST_N"],
        "PROVIDER_FAILED_REQUEST_N": summary["PROVIDER_FAILED_REQUEST_N"],
        "DUPLICATE_PROVIDER_KEY_N": summary["DUPLICATE_PROVIDER_KEY_N"],
        "INVALID_PROVIDER_ROW_N": summary["INVALID_PROVIDER_ROW_N"],
        "EXPECTED_BAR_CASE_N": summary["EXPECTED_BAR_CASE_N"],
        "NOT_EXPECTED_BAR_CASE_N": summary["NOT_EXPECTED_BAR_CASE_N"],
        "UNKNOWN_CASE_N": summary["UNKNOWN_CASE_N"],
        "CANARY_EXECUTION_COMPLETE": summary["CANARY_EXECUTION_COMPLETE"],
        "RESUME_PARITY_PASS": summary["RESUME_PARITY_PASS"],
        "RESUME_SKIPPED_COMPLETE_N": summary["RESUME_SKIPPED_COMPLETE_N"],
        "RESUME_REFETCHED_N": summary["RESUME_REFETCHED_N"],
        "STAGING_EXECUTION_RECEIPT_HASH": summary.get("STAGING_EXECUTION_RECEIPT_HASH"),
        "STAGING_QUALITY_REPORT_HASH": summary.get("STAGING_QUALITY_REPORT_HASH"),
        "FULL_EXTRACTION_ENGINE_READY": summary["FULL_EXTRACTION_ENGINE_READY"],
        "FULL_EXTRACTION_EXECUTED": False,
        "SAFETY": {
            "NETWORK_PROVIDER_DATA_FETCH": "BAOSTOCK_BOUNDED_CANARY_ONLY",
            "CANONICAL_WRITE_EXECUTED": False,
            "CANONICAL_BYTES_MUTATED": False,
            "R4A9_CHECKPOINT_MUTATED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRECLOSE_COMPLETE": False,
            "PRODUCTION": False,
            "FORWARD": False,
            "TRADEPLAN": False,
        },
        "REQUEST_CONTRACT": {
            "source": "committed V01.1 request row",
            "fields": QUERY_FIELDS,
            "frequency": QUERY_FREQUENCY,
            "adjustflag": QUERY_ADJUSTFLAG,
            "current_canonical_gaps_used": False,
        },
        "RETRY_CONTRACT": {
            "MAX_RETRY": MAX_RETRY,
            "MAX_ATTEMPT_N": MAX_ATTEMPT_N,
            "REQUEST_INTERVAL_SECONDS": REQUEST_INTERVAL_SECONDS,
            "CONCURRENCY": CONCURRENCY,
            "CHECKPOINT_INTERVAL": CHECKPOINT_INTERVAL,
        },
        "CLASSIFICATION_CONTRACT": {
            "tradestatus_1": "EXPECTED_BAR",
            "tradestatus_0": "NOT_EXPECTED_BAR",
            "row_absent_inside_lifetime": "UNKNOWN",
            "invalid_tradestatus": "UNKNOWN + INVALID_TRADESTATUS",
            "UNKNOWN_NOT_NOT_EXPECTED": True,
        },
        "TEST_RESULT": test_result,
        "PY_COMPILE": py_compile_result,
        "GIT_DIFF_CHECK": diff_check_result,
    }


def report_markdown(report: dict[str, Any]) -> str:
    safety = report["SAFETY"]
    lines = [
        f"# {TASK_NAME}",
        "",
        "Bounded live BaoStock canary only. Full 48,345-request extraction was not executed; canonical and R4A9 state were not written.",
        "",
        f"- AUTHOR_STATUS: `{report['AUTHOR_STATUS']}`",
        f"- BASE_HEAD / PLAN_COMMIT: `{report['BASE_HEAD']}` / `{report['PLAN_COMMIT']}`",
        f"- DAILY_INPUT_MANIFEST_HASH: `{report['DAILY_INPUT_MANIFEST_HASH']}`",
        f"- FORMAL_IDENTITY_HASH: `{report['FORMAL_IDENTITY_HASH']}`",
        f"- TRADING_DATESET_HASH: `{report['TRADING_DATESET_HASH']}`",
        f"- LIFECYCLE_AUTHORITY_HASH: `{report['LIFECYCLE_AUTHORITY_HASH']}`",
        f"- LIFECYCLE_SESSION_KEYSET_HASH: `{report['LIFECYCLE_SESSION_KEYSET_HASH']}`",
        f"- FULL_REQUEST_N / FULL_REQUEST_MANIFEST_HASH: `{report['FULL_REQUEST_N']}` / `{report['FULL_REQUEST_MANIFEST_HASH']}`",
        f"- CANARY_REQUEST_N / CANARY_REQUEST_MANIFEST_HASH: `{report['CANARY_REQUEST_N']}` / `{report['CANARY_REQUEST_MANIFEST_HASH']}`",
        "",
        "## Execution",
        "",
        f"- NETWORK_REQUEST_N: `{report['NETWORK_REQUEST_N']}`",
        f"- COMPLETE_REQUEST_N / FAILED_REQUEST_N: `{report['COMPLETE_REQUEST_N']}` / `{report['FAILED_REQUEST_N']}`",
        f"- PROVIDER_FAILED_REQUEST_N / DUPLICATE_PROVIDER_KEY_N / INVALID_PROVIDER_ROW_N: `{report['PROVIDER_FAILED_REQUEST_N']}` / `{report['DUPLICATE_PROVIDER_KEY_N']}` / `{report['INVALID_PROVIDER_ROW_N']}`",
        f"- EXPECTED_BAR / NOT_EXPECTED_BAR / UNKNOWN: `{report['EXPECTED_BAR_CASE_N']}` / `{report['NOT_EXPECTED_BAR_CASE_N']}` / `{report['UNKNOWN_CASE_N']}`",
        f"- RESUME_PARITY_PASS: `{str(report['RESUME_PARITY_PASS']).lower()}`",
        f"- RESUME_SKIPPED_COMPLETE_N / RESUME_REFETCHED_N: `{report['RESUME_SKIPPED_COMPLETE_N']}` / `{report['RESUME_REFETCHED_N']}`",
        f"- STAGING_EXECUTION_RECEIPT_HASH: `{report['STAGING_EXECUTION_RECEIPT_HASH']}`",
        f"- STAGING_QUALITY_REPORT_HASH: `{report['STAGING_QUALITY_REPORT_HASH']}`",
        f"- CANARY_EXECUTION_COMPLETE: `{str(report['CANARY_EXECUTION_COMPLETE']).lower()}`",
        f"- FULL_EXTRACTION_ENGINE_READY: `{str(report['FULL_EXTRACTION_ENGINE_READY']).lower()}`",
        "",
        "## Classification contract",
        "",
        "`tradestatus=1` -> `EXPECTED_BAR`; `tradestatus=0` -> `NOT_EXPECTED_BAR`; absent row inside lifetime -> `UNKNOWN`; invalid tradestatus -> `UNKNOWN` with an invalid reason. UNKNOWN is never collapsed to NOT_EXPECTED_BAR.",
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


def run_canary(
    data_root: Path,
    *,
    repo_root: Path = REPO_ROOT,
    stage_root: Path | None = None,
    provider_factory: Callable[[], Any] | None = None,
    stop_after: int | None = None,
    report_only: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
    test_result: str = "PENDING_TARGETED_TESTS",
    py_compile_result: str = "PENDING",
    diff_check_result: str = "PENDING",
) -> dict[str, Any]:
    stage = stage_root or (data_root / "staging" / STAGING_DIRNAME)
    # This is intentionally the first action before any stage mkdir/write.
    stage = require_isolated_staging_root(data_root, stage)
    context = _load_and_verify_authority(repo_root, data_root)
    long_pairs, long_source_hash = _load_long_suspension_source(repo_root)
    selected = select_canary_requests(context["requests"], context["lifecycle_rows"], long_pairs)
    canary_manifest, canary_manifest_hash = build_canary_request_manifest(context, selected, long_source_hash)
    canary_manifest["CANARY_REQUEST_MANIFEST_HASH"] = canary_manifest_hash
    # Guarded root creation happens only after authority and selection passed.
    require_isolated_staging_root(data_root, stage)
    stage.mkdir(parents=True, exist_ok=True)
    if stage.is_symlink() or not stage.is_dir():
        raise CanaryError("ISOLATED_STAGE_ROOT_INVALID_AFTER_MKDIR")
    require_isolated_staging_root(data_root, stage)
    manifest_path = _safe_stage_path(data_root, stage, "request_manifest.json")
    manifest_bytes = canonical_json_bytes(canary_manifest)
    if manifest_path.exists():
        if manifest_path.read_bytes() != manifest_bytes:
            raise CanaryError("CANARY_REQUEST_MANIFEST_EXISTING_BYTES_MISMATCH")
    else:
        _atomic_write(manifest_path, manifest_bytes)

    if report_only:
        if not _checkpoint_path(data_root, stage).is_file():
            raise CanaryError("REPORT_ONLY_CHECKPOINT_MISSING")
        checkpoint = load_or_init_checkpoint(data_root, stage, selected, canary_manifest_hash)
        evidence_run = _resume_evidence_run(checkpoint)
        stage_result = {"checkpoint": checkpoint, "run": evidence_run, "provider_runtime": None}
        stop_after = None
    else:
        stage_result = run_extraction_stage(
            context,
            canary_manifest,
            canary_manifest_hash,
            data_root=data_root,
            stage_root=stage,
            provider_factory=provider_factory,
            stop_after=stop_after,
            sleep_fn=sleep_fn,
        )
    current_run = stage_result["run"]
    summary = _receipt_index_and_summary(
        context,
        canary_manifest,
        data_root=data_root,
        stage_root=stage,
        current_run=current_run,
        checkpoint=stage_result["checkpoint"],
        terminal_requested=stop_after is None,
    )
    summary["RUN_HISTORY"] = stage_result["checkpoint"].get("history", [])
    execution_receipt = build_execution_receipt(
        context, canary_manifest, canary_manifest_hash, summary, stage_root=stage
    )
    quality_report = build_quality_report(canary_manifest, summary, stage_result["checkpoint"])
    execution_hash = write_stage_json(data_root, stage, "execution_receipt.json", execution_receipt)
    quality_hash = write_stage_json(data_root, stage, "quality_report.json", quality_report)
    summary["STAGING_EXECUTION_RECEIPT_HASH"] = execution_hash
    summary["STAGING_QUALITY_REPORT_HASH"] = quality_hash

    if stop_after is None:
        report = build_report(
            canary_manifest,
            canary_manifest_hash,
            summary,
            test_result=test_result,
            py_compile_result=py_compile_result,
            diff_check_result=diff_check_result,
        )
        compact_execution_receipt = {**execution_receipt, "STAGING_EXECUTION_RECEIPT_HASH": execution_hash}
        compact_quality_report = {**quality_report, "STAGING_QUALITY_REPORT_HASH": quality_hash}
        write_repo_json(repo_root, CANARY_MANIFEST_NAME, canary_manifest)
        write_repo_json(repo_root, CANARY_EXECUTION_RECEIPT_NAME, compact_execution_receipt)
        write_repo_json(repo_root, CANARY_QUALITY_REPORT_NAME, compact_quality_report)
        write_repo_json(repo_root, REPORT_NAME, report)
        write_repo_text(repo_root, REPORT_MD_NAME, report_markdown(report))
    return {
        "report": report if stop_after is None else None,
        "summary": summary,
        "canary_manifest": canary_manifest,
        "canary_manifest_hash": canary_manifest_hash,
        "stage_root": str(stage),
    }


def _resume_evidence_run(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Return the terminal run that actually exercised resume refetching."""

    history = checkpoint.get("history", [])
    for entry in reversed(history):
        if entry.get("stop_after") is None and int(entry.get("refetched_n", 0)) > 0:
            return entry
    if history:
        return history[-1]
    raise CanaryError("RESUME_EVIDENCE_MISSING")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path)
    parser.add_argument("--stop-after", type=int)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--test-result", default="PENDING_TARGETED_TESTS")
    parser.add_argument("--py-compile", dest="py_compile_result", default="PENDING")
    parser.add_argument("--diff-check", dest="diff_check_result", default="PENDING")
    args = parser.parse_args(argv)
    if args.stop_after is not None and args.stop_after <= 0:
        parser.error("--stop-after must be positive")
    try:
        result = run_canary(
            args.data_root,
            stage_root=args.stage_root,
            stop_after=args.stop_after,
            report_only=args.report_only,
            test_result=args.test_result,
            py_compile_result=args.py_compile_result,
            diff_check_result=args.diff_check_result,
        )
    except CanaryError as exc:
        print(json.dumps({"AUTHOR_STATUS": "BLOCKED", "NETWORK_REQUEST_N": 0, "ERROR": str(exc)}, sort_keys=True))
        return 2
    summary = result["summary"]
    print(
        json.dumps(
            {
                "CANARY_REQUEST_N": summary["CANARY_REQUEST_N"],
                "CANARY_REQUEST_MANIFEST_HASH": result["canary_manifest_hash"],
                "NETWORK_REQUEST_N": summary["NETWORK_REQUEST_N"],
                "COMPLETE_REQUEST_N": summary["COMPLETE_REQUEST_N"],
                "FAILED_REQUEST_N": summary["FAILED_REQUEST_N"],
                "EXPECTED_BAR_CASE_N": summary["EXPECTED_BAR_CASE_N"],
                "NOT_EXPECTED_BAR_CASE_N": summary["NOT_EXPECTED_BAR_CASE_N"],
                "UNKNOWN_CASE_N": summary["UNKNOWN_CASE_N"],
                "PROVIDER_FAILED_REQUEST_N": summary["PROVIDER_FAILED_REQUEST_N"],
                "DUPLICATE_PROVIDER_KEY_N": summary["DUPLICATE_PROVIDER_KEY_N"],
                "INVALID_PROVIDER_ROW_N": summary["INVALID_PROVIDER_ROW_N"],
                "RESUME_SKIPPED_COMPLETE_N": summary["RESUME_SKIPPED_COMPLETE_N"],
                "RESUME_REFETCHED_N": summary["RESUME_REFETCHED_N"],
                "CANARY_EXECUTION_COMPLETE": summary["CANARY_EXECUTION_COMPLETE"],
                "FULL_EXTRACTION_ENGINE_READY": summary["FULL_EXTRACTION_ENGINE_READY"],
                "FULL_EXTRACTION_EXECUTED": False,
                "STAGE_ROOT": result["stage_root"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
