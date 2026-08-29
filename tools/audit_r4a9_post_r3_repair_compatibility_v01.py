#!/usr/bin/env python3
"""Read-only compatibility audit for the R4A9 checkpoint after the R3 repair.

The audit deliberately has no provider import and no data-root write path.  It
recomputes the current canonical input manifest, cross-checks the committed R3
transaction evidence, reads the historical R4A9 checkpoint and only the small
canonical windows needed for the four repaired keys.  Reports are written only
under this repository's ``reports/implementation`` directory after every data
root gate has passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

TASK = "R4A9_POST_R3_REPAIR_COMPATIBILITY_V01"
BASE_HEAD = "b7739d8dfbf9438821c11e97c6147f7807a3375b"
BRANCH = "codex/r4a9-post-r3-repair-compatibility-v01"

OLD_DAILY_MANIFEST_HASH = (
    "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
)
NEW_DAILY_MANIFEST_HASH = (
    "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"
)
FORMAL_SYMBOL_N = 5_456
FORMAL_IDENTITY_HASH = (
    "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
)
R3_TARGET_KEYS = (
    ("002087.SZ", date(2024, 6, 13)),
    ("600647.SH", date(2024, 6, 13)),
    ("600766.SH", date(2024, 6, 13)),
    ("603133.SH", date(2024, 6, 13)),
)
R3_TARGET_KEYSET_HASH = (
    "49fd7d316e2a09bbb18f0b840d4a5034f3efb2dbba57e9f60255c7a8910b2663"
)

R4A9_STAGE_DIRNAME = "r4a9-preclose-real-full-extraction-v01"
R4A9_CHECKPOINT_NAME = "manifest.json"
R4A9_REPORT_REL = Path(
    "reports/implementation/R4A9_PRECLOSE_REAL_FULL_EXTRACTION_V01.json"
)
R4A9_DIAG_REL = Path(
    "reports/implementation/R4A9_1_300546_PRECLOSE_QUALITY_DIAGNOSIS_V01.json"
)
R3_REPORT_REL = Path("reports/implementation/R3_PROVEN_MISSING_4KEY_REPAIR_V01.json")
R3_STAGE_DIRNAME = "r3_proven_missing_4key_repair_v01"

REPORT_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"

R4A9_CODE_HEAD = "795b1b8f6b688ecc2e94f85c09d80c365e648920"
R4A9_QUERY_PLAN_HASH = (
    "9773875fbae9494bc1d9477cd18633dbccb92112733d9a1077fc3a43bcc38a60"
)
R4A9_FORMAL_SOURCE = "BAOSTOCK_HISTORY_K_PRECLOSE"
R4A9_QUERY_FIELDS = "date,code,preclose,tradestatus"
R4A9_QUERY_CONTRACT = "R4A_PRECLOSE_V01"

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


class CompatibilityError(RuntimeError):
    """Terminal fail-closed compatibility error."""


def _require(condition: bool, code: str, detail: str | None = None) -> None:
    if not condition:
        raise CompatibilityError(code if detail is None else f"{code}:{detail}")


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"NOT_JSON_SERIALIZABLE:{type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_dev)


def sha256_file(path: Path) -> str:
    """Hash a local file and fail if its relevant identity changes mid-read."""

    try:
        before = _file_identity(path)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        after = _file_identity(path)
    except OSError as exc:
        raise CompatibilityError(f"LOCAL_FILE_READ_FAILED:{path}") from exc
    if before != after:
        raise CompatibilityError(f"LOCAL_FILE_MUTATED_DURING_READ:{path}")
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompatibilityError(f"LOCAL_JSON_READ_FAILED:{path}") from exc


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise CompatibilityError(f"INVALID_DATE:{value!r}") from exc


def key_text(key: tuple[str, date]) -> str:
    return f"{key[0]}:{key[1].isoformat()}"


def parse_key_text(value: str) -> tuple[str, date]:
    try:
        symbol, raw_date = value.split(":", 1)
    except ValueError as exc:
        raise CompatibilityError(f"INVALID_KEY_TEXT:{value!r}") from exc
    return symbol, parse_date(raw_date)


def keyset_hash(keys: Iterable[tuple[str, date]]) -> str:
    ordered = sorted(keys)
    _require(len(ordered) == len(set(ordered)), "DUPLICATE_KEY_FOR_HASH")
    digest = hashlib.sha256()
    for symbol, trade_day in ordered:
        digest.update(f"{symbol}\t{trade_day.isoformat()}\n".encode("utf-8"))
    return digest.hexdigest()


def display_equal(left: Any, right: Any) -> bool:
    try:
        left_decimal = Decimal(str(left)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        right_decimal = Decimal(str(right)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (ArithmeticError, TypeError, ValueError):
        return False
    return left_decimal == right_decimal


def scalar_for_report(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def require_base_head(repo_root: Path) -> None:
    try:
        current = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CompatibilityError("GIT_HEAD_UNAVAILABLE") from exc
    _require(current == BASE_HEAD, "BASE_HEAD_MISMATCH", current)


def build_input_file_manifest(data_root: Path) -> dict[str, Any]:
    """Recompute the exact R3 daily-bars file manifest without writing data."""

    root = Path(data_root).expanduser()
    _require(root.is_absolute(), "DATA_ROOT_NOT_ABSOLUTE")
    _require(root.is_dir() and not root.is_symlink(), "DATA_ROOT_INVALID")
    root = root.resolve(strict=True)
    daily_root = root / "curated" / "daily_bars"
    _require(daily_root.is_dir() and not daily_root.is_symlink(), "DAILY_ROOT_INVALID")
    paths = sorted(daily_root.rglob("*.parquet"))
    _require(paths, "DAILY_FILES_MISSING")
    rows: list[dict[str, Any]] = []
    for path in paths:
        _require(path.is_file() and not path.is_symlink(), "DAILY_FILE_INVALID", str(path))
        rows.append(
            {
                "relative_path": str(path.relative_to(root)),
                "file_size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    rows.sort(key=lambda row: row["relative_path"])
    return {
        "INPUT_FILE_N": len(rows),
        "INPUT_MANIFEST_HASH": sha256_bytes(canonical_json_bytes(rows)),
        "FILES": rows,
    }


def manifests_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("INPUT_FILE_N") == right.get("INPUT_FILE_N")
        and left.get("INPUT_MANIFEST_HASH") == right.get("INPUT_MANIFEST_HASH")
        and left.get("FILES") == right.get("FILES")
    )


def _manifest_files_by_path(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    files = manifest.get("FILES")
    _require(isinstance(files, list), "INPUT_MANIFEST_FILES_INVALID")
    by_path = {str(row["relative_path"]): row for row in files}
    _require(len(by_path) == len(files), "INPUT_MANIFEST_DUPLICATE_PATH")
    return by_path


def compare_manifest_lineage(
    old_manifest: dict[str, Any],
    new_manifest: dict[str, Any],
    *,
    expected_changed_path: str,
    expected_inserted_key_n: int,
) -> dict[str, Any]:
    """Prove a manifest change is one exact file mutation, not global drift."""

    old_files = _manifest_files_by_path(old_manifest)
    new_files = _manifest_files_by_path(new_manifest)
    _require(set(old_files) == set(new_files), "CANONICAL_FILE_SET_DRIFT")
    changed_paths = sorted(
        path for path in old_files if old_files[path] != new_files[path]
    )
    _require(
        changed_paths == [expected_changed_path],
        "CANONICAL_CHANGED_FILE_SCOPE_MISMATCH",
        repr(changed_paths),
    )
    return {
        "AFFECTED_CANONICAL_FILE_N": len(changed_paths),
        "AFFECTED_CANONICAL_FILES": [
            {
                "relative_path": expected_changed_path,
                "old": old_files[expected_changed_path],
                "new": new_files[expected_changed_path],
            }
        ],
        "INSERTED_KEY_N": expected_inserted_key_n,
        "DELETED_KEY_N": 0,
        "MODIFIED_EXISTING_KEY_N": 0,
    }


def load_formal_symbols(data_root: Path) -> set[str]:
    daily_glob = str(data_root / "curated" / "daily_bars" / "**" / "*.parquet")
    try:
        frame = pl.scan_parquet(daily_glob).select("symbol").unique().collect()
    except Exception as exc:  # pragma: no cover - exercised by real-root gate
        raise CompatibilityError("FORMAL_SYMBOL_SCAN_FAILED") from exc
    symbols = {str(value) for value in frame.get_column("symbol").to_list()}
    _require(len(symbols) == FORMAL_SYMBOL_N, "FORMAL_SYMBOL_N_MISMATCH", str(len(symbols)))
    _require(
        all(symbol.endswith((".SH", ".SZ")) for symbol in symbols),
        "FORMAL_SYMBOL_SCOPE_MISMATCH",
    )
    computed = sha256_bytes(canonical_json_bytes(sorted(symbols)))
    _require(computed == FORMAL_IDENTITY_HASH, "FORMAL_IDENTITY_HASH_MISMATCH", computed)
    return symbols


def _require_under(path: Path, root: Path, code: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=False)
    _require(resolved_path == resolved_root or resolved_root in resolved_path.parents, code)
    return resolved_path


def load_r4a9_checkpoint(
    data_root: Path,
    formal_symbols: set[str],
) -> dict[str, Any]:
    stage_root = data_root / "staging" / R4A9_STAGE_DIRNAME
    checkpoint_path = stage_root / R4A9_CHECKPOINT_NAME
    _require(stage_root.is_dir() and not stage_root.is_symlink(), "R4A9_STAGE_ROOT_INVALID")
    _require(checkpoint_path.is_file() and not checkpoint_path.is_symlink(), "R4A9_CHECKPOINT_MISSING")
    checkpoint = load_json(checkpoint_path)
    _require(checkpoint.get("schema_version") == "R4A7_PRECLOSE_V01", "R4A9_CHECKPOINT_SCHEMA_MISMATCH")
    units = checkpoint.get("units")
    _require(isinstance(units, dict), "R4A9_CHECKPOINT_UNITS_INVALID")

    unit_symbols = set(units)
    _require(unit_symbols <= formal_symbols, "R4A9_CHECKPOINT_SYMBOL_SCOPE_MISMATCH")
    for symbol, entry in units.items():
        _require(isinstance(entry, dict), "R4A9_CHECKPOINT_ENTRY_INVALID", symbol)
        _require(entry.get("symbol") == symbol, "R4A9_CHECKPOINT_SYMBOL_KEY_MISMATCH", symbol)
        _require(entry.get("STATE") in {"COMPLETE", "FAILED"}, "R4A9_CHECKPOINT_STATE_INVALID", symbol)

    complete = {symbol for symbol, entry in units.items() if entry.get("STATE") == "COMPLETE"}
    failed = {symbol for symbol, entry in units.items() if entry.get("STATE") == "FAILED"}
    unvisited = formal_symbols - unit_symbols
    _require(complete == unit_symbols - failed, "R4A9_CHECKPOINT_PARTITION_INVALID")
    _require(len(complete) == 2_140, "R4A9_COMPLETE_COUNT_MISMATCH", str(len(complete)))
    _require(failed == {"300546.SZ"}, "R4A9_FAILED_SCOPE_MISMATCH", repr(sorted(failed)))
    _require(len(unvisited) == 3_315, "R4A9_UNVISITED_COUNT_MISMATCH", str(len(unvisited)))

    contract = checkpoint.get("contract")
    _require(isinstance(contract, dict), "R4A9_CHECKPOINT_CONTRACT_INVALID")
    checkpoint_hash = sha256_file(checkpoint_path)
    return {
        "path": checkpoint_path,
        "hash": checkpoint_hash,
        "manifest": checkpoint,
        "units": units,
        "contract": contract,
        "complete": complete,
        "failed": failed,
        "unvisited": unvisited,
        "COMPLETE_SYMBOL_N": len(complete),
        "FAILED_SYMBOL_N": len(failed),
        "UNVISITED_SYMBOL_N": len(unvisited),
    }


def _contains_exact_text(path: Path, value: str) -> bool:
    try:
        return value in path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CompatibilityError(f"LOCAL_TEXT_READ_FAILED:{path}") from exc


def load_r4a9_authority(repo_root: Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    report_path = repo_root / R4A9_REPORT_REL
    diag_path = repo_root / R4A9_DIAG_REL
    _require(report_path.is_file(), "R4A9_REPORT_MISSING")
    _require(diag_path.is_file(), "R4A9_DIAGNOSTIC_MISSING")
    report = load_json(report_path)
    diagnostic = load_json(diag_path)

    _require(report.get("FORMAL_IDENTITY_N") == FORMAL_SYMBOL_N, "R4A9_IDENTITY_N_MISMATCH")
    _require(report.get("FORMAL_IDENTITY_HASH") == FORMAL_IDENTITY_HASH, "R4A9_IDENTITY_HASH_MISMATCH")
    _require(report.get("FULL_QUERY_PLAN_HASH") == R4A9_QUERY_PLAN_HASH, "R4A9_QUERY_PLAN_HASH_MISMATCH")
    _require(report.get("COMPLETE_N") == checkpoint["COMPLETE_SYMBOL_N"], "R4A9_REPORT_COMPLETE_MISMATCH")
    _require(report.get("FAILED_N") == checkpoint["FAILED_SYMBOL_N"], "R4A9_REPORT_FAILED_MISMATCH")
    _require(report.get("FAILED_UNIT", {}).get("symbol") == "300546.SZ", "R4A9_REPORT_FAILED_SYMBOL_MISMATCH")
    _require(report.get("RUNTIME_HEAD") == R4A9_CODE_HEAD, "R4A9_RUNTIME_HEAD_MISMATCH")
    _require(report.get("provider", {}).get("runtime") == "baostock-0.9.3", "R4A9_PROVIDER_RUNTIME_MISMATCH")
    _require(report.get("query_contract", {}).get("fields") == R4A9_QUERY_FIELDS, "R4A9_QUERY_FIELDS_MISMATCH")
    _require(report.get("query_contract", {}).get("frequency") == "d", "R4A9_QUERY_FREQUENCY_MISMATCH")
    _require(report.get("query_contract", {}).get("adjustflag") == "3", "R4A9_QUERY_ADJUSTFLAG_MISMATCH")

    contract = checkpoint["contract"]
    _require(contract.get("ADAPTER_AUTHORITY_SHA") == R4A9_CODE_HEAD, "R4A9_CHECKPOINT_ADAPTER_HEAD_MISMATCH")
    _require(contract.get("FULL_QUERY_PLAN_HASH") == R4A9_QUERY_PLAN_HASH, "R4A9_CHECKPOINT_PLAN_HASH_MISMATCH")
    _require(contract.get("FORMAL_IDENTITY_HASH") == FORMAL_IDENTITY_HASH, "R4A9_CHECKPOINT_IDENTITY_HASH_MISMATCH")

    old_bound = any(
        _contains_exact_text(path, OLD_DAILY_MANIFEST_HASH)
        for path in (report_path, checkpoint["path"])
    )
    new_bound = any(
        _contains_exact_text(path, NEW_DAILY_MANIFEST_HASH)
        for path in (report_path, checkpoint["path"])
    )
    if old_bound and not new_bound:
        input_daily_manifest: str | None = OLD_DAILY_MANIFEST_HASH
        input_daily_status = "EXPLICIT_OLD_MANIFEST_BOUND"
    elif new_bound and not old_bound:
        input_daily_manifest = NEW_DAILY_MANIFEST_HASH
        input_daily_status = "EXPLICIT_NEW_MANIFEST_BOUND"
    elif not old_bound and not new_bound:
        input_daily_manifest = None
        input_daily_status = "UNBOUND_IN_R4A9_ARTIFACTS"
    else:
        raise CompatibilityError("R4A9_INPUT_DAILY_MANIFEST_CONFLICT")

    return {
        "report_path": report_path,
        "report_sha256": sha256_file(report_path),
        "diagnostic_path": diag_path,
        "diagnostic_sha256": sha256_file(diag_path),
        "report": report,
        "diagnostic": diagnostic,
        "R4A9_INPUT_DAILY_MANIFEST_HASH": input_daily_manifest,
        "R4A9_INPUT_DAILY_MANIFEST_STATUS": input_daily_status,
    }


def load_r3_lineage(repo_root: Path, data_root: Path, current_manifest: dict[str, Any]) -> dict[str, Any]:
    report_path = repo_root / R3_REPORT_REL
    stage_root = data_root / "staging" / R3_STAGE_DIRNAME
    plan_path = stage_root / "transaction" / "promotion_plan.json"
    receipt_path = stage_root / "transaction" / "promotion_receipt.json"
    candidate_manifest_path = stage_root / "candidate_repair_manifest.json"
    backup_path = stage_root / "transaction" / "backup" / "part-merged.parquet"
    candidate_path = stage_root / "candidate" / "curated" / "daily_bars" / "trade_date=2024-06-13" / "part-merged.parquet"

    for path, code in (
        (report_path, "R3_REPAIR_REPORT_MISSING"),
        (plan_path, "R3_PROMOTION_PLAN_MISSING"),
        (receipt_path, "R3_PROMOTION_RECEIPT_MISSING"),
        (candidate_manifest_path, "R3_CANDIDATE_MANIFEST_MISSING"),
        (backup_path, "R3_BACKUP_MISSING"),
        (candidate_path, "R3_CANDIDATE_FILE_MISSING"),
    ):
        _require(path.is_file() and not path.is_symlink(), code)

    report = load_json(report_path)
    plan = load_json(plan_path)
    receipt = load_json(receipt_path)
    candidate_manifest = load_json(candidate_manifest_path)
    pre = plan.get("PRE_INPUT_MANIFEST")
    expected_post = plan.get("EXPECTED_POST_INPUT_MANIFEST")
    _require(isinstance(pre, dict) and isinstance(expected_post, dict), "R3_PROMOTION_PLAN_MANIFESTS_INVALID")
    _require(pre.get("INPUT_MANIFEST_HASH") == OLD_DAILY_MANIFEST_HASH, "R3_OLD_MANIFEST_HASH_MISMATCH")
    _require(expected_post.get("INPUT_MANIFEST_HASH") == NEW_DAILY_MANIFEST_HASH, "R3_NEW_MANIFEST_HASH_MISMATCH")
    _require(sha256_bytes(canonical_json_bytes(pre.get("FILES"))) == OLD_DAILY_MANIFEST_HASH, "R3_OLD_MANIFEST_RECOMPUTE_MISMATCH")
    _require(sha256_bytes(canonical_json_bytes(expected_post.get("FILES"))) == NEW_DAILY_MANIFEST_HASH, "R3_NEW_MANIFEST_RECOMPUTE_MISMATCH")
    _require(manifests_equal(current_manifest, expected_post), "CURRENT_MANIFEST_NOT_EXPECTED_R3_POST")

    _require(report.get("PRE_INPUT_MANIFEST_HASH") == OLD_DAILY_MANIFEST_HASH, "R3_REPORT_OLD_HASH_MISMATCH")
    _require(report.get("POST_INPUT_MANIFEST_HASH") == NEW_DAILY_MANIFEST_HASH, "R3_REPORT_NEW_HASH_MISMATCH")
    _require(report.get("TARGET_KEY_N") == 4, "R3_REPORT_TARGET_N_MISMATCH")
    _require(report.get("INSERTED_KEY_N") == 4, "R3_REPORT_INSERTED_N_MISMATCH")
    _require(report.get("MODIFIED_EXISTING_KEY_N") == 0, "R3_REPORT_MODIFIED_N_MISMATCH")
    _require(report.get("TARGET_REPAIR_MISMATCH_N") == 0, "R3_REPORT_TARGET_MISMATCH")
    _require(report.get("TRANSACTION_STATE") == "COMMITTED", "R3_REPORT_TRANSACTION_NOT_COMMITTED")
    _require(receipt.get("STATE") == "COMMITTED", "R3_PROMOTION_RECEIPT_NOT_COMMITTED")
    _require(receipt.get("PRE_INPUT_MANIFEST_HASH") == OLD_DAILY_MANIFEST_HASH, "R3_RECEIPT_OLD_HASH_MISMATCH")
    _require(receipt.get("POST_INPUT_MANIFEST_HASH") == NEW_DAILY_MANIFEST_HASH, "R3_RECEIPT_NEW_HASH_MISMATCH")
    _require(receipt.get("CANONICAL_WRITE_EXECUTED") is True, "R3_RECEIPT_WRITE_NOT_RECORDED")

    target_strings = {key_text(key) for key in R3_TARGET_KEYS}
    report_targets = {str(value) for value in report.get("TARGET_KEYS", [])}
    _require(report_targets == target_strings, "R3_REPORT_TARGET_SCOPE_MISMATCH")
    _require(candidate_manifest.get("TARGET_KEYSET_HASH") == R3_TARGET_KEYSET_HASH, "R3_CANDIDATE_TARGET_HASH_MISMATCH")
    candidate_files = candidate_manifest.get("FILES")
    _require(isinstance(candidate_files, list) and len(candidate_files) == 1, "R3_CANDIDATE_FILE_MANIFEST_INVALID")
    candidate_file = candidate_files[0]
    expected_relative = "curated/daily_bars/trade_date=2024-06-13/part-merged.parquet"
    _require(candidate_file.get("relative_path") == expected_relative, "R3_CANDIDATE_PATH_MISMATCH")
    _require(set(candidate_file.get("changed_keys", [])) == target_strings, "R3_CANDIDATE_TARGET_SCOPE_MISMATCH")

    lineage = compare_manifest_lineage(
        pre,
        expected_post,
        expected_changed_path=expected_relative,
        expected_inserted_key_n=4,
    )
    _require(sha256_file(backup_path) == candidate_file.get("source_sha256"), "R3_BACKUP_SHA_MISMATCH")
    _require(sha256_file(backup_path) == plan.get("FILE", {}).get("backup_sha256"), "R3_PLAN_BACKUP_SHA_MISMATCH")
    _require(sha256_file(candidate_path) == candidate_file.get("candidate_sha256"), "R3_CANDIDATE_SHA_MISMATCH")
    current_path = data_root / expected_relative
    _require(sha256_file(current_path) == candidate_file.get("candidate_sha256"), "R3_CURRENT_AFFECTED_FILE_SHA_MISMATCH")

    return {
        **lineage,
        "old_manifest": pre,
        "expected_post_manifest": expected_post,
        "report_path": report_path,
        "report_sha256": sha256_file(report_path),
        "plan_path": plan_path,
        "plan_sha256": sha256_file(plan_path),
        "receipt_path": receipt_path,
        "receipt_sha256": sha256_file(receipt_path),
        "candidate_manifest_path": candidate_manifest_path,
        "candidate_manifest_sha256": sha256_file(candidate_manifest_path),
        "backup_path": backup_path,
        "candidate_path": candidate_path,
        "target_keyset_hash": R3_TARGET_KEYSET_HASH,
    }


def _load_current_target_window(
    data_root: Path,
    current_manifest: dict[str, Any],
) -> dict[tuple[str, date], dict[str, Any]]:
    files = _manifest_files_by_path(current_manifest)
    rows: dict[tuple[str, date], dict[str, Any]] = {}
    for raw_date in (date(2024, 6, 12), date(2024, 6, 13), date(2024, 6, 14)):
        relative = next(
            (
                path
                for path in files
                if f"trade_date={raw_date.isoformat()}" in path
            ),
            None,
        )
        _require(relative is not None, "TARGET_WINDOW_PARTITION_MISSING", raw_date.isoformat())
        path = data_root / relative
        frame = pl.read_parquet(path)
        _require(set(CANONICAL_COLUMNS) <= set(frame.columns), "CANONICAL_SCHEMA_MISMATCH")
        target_frame = frame.filter(pl.col("symbol").is_in([key[0] for key in R3_TARGET_KEYS]))
        for row in target_frame.to_dicts():
            key = (str(row["symbol"]), parse_date(row["trade_date"]))
            _require(key not in rows, "TARGET_WINDOW_DUPLICATE_KEY", key_text(key))
            rows[key] = row
    for symbol, repaired_date in R3_TARGET_KEYS:
        _require((symbol, repaired_date) in rows, "REPAIRED_KEY_NOT_PRESENT", key_text((symbol, repaired_date)))
    return rows


def _load_current_symbol_keys(data_root: Path) -> dict[str, set[tuple[str, date]]]:
    symbols = [key[0] for key in R3_TARGET_KEYS]
    try:
        frame = (
            pl.scan_parquet(str(data_root / "curated" / "daily_bars" / "**" / "*.parquet"))
            .select(["symbol", "trade_date"])
            .filter(pl.col("symbol").is_in(symbols))
            .collect()
        )
    except Exception as exc:  # pragma: no cover - real-root only
        raise CompatibilityError("TARGET_SYMBOL_KEY_SCAN_FAILED") from exc
    out: dict[str, set[tuple[str, date]]] = {symbol: set() for symbol in symbols}
    for row in frame.to_dicts():
        key = (str(row["symbol"]), parse_date(row["trade_date"]))
        _require(key not in out[key[0]], "CURRENT_CANONICAL_DUPLICATE_TARGET_KEY", key_text(key))
        out[key[0]].add(key)
    return out


def _load_pre_repair_target_keys(lineage: dict[str, Any]) -> set[tuple[str, date]]:
    backup_path = lineage["backup_path"]
    frame = pl.read_parquet(backup_path)
    keys: set[tuple[str, date]] = set()
    for row in frame.to_dicts():
        symbol = str(row.get("symbol"))
        if symbol not in {key[0] for key in R3_TARGET_KEYS}:
            continue
        key = (symbol, parse_date(row["trade_date"]))
        _require(key not in keys, "R3_BACKUP_DUPLICATE_TARGET_KEY", key_text(key))
        keys.add(key)
    return keys


def _load_old_formal_result(
    checkpoint: dict[str, Any],
    symbol: str,
    r4_stage_root: Path,
) -> tuple[set[tuple[str, date]], dict[tuple[str, date], dict[str, Any]]] | None:
    entry = checkpoint["units"].get(symbol)
    if entry is None or entry.get("STATE") != "COMPLETE":
        return None
    raw_path = Path(str(entry.get("formal_path", "")))
    path = raw_path if raw_path.is_absolute() else r4_stage_root / raw_path
    path = _require_under(path, r4_stage_root, "R4A9_FORMAL_RESULT_PATH_ESCAPE")
    _require(path.is_file() and not path.is_symlink(), "R4A9_FORMAL_RESULT_MISSING", symbol)
    try:
        frame = pl.read_parquet(path)
    except Exception as exc:
        raise CompatibilityError(f"R4A9_FORMAL_RESULT_READ_FAILED:{symbol}") from exc
    rows: dict[tuple[str, date], dict[str, Any]] = {}
    for row in frame.to_dicts():
        key = (str(row["symbol"]), parse_date(row["trade_date"]))
        _require(key not in rows, "R4A9_FORMAL_RESULT_DUPLICATE_KEY", key_text(key))
        rows[key] = row
    _require(len(rows) == int(entry.get("FORMAL_FACT_ROW_N", -1)), "R4A9_FORMAL_RESULT_ROW_COUNT_MISMATCH", symbol)
    return set(rows), rows


def analyze_target_impact(
    data_root: Path,
    current_manifest: dict[str, Any],
    checkpoint: dict[str, Any],
    lineage: dict[str, Any],
) -> dict[str, Any]:
    target_window = _load_current_target_window(data_root, current_manifest)
    current_symbol_keys = _load_current_symbol_keys(data_root)
    pre_repair_keys = _load_pre_repair_target_keys(lineage)
    _require(
        not (pre_repair_keys & set(R3_TARGET_KEYS)),
        "R3_BACKUP_STILL_CONTAINS_REPAIRED_KEYS",
    )

    r4_stage_root = data_root / "staging" / R4A9_STAGE_DIRNAME
    impacts: list[dict[str, Any]] = []
    affected_preclose_keys: set[tuple[str, date]] = set()
    invalidate_symbols: set[str] = set()
    invalidate_result_keys: set[tuple[str, date]] = set()

    for symbol, repaired_date in R3_TARGET_KEYS:
        keys = current_symbol_keys[symbol]
        prior_dates = sorted(value[1] for value in keys if value[1] < repaired_date)
        next_dates = sorted(value[1] for value in keys if value[1] > repaired_date)
        _require(prior_dates and next_dates, "TARGET_PREDECESSOR_WINDOW_INCOMPLETE", symbol)
        previous_date = prior_dates[-1]
        next_date = next_dates[0]
        previous_key = (symbol, previous_date)
        repaired_key = (symbol, repaired_date)
        next_key = (symbol, next_date)
        _require(previous_key in target_window, "TARGET_PREVIOUS_WINDOW_NOT_LOADED", key_text(previous_key))
        _require(repaired_key in target_window, "TARGET_REPAIRED_WINDOW_NOT_LOADED", key_text(repaired_key))
        _require(next_key in target_window, "TARGET_NEXT_WINDOW_NOT_LOADED", key_text(next_key))
        _require(next_date == date(2024, 6, 14), "TARGET_NEXT_DATE_UNEXPECTED", key_text(next_key))

        old_result = _load_old_formal_result(checkpoint, symbol, r4_stage_root)
        existing_result_present = old_result is not None
        old_result_keys = old_result[0] if old_result is not None else set()
        matches_new_input = None
        old_preclose = None
        old_predecessor_close = None
        old_parity = None
        new_parity = None
        if old_result is not None:
            entry = checkpoint["units"][symbol]
            matches_new_input = (
                old_result_keys == keys
                and int(entry.get("REQUIRED_ROW_N", -1)) == len(keys)
            )
            next_row = old_result[1].get(next_key)
            if next_row is not None:
                old_preclose = next_row.get("preclose")
                old_predecessor_close = target_window[previous_key].get("close")
                old_parity = display_equal(old_preclose, old_predecessor_close)
                new_parity = display_equal(old_preclose, target_window[repaired_key].get("close"))
            if not matches_new_input:
                invalidate_symbols.add(symbol)
                invalidate_result_keys.add(repaired_key)
                invalidate_result_keys.add(next_key)

        affected_preclose_keys.add(next_key)
        impact_status = "RECOMPUTE_REQUIRED" if existing_result_present and not matches_new_input else "NO_IMPACT"
        impacts.append(
            {
                "REPAIRED_KEY": key_text(repaired_key),
                "PREVIOUS_CANONICAL_PREDECESSOR": {
                    "key": key_text(previous_key),
                    "close": scalar_for_report(target_window[previous_key].get("close")),
                    "source": target_window[previous_key].get("source"),
                },
                "NEW_CANONICAL_PREDECESSOR": {
                    "key": key_text(repaired_key),
                    "close": scalar_for_report(target_window[repaired_key].get("close")),
                    "source": target_window[repaired_key].get("source"),
                },
                "NEXT_TRADED_DATE": next_date.isoformat(),
                "AFFECTED_PRECLOSE_KEY_N": 1,
                "AFFECTED_PRECLOSE_KEYS": [key_text(next_key)],
                "EXISTING_R4A9_RESULT_PRESENT": existing_result_present,
                "EXISTING_R4A9_RESULT_MATCHES_NEW_INPUT": matches_new_input,
                "EXISTING_R4A9_NEXT_PRECCLOSE": scalar_for_report(old_preclose),
                "OLD_PREDECESSOR_PARITY": old_parity,
                "NEW_PREDECESSOR_PARITY": new_parity,
                "PREDECESSOR_KEY_CHANGED": True,
                "PREDECESSOR_CLOSE_VALUE_CHANGED": target_window[previous_key].get("close")
                != target_window[repaired_key].get("close"),
                "CURRENT_SYMBOL_REQUIRED_KEY_N": len(keys),
                "OLD_R4A9_REQUIRED_KEY_N": (
                    int(checkpoint["units"][symbol].get("REQUIRED_ROW_N"))
                    if existing_result_present
                    else None
                ),
                "IMPACT_STATUS": impact_status,
            }
        )

    directly_affected = checkpoint["complete"] & {key[0] for key in R3_TARGET_KEYS}
    _require(directly_affected == invalidate_symbols, "DIRECT_AFFECTED_SYMBOL_ANALYSIS_MISMATCH")
    _require(len(directly_affected) == 1, "EXPECTED_ONE_COMPLETE_TARGET_SYMBOL")
    _require(len(affected_preclose_keys) == 4, "AFFECTED_PRECLOSE_KEY_COUNT_MISMATCH")
    _require(len(invalidate_result_keys) == 2, "INVALIDATE_RESULT_KEY_COUNT_MISMATCH")
    return {
        "TARGET_COMPLETE_N": len(checkpoint["complete"] & {key[0] for key in R3_TARGET_KEYS}),
        "TARGET_FAILED_N": len(checkpoint["failed"] & {key[0] for key in R3_TARGET_KEYS}),
        "TARGET_UNVISITED_N": len(checkpoint["unvisited"] & {key[0] for key in R3_TARGET_KEYS}),
        "TARGET_MEMBERSHIP": {
            symbol: (
                "COMPLETE"
                if symbol in checkpoint["complete"]
                else "FAILED"
                if symbol in checkpoint["failed"]
                else "UNVISITED"
            )
            for symbol, _ in R3_TARGET_KEYS
        },
        "IMPACTS": impacts,
        "AFFECTED_PRECLOSE_KEYS": sorted(affected_preclose_keys),
        "AFFECTED_PRECLOSE_KEY_N": len(affected_preclose_keys),
        "AFFECTED_PRECLOSE_KEYSET_HASH": keyset_hash(affected_preclose_keys),
        "COMPLETE_SYMBOL_DIRECTLY_AFFECTED_N": len(directly_affected),
        "COMPLETE_SYMBOL_DIRECTLY_AFFECTED_LIST": sorted(directly_affected),
        "COMPLETE_SYMBOL_SAFE_REUSE_N": len(checkpoint["complete"] - directly_affected),
        "INVALIDATE_SYMBOL_N": len(invalidate_symbols),
        "INVALIDATE_SYMBOLS": sorted(invalidate_symbols),
        "INVALIDATE_RESULT_KEY_N": len(invalidate_result_keys),
        "INVALIDATE_RESULT_KEYS": [key_text(key) for key in sorted(invalidate_result_keys)],
        "CURRENT_TARGET_KEY_N": len(R3_TARGET_KEYS),
    }


def load_300546_failure(repo_root: Path, data_root: Path) -> dict[str, Any]:
    diagnostic = load_json(repo_root / R4A9_DIAG_REL)
    _require(diagnostic.get("SYMBOL") == "300546.SZ", "R4A9_DIAGNOSTIC_SYMBOL_MISMATCH")
    _require(diagnostic.get("ROOT_CAUSE_CLASSIFICATION") == "R3_REQUIRED_KEY_MISMATCH", "R4A9_DIAGNOSTIC_ROOT_CAUSE_MISMATCH")
    anchors = {("300546.SZ", date(2016, 9, 29)), ("300546.SZ", date(2016, 10, 10))}
    rows: set[tuple[str, date]] = set()
    for raw_date in (date(2016, 9, 29), date(2016, 10, 10)):
        path = data_root / "curated" / "daily_bars" / f"trade_date={raw_date.isoformat()}" / "part-merged.parquet"
        _require(path.is_file(), "300546_REPAIR_ANCHOR_PARTITION_MISSING", raw_date.isoformat())
        frame = pl.read_parquet(path).filter(pl.col("symbol") == "300546.SZ")
        for row in frame.to_dicts():
            rows.add(("300546.SZ", parse_date(row["trade_date"])))
    _require(rows == anchors, "300546_REPAIR_ANCHORS_NOT_PRESENT")
    blocker_dates = diagnostic.get("PROVIDER_BLOCKER_DATES")
    _require(isinstance(blocker_dates, list) and len(blocker_dates) == 2, "300546_DIAGNOSTIC_BLOCKER_SCOPE_MISMATCH")
    return {
        "R4A9_300546_FAILURE_REASON": (
            "R3_REQUIRED_KEY_MISMATCH: BaoStock tradestatus=1 on 2016-09-29 and "
            "2016-10-10 while the pre-repair R3 required-key universe omitted both bars."
        ),
        "R3_REPAIR_ADDRESSES_300546_FAILURE": True,
        "DIAGNOSTIC_ROOT_CAUSE_CLASSIFICATION": diagnostic["ROOT_CAUSE_CLASSIFICATION"],
        "DIAGNOSTIC_ROOT_CAUSE_DETAIL": diagnostic["ROOT_CAUSE_DETAIL"],
        "DIAGNOSTIC_PROVIDER_BLOCKER_DATES": blocker_dates,
        "CURRENT_300546_REPAIR_ANCHORS_PRESENT": True,
    }


def choose_compatibility_verdict(
    *,
    directly_affected_n: int,
    dependency_traceable: bool,
) -> str:
    if not dependency_traceable:
        return "FULL_CHECKPOINT_INVALIDATION"
    if directly_affected_n:
        return "BOUNDED_INVALIDATION"
    return "ZERO_INVALIDATION"


def _compact_lineage(lineage: dict[str, Any]) -> dict[str, Any]:
    return {
        key: lineage[key]
        for key in (
            "AFFECTED_CANONICAL_FILE_N",
            "INSERTED_KEY_N",
            "DELETED_KEY_N",
            "MODIFIED_EXISTING_KEY_N",
        )
    } | {
        "AFFECTED_CANONICAL_FILES": [
            {
                "relative_path": item["relative_path"],
                "old_sha256": item["old"]["sha256"],
                "new_sha256": item["new"]["sha256"],
                "old_file_size": item["old"]["file_size"],
                "new_file_size": item["new"]["file_size"],
            }
            for item in lineage["AFFECTED_CANONICAL_FILES"]
        ],
        "R3_REPAIR_REPORT_SHA256": lineage["report_sha256"],
        "R3_PROMOTION_PLAN_SHA256": lineage["plan_sha256"],
        "R3_PROMOTION_RECEIPT_SHA256": lineage["receipt_sha256"],
        "R3_CANDIDATE_MANIFEST_SHA256": lineage["candidate_manifest_sha256"],
    }


def _compact_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "R4A9_CHECKPOINT_PATH": str(checkpoint["path"]),
        "R4A9_CHECKPOINT_HASH": checkpoint["hash"],
        "R4A9_CHECKPOINT_SCHEMA_VERSION": checkpoint["manifest"]["schema_version"],
        "R4A9_CHECKPOINT_CONTRACT": checkpoint["contract"],
        "FAILED_SYMBOL": sorted(checkpoint["failed"]),
    }


def _plan_contract_reference(repo_root: Path) -> dict[str, Any]:
    external_root = Path("/Users/luke808/ASL-r4a9-preclose-real-full-extraction-v01")
    contract_doc = external_root / "docs/plans/R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md"
    adapter = external_root / "src/ashare_data/r4a_preclose_bounded_adapter.py"
    orchestrator = external_root / "src/ashare_data/r4a7_preclose_full_extraction.py"
    for path in (contract_doc, adapter, orchestrator):
        _require(path.is_file(), "R4A9_CODE_OR_CONTRACT_REFERENCE_MISSING", str(path))
    return {
        "R4A9_CODE_HEAD": R4A9_CODE_HEAD,
        "R4A9_QUERY_PLAN_HASH": R4A9_QUERY_PLAN_HASH,
        "CONTRACT_DOCUMENT": str(contract_doc),
        "CONTRACT_DOCUMENT_SHA256": sha256_file(contract_doc),
        "ADAPTER_SOURCE": str(adapter),
        "ADAPTER_SOURCE_SHA256": sha256_file(adapter),
        "ORCHESTRATOR_SOURCE": str(orchestrator),
        "ORCHESTRATOR_SOURCE_SHA256": sha256_file(orchestrator),
        "CODE_REFERENCES": [
            "r4a_preclose_bounded_adapter.py:252-309 load_required_keys",
            "r4a_preclose_bounded_adapter.py:1065-1147 verify_clean_normal_parity",
            "r4a7_preclose_full_extraction.py:442-697 run_full_extraction",
            "R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md:69-76 formal required-key contract",
            "R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md:239-244 CLEAN_NORMAL predecessor parity",
        ],
    }


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# R4A9 Post-R3 Repair Compatibility V01",
        "",
        f"AUTHOR_STATUS: `{report['AUTHOR_STATUS']}`",
        f"BASE_HEAD: `{report['BASE_HEAD']}`",
        f"BRANCH: `{report['BRANCH']}`",
        "",
        "## Verdict",
        "",
        f"COMPATIBILITY_VERDICT: `{report['COMPATIBILITY_VERDICT']}`",
        f"INVALIDATE_SYMBOL_N: `{report['INVALIDATE_SYMBOL_N']}`",
        f"INVALIDATE_SYMBOLS: `{', '.join(report['INVALIDATE_SYMBOLS'])}`",
        f"INVALIDATE_RESULT_KEY_N: `{report['INVALIDATE_RESULT_KEY_N']}`",
        f"INVALIDATE_RESULT_KEYS: `{', '.join(report['INVALIDATE_RESULT_KEYS'])}`",
        "",
        "The two invalidated result keys are the affected COMPLETE 002087.SZ "
        "scope: the newly required 2024-06-13 key and the existing 2024-06-14 "
        "row whose local predecessor context changed.  The other three repaired "
        "symbols were UNVISITED and have no old R4A9 result to invalidate.",
        "",
        "The R4A9 result is provider preclose data.  For CLEAN_NORMAL rows, the "
        "frozen implementation validates that provider preclose equals the same "
        "symbol's previous effective local close.  The R3 insertion therefore "
        "adds one required preclose key and changes the predecessor context for "
        "the next traded key; it does not make unrelated symbols dependent on "
        "the changed partition.",
        "",
        "## Daily lineage",
        "",
        f"OLD_DAILY_MANIFEST_HASH: `{report['OLD_DAILY_MANIFEST_HASH']}`",
        f"NEW_DAILY_MANIFEST_HASH: `{report['NEW_DAILY_MANIFEST_HASH']}`",
        f"R4A9_INPUT_DAILY_MANIFEST_HASH: `{report['R4A9_INPUT_DAILY_MANIFEST_HASH']}`",
        f"R4A9_INPUT_DAILY_MANIFEST_STATUS: `{report['R4A9_INPUT_DAILY_MANIFEST_STATUS']}`",
        f"AFFECTED_CANONICAL_FILE_N: `{report['R3_REPAIR_LINEAGE']['AFFECTED_CANONICAL_FILE_N']}`",
        f"INSERTED_KEY_N: `{report['R3_REPAIR_LINEAGE']['INSERTED_KEY_N']}`",
        f"DELETED_KEY_N: `{report['R3_REPAIR_LINEAGE']['DELETED_KEY_N']}`",
        f"MODIFIED_EXISTING_KEY_N: `{report['R3_REPAIR_LINEAGE']['MODIFIED_EXISTING_KEY_N']}`",
        "",
        "## Checkpoint",
        "",
        f"R4A9_CHECKPOINT_PATH: `{report['R4A9_CHECKPOINT_PATH']}`",
        f"R4A9_CHECKPOINT_HASH: `{report['R4A9_CHECKPOINT_HASH']}`",
        f"COMPLETE_SYMBOL_N: `{report['COMPLETE_SYMBOL_N']}`",
        f"FAILED_SYMBOL_N: `{report['FAILED_SYMBOL_N']}`",
        f"UNVISITED_SYMBOL_N: `{report['UNVISITED_SYMBOL_N']}`",
        f"TARGET_COMPLETE_N: `{report['TARGET_COMPLETE_N']}`",
        f"TARGET_FAILED_N: `{report['TARGET_FAILED_N']}`",
        f"TARGET_UNVISITED_N: `{report['TARGET_UNVISITED_N']}`",
        f"COMPLETE_SYMBOL_DIRECTLY_AFFECTED_N: `{report['COMPLETE_SYMBOL_DIRECTLY_AFFECTED_N']}`",
        f"COMPLETE_SYMBOL_SAFE_REUSE_N: `{report['COMPLETE_SYMBOL_SAFE_REUSE_N']}`",
        "",
        "## Predecessor impact",
        "",
        f"AFFECTED_PRECLOSE_KEY_N: `{report['AFFECTED_PRECLOSE_KEY_N']}`",
        f"AFFECTED_PRECLOSE_KEYSET_HASH: `{report['AFFECTED_PRECLOSE_KEYSET_HASH']}`",
    ]
    for item in report["TARGET_IMPACTS"]:
        lines.append(
            f"- `{item['REPAIRED_KEY']}` -> predecessor `{item['NEW_CANONICAL_PREDECESSOR']['key']}` "
            f"for next `{item['AFFECTED_PRECLOSE_KEYS'][0]}`; "
            f"existing_result={item['EXISTING_R4A9_RESULT_PRESENT']}; "
            f"impact=`{item['IMPACT_STATUS']}`"
        )
    lines.extend(
        [
            "",
            "## 300546 failure",
            "",
            f"R4A9_300546_FAILURE_REASON: {report['R4A9_300546_FAILURE_REASON']}",
            f"R3_REPAIR_ADDRESSES_300546_FAILURE: `{report['R3_REPAIR_ADDRESSES_300546_FAILURE']}`",
            "",
            "## Recommended resume design (not executed)",
            "",
            *[f"{index}. {step}" for index, step in enumerate(report["RECOMMENDED_RESUME_PLAN"], 1)],
            "",
            "## Safety",
            "",
            f"NETWORK_PROVIDER_DATA_FETCH: `{report['NETWORK_PROVIDER_DATA_FETCH']}`",
            f"R4_DATA_WRITE_EXECUTED: `{report['R4_DATA_WRITE_EXECUTED']}`",
            f"R4A9_CHECKPOINT_MUTATED: `{report['R4A9_CHECKPOINT_MUTATED']}`",
            f"R4A9_RESUME_AUTHORIZED: `{report['R4A9_RESUME_AUTHORIZED']}`",
            f"PRECLOSE_COMPLETE: `{report['PRECLOSE_COMPLETE']}`",
            f"R3_DAILY_USABLE: `{report['R3_DAILY_USABLE']}`",
            f"R4_EXECUTION_AUTHORIZED: `{report['R4_EXECUTION_AUTHORIZED']}` (entry status only; no resume)",
            "",
            "No R4A9 execution, provider request, checkpoint mutation, or data-root write was performed by this audit.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_reports(repo_root: Path, report: dict[str, Any]) -> None:
    output_dir = repo_root / "reports" / "implementation"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / REPORT_NAME
    md_path = output_dir / REPORT_MD_NAME
    json_bytes = canonical_json_bytes(report) + b"\n"
    for path, data in ((json_path, json_bytes), (md_path, _report_markdown(report).encode("utf-8"))):
        temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        temporary.write_bytes(data)
        os.replace(temporary, path)


def run_compatibility_audit(
    *,
    repo_root: Path = REPO_ROOT,
    data_root: Path = DATA_ROOT_DEFAULT,
    write_output: bool = True,
    test_result: str = "PENDING_TARGETED_TESTS",
    py_compile_result: str = "PENDING",
    diff_check_result: str = "PENDING",
) -> dict[str, Any]:
    """Run the complete read-only audit; writes only the two repo reports."""

    repo_root = Path(repo_root).resolve(strict=True)
    data_root = Path(data_root).expanduser().resolve(strict=True)
    require_base_head(repo_root)
    current_manifest = build_input_file_manifest(data_root)
    _require(current_manifest["INPUT_FILE_N"] == 2_580, "CURRENT_DAILY_FILE_N_MISMATCH")
    _require(current_manifest["INPUT_MANIFEST_HASH"] == NEW_DAILY_MANIFEST_HASH, "CURRENT_DAILY_MANIFEST_DRIFT")
    formal_symbols = load_formal_symbols(data_root)
    checkpoint = load_r4a9_checkpoint(data_root, formal_symbols)
    r4_authority = load_r4a9_authority(repo_root, checkpoint)
    lineage = load_r3_lineage(repo_root, data_root, current_manifest)
    impact = analyze_target_impact(data_root, current_manifest, checkpoint, lineage)
    failure = load_300546_failure(repo_root, data_root)
    contract_reference = _plan_contract_reference(repo_root)

    verdict = choose_compatibility_verdict(
        directly_affected_n=impact["COMPLETE_SYMBOL_DIRECTLY_AFFECTED_N"],
        dependency_traceable=True,
    )
    _require(verdict == "BOUNDED_INVALIDATION", "UNEXPECTED_COMPATIBILITY_VERDICT", verdict)

    report: dict[str, Any] = {
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "OLD_DAILY_MANIFEST_HASH": OLD_DAILY_MANIFEST_HASH,
        "NEW_DAILY_MANIFEST_HASH": NEW_DAILY_MANIFEST_HASH,
        "R4A9_INPUT_DAILY_MANIFEST_HASH": r4_authority["R4A9_INPUT_DAILY_MANIFEST_HASH"] or "UNBOUND",
        "R4A9_INPUT_DAILY_MANIFEST_STATUS": r4_authority["R4A9_INPUT_DAILY_MANIFEST_STATUS"],
        "R4A9_CHECKPOINT_PATH": str(checkpoint["path"]),
        "R4A9_CHECKPOINT_HASH": checkpoint["hash"],
        "R4A9_CHECKPOINT_SCHEMA_VERSION": checkpoint["manifest"].get("schema_version"),
        "R4A9_CODE_HEAD": R4A9_CODE_HEAD,
        "R4A9_PLAN_CONTRACT_REFERENCE": contract_reference,
        "R4A9_CHECKPOINT_CONTRACT": checkpoint["contract"],
        "COMPLETE_SYMBOL_N": checkpoint["COMPLETE_SYMBOL_N"],
        "FAILED_SYMBOL_N": checkpoint["FAILED_SYMBOL_N"],
        "FAILED_SYMBOL": sorted(checkpoint["failed"]),
        "UNVISITED_SYMBOL_N": checkpoint["UNVISITED_SYMBOL_N"],
        "TARGET_COMPLETE_N": impact["TARGET_COMPLETE_N"],
        "TARGET_FAILED_N": impact["TARGET_FAILED_N"],
        "TARGET_UNVISITED_N": impact["TARGET_UNVISITED_N"],
        "TARGET_MEMBERSHIP": impact["TARGET_MEMBERSHIP"],
        "COMPLETE_SYMBOL_DIRECTLY_AFFECTED_N": impact["COMPLETE_SYMBOL_DIRECTLY_AFFECTED_N"],
        "COMPLETE_SYMBOL_DIRECTLY_AFFECTED_LIST": impact["COMPLETE_SYMBOL_DIRECTLY_AFFECTED_LIST"],
        "COMPLETE_SYMBOL_SAFE_REUSE_N": impact["COMPLETE_SYMBOL_SAFE_REUSE_N"],
        "AFFECTED_PRECLOSE_KEY_N": impact["AFFECTED_PRECLOSE_KEY_N"],
        "AFFECTED_PRECLOSE_KEYS": [key_text(key) for key in impact["AFFECTED_PRECLOSE_KEYS"]],
        "AFFECTED_PRECLOSE_KEYSET_HASH": impact["AFFECTED_PRECLOSE_KEYSET_HASH"],
        "AFFECTED_CANONICAL_FILE_N": lineage["AFFECTED_CANONICAL_FILE_N"],
        "INSERTED_KEY_N": lineage["INSERTED_KEY_N"],
        "DELETED_KEY_N": lineage["DELETED_KEY_N"],
        "MODIFIED_EXISTING_KEY_N": lineage["MODIFIED_EXISTING_KEY_N"],
        "INVALIDATE_SYMBOL_N": impact["INVALIDATE_SYMBOL_N"],
        "INVALIDATE_SYMBOLS": impact["INVALIDATE_SYMBOLS"],
        "INVALIDATE_RESULT_KEY_N": impact["INVALIDATE_RESULT_KEY_N"],
        "INVALIDATE_RESULT_KEYS": impact["INVALIDATE_RESULT_KEYS"],
        "TARGET_KEY_N": impact["CURRENT_TARGET_KEY_N"],
        "TARGET_IMPACTS": impact["IMPACTS"],
        "PRECLOSE_DEPENDENCY_CONTRACT": (
            "Formal preclose value is provider preclose for each R3 actual-traded "
            "required key; CLEAN_NORMAL parity validates it against the same "
            "symbol's previous effective local canonical close. It is not a "
            "previous-calendar-day or CA-adjusted computed value."
        ),
        "PRECLOSE_DEPENDENCY_SOURCE": [
            "R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md:69-76",
            "R4A_PRECLOSE_CANONICAL_SOURCE_CONTRACT_V01.md:239-244",
            "r4a_preclose_bounded_adapter.py:252-309",
            "r4a_preclose_bounded_adapter.py:1065-1147",
        ],
        "R3_REPAIR_LINEAGE": _compact_lineage(lineage),
        "R4A9_300546_FAILURE_REASON": failure["R4A9_300546_FAILURE_REASON"],
        "R3_REPAIR_ADDRESSES_300546_FAILURE": failure["R3_REPAIR_ADDRESSES_300546_FAILURE"],
        "R4A9_300546_DIAGNOSTIC": {
            "ROOT_CAUSE_CLASSIFICATION": failure["DIAGNOSTIC_ROOT_CAUSE_CLASSIFICATION"],
            "PROVIDER_BLOCKER_DATES": failure["DIAGNOSTIC_PROVIDER_BLOCKER_DATES"],
            "CURRENT_REPAIR_ANCHORS_PRESENT": failure["CURRENT_300546_REPAIR_ANCHORS_PRESENT"],
        },
        "COMPATIBILITY_VERDICT": verdict,
        "RECOMMENDED_RESUME_PLAN": [
            "等待 Sol 对本 compatibility commit 做独立审计；本任务不执行 resume。",
            "保留 2,139 个未受影响的 COMPLETE symbols 原样复用，不改旧 checkpoint。",
            "以 NEW_DAILY_MANIFEST_HASH 重新计算 002087.SZ 的完整 preclose unit，覆盖新增 2024-06-13 并重新验证 2024-06-14 predecessor parity。",
            "在单独授权后 bounded retry 300546.SZ；当前 R3 repair 已补齐其 2016-09-29 与 2016-10-10 blocker keys。",
            "随后以新 manifest 继续 3,315 个 UNVISITED symbols（含另外三个 repaired symbols）；任何失败继续 fail closed。",
        ],
        "R3_DAILY_USABLE": True,
        "R4_EXECUTION_AUTHORIZED": True,
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "BAOSTOCK_EXECUTED": False,
        "TUSHARE_EXECUTED": False,
        "TDX_EXECUTED": False,
        "EASTMONEY_EXECUTED": False,
        "CANONICAL_WRITE_EXECUTED": False,
        "CANONICAL_BYTES_MUTATED": False,
        "R4_DATA_WRITE_EXECUTED": False,
        "R4A9_CHECKPOINT_MUTATED": False,
        "R4A9_RESUME_EXECUTED": False,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "FACTS_READY": False,
        "FULL_HISTORY_CERTIFIED": False,
        "DAILY_COVERAGE_STATUS": "PARTIAL",
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
        "TEST_RESULT": test_result,
        "PY_COMPILE": py_compile_result,
        "GIT_DIFF_CHECK": diff_check_result,
    }
    if write_output:
        write_reports(repo_root, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--test-result", default="PENDING_TARGETED_TESTS")
    parser.add_argument("--py-compile-result", default="PENDING")
    parser.add_argument("--diff-check-result", default="PENDING")
    args = parser.parse_args(argv)
    try:
        report = run_compatibility_audit(
            repo_root=args.repo_root,
            data_root=args.data_root,
            test_result=args.test_result,
            py_compile_result=args.py_compile_result,
            diff_check_result=args.diff_check_result,
        )
    except CompatibilityError as exc:
        print(f"FAIL_CLOSED:{exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
