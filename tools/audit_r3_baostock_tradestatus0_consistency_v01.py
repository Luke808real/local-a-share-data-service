#!/usr/bin/env python3
"""Audit all frozen R3 BaoStock ``tradestatus=0`` provider rows.

This is an offline, read-only provenance audit.  It verifies the frozen
session authority and every full-run raw/normalized receipt pair, then checks
the provider OHLCV values attached to all NOT_EXPECTED_BAR keys.  It writes
only compact reports below the repository's reports directory and never calls
a provider or writes the data root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_r3_full_session_completeness_reconciliation_v01 as reconciliation  # noqa: E402


TASK = "R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01"
BRANCH = "codex/r3-baostock-tradestatus0-consistency-audit-v01"
BASE_HEAD = "568d0626e501391ed2317425547828f8b6caa56f"
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

STAGING_DIRNAME = "r3_full_session_completeness_authority_v01"
SESSION_AUTHORITY_NAME = "session_authority.parquet"
FULL_REQUEST_RECEIPT_INDEX_NAME = "full_request_receipt_index.json"
FULL_RAW_DIRNAME = "full_raw_receipts"
FULL_NORMALIZED_DIRNAME = "full_normalized_receipts"

DAILY_INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
SESSION_AUTHORITY_DATASET_HASH = "0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47"
REQUEST_RECEIPT_INDEX_HASH = "0884112d77b75469bc0e4145dfde8ad749b4ae16b7d6974e365029bca799ab8b"
STATUS0_KEY_N = 187_201
FULL_REQUEST_N = 48_345
LIFECYCLE_SESSION_KEYSET_HASH = "eacb645dc8ac6273458fe3a59ea46b6be5072a234f0acf3fabbe2dd7463f2a65"

STATUS0_CLASSIFICATION = "NOT_EXPECTED_BAR"
STATUS0_BASIS = "PROVIDER_TRADESTATUS_0"
STATUS0_UNKNOWN_BASIS = "PROVIDER_ROW_ABSENT_IN_LIFETIME"
PROVIDER = "baostock"
PROVIDER_RUNTIME = "baostock-0.9.3"

CONTRADICTION_MANIFEST_NAME = f"{TASK}_CONTRADICTIONS.json"
INVALID_NUMERIC_MANIFEST_NAME = f"{TASK}_INVALID_NUMERIC.json"
RECEIPT_HASH_INDEX_NAME = f"{TASK}_RECEIPT_HASH_INDEX.json"
REPORT_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"

CONTRADICTION_ANCHOR = ("688065.SH", date(2023, 6, 15))
ZERO_VOLUME_CONTROL = ("600651.SH", date(2016, 8, 25))
EMPTY_KEYSET_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class AuditError(RuntimeError):
    """Terminal fail-closed audit error."""


def canonical_json_bytes(value: Any) -> bytes:
    def default(item: Any) -> str:
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        if isinstance(item, Decimal):
            return format(item, "f")
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


def sha256_json_file_payload(value: Any) -> str:
    """Hash the exact bytes emitted by :func:`write_repo_json`."""

    return sha256_bytes(canonical_json_bytes(value) + b"\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AuditError(f"LOCAL_FILE_READ_FAILED:{path}") from exc
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"LOCAL_JSON_READ_FAILED:{path}") from exc


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise AuditError(f"INVALID_DATE:{value!r}") from exc


def key_line(key: tuple[str, date]) -> bytes:
    return f"{key[0]}\t{key[1].isoformat()}\n".encode("utf-8")


def keyset_hash(keys: Iterable[tuple[str, date]]) -> str:
    return reconciliation.keyset_hash(keys)


def status0_value_metrics(volume: Any, amount: Any) -> dict[str, Any]:
    """Normalize non-negative provider values and expose contradiction flags."""

    try:
        volume_decimal = Decimal(str(volume))
        amount_decimal = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AuditError("STATUS0_INVALID_NUMERIC_FIELD") from exc
    if (
        not volume_decimal.is_finite()
        or not amount_decimal.is_finite()
        or volume_decimal < 0
        or amount_decimal < 0
        or volume_decimal != volume_decimal.to_integral_value()
    ):
        raise AuditError("STATUS0_INVALID_NUMERIC_FIELD")
    volume_n = int(volume_decimal)
    return {
        "volume_n": volume_n,
        "amount_decimal": amount_decimal,
        "volume_zero": volume_n == 0,
        "volume_positive": volume_n > 0,
        "amount_zero": amount_decimal == 0,
        "amount_positive": amount_decimal > 0,
        "contradiction": volume_n > 0 or amount_decimal > 0,
    }


def verify_receipt_payload_hashes(
    raw_receipt: dict[str, Any],
    normalized_receipt: dict[str, Any],
    index_row: dict[str, Any],
) -> tuple[str, str]:
    """Recompute payload hashes and bind both receipts to the receipt index."""

    raw_payload = raw_receipt.get("raw_payload")
    normalized_payload = normalized_receipt.get("normalized_payload")
    if not isinstance(raw_payload, dict) or not isinstance(normalized_payload, dict):
        raise AuditError("RECEIPT_PAYLOAD_MISSING")
    raw_hash = sha256_json(raw_payload)
    normalized_hash = sha256_json(normalized_payload)
    expected_raw = {raw_receipt.get("raw_sha256"), index_row.get("raw_sha256")}
    expected_normalized = {
        normalized_receipt.get("normalized_sha256"),
        index_row.get("normalized_sha256"),
    }
    if raw_hash not in expected_raw or len(expected_raw) != 1:
        raise AuditError("RAW_RECEIPT_PAYLOAD_HASH_DRIFT")
    if normalized_hash not in expected_normalized or len(expected_normalized) != 1:
        raise AuditError("NORMALIZED_RECEIPT_PAYLOAD_HASH_DRIFT")
    if raw_receipt.get("normalized_sha256") != normalized_hash:
        raise AuditError("RAW_TO_NORMALIZED_HASH_BINDING_DRIFT")
    return raw_hash, normalized_hash


def _repo_output_path(repo_root: Path, name: str) -> Path:
    root = Path(repo_root).resolve()
    path = (root / "reports" / "implementation" / name).resolve()
    if root not in path.parents:
        raise AuditError("REPO_OUTPUT_PATH_ESCAPE")
    return path


def write_repo_json(repo_root: Path, name: str, payload: Any) -> str:
    path = _repo_output_path(repo_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(payload) + b"\n"
    path.write_bytes(data)
    return sha256_bytes(data)


def write_repo_text(repo_root: Path, name: str, value: str) -> str:
    path = _repo_output_path(repo_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def _stage_file(stage: Path, relative: str) -> Path:
    path = (stage / relative).resolve()
    if stage not in path.parents:
        raise AuditError(f"STAGE_PATH_ESCAPE:{relative}")
    return path


def collect_status0_keys(
    session_path: Path,
) -> tuple[dict[str, dict[date, dict[str, Any]]], dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise AuditError("PYARROW_REQUIRED_FOR_STATUS0_AUDIT") from exc
    parquet = pq.ParquetFile(session_path)
    by_request: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    class_counts: Counter[str] = Counter()
    for batch in parquet.iter_batches(
        batch_size=100_000,
        columns=["symbol", "trade_date", "classification", "basis", "tradestatus", "request_id", "provider_code"],
    ):
        for row in batch.to_pylist():
            classification = str(row.get("classification"))
            class_counts[classification] += 1
            if classification != STATUS0_CLASSIFICATION:
                continue
            if row.get("basis") != STATUS0_BASIS or row.get("tradestatus") != 0:
                raise AuditError("SESSION_AUTHORITY_STATUS0_BINDING_DRIFT")
            request_id = str(row.get("request_id"))
            trade_date = parse_date(row.get("trade_date"))
            if trade_date in by_request[request_id]:
                raise AuditError("STATUS0_SESSION_DUPLICATE_KEY")
            by_request[request_id][trade_date] = {
                "symbol": str(row.get("symbol")),
                "trade_date": trade_date,
                "basis": str(row.get("basis")),
                "tradestatus": 0,
                "request_id": request_id,
                "provider_code": str(row.get("provider_code")),
            }
    status0_n = sum(len(rows) for rows in by_request.values())
    if status0_n != STATUS0_KEY_N:
        raise AuditError(f"STATUS0_KEY_N_MISMATCH:{status0_n}")
    return by_request, {
        "STATUS0_KEY_N": status0_n,
        "EXPECTED_KEY_N": class_counts["EXPECTED_BAR"],
        "NOT_EXPECTED_KEY_N": class_counts[STATUS0_CLASSIFICATION],
        "UNKNOWN_KEY_N": class_counts["UNKNOWN"],
    }


def _parse_raw_target_row(
    raw_row: list[Any],
    authority: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    if len(raw_row) < 10:
        raise AuditError("STATUS0_PROVIDER_ROW_TOO_SHORT")
    raw_date = parse_date(raw_row[0])
    if raw_date != authority["trade_date"]:
        raise AuditError("STATUS0_PROVIDER_DATE_MISMATCH")
    if str(raw_row[1]) != str(request["bs_code"]):
        raise AuditError("STATUS0_PROVIDER_CODE_MISMATCH")
    try:
        tradestatus = int(str(raw_row[9]))
    except (TypeError, ValueError) as exc:
        raise AuditError("STATUS0_PROVIDER_TRADESTATUS_INVALID") from exc
    if tradestatus != 0:
        raise AuditError("STATUS0_PROVIDER_TRADESTATUS_NOT_ZERO")
    try:
        metrics = status0_value_metrics(raw_row[6], raw_row[7])
    except AuditError as exc:
        raise AuditError(
            f"STATUS0_INVALID_NUMERIC_FIELD:{authority['symbol']}:{raw_date}:"
            f"volume={raw_row[6]!r}:amount={raw_row[7]!r}"
        ) from exc
    if not str(request["start_date"]) <= raw_date.isoformat() <= str(request["end_date"]):
        raise AuditError("STATUS0_PROVIDER_DATE_OUTSIDE_REQUEST")
    return {
        "symbol": authority["symbol"],
        "trade_date": raw_date.isoformat(),
        "request_id": authority["request_id"],
        "request_order": int(request["request_order"]),
        "provider_code": str(raw_row[1]),
        "open": str(raw_row[2]),
        "high": str(raw_row[3]),
        "low": str(raw_row[4]),
        "close": str(raw_row[5]),
        "volume": int(metrics["volume_n"]),
        "amount": str(raw_row[7]),
        "preclose": str(raw_row[8]),
        "tradestatus": tradestatus,
        "_amount_decimal": metrics["amount_decimal"],
        "_contradiction": bool(metrics["contradiction"]),
    }


def scan_receipt_pairs(
    stage: Path,
    context: dict[str, Any],
    receipt_index: dict[str, Any],
    status0_by_request: dict[str, dict[date, dict[str, Any]]],
) -> dict[str, Any]:
    request_map = {str(row["request_id"]): row for row in context["requests"]}
    index_rows = receipt_index.get("REQUESTS")
    if not isinstance(index_rows, list) or len(index_rows) != FULL_REQUEST_N:
        raise AuditError("RECEIPT_INDEX_SCOPE_MISMATCH")
    index_by_id = {str(row.get("request_id")): row for row in index_rows}
    if len(index_by_id) != FULL_REQUEST_N or set(index_by_id) != set(request_map):
        raise AuditError("RECEIPT_INDEX_REQUEST_SET_MISMATCH")

    all_digest = hashlib.sha256()
    status0_digest = hashlib.sha256()
    status0_receipt_ids = set(status0_by_request)
    status0_provider_rows: list[dict[str, Any]] = []
    status0_invalid_rows: list[dict[str, Any]] = []
    raw_duplicate_n = 0
    normalized_target_mismatch_n = 0
    status0_receipt_hash_rows: list[dict[str, Any]] = []

    ordered_index = sorted(index_rows, key=lambda row: int(row["request_order"]))
    for position, index_row in enumerate(ordered_index, start=1):
        request_id = str(index_row.get("request_id"))
        request = request_map.get(request_id)
        if request is None:
            raise AuditError("RECEIPT_INDEX_REQUEST_NOT_IN_MANIFEST")
        if int(index_row.get("request_order")) != int(request["request_order"]):
            raise AuditError("RECEIPT_INDEX_ORDER_MISMATCH")
        raw_path = _stage_file(stage, f"{FULL_RAW_DIRNAME}/{request_id}.json")
        normalized_path = _stage_file(stage, f"{FULL_NORMALIZED_DIRNAME}/{request_id}.json")
        if not raw_path.is_file() or not normalized_path.is_file():
            raise AuditError(f"RECEIPT_PAIR_MISSING:{request_id}")
        try:
            raw_bytes = raw_path.read_bytes()
            normalized_bytes = normalized_path.read_bytes()
            raw_receipt = json.loads(raw_bytes)
            normalized_receipt = json.loads(normalized_bytes)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise AuditError(f"RECEIPT_PAIR_UNREADABLE:{request_id}") from exc
        if not isinstance(raw_receipt, dict) or not isinstance(normalized_receipt, dict):
            raise AuditError(f"RECEIPT_PAIR_NOT_OBJECT:{request_id}")
        if raw_receipt.get("request") != request or normalized_receipt.get("request") != request:
            raise AuditError(f"RECEIPT_REQUEST_DRIFT:{request_id}")
        for receipt in (raw_receipt, normalized_receipt):
            if (
                receipt.get("request_id") != request_id
                or int(receipt.get("request_order")) != int(request["request_order"])
                or receipt.get("request_manifest_hash") != request.get("request_manifest_hash", receipt_index.get("FULL_REQUEST_MANIFEST_HASH"))
                or receipt.get("lifecycle_session_keyset_hash") != LIFECYCLE_SESSION_KEYSET_HASH
                or receipt.get("status") != "COMPLETE"
                or str(receipt.get("provider_error_code")) != "0"
            ):
                raise AuditError(f"RECEIPT_METADATA_DRIFT:{request_id}")
        raw_payload_hash, normalized_payload_hash = verify_receipt_payload_hashes(
            raw_receipt, normalized_receipt, index_row
        )
        raw_rows = (raw_receipt.get("raw_payload") or {}).get("rows")
        normalized_payload = normalized_receipt.get("normalized_payload") or {}
        normalized_rows = normalized_payload.get("NORMALIZED_ROWS")
        normalized_cases = normalized_payload.get("CASES")
        if (
            not isinstance(raw_rows, list)
            or not isinstance(normalized_rows, list)
            or not isinstance(normalized_cases, list)
        ):
            raise AuditError(f"RECEIPT_ROWS_MISSING:{request_id}")
        if (
            int(raw_receipt.get("raw_row_n")) != len(raw_rows)
            # The frozen receipt contract defines normalized_row_n as the
            # normalized provider-row count.  CASES additionally contains
            # lifecycle UNKNOWN entries, so it may be larger.
            or int(normalized_receipt.get("normalized_row_n")) != len(normalized_rows)
            or int(index_row.get("raw_row_n")) != len(raw_rows)
            or int(index_row.get("normalized_row_n")) != len(normalized_rows)
        ):
            raise AuditError(f"RECEIPT_ROW_COUNT_DRIFT:{request_id}")

        raw_file_hash = sha256_bytes(raw_bytes)
        normalized_file_hash = sha256_bytes(normalized_bytes)
        hash_line = (
            f"{int(request['request_order'])}\t{request_id}\t{raw_file_hash}\t"
            f"{normalized_file_hash}\t{raw_payload_hash}\t{normalized_payload_hash}\n"
        ).encode("utf-8")
        all_digest.update(hash_line)
        if request_id in status0_receipt_ids:
            status0_digest.update(hash_line)
            raw_date_rows: dict[date, list[list[Any]]] = defaultdict(list)
            for raw_row in raw_rows:
                if isinstance(raw_row, list) and raw_row:
                    try:
                        raw_date_rows[parse_date(raw_row[0])].append(raw_row)
                    except AuditError:
                        raise AuditError(f"RECEIPT_RAW_DATE_INVALID:{request_id}")
            raw_duplicate_n += sum(1 for rows in raw_date_rows.values() if len(rows) > 1)
            case_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
            for case in normalized_cases:
                if isinstance(case, dict) and case.get("trade_date") is not None:
                    case_by_date[parse_date(case["trade_date"])].append(case)
            for target_date, authority in sorted(status0_by_request[request_id].items()):
                target_raw = raw_date_rows.get(target_date, [])
                if len(target_raw) != 1:
                    raise AuditError(
                        f"STATUS0_PROVIDER_ROW_N:{request_id}:{target_date}:{len(target_raw)}"
                    )
                try:
                    parsed_target = _parse_raw_target_row(
                        target_raw[0], authority, request
                    )
                except AuditError as exc:
                    # Blank/non-numeric OHLCV fields are provider evidence
                    # defects, not zeroes.  Preserve their exact raw values
                    # and continue the offline scan so the final report can
                    # fail closed with a complete anomaly inventory.
                    if not str(exc).startswith("STATUS0_INVALID_NUMERIC_FIELD:"):
                        raise
                    raw_target = target_raw[0]
                    status0_invalid_rows.append(
                        {
                            "symbol": authority["symbol"],
                            "trade_date": target_date.isoformat(),
                            "request_id": authority["request_id"],
                            "request_order": int(request["request_order"]),
                            "provider_code": str(raw_target[1]),
                            "volume_raw": raw_target[6],
                            "amount_raw": raw_target[7],
                            "reason_code": "NON_NUMERIC_VOLUME_OR_AMOUNT",
                        }
                    )
                else:
                    status0_provider_rows.append(parsed_target)
                target_cases = case_by_date.get(target_date, [])
                if (
                    len(target_cases) != 1
                    or target_cases[0].get("symbol") != authority["symbol"]
                    or target_cases[0].get("classification") != STATUS0_CLASSIFICATION
                    or target_cases[0].get("basis") != STATUS0_BASIS
                    or target_cases[0].get("provider_tradestatus") != 0
                ):
                    normalized_target_mismatch_n += 1
                    raise AuditError(f"STATUS0_NORMALIZED_CASE_DRIFT:{request_id}:{target_date}")
        if position % 5_000 == 0:
            print(f"verified receipt pairs: {position}/{FULL_REQUEST_N}", file=sys.stderr)

        if request_id in status0_receipt_ids:
            status0_receipt_hash_rows.append(
                {
                    "request_id": request_id,
                    "request_order": int(request["request_order"]),
                    "raw_file_sha256": raw_file_hash,
                    "normalized_file_sha256": normalized_file_hash,
                    "raw_payload_sha256": raw_payload_hash,
                    "normalized_payload_sha256": normalized_payload_hash,
                }
            )

    status0_row_present_n = len(status0_provider_rows) + len(status0_invalid_rows)
    if status0_row_present_n != STATUS0_KEY_N:
        raise AuditError(f"STATUS0_PROVIDER_ROW_PRESENT_N:{status0_row_present_n}")
    if raw_duplicate_n or normalized_target_mismatch_n:
        raise AuditError("STATUS0_RECEIPT_DUPLICATE_OR_NORMALIZED_DRIFT")
    status0_receipt_hash_rows.sort(key=lambda row: int(row["request_order"]))
    return {
        "STATUS0_PROVIDER_ROWS": status0_provider_rows,
        "STATUS0_INVALID_ROWS": status0_invalid_rows,
        "STATUS0_ROW_PRESENT_N": status0_row_present_n,
        "STATUS0_RELEVANT_RECEIPT_N": len(status0_receipt_ids),
        "FULL_RECEIPT_PAIR_N": FULL_REQUEST_N,
        "FULL_RECEIPT_PAIR_HASH": all_digest.hexdigest(),
        "STATUS0_RELEVANT_RECEIPT_HASH": status0_digest.hexdigest(),
        "STATUS0_RECEIPT_HASH_ROWS": status0_receipt_hash_rows,
        "RECEIPT_HASH_SERIALIZATION": "UTF-8 lines: request_order<TAB>request_id<TAB>raw_file_sha256<TAB>normalized_file_sha256<TAB>raw_payload_sha256<TAB>normalized_payload_sha256<LF>, sorted by request_order",
    }


def canonical_lookup(
    data_root: Path,
    target_keys: set[tuple[str, date]],
) -> dict[tuple[str, date], dict[str, Any]]:
    if not target_keys:
        return {}
    try:
        import duckdb
        import pyarrow as pa
    except Exception as exc:
        raise AuditError("DUCKDB_PYARROW_REQUIRED_FOR_CANONICAL_LOOKUP") from exc
    symbols = [key[0] for key in sorted(target_keys)]
    dates = [key[1] for key in sorted(target_keys)]
    target_table = pa.table(
        {
            "symbol": pa.array(symbols, type=pa.string()),
            "trade_date": pa.array(dates, type=pa.date32()),
        }
    )
    glob = (
        Path(data_root).resolve() / "curated" / "daily_bars" / "**" / "*.parquet"
    ).as_posix().replace("'", "''")
    connection = duckdb.connect(":memory:")
    try:
        connection.register("target_keys", target_table)
        rows = connection.execute(
            "SELECT CAST(c.symbol AS VARCHAR), CAST(c.trade_date AS DATE), c.open, c.high, "
            "c.low, c.close, c.volume, c.amount, CAST(c.source AS VARCHAR), "
            f"CAST(c.data_version AS VARCHAR), CAST(c.fetched_at AS VARCHAR) FROM read_parquet('{glob}') c "
            "JOIN target_keys t ON CAST(c.symbol AS VARCHAR)=t.symbol "
            "AND CAST(c.trade_date AS DATE)=t.trade_date "
            "ORDER BY c.symbol, c.trade_date"
        ).fetchall()
    except Exception as exc:
        raise AuditError("CANONICAL_LOOKUP_FAILED") from exc
    finally:
        connection.close()
    result: dict[tuple[str, date], dict[str, Any]] = {}
    for row in rows:
        key = (str(row[0]), parse_date(row[1]))
        if key in result:
            raise AuditError(f"CANONICAL_TARGET_DUPLICATE:{key}")
        result[key] = {
            "symbol": key[0],
            "trade_date": key[1].isoformat(),
            "open": None if row[2] is None else float(row[2]),
            "high": None if row[3] is None else float(row[3]),
            "low": None if row[4] is None else float(row[4]),
            "close": None if row[5] is None else float(row[5]),
            "volume": None if row[6] is None else int(row[6]),
            "amount": None if row[7] is None else float(row[7]),
            "source": None if row[8] is None else str(row[8]),
            "data_version": None if row[9] is None else str(row[9]),
            "fetched_at": None if row[10] is None else str(row[10]),
        }
    return result


def build_contradiction_manifest(
    provider_rows: list[dict[str, Any]],
    canonical_rows: dict[tuple[str, date], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    contradiction_rows: list[dict[str, Any]] = []
    for provider in provider_rows:
        key = (provider["symbol"], parse_date(provider["trade_date"]))
        if not provider["_contradiction"]:
            continue
        canonical = canonical_rows.get(key)
        contradiction_rows.append(
            {
                "symbol": provider["symbol"],
                "trade_date": provider["trade_date"],
                "request_id": provider["request_id"],
                "volume": provider["volume"],
                "amount": provider["amount"],
                "open": provider["open"],
                "high": provider["high"],
                "low": provider["low"],
                "close": provider["close"],
                "preclose": provider["preclose"],
                "canonical_present": canonical is not None,
                "canonical_volume": None if canonical is None else canonical["volume"],
                "canonical_amount": None if canonical is None else canonical["amount"],
                "canonical_source": None if canonical is None else canonical["source"],
            }
        )
    contradiction_rows.sort(key=lambda row: (row["symbol"], row["trade_date"]))
    keys = [(row["symbol"], parse_date(row["trade_date"])) for row in contradiction_rows]
    if len(set(keys)) != len(keys):
        raise AuditError("STATUS0_CONTRADICTION_DUPLICATE_KEY")
    body = {
        "TASK": TASK,
        "MANIFEST_KIND": "STATUS0_CONTRADICTION",
        "KEY_N": len(contradiction_rows),
        "KEYSET_HASH": keyset_hash(keys) if keys else EMPTY_KEYSET_HASH,
        "ROWS": contradiction_rows,
        "SERIALIZATION": "canonical JSON UTF-8, sorted keys, compact separators; rows sorted by (symbol, trade_date)",
    }
    return {**body, "MANIFEST_HASH": sha256_json(body)}, {
        "KEY_N": len(contradiction_rows),
        "KEYSET_HASH": body["KEYSET_HASH"],
        "ROWS": contradiction_rows,
    }


def build_invalid_numeric_manifest(
    invalid_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = sorted(
        invalid_rows,
        key=lambda row: (row["symbol"], row["trade_date"], row["request_id"]),
    )
    keys = [(row["symbol"], parse_date(row["trade_date"])) for row in rows]
    if len(set(keys)) != len(keys):
        raise AuditError("STATUS0_INVALID_NUMERIC_DUPLICATE_KEY")
    body = {
        "TASK": TASK,
        "MANIFEST_KIND": "STATUS0_INVALID_NUMERIC",
        "KEY_N": len(rows),
        "KEYSET_HASH": keyset_hash(keys) if keys else EMPTY_KEYSET_HASH,
        "ROWS": rows,
        "SERIALIZATION": "canonical JSON UTF-8, sorted keys, compact separators; rows sorted by (symbol, trade_date, request_id)",
    }
    return {**body, "MANIFEST_HASH": sha256_json(body)}, {
        "KEY_N": len(rows),
        "KEYSET_HASH": body["KEYSET_HASH"],
        "ROWS": rows,
    }


def _category_hash(rows: list[dict[str, Any]]) -> str:
    keys = sorted((row["symbol"], parse_date(row["trade_date"])) for row in rows)
    if len(set(keys)) != len(keys):
        raise AuditError("STATUS0_CATEGORY_DUPLICATE_KEY")
    return keyset_hash(keys) if keys else EMPTY_KEYSET_HASH


def classify_cross_canonical(
    contradiction_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    positive = [row for row in contradiction_rows if row["canonical_present"] and row["canonical_volume"] is not None and row["canonical_volume"] > 0]
    zero = [row for row in contradiction_rows if row["canonical_present"] and row["canonical_volume"] == 0]
    absent = [row for row in contradiction_rows if not row["canonical_present"]]
    if len(positive) + len(zero) + len(absent) != len(contradiction_rows):
        raise AuditError("STATUS0_CROSS_CANONICAL_UNCLASSIFIED")
    return {
        "CONTRADICTION_WITH_CANONICAL_POSITIVE_N": len(positive),
        "CONTRADICTION_WITH_CANONICAL_POSITIVE_KEYSET_HASH": _category_hash(positive),
        "CONTRADICTION_WITH_CANONICAL_ZERO_N": len(zero),
        "CONTRADICTION_WITH_CANONICAL_ZERO_KEYSET_HASH": _category_hash(zero),
        "CONTRADICTION_WITH_CANONICAL_ABSENT_N": len(absent),
        "CONTRADICTION_WITH_CANONICAL_ABSENT_KEYSET_HASH": _category_hash(absent),
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _anchor_payload(
    provider_rows: list[dict[str, Any]],
    canonical_rows: dict[tuple[str, date], dict[str, Any]],
    key: tuple[str, date],
) -> dict[str, Any]:
    provider = next(
        (row for row in provider_rows if (row["symbol"], parse_date(row["trade_date"])) == key),
        None,
    )
    if provider is None:
        raise AuditError(f"STATUS0_ANCHOR_PROVIDER_ROW_MISSING:{key}")
    canonical = canonical_rows.get(key)
    return {
        "symbol": key[0],
        "trade_date": key[1].isoformat(),
        "provider_tradestatus": provider["tradestatus"],
        "provider_volume": provider["volume"],
        "provider_amount": provider["amount"],
        "canonical_present": canonical is not None,
        "canonical_volume": None if canonical is None else canonical["volume"],
        "canonical_amount": None if canonical is None else canonical["amount"],
        "canonical_source": None if canonical is None else canonical["source"],
        "canonical_data_version": None if canonical is None else canonical["data_version"],
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {TASK}",
        "",
        "Offline full internal audit of BaoStock tradestatus=0 rows from the frozen R3 session authority.",
        "",
        "## Authority and receipt verification",
        "",
        f"- `BASE_HEAD={report['BASE_HEAD']}`; `INPUT_MANIFEST_HASH={report['INPUT_MANIFEST_HASH']}`.",
        f"- `SESSION_AUTHORITY_DATASET_HASH={report['SESSION_AUTHORITY_DATASET_HASH']}`; `REQUEST_RECEIPT_INDEX_HASH={report['REQUEST_RECEIPT_INDEX_HASH']}`.",
        f"- receipt pairs verified: `{report['FULL_RECEIPT_PAIR_N']}`; aggregate hash=`{report['FULL_RECEIPT_PAIR_HASH']}`.",
        f"- status0-relevant receipt pairs: `{report['STATUS0_RELEVANT_RECEIPT_N']}`; aggregate hash=`{report['STATUS0_RELEVANT_RECEIPT_HASH']}`.",
        "",
        "## Status0 value audit",
        "",
        f"- `STATUS0_KEY_N={report['STATUS0_KEY_N']}`; provider rows present=`{report['STATUS0_ROW_PRESENT_N']}`.",
        f"- zero/positive volume=`{report['STATUS0_ZERO_VOLUME_N']}/{report['STATUS0_POSITIVE_VOLUME_N']}`.",
        f"- zero/positive amount=`{report['STATUS0_ZERO_AMOUNT_N']}/{report['STATUS0_POSITIVE_AMOUNT_N']}`.",
        f"- positive volume OR amount=`{report['STATUS0_POSITIVE_VOLUME_OR_AMOUNT_N']}`.",
        f"- invalid numeric fields=`{report['STATUS0_INVALID_NUMERIC_N']}`; valid numeric rows=`{report['STATUS0_VALID_NUMERIC_N']}`.",
        f"- contradiction manifest: `{report['STATUS0_CONTRADICTION_KEY_N']}` keys / `{report['STATUS0_CONTRADICTION_KEYSET_HASH']}`.",
        f"- semantics status: `{report['TRADESTATUS0_SEMANTICS_STATUS']}`.",
        "",
        "`tradestatus=0 -> NOT_EXPECTED_BAR` remains the frozen classification label, but this audit finds whether the same provider rows carry OHLCV values that contradict a zero-trading interpretation. Contradictions are not automatically reclassified.",
        "Zero/positive counts apply only to rows with parseable non-negative numeric volume and amount. Blank provider numeric fields are preserved as invalid evidence and are never coerced to zero.",
        "",
        "## Cross-canonical classification",
        "",
        f"- canonical positive-volume: `{report['CONTRADICTION_WITH_CANONICAL_POSITIVE_N']}` / `{report['CONTRADICTION_WITH_CANONICAL_POSITIVE_KEYSET_HASH']}`.",
        f"- canonical zero-volume: `{report['CONTRADICTION_WITH_CANONICAL_ZERO_N']}` / `{report['CONTRADICTION_WITH_CANONICAL_ZERO_KEYSET_HASH']}`.",
        f"- canonical absent: `{report['CONTRADICTION_WITH_CANONICAL_ABSENT_N']}` / `{report['CONTRADICTION_WITH_CANONICAL_ABSENT_KEYSET_HASH']}`.",
        "",
        "## Anchors",
        "",
        f"- `688065_CONTRADICTION={str(report['688065_CONTRADICTION']).lower()}`.",
        f"- `600651_ZERO_VOLUME_CONTROL={str(report['600651_ZERO_VOLUME_CONTROL']).lower()}`.",
        "",
        "```json",
        json.dumps(report["ANCHORS"], ensure_ascii=True, sort_keys=True, indent=2),
        "```",
        "",
        "## Current reconciliation impact",
        "",
        f"- current expected/not-expected/unknown=`{report['CURRENT_EXPECTED_KEY_N']}/{report['CURRENT_NOT_EXPECTED_KEY_N']}/{report['CURRENT_UNKNOWN_KEY_N']}`.",
        f"- potentially misclassified NOT_EXPECTED=`{report['POTENTIALLY_MISCLASSIFIED_NOT_EXPECTED_N']}`.",
        f"- `R3_COMPLETENESS_COUNTS_FROZEN={str(report['R3_COMPLETENESS_COUNTS_FROZEN']).lower()}`; `R3_REFREEZE_RECOMMENDATION={report['R3_REFREEZE_RECOMMENDATION']}`.",
        "",
        "## Safety and verification",
        "",
    ]
    for key, value in report["SAFETY"].items():
        lines.append(f"- `{key}={str(value).lower() if isinstance(value, bool) else value}`")
    lines.extend(
        [
            "",
            f"- `TEST_RESULT={report['TEST_RESULT']}`; `PY_COMPILE={report['PY_COMPILE']}`; `GIT_DIFF_CHECK={report['GIT_DIFF_CHECK']}`.",
            f"- Compact contradiction manifest: `{CONTRADICTION_MANIFEST_NAME}`.",
            f"- Invalid numeric manifest: `{INVALID_NUMERIC_MANIFEST_NAME}`.",
            "",
        ]
    )
    return "\n".join(lines)


def run_audit(
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

    verified = reconciliation._load_stage_authority(repo_root, data_root, stage_root)
    context = verified["context"]
    pre_input = verified["input_manifest"]
    if pre_input.get("INPUT_MANIFEST_HASH") != DAILY_INPUT_MANIFEST_HASH:
        raise AuditError("DAILY_INPUT_MANIFEST_DRIFT")
    session = reconciliation.scan_session_authority(verified["session_path"], context)
    if session.get("SESSION_AUTHORITY_DATASET_HASH") != SESSION_AUTHORITY_DATASET_HASH:
        raise AuditError("SESSION_AUTHORITY_DATASET_HASH_DRIFT")
    receipt_index = verified["receipt_index"]
    if receipt_index.get("REQUEST_RECEIPT_INDEX_HASH") != REQUEST_RECEIPT_INDEX_HASH:
        raise AuditError("REQUEST_RECEIPT_INDEX_DRIFT")

    status0_by_request, authority_counts = collect_status0_keys(verified["session_path"])
    receipt_scan = scan_receipt_pairs(
        verified["stage"], context, receipt_index, status0_by_request
    )
    provider_rows = receipt_scan.pop("STATUS0_PROVIDER_ROWS")
    invalid_rows = receipt_scan.pop("STATUS0_INVALID_ROWS")
    if receipt_scan["STATUS0_ROW_PRESENT_N"] != STATUS0_KEY_N:
        raise AuditError("STATUS0_ROW_PRESENT_MISMATCH")

    metrics = [status0_value_metrics(row["volume"], row["amount"]) for row in provider_rows]
    status0_zero_volume_n = sum(item["volume_zero"] for item in metrics)
    status0_positive_volume_n = sum(item["volume_positive"] for item in metrics)
    status0_zero_amount_n = sum(item["amount_zero"] for item in metrics)
    status0_positive_amount_n = sum(item["amount_positive"] for item in metrics)
    status0_contradiction_n = sum(item["contradiction"] for item in metrics)
    if (
        status0_zero_volume_n + status0_positive_volume_n != len(provider_rows)
        or status0_zero_amount_n + status0_positive_amount_n != len(provider_rows)
        or status0_contradiction_n
        != sum(
            item["volume_positive"] or item["amount_positive"] for item in metrics
        )
    ):
        raise AuditError("STATUS0_METRIC_PARTITION_DRIFT")

    contradiction_keys = {
        (row["symbol"], parse_date(row["trade_date"]))
        for row in provider_rows
        if row["_contradiction"]
    }
    invalid_keys = {
        (row["symbol"], parse_date(row["trade_date"])) for row in invalid_rows
    }
    lookup_keys = contradiction_keys | invalid_keys | {ZERO_VOLUME_CONTROL}
    canonical_rows = canonical_lookup(data_root, lookup_keys)
    contradiction_manifest, contradiction_summary = build_contradiction_manifest(
        provider_rows, canonical_rows
    )
    cross = classify_cross_canonical(contradiction_summary["ROWS"])
    invalid_manifest, invalid_summary = build_invalid_numeric_manifest(invalid_rows)
    invalid_file_hash = sha256_json_file_payload(invalid_manifest)

    zero_control = _anchor_payload(provider_rows, canonical_rows, ZERO_VOLUME_CONTROL)
    if not (
        zero_control["provider_tradestatus"] == 0
        and zero_control["provider_volume"] == 0
        and Decimal(str(zero_control["provider_amount"])) == 0
        and zero_control["canonical_present"] is True
        and zero_control["canonical_volume"] == 0
        and zero_control["canonical_amount"] == 0
    ):
        raise AuditError("STATUS0_ZERO_VOLUME_CONTROL_NOT_CLOSED")
    contradiction_anchor = _anchor_payload(
        provider_rows, canonical_rows, CONTRADICTION_ANCHOR
    )

    try:
        post_input = context["plan"].build_input_file_manifest(Path(data_root))
    except Exception as exc:
        raise AuditError("DAILY_INPUT_POST_AUDIT_RECOMPUTE_FAILED") from exc
    if not reconciliation._input_equal(pre_input, post_input):
        raise AuditError("DAILY_INPUT_DRIFT_DURING_STATUS0_AUDIT")

    contradiction_file_hash = sha256_json_file_payload(contradiction_manifest)
    report = {
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
        "SESSION_AUTHORITY_FILE_SHA256": session["SESSION_AUTHORITY_FILE_SHA256"],
        "REQUEST_RECEIPT_INDEX_HASH": receipt_index["REQUEST_RECEIPT_INDEX_HASH"],
        "REQUEST_RECEIPT_INDEX_FILE_SHA256": verified["REQUEST_RECEIPT_INDEX_FILE_SHA256"],
        "FULL_REQUEST_N": FULL_REQUEST_N,
        "FULL_RECEIPT_PAIR_N": receipt_scan["FULL_RECEIPT_PAIR_N"],
        "FULL_RECEIPT_PAIR_HASH": receipt_scan["FULL_RECEIPT_PAIR_HASH"],
        "STATUS0_RELEVANT_RECEIPT_N": receipt_scan["STATUS0_RELEVANT_RECEIPT_N"],
        "STATUS0_RELEVANT_RECEIPT_HASH": receipt_scan["STATUS0_RELEVANT_RECEIPT_HASH"],
        "RECEIPT_HASH_SERIALIZATION": receipt_scan["RECEIPT_HASH_SERIALIZATION"],
        "STATUS0_KEY_N": STATUS0_KEY_N,
        "STATUS0_ROW_PRESENT_N": receipt_scan["STATUS0_ROW_PRESENT_N"],
        "STATUS0_VALID_NUMERIC_N": len(provider_rows),
        "STATUS0_ZERO_VOLUME_N": status0_zero_volume_n,
        "STATUS0_POSITIVE_VOLUME_N": status0_positive_volume_n,
        "STATUS0_ZERO_AMOUNT_N": status0_zero_amount_n,
        "STATUS0_POSITIVE_AMOUNT_N": status0_positive_amount_n,
        "STATUS0_POSITIVE_VOLUME_OR_AMOUNT_N": status0_contradiction_n,
        "STATUS0_INVALID_NUMERIC_N": len(invalid_rows),
        "STATUS0_INVALID_NUMERIC_MANIFEST": {
            "name": INVALID_NUMERIC_MANIFEST_NAME,
            "hash": invalid_manifest["MANIFEST_HASH"],
            "file_hash": invalid_file_hash,
            "key_n": invalid_summary["KEY_N"],
            "keyset_hash": invalid_summary["KEYSET_HASH"],
        },
        "STATUS0_CONTRADICTION_KEY_N": contradiction_summary["KEY_N"],
        "STATUS0_CONTRADICTION_KEYSET_HASH": contradiction_summary["KEYSET_HASH"],
        "STATUS0_CONTRADICTION_MANIFEST": {
            "name": CONTRADICTION_MANIFEST_NAME,
            "hash": contradiction_manifest["MANIFEST_HASH"],
            "file_hash": contradiction_file_hash,
            "key_n": contradiction_summary["KEY_N"],
            "keyset_hash": contradiction_summary["KEYSET_HASH"],
        },
        **cross,
        "CURRENT_EXPECTED_KEY_N": authority_counts["EXPECTED_KEY_N"],
        "CURRENT_NOT_EXPECTED_KEY_N": authority_counts["NOT_EXPECTED_KEY_N"],
        "CURRENT_UNKNOWN_KEY_N": authority_counts["UNKNOWN_KEY_N"],
        "POTENTIALLY_MISCLASSIFIED_NOT_EXPECTED_N": contradiction_summary["KEY_N"],
        "688065_CONTRADICTION": CONTRADICTION_ANCHOR in contradiction_keys,
        "600651_ZERO_VOLUME_CONTROL": True,
        "ANCHORS": {
            "688065_SH_20230615": contradiction_anchor,
            "600651_SH_20160825": zero_control,
        },
        "TRADESTATUS0_SEMANTICS_STATUS": (
            "REOPENED_CONTRADICTIONS_FOUND"
            if contradiction_summary["KEY_N"] > 0
            else (
                "BLOCKED_INVALID_PROVIDER_FIELDS"
                if invalid_rows
                else "SUPPORTED_AFTER_FULL_INTERNAL_AUDIT"
            )
        ),
        "R3_COMPLETENESS_COUNTS_FROZEN": (
            contradiction_summary["KEY_N"] == 0 and not invalid_rows
        ),
        "R3_REFREEZE_RECOMMENDATION": (
            "BLOCKED"
            if contradiction_summary["KEY_N"] > 0 or invalid_rows
            else "NO_AUTOMATIC_REFREEZE"
        ),
        "RECEIPT_HASH_INDEX": {
            "name": RECEIPT_HASH_INDEX_NAME,
            "scope": "status0-relevant receipt pairs only",
            "row_n": len(receipt_scan["STATUS0_RECEIPT_HASH_ROWS"]),
            "hash": sha256_json(receipt_scan["STATUS0_RECEIPT_HASH_ROWS"]),
        },
        "AUTHORITY_COUNTS": authority_counts,
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
    }

    # No data-root write is reachable: all data reads, hashes, and fail-closed
    # checks finish before the following repository-only writes.
    write_repo_json(repo_root, CONTRADICTION_MANIFEST_NAME, contradiction_manifest)
    write_repo_json(repo_root, INVALID_NUMERIC_MANIFEST_NAME, invalid_manifest)
    write_repo_json(repo_root, RECEIPT_HASH_INDEX_NAME, {
        "TASK": TASK,
        "FULL_RECEIPT_PAIR_N": receipt_scan["FULL_RECEIPT_PAIR_N"],
        "FULL_RECEIPT_PAIR_HASH": receipt_scan["FULL_RECEIPT_PAIR_HASH"],
        "STATUS0_RELEVANT_RECEIPT_N": receipt_scan["STATUS0_RELEVANT_RECEIPT_N"],
        "STATUS0_RELEVANT_RECEIPT_HASH": receipt_scan["STATUS0_RELEVANT_RECEIPT_HASH"],
        "SERIALIZATION": receipt_scan["RECEIPT_HASH_SERIALIZATION"],
        "ROWS": receipt_scan["STATUS0_RECEIPT_HASH_ROWS"],
    })
    write_repo_json(repo_root, REPORT_NAME, report)
    write_repo_text(repo_root, REPORT_MD_NAME, _markdown(report))
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
        report = run_audit(
            repo_root=args.repo_root,
            data_root=args.data_root,
            stage_root=args.stage_root,
            test_result=args.test_result,
            py_compile_result=args.py_compile_result,
            diff_check_result=args.diff_check_result,
        )
    except (AuditError, reconciliation.ReconciliationError) as exc:
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
