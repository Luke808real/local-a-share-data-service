#!/usr/bin/env python3
"""Execute the frozen R3 full-session authority extraction.

The provider boundary is deliberately narrow. Authority preflight happens
before BaoStock is imported, and all provider output is written below the
single isolated staging root. The full request set is never reduced from
canonical gaps and the canonical data root is never written.

The existing V01 canary module owns the already-audited provider and
normalization semantics. This module adds the full-run request loop,
hash-verified receipt resume, and deterministic session-authority assembly
without changing those semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
OUTPUT_DIR = REPO_ROOT / "reports" / "implementation"
sys.path.insert(0, str(REPO_ROOT / "tools"))

import run_r3_full_session_completeness_authority_extraction_canary_v01 as canary  # noqa: E402


TASK_NAME = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_V01"
BRANCH = "codex/r3-full-session-completeness-authority-extraction-v01"
BASE_HEAD = "ada99d214a87c34664dc4d1a6b2746cac062fbbe"
EXECUTION_BASE_HEAD = BASE_HEAD
PLAN_COMMIT = "c9b1fc7bd99d9a7c20df350efeb8fd7f88714321"
CANARY_COMMIT = BASE_HEAD
PLAN_AUTHORITY = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_PLAN_V01_1"

DAILY_INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
TRADING_DATESET_HASH = "51d80929eb493d83b709cd667e44809d285bb3e61e7ebfab87e6b677d61fc72d"
LIFECYCLE_AUTHORITY_HASH = "9987c05d2b424bd667e9ff339ca7ef9c712fb42ac9b781a27b72b2ee03337c93"
LIFECYCLE_SESSION_KEY_N = 10_897_229
LIFECYCLE_SESSION_KEYSET_HASH = "eacb645dc8ac6273458fe3a59ea46b6be5072a234f0acf3fabbe2dd7463f2a65"
FULL_REQUEST_N = 48_345
FULL_REQUEST_MANIFEST_HASH = "4654e4282d5191cf680dc6b539025c816bc4d21028a4f2e200230209348412ed"
FORMAL_SYMBOL_N = 5_456
TRADING_DATE_N = 2_580

PROVIDER = "baostock"
PROVIDER_VERSION = "0.9.3"
PROVIDER_RUNTIME = "baostock-0.9.3"
QUERY_FIELDS = "date,code,open,high,low,close,volume,amount,preclose,tradestatus"
QUERY_FREQUENCY = "d"
QUERY_ADJUSTFLAG = "3"

CANARY_REQUEST_N = 100
MAX_RETRY = 2
MAX_ATTEMPT_N = 3
REQUEST_INTERVAL_SECONDS = 1.0
CONCURRENCY = 1
CHECKPOINT_INTERVAL = 1
RETRY_BACKOFF_SECONDS = (1.0, 2.0)
STAGING_DIRNAME = canary.STAGING_DIRNAME

FULL_REQUEST_MANIFEST_STAGE_NAME = "full_request_manifest.json"
FULL_CHECKPOINT_STAGE_NAME = "full_checkpoint.json"
FULL_CHECKPOINT_EVENTS_STAGE_NAME = "full_checkpoint.jsonl"
FULL_RAW_DIRNAME = "full_raw_receipts"
FULL_NORMALIZED_DIRNAME = "full_normalized_receipts"
FULL_EXECUTION_RECEIPT_STAGE_NAME = "full_execution_receipt.json"
FULL_QUALITY_REPORT_STAGE_NAME = "full_quality_report.json"
FULL_RECEIPT_INDEX_STAGE_NAME = "full_request_receipt_index.json"
FULL_CANARY_CACHE_STAGE_NAME = "full_canary_cache_validation.json"
FULL_SESSION_METADATA_STAGE_NAME = "full_session_authority_metadata.json"
SESSION_AUTHORITY_STAGE_NAME = "session_authority.parquet"

REPO_REPORT_NAME = f"{TASK_NAME}.json"
REPO_REPORT_MD_NAME = f"{TASK_NAME}.md"
REPO_EXECUTION_RECEIPT_NAME = f"{TASK_NAME}_EXECUTION_RECEIPT.json"
REPO_QUALITY_REPORT_NAME = f"{TASK_NAME}_QUALITY_REPORT.json"
REPO_RECEIPT_INDEX_NAME = f"{TASK_NAME}_REQUEST_RECEIPT_INDEX.json"
REPO_SESSION_METADATA_NAME = f"{TASK_NAME}_SESSION_AUTHORITY_METADATA.json"

CHECKPOINT_STATES = ("PENDING", "RUNNING", "COMPLETE", "FAILED")
REQUIRED_RECEIPT_FIELDS = canary.REQUIRED_RECEIPT_FIELDS
SESSION_ROW_SERIALIZATION = "canonical JSON UTF-8 rows in request_order/case order, one row plus LF"


class ExtractionError(RuntimeError):
    """Fail-closed full extraction or authority error."""


def canonical_json_bytes(value: Any) -> bytes:
    return canary.canonical_json_bytes(value)


def sha256_bytes(value: bytes) -> str:
    return canary.sha256_bytes(value)


def sha256_json(value: Any) -> str:
    return canary.sha256_json(value)


def sha256_file(path: Path) -> str:
    return canary.sha256_file(path)


def load_json(path: Path) -> Any:
    return canary.load_json(path)


def parse_date(value: Any) -> date | None:
    return canary.parse_date(value)


def parse_int(value: Any) -> int | None:
    return canary.parse_int(value)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ExtractionError(message)


def require_isolated_staging_root(data_root: Path, stage_root: Path) -> Path:
    """Enforce the sole writable root before every stage-side operation."""

    try:
        return canary.require_isolated_staging_root(Path(data_root), Path(stage_root))
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc


def _safe_stage_path(data_root: Path, stage_root: Path, relative_path: str) -> Path:
    try:
        return canary._safe_stage_path(Path(data_root), Path(stage_root), relative_path)
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc


def _safe_repo_path(repo_root: Path, relative_path: str) -> Path:
    try:
        return canary._safe_repo_path(Path(repo_root), relative_path)
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc


def _atomic_write(path: Path, data: bytes) -> None:
    try:
        canary._atomic_write(path, data)
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc


def write_stage_json(data_root: Path, stage_root: Path, relative_path: str, payload: Any) -> str:
    try:
        return canary.write_stage_json(Path(data_root), Path(stage_root), relative_path, payload)
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc


def write_repo_json(repo_root: Path, relative_path: str, payload: Any) -> str:
    try:
        return canary.write_repo_json(Path(repo_root), relative_path, payload)
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc


def write_repo_text(repo_root: Path, relative_path: str, content: str) -> str:
    try:
        return canary.write_repo_text(Path(repo_root), relative_path, content)
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc


def _now_utc() -> str:
    return canary._now_utc()


def _authority_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    input_manifest = context["input_manifest"]
    calendar_manifest = context["trading_calendar_file_manifest"]
    return {
        "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
        "INPUT_FILES": input_manifest["FILES"],
        "FORMAL_SYMBOL_N": len(context["symbols"]),
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "TRADING_DATE_N": len(context["trading_dates"]),
        "TRADING_DATESET_HASH": TRADING_DATESET_HASH,
        "LIFECYCLE_AUTHORITY_HASH": LIFECYCLE_AUTHORITY_HASH,
        "LIFECYCLE_SESSION_KEY_N": context["key_counts"]["LIFECYCLE_SESSION_KEY_N"],
        "LIFECYCLE_SESSION_KEYSET_HASH": LIFECYCLE_SESSION_KEYSET_HASH,
        "TRADING_CALENDAR_FILE_MANIFEST_HASH": calendar_manifest[
            "TRADING_CALENDAR_FILE_MANIFEST_HASH"
        ],
        "TRADING_CALENDAR_FILES": calendar_manifest["FILES"],
        "FULL_REQUEST_N": len(context["requests"]),
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
    }


def load_and_verify_authority(repo_root: Path, data_root: Path) -> dict[str, Any]:
    """Run all frozen V01.1 checks without importing the provider."""

    try:
        context = canary._load_and_verify_authority(Path(repo_root), Path(data_root))
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc
    snapshot = _authority_snapshot(context)
    expected = {
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
    }
    for key, value in expected.items():
        if snapshot.get(key) != value:
            raise ExtractionError(f"AUTHORITY_PREFLIGHT_MISMATCH:{key}")
    if snapshot["INPUT_FILES"] != context["input_manifest"]["FILES"]:
        raise ExtractionError("DAILY_INPUT_FILE_LIST_UNAVAILABLE")
    if len(context["requests"]) != FULL_REQUEST_N:
        raise ExtractionError("FULL_REQUEST_SCOPE_NOT_48345")
    return context


def load_frozen_manifest(repo_root: Path) -> dict[str, Any]:
    path = Path(repo_root) / "reports" / "implementation" / canary.PLAN_REQUEST_MANIFEST_NAME
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ExtractionError("FULL_REQUEST_MANIFEST_NOT_OBJECT")
    persisted = payload.get("FULL_REQUEST_MANIFEST_HASH")
    without_hash = dict(payload)
    without_hash.pop("FULL_REQUEST_MANIFEST_HASH", None)
    if persisted != FULL_REQUEST_MANIFEST_HASH or sha256_json(without_hash) != FULL_REQUEST_MANIFEST_HASH:
        raise ExtractionError("FULL_REQUEST_MANIFEST_ARTIFACT_MISMATCH")
    requests = without_hash.get("requests")
    if not isinstance(requests, list) or len(requests) != FULL_REQUEST_N:
        raise ExtractionError("FULL_REQUEST_MANIFEST_SCOPE_MISMATCH")
    return payload


def ensure_stage_manifest(
    data_root: Path,
    stage_root: Path,
    manifest: dict[str, Any],
) -> str:
    """Persist the exact frozen request manifest, refusing stage drift."""

    path = _safe_stage_path(data_root, stage_root, FULL_REQUEST_MANIFEST_STAGE_NAME)
    if path.exists() or path.is_symlink():
        if not path.is_file():
            raise ExtractionError("FULL_REQUEST_MANIFEST_STAGE_NOT_FILE")
        existing = load_json(path)
        if existing != manifest:
            raise ExtractionError("FULL_REQUEST_MANIFEST_STAGE_DRIFT")
        return sha256_file(path)
    return write_stage_json(data_root, stage_root, FULL_REQUEST_MANIFEST_STAGE_NAME, manifest)


def _receipt_paths(
    data_root: Path,
    stage_root: Path,
    request_id: str,
    *,
    raw_dirname: str = FULL_RAW_DIRNAME,
    normalized_dirname: str = FULL_NORMALIZED_DIRNAME,
) -> tuple[Path, Path]:
    safe_id = str(request_id)
    if not safe_id or "/" in safe_id or "\\" in safe_id or safe_id in {".", ".."}:
        raise ExtractionError("REQUEST_ID_PATH_INVALID")
    return (
        _safe_stage_path(data_root, stage_root, f"{raw_dirname}/{safe_id}.json"),
        _safe_stage_path(data_root, stage_root, f"{normalized_dirname}/{safe_id}.json"),
    )


def _load_receipt_pair(
    data_root: Path,
    stage_root: Path,
    request_id: str,
    *,
    raw_dirname: str = FULL_RAW_DIRNAME,
    normalized_dirname: str = FULL_NORMALIZED_DIRNAME,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    raw_path, normalized_path = _receipt_paths(
        data_root,
        stage_root,
        request_id,
        raw_dirname=raw_dirname,
        normalized_dirname=normalized_dirname,
    )
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


def _full_receipt_inspection(
    data_root: Path,
    stage_root: Path,
    request: dict[str, Any],
    context: dict[str, Any],
    *,
    raw_dirname: str = FULL_RAW_DIRNAME,
    normalized_dirname: str = FULL_NORMALIZED_DIRNAME,
) -> tuple[bool, list[str], tuple[dict[str, Any], dict[str, Any]] | None]:
    pair = _load_receipt_pair(
        data_root,
        stage_root,
        str(request["request_id"]),
        raw_dirname=raw_dirname,
        normalized_dirname=normalized_dirname,
    )
    if pair is None:
        return False, ["MISSING_OR_UNREADABLE_RECEIPT_PAIR"], None
    raw, normalized = pair
    reasons: list[str] = []
    for field in REQUIRED_RECEIPT_FIELDS:
        if field not in raw or field not in normalized or raw.get(field) != normalized.get(field):
            reasons.append(f"RECEIPT_FIELD_MISMATCH:{field}")
    if raw.get("request_id") != request.get("request_id"):
        reasons.append("REQUEST_ID_MISMATCH")
    if raw.get("request_order") != request.get("request_order"):
        reasons.append("REQUEST_ORDER_MISMATCH")
    if raw.get("request") != request or normalized.get("request") != request:
        reasons.append("REQUEST_ROW_MISMATCH")
    if raw.get("request_manifest_hash") != FULL_REQUEST_MANIFEST_HASH:
        reasons.append("FULL_REQUEST_MANIFEST_HASH_MISMATCH")
    if raw.get("lifecycle_session_keyset_hash") != LIFECYCLE_SESSION_KEYSET_HASH:
        reasons.append("LIFECYCLE_SESSION_KEYSET_HASH_MISMATCH")
    if raw.get("status") != "COMPLETE":
        reasons.append("STATUS_NOT_COMPLETE")
    if str(raw.get("provider_error_code")) != "0":
        reasons.append("PROVIDER_ERROR")
    attempt_n = parse_int(raw.get("attempt_n"))
    raw_row_n = parse_int(raw.get("raw_row_n"))
    normalized_row_n = parse_int(raw.get("normalized_row_n"))
    if attempt_n is None or not 1 <= attempt_n <= MAX_ATTEMPT_N:
        reasons.append("ATTEMPT_N_INVALID")
    if raw_row_n is None or raw_row_n < 0:
        reasons.append("RAW_ROW_N_INVALID")
    if normalized_row_n is None or normalized_row_n < 0:
        reasons.append("NORMALIZED_ROW_N_INVALID")
    if not str(raw.get("completed_at", "")).strip():
        reasons.append("COMPLETED_AT_MISSING")
    raw_payload = raw.get("raw_payload")
    normalized_payload = normalized.get("normalized_payload")
    if not isinstance(raw_payload, dict) or not isinstance(normalized_payload, dict):
        reasons.append("PAYLOAD_NOT_OBJECT")
        return False, sorted(set(reasons)), pair
    rows = raw_payload.get("rows")
    if not isinstance(rows, list):
        reasons.append("RAW_ROWS_NOT_LIST")
    elif raw_row_n is not None and raw_row_n != len(rows):
        reasons.append("RAW_ROW_N_MISMATCH")
    if raw.get("raw_sha256") != sha256_json(raw_payload):
        reasons.append("RAW_PAYLOAD_HASH_MISMATCH")
    normalized_rows = normalized_payload.get("NORMALIZED_ROWS")
    cases = normalized_payload.get("CASES")
    metrics = normalized_payload.get("METRICS")
    if not isinstance(normalized_rows, list):
        reasons.append("NORMALIZED_ROWS_NOT_LIST")
    elif normalized_row_n is not None and normalized_row_n != len(normalized_rows):
        reasons.append("NORMALIZED_ROW_N_MISMATCH")
    if normalized.get("normalized_sha256") != sha256_json(normalized_payload):
        reasons.append("NORMALIZED_PAYLOAD_HASH_MISMATCH")
    if not isinstance(cases, list) or not isinstance(metrics, dict):
        reasons.append("NORMALIZED_PAYLOAD_SHAPE_INVALID")
        return False, sorted(set(reasons)), pair
    if metrics.get("DUPLICATE_PROVIDER_KEY_N") != 0:
        reasons.append("DUPLICATE_PROVIDER_KEY")
    if metrics.get("INVALID_PROVIDER_ROW_N") != 0:
        reasons.append("INVALID_PROVIDER_ROW")
    expected_dates = [value.isoformat() for value in canary._expected_session_dates(request, context)]
    if normalized_payload.get("EXPECTED_SESSION_DATES") != expected_dates:
        reasons.append("EXPECTED_SESSION_DATES_MISMATCH")
    if normalized_payload.get("REQUEST_ID") != request.get("request_id"):
        reasons.append("NORMALIZED_REQUEST_ID_MISMATCH")
    if normalized_payload.get("REQUEST_ORDER") != request.get("request_order"):
        reasons.append("NORMALIZED_REQUEST_ORDER_MISMATCH")
    if len(cases) != len(expected_dates):
        reasons.append("CASE_N_MISMATCH")
    else:
        for expected_date, case in zip(expected_dates, cases):
            if (
                not isinstance(case, dict)
                or case.get("symbol") != request.get("symbol")
                or case.get("trade_date") != expected_date
                or case.get("classification") not in {"EXPECTED_BAR", "NOT_EXPECTED_BAR", "UNKNOWN"}
            ):
                reasons.append("CASE_KEY_OR_CLASSIFICATION_INVALID")
                break
            status = case.get("provider_tradestatus")
            status = None if status is None else parse_int(status)
            classification = case.get("classification")
            if classification == "EXPECTED_BAR" and status != 1:
                reasons.append("EXPECTED_BAR_STATUS_MISMATCH")
                break
            if classification == "NOT_EXPECTED_BAR" and status != 0:
                reasons.append("NOT_EXPECTED_BAR_STATUS_MISMATCH")
                break
            if classification == "UNKNOWN" and status is not None:
                reasons.append("UNKNOWN_STATUS_MUST_BE_NULL")
                break
    return not reasons, sorted(set(reasons)), pair


def is_valid_complete_receipt(
    data_root: Path,
    stage_root: Path,
    request: dict[str, Any],
    context: dict[str, Any],
) -> bool:
    valid, _reasons, _pair = _full_receipt_inspection(data_root, stage_root, request, context)
    return valid


def write_full_request_receipts(
    data_root: Path,
    stage_root: Path,
    request: dict[str, Any],
    *,
    context: dict[str, Any],
    fetched: dict[str, Any],
    completed_at: str,
    provider_runtime: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        normalized_payload, metrics = canary.normalize_provider_rows(
            request,
            fetched["raw_rows"],
            provider_error_code=str(fetched["provider_error_code"]),
            provider_error_message=str(fetched["provider_error_message"]),
            context=context,
        )
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc
    status = (
        "COMPLETE"
        if fetched["provider_error_code"] == "0"
        and metrics["DUPLICATE_PROVIDER_KEY_N"] == 0
        and metrics["INVALID_PROVIDER_ROW_N"] == 0
        else "FAILED"
    )
    runtime = dict(provider_runtime or {})
    metadata = {
        "request_id": request["request_id"],
        "request_order": request["request_order"],
        "request_manifest_hash": FULL_REQUEST_MANIFEST_HASH,
        "lifecycle_session_keyset_hash": LIFECYCLE_SESSION_KEYSET_HASH,
        "attempt_n": fetched["attempt_n"],
        "provider_error_code": str(fetched["provider_error_code"]),
        "raw_row_n": len(fetched["raw_rows"]),
        "raw_sha256": sha256_json({"rows": fetched["raw_rows"]}),
        "normalized_row_n": metrics["NORMALIZED_ROW_N"],
        "normalized_sha256": sha256_json(normalized_payload),
        "status": status,
        "completed_at": completed_at,
        "provider": PROVIDER,
        "provider_distribution_version": str(runtime.get("distribution_version", PROVIDER_VERSION)),
        "provider_runtime": str(runtime.get("runtime", PROVIDER_RUNTIME)),
    }
    raw_receipt = {
        **metadata,
        "request": request,
        "attempts": fetched["attempts"],
        "provider_error_message": fetched["provider_error_message"],
        "raw_payload": {"rows": fetched["raw_rows"]},
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


def _request_idset_hash(requests: list[dict[str, Any]]) -> str:
    rows = [[str(row["request_id"]), int(row["request_order"])] for row in requests]
    return sha256_json(rows)


def _checkpoint_paths(data_root: Path, stage_root: Path) -> tuple[Path, Path]:
    return (
        _safe_stage_path(data_root, stage_root, FULL_CHECKPOINT_STAGE_NAME),
        _safe_stage_path(data_root, stage_root, FULL_CHECKPOINT_EVENTS_STAGE_NAME),
    )


def _new_checkpoint(requests: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "TASK": TASK_NAME,
        "FULL_REQUEST_N": len(requests),
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "LIFECYCLE_SESSION_KEYSET_HASH": LIFECYCLE_SESSION_KEYSET_HASH,
        "REQUEST_IDSET_HASH": _request_idset_hash(requests),
        "state_enum": list(CHECKPOINT_STATES),
        "checkpoint_interval": CHECKPOINT_INTERVAL,
        "concurrency": CONCURRENCY,
        "event_n": 0,
        "terminal_complete_n": 0,
        "terminal_failed_n": 0,
        "last_event": None,
        "history": [],
    }


def _read_checkpoint_events(
    events_path: Path,
    request_map: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    events: list[dict[str, Any]] = []
    states: dict[str, str] = {}
    if not events_path.exists():
        return events, states
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ExtractionError("CHECKPOINT_EVENTS_UNREADABLE") from exc
    for line in lines:
        if not line.strip():
            raise ExtractionError("CHECKPOINT_EMPTY_EVENT")
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ExtractionError("CHECKPOINT_EVENT_CORRUPT") from exc
        if not isinstance(event, dict):
            raise ExtractionError("CHECKPOINT_EVENT_NOT_OBJECT")
        request_id = str(event.get("request_id"))
        request = request_map.get(request_id)
        if request is None:
            raise ExtractionError("CHECKPOINT_EVENT_REQUEST_OUTSIDE_MANIFEST")
        if event.get("request_order") != request.get("request_order"):
            raise ExtractionError("CHECKPOINT_EVENT_ORDER_MISMATCH")
        if event.get("request_manifest_hash") != FULL_REQUEST_MANIFEST_HASH:
            raise ExtractionError("CHECKPOINT_EVENT_MANIFEST_HASH_MISMATCH")
        if event.get("lifecycle_session_keyset_hash") != LIFECYCLE_SESSION_KEYSET_HASH:
            raise ExtractionError("CHECKPOINT_EVENT_KEYSET_HASH_MISMATCH")
        if event.get("status") not in CHECKPOINT_STATES:
            raise ExtractionError("CHECKPOINT_EVENT_STATUS_INVALID")
        events.append(event)
        states[request_id] = str(event["status"])
    return events, states


def load_or_init_checkpoint(
    data_root: Path,
    stage_root: Path,
    requests: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    checkpoint_path, events_path = _checkpoint_paths(data_root, stage_root)
    checkpoint_exists = checkpoint_path.exists()
    events_exists = events_path.exists()
    if not checkpoint_exists and not events_exists:
        checkpoint = _new_checkpoint(requests)
        write_stage_json(data_root, stage_root, FULL_CHECKPOINT_STAGE_NAME, checkpoint)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("ab"):
            pass
        return checkpoint, [], {}
    if checkpoint_exists != events_exists:
        raise ExtractionError("CHECKPOINT_PAIR_INCOMPLETE")
    checkpoint = load_json(checkpoint_path)
    if not isinstance(checkpoint, dict):
        raise ExtractionError("CHECKPOINT_CORRUPT")
    if (
        checkpoint.get("TASK") != TASK_NAME
        or checkpoint.get("FULL_REQUEST_N") != len(requests)
        or checkpoint.get("FULL_REQUEST_MANIFEST_HASH") != FULL_REQUEST_MANIFEST_HASH
        or checkpoint.get("LIFECYCLE_SESSION_KEYSET_HASH") != LIFECYCLE_SESSION_KEYSET_HASH
        or checkpoint.get("REQUEST_IDSET_HASH") != _request_idset_hash(requests)
        or checkpoint.get("state_enum") != list(CHECKPOINT_STATES)
        or checkpoint.get("checkpoint_interval") != CHECKPOINT_INTERVAL
        or checkpoint.get("concurrency") != CONCURRENCY
        or not isinstance(checkpoint.get("history"), list)
    ):
        raise ExtractionError("CHECKPOINT_AUTHORITY_MISMATCH")
    request_map = {str(row["request_id"]): row for row in requests}
    events, states = _read_checkpoint_events(events_path, request_map)
    if checkpoint.get("event_n") != len(events):
        checkpoint["event_n"] = len(events)
        checkpoint["last_event"] = events[-1] if events else None
        checkpoint["terminal_complete_n"] = sum(value == "COMPLETE" for value in states.values())
        checkpoint["terminal_failed_n"] = sum(value == "FAILED" for value in states.values())
        write_stage_json(data_root, stage_root, FULL_CHECKPOINT_STAGE_NAME, checkpoint)
    return checkpoint, events, states


def _append_checkpoint_event(
    data_root: Path,
    stage_root: Path,
    checkpoint: dict[str, Any],
    events: list[dict[str, Any]],
    states: dict[str, str],
    request: dict[str, Any],
    status: str,
    *,
    action: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in CHECKPOINT_STATES:
        raise ExtractionError("CHECKPOINT_STATUS_INVALID")
    event = {
        "event_n": len(events) + 1,
        "request_id": request["request_id"],
        "request_order": request["request_order"],
        "request_manifest_hash": FULL_REQUEST_MANIFEST_HASH,
        "lifecycle_session_keyset_hash": LIFECYCLE_SESSION_KEYSET_HASH,
        "status": status,
        "action": action,
        "recorded_at": _now_utc(),
    }
    if extra:
        event.update(extra)
    _checkpoint_paths(data_root, stage_root)
    events_path = _safe_stage_path(data_root, stage_root, FULL_CHECKPOINT_EVENTS_STAGE_NAME)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("ab") as handle:
        handle.write(canonical_json_bytes(event) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    events.append(event)
    states[str(request["request_id"])] = status
    checkpoint["event_n"] = len(events)
    checkpoint["last_event"] = event
    checkpoint["terminal_complete_n"] = sum(value == "COMPLETE" for value in states.values())
    checkpoint["terminal_failed_n"] = sum(value == "FAILED" for value in states.values())
    write_stage_json(data_root, stage_root, FULL_CHECKPOINT_STAGE_NAME, checkpoint)
    return event


def _validate_request_parameters(request: dict[str, Any]) -> None:
    try:
        canary._validate_request_parameters(request)
    except canary.CanaryError as exc:
        raise ExtractionError(str(exc)) from exc


def _provider_result(provider_factory: Callable[[], Any] | None) -> tuple[Any, dict[str, Any]]:
    if provider_factory is None:
        try:
            return canary.load_baostock_provider()
        except canary.CanaryError as exc:
            raise ExtractionError(str(exc)) from exc
    supplied = provider_factory()
    if isinstance(supplied, tuple) and len(supplied) == 2:
        return supplied[0], dict(supplied[1])
    return supplied, {"runtime": PROVIDER_RUNTIME, "distribution_version": PROVIDER_VERSION}


def run_full_stage(
    context: dict[str, Any],
    *,
    data_root: Path,
    stage_root: Path,
    provider_factory: Callable[[], Any] | None = None,
    stop_after: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run pending full requests; only verified COMPLETE pairs are skipped."""

    require_isolated_staging_root(data_root, stage_root)
    requests = list(context["requests"])
    if not requests:
        raise ExtractionError("FULL_REQUESTS_EMPTY")
    request_orders = [int(row["request_order"]) for row in requests]
    if request_orders != list(range(1, len(requests) + 1)):
        raise ExtractionError("FULL_REQUEST_ORDER_NOT_CONTIGUOUS")
    for request in requests:
        _validate_request_parameters(request)
    checkpoint, events, states = load_or_init_checkpoint(data_root, stage_root, requests)
    started_at = _now_utc()
    processed_n = 0
    skipped_complete_n = 0
    refetched_n = 0
    provider_query_attempt_n = 0
    last_query_at: float | None = None
    provider: Any | None = None
    provider_runtime: dict[str, Any] | None = None
    login_error: str | None = None
    logout_error: str | None = None
    stopped_early = False
    interrupted_error: KeyboardInterrupt | None = None

    def ensure_provider() -> Any | None:
        nonlocal provider, provider_runtime, login_error
        if provider is not None or login_error is not None:
            return provider
        try:
            provider, provider_runtime = _provider_result(provider_factory)
        except Exception as exc:
            login_error = f"PROVIDER_LOAD:{type(exc).__name__}:{exc}"
            return None
        try:
            login = provider.login()
            code = str(canary._result_attr(login, "error_code"))
            if code != "0":
                login_error = f"{code}:{canary._result_attr(login, 'error_msg', '')}"
        except Exception as exc:
            login_error = f"EXCEPTION:{type(exc).__name__}:{exc}"
        return provider

    try:
        for request in requests:
            request_id = str(request["request_id"])
            if is_valid_complete_receipt(data_root, stage_root, request, context):
                _append_checkpoint_event(
                    data_root,
                    stage_root,
                    checkpoint,
                    events,
                    states,
                    request,
                    "COMPLETE",
                    action="SKIP_HASH_VERIFIED_COMPLETE",
                )
                processed_n += 1
                skipped_complete_n += 1
                continue
            if stop_after is not None and refetched_n >= stop_after:
                stopped_early = True
                break
            _append_checkpoint_event(
                data_root,
                stage_root,
                checkpoint,
                events,
                states,
                request,
                "RUNNING",
                action="EXECUTION_STARTED",
            )
            processed_n += 1
            refetched_n += 1
            active_provider = ensure_provider()
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
                fetched = canary.fetch_one_request(request, active_provider, sleep_fn=sleep_fn)
                provider_query_attempt_n += int(fetched["provider_query_attempt_n"])
            receipt = write_full_request_receipts(
                data_root,
                stage_root,
                request,
                context=context,
                fetched=fetched,
                completed_at=_now_utc(),
                provider_runtime=provider_runtime,
            )
            _append_checkpoint_event(
                data_root,
                stage_root,
                checkpoint,
                events,
                states,
                request,
                str(receipt["status"]),
                action="TERMINAL_RECEIPT_WRITTEN",
                extra={
                    "attempt_n": receipt["attempt_n"],
                    "provider_error_code": receipt["provider_error_code"],
                    "raw_sha256": receipt["raw_sha256"],
                    "normalized_sha256": receipt["normalized_sha256"],
                    "duplicate_provider_key_n": receipt["metrics"]["DUPLICATE_PROVIDER_KEY_N"],
                    "invalid_provider_row_n": receipt["metrics"]["INVALID_PROVIDER_ROW_N"],
                },
            )
    except KeyboardInterrupt as exc:
        interrupted_error = exc
    finally:
        if provider is not None:
            try:
                logout = provider.logout()
                code = str(canary._result_attr(logout, "error_code", "0"))
                if code != "0":
                    logout_error = f"{code}:{canary._result_attr(logout, 'error_msg', '')}"
            except Exception as exc:
                logout_error = f"EXCEPTION:{type(exc).__name__}:{exc}"

    pending_after_n = len(requests) - sum(value == "COMPLETE" for value in states.values())
    history_entry = {
        "run_order": len(checkpoint["history"]) + 1,
        "started_at": started_at,
        "ended_at": _now_utc(),
        "stop_after": stop_after,
        "processed_n": processed_n,
        "skipped_complete_n": skipped_complete_n,
        "refetched_n": refetched_n,
        "provider_query_attempt_n": provider_query_attempt_n,
        "pending_after_n": pending_after_n,
        "login_error": login_error,
        "logout_error": logout_error,
        "status": "INTERRUPTED" if interrupted_error is not None else ("PARTIAL" if stopped_early else "TERMINAL"),
    }
    checkpoint["history"].append(history_entry)
    checkpoint["last_run"] = history_entry
    checkpoint["updated_at"] = history_entry["ended_at"]
    write_stage_json(data_root, stage_root, FULL_CHECKPOINT_STAGE_NAME, checkpoint)
    if interrupted_error is not None:
        raise interrupted_error
    return {
        "checkpoint": checkpoint,
        "run": history_entry,
        "provider_runtime": provider_runtime,
        "NETWORK_REQUEST_N": sum(
            int(item.get("provider_query_attempt_n", 0)) for item in checkpoint["history"]
        ),
    }


def validate_canary_cache_candidates(
    repo_root: Path,
    data_root: Path,
    stage_root: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Apply the full-manifest predicate to the existing 100 canary pairs."""

    path = Path(repo_root) / "reports" / "implementation" / canary.CANARY_MANIFEST_NAME
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ExtractionError("CANARY_CACHE_MANIFEST_NOT_OBJECT")
    persisted_hash = payload.get("CANARY_REQUEST_MANIFEST_HASH")
    without_hash = dict(payload)
    without_hash.pop("CANARY_REQUEST_MANIFEST_HASH", None)
    if persisted_hash is None or sha256_json(without_hash) != persisted_hash:
        raise ExtractionError("CANARY_CACHE_MANIFEST_HASH_MISMATCH")
    requests = payload.get("REQUESTS")
    if not isinstance(requests, list) or len(requests) != CANARY_REQUEST_N:
        raise ExtractionError("CANARY_CACHE_CANDIDATE_N_NOT_100")
    full_map = {str(row["request_id"]): row for row in context["requests"]}
    result = {
        "CANARY_CACHE_CANDIDATE_N": len(requests),
        "CANARY_CACHE_REUSED_N": 0,
        "CANARY_CACHE_REJECTED_N": 0,
        "REJECTIONS": [],
        "CANARY_REQUEST_MANIFEST_HASH": persisted_hash,
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "SOURCE": "existing stage raw_receipts/normalized_receipts",
    }
    seen: set[str] = set()
    for candidate in requests:
        request_id = str(candidate.get("request_id"))
        if request_id in seen:
            raise ExtractionError("CANARY_CACHE_REQUEST_ID_DUPLICATE")
        seen.add(request_id)
        request = full_map.get(request_id)
        if request is None:
            raise ExtractionError("CANARY_CACHE_REQUEST_OUTSIDE_FULL_MANIFEST")
        valid, reasons, _pair = _full_receipt_inspection(
            data_root,
            stage_root,
            request,
            context,
            raw_dirname="raw_receipts",
            normalized_dirname="normalized_receipts",
        )
        if valid:
            result["CANARY_CACHE_REUSED_N"] += 1
        else:
            result["CANARY_CACHE_REJECTED_N"] += 1
            result["REJECTIONS"].append(
                {
                    "request_id": request_id,
                    "request_order": request["request_order"],
                    "reasons": reasons,
                }
            )
    if len(seen) != CANARY_REQUEST_N:
        raise ExtractionError("CANARY_CACHE_CANDIDATE_SET_MISMATCH")
    result["REJECTIONS"].sort(key=lambda row: int(row["request_order"]))
    return result


def _build_full_receipt_index(
    context: dict[str, Any],
    data_root: Path,
    stage_root: Path,
) -> dict[str, Any]:
    index: list[dict[str, Any]] = []
    status_counts = Counter()
    basis_counts = Counter()
    case_counts = Counter()
    provider_failed_n = 0
    duplicate_n = 0
    invalid_n = 0
    complete_n = 0
    missing_n = 0
    corrupt_n = 0
    for request in context["requests"]:
        request_id = str(request["request_id"])
        valid, reasons, pair = _full_receipt_inspection(
            data_root,
            stage_root,
            request,
            context,
        )
        if pair is None:
            missing_n += 1
            index.append(
                {
                    "request_id": request_id,
                    "request_order": request["request_order"],
                    "status": "MISSING_OR_UNREADABLE",
                    "valid_complete_receipt": False,
                    "reasons": reasons,
                }
            )
            continue
        raw, normalized = pair
        status = str(raw.get("status", "CORRUPT"))
        status_counts[status] += 1
        if str(raw.get("provider_error_code")) != "0":
            provider_failed_n += 1
        payload = normalized.get("normalized_payload")
        metrics = payload.get("METRICS", {}) if isinstance(payload, dict) else {}
        if isinstance(metrics, dict):
            try:
                duplicate_n += int(metrics.get("DUPLICATE_PROVIDER_KEY_N", 0) or 0)
                invalid_n += int(metrics.get("INVALID_PROVIDER_ROW_N", 0) or 0)
            except (TypeError, ValueError):
                corrupt_n += 1
        else:
            corrupt_n += 1
        if isinstance(payload, dict):
            for case in payload.get("CASES", []):
                if isinstance(case, dict):
                    case_counts[str(case.get("classification"))] += 1
                    basis_counts[str(case.get("basis"))] += 1
        if valid:
            complete_n += 1
        elif status == "COMPLETE":
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
                "reasons": reasons,
                "duplicate_provider_key_n": metrics.get("DUPLICATE_PROVIDER_KEY_N", 0)
                if isinstance(metrics, dict)
                else None,
                "invalid_provider_row_n": metrics.get("INVALID_PROVIDER_ROW_N", 0)
                if isinstance(metrics, dict)
                else None,
            }
        )
    receipt_index_hash = sha256_json(index)
    return {
        "TASK": TASK_NAME,
        "FULL_REQUEST_N": len(context["requests"]),
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "LIFECYCLE_SESSION_KEYSET_HASH": LIFECYCLE_SESSION_KEYSET_HASH,
        "REQUESTS": index,
        "REQUEST_RECEIPT_INDEX_HASH": receipt_index_hash,
        "COMPLETE_REQUEST_N": complete_n,
        "FAILED_REQUEST_N": len(context["requests"]) - complete_n,
        "MISSING_OR_UNREADABLE_REQUEST_N": missing_n,
        "CORRUPT_COMPLETE_RECEIPT_N": corrupt_n,
        "PROVIDER_FAILED_REQUEST_N": provider_failed_n,
        "DUPLICATE_PROVIDER_KEY_N": duplicate_n,
        "INVALID_PROVIDER_ROW_N": invalid_n,
        "EXPECTED_BAR_CASE_N": case_counts["EXPECTED_BAR"],
        "NOT_EXPECTED_BAR_CASE_N": case_counts["NOT_EXPECTED_BAR"],
        "UNKNOWN_CASE_N": case_counts["UNKNOWN"],
        "BASIS_COUNTS": dict(sorted(basis_counts.items())),
        "STATUS_COUNTS": dict(sorted(status_counts.items())),
    }


def _iter_expected_session_keys(context: dict[str, Any]) -> Iterable[tuple[str, date]]:
    trading_dates = context["trading_dates"]
    for symbol in sorted(context["symbols"]):
        lifecycle = context["lifecycle_by_symbol"][symbol]
        start = parse_date(lifecycle.get("effective_start"))
        end = parse_date(lifecycle.get("effective_end"))
        if start is None or end is None or start > end:
            continue
        lo = bisect_left(trading_dates, start)
        hi = bisect_right(trading_dates, end)
        for trading_date in trading_dates[lo:hi]:
            yield symbol, trading_date


def _session_schema() -> Any:
    try:
        import pyarrow as pa
    except Exception as exc:
        raise ExtractionError("PYARROW_REQUIRED_FOR_SESSION_AUTHORITY") from exc
    return pa.schema(
        [
            ("symbol", pa.string()),
            ("trade_date", pa.date32()),
            ("classification", pa.string()),
            ("basis", pa.string()),
            ("tradestatus", pa.int64()),
            ("request_id", pa.string()),
            ("provider", pa.string()),
            ("provider_runtime", pa.string()),
            ("provider_distribution_version", pa.string()),
            ("provider_code", pa.string()),
        ]
    )


def assemble_session_authority(
    context: dict[str, Any],
    *,
    data_root: Path,
    stage_root: Path,
) -> dict[str, Any]:
    """Stream verified normalized receipts into the isolated Parquet artifact."""

    require_isolated_staging_root(data_root, stage_root)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as exc:
        raise ExtractionError("PYARROW_REQUIRED_FOR_SESSION_AUTHORITY") from exc
    final_path = _safe_stage_path(data_root, stage_root, SESSION_AUTHORITY_STAGE_NAME)
    temporary_path = _safe_stage_path(
        data_root,
        stage_root,
        f".{SESSION_AUTHORITY_STAGE_NAME}.{os.getpid()}.tmp",
    )
    if temporary_path.exists() or temporary_path.is_symlink():
        raise ExtractionError("SESSION_AUTHORITY_TEMP_ALREADY_EXISTS")
    schema = _session_schema()
    writer = None
    batch: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    actual_key_n = 0
    expected_key_n = 0
    required_key_n = int(
        context.get("key_counts", {}).get("LIFECYCLE_SESSION_KEY_N", LIFECYCLE_SESSION_KEY_N)
    )
    expected_iter = iter(_iter_expected_session_keys(context))
    previous_key: tuple[str, date] | None = None
    try:
        writer = pq.ParquetWriter(str(temporary_path), schema, compression="zstd")
        for request in context["requests"]:
            valid, reasons, pair = _full_receipt_inspection(
                data_root,
                stage_root,
                request,
                context,
            )
            if not valid or pair is None:
                raise ExtractionError(
                    f"SESSION_AUTHORITY_RECEIPT_NOT_COMPLETE:{request['request_id']}:{','.join(reasons)}"
                )
            raw, normalized = pair
            payload = normalized["normalized_payload"]
            cases = payload["CASES"]
            expected_dates = canary._expected_session_dates(request, context)
            if len(cases) != len(expected_dates):
                raise ExtractionError(f"SESSION_AUTHORITY_CASE_COUNT_MISMATCH:{request['request_id']}")
            for case in cases:
                actual_date = parse_date(case.get("trade_date"))
                if actual_date is None:
                    raise ExtractionError("SESSION_AUTHORITY_INVALID_DATE")
                actual_key = (str(case.get("symbol")), actual_date)
                if previous_key is not None and actual_key <= previous_key:
                    raise ExtractionError("SESSION_AUTHORITY_OUTPUT_NOT_STRICTLY_SORTED")
                previous_key = actual_key
                try:
                    expected_key = next(expected_iter)
                except StopIteration as exc:
                    raise ExtractionError("DUPLICATE_SESSION_AUTHORITY_KEY") from exc
                if actual_key != expected_key:
                    raise ExtractionError(
                        f"SESSION_AUTHORITY_KEY_COVERAGE_MISMATCH:{actual_key}:{expected_key}"
                    )
                expected_key_n += 1
                actual_key_n += 1
                status = case.get("provider_tradestatus")
                if status is not None:
                    status = parse_int(status)
                row = {
                    "symbol": actual_key[0],
                    "trade_date": actual_date,
                    "classification": str(case.get("classification")),
                    "basis": str(case.get("basis")),
                    "tradestatus": status,
                    "request_id": str(request["request_id"]),
                    "provider": PROVIDER,
                    "provider_runtime": str(raw.get("provider_runtime", PROVIDER_RUNTIME)),
                    "provider_distribution_version": str(
                        raw.get("provider_distribution_version", PROVIDER_VERSION)
                    ),
                    "provider_code": str(request["bs_code"]),
                }
                digest.update(canonical_json_bytes(row) + b"\n")
                batch.append(row)
                if len(batch) >= 100_000:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch.clear()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
        try:
            next(expected_iter)
        except StopIteration:
            pass
        else:
            raise ExtractionError("MISSING_SESSION_AUTHORITY_KEY")
        if actual_key_n != required_key_n:
            raise ExtractionError(
                f"SESSION_AUTHORITY_KEY_N_MISMATCH:{actual_key_n}:{required_key_n}"
            )
    finally:
        if writer is not None:
            writer.close()
        if temporary_path.exists() and final_path != temporary_path:
            if actual_key_n == required_key_n and expected_key_n == required_key_n:
                os.replace(temporary_path, final_path)
            else:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
    return {
        "SESSION_AUTHORITY_PATH": str(final_path),
        "SESSION_AUTHORITY_KEY_N": actual_key_n,
        "MISSING_SESSION_AUTHORITY_KEY_N": 0,
        "DUPLICATE_SESSION_AUTHORITY_KEY_N": 0,
        "SESSION_AUTHORITY_DATASET_HASH": digest.hexdigest(),
        "SESSION_AUTHORITY_FILE_SHA256": sha256_file(final_path),
        "SESSION_AUTHORITY_FILE_SIZE": final_path.stat().st_size,
        "SERIALIZATION": SESSION_ROW_SERIALIZATION,
    }


def _quality_from_index(
    context: dict[str, Any],
    index: dict[str, Any],
    checkpoint: dict[str, Any],
    *,
    terminal_requested: bool,
    authority_stable: bool,
    session_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    complete_n = int(index["COMPLETE_REQUEST_N"])
    quality_ready = (
        terminal_requested
        and authority_stable
        and complete_n == FULL_REQUEST_N
        and int(index["FAILED_REQUEST_N"]) == 0
        and int(index["PROVIDER_FAILED_REQUEST_N"]) == 0
        and int(index["DUPLICATE_PROVIDER_KEY_N"]) == 0
        and int(index["INVALID_PROVIDER_ROW_N"]) == 0
    )
    return {
        "TASK": TASK_NAME,
        "FULL_REQUEST_N": FULL_REQUEST_N,
        "COMPLETE_REQUEST_N": complete_n,
        "FAILED_REQUEST_N": int(index["FAILED_REQUEST_N"]),
        "PROVIDER_FAILED_REQUEST_N": int(index["PROVIDER_FAILED_REQUEST_N"]),
        "DUPLICATE_PROVIDER_KEY_N": int(index["DUPLICATE_PROVIDER_KEY_N"]),
        "INVALID_PROVIDER_ROW_N": int(index["INVALID_PROVIDER_ROW_N"]),
        "EXPECTED_BAR_CASE_N": int(index["EXPECTED_BAR_CASE_N"]),
        "NOT_EXPECTED_BAR_CASE_N": int(index["NOT_EXPECTED_BAR_CASE_N"]),
        "UNKNOWN_CASE_N": int(index["UNKNOWN_CASE_N"]),
        "FULL_EXTRACTION_COMPLETE": quality_ready,
        "AUTHORITY_STABLE_PRE_POST": authority_stable,
        "UNKNOWN_CASE_ALLOWED_DURING_EXTRACTION": True,
        "UNKNOWN_CASE_BLOCKS_SESSION_KEY_PASS": True,
        "QUALITY_GATE": {
            "ALL_REQUESTS_COMPLETE": complete_n == FULL_REQUEST_N,
            "FAILED_REQUEST_N_REQUIRED": 0,
            "PROVIDER_FAILED_REQUEST_N_REQUIRED": 0,
            "DUPLICATE_PROVIDER_KEY_N_REQUIRED": 0,
            "INVALID_PROVIDER_ROW_N_REQUIRED": 0,
            "SESSION_AUTHORITY_ASSEMBLED_ONLY_AFTER_ALL_COMPLETE": True,
        },
        "SESSION_AUTHORITY": session_metadata,
        "CHECKPOINT_HISTORY": checkpoint.get("history", []),
    }


def build_execution_receipt(
    context: dict[str, Any],
    *,
    stage_result: dict[str, Any],
    cache_result: dict[str, Any],
    index: dict[str, Any],
    quality: dict[str, Any],
    session_metadata: dict[str, Any] | None,
    stage_manifest_file_sha256: str,
) -> dict[str, Any]:
    run = stage_result["run"]
    checkpoint = stage_result["checkpoint"]
    return {
        "TASK": TASK_NAME,
        "AUTHOR_STATUS": "PASS_PENDING_INDEPENDENT_AUDIT",
        "EXECUTION_BASE_HEAD": EXECUTION_BASE_HEAD,
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "PLAN_COMMIT": PLAN_COMMIT,
        "CANARY_COMMIT": CANARY_COMMIT,
        "PLAN_AUTHORITY": PLAN_AUTHORITY,
        "DAILY_INPUT_MANIFEST_HASH": DAILY_INPUT_MANIFEST_HASH,
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "TRADING_DATESET_HASH": TRADING_DATESET_HASH,
        "LIFECYCLE_AUTHORITY_HASH": LIFECYCLE_AUTHORITY_HASH,
        "LIFECYCLE_SESSION_KEY_N": LIFECYCLE_SESSION_KEY_N,
        "LIFECYCLE_SESSION_KEYSET_HASH": LIFECYCLE_SESSION_KEYSET_HASH,
        "FULL_REQUEST_N": FULL_REQUEST_N,
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "STAGE_REQUEST_MANIFEST_FILE_SHA256": stage_manifest_file_sha256,
        "CANARY_CACHE_CANDIDATE_N": cache_result["CANARY_CACHE_CANDIDATE_N"],
        "CANARY_CACHE_REUSED_N": cache_result["CANARY_CACHE_REUSED_N"],
        "CANARY_CACHE_REJECTED_N": cache_result["CANARY_CACHE_REJECTED_N"],
        "NETWORK_REQUEST_N": stage_result["NETWORK_REQUEST_N"],
        "COMPLETE_REQUEST_N": index["COMPLETE_REQUEST_N"],
        "FAILED_REQUEST_N": index["FAILED_REQUEST_N"],
        "PROVIDER_FAILED_REQUEST_N": index["PROVIDER_FAILED_REQUEST_N"],
        "DUPLICATE_PROVIDER_KEY_N": index["DUPLICATE_PROVIDER_KEY_N"],
        "INVALID_PROVIDER_ROW_N": index["INVALID_PROVIDER_ROW_N"],
        "EXPECTED_BAR_CASE_N": index["EXPECTED_BAR_CASE_N"],
        "NOT_EXPECTED_BAR_CASE_N": index["NOT_EXPECTED_BAR_CASE_N"],
        "UNKNOWN_CASE_N": index["UNKNOWN_CASE_N"],
        "CANARY_CACHE_VALIDATION": cache_result,
        "RESUME_SKIPPED_COMPLETE_N": run["skipped_complete_n"],
        "RESUME_REFETCHED_N": run["refetched_n"],
        "RUN_HISTORY": checkpoint.get("history", []),
        "REQUEST_RECEIPT_INDEX_HASH": index["REQUEST_RECEIPT_INDEX_HASH"],
        "SESSION_AUTHORITY": session_metadata,
        "FULL_EXTRACTION_COMPLETE": quality["FULL_EXTRACTION_COMPLETE"],
        "SAFETY": {
            "NETWORK_PROVIDER_DATA_FETCH": "YES",
            "FULL_EXTRACTION_EXECUTED": True,
            "CANONICAL_WRITE_EXECUTED": False,
            "CANONICAL_BYTES_MUTATED": False,
            "R4A9_CHECKPOINT_MUTATED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRECLOSE_COMPLETE": False,
            "PRODUCTION": False,
            "FORWARD": False,
            "TRADEPLAN": False,
        },
        "STAGING_ROOT_RELATIVE": f"staging/{STAGING_DIRNAME}/",
        "RAW_RECEIPTS_RELATIVE": f"{FULL_RAW_DIRNAME}/{{request_id}}.json",
        "NORMALIZED_RECEIPTS_RELATIVE": f"{FULL_NORMALIZED_DIRNAME}/{{request_id}}.json",
        "CHECKPOINT_RELATIVE": FULL_CHECKPOINT_STAGE_NAME,
        "SESSION_AUTHORITY_RELATIVE": SESSION_AUTHORITY_STAGE_NAME,
    }


def report_markdown(report: dict[str, Any]) -> str:
    safety = report["SAFETY"]
    lines = [
        f"# {TASK_NAME}",
        "",
        "Frozen full BaoStock session-authority extraction. Provider receipts and the session authority remain in isolated local staging; canonical and R4A9 state are not written.",
        "",
        f"- AUTHOR_STATUS: {report['AUTHOR_STATUS']}",
        f"- EXECUTION_BASE_HEAD: {report['EXECUTION_BASE_HEAD']}",
        f"- PLAN_COMMIT / CANARY_COMMIT: {report['PLAN_COMMIT']} / {report['CANARY_COMMIT']}",
        f"- FULL_REQUEST_N / FULL_REQUEST_MANIFEST_HASH: {report['FULL_REQUEST_N']} / {report['FULL_REQUEST_MANIFEST_HASH']}",
        f"- NETWORK_REQUEST_N: {report['NETWORK_REQUEST_N']}",
        f"- COMPLETE_REQUEST_N / FAILED_REQUEST_N: {report['COMPLETE_REQUEST_N']} / {report['FAILED_REQUEST_N']}",
        f"- EXPECTED_BAR_CASE_N / NOT_EXPECTED_BAR_CASE_N / UNKNOWN_CASE_N: {report['EXPECTED_BAR_CASE_N']} / {report['NOT_EXPECTED_BAR_CASE_N']} / {report['UNKNOWN_CASE_N']}",
        f"- PROVIDER_FAILED_REQUEST_N / DUPLICATE_PROVIDER_KEY_N / INVALID_PROVIDER_ROW_N: {report['PROVIDER_FAILED_REQUEST_N']} / {report['DUPLICATE_PROVIDER_KEY_N']} / {report['INVALID_PROVIDER_ROW_N']}",
        f"- CANARY_CACHE_CANDIDATE_N / REUSED_N / REJECTED_N: {report['CANARY_CACHE_CANDIDATE_N']} / {report['CANARY_CACHE_REUSED_N']} / {report['CANARY_CACHE_REJECTED_N']}",
        f"- REQUEST_RECEIPT_INDEX_HASH: {report['REQUEST_RECEIPT_INDEX_HASH']}",
        f"- FULL_EXTRACTION_COMPLETE: {str(report['FULL_EXTRACTION_COMPLETE']).lower()}",
        "",
        "## Frozen semantics",
        "",
        "tradestatus=1 -> EXPECTED_BAR; tradestatus=0 -> NOT_EXPECTED_BAR; row absent inside lifetime -> UNKNOWN; invalid status -> UNKNOWN with durable reason. UNKNOWN is not converted to NOT_EXPECTED_BAR and blocks the later session-key pass.",
        "",
        "## Cache and resume",
        "",
        "The 100 existing canary receipt pairs are only cache candidates. A candidate is reusable only when the raw and normalized pair binds the full request-manifest hash and exact lifecycle keyset hash, both payload hashes recompute, and the complete predicate passes. Missing, corrupt, FAILED, RUNNING, or mismatched receipts are refetched.",
        "",
        "## Session authority",
        "",
    ]
    session = report.get("SESSION_AUTHORITY")
    if session:
        lines.extend(
            [
                f"- SESSION_AUTHORITY_KEY_N: {session.get('SESSION_AUTHORITY_KEY_N')}",
                f"- SESSION_AUTHORITY_DATASET_HASH: {session.get('SESSION_AUTHORITY_DATASET_HASH')}",
                f"- MISSING_SESSION_AUTHORITY_KEY_N / DUPLICATE_SESSION_AUTHORITY_KEY_N: {session.get('MISSING_SESSION_AUTHORITY_KEY_N')} / {session.get('DUPLICATE_SESSION_AUTHORITY_KEY_N')}",
                f"- SESSION_AUTHORITY_FILE_SHA256: {session.get('SESSION_AUTHORITY_FILE_SHA256')}",
            ]
        )
    else:
        lines.append("- SESSION_AUTHORITY: not assembled because the full quality gate did not pass.")
    lines.extend(["", "## Safety", ""])
    lines.extend(
        f"- {key}: {str(value).lower() if isinstance(value, bool) else value}"
        for key, value in safety.items()
    )
    lines.extend(["", "## Verification", "", "- Targeted tests and static checks are recorded in the report generated after the extraction.", ""])
    return "\n".join(lines)


def _repo_report_from_execution(
    execution: dict[str, Any],
    quality: dict[str, Any],
    *,
    test_result: str,
    py_compile_result: str,
    diff_check_result: str,
) -> dict[str, Any]:
    report = dict(execution)
    report["REPORT"] = TASK_NAME
    report["TEST_RESULT"] = test_result
    report["PY_COMPILE"] = py_compile_result
    report["GIT_DIFF_CHECK"] = diff_check_result
    report["QUALITY_GATE"] = quality["QUALITY_GATE"]
    report["AUTHORITY_STABLE_PRE_POST"] = quality.get("AUTHORITY_STABLE_PRE_POST")
    report["SESSION_AUTHORITY"] = quality.get("SESSION_AUTHORITY")
    return report


def finalize_repo_artifacts(
    repo_root: Path,
    data_root: Path,
    stage_root: Path,
    *,
    test_result: str = "PENDING_TARGETED_TESTS",
    py_compile_result: str = "PENDING",
    diff_check_result: str = "PENDING",
) -> dict[str, Any]:
    """Copy only compact stage metadata/indexes into tracked reports."""

    require_isolated_staging_root(data_root, stage_root)
    execution = load_json(_safe_stage_path(data_root, stage_root, FULL_EXECUTION_RECEIPT_STAGE_NAME))
    quality = load_json(_safe_stage_path(data_root, stage_root, FULL_QUALITY_REPORT_STAGE_NAME))
    index = load_json(_safe_stage_path(data_root, stage_root, FULL_RECEIPT_INDEX_STAGE_NAME))
    metadata_path = _safe_stage_path(data_root, stage_root, FULL_SESSION_METADATA_STAGE_NAME)
    metadata = load_json(metadata_path) if metadata_path.is_file() else None
    report = _repo_report_from_execution(
        execution,
        quality,
        test_result=test_result,
        py_compile_result=py_compile_result,
        diff_check_result=diff_check_result,
    )
    report["REPORT_COMMIT_NOT_CLAIMED"] = True
    report["STAGING_METADATA_RELATIVE"] = {
        "execution_receipt": FULL_EXECUTION_RECEIPT_STAGE_NAME,
        "quality_report": FULL_QUALITY_REPORT_STAGE_NAME,
        "receipt_index": FULL_RECEIPT_INDEX_STAGE_NAME,
        "session_authority_metadata": FULL_SESSION_METADATA_STAGE_NAME,
    }
    write_repo_json(repo_root, REPO_EXECUTION_RECEIPT_NAME, execution)
    write_repo_json(repo_root, REPO_QUALITY_REPORT_NAME, quality)
    write_repo_json(repo_root, REPO_RECEIPT_INDEX_NAME, index)
    if metadata is not None:
        write_repo_json(repo_root, REPO_SESSION_METADATA_NAME, metadata)
    write_repo_json(repo_root, REPO_REPORT_NAME, report)
    write_repo_text(repo_root, REPO_REPORT_MD_NAME, report_markdown(report))
    return report


def run_full_extraction(
    *,
    repo_root: Path = REPO_ROOT,
    data_root: Path = DATA_ROOT_DEFAULT,
    stage_root: Path | None = None,
    provider_factory: Callable[[], Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Preflight, execute all frozen requests, and assemble on a clean gate."""

    if stage_root is None:
        stage_root = Path(data_root) / "staging" / STAGING_DIRNAME
    require_isolated_staging_root(data_root, stage_root)

    # This is intentionally the first operation that can precede a provider
    # import/login/query. It recomputes all six frozen authority layers.
    context = load_and_verify_authority(repo_root, data_root)
    frozen_manifest = load_frozen_manifest(repo_root)
    stage_manifest_file_sha256 = ensure_stage_manifest(data_root, stage_root, frozen_manifest)
    cache_result = validate_canary_cache_candidates(repo_root, data_root, stage_root, context)
    write_stage_json(data_root, stage_root, FULL_CANARY_CACHE_STAGE_NAME, cache_result)

    pre_snapshot = _authority_snapshot(context)
    stage_result = run_full_stage(
        context,
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=provider_factory,
        sleep_fn=sleep_fn,
    )
    index = _build_full_receipt_index(context, data_root, stage_root)

    authority_stable = True
    post_context: dict[str, Any] | None = None
    post_error: str | None = None
    try:
        post_context = load_and_verify_authority(repo_root, data_root)
        authority_stable = _authority_snapshot(post_context) == pre_snapshot
    except ExtractionError as exc:
        authority_stable = False
        post_error = str(exc)

    terminal_requested = stage_result["run"]["status"] == "TERMINAL"
    session_metadata: dict[str, Any] | None = None
    quality = _quality_from_index(
        context,
        index,
        stage_result["checkpoint"],
        terminal_requested=terminal_requested,
        authority_stable=authority_stable,
        session_metadata=None,
    )
    if quality["FULL_EXTRACTION_COMPLETE"]:
        session_metadata = assemble_session_authority(
            context,
            data_root=data_root,
            stage_root=stage_root,
        )
        quality["SESSION_AUTHORITY"] = session_metadata
        if (
            session_metadata["SESSION_AUTHORITY_KEY_N"] != LIFECYCLE_SESSION_KEY_N
            or session_metadata["MISSING_SESSION_AUTHORITY_KEY_N"] != 0
            or session_metadata["DUPLICATE_SESSION_AUTHORITY_KEY_N"] != 0
        ):
            quality["FULL_EXTRACTION_COMPLETE"] = False
    if post_error is not None:
        quality["POSTFLIGHT_ERROR"] = post_error
    quality["REQUEST_RECEIPT_INDEX_HASH"] = index["REQUEST_RECEIPT_INDEX_HASH"]
    execution = build_execution_receipt(
        context,
        stage_result=stage_result,
        cache_result=cache_result,
        index=index,
        quality=quality,
        session_metadata=session_metadata,
        stage_manifest_file_sha256=stage_manifest_file_sha256,
    )
    execution["FULL_EXTRACTION_COMPLETE"] = quality["FULL_EXTRACTION_COMPLETE"]
    write_stage_json(data_root, stage_root, FULL_RECEIPT_INDEX_STAGE_NAME, index)
    write_stage_json(data_root, stage_root, FULL_QUALITY_REPORT_STAGE_NAME, quality)
    if session_metadata is not None:
        write_stage_json(data_root, stage_root, FULL_SESSION_METADATA_STAGE_NAME, session_metadata)
    write_stage_json(data_root, stage_root, FULL_EXECUTION_RECEIPT_STAGE_NAME, execution)
    return {
        "context": context,
        "stage_result": stage_result,
        "cache_result": cache_result,
        "index": index,
        "quality": quality,
        "execution": execution,
        "session_metadata": session_metadata,
        "post_context": post_context,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path, default=None)
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--test-result", default="PENDING_TARGETED_TESTS")
    parser.add_argument("--py-compile-result", default="PENDING")
    parser.add_argument("--diff-check-result", default="PENDING")
    args = parser.parse_args(argv)
    stage_root = args.stage_root or args.data_root / "staging" / STAGING_DIRNAME
    try:
        if args.finalize:
            report = finalize_repo_artifacts(
                args.repo_root,
                args.data_root,
                stage_root,
                test_result=args.test_result,
                py_compile_result=args.py_compile_result,
                diff_check_result=args.diff_check_result,
            )
            print(json.dumps(report, ensure_ascii=True, sort_keys=True))
            return 0
        result = run_full_extraction(
            repo_root=args.repo_root,
            data_root=args.data_root,
            stage_root=stage_root,
        )
        print(json.dumps(result["execution"], ensure_ascii=True, sort_keys=True))
        return 0 if result["quality"]["FULL_EXTRACTION_COMPLETE"] else 2
    except ExtractionError as exc:
        print(
            json.dumps(
                {
                    "TASK": TASK_NAME,
                    "AUTHOR_STATUS": "BLOCKED_FAIL_CLOSED",
                    "ERROR": str(exc),
                    "NETWORK_REQUEST_N": 0,
                    "CANONICAL_WRITE_EXECUTED": False,
                    "R4A9_RESUME_AUTHORIZED": False,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
