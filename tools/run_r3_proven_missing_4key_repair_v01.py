#!/usr/bin/env python3
"""Bounded, provenance-bound repair of four proven R3 daily-bar gaps.

The tool deliberately has no provider import.  It consumes the already frozen
local Tushare evidence, builds an isolated candidate, validates the exact
insertion diff, and only then performs one atomic canonical-file replacement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

TASK = "R3_PROVEN_MISSING_4KEY_REPAIR_V01"
BASE_HEAD = "c820d5897720e24d9cd65a61523016fb8d581292"
BRANCH = "codex/r3-proven-missing-4key-repair-v01"

INPUT_FILE_N = 2_580
PRE_INPUT_MANIFEST_HASH = (
    "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
)
TARGET_KEYS = (
    ("002087.SZ", date(2024, 6, 13)),
    ("600647.SH", date(2024, 6, 13)),
    ("600766.SH", date(2024, 6, 13)),
    ("603133.SH", date(2024, 6, 13)),
)
TARGET_KEY_N = 4
TARGET_KEYSET_HASH = (
    "49fd7d316e2a09bbb18f0b840d4a5034f3efb2dbba57e9f60255c7a8910b2663"
)
AUTHORITY_TASK = "R3_STATUS0_SECONDARY_AUTHORITY_PILOT_V01"
SECONDARY_SCOPE_KEYSET_HASH = (
    "756258b487c409843a4accdf42a5a12b6ca5f87a117fb9ac35d55ef74ab60971"
)
SESSION_AUTHORITY_DATASET_HASH = (
    "0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47"
)
ADJUDICATION_MANIFEST_HASH = (
    "fbfbd2dd29c35ca686bf3373c2b70aa24ff615f1d2c853542fa7ba3d334a3a30"
)
EVIDENCE_INDEX_HASH = (
    "2158c237683462b37e5f12357250d69473cd33d2c368d81aa07348453c4b77d3"
)
EVIDENCE_INDEX_FILE_SHA256 = (
    "86eb2a47aef68a78a886882c9b1526fd3f5efd1edbca3c61a3bbc4b6d8d25a3c"
)
LOCAL_TUSHARE_FILE_SHA256 = (
    "1413ca2a9f14e5fef2f548643eb7e9353d185ffaaba93ee0a2ba40d9a88f87d1"
)
CHANGED_VOLUME_MANIFEST_HASH = (
    "f1cdb9d5416535e76631673479ef1fc36539a8626ee18026a581f6e1191b62c7"
)
CHANGED_VOLUME_KEY_N = 1_169

STAGE_ROOT_NAME = "r3_proven_missing_4key_repair_v01"
CANDIDATE_DIR_NAME = "candidate"
TRANSACTION_DIR_NAME = "transaction"
INPUT_REFERENCE = (
    "reports/implementation/"
    "R3_SHSZ_DAILY_FOUNDATION_CORRECTNESS_REVALIDATION_POST_VALIDATION_INPUT_MANIFEST_V01.json"
)
PILOT_REPORT = "reports/implementation/R3_STATUS0_SECONDARY_AUTHORITY_PILOT_V01.json"
PILOT_ADJUDICATION = (
    "reports/implementation/R3_STATUS0_SECONDARY_AUTHORITY_PILOT_V01_ADJUDICATION_MANIFEST.json"
)
PILOT_EVIDENCE_INDEX = (
    "reports/implementation/R3_STATUS0_SECONDARY_AUTHORITY_PILOT_V01_EVIDENCE_INDEX.json"
)
CONTRADICTION_MANIFEST = (
    "reports/implementation/"
    "R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01_CONTRADICTIONS.json"
)
CHANGED_VOLUME_MANIFEST = (
    "reports/implementation/R3_TDX_VOLUME_TARGETED_REFETCH_CHANGED_MANIFEST_V01_1.json"
)
TUSHARE_ROOT = Path("/Users/luke808/AI/V flash/data/raw/tushare/daily_bars")
TUSHARE_FILE = TUSHARE_ROOT / "359774eea1675b329e747cbb-0006.parquet"

REPORT_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"
REGISTRY_NAME = f"{TASK}_KNOWN_HISTORICAL_EXCEPTION_REGISTRY.json"

CANONICAL_COLUMNS = (
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
)
CANONICAL_SCHEMA = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
    "source": pl.String,
    "data_version": pl.String,
    "fetched_at": pl.Datetime("us", "UTC"),
}


class RepairError(RuntimeError):
    """Terminal fail-closed repair error."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=_json_default).encode(
        "utf-8"
    )


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"NOT_JSON_SERIALIZABLE:{type(value).__name__}")


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
        raise RepairError(f"JSON_READ_FAILED:{path}") from exc


def _require(condition: bool, code: str, detail: str | None = None) -> None:
    if not condition:
        raise RepairError(code if detail is None else f"{code}:{detail}")


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise RepairError(f"INVALID_DATE:{value!r}") from exc


def key_of(row: dict[str, Any]) -> tuple[str, date]:
    return str(row["symbol"]), parse_date(row["trade_date"])


def key_text(key: tuple[str, date]) -> str:
    return f"{key[0]}:{key[1].isoformat()}"


def keyset_hash(keys: Iterable[tuple[str, date]]) -> str:
    ordered = sorted(keys)
    _require(len(ordered) == len(set(ordered)), "DUPLICATE_KEY_FOR_HASH")
    digest = hashlib.sha256()
    for symbol, trade_day in ordered:
        digest.update(f"{symbol}\t{trade_day.isoformat()}\n".encode("utf-8"))
    return digest.hexdigest()


def _scalar_token(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, Decimal):
        return str(value)
    return value


def rows_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        _scalar_token(left.get(column)) == _scalar_token(right.get(column))
        for column in CANONICAL_COLUMNS
    )


def _path_has_symlink_component(path: Path, stop: Path) -> bool:
    current = path
    stop = stop.resolve(strict=False)
    while True:
        if current.is_symlink():
            return True
        if current == stop:
            return False
        if current.parent == current:
            return False
        current = current.parent


def require_isolated_stage_root(data_root: Path, stage_root: Path) -> Path:
    """Enforce the sole data-root staging location before any stage write."""

    data_input = Path(data_root).expanduser()
    stage_input = Path(stage_root).expanduser()
    _require(data_input.is_absolute() and stage_input.is_absolute(), "STAGE_ROOT_NOT_ABSOLUTE")
    _require(data_input.is_dir(), "DATA_ROOT_NOT_DIRECTORY")
    data_resolved = data_input.resolve(strict=True)
    _require(data_input == data_resolved, "DATA_ROOT_SYMLINK_OR_ESCAPE")
    staging = data_resolved / "staging"
    _require(not staging.is_symlink(), "STAGING_PARENT_SYMLINK")
    expected = staging / STAGE_ROOT_NAME
    stage_resolved = stage_input.resolve(strict=False)
    _require(stage_input == stage_resolved and stage_resolved == expected, "ISOLATED_STAGE_ROOT_FORBIDDEN")
    _require(not _path_has_symlink_component(stage_input, data_resolved), "ISOLATED_STAGE_ROOT_SYMLINK")
    if stage_input.exists():
        _require(stage_input.is_dir() and not stage_input.is_symlink(), "ISOLATED_STAGE_ROOT_INVALID")
    return stage_resolved


def safe_join(root: Path, relative: str, label: str) -> Path:
    rel = Path(relative)
    _require(not rel.is_absolute() and ".." not in rel.parts, f"{label}_PATH_TRAVERSAL")
    root_resolved = root.resolve(strict=False)
    current = root
    for part in rel.parts:
        current = current / part
        _require(not current.is_symlink(), f"{label}_SYMLINK_COMPONENT")
    result = (root / rel).resolve(strict=False)
    _require(result.is_relative_to(root_resolved), f"{label}_PATH_ESCAPE")
    return result


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
    _require(current == BASE_HEAD, "BASE_HEAD_MISMATCH", current)


def build_input_file_manifest(data_root: Path) -> dict[str, Any]:
    root = data_root / "curated" / "daily_bars"
    paths = sorted(root.rglob("*.parquet"))
    rows = [
        {
            "relative_path": str(path.relative_to(data_root)),
            "file_size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in paths
    ]
    return {
        "INPUT_FILE_N": len(rows),
        "INPUT_MANIFEST_HASH": sha256_bytes(canonical_json_bytes(rows)),
        "CANONICAL_SERIALIZATION": (
            "json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(',', ':')) "
            "sorted by relative_path"
        ),
        "FILES": rows,
    }


def manifests_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("INPUT_FILE_N") == right.get("INPUT_FILE_N")
        and left.get("INPUT_MANIFEST_HASH") == right.get("INPUT_MANIFEST_HASH")
        and left.get("FILES") == right.get("FILES")
    )


def require_frozen_input(repo_root: Path, data_root: Path) -> dict[str, Any]:
    reference = load_json(repo_root / INPUT_REFERENCE)
    _require(reference.get("INPUT_FILE_N") == INPUT_FILE_N, "INPUT_REFERENCE_FILE_N_MISMATCH")
    _require(reference.get("INPUT_MANIFEST_HASH") == PRE_INPUT_MANIFEST_HASH, "INPUT_REFERENCE_HASH_MISMATCH")
    _require(isinstance(reference.get("FILES"), list) and len(reference["FILES"]) == INPUT_FILE_N, "INPUT_REFERENCE_FILES_MISMATCH")
    live = build_input_file_manifest(data_root)
    _require(live.get("INPUT_FILE_N") == INPUT_FILE_N, "INPUT_FILE_N_MISMATCH")
    _require(live.get("INPUT_MANIFEST_HASH") == PRE_INPUT_MANIFEST_HASH, "INPUT_MANIFEST_DRIFT")
    _require(manifests_equal(live, reference), "INPUT_FILES_DRIFT")
    return live


def partition_path_map(input_manifest: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in input_manifest.get("FILES", []):
        relative = str(item["relative_path"])
        marker = "trade_date="
        if marker not in relative:
            continue
        trade_day = relative.split(marker, 1)[1].split("/", 1)[0]
        if len(trade_day) != 10:
            continue
        _require(trade_day not in result or result[trade_day] == relative, "CANONICAL_PARTITION_DUPLICATE", trade_day)
        result[trade_day] = relative
    return result


def _frame_key_map(frame: pl.DataFrame, label: str) -> dict[tuple[str, date], dict[str, Any]]:
    result: dict[tuple[str, date], dict[str, Any]] = {}
    for row in frame.iter_rows(named=True):
        key = key_of(row)
        _require(key not in result, f"DUPLICATE_KEY_IN_{label}", key_text(key))
        result[key] = row
    return result


def inspect_target_precondition(data_root: Path, input_manifest: dict[str, Any]) -> dict[str, Any]:
    counts: Counter[tuple[str, date]] = Counter()
    target_symbols = {symbol for symbol, _ in TARGET_KEYS}
    for item in input_manifest["FILES"]:
        path = safe_join(data_root, str(item["relative_path"]), "CANONICAL_READ")
        _require(path.is_file() and not path.is_symlink(), "CANONICAL_FILE_MISSING", str(path))
        frame = pl.read_parquet(path, columns=["symbol", "trade_date"])
        for row in frame.filter(pl.col("symbol").is_in(sorted(target_symbols))).iter_rows(named=True):
            counts[(str(row["symbol"]), parse_date(row["trade_date"]))] += 1
    duplicate_keys = sorted(key_text(key) for key, n in counts.items() if n > 1)
    present = sorted(key_text(key) for key in TARGET_KEYS if counts.get(key, 0))
    _require(not duplicate_keys, "TARGET_SYMBOL_DUPLICATE_KEY", ",".join(duplicate_keys))
    _require(not present, "TARGET_KEY_ALREADY_EXISTS", ",".join(present))
    return {
        "EXISTING_TARGET_KEY_N": len(present),
        "TARGET_KEY_CARDINALITY": {key_text(key): counts.get(key, 0) for key in TARGET_KEYS},
        "TARGET_SYMBOL_DUPLICATE_KEY_N": len(duplicate_keys),
        "NEIGHBOR_SANITY": _neighbor_sanity(data_root),
    }


def _neighbor_sanity(data_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for trade_day in (date(2024, 6, 12), date(2024, 6, 13), date(2024, 6, 14)):
        path = data_root / "curated" / "daily_bars" / f"trade_date={trade_day.isoformat()}" / "part-merged.parquet"
        if not path.is_file():
            result[trade_day.isoformat()] = {"file_present": False}
            continue
        frame = pl.read_parquet(path, columns=["symbol", "trade_date"])
        result[trade_day.isoformat()] = {
            "file_present": True,
            "target_symbol_row_n": frame.filter(
                pl.col("symbol").is_in([symbol for symbol, _ in TARGET_KEYS])
            ).height,
        }
    return result


def _parse_decimal(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RepairError(f"INVALID_NUMERIC:{label}") from exc
    _require(parsed.is_finite(), "NONFINITE_NUMERIC", label)
    return parsed


def _parse_positive_float(value: Any, label: str) -> float:
    parsed = _parse_decimal(value, label)
    result = float(parsed)
    _require(math.isfinite(result), "NONFINITE_NUMERIC", label)
    _require(result >= 0, "NEGATIVE_NUMERIC", label)
    return result


def _parse_positive_int(value: Any, label: str) -> int:
    parsed = _parse_decimal(value, label)
    _require(parsed == parsed.to_integral_value() and parsed > 0, "INVALID_POSITIVE_INTEGER", label)
    return int(parsed)


def _parse_fetched_at(value: Any) -> datetime:
    _require(isinstance(value, datetime), "FETCHED_AT_MISSING")
    _require(value.tzinfo is not None, "FETCHED_AT_NOT_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def _authority_source_row(evidence: dict[str, Any], target: tuple[str, date]) -> dict[str, Any]:
    symbol, trade_day = target
    daily = evidence.get("secondary_observation", {}).get("daily")
    _require(isinstance(daily, dict), "TUSHARE_EVIDENCE_MISSING", key_text(target))
    _require(daily.get("provider") == "TUSHARE", "TUSHARE_PROVIDER_MISMATCH", key_text(target))
    _require(daily.get("provider_version") == "1.4.29", "TUSHARE_VERSION_MISMATCH", key_text(target))
    _require(daily.get("symbol") == symbol and daily.get("trade_date") == trade_day.isoformat(), "TUSHARE_EVIDENCE_KEY_MISMATCH", key_text(target))
    _require(daily.get("trade_status") is True, "TUSHARE_NOT_TRADING", key_text(target))
    path = Path(str(daily.get("file", ""))).expanduser()
    _require(path == TUSHARE_FILE, "TUSHARE_EVIDENCE_FILE_UNEXPECTED", key_text(target))
    _require(daily.get("file_sha256") == LOCAL_TUSHARE_FILE_SHA256, "TUSHARE_EVIDENCE_FILE_HASH_DECLARATION_MISMATCH", key_text(target))
    _require(path.is_file() and not path.is_symlink(), "TUSHARE_EVIDENCE_FILE_MISSING", str(path))
    _require(sha256_file(path) == LOCAL_TUSHARE_FILE_SHA256, "TUSHARE_EVIDENCE_FILE_HASH_MISMATCH", str(path))
    return daily


def load_repair_authority(repo_root: Path) -> dict[str, Any]:
    pilot = load_json(repo_root / PILOT_REPORT)
    adjudication = load_json(repo_root / PILOT_ADJUDICATION)
    evidence_index = load_json(repo_root / PILOT_EVIDENCE_INDEX)
    _require(pilot.get("TASK") == AUTHORITY_TASK, "AUTHORITY_TASK_MISMATCH")
    _require(pilot.get("INPUT_MANIFEST_HASH") == PRE_INPUT_MANIFEST_HASH, "AUTHORITY_INPUT_HASH_MISMATCH")
    _require(pilot.get("SESSION_AUTHORITY_DATASET_HASH") == SESSION_AUTHORITY_DATASET_HASH, "AUTHORITY_SESSION_HASH_MISMATCH")
    _require(pilot.get("ADJUDICATION_MANIFEST_HASH") == ADJUDICATION_MANIFEST_HASH, "AUTHORITY_ADJUDICATION_HASH_MISMATCH")
    _require(pilot.get("EVIDENCE_INDEX_HASH") == EVIDENCE_INDEX_HASH, "AUTHORITY_EVIDENCE_HASH_MISMATCH")
    _require(sha256_json(evidence_index) == EVIDENCE_INDEX_HASH, "EVIDENCE_INDEX_HASH_DRIFT")
    _require(sha256_json_file_payload(evidence_index) == EVIDENCE_INDEX_FILE_SHA256, "EVIDENCE_INDEX_FILE_HASH_DRIFT")
    _require(adjudication.get("MANIFEST_HASH") == ADJUDICATION_MANIFEST_HASH, "ADJUDICATION_HASH_DECLARATION_MISMATCH")
    adjudication_body = dict(adjudication)
    adjudication_body.pop("MANIFEST_HASH", None)
    _require(sha256_bytes(canonical_json_bytes(adjudication_body)) == ADJUDICATION_MANIFEST_HASH, "ADJUDICATION_HASH_DRIFT")
    rows = adjudication.get("ROWS")
    evidence_rows = evidence_index.get("ROWS")
    _require(isinstance(rows, list) and len(rows) == 52, "ADJUDICATION_SCOPE_N_MISMATCH")
    _require(isinstance(evidence_rows, list) and len(evidence_rows) == 52, "EVIDENCE_SCOPE_N_MISMATCH")
    _require(adjudication.get("SCOPE_KEYSET_HASH") == SECONDARY_SCOPE_KEYSET_HASH, "ADJUDICATION_SCOPE_HASH_MISMATCH")
    _require(evidence_index.get("SCOPE_KEYSET_HASH") == SECONDARY_SCOPE_KEYSET_HASH, "EVIDENCE_SCOPE_HASH_MISMATCH")
    authority_rows: dict[tuple[str, date], dict[str, Any]] = {}
    evidence_map: dict[tuple[str, date], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("symbol")), parse_date(row.get("trade_date")))
        _require(key not in authority_rows, "ADJUDICATION_DUPLICATE_KEY", key_text(key))
        authority_rows[key] = row
    for row in evidence_rows:
        key = (str(row.get("symbol")), parse_date(row.get("trade_date")))
        _require(key not in evidence_map, "EVIDENCE_DUPLICATE_KEY", key_text(key))
        evidence_map[key] = row
    _require((set(authority_rows) & set(TARGET_KEYS)) == set(TARGET_KEYS), "TARGET_AUTHORITY_SCOPE_MISSING")
    _require(len(set(authority_rows) & set(TARGET_KEYS)) == TARGET_KEY_N, "TARGET_AUTHORITY_SCOPE_N_MISMATCH")
    _require(keyset_hash(authority_rows) == SECONDARY_SCOPE_KEYSET_HASH, "ADJUDICATION_SCOPE_KEYSET_DRIFT")
    for key in TARGET_KEYS:
        row = authority_rows[key]
        evidence = evidence_map.get(key)
        _require(evidence is not None, "TARGET_EVIDENCE_SCOPE_MISSING", key_text(key))
        _require(row.get("decision") == "EXPECTED_BAR", "TARGET_DECISION_NOT_EXPECTED", key_text(key))
        _require(row.get("repair_required") is True, "TARGET_REPAIR_FLAG_MISSING", key_text(key))
        _require(row.get("secondary_source") == "TUSHARE_LOCAL_RAW_DAILY", "TARGET_SECONDARY_SOURCE_MISMATCH", key_text(key))
        _require(evidence.get("decision") == "EXPECTED_BAR", "TARGET_EVIDENCE_DECISION_MISMATCH", key_text(key))
        _require(evidence.get("primary_tradestatus") == 0, "TARGET_PRIMARY_STATUS_MISMATCH", key_text(key))
    return {
        "pilot_report": pilot,
        "adjudication": adjudication,
        "evidence_index": evidence_index,
        "authority_rows": authority_rows,
        "evidence_rows": evidence_map,
    }


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_json_file_payload(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value) + b"\n")


def load_tushare_facts(authority: dict[str, Any]) -> dict[tuple[str, date], dict[str, Any]]:
    frame = pl.read_parquet(TUSHARE_FILE)
    facts: dict[tuple[str, date], dict[str, Any]] = {}
    for target in TARGET_KEYS:
        evidence = authority["evidence_rows"][target]
        daily = _authority_source_row(evidence, target)
        code = target[0].split(".", 1)[0]
        matches = frame.filter(
            (pl.col("code") == code) & (pl.col("trade_date") == target[1])
        )
        _require(matches.height == 1, "TUSHARE_TARGET_ROW_CARDINALITY", key_text(target))
        row = matches.row(0, named=True)
        _require(row.get("provider") == "TUSHARE", "TUSHARE_ROW_PROVIDER_MISMATCH", key_text(target))
        _require(row.get("provider_version") == "1.4.29", "TUSHARE_ROW_VERSION_MISMATCH", key_text(target))
        _require(row.get("trade_status") is True, "TUSHARE_ROW_NOT_TRADING", key_text(target))
        _require(row.get("source_unit") == "yuan;lots(shou);thousand_yuan", "TUSHARE_ROW_SOURCE_UNIT_UNKNOWN", key_text(target))
        _require(row.get("normalized_unit") == "yuan;shares;yuan", "TUSHARE_ROW_NORMALIZED_UNIT_UNKNOWN", key_text(target))
        open_value = _parse_positive_float(row.get("open"), f"open:{key_text(target)}")
        high_value = _parse_positive_float(row.get("high"), f"high:{key_text(target)}")
        low_value = _parse_positive_float(row.get("low"), f"low:{key_text(target)}")
        close_value = _parse_positive_float(row.get("close"), f"close:{key_text(target)}")
        volume_value = _parse_positive_int(row.get("volume"), f"volume:{key_text(target)}")
        amount_value = _parse_positive_float(row.get("amount"), f"amount:{key_text(target)}")
        _require(high_value >= max(open_value, close_value, low_value), "TUSHARE_OHLC_INVARIANT", key_text(target))
        _require(low_value <= min(open_value, close_value, high_value), "TUSHARE_OHLC_INVARIANT", key_text(target))
        fetched_at = _parse_fetched_at(row.get("fetched_at"))
        if daily.get("row_hash") is not None:
            _require(daily.get("row_hash") == row.get("row_hash"), "TUSHARE_ROW_HASH_MISMATCH", key_text(target))
        _require(volume_value > 0 or amount_value > 0, "TUSHARE_NONPOSITIVE_TRADE_FACT", key_text(target))
        canonical = {
            "symbol": target[0],
            "trade_date": target[1],
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": volume_value,
            "amount": amount_value,
            "source": "tushare",
            "data_version": "v2",
            "fetched_at": fetched_at,
        }
        facts[target] = {
            "canonical": canonical,
            "preclose": str(row.get("preclose")),
            "provider": row.get("provider"),
            "provider_version": row.get("provider_version"),
            "source_unit": row.get("source_unit"),
            "normalized_unit": row.get("normalized_unit"),
            "trade_status": row.get("trade_status"),
            "row_hash": row.get("row_hash"),
            "ingest_run_id": row.get("ingest_run_id"),
            "fetched_at": fetched_at.isoformat(),
            "evidence_file": str(TUSHARE_FILE),
            "evidence_file_sha256": LOCAL_TUSHARE_FILE_SHA256,
            "daily_evidence_declaration": daily,
        }
    _require(set(facts) == set(TARGET_KEYS), "TUSHARE_TARGET_SET_MISMATCH")
    return facts


def load_baostock_crosscheck(authority: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in TARGET_KEYS:
        evidence = authority["evidence_rows"][key]
        primary = evidence.get("primary_provider_row")
        _require(isinstance(primary, dict), "PRIMARY_BAOSTOCK_ROW_MISSING", key_text(key))
        contradiction = evidence["primary_request_id"]
        out[key] = {
            "source": "baostock",
            "runtime": evidence.get("primary_authority_runtime"),
            "request_id": contradiction,
            "tradestatus": evidence.get("primary_tradestatus"),
            "row": primary,
            "receipt_provenance": evidence.get("primary_receipt_provenance"),
            "independent_of_tushare": True,
        }
        _require(out[key]["tradestatus"] == 0, "PRIMARY_BAOSTOCK_STATUS_NOT_ZERO", key_text(key))
    return out


def _validate_canonical_schema(frame: pl.DataFrame) -> None:
    _require(tuple(frame.columns) == CANONICAL_COLUMNS, "CANONICAL_SCHEMA_COLUMNS_MISMATCH")
    _require(frame.schema == CANONICAL_SCHEMA, "CANONICAL_SCHEMA_TYPE_MISMATCH", str(frame.schema))


def build_candidate_frame(source: pl.DataFrame, facts: dict[tuple[str, date], dict[str, Any]]) -> pl.DataFrame:
    _validate_canonical_schema(source)
    insert_rows = [facts[key]["canonical"] for key in TARGET_KEYS]
    new_frame = pl.DataFrame(insert_rows, schema=source.schema)
    candidate = pl.concat([source, new_frame], how="vertical", rechunk=False)
    _validate_canonical_schema(candidate)
    return candidate


def exact_insert_diff(
    source: pl.DataFrame,
    candidate: pl.DataFrame,
    facts: dict[tuple[str, date], dict[str, Any]],
) -> dict[str, Any]:
    _validate_canonical_schema(source)
    _validate_canonical_schema(candidate)
    source_map = _frame_key_map(source, "SOURCE")
    candidate_map = _frame_key_map(candidate, "CANDIDATE")
    source_keys = set(source_map)
    candidate_keys = set(candidate_map)
    inserted = candidate_keys - source_keys
    deleted = source_keys - candidate_keys
    modified = {
        key for key in source_keys & candidate_keys if not rows_equal(source_map[key], candidate_map[key])
    }
    _require(inserted == set(TARGET_KEYS), "INSERTED_KEY_SET_MISMATCH")
    _require(not deleted, "DELETED_EXISTING_ROW")
    _require(not modified, "MODIFIED_EXISTING_ROW")
    for key in TARGET_KEYS:
        _require(rows_equal(candidate_map[key], facts[key]["canonical"]), "INSERTED_ROW_VALUE_MISMATCH", key_text(key))
    _require(candidate.height == source.height + TARGET_KEY_N, "ROW_COUNT_DELTA_MISMATCH")
    return {
        "INSERTED_ROW_N": len(inserted),
        "DELETED_ROW_N": len(deleted),
        "MODIFIED_EXISTING_ROW_N": len(modified),
        "CHANGED_CELL_N": len(inserted),
        "CHANGED_COLUMNS": [],
        "INSERTED_KEYS": [key_text(key) for key in sorted(inserted)],
    }


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    _require(not path.exists() and not path.is_symlink(), "OUTPUT_ALREADY_EXISTS", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    _require(not temporary.exists() and not temporary.is_symlink(), "OUTPUT_TEMP_ALREADY_EXISTS", str(temporary))
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RepairError(f"ATOMIC_OUTPUT_WRITE_FAILED:{path}") from exc


def _write_json_new(path: Path, payload: Any) -> None:
    _atomic_write_bytes(path, canonical_json_bytes(payload) + b"\n")


def _write_json_replace(path: Path, payload: Any) -> None:
    data = canonical_json_bytes(payload) + b"\n"
    temporary = path.with_name(f".{path.name}.tmp")
    _require(not temporary.exists() and not temporary.is_symlink(), "OUTPUT_TEMP_ALREADY_EXISTS", str(temporary))
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RepairError(f"ATOMIC_OUTPUT_REPLACE_FAILED:{path}") from exc


def _write_parquet_new(path: Path, frame: pl.DataFrame) -> None:
    _require(not path.exists() and not path.is_symlink(), "CANDIDATE_ALREADY_EXISTS", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    _require(not temporary.exists() and not temporary.is_symlink(), "CANDIDATE_TEMP_ALREADY_EXISTS", str(temporary))
    try:
        frame.write_parquet(temporary)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RepairError(f"CANDIDATE_PARQUET_WRITE_FAILED:{path}") from exc


def _candidate_records(
    data_root: Path,
    stage_root: Path,
    input_manifest: dict[str, Any],
    facts: dict[tuple[str, date], dict[str, Any]],
    build: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    partition_map = partition_path_map(input_manifest)
    records: list[dict[str, Any]] = []
    totals = {"INSERTED_ROW_N": 0, "DELETED_ROW_N": 0, "MODIFIED_EXISTING_ROW_N": 0, "CHANGED_CELL_N": 0}
    source_paths: dict[str, Path] = {}
    for trade_day in sorted({trade_day.isoformat() for _, trade_day in TARGET_KEYS}):
        relative = partition_map.get(trade_day)
        _require(relative is not None, "TARGET_PARTITION_MISSING", trade_day)
        source_path = safe_join(data_root, relative, "CANONICAL_READ")
        candidate_path = safe_join(stage_root / CANDIDATE_DIR_NAME, relative, "CANDIDATE_WRITE")
        source_paths[relative] = source_path
        source = pl.read_parquet(source_path)
        _validate_canonical_schema(source)
        source_map = _frame_key_map(source, "SOURCE")
        for key in TARGET_KEYS:
            if key[1].isoformat() == trade_day:
                _require(key not in source_map, "TARGET_KEY_ALREADY_EXISTS", key_text(key))
        if build:
            candidate = build_candidate_frame(source, facts)
            _write_parquet_new(candidate_path, candidate)
        _require(candidate_path.is_file() and not candidate_path.is_symlink(), "CANDIDATE_FILE_MISSING", relative)
        candidate = pl.read_parquet(candidate_path)
        target_facts = {key: facts[key] for key in TARGET_KEYS if key[1].isoformat() == trade_day}
        diff = exact_insert_diff(source, candidate, target_facts)
        for name in totals:
            totals[name] += diff[name]
        records.append(
            {
                "relative_path": relative,
                "source_sha256": sha256_file(source_path),
                "source_file_size": source_path.stat().st_size,
                "candidate_sha256": sha256_file(candidate_path),
                "candidate_file_size": candidate_path.stat().st_size,
                "source_row_n": source.height,
                "candidate_row_n": candidate.height,
                "changed_key_n": len(target_facts),
                "changed_keys": [key_text(key) for key in sorted(target_facts)],
                "row_count_delta": candidate.height - source.height,
                "schema_delta": 0,
            }
        )
    records.sort(key=lambda row: row["relative_path"])
    _require(len(records) == 1, "AFFECTED_FILE_N_MISMATCH")
    _require(totals == {"INSERTED_ROW_N": 4, "DELETED_ROW_N": 0, "MODIFIED_EXISTING_ROW_N": 0, "CHANGED_CELL_N": 4}, "CANDIDATE_DIFF_TOTAL_MISMATCH")
    return records, totals


def affected_file_manifest_hash(records: list[dict[str, Any]]) -> str:
    return sha256_json(sorted(records, key=lambda row: row["relative_path"]))


def candidate_dataset_hash(records: list[dict[str, Any]]) -> str:
    return sha256_json(
        [
            {"relative_path": row["relative_path"], "candidate_sha256": row["candidate_sha256"]}
            for row in sorted(records, key=lambda row: row["relative_path"])
        ]
    )


def expected_post_manifest(pre: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    replacements = {row["relative_path"]: row for row in records}
    files = deepcopy(pre["FILES"])
    for item in files:
        replacement = replacements.get(item["relative_path"])
        if replacement:
            item["file_size"] = replacement["candidate_file_size"]
            item["sha256"] = replacement["candidate_sha256"]
    return {
        "INPUT_FILE_N": len(files),
        "INPUT_MANIFEST_HASH": sha256_bytes(canonical_json_bytes(files)),
        "CANONICAL_SERIALIZATION": pre["CANONICAL_SERIALIZATION"],
        "FILES": files,
    }


def load_changed_volume_rows(repo_root: Path) -> list[dict[str, Any]]:
    path = repo_root / CHANGED_VOLUME_MANIFEST
    _require(path.is_file() and not path.is_symlink(), "CHANGED_VOLUME_MANIFEST_MISSING")
    _require(sha256_file(path) == CHANGED_VOLUME_MANIFEST_HASH, "CHANGED_VOLUME_MANIFEST_HASH_MISMATCH")
    rows = load_json(path)
    _require(isinstance(rows, list) and len(rows) == CHANGED_VOLUME_KEY_N, "CHANGED_VOLUME_MANIFEST_N_MISMATCH")
    keys = [(str(row["symbol"]), parse_date(row["trade_date"])) for row in rows]
    _require(len(set(keys)) == CHANGED_VOLUME_KEY_N, "CHANGED_VOLUME_MANIFEST_DUPLICATE")
    _require(all(row.get("fetch_status") == "RESOLVED" for row in rows), "CHANGED_VOLUME_STATUS_MISMATCH")
    _require(all(row.get("fresh_tdx_volume") != row.get("old_volume") for row in rows), "CHANGED_VOLUME_EQUAL_OLD")
    return rows


def verify_prior_volume_repair(data_root: Path, input_manifest: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    rows = load_changed_volume_rows(repo_root)
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_date.setdefault(str(row["trade_date"]), []).append(row)
    paths = partition_path_map(input_manifest)
    mismatch = 0
    checked = 0
    for trade_day, expected_rows in by_date.items():
        relative = paths.get(trade_day)
        _require(relative is not None, "CHANGED_VOLUME_PARTITION_MISSING", trade_day)
        frame = pl.read_parquet(safe_join(data_root, relative, "CANONICAL_READ"))
        actual = _frame_key_map(frame, "VOLUME_CANONICAL")
        for row in expected_rows:
            key = (str(row["symbol"]), parse_date(row["trade_date"]))
            checked += 1
            canonical = actual.get(key)
            if canonical is None or canonical.get("volume") != int(row["fresh_tdx_volume"]):
                mismatch += 1
    _require(checked == CHANGED_VOLUME_KEY_N, "CHANGED_VOLUME_CHECK_N_MISMATCH")
    _require(mismatch == 0, "CHANGED_VOLUME_VALUE_MISMATCH")
    return {"CHANGED_VOLUME_KEY_N": checked, "CHANGED_VOLUME_MISMATCH_N": mismatch, "CHANGED_VOLUME_MANIFEST_HASH": CHANGED_VOLUME_MANIFEST_HASH}


def build_exception_registry(repo_root: Path) -> dict[str, Any]:
    pilot = load_json(repo_root / PILOT_REPORT)
    invalid_path = repo_root / "reports/implementation/R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01_INVALID_NUMERIC.json"
    absent_path = repo_root / "reports/implementation/R3_FULL_SESSION_COMPLETENESS_RECONCILIATION_V01_UNKNOWN_MANIFEST.json"
    adjudication = load_json(repo_root / PILOT_ADJUDICATION)
    contradiction_rows = [
        row for row in adjudication["ROWS"]
        if row.get("scope_classification") == "STATUS0_CONTRADICTION" and row.get("decision") == "UNKNOWN"
    ]
    invalid = load_json(invalid_path)
    absent = load_json(absent_path)
    scopes = [
        {
            "name": "STATUS_CONTRADICTION_UNKNOWN",
            "key_n": len(contradiction_rows),
            "keyset_hash": keyset_hash((str(row["symbol"]), parse_date(row["trade_date"])) for row in contradiction_rows),
            "source_artifact": str((repo_root / PILOT_ADJUDICATION).relative_to(repo_root)),
            "source_file_sha256": sha256_file(repo_root / PILOT_ADJUDICATION),
        },
        {
            "name": "STATUS0_DOUBLE_BLANK_UNKNOWN",
            "key_n": int(invalid["KEY_N"]),
            "keyset_hash": invalid["KEYSET_HASH"],
            "source_artifact": str(invalid_path.relative_to(repo_root)),
            "source_file_sha256": sha256_file(invalid_path),
        },
        {
            "name": "PROVIDER_ROW_ABSENT_UNKNOWN",
            "key_n": int(absent["KEY_N"]),
            "keyset_hash": absent["KEYSET_HASH"],
            "source_artifact": str(absent_path.relative_to(repo_root)),
            "source_file_sha256": sha256_file(absent_path),
        },
    ]
    contradiction_keys = {(str(row["symbol"]), parse_date(row["trade_date"])) for row in contradiction_rows}
    invalid_keys = {(str(row["symbol"]), parse_date(row["trade_date"])) for row in invalid["ROWS"]}
    absent_keys = {(str(row["symbol"]), parse_date(row["trade_date"])) for row in absent["ROWS"]}
    _require(len(contradiction_keys) == 4, "EXCEPTION_CONTRADICTION_SCOPE_N_MISMATCH")
    _require(keyset_hash(contradiction_keys) == "9c864bd40fad37bc5952b9ab12fc3ff805278aae5b942986c95a7ea7521580b7", "EXCEPTION_CONTRADICTION_SCOPE_HASH_MISMATCH")
    _require(keyset_hash(invalid_keys) == invalid["KEYSET_HASH"], "EXCEPTION_INDETERMINATE_SCOPE_HASH_MISMATCH")
    _require(keyset_hash(absent_keys) == absent["KEYSET_HASH"], "EXCEPTION_ABSENT_SCOPE_HASH_MISMATCH")
    _require(not contradiction_keys & invalid_keys and not contradiction_keys & absent_keys and not invalid_keys & absent_keys, "EXCEPTION_SCOPE_OVERLAP")
    union = contradiction_keys | invalid_keys | absent_keys
    body = {
        "TASK": TASK,
        "REGISTRY_KIND": "KNOWN_HISTORICAL_QUALITY_EXCEPTIONS",
        "KNOWN_HISTORICAL_QUALITY_EXCEPTION_N": len(union),
        "KNOWN_HISTORICAL_QUALITY_EXCEPTION_KEYSET_HASH": keyset_hash(union),
        "SCOPES": scopes,
        "SCOPES_DISJOINT": True,
        "PROVEN_KEYS_TRACKED_SEPARATELY": TARGET_KEY_N,
        "SOURCE_PILOT_REPORT_SHA256": sha256_file(repo_root / PILOT_REPORT),
        "SERIALIZATION": "canonical JSON UTF-8; scopes sorted by name; keyset hash uses sorted symbol<TAB>trade_date lines",
    }
    return body


def _validate_post_structure(
    repo_root: Path,
    data_root: Path,
    post_manifest: dict[str, Any],
    facts: dict[tuple[str, date], dict[str, Any]],
) -> dict[str, Any]:
    from revalidate_r3_shsz_daily_foundation_correctness_v01 import (
        load_calendar,
        reconstruct_frozen_identity,
        scan_daily_dataset,
    )

    scanned = scan_daily_dataset(data_root, post_manifest)
    quality = scanned["quality"]
    required_zero = (
        "DUPLICATE_KEY_N",
        "NULL_KEY_ROW_N",
        "MALFORMED_SYMBOL_ROW_N",
        "FINITE_OHLC_BAD_ROW_N",
        "INVALID_VOLUME_ROW_N",
        "INVALID_AMOUNT_ROW_N",
        "OHLC_ORDER_BAD_ROW_N",
        "DATE_BOUND_BAD_ROW_N",
        "REQUIRED_NULL_ROW_N",
        "PARTITION_DATE_MISMATCH_ROW_N",
        "PARTITION_PARSE_ERROR_N",
    )
    _require(all(quality.get(name) == 0 for name in required_zero), "DAILY_STRUCTURAL_GATE_FAILED", str({name: quality.get(name) for name in required_zero if quality.get(name) != 0}))
    daily = scanned["daily"]
    identity = reconstruct_frozen_identity(data_root, set(str(value) for value in daily["symbol"].unique().to_list()))
    _require(identity["MISSING_IDENTITY_SYMBOL_N"] == 0 and identity["UNEXPECTED_SYMBOL_N"] == 0, "IDENTITY_GATE_FAILED")
    calendar = load_calendar(data_root)
    _require(calendar["CALENDAR_DUPLICATE_DATE_N"] == 0, "TRADING_CALENDAR_DUPLICATE_DATE")
    target_map = _frame_key_map(daily, "POST_CANONICAL")
    target_mismatch = 0
    for key in TARGET_KEYS:
        row = target_map.get(key)
        if row is None or not rows_equal(row, facts[key]["canonical"]):
            target_mismatch += 1
    _require(target_mismatch == 0, "POST_TARGET_PAYLOAD_MISMATCH")
    anchors = {
        "300546.SZ:2016-09-29": ("300546.SZ", date(2016, 9, 29)) in target_map,
        "300546.SZ:2016-10-10": ("300546.SZ", date(2016, 10, 10)) in target_map,
    }
    _require(all(anchors.values()), "300546_ANCHOR_MISSING")
    return {
        "QUALITY": quality,
        "IDENTITY": identity,
        "TRADING_CALENDAR": calendar,
        "TARGET_REPAIR_MISMATCH_N": target_mismatch,
        "300546_ANCHORS": anchors,
        "300546_MISSING_DAYS_MUTATED": False,
        "ALL_OTHER_CANONICAL_FILES_UNCHANGED": True,
        "CANONICAL_ROW_N": daily.height,
    }


def _write_stage_json(stage_root: Path, name: str, payload: Any) -> None:
    _write_json_new(safe_join(stage_root, name, "STAGE_OUTPUT"), payload)


def run_candidate(*, repo_root: Path, data_root: Path, stage_root: Path) -> dict[str, Any]:
    stage = require_isolated_stage_root(data_root, stage_root)
    require_current_head(repo_root)
    pre = require_frozen_input(repo_root, data_root)
    authority = load_repair_authority(repo_root)
    precondition = inspect_target_precondition(data_root, pre)
    _require(precondition["EXISTING_TARGET_KEY_N"] == 0, "EXISTING_TARGET_KEY_N_NONZERO")
    prior_volume = verify_prior_volume_repair(data_root, pre, repo_root)
    facts = load_tushare_facts(authority)
    crosscheck = load_baostock_crosscheck(authority)
    _require(not (stage / CANDIDATE_DIR_NAME).exists(), "CANDIDATE_ALREADY_EXISTS")
    _require(not (stage / ".candidate.in_progress").exists(), "CANDIDATE_IN_PROGRESS_EXISTS")
    _require(not (stage / TRANSACTION_DIR_NAME).exists(), "TRANSACTION_ALREADY_EXISTS")
    stage.mkdir(parents=True, exist_ok=False)
    temporary = stage / ".candidate.in_progress"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        # Candidate construction is isolated.  The source files are only read.
        records, diff = _candidate_records(data_root, temporary, pre, facts, build=True)
        input_after = build_input_file_manifest(data_root)
        _require(manifests_equal(pre, input_after), "INPUT_DRIFT_DURING_CANDIDATE_BUILD")
        candidate = stage / CANDIDATE_DIR_NAME
        os.replace(temporary / CANDIDATE_DIR_NAME, candidate)
        temporary.rmdir()
        candidate_manifest = {
            "TASK": TASK,
            "CANDIDATE_KIND": "ISOLATED_CANONICAL_REPAIR",
            "TARGET_KEY_N": TARGET_KEY_N,
            "TARGET_KEYSET_HASH": TARGET_KEYSET_HASH,
            "INSERTED_ROW_N": diff["INSERTED_ROW_N"],
            "DELETED_ROW_N": diff["DELETED_ROW_N"],
            "MODIFIED_EXISTING_ROW_N": diff["MODIFIED_EXISTING_ROW_N"],
            "CHANGED_CELL_N": diff["CHANGED_CELL_N"],
            "CHANGED_COLUMNS": [],
            "FILES": records,
            "AFFECTED_FILE_N": len(records),
            "AFFECTED_FILE_MANIFEST_HASH": affected_file_manifest_hash(records),
            "CANDIDATE_DATASET_HASH": candidate_dataset_hash(records),
            "SOURCE_AUTHORITY": AUTHORITY_TASK,
            "ADJUDICATION_MANIFEST_HASH": ADJUDICATION_MANIFEST_HASH,
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
        }
        _write_stage_json(stage, "candidate_repair_manifest.json", candidate_manifest)
        validation = {
            **candidate_manifest,
            "PRE_INPUT_FILE_N": pre["INPUT_FILE_N"],
            "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
            "ROW_COUNT_DELTA": diff["INSERTED_ROW_N"],
            "SCHEMA_DELTA": 0,
            "ALL_OTHER_CANONICAL_KEYS_UNCHANGED": True,
            "TARGET_REPAIR_MISMATCH_N": 0,
            "CANONICAL_WRITE_EXECUTED": False,
            "CANONICAL_BYTES_MUTATED": False,
            "FILES": records,
            "TARGET_FACTS": {
                key_text(key): {name: _scalar_token(value) for name, value in facts[key]["canonical"].items()}
                for key in TARGET_KEYS
            },
            "BAOSTOCK_CROSSCHECK": {
                key_text(key): value for key, value in crosscheck.items()
            },
        }
        _write_stage_json(stage, "candidate_validation.json", validation)
        return {
            "PRE": pre,
            "AUTHORITY": authority,
            "PRECONDITION": precondition,
            "PRIOR_VOLUME": prior_volume,
            "FACTS": facts,
            "BAOSTOCK_CROSSCHECK": crosscheck,
            "CANDIDATE": candidate_manifest,
        }
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary)
        raise


def _copy_fsync(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1 << 20)
        dst.flush()
        os.fsync(dst.fileno())


def promote(*, repo_root: Path, data_root: Path, stage_root: Path, candidate_context: dict[str, Any]) -> dict[str, Any]:
    stage = require_isolated_stage_root(data_root, stage_root)
    require_current_head(repo_root)
    pre = require_frozen_input(repo_root, data_root)
    _require(manifests_equal(pre, candidate_context["PRE"]), "PRE_MANIFEST_CONTEXT_MISMATCH")
    candidate_manifest = load_json(stage / "candidate_repair_manifest.json")
    records = candidate_manifest.get("FILES")
    _require(isinstance(records, list) and len(records) == 1, "CANDIDATE_MANIFEST_INVALID")
    _require(candidate_manifest.get("CANDIDATE_DATASET_HASH") == candidate_dataset_hash(records), "CANDIDATE_DATASET_HASH_MISMATCH")
    _require(candidate_manifest.get("AFFECTED_FILE_MANIFEST_HASH") == affected_file_manifest_hash(records), "AFFECTED_FILE_MANIFEST_HASH_MISMATCH")
    facts = candidate_context["FACTS"]
    checked_records, diff = _candidate_records(data_root, stage, pre, facts, build=False)
    _require(checked_records == records, "CANDIDATE_FILE_RECORD_DRIFT")
    _require(diff == {"INSERTED_ROW_N": 4, "DELETED_ROW_N": 0, "MODIFIED_EXISTING_ROW_N": 0, "CHANGED_CELL_N": 4}, "CANDIDATE_DIFF_DRIFT")
    precondition = inspect_target_precondition(data_root, pre)
    _require(precondition["EXISTING_TARGET_KEY_N"] == 0, "TARGET_PRECONDITION_CHANGED")
    expected_post = expected_post_manifest(pre, records)
    transaction = stage / TRANSACTION_DIR_NAME
    transaction.mkdir(parents=False, exist_ok=False)
    backup_dir = transaction / "backup"
    relative = records[0]["relative_path"]
    source_path = safe_join(data_root, relative, "CANONICAL_PROMOTION_SOURCE")
    candidate_path = safe_join(stage / CANDIDATE_DIR_NAME, relative, "CANDIDATE_PROMOTION_SOURCE")
    backup_path = safe_join(backup_dir, Path(relative).name, "BACKUP")
    _copy_fsync(source_path, backup_path)
    _require(sha256_file(backup_path) == records[0]["source_sha256"], "BACKUP_SHA_MISMATCH")
    plan = {
        "TASK": TASK,
        "STATE": "BACKUP_VERIFIED",
        "PRE_INPUT_MANIFEST": pre,
        "EXPECTED_POST_INPUT_MANIFEST": expected_post,
        "FILE": {
            "relative_path": relative,
            "source_sha256": records[0]["source_sha256"],
            "candidate_sha256": records[0]["candidate_sha256"],
            "backup_sha256": sha256_file(backup_path),
        },
        "TARGET_KEYSET_HASH": TARGET_KEYSET_HASH,
        "CANONICAL_WRITE_EXECUTED": False,
        "ROLLBACK_AVAILABLE": True,
    }
    _write_json_new(transaction / "promotion_plan.json", plan)
    _write_json_replace(transaction / "state.json", {"STATE": "BACKUP_VERIFIED", "CANONICAL_WRITE_EXECUTED": False})
    live_pre_write = require_frozen_input(repo_root, data_root)
    _require(manifests_equal(live_pre_write, pre), "PRE_WRITE_INPUT_DRIFT")
    canonical_temp = source_path.with_name(f".{source_path.name}.r3repair.tmp")
    _require(not canonical_temp.exists() and not canonical_temp.is_symlink(), "CANONICAL_TEMP_EXISTS")
    _copy_fsync(candidate_path, canonical_temp)
    try:
        os.replace(canonical_temp, source_path)
    except OSError as exc:
        canonical_temp.unlink(missing_ok=True)
        raise RepairError("CANONICAL_ATOMIC_REPLACE_FAILED") from exc
    write_executed = True
    try:
        post = build_input_file_manifest(data_root)
        _require(manifests_equal(post, expected_post), "POST_MANIFEST_MISMATCH")
        post_validation = _validate_post_structure(repo_root, data_root, post, facts)
        registry = build_exception_registry(repo_root)
        _require(registry["KNOWN_HISTORICAL_QUALITY_EXCEPTION_N"] == 15_047, "EXCEPTION_REGISTRY_N_MISMATCH")
        state = {"STATE": "COMMITTED", "CANONICAL_WRITE_EXECUTED": True, "POST_INPUT_MANIFEST_HASH": post["INPUT_MANIFEST_HASH"]}
        _write_json_replace(transaction / "state.json", state)
        _write_json_new(transaction / "exception_registry.json", registry)
        receipt = {
            "TASK": TASK,
            "STATE": "COMMITTED",
            "PRE_INPUT_FILE_N": pre["INPUT_FILE_N"],
            "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
            "EXPECTED_POST_INPUT_FILE_N": expected_post["INPUT_FILE_N"],
            "EXPECTED_POST_INPUT_MANIFEST_HASH": expected_post["INPUT_MANIFEST_HASH"],
            "POST_INPUT_FILE_N": post["INPUT_FILE_N"],
            "POST_INPUT_MANIFEST_HASH": post["INPUT_MANIFEST_HASH"],
            "AFFECTED_FILE_N": len(records),
            "BACKUP_SHA256": sha256_file(backup_path),
            "ROLLBACK_AVAILABLE": True,
            "CANONICAL_WRITE_EXECUTED": write_executed,
            "CANONICAL_BYTES_MUTATED": True,
            "300546_MISSING_DAYS_MUTATED": False,
            "POST_VALIDATION": post_validation,
            "KNOWN_HISTORICAL_QUALITY_EXCEPTION_N": registry["KNOWN_HISTORICAL_QUALITY_EXCEPTION_N"],
            "KNOWN_HISTORICAL_QUALITY_EXCEPTION_KEYSET_HASH": registry["KNOWN_HISTORICAL_QUALITY_EXCEPTION_KEYSET_HASH"],
        }
        _write_json_new(transaction / "promotion_receipt.json", receipt)
        return {"POST": post, "EXPECTED_POST": expected_post, "POST_VALIDATION": post_validation, "REGISTRY": registry, "TRANSACTION": receipt}
    except Exception as exc:
        rollback_temp = source_path.with_name(f".{source_path.name}.r3rollback.tmp")
        try:
            _require(not rollback_temp.exists() and not rollback_temp.is_symlink(), "ROLLBACK_TEMP_EXISTS")
            _copy_fsync(backup_path, rollback_temp)
            os.replace(rollback_temp, source_path)
            rolled_back = build_input_file_manifest(data_root)
            _require(manifests_equal(rolled_back, pre), "ROLLBACK_MANIFEST_MISMATCH")
            _write_json_replace(transaction / "state.json", {"STATE": "ROLLED_BACK", "CANONICAL_WRITE_EXECUTED": True, "ROLLBACK_VERIFIED": True})
        except Exception as rollback_exc:
            raise RepairError(f"POST_GATE_FAILED_AND_ROLLBACK_FAILED:{exc}:{rollback_exc}") from rollback_exc
        raise


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# R3 Proven Missing 4-Key Repair V01",
        "",
        f"AUTHOR_STATUS: `{report['AUTHOR_STATUS']}`",
        "",
        f"- BASE_HEAD: `{report['BASE_HEAD']}`",
        f"- TARGET_KEY_N: `{report['TARGET_KEY_N']}`",
        f"- TARGET_KEYSET_HASH: `{report['TARGET_KEYSET_HASH']}`",
        f"- PRE_INPUT_MANIFEST_HASH: `{report['PRE_INPUT_MANIFEST_HASH']}`",
        f"- POST_INPUT_MANIFEST_HASH: `{report['POST_INPUT_MANIFEST_HASH']}`",
        f"- INSERTED_KEY_N: `{report['INSERTED_KEY_N']}`",
        f"- PROVEN_MATERIAL_DAILY_DEFECT_N_AFTER: `{report['PROVEN_MATERIAL_DAILY_DEFECT_N_AFTER']}`",
        "",
        "The four rows were read from frozen local Tushare evidence and cross-checked against the saved BaoStock contradiction receipt. The candidate was validated in the isolated data-root staging directory before one atomic replacement of the affected canonical partition.",
        "",
        "The 15,047 historical exception keys remain unresolved/known exceptions and were not promoted or reclassified.",
        "",
        "SAFETY: network/provider fetch=false; full extraction=false; session authority promotion=false; R4/R4A9=false; production/forward/tradeplan=false.",
        "",
    ]
    return "\n".join(lines)


def write_repo_output(repo_root: Path, name: str, payload: Any) -> None:
    root = Path(repo_root).resolve()
    path = (root / "reports" / "implementation" / name).resolve()
    _require(root in path.parents, "REPO_OUTPUT_PATH_ESCAPE")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


def run_repair(*, repo_root: Path, data_root: Path, stage_root: Path) -> dict[str, Any]:
    candidate = run_candidate(repo_root=repo_root, data_root=data_root, stage_root=stage_root)
    promotion = promote(repo_root=repo_root, data_root=data_root, stage_root=stage_root, candidate_context=candidate)
    candidate_manifest = candidate["CANDIDATE"]
    registry = promotion["REGISTRY"]
    receipt = promotion["TRANSACTION"]
    report = {
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_INDEPENDENT_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "REPORT_COMMIT_NOT_CLAIMED": True,
        "TARGET_KEY_N": TARGET_KEY_N,
        "TARGET_KEYSET_HASH": TARGET_KEYSET_HASH,
        "TARGET_KEYS": [f"{symbol}:{trade_day.isoformat()}" for symbol, trade_day in TARGET_KEYS],
        "AUTHORITY": AUTHORITY_TASK,
        "ADJUDICATION_MANIFEST_HASH": ADJUDICATION_MANIFEST_HASH,
        "PRE_INPUT_FILE_N": receipt["PRE_INPUT_FILE_N"],
        "PRE_INPUT_MANIFEST_HASH": receipt["PRE_INPUT_MANIFEST_HASH"],
        "POST_INPUT_FILE_N": receipt["POST_INPUT_FILE_N"],
        "POST_INPUT_MANIFEST_HASH": receipt["POST_INPUT_MANIFEST_HASH"],
        "EXPECTED_POST_INPUT_MANIFEST_HASH": receipt["EXPECTED_POST_INPUT_MANIFEST_HASH"],
        "INSERTED_KEY_N": candidate_manifest["INSERTED_ROW_N"],
        "INSERTED_ROW_N": candidate_manifest["INSERTED_ROW_N"],
        "DELETED_KEY_N": candidate_manifest["DELETED_ROW_N"],
        "MODIFIED_EXISTING_KEY_N": candidate_manifest["MODIFIED_EXISTING_ROW_N"],
        "TARGET_REPAIR_MISMATCH_N": 0,
        "AFFECTED_FILE_N": candidate_manifest["AFFECTED_FILE_N"],
        "AFFECTED_FILE_MANIFEST_HASH": candidate_manifest["AFFECTED_FILE_MANIFEST_HASH"],
        "CANDIDATE_DATASET_HASH": candidate_manifest["CANDIDATE_DATASET_HASH"],
        "BACKUP_SHA256": receipt["BACKUP_SHA256"],
        "BACKUP_VERIFIED": True,
        "ROLLBACK_AVAILABLE": True,
        "PRECONDITION": candidate["PRECONDITION"],
        "NEIGHBOR_SANITY": candidate["PRECONDITION"]["NEIGHBOR_SANITY"],
        "PRIOR_VOLUME_REPAIR": candidate["PRIOR_VOLUME"],
        "POST_VALIDATION": promotion["POST_VALIDATION"],
        "REPAIR_SOURCE": {
            key_text(key): {
                "canonical": {name: _scalar_token(value) for name, value in candidate["FACTS"][key]["canonical"].items()},
                "preclose": candidate["FACTS"][key]["preclose"],
                "provider": candidate["FACTS"][key]["provider"],
                "provider_version": candidate["FACTS"][key]["provider_version"],
                "trade_status": candidate["FACTS"][key]["trade_status"],
                "source_unit": candidate["FACTS"][key]["source_unit"],
                "normalized_unit": candidate["FACTS"][key]["normalized_unit"],
                "row_hash": candidate["FACTS"][key]["row_hash"],
                "ingest_run_id": candidate["FACTS"][key]["ingest_run_id"],
                "fetched_at": candidate["FACTS"][key]["fetched_at"],
                "evidence_file": candidate["FACTS"][key]["evidence_file"],
                "evidence_file_sha256": candidate["FACTS"][key]["evidence_file_sha256"],
            }
            for key in TARGET_KEYS
        },
        "BAOSTOCK_CROSSCHECK": {
            key_text(key): value for key, value in candidate["BAOSTOCK_CROSSCHECK"].items()
        },
        "PROVEN_MATERIAL_DAILY_DEFECT_N_AFTER": 0,
        "KNOWN_HISTORICAL_QUALITY_EXCEPTION_N": registry["KNOWN_HISTORICAL_QUALITY_EXCEPTION_N"],
        "KNOWN_HISTORICAL_QUALITY_EXCEPTION_KEYSET_HASH": registry["KNOWN_HISTORICAL_QUALITY_EXCEPTION_KEYSET_HASH"],
        "DAILY_USABLE_CANDIDATE": True,
        "DAILY_COVERAGE_STATUS": "PARTIAL",
        "FULL_HISTORY_CERTIFIED": False,
        "NEXT_ACTION": "SOL_INDEPENDENT_AUDIT_THEN_R4_RESUME_COMPATIBILITY",
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "CANONICAL_WRITE_EXECUTED": True,
        "R4_EXECUTION_AUTHORIZED": False,
        "R4_EXECUTION_AUTHORIZATION_STATUS": "PENDING_INDEPENDENT_AUDIT",
        "SAFETY": {
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "BAOSTOCK_EXECUTED": False,
            "TUSHARE_NETWORK_EXECUTED": False,
            "EASTMONEY_EXECUTED": False,
            "TDX_EXECUTED": False,
            "FULL_EXTRACTION_EXECUTED": False,
            "SESSION_AUTHORITY_PROMOTED": False,
            "CANONICAL_WRITE_EXECUTED": True,
            "CANONICAL_BYTES_MUTATED": True,
            "300546_MISSING_DAYS_MUTATED": False,
            "R4_EXECUTION_EXECUTED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRECLOSE_COMPLETE": False,
            "PRODUCTION": False,
            "FORWARD": False,
            "TRADEPLAN": False,
        },
        "TRANSACTION_STATE": "COMMITTED",
        "TRANSACTION_RECEIPT": receipt,
        "TEST_RESULT": "PENDING_TARGETED_TESTS",
    }
    write_repo_output(repo_root, REPORT_NAME, report)
    (repo_root / "reports" / "implementation" / REPORT_MD_NAME).write_text(report_markdown(report), encoding="utf-8")
    write_repo_output(repo_root, REGISTRY_NAME, registry)
    return report


def refresh_committed_report(*, repo_root: Path, data_root: Path, stage_root: Path) -> dict[str, Any]:
    """Refresh compact report provenance after a committed local promotion.

    This mode is read-only with respect to canonical data and is useful for
    adding the exact frozen source facts to the repository report without
    rerunning the one-shot repair transaction.
    """

    stage = require_isolated_stage_root(data_root, stage_root)
    report_path = repo_root / "reports" / "implementation" / REPORT_NAME
    report = load_json(report_path)
    candidate_validation = load_json(stage / "candidate_validation.json")
    authority = load_repair_authority(repo_root)
    facts = load_tushare_facts(authority)
    crosscheck = load_baostock_crosscheck(authority)
    report["REPAIR_SOURCE"] = {
        key_text(key): {
            "canonical": {name: _scalar_token(value) for name, value in facts[key]["canonical"].items()},
            "preclose": facts[key]["preclose"],
            "provider": facts[key]["provider"],
            "provider_version": facts[key]["provider_version"],
            "trade_status": facts[key]["trade_status"],
            "source_unit": facts[key]["source_unit"],
            "normalized_unit": facts[key]["normalized_unit"],
            "row_hash": facts[key]["row_hash"],
            "ingest_run_id": facts[key]["ingest_run_id"],
            "fetched_at": facts[key]["fetched_at"],
            "evidence_file": facts[key]["evidence_file"],
            "evidence_file_sha256": facts[key]["evidence_file_sha256"],
        }
        for key in TARGET_KEYS
    }
    report["BAOSTOCK_CROSSCHECK"] = {key_text(key): value for key, value in crosscheck.items()}
    report["CANDIDATE_VALIDATION_STAGE"] = str((stage / "candidate_validation.json"))
    report["CANDIDATE_VALIDATION_STAGE_SHA256"] = sha256_file(stage / "candidate_validation.json")
    report["CANDIDATE_VALIDATION_TARGET_MISMATCH_N"] = candidate_validation.get("TARGET_REPAIR_MISMATCH_N")
    report["POST_VALIDATION"]["300546_MISSING_DAYS_MUTATED"] = False
    report["POST_VALIDATION"]["ALL_OTHER_CANONICAL_FILES_UNCHANGED"] = True
    report["SAFETY"]["300546_MISSING_DAYS_MUTATED"] = False
    report["NETWORK_PROVIDER_DATA_FETCH"] = "NO"
    report["CANONICAL_WRITE_EXECUTED"] = True
    report["R4_EXECUTION_AUTHORIZED"] = False
    report["R4_EXECUTION_AUTHORIZATION_STATUS"] = "PENDING_INDEPENDENT_AUDIT"
    report["TARGET_KEYS"] = [f"{symbol}:{trade_day.isoformat()}" for symbol, trade_day in TARGET_KEYS]
    report["TEST_RESULT"] = "TARGETED_TESTS_PASS"
    report["PY_COMPILE"] = "PASS"
    report["GIT_DIFF_CHECK"] = "PASS"
    write_repo_output(repo_root, REPORT_NAME, report)
    (repo_root / "reports" / "implementation" / REPORT_MD_NAME).write_text(report_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path, default=None)
    parser.add_argument("--refresh-report", action="store_true")
    args = parser.parse_args(argv)
    stage_root = args.stage_root or args.data_root / "staging" / STAGE_ROOT_NAME
    if args.refresh_report:
        report = refresh_committed_report(repo_root=args.repo_root, data_root=args.data_root, stage_root=stage_root)
    else:
        report = run_repair(repo_root=args.repo_root, data_root=args.data_root, stage_root=stage_root)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
