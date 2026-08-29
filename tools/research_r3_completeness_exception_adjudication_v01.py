#!/usr/bin/env python3
"""Adjudicate the bounded R3 completeness exceptions without data mutation.

The tool re-runs the frozen local reconciliation gates, reads the two
canonical-on-NOT_EXPECTED rows and the frozen 39-key UNKNOWN manifest, and
records only compact evidence references and decisions below the repository's
reports directory.  It never imports a provider client, calls a provider, or
writes the data root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_r3_full_session_completeness_reconciliation_v01 as reconciliation  # noqa: E402


TASK = "R3_COMPLETENESS_EXCEPTION_ADJUDICATION_V01"
BRANCH = "codex/r3-completeness-exception-adjudication-v01"
BASE_HEAD = "0e7c290e0a5e80eb44e90192405a716fe2f6dce8"
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
SESSION_AUTHORITY_DATASET_HASH = "0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47"
UNKNOWN_KEYSET_HASH = "fff37a6ac7cff43ad1a9b6ce9265851f56ee9248f10a1f5544ec9cceb633a852"
UNKNOWN_KEY_N = 39
CANONICAL_ON_NOT_EXPECTED_KEY_N = 2
CANONICAL_ON_NOT_EXPECTED_KEYSET_HASH = (
    "c0d7cb5cd104a52d121c74581adf7248f78373500ce36d0c2af9bed168dd2f9e"
)
EMPTY_KEYSET_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

UNKNOWN_MANIFEST_NAME = (
    "R3_FULL_SESSION_COMPLETENESS_RECONCILIATION_V01_UNKNOWN_MANIFEST.json"
)
NOT_EXPECTED_MANIFEST_NAME = (
    "R3_FULL_SESSION_COMPLETENESS_RECONCILIATION_V01_UNEXPECTED_NOT_EXPECTED_KEYS.json"
)
ON_UNKNOWN_MANIFEST_NAME = (
    "R3_FULL_SESSION_COMPLETENESS_RECONCILIATION_V01_CANONICAL_ON_UNKNOWN_KEYS.json"
)
OUTSIDE_MANIFEST_NAME = (
    "R3_FULL_SESSION_COMPLETENESS_RECONCILIATION_V01_CANONICAL_OUTSIDE_AUTHORITY_KEYS.json"
)

ADJUDICATION_MANIFEST_NAME = f"{TASK}_MANIFEST.json"
EVIDENCE_INDEX_NAME = f"{TASK}_EVIDENCE_INDEX.json"
REPORT_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"

CONFLICT_KEYS = (
    ("600651.SH", date(2016, 8, 25)),
    ("688065.SH", date(2023, 6, 15)),
)
PROVIDER = "baostock"
CANONICAL_PROVIDER = "tdx_protocol"
UNKNOWN_BASIS = "PROVIDER_ROW_ABSENT_IN_LIFETIME"


class AdjudicationError(RuntimeError):
    """Terminal fail-closed error."""


def canonical_json_bytes(value: Any) -> bytes:
    def default(item: Any) -> str:
        if isinstance(item, (datetime, date)):
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
        raise AdjudicationError(f"LOCAL_FILE_READ_FAILED:{path}") from exc
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdjudicationError(f"LOCAL_JSON_READ_FAILED:{path}") from exc


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise AdjudicationError(f"INVALID_DATE:{value!r}") from exc


def key_from_row(row: dict[str, Any]) -> tuple[str, date]:
    return str(row["symbol"]), parse_date(row["trade_date"])


def keyset_from_payload(payload: dict[str, Any]) -> list[tuple[str, date]]:
    rows = payload.get("ROWS")
    if rows is None:
        rows = payload.get("KEYS")
    if not isinstance(rows, list):
        raise AdjudicationError("EXCEPTION_MANIFEST_ROWS_UNAVAILABLE")
    keys: list[tuple[str, date]] = []
    for row in rows:
        if not isinstance(row, dict) or "symbol" not in row or "trade_date" not in row:
            raise AdjudicationError("EXCEPTION_MANIFEST_KEY_INVALID")
        keys.append(key_from_row(row))
    return keys


def validate_frozen_exception_manifest(
    payload: dict[str, Any],
    *,
    expected_kind: str,
    expected_n: int,
    expected_hash: str,
) -> list[tuple[str, date]]:
    """Validate a prior exact-key manifest and return its sorted key list."""

    if payload.get("TASK") != reconciliation.TASK:
        raise AdjudicationError("EXCEPTION_MANIFEST_TASK_MISMATCH")
    if payload.get("MANIFEST_KIND") != expected_kind:
        raise AdjudicationError("EXCEPTION_MANIFEST_KIND_MISMATCH")
    if payload.get("KEY_N") != expected_n or payload.get("KEYSET_HASH") != expected_hash:
        raise AdjudicationError("EXCEPTION_MANIFEST_DECLARED_HASH_MISMATCH")
    keys = sorted(keyset_from_payload(payload))
    if len(keys) != expected_n or len(set(keys)) != expected_n:
        raise AdjudicationError("EXCEPTION_MANIFEST_SCOPE_MISMATCH")
    if reconciliation.keyset_hash(keys) != expected_hash:
        raise AdjudicationError("EXCEPTION_MANIFEST_RECOMPUTED_HASH_MISMATCH")
    return keys


def validate_exact_scope(
    actual_keys: list[tuple[str, date]],
    expected_keys: list[tuple[str, date]],
    expected_hash: str,
) -> None:
    """Fail closed unless two bounded key lists are exactly equal."""

    actual = sorted(actual_keys)
    expected = sorted(expected_keys)
    if actual != expected:
        raise AdjudicationError("EXCEPTION_SCOPE_NOT_EXACT")
    if reconciliation.keyset_hash(actual) != expected_hash:
        raise AdjudicationError("EXCEPTION_SCOPE_HASH_MISMATCH")


def unexpected_canonical_total(
    on_not_expected_n: int,
    on_unknown_n: int,
    outside_authority_n: int,
) -> int:
    """Use additive exact-key accounting; no subtraction/cancellation."""

    return on_not_expected_n + on_unknown_n + outside_authority_n


def independence_status(canonical_source: str | None, adjudication_source: str) -> str:
    if canonical_source and canonical_source == adjudication_source:
        return "SAME_SOURCE_NOT_INDEPENDENT_CONFIRMATION"
    if canonical_source is None:
        return "NO_CANONICAL_ROW_NO_INDEPENDENT_CONFIRMATION"
    return "DIFFERENT_PROVIDER_FAMILIES"


def classify_canonical_conflict(canonical_row: dict[str, Any], provider_facts: dict[str, Any]) -> str:
    """Classify only what the frozen row contract and local evidence prove."""

    if provider_facts.get("tradestatus") != 0:
        return "UNRESOLVED"
    try:
        provider_volume = int(str(provider_facts.get("volume")))
        provider_amount = Decimal(str(provider_facts.get("amount")))
        canonical_volume = int(canonical_row["volume"])
        canonical_amount = Decimal(str(canonical_row["amount"]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return "UNRESOLVED"
    if provider_volume == 0 and provider_amount == 0 and canonical_volume == 0 and canonical_amount == 0:
        return "CANONICAL_VALID"
    return "UNRESOLVED"


def adjudicate_unknown_case() -> str:
    """Absent in-lifetime provider rows remain UNKNOWN without other authority."""

    return "UNKNOWN"


def _repo_report_path(repo_root: Path, name: str) -> Path:
    root = Path(repo_root).resolve()
    path = (root / "reports" / "implementation" / name).resolve()
    if root not in path.parents:
        raise AdjudicationError("REPO_OUTPUT_PATH_ESCAPE")
    return path


def write_repo_json(repo_root: Path, name: str, payload: Any) -> str:
    path = _repo_report_path(repo_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(payload) + b"\n"
    path.write_bytes(data)
    return sha256_bytes(data)


def write_repo_text(repo_root: Path, name: str, value: str) -> str:
    path = _repo_report_path(repo_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def _relative_data_path(data_root: Path, value: Any) -> str:
    try:
        return Path(str(value)).resolve().relative_to(Path(data_root).resolve()).as_posix()
    except (ValueError, OSError):
        return str(value)


def _canonical_row_dict(data_root: Path, row: tuple[Any, ...]) -> dict[str, Any]:
    fields = (
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "source",
        "data_version",
        "fetched_at",
        "filename",
    )
    value = dict(zip(fields, row))
    canonical = {
        "symbol": str(value["symbol"]),
        "trade_date": parse_date(value["trade_date"]).isoformat(),
        "open": float(value["open"]),
        "high": float(value["high"]),
        "low": float(value["low"]),
        "close": float(value["close"]),
        "volume": int(value["volume"]),
        "amount": None if value["amount"] is None else float(value["amount"]),
        "source": str(value["source"]),
        "data_version": str(value["data_version"]),
        "fetched_at": str(value["fetched_at"]),
        "preclose": None,
        "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
        "partition_file": _relative_data_path(data_root, value["filename"]),
    }
    canonical["provenance"] = {
        "source": canonical["source"],
        "data_version": canonical["data_version"],
        "fetched_at": canonical["fetched_at"],
    }
    return canonical


def read_canonical_window(data_root: Path, symbol: str, trade_date: date) -> list[dict[str, Any]]:
    try:
        import duckdb
    except Exception as exc:
        raise AdjudicationError("DUCKDB_REQUIRED_FOR_CANONICAL_EVIDENCE") from exc
    glob = (Path(data_root).resolve() / "curated" / "daily_bars" / "**" / "*.parquet").as_posix()
    escaped = glob.replace("'", "''")
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            "SELECT CAST(symbol AS VARCHAR), CAST(trade_date AS VARCHAR), open, high, low, close, "
            "volume, amount, CAST(source AS VARCHAR), CAST(data_version AS VARCHAR), "
            "CAST(fetched_at AS VARCHAR), filename "
            f"FROM read_parquet('{escaped}') "
            "WHERE symbol = ? AND trade_date BETWEEN CAST(? AS DATE) - INTERVAL 7 DAY "
            "AND CAST(? AS DATE) + INTERVAL 7 DAY ORDER BY trade_date",
            [symbol, trade_date.isoformat(), trade_date.isoformat()],
        ).fetchall()
        return [_canonical_row_dict(data_root, row) for row in rows]
    except Exception as exc:
        raise AdjudicationError(f"CANONICAL_EVIDENCE_READ_FAILED:{symbol}:{trade_date}") from exc
    finally:
        connection.close()


def _receipt_path(stage: Path, directory: str, request_id: str) -> Path:
    if not request_id or "/" in request_id or "\\" in request_id or request_id in {".", ".."}:
        raise AdjudicationError("RECEIPT_REQUEST_ID_INVALID")
    return reconciliation._stage_path(stage, f"{directory}/{request_id}.json")


def _receipt_evidence(
    stage: Path,
    request: dict[str, Any],
    target_date: date,
    *,
    expected_classification: str,
) -> dict[str, Any]:
    request_id = str(request["request_id"])
    raw_path = _receipt_path(stage, "full_raw_receipts", request_id)
    normalized_path = _receipt_path(stage, "full_normalized_receipts", request_id)
    if not raw_path.is_file() or not normalized_path.is_file():
        raise AdjudicationError(f"RECEIPT_PAIR_MISSING:{request_id}")
    raw = load_json(raw_path)
    normalized = load_json(normalized_path)
    if not isinstance(raw, dict) or not isinstance(normalized, dict):
        raise AdjudicationError(f"RECEIPT_PAIR_NOT_OBJECT:{request_id}")
    if raw.get("request") != request or normalized.get("request") != request:
        raise AdjudicationError(f"RECEIPT_REQUEST_MISMATCH:{request_id}")
    if raw.get("status") != "COMPLETE" or normalized.get("status") != "COMPLETE":
        raise AdjudicationError(f"RECEIPT_NOT_COMPLETE:{request_id}")
    if str(raw.get("provider_error_code")) != "0" or str(normalized.get("provider_error_code")) != "0":
        raise AdjudicationError(f"RECEIPT_PROVIDER_FAILURE:{request_id}")
    raw_payload = raw.get("raw_payload")
    normalized_payload = normalized.get("normalized_payload")
    if not isinstance(raw_payload, dict) or not isinstance(normalized_payload, dict):
        raise AdjudicationError(f"RECEIPT_PAYLOAD_MISSING:{request_id}")
    if raw.get("raw_sha256") != sha256_json(raw_payload):
        raise AdjudicationError(f"RAW_PAYLOAD_HASH_MISMATCH:{request_id}")
    if normalized.get("normalized_sha256") != sha256_json(normalized_payload):
        raise AdjudicationError(f"NORMALIZED_PAYLOAD_HASH_MISMATCH:{request_id}")
    raw_rows = raw_payload.get("rows")
    cases = normalized_payload.get("CASES")
    if not isinstance(raw_rows, list) or not isinstance(cases, list):
        raise AdjudicationError(f"RECEIPT_ROWS_MISSING:{request_id}")
    raw_target_rows = [row for row in raw_rows if isinstance(row, list) and len(row) > 0 and str(row[0]) == target_date.isoformat()]
    normalized_target_cases = [
        row
        for row in cases
        if isinstance(row, dict) and str(row.get("trade_date")) == target_date.isoformat()
    ]
    if len(normalized_target_cases) != 1:
        raise AdjudicationError(f"NORMALIZED_TARGET_CASE_N:{request_id}:{len(normalized_target_cases)}")
    target_case = normalized_target_cases[0]
    if target_case.get("classification") != expected_classification:
        raise AdjudicationError(f"NORMALIZED_TARGET_CLASSIFICATION_MISMATCH:{request_id}")
    if expected_classification == "UNKNOWN":
        if raw_target_rows:
            raise AdjudicationError(f"UNKNOWN_RAW_ROW_PRESENT:{request_id}")
    elif len(raw_target_rows) != 1:
        raise AdjudicationError(f"RAW_TARGET_ROW_N:{request_id}:{len(raw_target_rows)}")
    evidence = {
        "request_id": request_id,
        "request_order": request["request_order"],
        "request_manifest_hash": raw.get("request_manifest_hash"),
        "lifecycle_session_keyset_hash": raw.get("lifecycle_session_keyset_hash"),
        "provider": raw.get("provider"),
        "provider_runtime": raw.get("provider_runtime"),
        "provider_distribution_version": raw.get("provider_distribution_version"),
        "provider_error_code": raw.get("provider_error_code"),
        "status": raw.get("status"),
        "raw_file": f"staging/r3_full_session_completeness_authority_v01/full_raw_receipts/{request_id}.json",
        "normalized_file": f"staging/r3_full_session_completeness_authority_v01/full_normalized_receipts/{request_id}.json",
        "raw_file_sha256": sha256_file(raw_path),
        "normalized_file_sha256": sha256_file(normalized_path),
        "raw_payload_sha256": raw.get("raw_sha256"),
        "normalized_payload_sha256": normalized.get("normalized_sha256"),
        "provider_row_present": bool(raw_target_rows),
        "normalized_case": target_case,
    }
    if raw_target_rows:
        evidence["raw_target_row"] = raw_target_rows[0]
    evidence["evidence_hash"] = sha256_json({key: value for key, value in evidence.items() if key != "evidence_hash"})
    return evidence


def _provider_facts(evidence: dict[str, Any]) -> dict[str, Any]:
    row = evidence.get("raw_target_row")
    if not isinstance(row, list) or len(row) < 10:
        raise AdjudicationError("PROVIDER_TARGET_ROW_UNAVAILABLE")
    try:
        status = int(str(row[9]))
    except (TypeError, ValueError) as exc:
        raise AdjudicationError("PROVIDER_TRADESTATUS_INVALID") from exc
    return {
        "trade_date": str(row[0]),
        "provider_code": str(row[1]),
        "open": str(row[2]),
        "high": str(row[3]),
        "low": str(row[4]),
        "close": str(row[5]),
        "volume": str(row[6]),
        "amount": str(row[7]),
        "preclose": str(row[8]),
        "tradestatus": status,
        "raw_row": row,
    }


def _request_map(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    requests = context.get("requests")
    if not isinstance(requests, list):
        raise AdjudicationError("REQUEST_MANIFEST_UNAVAILABLE")
    return {str(row["request_id"]): row for row in requests}


def _conflict_entry(
    data_root: Path,
    stage: Path,
    context: dict[str, Any],
    key: tuple[str, date],
    request_map: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    symbol, trade_date = key
    canonical_rows = read_canonical_window(data_root, symbol, trade_date)
    target_rows = [row for row in canonical_rows if row["trade_date"] == trade_date.isoformat()]
    if len(target_rows) != 1:
        raise AdjudicationError(f"CANONICAL_TARGET_ROW_N:{symbol}:{trade_date}:{len(target_rows)}")
    target = target_rows[0]
    # The preceding reconciliation scan has already checked the exact authority
    # keyset.  The request id is obtained from the matching normalized receipt
    # case through the frozen exception's authority row below.
    request_id = None
    for candidate in context["requests"]:
        if candidate.get("symbol") == symbol:
            request_id = str(candidate["request_id"])
            # The yearly request containing the target date is selected below.
            if str(candidate.get("start_date")) <= trade_date.isoformat() <= str(candidate.get("end_date")):
                break
    if request_id is None:
        raise AdjudicationError(f"CONFLICT_REQUEST_NOT_FOUND:{symbol}:{trade_date}")
    request = request_map[request_id]
    evidence = _receipt_evidence(
        stage,
        request,
        trade_date,
        expected_classification="NOT_EXPECTED_BAR",
    )
    authority_case = evidence["normalized_case"]
    provider_facts = _provider_facts(evidence)
    decision = classify_canonical_conflict(target, provider_facts)
    if decision == "CANONICAL_VALID":
        reason = (
            "TDX canonical row is a structurally accepted zero-volume/zero-amount "
            "placeholder; frozen R3 excludes zero-volume placeholders from coverage "
            "evidence, while BaoStock tradestatus=0 agrees. Retain the physical row; "
            "it is not an EXPECTED_BAR and is not a delete repair target."
        )
        independence = "DIFFERENT_PROVIDER_FAMILIES"
    else:
        reason = (
            "BaoStock tradestatus=0 conflicts with a positive-volume TDX canonical row "
            "and the provider OHLCV values are not identical. No independent local "
            "status authority resolves the conflict; fail closed as UNRESOLVED."
        )
        independence = "DIFFERENT_PROVIDER_FAMILIES_BUT_CONFLICTING_EVIDENCE"
    details = {
        "symbol": symbol,
        "trade_date": trade_date.isoformat(),
        "decision": decision,
        "reason": reason,
        "canonical_source": target["source"],
        "adjudication_source": PROVIDER,
        "same_source_as_frozen_authority": False,
        "independence_status": independence,
        "canonical": target,
        "canonical_adjacent_rows": [
            row for row in canonical_rows if row["trade_date"] != trade_date.isoformat()
        ],
        "session_authority": {
            "classification": authority_case.get("classification"),
            "basis": authority_case.get("basis"),
            "tradestatus": authority_case.get("provider_tradestatus"),
            "request_id": request_id,
            "provider_code": request.get("bs_code"),
        },
        "provider_facts": provider_facts,
        "receipt_evidence": {
            key: value
            for key, value in evidence.items()
            if key != "raw_target_row" and key != "normalized_case"
        },
    }
    details["evidence_hash"] = sha256_json({key: value for key, value in details.items() if key != "evidence_hash"})
    manifest_row = {
        "exception_kind": "CANONICAL_ON_NOT_EXPECTED",
        "symbol": symbol,
        "trade_date": trade_date.isoformat(),
        "input_classification": authority_case.get("classification"),
        "input_basis": authority_case.get("basis"),
        "input_tradestatus": authority_case.get("provider_tradestatus"),
        "request_id": request_id,
        "decision": decision,
        "evidence_hash": details["evidence_hash"],
        "canonical_source": target["source"],
        "adjudication_source": PROVIDER,
        "independence_status": independence,
    }
    return details, manifest_row, evidence


def _unknown_entry(
    stage: Path,
    context: dict[str, Any],
    row: dict[str, Any],
    request_map: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    symbol = str(row["symbol"])
    trade_date = parse_date(row["trade_date"])
    request_id = str(row["request_id"])
    request = request_map.get(request_id)
    if request is None:
        raise AdjudicationError(f"UNKNOWN_REQUEST_NOT_IN_MANIFEST:{request_id}")
    evidence = _receipt_evidence(
        stage,
        request,
        trade_date,
        expected_classification="UNKNOWN",
    )
    lifecycle = context.get("lifecycle_by_symbol", {}).get(symbol)
    if not isinstance(lifecycle, dict):
        raise AdjudicationError(f"UNKNOWN_LIFECYCLE_UNAVAILABLE:{symbol}")
    decision = adjudicate_unknown_case()
    evidence_basis = {
        "authority_dataset_hash": SESSION_AUTHORITY_DATASET_HASH,
        "receipt_evidence_hash": evidence["evidence_hash"],
        "lifecycle": lifecycle,
        "canonical_present": False,
        "provider_row_present": False,
        "decision_rule": "absence inside frozen lifetime remains UNKNOWN",
    }
    evidence_hash = sha256_json(evidence_basis)
    details = {
        "exception_kind": "UNKNOWN_SESSION_KEY",
        "symbol": symbol,
        "trade_date": trade_date.isoformat(),
        "input_basis": row["basis"],
        "request_id": request_id,
        "provider_code": row["provider_code"],
        "decision": decision,
        "evidence_source": [
            "full_session_authority.parquet",
            f"full_normalized_receipts/{request_id}.json",
            "frozen lifecycle authority",
            "canonical reconciliation: no canonical row",
        ],
        "evidence_level": "LOCAL_FROZEN_RECEIPT_ONLY",
        "confidence": "INSUFFICIENT_FOR_RECLASSIFICATION",
        "reason": (
            "Provider row is absent inside the frozen in-lifetime key; the frozen "
            "BaoStock receipt is the same provider family as the session authority, "
            "and no independent local status source resolves suspension/lifecycle "
            "semantics for this key. UNKNOWN is retained."
        ),
        "canonical_source": "NO_CANONICAL_ROW",
        "adjudication_source": PROVIDER,
        "same_source_as_frozen_authority": True,
        "independence_status": independence_status(None, PROVIDER),
        "lifecycle": lifecycle,
        "receipt_evidence": {
            key: value
            for key, value in evidence.items()
            if key != "normalized_case" and key != "raw_target_row"
        },
        "evidence_hash": evidence_hash,
    }
    manifest_row = {
        "exception_kind": "UNKNOWN_SESSION_KEY",
        "symbol": symbol,
        "trade_date": trade_date.isoformat(),
        "input_basis": row["basis"],
        "request_id": request_id,
        "provider_code": row["provider_code"],
        "decision": decision,
        "evidence_hash": evidence_hash,
        "canonical_source": "NO_CANONICAL_ROW",
        "adjudication_source": PROVIDER,
        "independence_status": details["independence_status"],
    }
    return details, manifest_row, evidence


def _row_eligibility_contract() -> dict[str, Any]:
    return {
        "semantic_layers": {
            "physical_canonical_storage": "the frozen structural checker has no zero-value exclusion; raw/unadjusted rows are accepted when its value constraints pass",
            "actual_traded_coverage": "only positive-volume in-window rows are coverage evidence; zero-volume placeholders are not coverage evidence",
            "external_session_authority": "tradestatus=1 -> EXPECTED_BAR; tradestatus=0 -> NOT_EXPECTED_BAR; in-lifetime absence -> UNKNOWN",
        },
        "decisions": {
            "tradestatus_0_rows": "NOT_EXPECTED_BAR for session completeness; not automatically a physical-row delete",
            "suspension_placeholder_rows": "not excluded by the frozen structural checker, but cannot prove coverage",
            "volume_0_rows": "structurally allowed when non-null and non-negative; not coverage evidence",
            "amount_0_rows": "not rejected by the frozen structural check; not positive-volume coverage evidence",
        },
        "evidence": [
            {
                "source": "docs/contracts/R3_DAILY_FOUNDATION_CONTRACT.md",
                "lines": "15-18,181-182",
                "commit": "6eb39447",
                "finding": "daily_bars is raw/unadjusted v2 with volume/amount fields; zero-volume placeholders are not coverage evidence",
            },
            {
                "source": "docs/plans/R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN.md",
                "lines": "760-767",
                "commit": "d13e2ece",
                "finding": "positive-volume coverage is required and suspension cannot be inferred from volume==0",
            },
            {
                "source": "tools/verify_r3_daily_foundation.py",
                "lines": "124-169",
                "commit": "6eb39447",
                "finding": "structural QA rejects negative/null volume and negative amount, but does not reject zero volume or zero amount",
            },
            {
                "source": "reports/implementation/R3_DAILY_COVERAGE_AND_FALLBACK_CONTRACT_V01.md",
                "lines": "11-12,55-63,75-78",
                "commit": "40bd2411",
                "finding": "expected traded keys are independent BaoStock tradestatus==1 keys; UNKNOWN means no repair",
            },
        ],
    }


def _manifest_record(repo_root: Path, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = _repo_report_path(repo_root, name)
    return {
        "name": name,
        "file_sha256": sha256_file(path),
        "key_n": payload.get("KEY_N"),
        "keyset_hash": payload.get("KEYSET_HASH"),
    }


def _markdown(report: dict[str, Any], adjudication_rows: list[dict[str, Any]]) -> str:
    safety = report["SAFETY"]
    lines = [
        f"# {TASK}",
        "",
        "Bounded offline adjudication of the two canonical-on-NOT_EXPECTED keys and the frozen 39 UNKNOWN session keys.",
        "",
        "## Decision",
        "",
        f"- `CANONICAL_VALID_N={report['CANONICAL_VALID_N']}`, `CANONICAL_INVALID_N={report['CANONICAL_INVALID_N']}`, `CANONICAL_UNRESOLVED_N={report['CANONICAL_UNRESOLVED_N']}`.",
        f"- `UNKNOWN_INPUT_N={report['UNKNOWN_INPUT_N']}`, resolved expected=`{report['UNKNOWN_RESOLVED_EXPECTED_N']}`, resolved not-expected=`{report['UNKNOWN_RESOLVED_NOT_EXPECTED_N']}`, remaining=`{report['UNKNOWN_REMAINING_N']}`.",
        f"- `R3_REFREEZE_RECOMMENDATION={report['R3_REFREEZE_RECOMMENDATION']}`; no repair or deletion was executed.",
        "",
        "## Frozen authority and exact reconciliation",
        "",
        f"- `BASE_HEAD={report['BASE_HEAD']}`; report does not claim a future commit SHA (`REPORT_COMMIT_NOT_CLAIMED={str(report['REPORT_COMMIT_NOT_CLAIMED']).lower()}`).",
        f"- input manifest: `{report['INPUT_FILE_N']}` files / `{report['INPUT_MANIFEST_HASH']}`.",
        f"- session authority: `{report['SESSION_AUTHORITY_KEY_N']}` keys / `{report['SESSION_AUTHORITY_DATASET_HASH']}`.",
        f"- expected / not-expected / unknown: `{report['EXPECTED_KEY_N']}` / `{report['NOT_EXPECTED_KEY_N']}` / `{report['UNKNOWN_KEY_N']}`.",
        f"- canonical rows / unique / duplicate: `{report['CANONICAL_ROW_N']}` / `{report['CANONICAL_UNIQUE_KEY_N']}` / `{report['CANONICAL_DUPLICATE_KEY_N']}`.",
        f"- missing expected=`{report['MISSING_EXPECTED_KEY_N']}`, on not-expected=`{report['CANONICAL_ON_NOT_EXPECTED_KEY_N']}`, on unknown=`{report['CANONICAL_ON_UNKNOWN_KEY_N']}`, outside=`{report['CANONICAL_OUTSIDE_SESSION_AUTHORITY_KEY_N']}`.",
        f"- additive `UNEXPECTED_CANONICAL_TOTAL_N={report['UNEXPECTED_CANONICAL_TOTAL_N']}` (`2 + 0 + 0`).",
        "",
        "## Recovered R3 row-eligibility contract",
        "",
        "R3 has two separate meanings: the frozen structural checker has no zero-value exclusion, while actual-traded completeness coverage requires positive-volume evidence. A zero-volume placeholder is therefore not a traded-session PASS and is not, by itself, a delete instruction.",
    ]
    for item in report["ROW_ELIGIBILITY_CONTRACT"]["evidence"]:
        lines.append(
            f"- `{item['source']}:{item['lines']}` at `{item['commit']}`: {item['finding']}."
        )
    lines.extend(["", "## Two canonical conflicts", ""])
    for detail in report["CANONICAL_CONFLICTS"]:
        lines.extend(
            [
                f"### {detail['symbol']} / {detail['trade_date']}",
                "",
                f"- decision=`{detail['decision']}`; authority=`{detail['session_authority']['classification']}` / `{detail['session_authority']['basis']}` / tradestatus=`{detail['session_authority']['tradestatus']}`; request=`{detail['session_authority']['request_id']}`.",
                f"- canonical_source=`{detail['canonical_source']}`; adjudication_source=`{detail['adjudication_source']}`; independence=`{detail['independence_status']}`.",
                f"- reason: {detail['reason']}",
                "",
                "Canonical target row:",
                "",
                "```json",
                json.dumps(detail["canonical"], ensure_ascii=True, sort_keys=True, indent=2),
                "```",
                "",
                "Provider target facts:",
                "",
                "```json",
                json.dumps(detail["provider_facts"], ensure_ascii=True, sort_keys=True, indent=2),
                "```",
                "",
                "Adjacent canonical rows:",
                "",
                "```json",
                json.dumps(detail["canonical_adjacent_rows"], ensure_ascii=True, sort_keys=True, indent=2),
                "```",
                "",
            ]
        )
    lines.extend(["## 39 UNKNOWN keys", "", "Each key was checked against its frozen receipt, lifecycle row, and canonical absence; no provider/network request was made.", "", "| symbol | trade_date | request_id | decision | evidence level |", "|---|---|---|---|---|"])
    for row in adjudication_rows:
        if row["exception_kind"] == "UNKNOWN_SESSION_KEY":
            lines.append(
                f"| {row['symbol']} | {row['trade_date']} | {row['request_id']} | {row['decision']} | {row.get('evidence_level', 'LOCAL_FROZEN_RECEIPT_ONLY')} |"
            )
    lines.extend(["", "## Safety and verification", ""])
    for key, value in safety.items():
        lines.append(f"- `{key}={str(value).lower() if isinstance(value, bool) else value}`")
    lines.extend(
        [
            "",
            f"- `NETWORK_PROVIDER_REQUEST_N={report['NETWORK_PROVIDER_REQUEST_N']}`.",
            f"- `TEST_RESULT={report['TEST_RESULT']}`; `PY_COMPILE={report['PY_COMPILE']}`; `GIT_DIFF_CHECK={report['GIT_DIFF_CHECK']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def run_adjudication(
    *,
    repo_root: Path = REPO_ROOT,
    data_root: Path = DATA_ROOT_DEFAULT,
    stage_root: Path | None = None,
    test_result: str = "PENDING_TARGETED_TESTS",
    py_compile_result: str = "PENDING",
    diff_check_result: str = "PENDING",
) -> dict[str, Any]:
    if stage_root is None:
        stage_root = Path(data_root) / "staging" / reconciliation.STAGING_DIRNAME

    # This performs the existing exact local authority gate before any report write.
    verified = reconciliation._load_stage_authority(repo_root, data_root, stage_root)
    context = verified["context"]
    pre_input = verified["input_manifest"]
    session = reconciliation.scan_session_authority(verified["session_path"], context)
    canonical = reconciliation.reconcile_canonical(data_root, verified["session_path"])
    try:
        post_input = context["plan"].build_input_file_manifest(Path(data_root))
    except Exception as exc:
        raise AdjudicationError("DAILY_INPUT_POSTSCAN_RECOMPUTE_FAILED") from exc
    if not reconciliation._input_equal(pre_input, post_input):
        raise AdjudicationError("DAILY_INPUT_DRIFT_DURING_ADJUDICATION")
    if pre_input.get("INPUT_MANIFEST_HASH") != INPUT_MANIFEST_HASH:
        raise AdjudicationError("DAILY_INPUT_MANIFEST_DRIFT")
    if session.get("SESSION_AUTHORITY_DATASET_HASH") != SESSION_AUTHORITY_DATASET_HASH:
        raise AdjudicationError("SESSION_AUTHORITY_DATASET_HASH_DRIFT")

    report_dir = Path(repo_root) / "reports" / "implementation"
    unknown_payload = load_json(report_dir / UNKNOWN_MANIFEST_NAME)
    not_expected_payload = load_json(report_dir / NOT_EXPECTED_MANIFEST_NAME)
    on_unknown_payload = load_json(report_dir / ON_UNKNOWN_MANIFEST_NAME)
    outside_payload = load_json(report_dir / OUTSIDE_MANIFEST_NAME)
    frozen_unknown_keys = validate_frozen_exception_manifest(
        unknown_payload,
        expected_kind="UNKNOWN_SESSION_KEYS",
        expected_n=UNKNOWN_KEY_N,
        expected_hash=UNKNOWN_KEYSET_HASH,
    )
    frozen_not_expected_keys = validate_frozen_exception_manifest(
        not_expected_payload,
        expected_kind="CANONICAL_ON_NOT_EXPECTED_KEYS",
        expected_n=CANONICAL_ON_NOT_EXPECTED_KEY_N,
        expected_hash=CANONICAL_ON_NOT_EXPECTED_KEYSET_HASH,
    )
    validate_frozen_exception_manifest(
        on_unknown_payload,
        expected_kind="CANONICAL_ON_UNKNOWN_KEYS",
        expected_n=0,
        expected_hash=EMPTY_KEYSET_HASH,
    )
    validate_frozen_exception_manifest(
        outside_payload,
        expected_kind="CANONICAL_OUTSIDE_SESSION_AUTHORITY_KEYS",
        expected_n=0,
        expected_hash=EMPTY_KEYSET_HASH,
    )
    validate_exact_scope(
        canonical["CANONICAL_ON_NOT_EXPECTED_KEYS"],
        frozen_not_expected_keys,
        CANONICAL_ON_NOT_EXPECTED_KEYSET_HASH,
    )
    validate_exact_scope(
        session["UNKNOWN_ROWS"] and [key_from_row(row) for row in session["UNKNOWN_ROWS"]] or [],
        frozen_unknown_keys,
        UNKNOWN_KEYSET_HASH,
    )
    if [key_from_row(row) for row in unknown_payload["ROWS"]] != frozen_unknown_keys:
        raise AdjudicationError("UNKNOWN_MANIFEST_ROW_ORDER_DRIFT")
    if canonical["CANONICAL_ON_UNKNOWN_KEYS"] or canonical["CANONICAL_OUTSIDE_KEYS"]:
        raise AdjudicationError("CURRENT_EXCEPTION_SCOPE_EXPANDED")

    request_map = _request_map(context)
    conflict_details: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for key in CONFLICT_KEYS:
        if key not in set(frozen_not_expected_keys):
            raise AdjudicationError(f"CONFLICT_KEY_NOT_IN_FROZEN_SCOPE:{key}")
        details, manifest_row, evidence = _conflict_entry(
            data_root, verified["stage"], context, key, request_map
        )
        conflict_details.append(details)
        manifest_rows.append(manifest_row)
        evidence_rows.append(
            {
                "symbol": key[0],
                "trade_date": key[1].isoformat(),
                "request_id": evidence["request_id"],
                "raw_file": evidence["raw_file"],
                "raw_file_sha256": evidence["raw_file_sha256"],
                "normalized_file": evidence["normalized_file"],
                "normalized_file_sha256": evidence["normalized_file_sha256"],
                "evidence_hash": evidence["evidence_hash"],
            }
        )

    unknown_details: list[dict[str, Any]] = []
    unknown_manifest_rows = unknown_payload.get("ROWS")
    if not isinstance(unknown_manifest_rows, list) or len(unknown_manifest_rows) != UNKNOWN_KEY_N:
        raise AdjudicationError("UNKNOWN_MANIFEST_ROWS_INVALID")
    for row in unknown_manifest_rows:
        details, manifest_row, evidence = _unknown_entry(
            verified["stage"], context, row, request_map
        )
        unknown_details.append(details)
        manifest_rows.append(manifest_row)
        evidence_rows.append(
            {
                "symbol": details["symbol"],
                "trade_date": details["trade_date"],
                "request_id": details["request_id"],
                "raw_file": evidence["raw_file"],
                "raw_file_sha256": evidence["raw_file_sha256"],
                "normalized_file": evidence["normalized_file"],
                "normalized_file_sha256": evidence["normalized_file_sha256"],
                "evidence_hash": evidence["evidence_hash"],
            }
        )

    manifest_rows.sort(key=lambda row: (row["symbol"], row["trade_date"], row["exception_kind"]))
    if len(manifest_rows) != 41 or len({(row["symbol"], row["trade_date"]) for row in manifest_rows}) != 41:
        raise AdjudicationError("ADJUDICATION_MANIFEST_SCOPE_MISMATCH")
    manifest_body = {
        "TASK": TASK,
        "MANIFEST_KIND": "R3_COMPLETENESS_EXCEPTION_ADJUDICATION",
        "KEY_N": 41,
        "CANONICAL_CONFLICT_KEY_N": 2,
        "UNKNOWN_INPUT_N": 39,
        "EXCEPTION_KEYSET_HASH": reconciliation.keyset_hash(
            sorted((row["symbol"], parse_date(row["trade_date"])) for row in manifest_rows)
        ),
        "MANIFEST_SERIALIZATION": "canonical JSON UTF-8, sorted keys, compact separators; ROWS sorted by (symbol, trade_date, exception_kind)",
        "ROWS": manifest_rows,
    }
    adjudication_manifest = {
        **manifest_body,
        "ADJUDICATION_MANIFEST_HASH": sha256_json(manifest_body),
    }
    adjudication_hash = adjudication_manifest["ADJUDICATION_MANIFEST_HASH"]
    evidence_rows.sort(key=lambda row: (row["symbol"], row["trade_date"], row["request_id"]))
    evidence_index = {
        "TASK": TASK,
        "MANIFEST_HASH": adjudication_hash,
        "KEY_N": len(evidence_rows),
        "ROWS": evidence_rows,
    }

    canonical_valid_n = sum(row["decision"] == "CANONICAL_VALID" for row in conflict_details)
    canonical_invalid_n = sum(row["decision"] == "CANONICAL_INVALID" for row in conflict_details)
    canonical_unresolved_n = sum(row["decision"] == "UNRESOLVED" for row in conflict_details)
    unknown_expected_n = sum(row["decision"] == "EXPECTED_BAR" for row in unknown_details)
    unknown_not_expected_n = sum(row["decision"] == "NOT_EXPECTED_BAR" for row in unknown_details)
    unknown_remaining_n = sum(row["decision"] == "UNKNOWN" for row in unknown_details)
    reports_manifest = {
        "unknown": _manifest_record(repo_root, UNKNOWN_MANIFEST_NAME, unknown_payload),
        "canonical_on_not_expected": _manifest_record(repo_root, NOT_EXPECTED_MANIFEST_NAME, not_expected_payload),
        "canonical_on_unknown": _manifest_record(repo_root, ON_UNKNOWN_MANIFEST_NAME, on_unknown_payload),
        "canonical_outside_authority": _manifest_record(repo_root, OUTSIDE_MANIFEST_NAME, outside_payload),
    }
    report: dict[str, Any] = {
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_INDEPENDENT_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "REPORT_COMMIT_NOT_CLAIMED": True,
        "INPUT_FILE_N": pre_input["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": pre_input["INPUT_MANIFEST_HASH"],
        "INPUT_MANIFEST_FILES_EQUAL_PRE_POST": reconciliation._input_equal(pre_input, post_input),
        "SESSION_AUTHORITY_KEY_N": session["SESSION_AUTHORITY_KEY_N"],
        "SESSION_AUTHORITY_DATASET_HASH": session["SESSION_AUTHORITY_DATASET_HASH"],
        "EXPECTED_KEY_N": session["EXPECTED_KEY_N"],
        "NOT_EXPECTED_KEY_N": session["NOT_EXPECTED_KEY_N"],
        "UNKNOWN_KEY_N": session["UNKNOWN_KEY_N"],
        "UNKNOWN_KEYSET_HASH": session["UNKNOWN_KEYSET_HASH"],
        "CANONICAL_ROW_N": canonical["CANONICAL_ROW_N"],
        "CANONICAL_UNIQUE_KEY_N": canonical["CANONICAL_UNIQUE_KEY_N"],
        "CANONICAL_DUPLICATE_KEY_N": canonical["CANONICAL_DUPLICATE_KEY_N"],
        "CANONICAL_KEYSET_HASH": canonical["CANONICAL_KEYSET_HASH"],
        "EXPECTED_PRESENT_KEY_N": canonical["EXPECTED_PRESENT_KEY_N"],
        "MISSING_EXPECTED_KEY_N": len(canonical["MISSING_EXPECTED_KEYS"]),
        "CANONICAL_ON_NOT_EXPECTED_KEY_N": len(canonical["CANONICAL_ON_NOT_EXPECTED_KEYS"]),
        "CANONICAL_ON_NOT_EXPECTED_KEYSET_HASH": reconciliation.keyset_hash(
            canonical["CANONICAL_ON_NOT_EXPECTED_KEYS"]
        ),
        "CANONICAL_ON_UNKNOWN_KEY_N": len(canonical["CANONICAL_ON_UNKNOWN_KEYS"]),
        "CANONICAL_OUTSIDE_SESSION_AUTHORITY_KEY_N": len(canonical["CANONICAL_OUTSIDE_KEYS"]),
        "UNEXPECTED_CANONICAL_TOTAL_N": unexpected_canonical_total(
            len(canonical["CANONICAL_ON_NOT_EXPECTED_KEYS"]),
            len(canonical["CANONICAL_ON_UNKNOWN_KEYS"]),
            len(canonical["CANONICAL_OUTSIDE_KEYS"]),
        ),
        "EXCEPTION_MANIFESTS": reports_manifest,
        "ADJUDICATION_MANIFEST": {
            "name": ADJUDICATION_MANIFEST_NAME,
            "hash": adjudication_hash,
            "key_n": 41,
        },
        "EVIDENCE_INDEX": {
            "name": EVIDENCE_INDEX_NAME,
            "hash": sha256_json(evidence_index),
            "key_n": len(evidence_rows),
        },
        "ROW_ELIGIBILITY_CONTRACT": _row_eligibility_contract(),
        "CANONICAL_CONFLICT_KEY_N": 2,
        "CANONICAL_VALID_N": canonical_valid_n,
        "CANONICAL_INVALID_N": canonical_invalid_n,
        "CANONICAL_UNRESOLVED_N": canonical_unresolved_n,
        "UNKNOWN_INPUT_N": UNKNOWN_KEY_N,
        "UNKNOWN_RESOLVED_EXPECTED_N": unknown_expected_n,
        "UNKNOWN_RESOLVED_NOT_EXPECTED_N": unknown_not_expected_n,
        "UNKNOWN_REMAINING_N": unknown_remaining_n,
        "REPAIR_REQUIRED_KEY_N": unknown_expected_n,
        "DELETE_REPAIR_REQUIRED_KEY_N": canonical_invalid_n,
        "R3_REFREEZE_RECOMMENDATION": (
            "RECOMMEND_BOUNDED_REPAIR_RECONCILIATION"
            if canonical_unresolved_n == 0 and unknown_remaining_n == 0
            else "BLOCKED"
        ),
        "NETWORK_PROVIDER_REQUEST_N": 0,
        "CANONICAL_CONFLICTS": conflict_details,
        "UNKNOWN_ADJUDICATION": unknown_details,
        "SAFETY": {
            "FULL_EXTRACTION_EXECUTED": False,
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "BAOSTOCK_EXECUTED": False,
            "TDX_EXECUTED": False,
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

    # All data-root reads and fail-closed gates are complete before repository writes.
    write_repo_json(repo_root, ADJUDICATION_MANIFEST_NAME, adjudication_manifest)
    write_repo_json(repo_root, EVIDENCE_INDEX_NAME, evidence_index)
    write_repo_json(repo_root, REPORT_NAME, report)
    write_repo_text(repo_root, REPORT_MD_NAME, _markdown(report, manifest_rows))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path, default=None)
    parser.add_argument("--test-result", default="PENDING_TARGETED_TESTS")
    parser.add_argument("--py-compile-result", default="PENDING")
    parser.add_argument("--diff-check-result", default="PENDING")
    args = parser.parse_args(argv)
    try:
        report = run_adjudication(
            repo_root=args.repo_root,
            data_root=args.data_root,
            stage_root=args.stage_root,
            test_result=args.test_result,
            py_compile_result=args.py_compile_result,
            diff_check_result=args.diff_check_result,
        )
    except (AdjudicationError, reconciliation.ReconciliationError) as exc:
        print(
            json.dumps(
                {
                    "TASK": TASK,
                    "AUTHOR_STATUS": "BLOCKED_FAIL_CLOSED",
                    "ERROR": str(exc),
                    "NETWORK_PROVIDER_REQUEST_N": 0,
                    "CANONICAL_WRITE_EXECUTED": False,
                    "CANONICAL_BYTES_MUTATED": False,
                    "R4A9_RESUME_AUTHORIZED": False,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
