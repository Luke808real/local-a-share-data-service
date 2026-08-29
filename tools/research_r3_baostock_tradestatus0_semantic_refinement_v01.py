#!/usr/bin/env python3
"""Refine the frozen R3 BaoStock tradestatus=0 semantics offline.

The prior status0 audit owns the frozen receipt-pair verification.  This
module consumes that verified, local-only evidence and independently builds a
three-way status0 partition, numeric/OHLC profiles, a canonical cross-check,
observation-only clustering, and a bounded secondary-validation scope.

No provider client is imported or called and no data-root file is written.
Only repository reports/manifests are emitted after every input gate passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import audit_r3_baostock_tradestatus0_consistency_v01 as prior  # noqa: E402


TASK = "R3_BAOSTOCK_TRADESTATUS0_SEMANTIC_REFINEMENT_V01"
BRANCH = "codex/r3-baostock-tradestatus0-semantic-refinement-v01"
BASE_HEAD = "08548237664a48238dea088768a9d552bb2c8318"
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

STAGING_DIRNAME = "r3_full_session_completeness_authority_v01"
SESSION_AUTHORITY_NAME = "session_authority.parquet"
FULL_RECEIPT_INDEX_NAME = "full_request_receipt_index.json"

INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
SESSION_AUTHORITY_DATASET_HASH = "0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47"
SESSION_AUTHORITY_FILE_SHA256 = "33df049921b8a9158f5910d58020707a39d9493a50ddf252aab6c49ebee85cd8"
REQUEST_RECEIPT_INDEX_HASH = "0884112d77b75469bc0e4145dfde8ad749b4ae16b7d6974e365029bca799ab8b"
REQUEST_RECEIPT_INDEX_FILE_SHA256 = "396011c56bc837ac6d9f5238eda96ab205e8451a4dd9989390babb82a4322261"
FULL_REQUEST_N = 48_345
FULL_RECEIPT_PAIR_HASH = "19404ce35db37169c3ec4e2a16dba900979c1b6d74743956c952e440fc234358"
STATUS0_RELEVANT_RECEIPT_N = 7_053
STATUS0_RELEVANT_RECEIPT_HASH = "ce506b42726a70a55bccb4c8bc9426c62f5cec1ebbfe373e112ef1f6127dafdd"

STATUS0_KEY_N = 187_201
CONTRADICTION_KEY_N = 8
CONTRADICTION_KEYSET_HASH = "6486c6f475c0be95c7882db30a02476cb0974399b0d8d2b6ea43033b3b547e76"
INVALID_NUMERIC_KEY_N = 15_004
INVALID_NUMERIC_KEYSET_HASH = "006d47931ab65628abc6825e86baa2c1659e3661ab54b6fa386d796d4fd8960d"

EXPECTED_KEY_N = 10_709_989
NOT_EXPECTED_KEY_N = 187_201
UNKNOWN_KEY_N = 39
LIFECYCLE_SESSION_KEY_N = 10_897_229

STATUS0_CLASSIFICATION = "NOT_EXPECTED_BAR"
STATUS0_BASIS = "PROVIDER_TRADESTATUS_0"
ZERO_ZERO = "STATUS0_ZERO_ZERO"
CONTRADICTION = "STATUS0_CONTRADICTION"
INDETERMINATE = "STATUS0_NUMERIC_INDETERMINATE"
PARTITIONS = (ZERO_ZERO, CONTRADICTION, INDETERMINATE)
CANONICAL_STATES = (
    "CANONICAL_POSITIVE_VOLUME",
    "CANONICAL_ZERO_VOLUME",
    "CANONICAL_ABSENT",
)

PRIOR_REPORT_NAME = "R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01.json"
PRIOR_CONTRADICTION_NAME = "R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01_CONTRADICTIONS.json"
PRIOR_INVALID_NAME = "R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01_INVALID_NUMERIC.json"

CROSS_MATRIX_NAME = f"{TASK}_CROSS_CANONICAL_MATRIX.json"
SECONDARY_SCOPE_NAME = f"{TASK}_SECONDARY_SCOPE.json"
REPORT_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"
EMPTY_KEYSET_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class RefinementError(RuntimeError):
    """Terminal fail-closed refinement error."""


def canonical_json_bytes(value: Any) -> bytes:
    return prior.canonical_json_bytes(value)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_json_file_payload(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value) + b"\n")


def sha256_file(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise RefinementError(f"LOCAL_FILE_READ_FAILED:{path}") from exc


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise RefinementError(f"LOCAL_JSON_READ_FAILED:{path}") from exc


def write_repo_json(repo_root: Path, name: str, payload: Any) -> str:
    root = Path(repo_root).resolve()
    path = (root / "reports" / "implementation" / name).resolve()
    if root not in path.parents:
        raise RefinementError("REPO_OUTPUT_PATH_ESCAPE")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(payload) + b"\n"
    path.write_bytes(data)
    return sha256_bytes(data)


def write_repo_text(repo_root: Path, name: str, content: str) -> str:
    root = Path(repo_root).resolve()
    path = (root / "reports" / "implementation" / name).resolve()
    if root not in path.parents:
        raise RefinementError("REPO_OUTPUT_PATH_ESCAPE")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def parse_date(value: Any) -> date:
    try:
        return prior.parse_date(value)
    except prior.AuditError as exc:
        raise RefinementError(str(exc)) from exc


def key_of(row: dict[str, Any]) -> tuple[str, date]:
    return str(row["symbol"]), parse_date(row["trade_date"])


def keyset_hash(keys: Iterable[tuple[str, date]]) -> str:
    ordered = sorted(keys)
    if len(set(ordered)) != len(ordered):
        raise RefinementError("KEYSET_DUPLICATE")
    return prior.keyset_hash(ordered) if ordered else EMPTY_KEYSET_HASH


def key_strings(keys: Iterable[tuple[str, date]]) -> list[str]:
    return [f"{symbol}\t{trade_date.isoformat()}" for symbol, trade_date in sorted(keys)]


def is_blank(value: Any) -> bool:
    return value is None or str(value) == ""


def _previous_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (str(row["symbol"]), str(row["trade_date"]), str(row.get("request_id", ""))),
    )


def _manifest_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [key_of(row) for row in rows]
    return {
        "KEY_N": len(rows),
        "KEYSET_HASH": keyset_hash(keys),
    }


def _verify_prior_frozen_artifacts(repo_root: Path) -> dict[str, Any]:
    implementation = Path(repo_root) / "reports" / "implementation"
    report = load_json(implementation / PRIOR_REPORT_NAME)
    if not isinstance(report, dict):
        raise RefinementError("PRIOR_STATUS0_REPORT_NOT_OBJECT")
    expected_report = {
        "INPUT_MANIFEST_HASH": INPUT_MANIFEST_HASH,
        "SESSION_AUTHORITY_DATASET_HASH": SESSION_AUTHORITY_DATASET_HASH,
        "SESSION_AUTHORITY_FILE_SHA256": SESSION_AUTHORITY_FILE_SHA256,
        "REQUEST_RECEIPT_INDEX_HASH": REQUEST_RECEIPT_INDEX_HASH,
        "STATUS0_KEY_N": STATUS0_KEY_N,
        "STATUS0_CONTRADICTION_KEY_N": CONTRADICTION_KEY_N,
        "STATUS0_CONTRADICTION_KEYSET_HASH": CONTRADICTION_KEYSET_HASH,
        "STATUS0_INVALID_NUMERIC_N": INVALID_NUMERIC_KEY_N,
        "STATUS0_INVALID_NUMERIC_MANIFEST": {
            "key_n": INVALID_NUMERIC_KEY_N,
            "keyset_hash": INVALID_NUMERIC_KEYSET_HASH,
        },
    }
    for field, expected in expected_report.items():
        actual = report.get(field)
        if field == "STATUS0_INVALID_NUMERIC_MANIFEST":
            if not isinstance(actual, dict) or any(actual.get(k) != v for k, v in expected.items()):
                raise RefinementError(f"PRIOR_STATUS0_REPORT_DRIFT:{field}")
        elif actual != expected:
            raise RefinementError(f"PRIOR_STATUS0_REPORT_DRIFT:{field}")

    contradiction = load_json(implementation / PRIOR_CONTRADICTION_NAME)
    invalid = load_json(implementation / PRIOR_INVALID_NAME)
    if not isinstance(contradiction, dict) or not isinstance(invalid, dict):
        raise RefinementError("PRIOR_STATUS0_MANIFEST_NOT_OBJECT")
    if (
        contradiction.get("KEY_N") != CONTRADICTION_KEY_N
        or contradiction.get("KEYSET_HASH") != CONTRADICTION_KEYSET_HASH
        or invalid.get("KEY_N") != INVALID_NUMERIC_KEY_N
        or invalid.get("KEYSET_HASH") != INVALID_NUMERIC_KEYSET_HASH
        or not isinstance(contradiction.get("ROWS"), list)
        or not isinstance(invalid.get("ROWS"), list)
    ):
        raise RefinementError("PRIOR_STATUS0_MANIFEST_AUTHORITY_DRIFT")
    if len(contradiction["ROWS"]) != CONTRADICTION_KEY_N or len(invalid["ROWS"]) != INVALID_NUMERIC_KEY_N:
        raise RefinementError("PRIOR_STATUS0_MANIFEST_CARDINALITY_DRIFT")
    return {
        "REPORT": report,
        "CONTRADICTION": contradiction,
        "INVALID": invalid,
        "REPORT_FILE_SHA256": sha256_file(implementation / PRIOR_REPORT_NAME),
        "CONTRADICTION_FILE_SHA256": sha256_file(implementation / PRIOR_CONTRADICTION_NAME),
        "INVALID_FILE_SHA256": sha256_file(implementation / PRIOR_INVALID_NAME),
    }


def _load_and_verify_inputs(repo_root: Path, data_root: Path, stage_root: Path) -> dict[str, Any]:
    try:
        verified = prior.reconciliation._load_stage_authority(repo_root, data_root, stage_root)
    except (prior.reconciliation.ReconciliationError, prior.reconciliation.extraction.ExtractionError) as exc:
        raise RefinementError(f"FROZEN_AUTHORITY_GATE:{exc}") from exc
    context = verified["context"]
    input_manifest = verified["input_manifest"]
    if (
        input_manifest.get("INPUT_FILE_N") != 2_580
        or input_manifest.get("INPUT_MANIFEST_HASH") != INPUT_MANIFEST_HASH
        or len(input_manifest.get("FILES", [])) != 2_580
    ):
        raise RefinementError("INPUT_MANIFEST_DRIFT")
    session = prior.reconciliation.scan_session_authority(verified["session_path"], context)
    if (
        session.get("SESSION_AUTHORITY_KEY_N") != LIFECYCLE_SESSION_KEY_N
        or session.get("SESSION_AUTHORITY_DATASET_HASH") != SESSION_AUTHORITY_DATASET_HASH
        or session.get("SESSION_AUTHORITY_FILE_SHA256") != SESSION_AUTHORITY_FILE_SHA256
        or session.get("NOT_EXPECTED_KEY_N") != NOT_EXPECTED_KEY_N
        or session.get("UNKNOWN_KEY_N") != UNKNOWN_KEY_N
    ):
        raise RefinementError("SESSION_AUTHORITY_DRIFT")
    if verified.get("REQUEST_RECEIPT_INDEX_FILE_SHA256") != REQUEST_RECEIPT_INDEX_FILE_SHA256:
        raise RefinementError("REQUEST_RECEIPT_INDEX_FILE_SHA256_DRIFT")
    if verified["receipt_index"].get("REQUEST_RECEIPT_INDEX_HASH") != REQUEST_RECEIPT_INDEX_HASH:
        raise RefinementError("REQUEST_RECEIPT_INDEX_HASH_DRIFT")
    return {**verified, "context": context, "session": session}


def _scan_frozen_status0(verified: dict[str, Any]) -> dict[str, Any]:
    status0_by_request, authority_counts = prior.collect_status0_keys(verified["session_path"])
    receipt_scan = prior.scan_receipt_pairs(
        verified["stage"],
        verified["context"],
        verified["receipt_index"],
        status0_by_request,
    )
    if (
        receipt_scan.get("FULL_RECEIPT_PAIR_N") != FULL_REQUEST_N
        or receipt_scan.get("FULL_RECEIPT_PAIR_HASH") != FULL_RECEIPT_PAIR_HASH
        or receipt_scan.get("STATUS0_RELEVANT_RECEIPT_N") != STATUS0_RELEVANT_RECEIPT_N
        or receipt_scan.get("STATUS0_RELEVANT_RECEIPT_HASH") != STATUS0_RELEVANT_RECEIPT_HASH
        or receipt_scan.get("STATUS0_ROW_PRESENT_N") != STATUS0_KEY_N
    ):
        raise RefinementError("RECEIPT_PAYLOAD_OR_STATUS0_SCOPE_DRIFT")
    provider_rows = receipt_scan["STATUS0_PROVIDER_ROWS"]
    invalid_rows = receipt_scan["STATUS0_INVALID_ROWS"]
    if len(provider_rows) + len(invalid_rows) != STATUS0_KEY_N:
        raise RefinementError("STATUS0_ROW_PARTITION_INPUT_DRIFT")
    return {
        "status0_by_request": status0_by_request,
        "authority_counts": authority_counts,
        "receipt_scan": receipt_scan,
        "provider_rows": provider_rows,
        "invalid_rows": invalid_rows,
    }


def _enrich_invalid_rows(verified: dict[str, Any], invalid_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover exact OHLC/preclose fields for invalid numeric targets only."""

    by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in invalid_rows:
        by_request[str(row["request_id"])].append(row)
    enriched: list[dict[str, Any]] = []
    stage = verified["stage"]
    for request_id in sorted(by_request):
        raw_path = (stage / prior.FULL_RAW_DIRNAME / f"{request_id}.json").resolve()
        if stage not in raw_path.parents or not raw_path.is_file():
            raise RefinementError(f"INVALID_NUMERIC_RAW_RECEIPT_MISSING:{request_id}")
        raw_receipt = load_json(raw_path)
        rows_by_date: dict[date, list[list[Any]]] = defaultdict(list)
        for raw_row in (raw_receipt.get("raw_payload") or {}).get("rows", []):
            if isinstance(raw_row, list) and raw_row:
                rows_by_date[parse_date(raw_row[0])].append(raw_row)
        for row in sorted(by_request[request_id], key=lambda item: item["trade_date"]):
            target_date = parse_date(row["trade_date"])
            candidates = rows_by_date.get(target_date, [])
            if len(candidates) != 1:
                raise RefinementError(
                    f"INVALID_NUMERIC_RAW_TARGET_CARDINALITY:{request_id}:{target_date}:{len(candidates)}"
                )
            raw = candidates[0]
            current = dict(row)
            current.update(
                {
                    "open": str(raw[2]),
                    "high": str(raw[3]),
                    "low": str(raw[4]),
                    "close": str(raw[5]),
                    "preclose": str(raw[8]),
                    "tradestatus": 0,
                }
            )
            enriched.append(current)
    enriched.sort(key=lambda row: (row["symbol"], row["trade_date"], row["request_id"]))
    keys = [key_of(row) for row in enriched]
    if len(keys) != len(set(keys)):
        raise RefinementError("INVALID_NUMERIC_DUPLICATE_KEY")
    return enriched


def _compact_invalid_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        field: row[field]
        for field in (
            "symbol",
            "trade_date",
            "request_id",
            "request_order",
            "provider_code",
            "volume_raw",
            "amount_raw",
            "reason_code",
        )
    }


def _verify_recomputed_prior_manifests(
    repo_artifacts: dict[str, Any],
    contradiction_rows: list[dict[str, Any]],
    invalid_rows: list[dict[str, Any]],
) -> None:
    contradiction_summary = _manifest_summary(contradiction_rows)
    if contradiction_summary != {"KEY_N": CONTRADICTION_KEY_N, "KEYSET_HASH": CONTRADICTION_KEYSET_HASH}:
        raise RefinementError("CONTRADICTION_FROZEN_SCOPE_DRIFT")
    prior_contradiction_rows = repo_artifacts["CONTRADICTION"]["ROWS"]
    if prior_contradiction_rows != contradiction_rows:
        raise RefinementError("CONTRADICTION_FROZEN_ROWS_DRIFT")

    compact_current = [_compact_invalid_row(row) for row in _previous_rows(invalid_rows)]
    prior_invalid_rows = _previous_rows(repo_artifacts["INVALID"]["ROWS"])
    if compact_current != prior_invalid_rows:
        raise RefinementError("INVALID_NUMERIC_FROZEN_ROWS_DRIFT")
    invalid_summary = _manifest_summary(invalid_rows)
    if invalid_summary != {"KEY_N": INVALID_NUMERIC_KEY_N, "KEYSET_HASH": INVALID_NUMERIC_KEYSET_HASH}:
        raise RefinementError("INVALID_NUMERIC_FROZEN_SCOPE_DRIFT")


def classify_numeric_pattern(volume: Any, amount: Any) -> str:
    """Return one mutually-exclusive invalid numeric pattern."""

    volume_blank = is_blank(volume)
    amount_blank = is_blank(amount)
    if volume_blank and amount_blank:
        return "VOLUME_BLANK_AMOUNT_BLANK"
    if volume_blank:
        return "VOLUME_BLANK_ONLY"
    if amount_blank:
        return "AMOUNT_BLANK_ONLY"
    try:
        volume_decimal = Decimal(str(volume))
        amount_decimal = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return "NON_NUMERIC_NONBLANK"
    if not volume_decimal.is_finite() or not amount_decimal.is_finite():
        return "NONFINITE"
    if volume_decimal < 0 or amount_decimal < 0:
        return "NEGATIVE"
    if volume_decimal != volume_decimal.to_integral_value():
        return "FRACTIONAL_VOLUME"
    return "OTHER"


def build_invalid_pattern_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = (
        "VOLUME_BLANK_AMOUNT_BLANK",
        "VOLUME_BLANK_ONLY",
        "AMOUNT_BLANK_ONLY",
        "NON_NUMERIC_NONBLANK",
        "NEGATIVE",
        "NONFINITE",
        "FRACTIONAL_VOLUME",
        "OTHER",
    )
    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label in labels}
    for row in rows:
        grouped[classify_numeric_pattern(row.get("volume_raw"), row.get("amount_raw"))].append(row)
    profile: dict[str, Any] = {}
    for label in labels:
        group = _previous_rows(grouped[label])
        profile[label] = {
            "KEY_N": len(group),
            "KEYSET_HASH": keyset_hash([key_of(row) for row in group]),
        }
    profile["INVALID_NONBLANK_N"] = sum(
        profile[label]["KEY_N"]
        for label in labels
        if label not in {"VOLUME_BLANK_AMOUNT_BLANK", "VOLUME_BLANK_ONLY", "AMOUNT_BLANK_ONLY"}
    )
    profile["INVALID_BOTH_BLANK_N"] = profile["VOLUME_BLANK_AMOUNT_BLANK"]["KEY_N"]
    profile["INVALID_VOLUME_ONLY_BLANK_N"] = profile["VOLUME_BLANK_ONLY"]["KEY_N"]
    profile["INVALID_AMOUNT_ONLY_BLANK_N"] = profile["AMOUNT_BLANK_ONLY"]["KEY_N"]
    if sum(profile[label]["KEY_N"] for label in labels) != len(rows):
        raise RefinementError("INVALID_PATTERN_PARTITION_DRIFT")
    return profile


def build_ohlc_preclose_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ohlc_state = "OHLC_ALL_BLANK" if all(is_blank(row.get(field)) for field in ("open", "high", "low", "close")) else "OHLC_POPULATED"
        preclose_state = "PRECLOSE_BLANK" if is_blank(row.get("preclose")) else "PRECLOSE_POPULATED"
        grouped[(ohlc_state, preclose_state)].append(row)
    cells: dict[str, Any] = {}
    for ohlc_state in ("OHLC_ALL_BLANK", "OHLC_POPULATED"):
        for preclose_state in ("PRECLOSE_BLANK", "PRECLOSE_POPULATED"):
            group = _previous_rows(grouped[(ohlc_state, preclose_state)])
            cells[f"{ohlc_state}|{preclose_state}"] = {
                "KEY_N": len(group),
                "KEYSET_HASH": keyset_hash([key_of(row) for row in group]),
            }
    ohlc_all_blank = [
        row
        for row in rows
        if all(is_blank(row.get(field)) for field in ("open", "high", "low", "close"))
    ]
    ohlc_populated = [
        row
        for row in rows
        if not all(is_blank(row.get(field)) for field in ("open", "high", "low", "close"))
    ]
    preclose_blank = [row for row in rows if is_blank(row.get("preclose"))]
    preclose_populated = [row for row in rows if not is_blank(row.get("preclose"))]
    if sum(cell["KEY_N"] for cell in cells.values()) != len(rows):
        raise RefinementError("OHLC_PRECLOSE_MATRIX_DRIFT")
    return {
        "OHLC_ALL_BLANK": {"KEY_N": len(ohlc_all_blank), "KEYSET_HASH": keyset_hash([key_of(row) for row in ohlc_all_blank])},
        "OHLC_POPULATED": {"KEY_N": len(ohlc_populated), "KEYSET_HASH": keyset_hash([key_of(row) for row in ohlc_populated])},
        "PRECLOSE_BLANK": {"KEY_N": len(preclose_blank), "KEYSET_HASH": keyset_hash([key_of(row) for row in preclose_blank])},
        "PRECLOSE_POPULATED": {"KEY_N": len(preclose_populated), "KEYSET_HASH": keyset_hash([key_of(row) for row in preclose_populated])},
        "MATRIX": cells,
    }


def build_status0_partitions(
    provider_rows: list[dict[str, Any]],
    invalid_rows: list[dict[str, Any]],
    *,
    expected_key_n: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    zero_zero: list[dict[str, Any]] = []
    contradiction: list[dict[str, Any]] = []
    for row in provider_rows:
        volume = int(row["volume"])
        amount = Decimal(str(row["amount"]))
        if volume == 0 and amount == 0:
            zero_zero.append(row)
        elif volume > 0 or amount > 0:
            contradiction.append(row)
        else:
            raise RefinementError("VALID_NUMERIC_STATUS0_UNCLASSIFIED")
    indeterminate = list(invalid_rows)
    partitions = {
        ZERO_ZERO: _previous_rows(zero_zero),
        CONTRADICTION: _previous_rows(contradiction),
        INDETERMINATE: _previous_rows(indeterminate),
    }
    all_keys = [key_of(row) for rows in partitions.values() for row in rows]
    if len(all_keys) != len(set(all_keys)):
        raise RefinementError("STATUS0_THREE_WAY_PARTITION_NOT_EXHAUSTIVE")
    if expected_key_n is not None and len(all_keys) != expected_key_n:
        raise RefinementError("STATUS0_THREE_WAY_PARTITION_COUNT_DRIFT")
    return partitions


def canonical_state(canonical: dict[tuple[str, date], dict[str, Any]], key: tuple[str, date]) -> str:
    row = canonical.get(key)
    if row is None:
        return "CANONICAL_ABSENT"
    volume = row.get("volume")
    if volume is None or int(volume) < 0:
        raise RefinementError(f"CANONICAL_VOLUME_INVALID:{key}")
    return "CANONICAL_POSITIVE_VOLUME" if int(volume) > 0 else "CANONICAL_ZERO_VOLUME"


def build_cross_canonical_matrix(
    partitions: dict[str, list[dict[str, Any]]],
    canonical: dict[tuple[str, date], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    cells: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    for partition in PARTITIONS:
        summary[partition] = {}
        for state in CANONICAL_STATES:
            rows = [row for row in partitions[partition] if canonical_state(canonical, key_of(row)) == state]
            keys = [key_of(row) for row in rows]
            cell_name = f"{partition}|{state}"
            cells[cell_name] = {
                "PARTITION": partition,
                "CANONICAL_STATE": state,
                "KEY_N": len(keys),
                "KEYSET_HASH": keyset_hash(keys),
                "KEYS": key_strings(keys),
            }
            summary[partition][state] = {
                "KEY_N": len(keys),
                "KEYSET_HASH": cells[cell_name]["KEYSET_HASH"],
            }
        if sum(summary[partition][state]["KEY_N"] for state in CANONICAL_STATES) != len(partitions[partition]):
            raise RefinementError(f"CANONICAL_MATRIX_PARTITION_DRIFT:{partition}")
    body = {
        "TASK": TASK,
        "MANIFEST_KIND": "STATUS0_PARTITION_CANONICAL_3X3_EXACT_KEY_MANIFEST",
        "PARTITION_KEY_N": {partition: len(partitions[partition]) for partition in PARTITIONS},
        "CANONICAL_STATES": list(CANONICAL_STATES),
        "CELLS": cells,
        "SERIALIZATION": "canonical JSON UTF-8; cell keys sorted by partition/state; KEYS are symbol<TAB>trade_date lines sorted by (symbol, trade_date)",
    }
    manifest = {**body, "MANIFEST_HASH": sha256_json(body)}
    return manifest, summary


def build_observation_clustering(rows: list[dict[str, Any]], trading_dates: list[date]) -> dict[str, Any]:
    keys = [key_of(row) for row in rows]
    if len(keys) != len(set(keys)):
        raise RefinementError("CLUSTERING_DUPLICATE_KEY")
    date_index = {value: index for index, value in enumerate(sorted(trading_dates))}
    by_symbol: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for symbol, trade_date in keys:
        if trade_date not in date_index:
            raise RefinementError(f"CLUSTERING_DATE_NOT_IN_FROZEN_CALENDAR:{symbol}:{trade_date}")
        by_symbol[symbol].append((trade_date, date_index[trade_date]))
    runs: list[dict[str, Any]] = []
    for symbol in sorted(by_symbol):
        ordered = sorted(by_symbol[symbol])
        current = [ordered[0]]
        for item in ordered[1:]:
            if item[1] == current[-1][1] + 1:
                current.append(item)
            else:
                runs.append({"symbol": symbol, "start_date": current[0][0].isoformat(), "end_date": current[-1][0].isoformat(), "length": len(current)})
                current = [item]
        runs.append({"symbol": symbol, "start_date": current[0][0].isoformat(), "end_date": current[-1][0].isoformat(), "length": len(current)})
    date_counts = Counter(trade_date.isoformat() for _, trade_date in keys)
    same_date = {value: count for value, count in sorted(date_counts.items()) if count > 1}
    runs.sort(key=lambda row: (-row["length"], row["symbol"], row["start_date"]))
    year_counts = Counter(trade_date.year for _, trade_date in keys)
    return {
        "OBSERVATION_ONLY": True,
        "UNIQUE_SYMBOL_N": len(by_symbol),
        "DATE_MIN": min((trade_date for _, trade_date in keys), default=None),
        "DATE_MAX": max((trade_date for _, trade_date in keys), default=None),
        "YEAR_COUNTS": {str(year): year_counts[year] for year in sorted(year_counts)},
        "RUN_COUNT_N": len(runs),
        "LONGEST_CONSECUTIVE_RUN_N": runs[0]["length"] if runs else 0,
        "LONGEST_RUNS": runs[:10],
        "SINGLE_DAY_RUN_N": sum(run["length"] == 1 for run in runs),
        "SINGLE_DAY_SYMBOL_N": sum(len(items) == 1 for items in by_symbol.values()),
        "SAME_DATE_CLUSTER_N": len(same_date),
        "MAX_SAME_DATE_CLUSTER_N": max(same_date.values(), default=0),
        "SAME_DATE_CLUSTER_COUNTS": same_date,
    }


def _scope_add(scope: dict[tuple[str, date], dict[str, Any]], row: dict[str, Any], reason: str, classification: str) -> None:
    key = key_of(row)
    entry = scope.setdefault(
        key,
        {
            "symbol": key[0],
            "trade_date": key[1].isoformat(),
            "classification": classification,
            "old_classification": STATUS0_CLASSIFICATION,
            "selection_reasons": [],
        },
    )
    if reason not in entry["selection_reasons"]:
        entry["selection_reasons"].append(reason)


def build_secondary_scope(
    partitions: dict[str, list[dict[str, Any]]],
    clustering: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    scope: dict[tuple[str, date], dict[str, Any]] = {}
    for row in partitions[CONTRADICTION]:
        _scope_add(scope, row, "ALL_8_CONTRADICTION_KEYS_REQUIRED", CONTRADICTION)

    indeterminate = partitions[INDETERMINATE]
    by_pattern: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in indeterminate:
        by_pattern[classify_numeric_pattern(row.get("volume_raw"), row.get("amount_raw"))].append(row)
    for pattern, rows in sorted(by_pattern.items()):
        for row in (_previous_rows(rows)[:1] + _previous_rows(rows)[-1:]):
            _scope_add(scope, row, f"NUMERIC_PATTERN_BOUNDARY:{pattern}", INDETERMINATE)

    for state in CANONICAL_STATES:
        # Select from the exact state labels attached after the 3x3 lookup,
        # rather than infer from aggregate counts.
        state_rows = [
            row
            for row in indeterminate
            if row.get("_canonical_state") == state
        ]
        for row in _previous_rows(state_rows)[:3]:
            _scope_add(scope, row, f"CANONICAL_STATE_REPRESENTATIVE:{state}", INDETERMINATE)

    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in indeterminate:
        by_year[parse_date(row["trade_date"]).year].append(row)
    for year, rows in sorted(by_year.items()):
        ordered = _previous_rows(rows)
        _scope_add(scope, ordered[0], f"YEAR_BOUNDARY_FIRST:{year}", INDETERMINATE)
        _scope_add(scope, ordered[-1], f"YEAR_BOUNDARY_LAST:{year}", INDETERMINATE)

    for run in clustering["LONGEST_RUNS"][:5]:
        if run["length"] < 2:
            continue
        for row in indeterminate:
            if row["symbol"] == run["symbol"] and row["trade_date"] in {run["start_date"], run["end_date"]}:
                _scope_add(scope, row, "LONGEST_RUN_ENDPOINT", INDETERMINATE)

    singleton_rows = [
        row for row in indeterminate
        if any(run["length"] == 1 and run["symbol"] == row["symbol"] and run["start_date"] == row["trade_date"] for run in clustering["LONGEST_RUNS"])
    ]
    for row in _previous_rows(singleton_rows)[:5]:
        _scope_add(scope, row, "SINGLE_DAY_OBSERVATION", INDETERMINATE)

    for cluster_date, _count in sorted(clustering["SAME_DATE_CLUSTER_COUNTS"].items(), key=lambda item: (-item[1], item[0]))[:3]:
        cluster_rows = [row for row in indeterminate if row["trade_date"] == cluster_date]
        for row in _previous_rows(cluster_rows)[:3]:
            _scope_add(scope, row, "SAME_DATE_CLUSTER_REPRESENTATIVE", INDETERMINATE)

    # Lifecycle-edge rows are selected only when the frozen lifecycle says
    # the invalid observation is at the first/last session boundary.  The
    # scope records observation, not a suspension/delisting interpretation.
    lifecycle_by_symbol = {str(row["symbol"]): row for row in context["lifecycle_rows"]}
    trading_dates = sorted(context["trading_dates"])
    date_index = {value: index for index, value in enumerate(trading_dates)}

    def first_session_on_or_after(value: date) -> int:
        index = bisect_left(trading_dates, value)
        return min(index, len(trading_dates) - 1)

    def last_session_on_or_before(value: date) -> int:
        index = bisect_right(trading_dates, value) - 1
        return max(index, 0)

    edge_candidates: list[dict[str, Any]] = []
    for row in indeterminate:
        lifecycle = lifecycle_by_symbol.get(row["symbol"])
        if lifecycle is None:
            continue
        start = parse_date(lifecycle["effective_start"])
        end = parse_date(lifecycle["effective_end"])
        current = parse_date(row["trade_date"])
        if current in date_index and (
            abs(date_index[current] - first_session_on_or_after(start)) <= 1
            or abs(date_index[current] - last_session_on_or_before(end)) <= 1
        ):
            edge_candidates.append(row)
    for row in _previous_rows(edge_candidates)[:8]:
        _scope_add(scope, row, "LIFECYCLE_BOUNDARY_OBSERVATION", INDETERMINATE)

    for row in _previous_rows(indeterminate)[:1] + _previous_rows(indeterminate)[-1:]:
        _scope_add(scope, row, "GLOBAL_DATE_BOUNDARY", INDETERMINATE)

    rows = sorted(scope.values(), key=lambda row: (row["symbol"], row["trade_date"], row["classification"]))
    for row in rows:
        row["selection_reasons"].sort()
    keys = [(row["symbol"], parse_date(row["trade_date"])) for row in rows]
    if not set((row["symbol"], row["trade_date"]) for row in rows).issubset(
        {(row["symbol"], row["trade_date"]) for row in partitions[CONTRADICTION] + partitions[INDETERMINATE]}
    ):
        raise RefinementError("SECONDARY_SCOPE_OUTSIDE_STATUS0_SCOPE")
    if not all(key_of(row) in set(keys) for row in partitions[CONTRADICTION]):
        raise RefinementError("SECONDARY_SCOPE_MISSING_CONTRADICTION")
    body = {
        "TASK": TASK,
        "MANIFEST_KIND": "BOUNDED_SECONDARY_AUTHORITY_SCOPE",
        "KEY_N": len(rows),
        "KEYSET_HASH": keyset_hash(keys),
        "ROWS": rows,
        "SECONDARY_AUTHORITY_PLAN": {
            "EXECUTED": False,
            "NETWORK_REQUEST_N": 0,
            "REQUIRED": "Independent validation of all 8 contradictions plus deterministic representatives/boundaries from the homogeneous invalid-numeric partition; no direct 15,004-request expansion is authorized.",
            "EXPANSION_RULE": "If any representative pattern/category disagrees, stop and expand only under a separately authorized bounded design; do not promote from this research report.",
            "OBSERVATION_NOT_INTERPRETATION": True,
        },
        "SERIALIZATION": "canonical JSON UTF-8, sorted keys, compact separators; rows sorted by (symbol, trade_date, classification)",
    }
    return {**body, "MANIFEST_HASH": sha256_json(body)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {TASK}",
        "",
        "Offline semantic refinement of the frozen 187,201-key BaoStock tradestatus=0 evidence.",
        "",
        "## Frozen input and receipt verification",
        "",
        f"- `BASE_HEAD={report['BASE_HEAD']}`; `INPUT_MANIFEST_HASH={report['INPUT_MANIFEST_HASH']}`.",
        f"- `SESSION_AUTHORITY_DATASET_HASH={report['SESSION_AUTHORITY_DATASET_HASH']}`; `REQUEST_RECEIPT_INDEX_HASH={report['REQUEST_RECEIPT_INDEX_HASH']}`.",
        f"- full receipt pairs=`{report['FULL_RECEIPT_PAIR_N']}`; full aggregate hash=`{report['FULL_RECEIPT_PAIR_HASH']}`.",
        f"- relevant receipt pairs=`{report['STATUS0_RELEVANT_RECEIPT_N']}`; relevant aggregate hash=`{report['STATUS0_RELEVANT_RECEIPT_HASH']}`.",
        "",
        "## Exact three-way partition",
        "",
        f"- `STATUS0_KEY_N={report['STATUS0_KEY_N']}`; zero-zero=`{report['STATUS0_ZERO_ZERO_N']}`; contradiction=`{report['STATUS0_CONTRADICTION_N']}`; numeric-indeterminate=`{report['STATUS0_NUMERIC_INDETERMINATE_N']}`.",
        f"- partition arithmetic=`{report['STATUS0_ZERO_ZERO_N']} + {report['STATUS0_CONTRADICTION_N']} + {report['STATUS0_NUMERIC_INDETERMINATE_N']} = {report['STATUS0_PARTITION_SUM_N']}`.",
        "",
        "## Invalid numeric and OHLC profiles",
        "",
        f"- both blank=`{report['INVALID_BOTH_BLANK_N']}`; volume-only blank=`{report['INVALID_VOLUME_ONLY_BLANK_N']}`; amount-only blank=`{report['INVALID_AMOUNT_ONLY_BLANK_N']}`; nonblank-invalid=`{report['INVALID_NONBLANK_N']}`.",
        f"- indeterminate OHLC all blank/populated=`{report['INDETERMINATE_OHLC_ALL_BLANK_N']}/{report['INDETERMINATE_OHLC_POPULATED_N']}`.",
        f"- indeterminate preclose blank/populated=`{report['INDETERMINATE_PRECLOSE_BLANK_N']}/{report['INDETERMINATE_PRECLOSE_POPULATED_N']}`.",
        "",
        "## Canonical 3x3 cross-check",
        "",
    ]
    for partition in PARTITIONS:
        row = report["CANONICAL_CROSS_MATRIX"][partition]
        lines.append(
            f"- `{partition}`: positive={row['CANONICAL_POSITIVE_VOLUME']['KEY_N']}, zero={row['CANONICAL_ZERO_VOLUME']['KEY_N']}, absent={row['CANONICAL_ABSENT']['KEY_N']}."
        )
    lines.extend(
        [
            "",
            "## Observation-only clustering",
            "",
            f"- contradiction: `{report['CLUSTERING'][CONTRADICTION]['UNIQUE_SYMBOL_N']}` symbols, dates `{report['CLUSTERING'][CONTRADICTION]['DATE_MIN']}`..`{report['CLUSTERING'][CONTRADICTION]['DATE_MAX']}`, longest frozen-session run=`{report['CLUSTERING'][CONTRADICTION]['LONGEST_CONSECUTIVE_RUN_N']}`.",
            f"- numeric-indeterminate: `{report['CLUSTERING'][INDETERMINATE]['UNIQUE_SYMBOL_N']}` symbols, dates `{report['CLUSTERING'][INDETERMINATE]['DATE_MIN']}`..`{report['CLUSTERING'][INDETERMINATE]['DATE_MAX']}`, longest frozen-session run=`{report['CLUSTERING'][INDETERMINATE]['LONGEST_CONSECUTIVE_RUN_N']}`.",
            "- These are observations only; no suspension, listing, or delisting interpretation is inferred.",
            "",
            "## Refined research contract",
            "",
            "- `STATUS0_ZERO_ZERO -> NOT_EXPECTED_SUPPORTED` provisionally; `STATUS0_CONTRADICTION -> UNKNOWN`; `STATUS0_NUMERIC_INDETERMINATE -> UNKNOWN`.",
            f"- provisional counts: expected=`{report['PROVISIONAL_EXPECTED_N']}`, not-expected=`{report['PROVISIONAL_NOT_EXPECTED_N']}`, unknown=`{report['PROVISIONAL_UNKNOWN_N']}`.",
            "- This is research-only reclassification evidence; session_authority.parquet is unchanged and no promotion/refreeze is performed.",
            "",
            "## Safety",
            "",
        ]
    )
    for key, value in report["SAFETY"].items():
        lines.append(f"- `{key}={str(value).lower() if isinstance(value, bool) else value}`")
    lines.extend(
        [
            "",
            f"- secondary scope=`{report['SECONDARY_AUTHORITY_REQUIRED_KEY_N']}` keys / `{report['SECONDARY_AUTHORITY_SCOPE_HASH']}`; network requests=`{report['SECONDARY_AUTHORITY_NETWORK_REQUEST_N']}`.",
            f"- `TEST_RESULT={report['TEST_RESULT']}`; `PY_COMPILE={report['PY_COMPILE']}`; `GIT_DIFF_CHECK={report['GIT_DIFF_CHECK']}`.",
            f"- exact cross-canonical manifests: `{CROSS_MATRIX_NAME}`; secondary scope: `{SECONDARY_SCOPE_NAME}`.",
            "",
        ]
    )
    return "\n".join(lines)


def run_refinement(
    *,
    repo_root: Path = REPO_ROOT,
    data_root: Path = DATA_ROOT_DEFAULT,
    stage_root: Path | None = None,
    test_result: str = "PENDING_TARGETED_TESTS",
    py_compile_result: str = "PENDING",
    diff_check_result: str = "PENDING",
) -> dict[str, Any]:
    if stage_root is None:
        stage_root = Path(data_root) / "staging" / STAGING_DIRNAME

    prior_artifacts = _verify_prior_frozen_artifacts(repo_root)
    verified = _load_and_verify_inputs(repo_root, data_root, stage_root)
    scanned = _scan_frozen_status0(verified)
    invalid_rows = _enrich_invalid_rows(verified, scanned["invalid_rows"])
    provider_rows = scanned["provider_rows"]
    partitions = build_status0_partitions(
        provider_rows,
        invalid_rows,
        expected_key_n=STATUS0_KEY_N,
    )

    if {
        partition: len(partitions[partition]) for partition in PARTITIONS
    } != {ZERO_ZERO: 172_189, CONTRADICTION: 8, INDETERMINATE: 15_004}:
        raise RefinementError("STATUS0_PARTITION_FROZEN_COUNT_DRIFT")
    contradiction_manifest, contradiction_summary = prior.build_contradiction_manifest(
        provider_rows,
        {},
    )
    # Canonical fields are added after the complete status0 target lookup.
    all_keys = {key_of(row) for rows in partitions.values() for row in rows}
    canonical_rows = prior.canonical_lookup(data_root, all_keys)
    contradiction_manifest, contradiction_summary = prior.build_contradiction_manifest(
        provider_rows,
        canonical_rows,
    )
    contradiction_rows = contradiction_summary["ROWS"]
    _verify_recomputed_prior_manifests(prior_artifacts, contradiction_rows, invalid_rows)

    pattern_profile = build_invalid_pattern_profile(invalid_rows)
    ohlc_profile = build_ohlc_preclose_profile(invalid_rows)
    cross_manifest, cross_summary = build_cross_canonical_matrix(partitions, canonical_rows)

    trading_dates = [parse_date(value) for value in verified["context"]["trading_dates"]]
    clustering = {
        CONTRADICTION: build_observation_clustering(partitions[CONTRADICTION], trading_dates),
        INDETERMINATE: build_observation_clustering(partitions[INDETERMINATE], trading_dates),
    }

    # Bind state labels to indeterminate rows for deterministic scope selection.
    for row in partitions[INDETERMINATE]:
        row["_canonical_state"] = canonical_state(canonical_rows, key_of(row))
    secondary_scope = build_secondary_scope(
        partitions,
        clustering[INDETERMINATE],
        verified["context"],
    )

    try:
        post_input = verified["context"]["plan"].build_input_file_manifest(Path(data_root))
    except Exception as exc:
        raise RefinementError("INPUT_POST_RECOMPUTE_FAILED") from exc
    if not prior.reconciliation._input_equal(verified["input_manifest"], post_input):
        raise RefinementError("INPUT_DRIFT_DURING_REFINEMENT")

    zero_zero_n = len(partitions[ZERO_ZERO])
    contradiction_n = len(partitions[CONTRADICTION])
    indeterminate_n = len(partitions[INDETERMINATE])
    provisional_unknown = UNKNOWN_KEY_N + contradiction_n + indeterminate_n
    report: dict[str, Any] = {
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_INDEPENDENT_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "REPORT_COMMIT_NOT_CLAIMED": True,
        "INPUT_FILE_N": verified["input_manifest"]["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": verified["input_manifest"]["INPUT_MANIFEST_HASH"],
        "INPUT_MANIFEST_FILES_EQUAL_PRE_POST": True,
        "SESSION_AUTHORITY_KEY_N": verified["session"]["SESSION_AUTHORITY_KEY_N"],
        "SESSION_AUTHORITY_DATASET_HASH": verified["session"]["SESSION_AUTHORITY_DATASET_HASH"],
        "SESSION_AUTHORITY_FILE_SHA256": verified["session"]["SESSION_AUTHORITY_FILE_SHA256"],
        "REQUEST_RECEIPT_INDEX_HASH": REQUEST_RECEIPT_INDEX_HASH,
        "REQUEST_RECEIPT_INDEX_FILE_SHA256": REQUEST_RECEIPT_INDEX_FILE_SHA256,
        "FULL_REQUEST_N": FULL_REQUEST_N,
        "FULL_RECEIPT_PAIR_N": scanned["receipt_scan"]["FULL_RECEIPT_PAIR_N"],
        "FULL_RECEIPT_PAIR_HASH": scanned["receipt_scan"]["FULL_RECEIPT_PAIR_HASH"],
        "STATUS0_RELEVANT_RECEIPT_N": scanned["receipt_scan"]["STATUS0_RELEVANT_RECEIPT_N"],
        "STATUS0_RELEVANT_RECEIPT_HASH": scanned["receipt_scan"]["STATUS0_RELEVANT_RECEIPT_HASH"],
        "STATUS0_KEY_N": STATUS0_KEY_N,
        "STATUS0_ZERO_ZERO_N": zero_zero_n,
        "STATUS0_CONTRADICTION_N": contradiction_n,
        "STATUS0_NUMERIC_INDETERMINATE_N": indeterminate_n,
        "STATUS0_PARTITION_SUM_N": zero_zero_n + contradiction_n + indeterminate_n,
        "STATUS0_ZERO_ZERO_KEYSET_HASH": keyset_hash([key_of(row) for row in partitions[ZERO_ZERO]]),
        "STATUS0_CONTRADICTION_KEY_N": contradiction_n,
        "STATUS0_CONTRADICTION_KEYSET_HASH": keyset_hash([key_of(row) for row in partitions[CONTRADICTION]]),
        "STATUS0_INVALID_NUMERIC_N": indeterminate_n,
        "STATUS0_INVALID_NUMERIC_KEYSET_HASH": keyset_hash([key_of(row) for row in partitions[INDETERMINATE]]),
        "INVALID_PATTERN_PROFILE": pattern_profile,
        "INVALID_BOTH_BLANK_N": pattern_profile["INVALID_BOTH_BLANK_N"],
        "INVALID_VOLUME_ONLY_BLANK_N": pattern_profile["INVALID_VOLUME_ONLY_BLANK_N"],
        "INVALID_AMOUNT_ONLY_BLANK_N": pattern_profile["INVALID_AMOUNT_ONLY_BLANK_N"],
        "INVALID_NONBLANK_N": pattern_profile["INVALID_NONBLANK_N"],
        "INDETERMINATE_OHLC_ALL_BLANK_N": ohlc_profile["OHLC_ALL_BLANK"]["KEY_N"],
        "INDETERMINATE_OHLC_POPULATED_N": ohlc_profile["OHLC_POPULATED"]["KEY_N"],
        "INDETERMINATE_PRECLOSE_BLANK_N": ohlc_profile["PRECLOSE_BLANK"]["KEY_N"],
        "INDETERMINATE_PRECLOSE_POPULATED_N": ohlc_profile["PRECLOSE_POPULATED"]["KEY_N"],
        "INDETERMINATE_OHLC_PRECLOSE_MATRIX": ohlc_profile["MATRIX"],
        "CANONICAL_CROSS_MATRIX": cross_summary,
        "CROSS_CANONICAL_MANIFEST": {
            "name": CROSS_MATRIX_NAME,
            "manifest_hash": cross_manifest["MANIFEST_HASH"],
            "file_hash": sha256_json_file_payload(cross_manifest),
        },
        "CLUSTERING": clustering,
        "REFINED_TRADESTATUS0_CONTRACT": {
            "CHOICE": "A",
            "STATUS0_ZERO_ZERO": "NOT_EXPECTED_SUPPORTED",
            "STATUS0_CONTRADICTION": "UNKNOWN",
            "STATUS0_NUMERIC_INDETERMINATE": "UNKNOWN",
            "STATUS": "PROVISIONAL_RESEARCH_ONLY",
            "PROMOTION": False,
        },
        "ROW_ELIGIBILITY_CONTRACT_EVIDENCE": [
            {
                "path": "docs/contracts/R3_DAILY_FOUNDATION_CONTRACT.md",
                "lines": "181-182",
                "commit": "6eb39447f729aa37d747f16f30e996795112f5a9",
                "evidence": "Coverage requires a positive-volume in-window row; zero-volume placeholders are not coverage evidence.",
            },
            {
                "path": "docs/plans/R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN.md",
                "lines": "760-767",
                "commit": "d13e2ecefbb66250b73aca4312dc8706a4d2b7a3",
                "evidence": "R3 does not infer suspension from volume == 0; zero-only coverage remains unexplained until a status datasource proves it.",
            },
            {
                "path": "tools/verify_r3_daily_foundation.py",
                "lines": "124-169",
                "commit": "6eb39447f729aa37d747f16f30e996795112f5a9",
                "evidence": "Canonical structural QA rejects negative/null volume and negative amount, while zero values are structurally accepted but not coverage evidence.",
            },
        ],
        "PROVISIONAL_EXPECTED_N": EXPECTED_KEY_N,
        "PROVISIONAL_NOT_EXPECTED_N": zero_zero_n,
        "PROVISIONAL_UNKNOWN_N": provisional_unknown,
        "RESEARCH_RECLASSIFICATION_ONLY": True,
        "NOT_SESSION_AUTHORITY_PROMOTION": True,
        "SECONDARY_AUTHORITY_REQUIRED_KEY_N": secondary_scope["KEY_N"],
        "SECONDARY_AUTHORITY_SCOPE_HASH": secondary_scope["KEYSET_HASH"],
        "SECONDARY_AUTHORITY_SCOPE_MANIFEST": {
            "name": SECONDARY_SCOPE_NAME,
            "manifest_hash": secondary_scope["MANIFEST_HASH"],
            "file_hash": sha256_json_file_payload(secondary_scope),
        },
        "SECONDARY_AUTHORITY_PLAN": secondary_scope["SECONDARY_AUTHORITY_PLAN"],
        "SECONDARY_AUTHORITY_NETWORK_REQUEST_N": 0,
        "R3_REFREEZE_RECOMMENDATION": "BLOCKED",
        "R3_COMPLETENESS_COUNTS_FROZEN": False,
        "SAFETY": {
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "BAOSTOCK_EXECUTED": False,
            "TDX_EXECUTED": False,
            "CANONICAL_WRITE_EXECUTED": False,
            "CANONICAL_BYTES_MUTATED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRODUCTION": False,
            "FORWARD": False,
            "TRADEPLAN": False,
        },
        "TEST_RESULT": test_result,
        "PY_COMPILE": py_compile_result,
        "GIT_DIFF_CHECK": diff_check_result,
        "PRIOR_FROZEN_ARTIFACTS": {
            "REPORT_FILE_SHA256": prior_artifacts["REPORT_FILE_SHA256"],
            "CONTRADICTION_FILE_SHA256": prior_artifacts["CONTRADICTION_FILE_SHA256"],
            "INVALID_FILE_SHA256": prior_artifacts["INVALID_FILE_SHA256"],
        },
    }

    # All writes below are repository-only and occur after every data-root
    # authority, receipt, partition, matrix, and input immutability gate.
    write_repo_json(repo_root, CROSS_MATRIX_NAME, cross_manifest)
    write_repo_json(repo_root, SECONDARY_SCOPE_NAME, secondary_scope)
    write_repo_json(repo_root, REPORT_NAME, _json_safe(report))
    write_repo_text(repo_root, REPORT_MD_NAME, _markdown(_json_safe(report)))
    return _json_safe(report)


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
        report = run_refinement(
            repo_root=args.repo_root,
            data_root=args.data_root,
            stage_root=args.stage_root,
            test_result=args.test_result,
            py_compile_result=args.py_compile_result,
            diff_check_result=args.diff_check_result,
        )
    except (RefinementError, prior.AuditError, prior.reconciliation.ReconciliationError) as exc:
        print(
            json.dumps(
                {
                    "TASK": TASK,
                    "AUTHOR_STATUS": "BLOCKED_FAIL_CLOSED",
                    "ERROR": str(exc),
                    "NETWORK_PROVIDER_DATA_FETCH": "NO",
                    "BAOSTOCK_EXECUTED": False,
                    "TDX_EXECUTED": False,
                    "CANONICAL_WRITE_EXECUTED": False,
                    "CANONICAL_BYTES_MUTATED": False,
                    "R4A9_RESUME_AUTHORIZED": False,
                    "R3_REFREEZE_RECOMMENDATION": "BLOCKED",
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
