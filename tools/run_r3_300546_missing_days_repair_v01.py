#!/usr/bin/env python3
"""Bounded authority closure, candidate construction, and promotion for two R3 gaps.

The tool has three deliberately separate phases:

``authority``
    Validate the current canonical input and the two-key absence precondition,
    then either reuse a durable receipt or make exactly one bounded BaoStock
    request for ``sz.300546``.

``candidate``
    Read the receipt and current canonical only.  Write two isolated parquet
    candidates, one in each existing date partition, and prove that the only
    dataset change is the two requested insertions.

``promote``
    Re-run the candidate gate, prepare rollback backups, perform a final
    PRE->EXPECTED_POST->POST manifest closure, and atomically replace exactly
    the two canonical partition files.  Any failure after the first replace
    attempts a complete rollback and leaves the transaction root for recovery
    inspection.

No TDX client is imported here.  BaoStock is imported only inside the
authority phase and only for the explicitly bounded two-day request.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import polars as pl

TOOLS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_ROOT / "audits"))
from r3_tdx_volume_rebuild_scope_v01 import (  # noqa: E402
    build_input_file_manifest,
    manifest_byte_form,
)


TASK = "R3_300546_MISSING_DAYS_REPAIR_V01"
BASE_HEAD = "5228dad78e90118eb481a5323f2a4bcce6ac9c28"
EXECUTION_BASE_HEAD = BASE_HEAD
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
TARGET_SYMBOL = "300546.SZ"
TARGET_DATES = ("2016-09-29", "2016-10-10")
TARGET_KEYS = tuple((TARGET_SYMBOL, value) for value in TARGET_DATES)
BAOSTOCK_CODE = "sz.300546"
QUERY_FIELDS = "date,code,open,high,low,close,volume,amount,preclose,tradestatus"
QUERY_START_DATE = "2016-09-29"
QUERY_END_DATE = "2016-10-10"
QUERY_FREQUENCY = "d"
QUERY_ADJUSTFLAG = "3"
CANONICAL_COLUMNS = [
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
]
CURRENT_INPUT_FILE_N = 2580
CURRENT_INPUT_MANIFEST_HASH = (
    "2e0664b4eafb64325691cf2fb8b955a61906222173fc570e755985a604fb3da0"
)
CURRENT_INPUT_MANIFEST_REPORT = (
    "reports/implementation/"
    "R3_TDX_VOLUME_CANONICAL_PROMOTION_POST_INPUT_MANIFEST_V01.json"
)
CHANGED_MANIFEST_NAME = "R3_TDX_VOLUME_TARGETED_REFETCH_CHANGED_MANIFEST_V01_1.json"
CHANGED_MANIFEST_HASH = (
    "f1cdb9d5416535e76631673479ef1fc36539a8626ee18026a581f6e1191b62c7"
)
PRIOR_VOLUME_REPAIR_KEY_N = 1169
STAGE_ROOT_NAME = "r3_300546_missing_days_repair_v01"
PROMOTION_ROOT_NAME = "r3_300546_missing_days_promotion_v01"
AUTHORITY_RECEIPT_NAME = "authority_baostock_receipt.json"
CANDIDATE_DIR_NAME = "candidate"
CANDIDATE_MANIFEST_NAME = "candidate_repair_manifest.json"
CANDIDATE_VALIDATION_NAME = "candidate_validation_report.json"
AUTHORITY_REPORT_NAME = "R3_300546_MISSING_DAYS_AUTHORITY_V01.json"
AUTHORITY_REPORT_MD_NAME = "R3_300546_MISSING_DAYS_AUTHORITY_V01.md"
CANDIDATE_REPORT_NAME = "R3_300546_MISSING_DAYS_CANDIDATE_V01.json"
CANDIDATE_REPORT_MD_NAME = "R3_300546_MISSING_DAYS_CANDIDATE_V01.md"
EXPECTED_POST_REPORT_NAME = "R3_300546_MISSING_DAYS_EXPECTED_POST_INPUT_MANIFEST_V01.json"
OBSERVED_POST_REPORT_NAME = "R3_300546_MISSING_DAYS_POST_INPUT_MANIFEST_V01.json"
PROMOTION_RECEIPT_NAME = "R3_300546_MISSING_DAYS_PROMOTION_RECEIPT_V01.json"
FINAL_REPORT_NAME = "R3_300546_MISSING_DAYS_REPAIR_V01.json"
FINAL_REPORT_MD_NAME = "R3_300546_MISSING_DAYS_REPAIR_V01.md"
TRANSACTION_PLAN_NAME = "promotion_plan.json"
TRANSACTION_STATE_NAME = "transaction_state.json"
TRANSACTION_EXPECTED_NAME = "expected_post_input_manifest.json"
TRANSACTION_OBSERVED_NAME = "observed_post_input_manifest.json"
TRANSACTION_RECEIPT_NAME = "promotion_receipt.json"


class RepairError(RuntimeError):
    """Fail-closed error for this bounded repair."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RepairError(message)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RepairError(f"FILE_READ_FAILED:{path}") from exc
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepairError(f"UNREADABLE_JSON:{path}") from exc


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RepairError(f"TEMP_OUTPUT_ALREADY_EXISTS:{temporary}")
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
        raise RepairError(f"ATOMIC_WRITE_FAILED:{path}") from exc


def write_json_atomic(path: Path, payload: Any) -> None:
    write_bytes_atomic(path, canonical_json_bytes(payload))


def write_new_or_same(path: Path, payload: Any) -> None:
    data = canonical_json_bytes(payload)
    if path.exists() or path.is_symlink():
        _require(path.is_file() and not path.is_symlink(), f"OUTPUT_PATH_INVALID:{path}")
        _require(path.read_bytes() == data, f"OUTPUT_ALREADY_EXISTS_DIFFERENT:{path}")
        return
    write_bytes_atomic(path, data)


def write_text_new_or_same(path: Path, text: str) -> None:
    data = text.encode("utf-8")
    if path.exists() or path.is_symlink():
        _require(path.is_file() and not path.is_symlink(), f"OUTPUT_PATH_INVALID:{path}")
        _require(path.read_bytes() == data, f"OUTPUT_ALREADY_EXISTS_DIFFERENT:{path}")
        return
    write_bytes_atomic(path, data)


def date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def key_of(row: dict[str, Any]) -> tuple[str, str]:
    return str(row["symbol"]), date_text(row["trade_date"])


def canonical_key_text(key: tuple[str, str]) -> str:
    return f"{key[0]}:{key[1]}"


def require_isolated_stage_root(data_root: Path, stage_root: Path) -> Path:
    """Return the only allowed stage root after rejecting path escapes."""
    data_input = Path(data_root).expanduser()
    stage_input = Path(stage_root).expanduser()
    if not data_input.is_absolute() or not stage_input.is_absolute():
        raise RepairError("ISOLATED_STAGE_ROOT_NOT_ABSOLUTE")
    if not data_input.is_dir():
        raise RepairError("DATA_ROOT_NOT_DIRECTORY")
    data_resolved = data_input.resolve(strict=True)
    stage_resolved = stage_input.resolve(strict=False)
    expected = data_resolved / "staging" / STAGE_ROOT_NAME
    if data_input != data_resolved:
        raise RepairError("DATA_ROOT_SYMLINK_OR_TRAVERSAL")
    if stage_input != stage_resolved or stage_resolved != expected:
        raise RepairError("ISOLATED_STAGE_ROOT_FORBIDDEN")
    staging = data_resolved / "staging"
    if staging.is_symlink() or stage_input.is_symlink():
        raise RepairError("ISOLATED_STAGE_ROOT_SYMLINK")
    return stage_resolved


def require_promotion_root(data_root: Path, promotion_root: Path) -> Path:
    data_input = Path(data_root).expanduser()
    root_input = Path(promotion_root).expanduser()
    if not data_input.is_absolute() or not root_input.is_absolute():
        raise RepairError("PROMOTION_ROOT_NOT_ABSOLUTE")
    if not data_input.is_dir():
        raise RepairError("DATA_ROOT_NOT_DIRECTORY")
    data_resolved = data_input.resolve(strict=True)
    root_resolved = root_input.resolve(strict=False)
    expected = data_resolved / "staging" / PROMOTION_ROOT_NAME
    if data_input != data_resolved:
        raise RepairError("DATA_ROOT_SYMLINK_OR_TRAVERSAL")
    if root_input != root_resolved or root_resolved != expected:
        raise RepairError("PROMOTION_ROOT_FORBIDDEN")
    if (data_resolved / "staging").is_symlink() or root_input.is_symlink():
        raise RepairError("PROMOTION_ROOT_SYMLINK")
    return root_resolved


def safe_join(root: Path, relative_path: str, label: str) -> Path:
    relative = Path(relative_path)
    _require(not relative.is_absolute() and ".." not in relative.parts, f"{label}_PATH_TRAVERSAL")
    root_resolved = root.resolve(strict=True)
    current = root
    for part in relative.parts:
        current = current / part
        _require(not current.is_symlink(), f"{label}_SYMLINK_COMPONENT")
    resolved = (root / relative).resolve(strict=False)
    _require(resolved.is_relative_to(root_resolved), f"{label}_PATH_ESCAPE")
    return resolved


def require_current_head(repo_root: Path) -> None:
    try:
        current = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RepairError("GIT_HEAD_UNAVAILABLE") from exc
    _require(current == BASE_HEAD, f"BASE_HEAD_MISMATCH:{current}")


def require_manifest_equal(left: dict[str, Any], right: dict[str, Any], label: str) -> None:
    if (
        left.get("INPUT_FILE_N") != right.get("INPUT_FILE_N")
        or left.get("INPUT_MANIFEST_HASH") != right.get("INPUT_MANIFEST_HASH")
        or left.get("FILES") != right.get("FILES")
    ):
        raise RepairError(f"{label}_FILES_MISMATCH")


def require_current_input(data_root: Path, repo_root: Path) -> dict[str, Any]:
    expected = load_json(repo_root / CURRENT_INPUT_MANIFEST_REPORT)
    live = build_input_file_manifest(data_root)
    _require(live.get("INPUT_FILE_N") == CURRENT_INPUT_FILE_N, "INPUT_FILE_N_MISMATCH")
    _require(
        live.get("INPUT_MANIFEST_HASH") == CURRENT_INPUT_MANIFEST_HASH,
        "INPUT_MANIFEST_DRIFT",
    )
    require_manifest_equal(expected, live, "INPUT_MANIFEST_EXPECTED")
    return live


def partition_path_map(input_manifest: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    marker = "trade_date="
    for item in input_manifest.get("FILES", []):
        relative = str(item["relative_path"])
        if marker not in relative:
            continue
        value = relative.split(marker, 1)[1].split("/", 1)[0]
        if len(value) != 10:
            continue
        if value in result and result[value] != relative:
            raise RepairError(f"CANONICAL_PARTITION_DUPLICATE:{value}")
        result[value] = relative
    return result


def _read_symbol_key_rows(data_root: Path, input_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in input_manifest["FILES"]:
        path = safe_join(data_root, str(item["relative_path"]), "CANONICAL_READ")
        _require(path.is_file() and not path.is_symlink(), f"CANONICAL_FILE_MISSING:{path}")
        try:
            frame = pl.read_parquet(path, columns=["symbol", "trade_date", "volume"])
        except Exception as exc:  # pragma: no cover - concrete parquet errors vary by runtime
            raise RepairError(f"CANONICAL_READ_FAILED:{path}") from exc
        selected = frame.filter(pl.col("symbol") == TARGET_SYMBOL)
        rows.extend(selected.iter_rows(named=True))
    return rows


def inspect_300546_keyspace(
    data_root: Path, input_manifest: dict[str, Any]
) -> dict[str, Any]:
    rows = _read_symbol_key_rows(data_root, input_manifest)
    keys = [(TARGET_SYMBOL, date_text(row["trade_date"])) for row in rows]
    counts = Counter(keys)
    duplicates = sorted(
        canonical_key_text(key) for key, count in counts.items() if count > 1
    )
    present_targets = sorted(
        canonical_key_text(key) for key in TARGET_KEYS if counts.get(key, 0) > 0
    )
    return {
        "ROW_N": len(rows),
        "UNIQUE_KEY_N": len(counts),
        "DUPLICATE_KEY_N": len(duplicates),
        "DUPLICATE_KEYS": duplicates,
        "TARGET_KEYS_PRESENT": present_targets,
        "TARGET_KEY_CARDINALITY": {
            canonical_key_text(key): counts.get(key, 0) for key in TARGET_KEYS
        },
    }


def require_target_keys_absent(data_root: Path, input_manifest: dict[str, Any]) -> dict[str, Any]:
    inspection = inspect_300546_keyspace(data_root, input_manifest)
    _require(inspection["DUPLICATE_KEY_N"] == 0, "DUPLICATE_300546_CANONICAL_KEY")
    _require(not inspection["TARGET_KEYS_PRESENT"], "TARGET_KEY_ALREADY_EXISTS")
    return inspection


def require_target_keys_exactly_once(
    data_root: Path, input_manifest: dict[str, Any]
) -> dict[str, Any]:
    inspection = inspect_300546_keyspace(data_root, input_manifest)
    _require(inspection["DUPLICATE_KEY_N"] == 0, "DUPLICATE_300546_CANONICAL_KEY_POST")
    expected = {canonical_key_text(key) for key in TARGET_KEYS}
    actual = set(inspection["TARGET_KEYS_PRESENT"])
    _require(actual == expected, "TARGET_KEYS_POST_CARDINALITY_FAILED")
    _require(
        all(value == 1 for value in inspection["TARGET_KEY_CARDINALITY"].values()),
        "TARGET_KEY_NOT_EXACTLY_ONCE",
    )
    return inspection


def parse_finite_float(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RepairError(f"INVALID_FLOAT:{label}") from exc
    if not math.isfinite(parsed):
        raise RepairError(f"INVALID_FLOAT:{label}")
    return parsed


def parse_exact_int(value: Any, label: str) -> int:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RepairError(f"INVALID_INTEGER:{label}") from exc
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise RepairError(f"INVALID_INTEGER:{label}")
    return int(parsed)


def parse_utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RepairError("INVALID_FETCHED_AT") from exc
    if parsed.tzinfo is None:
        raise RepairError("FETCHED_AT_NOT_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


def provider_error_code(result: Any) -> str:
    return str(getattr(result, "error_code", "UNKNOWN"))


def provider_error_message(result: Any) -> str:
    return str(getattr(result, "error_msg", ""))


def normalize_raw_authority_row(raw: list[Any], row_index: int) -> dict[str, Any]:
    if len(raw) != 10:
        raise RepairError(f"BAOSTOCK_ROW_FIELD_N_INVALID:{row_index}")
    raw_date = str(raw[0])
    raw_code = str(raw[1])
    try:
        parsed_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise RepairError(f"BAOSTOCK_ROW_DATE_INVALID:{row_index}") from exc
    _require(raw_code.lower() == BAOSTOCK_CODE, f"BAOSTOCK_CODE_MISMATCH:{raw_code}")
    return {
        "date": parsed_date.isoformat(),
        "code": raw_code,
        "open": parse_finite_float(raw[2], f"open:{row_index}"),
        "high": parse_finite_float(raw[3], f"high:{row_index}"),
        "low": parse_finite_float(raw[4], f"low:{row_index}"),
        "close": parse_finite_float(raw[5], f"close:{row_index}"),
        "volume": parse_exact_int(raw[6], f"volume:{row_index}"),
        "amount": parse_finite_float(raw[7], f"amount:{row_index}"),
        "preclose": parse_finite_float(raw[8], f"preclose:{row_index}"),
        "tradestatus": parse_exact_int(raw[9], f"tradestatus:{row_index}"),
    }


def provider_version(provider: Any) -> dict[str, Any]:
    try:
        distribution_version = importlib.metadata.version("baostock")
    except importlib.metadata.PackageNotFoundError:
        distribution_version = None
    return {
        "distribution": "baostock",
        "distribution_version": distribution_version,
        "module_version": getattr(provider, "__version__", None),
        "query_runtime": "baostock-0.9.3",
    }


def _authority_fact(raw: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    return {
        "symbol": TARGET_SYMBOL,
        "trade_date": raw["date"],
        "open": raw["open"],
        "high": raw["high"],
        "low": raw["low"],
        "close": raw["close"],
        "volume": raw["volume"],
        "amount": raw["amount"],
        "preclose": raw["preclose"],
        "tradestatus": raw["tradestatus"],
        "source": "baostock",
        "data_version": "v2",
        "fetched_at": fetched_at,
        "provenance": {
            "authority": "BAOSTOCK_QUERY_HISTORY_K_DATA_PLUS",
            "provider_code": BAOSTOCK_CODE,
            "fields": QUERY_FIELDS,
            "frequency": QUERY_FREQUENCY,
            "adjustflag": QUERY_ADJUSTFLAG,
            "role": "BOUNDED_TWO_DAY_MISSING_BAR_AUTHORITY",
        },
    }


def _existing_row_for_key(path: Path, key: tuple[str, str]) -> dict[str, Any] | None:
    frame = pl.read_parquet(path)
    rows = [row for row in frame.iter_rows(named=True) if key_of(row) == key]
    if len(rows) > 1:
        raise RepairError(f"CANONICAL_KEY_DUPLICATE:{canonical_key_text(key)}")
    return rows[0] if rows else None


def _numeric_equal(left: Any, right: Any) -> bool:
    return parse_finite_float(left, "left") == parse_finite_float(right, "right")


def verify_existing_crosscheck_20160930(
    data_root: Path, input_manifest: dict[str, Any], raw_rows: list[dict[str, Any]]
) -> None:
    partition = partition_path_map(input_manifest).get("2016-09-30")
    _require(partition is not None, "CANONICAL_PARTITION_MISSING:2016-09-30")
    existing = _existing_row_for_key(
        safe_join(data_root, partition, "CANONICAL_READ"), (TARGET_SYMBOL, "2016-09-30")
    )
    _require(existing is not None, "CANONICAL_CROSSCHECK_ROW_MISSING:2016-09-30")
    matching = [row for row in raw_rows if row["date"] == "2016-09-30"]
    _require(len(matching) == 1, "BAOSTOCK_CROSSCHECK_ROW_N_INVALID")
    authority = matching[0]
    for field in ("open", "high", "low", "close", "volume", "amount"):
        if field == "volume":
            equal = parse_exact_int(existing[field], field) == authority[field]
        else:
            equal = _numeric_equal(existing[field], authority[field])
        _require(equal, f"BAOSTOCK_EXISTING_CROSSCHECK_MISMATCH:{field}")


def fetch_baostock_authority(
    *,
    data_root: Path,
    input_manifest: dict[str, Any],
    provider: Any | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    if provider is None:
        try:
            import baostock as provider  # type: ignore[no-redef]
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RepairError("BAOSTOCK_IMPORT_FAILED") from exc
    clock = now or (lambda: datetime.now(timezone.utc))
    login = provider.login()
    _require(provider_error_code(login) == "0", f"BAOSTOCK_LOGIN_FAILED:{provider_error_message(login)}")
    rows: list[list[Any]] = []
    query_error_code = "UNKNOWN"
    query_error_message = ""
    logout_error: str | None = None
    fetched_at = clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        result = provider.query_history_k_data_plus(
            BAOSTOCK_CODE,
            QUERY_FIELDS,
            start_date=QUERY_START_DATE,
            end_date=QUERY_END_DATE,
            frequency=QUERY_FREQUENCY,
            adjustflag=QUERY_ADJUSTFLAG,
        )
        query_error_code = provider_error_code(result)
        query_error_message = provider_error_message(result)
        if query_error_code == "0":
            while result.next():
                rows.append(list(result.get_row_data()))
    finally:
        try:
            logout_result = provider.logout()
            if logout_result is not None and provider_error_code(logout_result) != "0":
                logout_error = provider_error_message(logout_result)
        except Exception as exc:  # pragma: no cover - provider implementation dependent
            logout_error = f"{type(exc).__name__}:{exc}"
    _require(query_error_code == "0", f"BAOSTOCK_QUERY_FAILED:{query_error_message}")
    _require(logout_error is None, f"BAOSTOCK_LOGOUT_FAILED:{logout_error}")
    normalized = [normalize_raw_authority_row(row, index) for index, row in enumerate(rows)]
    target_rows = [row for row in normalized if row["date"] in TARGET_DATES]
    _require(len(target_rows) == 2, "BAOSTOCK_TARGET_ROW_N_MISMATCH")
    _require({row["date"] for row in target_rows} == set(TARGET_DATES), "BAOSTOCK_TARGET_DATE_SET_MISMATCH")
    _require(all(row["tradestatus"] == 1 for row in target_rows), "BAOSTOCK_TARGET_NOT_TRADING")
    _require(all(row["volume"] > 0 for row in target_rows), "BAOSTOCK_TARGET_NONPOSITIVE_VOLUME")
    verify_existing_crosscheck_20160930(data_root, input_manifest, normalized)
    facts = [_authority_fact(row, fetched_at) for row in sorted(target_rows, key=lambda x: x["date"])]
    payload: dict[str, Any] = {
        "REPORT": "R3_300546_MISSING_DAYS_AUTHORITY_V01",
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "EXECUTION_BASE_HEAD": EXECUTION_BASE_HEAD,
        "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
        "TARGET_SYMBOL": TARGET_SYMBOL,
        "TARGET_DATES": list(TARGET_DATES),
        "TARGET_KEY_N": 2,
        "SOURCE": "BaoStock",
        "SOURCE_ROLE": "BOUNDED_SECONDARY_OHLCV_AUTHORITY_FOR_TDX_HISTORICAL_GAP",
        "BAOSTOCK_REQUEST_N": 1,
        "NETWORK_PROVIDER_DATA_FETCH": "BAOSTOCK_BOUNDED_ONLY",
        "QUERY_RUNTIME": provider_version(provider),
        "QUERY": {
            "code": BAOSTOCK_CODE,
            "fields": QUERY_FIELDS,
            "start_date": QUERY_START_DATE,
            "end_date": QUERY_END_DATE,
            "frequency": QUERY_FREQUENCY,
            "adjustflag": QUERY_ADJUSTFLAG,
        },
        "RESPONSE_ERROR_CODE": query_error_code,
        "RESPONSE_ERROR_MESSAGE": query_error_message,
        "RESPONSE_ROW_N": len(rows),
        "RAW_RESPONSE_ROWS": [[str(value) for value in row] for row in rows],
        "NORMALIZED_RESPONSE_ROWS": normalized,
        "TARGET_FACTS": facts,
        "IN_WINDOW_NON_TARGET_ROW_N": len(normalized) - len(target_rows),
        "FETCHED_AT": fetched_at,
        "TDX_REFETCH_EXECUTED": False,
        "CANONICAL_WRITE_EXECUTED": False,
        "300546_MISSING_DAYS_MUTATED": False,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
    }
    payload["AUTHORITY_PAYLOAD_SHA256"] = sha256_bytes(canonical_json_bytes(payload))
    return payload


def verify_authority_receipt(
    receipt: dict[str, Any], input_manifest: dict[str, Any]
) -> dict[str, Any]:
    _require(receipt.get("INPUT_FILE_N") == CURRENT_INPUT_FILE_N, "AUTHORITY_INPUT_FILE_N_MISMATCH")
    _require(
        receipt.get("INPUT_MANIFEST_HASH") == CURRENT_INPUT_MANIFEST_HASH,
        "AUTHORITY_INPUT_MANIFEST_MISMATCH",
    )
    payload_hash = receipt.get("AUTHORITY_PAYLOAD_SHA256")
    _require(isinstance(payload_hash, str), "AUTHORITY_RECEIPT_HASH_MISSING")
    unsigned = dict(receipt)
    unsigned.pop("AUTHORITY_PAYLOAD_SHA256", None)
    _require(
        sha256_bytes(canonical_json_bytes(unsigned)) == payload_hash,
        "AUTHORITY_RECEIPT_HASH_MISMATCH",
    )
    facts = receipt.get("TARGET_FACTS")
    _require(isinstance(facts, list) and len(facts) == 2, "AUTHORITY_TARGET_FACT_N_MISMATCH")
    keys = [(str(row.get("symbol")), str(row.get("trade_date"))) for row in facts]
    _require(sorted(keys) == sorted(TARGET_KEYS), "AUTHORITY_TARGET_KEY_SET_MISMATCH")
    _require(len(set(keys)) == 2, "AUTHORITY_TARGET_KEY_DUPLICATE")
    for row in facts:
        _require(row.get("tradestatus") == 1, "AUTHORITY_TARGET_NOT_TRADING")
        _require(parse_exact_int(row.get("volume"), "authority_volume") > 0, "AUTHORITY_VOLUME_INVALID")
        for field in ("open", "high", "low", "close", "amount", "preclose"):
            parse_finite_float(row.get(field), f"authority_{field}")
        parse_utc_datetime(str(row.get("fetched_at")))
    return receipt


def authority_markdown(payload: dict[str, Any], reused: bool) -> str:
    lines = [
        "# R3 300546 MISSING DAYS AUTHORITY V01",
        "",
        f"AUTHOR_STATUS: `{payload['AUTHOR_STATUS']}`",
        "",
        f"- EXECUTION_BASE_HEAD: `{payload['EXECUTION_BASE_HEAD']}`",
        f"- INPUT_FILE_N: {payload['INPUT_FILE_N']}",
        f"- INPUT_MANIFEST_HASH: `{payload['INPUT_MANIFEST_HASH']}`",
        f"- SOURCE: `{payload['SOURCE']}`",
        f"- SOURCE_ROLE: `{payload['SOURCE_ROLE']}`",
        f"- BAOSTOCK_REQUEST_N: {payload['BAOSTOCK_REQUEST_N']}",
        f"- AUTHORITY_RECEIPT_REUSED: {str(reused).lower()}",
        "",
        "## Target facts",
        "",
    ]
    for row in payload["TARGET_FACTS"]:
        lines.append(
            f"- `{row['symbol']}:{row['trade_date']}` OHLC="
            f"({row['open']},{row['high']},{row['low']},{row['close']}) "
            f"volume={row['volume']} amount={row['amount']} preclose={row['preclose']} "
            f"tradestatus={row['tradestatus']} source={row['source']} "
            f"data_version={row['data_version']}"
        )
    lines.extend(
        [
            "",
            "The BaoStock request was one bounded query for `sz.300546`, "
            "2016-09-29 through 2016-10-10, using the recorded field and "
            "adjustment contract. The in-window 2016-09-30 row was used only "
            "as a cross-check against the existing canonical row and is not "
            "a repair target.",
            "",
            "SAFETY: TDX_REFETCH_EXECUTED=false; canonical write=false in this phase; "
            "R4A9_RESUME_AUTHORIZED=false; PRECLOSE_COMPLETE=false.",
            "",
        ]
    )
    return "\n".join(lines)


def run_authority(
    *,
    repo_root: Path,
    data_root: Path,
    stage_root: Path,
    allow_baostock_fetch: bool,
    provider: Any | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    require_isolated_stage_root(data_root, stage_root)
    require_current_head(repo_root)
    input_manifest = require_current_input(data_root, repo_root)
    precondition = require_target_keys_absent(data_root, input_manifest)
    receipt_path = stage_root / AUTHORITY_RECEIPT_NAME
    if receipt_path.exists() or receipt_path.is_symlink():
        _require(receipt_path.is_file() and not receipt_path.is_symlink(), "AUTHORITY_RECEIPT_PATH_INVALID")
        receipt = verify_authority_receipt(load_json(receipt_path), input_manifest)
        write_new_or_same(repo_root / "reports/implementation" / AUTHORITY_REPORT_NAME, receipt)
        write_text_new_or_same(
            repo_root / "reports/implementation" / AUTHORITY_REPORT_MD_NAME,
            authority_markdown(receipt, reused=True),
        )
        return {"AUTHORITY": receipt, "AUTHORITY_RECEIPT_REUSED": True, "PRECONDITION": precondition}
    if stage_root.exists() or stage_root.is_symlink():
        _require(stage_root.is_dir() and not stage_root.is_symlink(), "AUTHORITY_STAGE_ROOT_INVALID")
        _require(not any(stage_root.iterdir()), "AUTHORITY_STAGE_ROOT_NONEMPTY_WITHOUT_RECEIPT")
    _require(allow_baostock_fetch, "AUTHORITY_RECEIPT_MISSING_FETCH_NOT_ENABLED")
    receipt = fetch_baostock_authority(
        data_root=data_root,
        input_manifest=input_manifest,
        provider=provider,
        now=now,
    )
    verify_authority_receipt(receipt, input_manifest)
    write_json_atomic(receipt_path, receipt)
    write_new_or_same(repo_root / "reports/implementation" / AUTHORITY_REPORT_NAME, receipt)
    write_text_new_or_same(
        repo_root / "reports/implementation" / AUTHORITY_REPORT_MD_NAME,
        authority_markdown(receipt, reused=False),
    )
    return {"AUTHORITY": receipt, "AUTHORITY_RECEIPT_REUSED": False, "PRECONDITION": precondition}


def load_authority_for_stage(
    *, data_root: Path, stage_root: Path, input_manifest: dict[str, Any]
) -> dict[str, Any]:
    require_isolated_stage_root(data_root, stage_root)
    receipt_path = stage_root / AUTHORITY_RECEIPT_NAME
    _require(receipt_path.is_file() and not receipt_path.is_symlink(), "AUTHORITY_RECEIPT_MISSING")
    return verify_authority_receipt(load_json(receipt_path), input_manifest)


def load_changed_manifest(repo_root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = repo_root / "reports/implementation" / CHANGED_MANIFEST_NAME
    _require(path.is_file() and not path.is_symlink(), "PRIOR_CHANGED_MANIFEST_MISSING")
    _require(sha256_file(path) == CHANGED_MANIFEST_HASH, "PRIOR_CHANGED_MANIFEST_HASH_MISMATCH")
    rows = load_json(path)
    _require(isinstance(rows, list) and len(rows) == PRIOR_VOLUME_REPAIR_KEY_N, "PRIOR_CHANGED_MANIFEST_N_MISMATCH")
    keys = [(str(row["symbol"]), str(row["trade_date"])) for row in rows]
    _require(len(set(keys)) == PRIOR_VOLUME_REPAIR_KEY_N, "PRIOR_CHANGED_MANIFEST_DUPLICATE")
    return {key: row for key, row in zip(keys, rows)}


def verify_prior_volume_repair(
    *, data_root: Path, input_manifest: dict[str, Any], repo_root: Path
) -> dict[str, Any]:
    changed = load_changed_manifest(repo_root)
    paths = partition_path_map(input_manifest)
    checked = 0
    mismatch = 0
    for trade_date in sorted({key[1] for key in changed}):
        relative = paths.get(trade_date)
        _require(relative is not None, f"PRIOR_VOLUME_PARTITION_MISSING:{trade_date}")
        frame = pl.read_parquet(safe_join(data_root, relative, "CANONICAL_READ"))
        rows = {key_of(row): row for row in frame.iter_rows(named=True)}
        for key, expected in changed.items():
            if key[1] != trade_date:
                continue
            checked += 1
            row = rows.get(key)
            if row is None or parse_exact_int(row["volume"], "canonical_volume") != parse_exact_int(
                expected["fresh_tdx_volume"], "fresh_tdx_volume"
            ):
                mismatch += 1
    _require(checked == PRIOR_VOLUME_REPAIR_KEY_N, "PRIOR_VOLUME_REPAIR_CHECK_N_MISMATCH")
    _require(mismatch == 0, "PRIOR_VOLUME_REPAIR_VALUE_MISMATCH")
    return {
        "PRIOR_VOLUME_REPAIR_KEY_N": checked,
        "PRIOR_VOLUME_REPAIR_MISMATCH_N": mismatch,
        "CHANGED_KEY_MANIFEST_HASH": CHANGED_MANIFEST_HASH,
    }


def authority_fact_map(receipt: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row["symbol"]), str(row["trade_date"])): row
        for row in receipt["TARGET_FACTS"]
    }


def _row_cell_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        return value.hex()
    return value


def rows_equal(left: dict[str, Any], right: dict[str, Any], columns: list[str] = CANONICAL_COLUMNS) -> bool:
    return all(_row_cell_value(left.get(column)) == _row_cell_value(right.get(column)) for column in columns)


def _frame_key_map(frame: pl.DataFrame, label: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = frame.iter_rows(named=True)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = key_of(row)
        if key in result:
            raise RepairError(f"DUPLICATE_KEY_IN_{label}:{canonical_key_text(key)}")
        result[key] = row
    return result


def expected_authority_row(fact: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    fetched_at = parse_utc_datetime(str(fact["fetched_at"]))
    row: dict[str, Any] = {
        "symbol": TARGET_SYMBOL,
        "trade_date": date.fromisoformat(str(fact["trade_date"])),
        "open": parse_finite_float(fact["open"], "open"),
        "high": parse_finite_float(fact["high"], "high"),
        "low": parse_finite_float(fact["low"], "low"),
        "close": parse_finite_float(fact["close"], "close"),
        "volume": parse_exact_int(fact["volume"], "volume"),
        "amount": parse_finite_float(fact["amount"], "amount"),
        "source": "baostock",
        "data_version": "v2",
        "fetched_at": fetched_at,
    }
    # Constructing from a source schema is intentional: it keeps the new row
    # in the exact canonical physical schema without changing existing cells.
    return row


def build_candidate_frame(
    source: pl.DataFrame,
    inserts: list[dict[str, Any]],
) -> pl.DataFrame:
    _require(source.columns == CANONICAL_COLUMNS, "CANONICAL_SCHEMA_COLUMNS_MISMATCH")
    new_rows = [expected_authority_row(row, source.schema) for row in inserts]
    new_frame = pl.DataFrame(new_rows, schema=source.schema)
    candidate = pl.concat([source, new_frame], how="vertical")
    _require(candidate.columns == source.columns and candidate.schema == source.schema, "CANDIDATE_SCHEMA_DELTA")
    return candidate


def exact_insert_diff(
    source: pl.DataFrame,
    candidate: pl.DataFrame,
    expected_inserts: dict[tuple[str, str], dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    _require(source.columns == candidate.columns, f"SCHEMA_DELTA:{label}")
    _require(source.schema == candidate.schema, f"SCHEMA_DELTA:{label}")
    source_map = _frame_key_map(source, f"SOURCE:{label}")
    candidate_map = _frame_key_map(candidate, f"CANDIDATE:{label}")
    source_keys = set(source_map)
    candidate_keys = set(candidate_map)
    inserted = candidate_keys - source_keys
    deleted = source_keys - candidate_keys
    modified = {
        key
        for key in source_keys & candidate_keys
        if not rows_equal(source_map[key], candidate_map[key])
    }
    expected_keys = set(expected_inserts)
    _require(inserted == expected_keys, f"INSERTED_KEY_SET_MISMATCH:{label}")
    _require(not deleted, f"DELETED_EXISTING_ROW:{label}")
    _require(not modified, f"MODIFIED_EXISTING_ROW:{label}")
    for key, expected in expected_inserts.items():
        _require(rows_equal(candidate_map[key], expected), f"INSERTED_ROW_VALUE_MISMATCH:{label}:{key}")
    return {
        "INSERTED_ROW_N": len(inserted),
        "DELETED_ROW_N": len(deleted),
        "MODIFIED_EXISTING_ROW_N": len(modified),
        "INSERTED_KEYS": [canonical_key_text(key) for key in sorted(inserted)],
    }


def candidate_dataset_hash(records: list[dict[str, Any]]) -> str:
    payload = [
        {"relative_path": row["relative_path"], "candidate_sha256": row["candidate_sha256"]}
        for row in sorted(records, key=lambda item: item["relative_path"])
    ]
    return sha256_bytes(canonical_json_bytes(payload))


def candidate_manifest_hash(records: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(sorted(records, key=lambda item: item["relative_path"])))


def _candidate_file_records(
    *,
    data_root: Path,
    candidate_dir: Path,
    input_manifest: dict[str, Any],
    authority: dict[str, Any],
    read_and_build: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paths = partition_path_map(input_manifest)
    facts = authority_fact_map(authority)
    records: list[dict[str, Any]] = []
    diff_totals = {"INSERTED_ROW_N": 0, "DELETED_ROW_N": 0, "MODIFIED_EXISTING_ROW_N": 0}
    for trade_date in TARGET_DATES:
        relative = paths.get(trade_date)
        _require(relative is not None, f"TARGET_PARTITION_MISSING:{trade_date}")
        source_path = safe_join(data_root, relative, "CANONICAL_READ")
        candidate_path = safe_join(candidate_dir, relative, "CANDIDATE")
        source = pl.read_parquet(source_path)
        expected_key = (TARGET_SYMBOL, trade_date)
        expected = expected_authority_row(facts[expected_key], source.schema)
        if read_and_build:
            # The caller writes the frame after all source preconditions have
            # been checked.  This branch is kept for a single source of truth
            # in the frame/diff construction.
            candidate = build_candidate_frame(source, [facts[expected_key]])
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_parquet(candidate_path)
        _require(candidate_path.is_file() and not candidate_path.is_symlink(), f"CANDIDATE_FILE_MISSING:{relative}")
        written = pl.read_parquet(candidate_path)
        diff = exact_insert_diff(source, written, {expected_key: expected}, relative)
        for field in diff_totals:
            diff_totals[field] += int(diff[field])
        records.append(
            {
                "relative_path": relative,
                "source_sha256": sha256_file(source_path),
                "source_file_size": source_path.stat().st_size,
                "candidate_sha256": sha256_file(candidate_path),
                "candidate_file_size": candidate_path.stat().st_size,
                "source_row_n": source.height,
                "candidate_row_n": written.height,
                "inserted_key_n": 1,
                "inserted_keys": [canonical_key_text(expected_key)],
                "row_count_delta": 1,
                "schema_delta": 0,
            }
        )
    records.sort(key=lambda item: item["relative_path"])
    _require(len(records) == 2, "CANDIDATE_AFFECTED_FILE_N_MISMATCH")
    _require(diff_totals == {"INSERTED_ROW_N": 2, "DELETED_ROW_N": 0, "MODIFIED_EXISTING_ROW_N": 0}, "CANDIDATE_EXACT_DIFF_TOTAL_MISMATCH")
    return records, diff_totals


def audit_candidate(
    *,
    repo_root: Path,
    data_root: Path,
    stage_root: Path,
    input_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_isolated_stage_root(data_root, stage_root)
    live = input_manifest or require_current_input(data_root, repo_root)
    authority = load_authority_for_stage(data_root=data_root, stage_root=stage_root, input_manifest=live)
    candidate_dir = stage_root / CANDIDATE_DIR_NAME
    manifest_path = candidate_dir / CANDIDATE_MANIFEST_NAME
    validation_path = candidate_dir / CANDIDATE_VALIDATION_NAME
    _require(candidate_dir.is_dir() and not candidate_dir.is_symlink(), "CANDIDATE_DIR_MISSING")
    _require(manifest_path.is_file() and not manifest_path.is_symlink(), "CANDIDATE_MANIFEST_MISSING")
    manifest = load_json(manifest_path)
    records = manifest.get("FILES") if isinstance(manifest, dict) else None
    _require(isinstance(records, list) and len(records) == 2, "CANDIDATE_MANIFEST_FILE_N_MISMATCH")
    _require(records == sorted(records, key=lambda item: item["relative_path"]), "CANDIDATE_MANIFEST_NOT_SORTED")
    expected_paths = {
        partition_path_map(live).get(trade_date) for trade_date in TARGET_DATES
    }
    _require(None not in expected_paths, "TARGET_PARTITION_MISSING")
    actual_paths = {str(item["relative_path"]) for item in records}
    _require(actual_paths == expected_paths, "CANDIDATE_FILE_SET_MISMATCH")
    checked_records, diff_totals = _candidate_file_records(
        data_root=data_root,
        candidate_dir=candidate_dir,
        input_manifest=live,
        authority=authority,
        read_and_build=False,
    )
    _require(checked_records == records, "CANDIDATE_MANIFEST_RECORD_MISMATCH")
    _require(candidate_dataset_hash(records) == manifest.get("CANDIDATE_DATASET_HASH"), "CANDIDATE_DATASET_HASH_MISMATCH")
    _require(candidate_manifest_hash(records) == manifest.get("AFFECTED_FILE_MANIFEST_HASH"), "CANDIDATE_FILE_MANIFEST_HASH_MISMATCH")
    if validation_path.exists():
        existing_validation = load_json(validation_path)
        _require(existing_validation.get("CANDIDATE_DATASET_HASH") == candidate_dataset_hash(records), "CANDIDATE_VALIDATION_HASH_MISMATCH")
    return {
        "REPORT": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "INPUT_FILE_N": live["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": live["INPUT_MANIFEST_HASH"],
        "CANDIDATE_DATASET_HASH": candidate_dataset_hash(records),
        "AFFECTED_FILE_MANIFEST_HASH": candidate_manifest_hash(records),
        "AFFECTED_FILE_N": 2,
        "INSERTED_ROW_N": diff_totals["INSERTED_ROW_N"],
        "DELETED_ROW_N": diff_totals["DELETED_ROW_N"],
        "MODIFIED_EXISTING_ROW_N": diff_totals["MODIFIED_EXISTING_ROW_N"],
        "INSERTED_KEYS": [canonical_key_text(key) for key in sorted(TARGET_KEYS)],
        "SCHEMA_DELTA": 0,
        "TARGET_KEY_N": 2,
        "NETWORK_PROVIDER_DATA_FETCH": "NO_IN_CANDIDATE_PHASE",
        "TDX_REFETCH_EXECUTED": False,
        "BAOSTOCK_EXECUTED": False,
        "CANONICAL_WRITE_EXECUTED": False,
        "CANONICAL_BYTES_MUTATED": False,
        "300546_MISSING_DAYS_MUTATED": False,
        "R4A9_CHECKPOINT_MUTATED": False,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
        "FILES": records,
    }


def candidate_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# R3 300546 MISSING DAYS CANDIDATE V01",
            "",
            f"AUTHOR_STATUS: `{report['AUTHOR_STATUS']}`",
            "",
            f"- INPUT_FILE_N: {report['INPUT_FILE_N']}",
            f"- INPUT_MANIFEST_HASH: `{report['INPUT_MANIFEST_HASH']}`",
            f"- AFFECTED_FILE_N: {report['AFFECTED_FILE_N']}",
            f"- CANDIDATE_DATASET_HASH: `{report['CANDIDATE_DATASET_HASH']}`",
            f"- AFFECTED_FILE_MANIFEST_HASH: `{report['AFFECTED_FILE_MANIFEST_HASH']}`",
            f"- INSERTED_ROW_N: {report['INSERTED_ROW_N']}",
            f"- DELETED_ROW_N: {report['DELETED_ROW_N']}",
            f"- MODIFIED_EXISTING_ROW_N: {report['MODIFIED_EXISTING_ROW_N']}",
            f"- INSERTED_KEYS: `{json.dumps(report['INSERTED_KEYS'])}`",
            "- SCHEMA_DELTA: 0",
            "",
            "Candidate output is isolated under the task-owned staging root; "
            "canonical bytes were not written in this phase.",
            "",
        ]
    )


def run_candidate(*, repo_root: Path, data_root: Path, stage_root: Path) -> dict[str, Any]:
    require_isolated_stage_root(data_root, stage_root)
    require_current_head(repo_root)
    input_manifest = require_current_input(data_root, repo_root)
    precondition = require_target_keys_absent(data_root, input_manifest)
    authority = load_authority_for_stage(data_root=data_root, stage_root=stage_root, input_manifest=input_manifest)
    prior_volume = verify_prior_volume_repair(
        data_root=data_root, input_manifest=input_manifest, repo_root=repo_root
    )
    candidate_dir = stage_root / CANDIDATE_DIR_NAME
    temporary_dir = stage_root / ".candidate.in_progress"
    _require(not candidate_dir.exists() and not candidate_dir.is_symlink(), "CANDIDATE_ALREADY_EXISTS")
    _require(not temporary_dir.exists() and not temporary_dir.is_symlink(), "CANDIDATE_IN_PROGRESS_EXISTS")
    # Read all source frames and construct candidates only after the absence,
    # schema, prior-volume, and authority gates have passed.
    paths = partition_path_map(input_manifest)
    facts = authority_fact_map(authority)
    built_frames: list[tuple[str, pl.DataFrame, pl.DataFrame, str]] = []
    for trade_date in TARGET_DATES:
        relative = paths.get(trade_date)
        _require(relative is not None, f"TARGET_PARTITION_MISSING:{trade_date}")
        source_path = safe_join(data_root, relative, "CANONICAL_READ")
        source = pl.read_parquet(source_path)
        source_map = _frame_key_map(source, f"SOURCE:{relative}")
        _require((TARGET_SYMBOL, trade_date) not in source_map, f"TARGET_KEY_ALREADY_EXISTS:{trade_date}")
        candidate_frame = build_candidate_frame(source, [facts[(TARGET_SYMBOL, trade_date)]])
        expected = expected_authority_row(facts[(TARGET_SYMBOL, trade_date)], source.schema)
        exact_insert_diff(source, candidate_frame, {(TARGET_SYMBOL, trade_date): expected}, relative)
        built_frames.append((relative, source, candidate_frame, trade_date))
    input_post = build_input_file_manifest(data_root)
    require_manifest_equal(input_manifest, input_post, "INPUT_DRIFT_DURING_CANDIDATE_BUILD")
    temporary_dir.mkdir(parents=True, exist_ok=False)
    try:
        for relative, _source, frame, _trade_date in built_frames:
            target = temporary_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(target)
        records, diff_totals = _candidate_file_records(
            data_root=data_root,
            candidate_dir=temporary_dir,
            input_manifest=input_manifest,
            authority=authority,
            read_and_build=False,
        )
        manifest = {
            "REPORT": TASK,
            "EXECUTION_BASE_HEAD": EXECUTION_BASE_HEAD,
            "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
            "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
            "TARGET_SYMBOL": TARGET_SYMBOL,
            "TARGET_DATES": list(TARGET_DATES),
            "TARGET_KEY_N": 2,
            "INSERTED_ROW_N": diff_totals["INSERTED_ROW_N"],
            "DELETED_ROW_N": diff_totals["DELETED_ROW_N"],
            "MODIFIED_EXISTING_ROW_N": diff_totals["MODIFIED_EXISTING_ROW_N"],
            "SCHEMA_DELTA": 0,
            "CANONICAL_WRITE_EXECUTED": False,
            "CANONICAL_BYTES_MUTATED": False,
            "FILES": records,
        }
        manifest["AFFECTED_FILE_MANIFEST_HASH"] = candidate_manifest_hash(records)
        manifest["CANDIDATE_DATASET_HASH"] = candidate_dataset_hash(records)
        write_json_atomic(temporary_dir / CANDIDATE_MANIFEST_NAME, manifest)
        # Audit the in-progress directory directly before publication.  This
        # is the candidate gate; no canonical path is opened for writing.
        validation = audit_candidate_dir(
            repo_root=repo_root,
            data_root=data_root,
            stage_root=stage_root,
            candidate_dir=temporary_dir,
            input_manifest=input_manifest,
        )
        validation["PRECONDITION"] = precondition
        validation["PRIOR_VOLUME_REPAIR"] = prior_volume
        write_json_atomic(temporary_dir / CANDIDATE_VALIDATION_NAME, validation)
        os.replace(temporary_dir, candidate_dir)
    except Exception:
        raise
    report = audit_candidate(repo_root=repo_root, data_root=data_root, stage_root=stage_root)
    write_new_or_same(repo_root / "reports/implementation" / CANDIDATE_REPORT_NAME, report)
    write_text_new_or_same(
        repo_root / "reports/implementation" / CANDIDATE_REPORT_MD_NAME,
        candidate_markdown(report),
    )
    return {"CANDIDATE": report, "PRECONDITION": precondition, "PRIOR_VOLUME_REPAIR": prior_volume, "AUTHORITY": authority}


def audit_candidate_dir(
    *,
    repo_root: Path,
    data_root: Path,
    stage_root: Path,
    candidate_dir: Path,
    input_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Audit an in-progress or published candidate without writing canonical."""
    require_isolated_stage_root(data_root, stage_root)
    authority = load_authority_for_stage(data_root=data_root, stage_root=stage_root, input_manifest=input_manifest)
    manifest_path = candidate_dir / CANDIDATE_MANIFEST_NAME
    _require(manifest_path.is_file() and not manifest_path.is_symlink(), "CANDIDATE_MANIFEST_MISSING")
    manifest = load_json(manifest_path)
    records = manifest.get("FILES")
    _require(isinstance(records, list) and len(records) == 2, "CANDIDATE_MANIFEST_FILE_N_MISMATCH")
    checked_records, diff_totals = _candidate_file_records(
        data_root=data_root,
        candidate_dir=candidate_dir,
        input_manifest=input_manifest,
        authority=authority,
        read_and_build=False,
    )
    _require(checked_records == records, "CANDIDATE_MANIFEST_RECORD_MISMATCH")
    _require(candidate_manifest_hash(records) == manifest.get("AFFECTED_FILE_MANIFEST_HASH"), "CANDIDATE_FILE_MANIFEST_HASH_MISMATCH")
    _require(candidate_dataset_hash(records) == manifest.get("CANDIDATE_DATASET_HASH"), "CANDIDATE_DATASET_HASH_MISMATCH")
    return {
        "REPORT": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
        "CANDIDATE_DATASET_HASH": candidate_dataset_hash(records),
        "AFFECTED_FILE_MANIFEST_HASH": candidate_manifest_hash(records),
        "AFFECTED_FILE_N": 2,
        "INSERTED_ROW_N": diff_totals["INSERTED_ROW_N"],
        "DELETED_ROW_N": diff_totals["DELETED_ROW_N"],
        "MODIFIED_EXISTING_ROW_N": diff_totals["MODIFIED_EXISTING_ROW_N"],
        "INSERTED_KEYS": [canonical_key_text(key) for key in sorted(TARGET_KEYS)],
        "SCHEMA_DELTA": 0,
        "FILES": records,
    }


def build_expected_post_manifest(
    *, pre: dict[str, Any], candidate_records: list[dict[str, Any]]
) -> dict[str, Any]:
    by_path = {row["relative_path"]: row for row in candidate_records}
    _require(len(by_path) == 2, "EXPECTED_POST_AFFECTED_FILE_N_MISMATCH")
    rows: list[dict[str, Any]] = []
    for item in pre["FILES"]:
        relative = item["relative_path"]
        if relative in by_path:
            record = by_path[relative]
            rows.append(
                {
                    "relative_path": relative,
                    "file_size": record["candidate_file_size"],
                    "sha256": record["candidate_sha256"],
                }
            )
        else:
            rows.append(dict(item))
    ordered = sorted(rows, key=lambda row: row["relative_path"])
    return {
        "INPUT_FILE_N": len(ordered),
        "INPUT_MANIFEST_HASH": sha256_bytes(manifest_byte_form(ordered)),
        "CANONICAL_SERIALIZATION": (
            "json.dumps(rows, ensure_ascii=True, sort_keys=True, "
            "separators=(',', ':')) sorted by relative_path"
        ),
        "FILES": ordered,
    }


def require_candidate_immutable(
    *, candidate_dir: Path, records: list[dict[str, Any]], before: dict[str, str]
) -> None:
    after: dict[str, str] = {}
    for record in records:
        relative = str(record["relative_path"])
        path = safe_join(candidate_dir, relative, "CANDIDATE")
        _require(path.is_file() and not path.is_symlink(), f"CANDIDATE_FILE_MISSING:{relative}")
        after[relative] = sha256_file(path)
        _require(after[relative] == record["candidate_sha256"], f"CANDIDATE_SHA_MISMATCH:{relative}")
    _require(after == before, "CANDIDATE_MUTATED_DURING_PROMOTION")


def ensure_repo_promotion_outputs_absent(repo_root: Path) -> None:
    names = (
        EXPECTED_POST_REPORT_NAME,
        OBSERVED_POST_REPORT_NAME,
        PROMOTION_RECEIPT_NAME,
        FINAL_REPORT_NAME,
        FINAL_REPORT_MD_NAME,
    )
    for name in names:
        path = repo_root / "reports/implementation" / name
        _require(not path.exists() and not path.is_symlink(), f"PROMOTION_OUTPUT_ALREADY_EXISTS:{name}")


def copy_file_verified(source: Path, destination: Path, expected_sha256: str, suffix: str) -> int:
    _require(source.is_file() and not source.is_symlink(), f"COPY_SOURCE_INVALID:{source}")
    _require(not destination.exists() and not destination.is_symlink(), f"COPY_DESTINATION_EXISTS:{destination}")
    temporary = destination.with_name(f".{destination.name}.{suffix}.tmp")
    _require(not temporary.exists() and not temporary.is_symlink(), f"COPY_TEMP_EXISTS:{temporary}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as source_handle, temporary.open("wb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=1 << 20)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        _require(sha256_file(temporary) == expected_sha256, f"COPY_SHA_MISMATCH:{source}")
        size = temporary.stat().st_size
        os.replace(temporary, destination)
        _require(sha256_file(destination) == expected_sha256, f"COPY_POST_SHA_MISMATCH:{destination}")
        return size
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_transaction_state(
    transaction_root: Path,
    state: str,
    *,
    promoted_file_n: int,
    promotion_complete: bool,
) -> None:
    write_json_atomic(
        transaction_root / TRANSACTION_STATE_NAME,
        {
            "REPORT": TASK,
            "EXECUTION_BASE_HEAD": EXECUTION_BASE_HEAD,
            "TRANSACTION_STATE": state,
            "PROMOTED_FILE_N": promoted_file_n,
            "PROMOTION_COMPLETE": promotion_complete,
        },
    )


def write_journal(transaction_root: Path, name: str, state: str, files: list[str]) -> None:
    write_json_atomic(
        transaction_root / name,
        {"REPORT": TASK, "TRANSACTION_STATE": state, "FILES": sorted(files)},
    )


def validate_promoted_semantics(
    *,
    data_root: Path,
    transaction_root: Path,
    records: list[dict[str, Any]],
    authority: dict[str, Any],
) -> dict[str, Any]:
    facts = authority_fact_map(authority)
    inserted = 0
    deleted = 0
    modified = 0
    for record in records:
        relative = record["relative_path"]
        backup = safe_join(transaction_root, record["rollback_backup_relative_path"], "ROLLBACK_BACKUP")
        target = safe_join(data_root, relative, "CANONICAL_TARGET")
        source = pl.read_parquet(backup)
        post = pl.read_parquet(target)
        trade_date = relative.split("trade_date=", 1)[1].split("/", 1)[0]
        key = (TARGET_SYMBOL, trade_date)
        expected = expected_authority_row(facts[key], source.schema)
        diff = exact_insert_diff(source, post, {key: expected}, relative)
        inserted += diff["INSERTED_ROW_N"]
        deleted += diff["DELETED_ROW_N"]
        modified += diff["MODIFIED_EXISTING_ROW_N"]
    _require((inserted, deleted, modified) == (2, 0, 0), "POST_EXACT_DIFF_FAILED")
    return {
        "INSERTED_ROW_N": inserted,
        "DELETED_ROW_N": deleted,
        "MODIFIED_EXISTING_ROW_N": modified,
        "CHANGED_CELL_N": 0,
        "CHANGED_COLUMNS": [],
    }


def rollback_files(
    *,
    data_root: Path,
    transaction_root: Path,
    records: list[dict[str, Any]],
    promoted: list[str],
) -> None:
    write_transaction_state(
        transaction_root,
        "ROLLING_BACK",
        promoted_file_n=len(promoted),
        promotion_complete=False,
    )
    promoted_set = set(promoted)
    for record in reversed(records):
        relative = record["relative_path"]
        if relative not in promoted_set:
            continue
        target = safe_join(data_root, relative, "CANONICAL_TARGET")
        backup = safe_join(transaction_root, record["rollback_backup_relative_path"], "ROLLBACK_BACKUP")
        temporary = target.with_name(f".{target.name}.r3_missing_days_rollback.tmp")
        _require(backup.is_file() and not backup.is_symlink(), f"ROLLBACK_BACKUP_MISSING:{relative}")
        _require(sha256_file(backup) == record["source_sha256"], f"ROLLBACK_BACKUP_SHA_MISMATCH:{relative}")
        _require(not temporary.exists() and not temporary.is_symlink(), f"ROLLBACK_TEMP_EXISTS:{relative}")
        copy_file_verified(backup, temporary, record["source_sha256"], "unused")
        # copy_file_verified requires a non-existing destination and replaces
        # the temporary path itself; the following replace restores target.
        os.replace(temporary, target)
        _require(sha256_file(target) == record["source_sha256"], f"ROLLBACK_POST_SHA_MISMATCH:{relative}")
        record["rollback_status"] = "RESTORED"
    write_journal(transaction_root, "rolled_back_files_journal.json", "ROLLED_BACK", promoted)
    write_transaction_state(
        transaction_root,
        "ROLLED_BACK",
        promoted_file_n=len(promoted),
        promotion_complete=False,
    )


def promote_files(
    *,
    data_root: Path,
    candidate_dir: Path,
    transaction_root: Path,
    records: list[dict[str, Any]],
    promoted: list[str] | None = None,
    fault_injector: Callable[[int, dict[str, Any]], None] | None = None,
) -> list[str]:
    promoted = promoted if promoted is not None else []
    write_transaction_state(transaction_root, "COMMITTING", promoted_file_n=0, promotion_complete=False)
    for index, record in enumerate(records):
        relative = record["relative_path"]
        target = safe_join(data_root, relative, "CANONICAL_TARGET")
        candidate_path = safe_join(candidate_dir, relative, "CANDIDATE")
        _require(target.is_file() and not target.is_symlink(), f"CANONICAL_TARGET_MISSING:{relative}")
        _require(sha256_file(target) == record["source_sha256"], f"INPUT_DRIFT_BEFORE_REPLACE:{relative}")
        _require(sha256_file(candidate_path) == record["candidate_sha256"], f"CANDIDATE_SHA_MISMATCH:{relative}")
        temporary = target.with_name(f".{target.name}.r3_missing_days_promote.tmp")
        _require(not temporary.exists() and not temporary.is_symlink(), f"PROMOTION_TEMP_EXISTS:{relative}")
        copy_file_verified(candidate_path, temporary, record["candidate_sha256"], "unused")
        os.replace(temporary, target)
        _require(sha256_file(target) == record["candidate_sha256"], f"PROMOTION_POST_SHA_MISMATCH:{relative}")
        record["promotion_status"] = "PROMOTED"
        promoted.append(relative)
        write_transaction_state(
            transaction_root,
            "COMMITTING",
            promoted_file_n=len(promoted),
            promotion_complete=False,
        )
        write_journal(transaction_root, "promoted_files_journal.json", "COMMITTING", promoted)
        if fault_injector is not None:
            fault_injector(index, record)
    return promoted


def build_promotion_records(
    *,
    data_root: Path,
    candidate_dir: Path,
    transaction_root: Path,
    pre: dict[str, Any],
    candidate_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pre_by_path = {row["relative_path"]: row for row in pre["FILES"]}
    result: list[dict[str, Any]] = []
    for candidate in sorted(candidate_records, key=lambda item: item["relative_path"]):
        relative = candidate["relative_path"]
        _require(relative in pre_by_path, f"PROMOTION_PATH_NOT_IN_PRE_MANIFEST:{relative}")
        source = safe_join(data_root, relative, "CANONICAL_SOURCE")
        candidate_path = safe_join(candidate_dir, relative, "CANDIDATE")
        _require(sha256_file(source) == pre_by_path[relative]["sha256"], f"SOURCE_SHA_MISMATCH:{relative}")
        _require(sha256_file(candidate_path) == candidate["candidate_sha256"], f"CANDIDATE_SHA_MISMATCH:{relative}")
        backup_relative = str(Path("rollback_backup") / relative)
        result.append(
            {
                "relative_path": relative,
                "source_path": str(source),
                "source_sha256": pre_by_path[relative]["sha256"],
                "source_file_size": pre_by_path[relative]["file_size"],
                "candidate_sha256": candidate["candidate_sha256"],
                "candidate_file_size": candidate["candidate_file_size"],
                "rollback_backup_relative_path": backup_relative,
                "rollback_backup_sha256": None,
                "rollback_backup_file_size": None,
                "rollback_backup_verified": False,
                "promotion_status": "PLANNED",
                "rollback_status": "AVAILABLE_AFTER_PREPARE",
            }
        )
    _require(len(result) == 2, "PROMOTION_FILE_N_MISMATCH")
    return result


def prepare_backups(transaction_root: Path, records: list[dict[str, Any]]) -> None:
    backed_up: list[str] = []
    for record in records:
        relative = record["relative_path"]
        backup = safe_join(transaction_root, record["rollback_backup_relative_path"], "ROLLBACK_BACKUP")
        size = copy_file_verified(Path(record["source_path"]), backup, record["source_sha256"], "backup")
        record["rollback_backup_sha256"] = sha256_file(backup)
        record["rollback_backup_file_size"] = size
        record["rollback_backup_verified"] = True
        record["rollback_status"] = "AVAILABLE"
        backed_up.append(relative)
    _require(len(backed_up) == len(records), "ROLLBACK_BACKUP_INCOMPLETE")


def promotion_receipt(
    *,
    pre: dict[str, Any],
    expected: dict[str, Any],
    post: dict[str, Any],
    records: list[dict[str, Any]],
    semantic: dict[str, Any],
    prior_volume: dict[str, Any],
    authority: dict[str, Any],
    candidate_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "REPORT": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "EXECUTION_BASE_HEAD": EXECUTION_BASE_HEAD,
        "PRE_INPUT_FILE_N": pre["INPUT_FILE_N"],
        "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
        "EXPECTED_POST_INPUT_FILE_N": expected["INPUT_FILE_N"],
        "EXPECTED_POST_INPUT_MANIFEST_HASH": expected["INPUT_MANIFEST_HASH"],
        "POST_INPUT_FILE_N": post["INPUT_FILE_N"],
        "POST_INPUT_MANIFEST_HASH": post["INPUT_MANIFEST_HASH"],
        "TARGET_SYMBOL": TARGET_SYMBOL,
        "TARGET_DATES": list(TARGET_DATES),
        "TARGET_KEY_N": 2,
        "INSERTED_ROW_N": semantic["INSERTED_ROW_N"],
        "DELETED_ROW_N": semantic["DELETED_ROW_N"],
        "MODIFIED_EXISTING_ROW_N": semantic["MODIFIED_EXISTING_ROW_N"],
        "CHANGED_CELL_N": semantic["CHANGED_CELL_N"],
        "CHANGED_COLUMNS": semantic["CHANGED_COLUMNS"],
        "AFFECTED_FILE_N": 2,
        "PROMOTED_FILE_N": len(records),
        "UNAFFECTED_FILE_N": CURRENT_INPUT_FILE_N - len(records),
        "ROLLBACK_BACKUP_FILE_N": len(records),
        "ROLLBACK_BACKUP_VERIFIED": all(row["rollback_backup_verified"] for row in records),
        "TRANSACTION_STATE": "COMMITTED",
        "PROMOTION_COMPLETE": True,
        "AUTHORITY_RECEIPT_PAYLOAD_SHA256": authority["AUTHORITY_PAYLOAD_SHA256"],
        "CANDIDATE_DATASET_HASH": candidate_report["CANDIDATE_DATASET_HASH"],
        "AFFECTED_FILE_MANIFEST_HASH": candidate_report["AFFECTED_FILE_MANIFEST_HASH"],
        "CANDIDATE_IMMUTABLE": True,
        "CHANGED_KEY_MANIFEST_HASH": CHANGED_MANIFEST_HASH,
        **prior_volume,
        "BAOSTOCK_BOUNDED_REQUEST_N": 1,
        "NETWORK_PROVIDER_DATA_FETCH": "BAOSTOCK_BOUNDED_ONLY",
        "TDX_REFETCH_EXECUTED": False,
        "BAOSTOCK_EXECUTED": True,
        "R3_DATA_REBUILD_EXECUTED": False,
        "R3_MARKET_DATA_WRITE": "BOUNDED_TWO_KEY_CANONICAL_INSERT_ONLY",
        "CANONICAL_WRITE_EXECUTED": True,
        "CANONICAL_BYTES_MUTATED": True,
        "300546_MISSING_DAYS_MUTATED": True,
        "R4A9_CHECKPOINT_MUTATED": False,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
        "FILES": records,
    }


def final_markdown(receipt: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# R3 300546 MISSING TRADING DAYS REPAIR V01",
            "",
            f"AUTHOR_STATUS: `{receipt['AUTHOR_STATUS']}`",
            "",
            f"- BASE_HEAD: `{receipt['BASE_HEAD']}`",
            f"- EXECUTION_BASE_HEAD: `{receipt['EXECUTION_BASE_HEAD']}`",
            f"- PRE_INPUT_MANIFEST_HASH: `{receipt['PRE_INPUT_MANIFEST_HASH']}`",
            f"- EXPECTED_POST_INPUT_MANIFEST_HASH: `{receipt['EXPECTED_POST_INPUT_MANIFEST_HASH']}`",
            f"- POST_INPUT_MANIFEST_HASH: `{receipt['POST_INPUT_MANIFEST_HASH']}`",
            "",
            "## Exact diff",
            "",
            f"- INSERTED_ROW_N: {receipt['INSERTED_ROW_N']}",
            f"- DELETED_ROW_N: {receipt['DELETED_ROW_N']}",
            f"- MODIFIED_EXISTING_ROW_N: {receipt['MODIFIED_EXISTING_ROW_N']}",
            f"- CHANGED_CELL_N: {receipt['CHANGED_CELL_N']}",
            f"- CHANGED_COLUMNS: `{json.dumps(receipt['CHANGED_COLUMNS'])}`",
            f"- PROMOTED_FILE_N: {receipt['PROMOTED_FILE_N']}",
            "",
            "## Safety",
            "",
            "- BAOSTOCK_BOUNDED_REQUEST_N: 1",
            "- TDX_REFETCH_EXECUTED: false",
            "- CANONICAL_WRITE_EXECUTED: true (exactly two missing keys)",
            "- CANONICAL_BYTES_MUTATED: true",
            "- 300546_MISSING_DAYS_MUTATED: true",
            "- R4A9_CHECKPOINT_MUTATED: false",
            "- R4A9_RESUME_AUTHORIZED: false",
            "- PRECLOSE_COMPLETE: false",
            "- PRODUCTION: false",
            "- FORWARD: false",
            "- TRADEPLAN: false",
            "",
        ]
    )


def run_promotion(
    *,
    repo_root: Path,
    data_root: Path,
    stage_root: Path,
    promotion_root: Path,
    fault_injector: Callable[[int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    require_isolated_stage_root(data_root, stage_root)
    require_promotion_root(data_root, promotion_root)
    require_current_head(repo_root)
    _require(not promotion_root.exists() and not promotion_root.is_symlink(), "RECOVERY_REQUIRED:TRANSACTION_ROOT_EXISTS")
    ensure_repo_promotion_outputs_absent(repo_root)
    pre = require_current_input(data_root, repo_root)
    precondition = require_target_keys_absent(data_root, pre)
    prior_volume = verify_prior_volume_repair(data_root=data_root, input_manifest=pre, repo_root=repo_root)
    candidate_gate = audit_candidate(repo_root=repo_root, data_root=data_root, stage_root=stage_root, input_manifest=pre)
    authority = load_authority_for_stage(data_root=data_root, stage_root=stage_root, input_manifest=pre)
    candidate_dir = stage_root / CANDIDATE_DIR_NAME
    candidate_manifest = load_json(candidate_dir / CANDIDATE_MANIFEST_NAME)
    candidate_records = candidate_manifest["FILES"]
    expected = build_expected_post_manifest(pre=pre, candidate_records=candidate_records)
    plan_records = build_promotion_records(
        data_root=data_root,
        candidate_dir=candidate_dir,
        transaction_root=promotion_root,
        pre=pre,
        candidate_records=candidate_records,
    )
    promotion_root.mkdir(parents=True, exist_ok=False)
    promoted: list[str] = []
    try:
        write_transaction_state(promotion_root, "PREPARING", promoted_file_n=0, promotion_complete=False)
        write_json_atomic(
            promotion_root / TRANSACTION_PLAN_NAME,
            {
                "REPORT": TASK,
                "EXECUTION_BASE_HEAD": EXECUTION_BASE_HEAD,
                "TRANSACTION_STATE": "PREPARING",
                "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
                "EXPECTED_POST_INPUT_MANIFEST_HASH": expected["INPUT_MANIFEST_HASH"],
                "FILES": plan_records,
            },
        )
        prepare_backups(promotion_root, plan_records)
        write_journal(
            promotion_root,
            "backed_up_files_journal.json",
            "PREPARED",
            [record["relative_path"] for record in plan_records],
        )
        write_json_atomic(
            promotion_root / TRANSACTION_PLAN_NAME,
            {
                "REPORT": TASK,
                "EXECUTION_BASE_HEAD": EXECUTION_BASE_HEAD,
                "TRANSACTION_STATE": "PREPARED",
                "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
                "EXPECTED_POST_INPUT_MANIFEST_HASH": expected["INPUT_MANIFEST_HASH"],
                "FILES": plan_records,
            },
        )
        pre_commit = require_current_input(data_root, repo_root)
        require_manifest_equal(pre, pre_commit, "INPUT_DRIFT_BEFORE_PROMOTION")
        require_target_keys_absent(data_root, pre_commit)
        promoted = promote_files(
            data_root=data_root,
            candidate_dir=candidate_dir,
            transaction_root=promotion_root,
            records=plan_records,
            promoted=promoted,
            fault_injector=fault_injector,
        )
        post = build_input_file_manifest(data_root)
        require_manifest_equal(expected, post, "POST_INPUT_MANIFEST")
        semantic = validate_promoted_semantics(
            data_root=data_root,
            transaction_root=promotion_root,
            records=plan_records,
            authority=authority,
        )
        require_target_keys_exactly_once(data_root, post)
        prior_after = verify_prior_volume_repair(data_root=data_root, input_manifest=post, repo_root=repo_root)
        _require(prior_after == prior_volume, "PRIOR_VOLUME_REPAIR_CHANGED_DURING_PROMOTION")
        candidate_before = {
            str(record["relative_path"]): str(record["candidate_sha256"])
            for record in candidate_gate["FILES"]
        }
        require_candidate_immutable(
            candidate_dir=candidate_dir,
            records=candidate_gate["FILES"],
            before=candidate_before,
        )
        receipt = promotion_receipt(
            pre=pre,
            expected=expected,
            post=post,
            records=plan_records,
            semantic=semantic,
            prior_volume=prior_volume,
            authority=authority,
            candidate_report=candidate_gate,
        )
        write_json_atomic(promotion_root / TRANSACTION_EXPECTED_NAME, expected)
        write_json_atomic(promotion_root / TRANSACTION_OBSERVED_NAME, post)
        write_json_atomic(promotion_root / TRANSACTION_RECEIPT_NAME, receipt)
        write_new_or_same(repo_root / "reports/implementation" / EXPECTED_POST_REPORT_NAME, expected)
        write_new_or_same(repo_root / "reports/implementation" / OBSERVED_POST_REPORT_NAME, post)
        write_new_or_same(repo_root / "reports/implementation" / PROMOTION_RECEIPT_NAME, receipt)
        write_new_or_same(repo_root / "reports/implementation" / FINAL_REPORT_NAME, receipt)
        write_text_new_or_same(
            repo_root / "reports/implementation" / FINAL_REPORT_MD_NAME,
            final_markdown(receipt),
        )
        write_json_atomic(
            promotion_root / TRANSACTION_PLAN_NAME,
            {
                "REPORT": TASK,
                "EXECUTION_BASE_HEAD": EXECUTION_BASE_HEAD,
                "TRANSACTION_STATE": "COMMITTED",
                "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
                "EXPECTED_POST_INPUT_MANIFEST_HASH": expected["INPUT_MANIFEST_HASH"],
                "FILES": plan_records,
            },
        )
        write_transaction_state(
            promotion_root,
            "COMMITTED",
            promoted_file_n=len(promoted),
            promotion_complete=True,
        )
        return {
            "RECEIPT": receipt,
            "PRECONDITION": precondition,
            "CANDIDATE_GATE": candidate_gate,
            "PRIOR_VOLUME_REPAIR": prior_volume,
        }
    except Exception as exc:
        if promoted:
            try:
                rollback_files(
                    data_root=data_root,
                    transaction_root=promotion_root,
                    records=plan_records,
                    promoted=promoted,
                )
                restored = build_input_file_manifest(data_root)
                require_manifest_equal(pre, restored, "ROLLBACK_PRE_INPUT")
            except Exception as rollback_exc:
                try:
                    write_transaction_state(
                        promotion_root,
                        "RECOVERY_REQUIRED",
                        promoted_file_n=len(promoted),
                        promotion_complete=False,
                    )
                except Exception:
                    pass
                raise RepairError(f"RECOVERY_REQUIRED:{rollback_exc}") from exc
            raise RepairError(f"PROMOTION_FAILED_ROLLED_BACK:{exc}") from exc
        try:
            write_transaction_state(
                promotion_root,
                "RECOVERY_REQUIRED",
                promoted_file_n=0,
                promotion_complete=False,
            )
        except Exception:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bounded 300546 missing-day repair")
    parser.add_argument("--phase", choices=("authority", "candidate", "promote"), required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path, default=None)
    parser.add_argument("--promotion-root", type=Path, default=None)
    parser.add_argument(
        "--fetch-baostock",
        action="store_true",
        help="authority phase only: permit the one bounded BaoStock request if no receipt exists",
    )
    args = parser.parse_args(argv)
    stage_root = args.stage_root or args.data_root / "staging" / STAGE_ROOT_NAME
    promotion_root = args.promotion_root or args.data_root / "staging" / PROMOTION_ROOT_NAME
    try:
        if args.phase == "authority":
            result = run_authority(
                repo_root=args.repo_root,
                data_root=args.data_root,
                stage_root=stage_root,
                allow_baostock_fetch=args.fetch_baostock,
            )
        elif args.phase == "candidate":
            _require(not args.fetch_baostock, "FETCH_FLAG_FORBIDDEN_IN_CANDIDATE_PHASE")
            result = run_candidate(repo_root=args.repo_root, data_root=args.data_root, stage_root=stage_root)
        else:
            _require(not args.fetch_baostock, "FETCH_FLAG_FORBIDDEN_IN_PROMOTION_PHASE")
            result = run_promotion(
                repo_root=args.repo_root,
                data_root=args.data_root,
                stage_root=stage_root,
                promotion_root=promotion_root,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except RepairError as exc:
        print(json.dumps({"STATUS": "FAIL_CLOSED", "ERROR": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
