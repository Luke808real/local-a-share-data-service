#!/usr/bin/env python3
"""Build a lineage-safe R4A9 resume checkpoint after the R3 daily repair.

This tool is deliberately offline.  It reads the frozen compatibility
evidence, the old R4A9 checkpoint, the current R3 daily input, and the old
formal artifacts.  It writes only a new checkpoint/receipt under the exact
post-R3 staging root and compact repository reports.  It never imports a
provider, runs R4A9, or writes canonical data.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

TASK = "R4A9_LINEAGE_SAFE_CHECKPOINT_REBASE_V01"
BASE_HEAD = "baeedc4bb17ddbd4d04b53df29bd434a2808ca0e"
BRANCH = "codex/r4a9-lineage-safe-checkpoint-rebase-v01"

OLD_DAILY_INPUT_MANIFEST_HASH = (
    "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
)
CURRENT_DAILY_INPUT_MANIFEST_HASH = (
    "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"
)
FORMAL_IDENTITY_N = 5_456
FORMAL_IDENTITY_HASH = (
    "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
)

COMPATIBILITY_REPORT_REL = Path(
    "reports/implementation/R4A9_POST_R3_REPAIR_COMPATIBILITY_V01.json"
)
COMPATIBILITY_REPORT_SHA256 = (
    "7beaf08f61ed4132bc9be06fe7acdb1e8191d8e6e5a6b6b39451f9b5b2042a40"
)
COMPATIBILITY_VERDICT = "BOUNDED_INVALIDATION"

OLD_R4A9_STAGE_DIRNAME = "r4a9-preclose-real-full-extraction-v01"
OLD_CHECKPOINT_NAME = "manifest.json"
OLD_CHECKPOINT_SHA256 = (
    "d09c53dbca0b49ecb070b01c2740d1f122e9a327bfb9f7bdabca8739d4c17d6e"
)
NEW_R4A9_STAGE_DIRNAME = "r4a9-preclose-resume-post-r3-repair-v01"
NEW_CHECKPOINT_SCHEMA_VERSION = "R4A9_LINEAGE_SAFE_RESUME_V01"

R4A9_CODE_HEAD = "795b1b8f6b688ecc2e94f85c09d80c365e648920"
R4A9_QUERY_PLAN_HASH = (
    "9773875fbae9494bc1d9477cd18633dbccb92112733d9a1077fc3a43bcc38a60"
)
R4A9_QUERY_CONTRACT_VERSION = "R4A_PRECLOSE_V01"
R4A9_SOURCE_VERSION = "baostock-0.9.3"

R3_REPAIR_REPORT_REL = Path("reports/implementation/R3_PROVEN_MISSING_4KEY_REPAIR_V01.json")
R3_REPAIR_REPORT_SHA256 = (
    "b72b099b0c359158c5cd8829c248239370c8491b874c3c850bb99f34e5f86206"
)
R4A9_DIAGNOSTIC_REL = Path(
    "reports/implementation/R4A9_1_300546_PRECLOSE_QUALITY_DIAGNOSIS_V01.json"
)
R4A9_DIAGNOSTIC_SHA256 = (
    "ab5953efb713f0a7ef1bb452e21d8363dcf1999238d64570d6abf4cda6c8ac36"
)

R3_REPAIR_RESULT_KEYS = [
    "002087.SZ:2024-06-13",
    "002087.SZ:2024-06-14",
]
R3_REPAIR_300546_KEYS = [
    "300546.SZ:2016-09-29",
    "300546.SZ:2016-10-10",
]

FORMAL_COLUMNS = [
    "symbol",
    "trade_date",
    "preclose",
    "source",
    "source_version",
    "adapter_version",
    "query_contract_version",
    "fetched_at",
    "provider_tradestatus",
    "coverage_status",
]

EXPECTED_OLD_CONTRACT = {
    "MANIFEST_SCHEMA_VERSION": "R4A7_PRECLOSE_V01",
    "AS_OF": "2026-08-17",
    "WINDOW_START": "2016-01-01",
    "PRIMARY_SOURCE": "BAOSTOCK_HISTORY_K_PRECLOSE",
    "SOURCE_VERSION": R4A9_SOURCE_VERSION,
    "QUERY_CONTRACT_VERSION": R4A9_QUERY_CONTRACT_VERSION,
    "QUERY_FIELDS": "date,code,preclose,tradestatus",
    "QUERY_FREQUENCY": "d",
    "QUERY_ADJUSTFLAG": "3",
    "CNEQUITY_PIN": "a18ee0484dfb0801650175471724def3228b8a17",
    "FORMAL_IDENTITY_N": FORMAL_IDENTITY_N,
    "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
    "ADAPTER_AUTHORITY_SHA": R4A9_CODE_HEAD,
    "FULL_QUERY_PLAN_HASH": R4A9_QUERY_PLAN_HASH,
}

QUALITY_COUNTER_FIELDS = (
    "MISSING_REQUIRED_N",
    "UNEXPECTED_TRADED_N",
    "TRADESTATUS_UNKNOWN_N",
    "IDENTITY_FAILURE_N",
    "WINDOW_SCOPE_FAILURE_N",
    "DUPLICATE_N",
    "POST_ASOF_N",
    "INVALID_PRECLOSE_N",
)

REPORT_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"


class RebaseError(RuntimeError):
    """A terminal fail-closed error."""


def _require(condition: bool, code: str, detail: Any | None = None) -> None:
    if not condition:
        message = code if detail is None else f"{code}:{detail}"
        raise RebaseError(message)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
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
    """Hash a file and reject a file that changes while it is being read."""

    try:
        before = _file_identity(path)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        after = _file_identity(path)
    except OSError as exc:
        raise RebaseError(f"LOCAL_FILE_READ_FAILED:{path}") from exc
    _require(before == after, "LOCAL_FILE_MUTATED_DURING_READ", str(path))
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RebaseError(f"LOCAL_JSON_READ_FAILED:{path}") from exc


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise RebaseError(f"INVALID_DATE:{value!r}") from exc


def _resolve_root(path: Path, code: str) -> Path:
    candidate = Path(path).expanduser()
    _require(candidate.is_absolute(), "PATH_NOT_ABSOLUTE", str(candidate))
    _require(candidate.is_dir() and not candidate.is_symlink(), code, str(candidate))
    try:
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise RebaseError(f"{code}:{candidate}") from exc


def _require_under(path: Path, root: Path, code: str) -> Path:
    resolved_root = root.resolve(strict=True)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise RebaseError(f"{code}:{candidate}") from exc
    _require(
        resolved == resolved_root or resolved_root in resolved.parents,
        code,
        str(candidate),
    )
    return resolved


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
        raise RebaseError("GIT_HEAD_UNAVAILABLE") from exc
    _require(current == BASE_HEAD, "BASE_HEAD_MISMATCH", current)


def require_isolated_stage_root(
    data_root: Path,
    stage_root: Path,
    *,
    allow_existing: bool = False,
) -> Path:
    """Require the exact post-R3 stage path before any write is possible."""

    root = _resolve_root(data_root, "DATA_ROOT_INVALID")
    staging_parent = root / "staging"
    _require(
        staging_parent.is_dir() and not staging_parent.is_symlink(),
        "STAGING_PARENT_INVALID",
        str(staging_parent),
    )
    requested = Path(stage_root).expanduser()
    _require(requested.is_absolute(), "STAGE_ROOT_NOT_ABSOLUTE", str(requested))
    expected = root / "staging" / NEW_R4A9_STAGE_DIRNAME
    try:
        resolved_requested = requested.resolve(strict=False)
    except OSError as exc:
        raise RebaseError(f"STAGE_ROOT_RESOLVE_FAILED:{requested}") from exc
    _require(resolved_requested == expected, "ISOLATED_STAGE_ROOT_MISMATCH", str(requested))
    if requested.exists():
        _require(not requested.is_symlink(), "ISOLATED_STAGE_ROOT_SYMLINK", str(requested))
        _require(requested.is_dir(), "ISOLATED_STAGE_ROOT_NOT_DIRECTORY", str(requested))
        _require(allow_existing, "NEW_CHECKPOINT_ROOT_ALREADY_EXISTS", str(requested))
    return expected


def build_input_file_manifest(data_root: Path) -> dict[str, Any]:
    """Recompute the current canonical daily-bar manifest without writing."""

    root = _resolve_root(data_root, "DATA_ROOT_INVALID")
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
    rows.sort(key=lambda item: item["relative_path"])
    return {
        "INPUT_FILE_N": len(rows),
        "INPUT_MANIFEST_HASH": sha256_bytes(canonical_json_bytes(rows)),
        "FILES": rows,
    }


def load_formal_symbols(data_root: Path) -> set[str]:
    root = _resolve_root(data_root, "DATA_ROOT_INVALID")
    glob = str(root / "curated" / "daily_bars" / "**" / "*.parquet")
    try:
        frame = pl.scan_parquet(glob).select("symbol").unique().collect()
    except Exception as exc:  # pragma: no cover - real-root gate
        raise RebaseError("FORMAL_SYMBOL_SCAN_FAILED") from exc
    symbols = {str(value) for value in frame.get_column("symbol").to_list()}
    _require(len(symbols) == FORMAL_IDENTITY_N, "FORMAL_SYMBOL_N_MISMATCH", len(symbols))
    _require(all(symbol.endswith((".SH", ".SZ")) for symbol in symbols), "FORMAL_SYMBOL_SCOPE_MISMATCH")
    actual = sha256_bytes(canonical_json_bytes(sorted(symbols)))
    _require(actual == FORMAL_IDENTITY_HASH, "FORMAL_IDENTITY_HASH_MISMATCH", actual)
    return symbols


def load_current_symbol_dates(data_root: Path, symbols: set[str]) -> dict[str, set[date]]:
    root = _resolve_root(data_root, "DATA_ROOT_INVALID")
    try:
        frame = (
            pl.scan_parquet(str(root / "curated" / "daily_bars" / "**" / "*.parquet"))
            .select(["symbol", "trade_date"])
            .filter(pl.col("symbol").is_in(sorted(symbols)))
            .collect()
        )
    except Exception as exc:  # pragma: no cover - real-root gate
        raise RebaseError("CURRENT_SYMBOL_SCAN_FAILED") from exc
    output = {symbol: set() for symbol in symbols}
    for row in frame.to_dicts():
        symbol = str(row["symbol"])
        trade_day = parse_date(row["trade_date"])
        _require((symbol in output) and (trade_day not in output[symbol]), "CURRENT_CANONICAL_DUPLICATE_KEY", f"{symbol}:{trade_day}")
        output[symbol].add(trade_day)
    return output


def formal_fact_hash(rows: Iterable[dict[str, Any]]) -> str:
    tuples = sorted(
        (str(row["symbol"]), parse_date(row["trade_date"]).isoformat(), round(float(row["preclose"]), 4))
        for row in rows
    )
    return hashlib.sha256(json.dumps(tuples, separators=(",", ":")).encode()).hexdigest()


def staged_formal_content_hash(rows: Iterable[dict[str, Any]]) -> str:
    def format_value(value: Any) -> Any:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

    payload = [
        [format_value(row.get(column)) for column in FORMAL_COLUMNS]
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_compatibility_report(repo_root: Path) -> tuple[dict[str, Any], str]:
    path = repo_root / COMPATIBILITY_REPORT_REL
    _require(path.is_file() and not path.is_symlink(), "COMPATIBILITY_REPORT_MISSING", str(path))
    actual_sha = sha256_file(path)
    _require(actual_sha == COMPATIBILITY_REPORT_SHA256, "COMPATIBILITY_REPORT_HASH_MISMATCH", actual_sha)
    report = load_json(path)
    _require(report.get("COMPATIBILITY_VERDICT") == COMPATIBILITY_VERDICT, "COMPATIBILITY_VERDICT_MISMATCH")
    _require(report.get("NEW_DAILY_MANIFEST_HASH") == CURRENT_DAILY_INPUT_MANIFEST_HASH, "COMPATIBILITY_CURRENT_DAILY_HASH_MISMATCH")
    _require(report.get("OLD_DAILY_MANIFEST_HASH") == OLD_DAILY_INPUT_MANIFEST_HASH, "COMPATIBILITY_OLD_DAILY_HASH_MISMATCH")
    _require(report.get("R4A9_CHECKPOINT_HASH") == OLD_CHECKPOINT_SHA256, "COMPATIBILITY_CHECKPOINT_HASH_MISMATCH")
    _require(report.get("COMPLETE_SYMBOL_N") == 2_140, "COMPATIBILITY_COMPLETE_COUNT_MISMATCH")
    _require(report.get("FAILED_SYMBOL") == ["300546.SZ"], "COMPATIBILITY_FAILED_SCOPE_MISMATCH")
    _require(report.get("COMPLETE_SYMBOL_DIRECTLY_AFFECTED_LIST") == ["002087.SZ"], "COMPATIBILITY_AFFECTED_SCOPE_MISMATCH")
    _require(report.get("COMPLETE_SYMBOL_SAFE_REUSE_N") == 2_139, "COMPATIBILITY_SAFE_REUSE_COUNT_MISMATCH")
    _require(report.get("INVALIDATE_RESULT_KEYS") == R3_REPAIR_RESULT_KEYS, "COMPATIBILITY_INVALIDATE_KEYS_MISMATCH")
    _require(report.get("R3_REPAIR_ADDRESSES_300546_FAILURE") is True, "COMPATIBILITY_300546_REPAIR_FLAG_MISSING")
    return report, actual_sha


def load_old_checkpoint(
    data_root: Path,
    formal_symbols: set[str],
) -> dict[str, Any]:
    root = _resolve_root(data_root, "DATA_ROOT_INVALID")
    stage_root = root / "staging" / OLD_R4A9_STAGE_DIRNAME
    _require(stage_root.is_dir() and not stage_root.is_symlink(), "OLD_R4A9_STAGE_ROOT_INVALID", str(stage_root))
    checkpoint_path = stage_root / OLD_CHECKPOINT_NAME
    _require(checkpoint_path.is_file() and not checkpoint_path.is_symlink(), "OLD_CHECKPOINT_MISSING", str(checkpoint_path))
    checkpoint_hash = sha256_file(checkpoint_path)
    _require(checkpoint_hash == OLD_CHECKPOINT_SHA256, "OLD_CHECKPOINT_HASH_MISMATCH", checkpoint_hash)
    checkpoint = load_json(checkpoint_path)
    _require(checkpoint.get("schema_version") == "R4A7_PRECLOSE_V01", "OLD_CHECKPOINT_SCHEMA_MISMATCH")
    _require(checkpoint.get("contract") == EXPECTED_OLD_CONTRACT, "OLD_CHECKPOINT_CONTRACT_MISMATCH")
    units = checkpoint.get("units")
    _require(isinstance(units, dict), "OLD_CHECKPOINT_UNITS_INVALID")
    _require(set(units) <= formal_symbols, "OLD_CHECKPOINT_SYMBOL_SCOPE_MISMATCH")
    for symbol, entry in units.items():
        _require(isinstance(entry, dict), "OLD_CHECKPOINT_ENTRY_INVALID", symbol)
        _require(entry.get("symbol") == symbol, "OLD_CHECKPOINT_SYMBOL_KEY_MISMATCH", symbol)
        _require(entry.get("STATE") in {"COMPLETE", "FAILED"}, "OLD_CHECKPOINT_STATE_INVALID", symbol)
    complete = {symbol for symbol, entry in units.items() if entry.get("STATE") == "COMPLETE"}
    failed = {symbol for symbol, entry in units.items() if entry.get("STATE") == "FAILED"}
    unvisited = formal_symbols - set(units)
    _require(complete == set(units) - failed, "OLD_CHECKPOINT_PARTITION_INVALID")
    _require(complete == set(units) - failed, "OLD_CHECKPOINT_COMPLETE_FAILED_PARTITION_INVALID")
    _require(len(complete) == 2_140, "OLD_CHECKPOINT_COMPLETE_COUNT_MISMATCH", len(complete))
    _require(failed == {"300546.SZ"}, "OLD_CHECKPOINT_FAILED_SCOPE_MISMATCH", sorted(failed))
    _require(len(unvisited) == 3_315, "OLD_CHECKPOINT_UNVISITED_COUNT_MISMATCH", len(unvisited))
    return {
        "path": checkpoint_path,
        "stage_root": stage_root,
        "hash": checkpoint_hash,
        "manifest": checkpoint,
        "contract": checkpoint["contract"],
        "units": units,
        "complete": complete,
        "failed": failed,
        "unvisited": unvisited,
    }


def load_diagnostic(repo_root: Path) -> tuple[dict[str, Any], str]:
    path = repo_root / R4A9_DIAGNOSTIC_REL
    _require(path.is_file() and not path.is_symlink(), "300546_DIAGNOSTIC_MISSING", str(path))
    actual_sha = sha256_file(path)
    _require(actual_sha == R4A9_DIAGNOSTIC_SHA256, "300546_DIAGNOSTIC_HASH_MISMATCH", actual_sha)
    diagnostic = load_json(path)
    _require(diagnostic.get("SYMBOL") == "300546.SZ", "300546_DIAGNOSTIC_SYMBOL_MISMATCH")
    _require(diagnostic.get("ROOT_CAUSE_CLASSIFICATION") == "R3_REQUIRED_KEY_MISMATCH", "300546_DIAGNOSTIC_ROOT_CAUSE_MISMATCH")
    return diagnostic, actual_sha


def load_r3_repair_report(repo_root: Path) -> tuple[dict[str, Any], str]:
    path = repo_root / R3_REPAIR_REPORT_REL
    _require(path.is_file() and not path.is_symlink(), "R3_REPAIR_REPORT_MISSING", str(path))
    actual_sha = sha256_file(path)
    _require(actual_sha == R3_REPAIR_REPORT_SHA256, "R3_REPAIR_REPORT_HASH_MISMATCH", actual_sha)
    report = load_json(path)
    _require(report.get("POST_INPUT_MANIFEST_HASH") == CURRENT_DAILY_INPUT_MANIFEST_HASH, "R3_REPAIR_POST_HASH_MISMATCH")
    _require(report.get("INSERTED_KEY_N") == 4, "R3_REPAIR_INSERTED_COUNT_MISMATCH")
    _require(report.get("MODIFIED_EXISTING_KEY_N") == 0, "R3_REPAIR_MODIFIED_COUNT_MISMATCH")
    return report, actual_sha


def verify_formal_artifact(
    entry: dict[str, Any],
    *,
    expected_symbol: str,
    old_stage_root: Path,
) -> dict[str, Any]:
    """Revalidate one old formal artifact with the original R4 hashes."""

    raw_path = Path(str(entry.get("formal_path", ""))).expanduser()
    path = _require_under(raw_path, old_stage_root, "FORMAL_ARTIFACT_PATH_ESCAPE")
    _require(path.is_file() and not path.is_symlink(), "FORMAL_ARTIFACT_MISSING", expected_symbol)
    for field in QUALITY_COUNTER_FIELDS:
        _require(entry.get(field) == 0, "SAFE_FORMAL_QUALITY_COUNTER_NONZERO", f"{expected_symbol}:{field}")
    _require(entry.get("adapter_version") == R4A9_CODE_HEAD, "FORMAL_ARTIFACT_ADAPTER_MISMATCH", expected_symbol)
    _require(entry.get("contract") == EXPECTED_OLD_CONTRACT, "FORMAL_ARTIFACT_CONTRACT_MISMATCH", expected_symbol)
    try:
        frame = pl.read_parquet(path)
        rows = frame.to_dicts()
    except Exception as exc:
        raise RebaseError(f"FORMAL_ARTIFACT_READ_FAILED:{expected_symbol}") from exc
    _require(frame.columns == FORMAL_COLUMNS, "FORMAL_ARTIFACT_SCHEMA_MISMATCH", expected_symbol)
    keys: set[tuple[str, date]] = set()
    for row in rows:
        symbol = str(row.get("symbol"))
        trade_day = parse_date(row.get("trade_date"))
        key = (symbol, trade_day)
        _require(key not in keys, "FORMAL_ARTIFACT_DUPLICATE_KEY", f"{symbol}:{trade_day}")
        keys.add(key)
        _require(symbol == expected_symbol, "FORMAL_ARTIFACT_SYMBOL_MISMATCH", expected_symbol)
        _require(trade_day <= date(2026, 8, 17), "FORMAL_ARTIFACT_POST_ASOF", expected_symbol)
        try:
            preclose = float(row.get("preclose"))
        except (TypeError, ValueError) as exc:
            raise RebaseError(f"FORMAL_ARTIFACT_INVALID_PRECLOSE:{expected_symbol}") from exc
        _require(math.isfinite(preclose) and preclose > 0, "FORMAL_ARTIFACT_INVALID_PRECLOSE", expected_symbol)
        _require(row.get("provider_tradestatus") == 1, "FORMAL_ARTIFACT_TRADESTATUS_MISMATCH", expected_symbol)
        _require(row.get("coverage_status") == "COVERED", "FORMAL_ARTIFACT_COVERAGE_MISMATCH", expected_symbol)
        _require(row.get("source") == "BAOSTOCK_HISTORY_K_PRECLOSE", "FORMAL_ARTIFACT_SOURCE_MISMATCH", expected_symbol)
        _require(row.get("source_version") == R4A9_SOURCE_VERSION, "FORMAL_ARTIFACT_SOURCE_VERSION_MISMATCH", expected_symbol)
        _require(row.get("query_contract_version") == R4A9_QUERY_CONTRACT_VERSION, "FORMAL_ARTIFACT_QUERY_CONTRACT_MISMATCH", expected_symbol)
        _require(row.get("adapter_version") == R4A9_CODE_HEAD, "FORMAL_ARTIFACT_ROW_ADAPTER_MISMATCH", expected_symbol)
    row_n = len(rows)
    _require(row_n == int(entry.get("FORMAL_FACT_ROW_N", -1)), "FORMAL_ARTIFACT_ROW_COUNT_MISMATCH", expected_symbol)
    _require(row_n == int(entry.get("REQUIRED_ROW_N", -1)), "FORMAL_ARTIFACT_REQUIRED_COUNT_MISMATCH", expected_symbol)
    actual_fact_hash = formal_fact_hash(rows)
    actual_content_hash = staged_formal_content_hash(rows)
    _require(actual_fact_hash == entry.get("FORMAL_FACT_HASH"), "FORMAL_ARTIFACT_FACT_HASH_MISMATCH", expected_symbol)
    _require(actual_content_hash == entry.get("STAGED_FORMAL_CONTENT_HASH"), "FORMAL_ARTIFACT_CONTENT_HASH_MISMATCH", expected_symbol)
    return {
        "formal_path": str(path),
        "formal_file_sha256": sha256_file(path),
        "formal_fact_row_n": row_n,
        "formal_fact_hash": actual_fact_hash,
        "staged_formal_content_hash": actual_content_hash,
    }


def compute_safe_reuse_set(
    old_complete: set[str], directly_affected: set[str]
) -> set[str]:
    _require(directly_affected <= old_complete, "DIRECTLY_AFFECTED_NOT_COMPLETE")
    safe = old_complete - directly_affected
    _require(len(safe) == 2_139, "SAFE_REUSE_COUNT_MISMATCH", len(safe))
    return safe


def build_rebased_units(
    *,
    formal_symbols: set[str],
    old_checkpoint: dict[str, Any],
    directly_affected: set[str],
    safe_artifacts: dict[str, dict[str, Any]],
    invalidated_artifact: dict[str, Any],
    current_required_row_n: int,
    current_dates: dict[str, set[date]],
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    safe_symbols = compute_safe_reuse_set(old_checkpoint["complete"], directly_affected)
    _require(set(safe_artifacts) == safe_symbols, "SAFE_ARTIFACT_SCOPE_MISMATCH")
    _require(current_required_row_n == 2_026, "002087_CURRENT_REQUIRED_COUNT_MISMATCH", current_required_row_n)
    _require(len(current_dates["002087.SZ"]) == current_required_row_n, "002087_CURRENT_DATESET_COUNT_MISMATCH")
    for key in R3_REPAIR_RESULT_KEYS:
        symbol, raw_date = key.split(":", 1)
        _require(date.fromisoformat(raw_date) in current_dates[symbol], "002087_REPAIR_KEY_NOT_PRESENT", key)

    units: dict[str, dict[str, Any]] = {}
    for symbol in sorted(formal_symbols):
        if symbol in safe_symbols:
            entry = copy.deepcopy(old_checkpoint["units"][symbol])
            entry["STATE"] = "SAFE_COMPLETE"
            entry["UPSTREAM_STATE"] = "COMPLETE"
            entry["UPSTREAM_CHECKPOINT_SHA256"] = old_checkpoint["hash"]
            entry["SAFE_REUSE_VERIFIED"] = True
            entry["VERIFIED_FORMAL_FILE_SHA256"] = safe_artifacts[symbol]["formal_file_sha256"]
            units[symbol] = entry
        elif symbol == "002087.SZ":
            entry = copy.deepcopy(old_checkpoint["units"][symbol])
            entry["STATE"] = "RECOMPUTE_REQUIRED"
            entry["UPSTREAM_STATE"] = "COMPLETE"
            entry["REASON"] = "R3_DAILY_REQUIRED_KEYSET_CHANGED"
            entry["OLD_REQUIRED_ROW_N"] = 2_025
            entry["CURRENT_REQUIRED_ROW_N"] = current_required_row_n
            entry["AFFECTED_RESULT_KEYS"] = list(R3_REPAIR_RESULT_KEYS)
            entry["UPSTREAM_CHECKPOINT_SHA256"] = old_checkpoint["hash"]
            entry["UPSTREAM_FORMAL_FILE_SHA256"] = invalidated_artifact["formal_file_sha256"]
            entry["SAFE_REUSE_VERIFIED"] = False
            units[symbol] = entry
        elif symbol == "300546.SZ":
            entry = copy.deepcopy(old_checkpoint["units"][symbol])
            entry["STATE"] = "RETRY_REQUIRED"
            entry["UPSTREAM_STATE"] = "FAILED"
            entry["REASON"] = "UPSTREAM_R3_DEFECT_REPAIRED"
            entry["DIAGNOSTIC_CLASSIFICATION"] = "R3_REQUIRED_KEY_MISMATCH"
            entry["REPAIR_ANCHOR_KEYS_PRESENT"] = list(R3_REPAIR_300546_KEYS)
            entry["UPSTREAM_CHECKPOINT_SHA256"] = old_checkpoint["hash"]
            units[symbol] = entry
        else:
            _require(symbol in old_checkpoint["unvisited"], "UNEXPECTED_NONVISITED_SYMBOL", symbol)
            units[symbol] = {
                "symbol": symbol,
                "STATE": "UNVISITED",
                "UPSTREAM_STATE": "UNVISITED",
            }

    counts = {
        "SAFE_COMPLETE": sum(entry["STATE"] == "SAFE_COMPLETE" for entry in units.values()),
        "RECOMPUTE_REQUIRED": sum(entry["STATE"] == "RECOMPUTE_REQUIRED" for entry in units.values()),
        "RETRY_REQUIRED": sum(entry["STATE"] == "RETRY_REQUIRED" for entry in units.values()),
        "UNVISITED": sum(entry["STATE"] == "UNVISITED" for entry in units.values()),
    }
    _require(counts == {"SAFE_COMPLETE": 2_139, "RECOMPUTE_REQUIRED": 1, "RETRY_REQUIRED": 1, "UNVISITED": 3_315}, "NEW_CHECKPOINT_STATE_COUNTS_MISMATCH", counts)
    _require(sum(counts.values()) == FORMAL_IDENTITY_N, "NEW_CHECKPOINT_STATE_TOTAL_MISMATCH")
    return units, counts


def validate_resume_input_gate(checkpoint: dict[str, Any], live_daily_manifest_hash: str) -> bool:
    expected = checkpoint.get("daily_input_manifest_hash")
    _require(expected == live_daily_manifest_hash, "COMPATIBILITY_REASSESSMENT_REQUIRED", f"expected={expected},live={live_daily_manifest_hash}")
    return True


def validate_old_checkpoint_unchanged(path: Path, before_sha256: str) -> bool:
    after_sha256 = sha256_file(path)
    _require(after_sha256 == before_sha256, "OLD_CHECKPOINT_MUTATED", after_sha256)
    return True


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(canonical_json_bytes(payload))
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RebaseError(f"LOCAL_WRITE_FAILED:{path}") from exc


def _recheck_old_artifacts(
    safe_artifacts: dict[str, dict[str, Any]],
    invalidated_artifact: dict[str, Any],
) -> None:
    for symbol, metadata in [*sorted(safe_artifacts.items()), ("002087.SZ", invalidated_artifact)]:
        actual = sha256_file(Path(metadata["formal_path"]))
        _require(actual == metadata["formal_file_sha256"], "OLD_FORMAL_ARTIFACT_CHANGED", symbol)


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        f"# {TASK}",
        "",
        "Offline lineage-safe checkpoint rebase. No provider execution and no canonical write were performed.",
        "",
        "## Authority",
        "",
        f"- BASE_HEAD: `{report['BASE_HEAD']}`",
        f"- COMPATIBILITY_VERDICT: `{report['COMPATIBILITY_VERDICT']}`",
        f"- COMPATIBILITY_REPORT_SHA256: `{report['COMPATIBILITY_REPORT_SHA256']}`",
        f"- OLD_CHECKPOINT_SHA256: `{report['OLD_CHECKPOINT_HASH']}`",
        f"- DAILY_INPUT_MANIFEST_HASH: `{report['DAILY_INPUT_MANIFEST_HASH']}`",
        "",
        "## Rebased partition",
        "",
        f"- SAFE_REUSE_COMPLETE_N: `{report['SAFE_REUSE_COMPLETE_N']}`",
        f"- RECOMPUTE_REQUIRED_N: `{report['RECOMPUTE_REQUIRED_N']}` (`{', '.join(report['RECOMPUTE_REQUIRED_SYMBOLS'])}`)",
        f"- RETRY_REQUIRED_N: `{report['RETRY_REQUIRED_N']}` (`{', '.join(report['RETRY_REQUIRED_SYMBOLS'])}`)",
        f"- UNVISITED_N: `{report['UNVISITED_N']}`",
        f"- STATE_TOTAL_N: `{report['STATE_TOTAL_N']}`",
        "",
        "The new checkpoint binds future resume to the current daily manifest. A live manifest mismatch is `COMPATIBILITY_REASSESSMENT_REQUIRED` and cannot become an unbound resume.",
        "",
        "## Resume design only",
        "",
        "1. Recompute `002087.SZ`.",
        "2. Bounded retry `300546.SZ`.",
        "3. Continue the remaining 3,315 UNVISITED symbols only after both bounded units pass.",
        "",
        "No phase above was executed in this task.",
        "",
        "## Safety",
        "",
        f"- OLD_CHECKPOINT_MUTATED: `{report['OLD_CHECKPOINT_MUTATED']}`",
        f"- OLD_COMPLETE_ARTIFACT_DELETED_N: `{report['OLD_COMPLETE_ARTIFACT_DELETED_N']}`",
        f"- OLD_FAILED_ARTIFACT_DELETED_N: `{report['OLD_FAILED_ARTIFACT_DELETED_N']}`",
        f"- NETWORK_PROVIDER_DATA_FETCH: `{report['NETWORK_PROVIDER_DATA_FETCH']}`",
        f"- BAOSTOCK_EXECUTED: `{report['BAOSTOCK_EXECUTED']}`",
        f"- TDX_EXECUTED: `{report['TDX_EXECUTED']}`",
        f"- CANONICAL_WRITE_EXECUTED: `{report['CANONICAL_WRITE_EXECUTED']}`",
        f"- R4_PRECLOSE_RESULT_WRITE_EXECUTED: `{report['R4_PRECLOSE_RESULT_WRITE_EXECUTED']}`",
        f"- R4A9_RESUME_CANDIDATE: `{report['R4A9_RESUME_CANDIDATE']}`",
        f"- R4A9_RESUME_AUTHORIZED: `{report['R4A9_RESUME_AUTHORIZED']}`",
        f"- PRECLOSE_COMPLETE: `{report['PRECLOSE_COMPLETE']}`",
        "",
        f"New checkpoint: `{report['NEW_CHECKPOINT_PATH']}`",
        f"New checkpoint SHA256: `{report['NEW_CHECKPOINT_HASH']}`",
        f"New checkpoint schema: `{report['NEW_CHECKPOINT_SCHEMA_VERSION']}`",
    ]
    return "\n".join(lines) + "\n"


def write_reports(repo_root: Path, report: dict[str, Any]) -> None:
    report_path = repo_root / "reports" / "implementation" / REPORT_NAME
    markdown_path = repo_root / "reports" / "implementation" / REPORT_MD_NAME
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(report_path, report)
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")


def run_rebase(
    repo_root: Path = REPO_ROOT,
    data_root: Path = DATA_ROOT_DEFAULT,
    stage_root: Path | None = None,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run all offline gates and write only the new checkpoint/receipt."""

    repo_root = Path(repo_root).resolve(strict=True)
    data_root = Path(data_root).expanduser()
    expected_stage_root = data_root / "staging" / NEW_R4A9_STAGE_DIRNAME
    stage_root = expected_stage_root if stage_root is None else Path(stage_root).expanduser()

    # This guard is intentionally the first operation that could precede a
    # write.  It also runs for Python direct callers, not only the CLI.
    require_isolated_stage_root(data_root, stage_root, allow_existing=False)
    require_base_head(repo_root)

    current_manifest = build_input_file_manifest(data_root)
    _require(current_manifest["INPUT_FILE_N"] == 2_580, "CURRENT_DAILY_INPUT_FILE_COUNT_MISMATCH")
    _require(current_manifest["INPUT_MANIFEST_HASH"] == CURRENT_DAILY_INPUT_MANIFEST_HASH, "CURRENT_DAILY_INPUT_MANIFEST_DRIFT", current_manifest["INPUT_MANIFEST_HASH"])

    formal_symbols = load_formal_symbols(data_root)
    compatibility, compatibility_sha = load_compatibility_report(repo_root)
    old_checkpoint = load_old_checkpoint(data_root, formal_symbols)
    r3_report, r3_report_sha = load_r3_repair_report(repo_root)
    diagnostic, diagnostic_sha = load_diagnostic(repo_root)

    current_dates = load_current_symbol_dates(data_root, {"002087.SZ", "300546.SZ"})
    _require(len(current_dates["002087.SZ"]) == 2_026, "002087_CURRENT_REQUIRED_COUNT_MISMATCH", len(current_dates["002087.SZ"]))
    for key in R3_REPAIR_RESULT_KEYS:
        symbol, raw_date = key.split(":", 1)
        _require(date.fromisoformat(raw_date) in current_dates[symbol], "002087_REPAIR_KEY_NOT_PRESENT", key)
    for key in R3_REPAIR_300546_KEYS:
        symbol, raw_date = key.split(":", 1)
        _require(date.fromisoformat(raw_date) in current_dates[symbol], "300546_REPAIR_ANCHOR_NOT_PRESENT", key)

    directly_affected = set(compatibility["COMPLETE_SYMBOL_DIRECTLY_AFFECTED_LIST"])
    safe_symbols = compute_safe_reuse_set(old_checkpoint["complete"], directly_affected)
    safe_artifacts: dict[str, dict[str, Any]] = {}
    for index, symbol in enumerate(sorted(safe_symbols), start=1):
        safe_artifacts[symbol] = verify_formal_artifact(
            old_checkpoint["units"][symbol],
            expected_symbol=symbol,
            old_stage_root=old_checkpoint["stage_root"],
        )
        if progress and (index == 1 or index % 250 == 0 or index == len(safe_symbols)):
            progress(f"verified safe formal artifacts: {index}/{len(safe_symbols)}")

    invalidated_artifact = verify_formal_artifact(
        old_checkpoint["units"]["002087.SZ"],
        expected_symbol="002087.SZ",
        old_stage_root=old_checkpoint["stage_root"],
    )
    _require(invalidated_artifact["formal_fact_row_n"] == 2_025, "002087_OLD_FORMAL_ROW_COUNT_MISMATCH")
    _require(diagnostic["ROOT_CAUSE_CLASSIFICATION"] == "R3_REQUIRED_KEY_MISMATCH", "300546_DIAGNOSTIC_NOT_REPAIR_ADDRESSABLE")

    # Recheck every old artifact and the old checkpoint immediately before any
    # new-root mkdir/write.  This closes the read-to-write lineage window.
    _recheck_old_artifacts(safe_artifacts, invalidated_artifact)
    validate_old_checkpoint_unchanged(old_checkpoint["path"], old_checkpoint["hash"])
    _require(not expected_stage_root.exists(), "NEW_CHECKPOINT_ROOT_APPEARED_DURING_PREFLIGHT")

    units, state_counts = build_rebased_units(
        formal_symbols=formal_symbols,
        old_checkpoint=old_checkpoint,
        directly_affected=directly_affected,
        safe_artifacts=safe_artifacts,
        invalidated_artifact=invalidated_artifact,
        current_required_row_n=len(current_dates["002087.SZ"]),
        current_dates=current_dates,
    )

    checkpoint = {
        "schema_version": NEW_CHECKPOINT_SCHEMA_VERSION,
        "task": TASK,
        "upstream_checkpoint_path": str(old_checkpoint["path"]),
        "upstream_checkpoint_sha256": old_checkpoint["hash"],
        "compatibility_report_path": str(COMPATIBILITY_REPORT_REL),
        "compatibility_report_sha256": compatibility_sha,
        "compatibility_verdict": COMPATIBILITY_VERDICT,
        "daily_input_manifest_hash": CURRENT_DAILY_INPUT_MANIFEST_HASH,
        "daily_coverage_status": "PARTIAL",
        "r3_daily_usable": True,
        "formal_identity_n": FORMAL_IDENTITY_N,
        "formal_identity_hash": FORMAL_IDENTITY_HASH,
        "full_query_plan_hash": R4A9_QUERY_PLAN_HASH,
        "adapter_authority_sha": R4A9_CODE_HEAD,
        "query_contract_version": R4A9_QUERY_CONTRACT_VERSION,
        "safe_reuse_complete_n": state_counts["SAFE_COMPLETE"],
        "recompute_required_n": state_counts["RECOMPUTE_REQUIRED"],
        "retry_required_n": state_counts["RETRY_REQUIRED"],
        "unvisited_n": state_counts["UNVISITED"],
        "state_counts": state_counts,
        "resume_order": [
            {"phase": 1, "state": "RECOMPUTE_REQUIRED", "symbols": ["002087.SZ"]},
            {"phase": 2, "state": "RETRY_REQUIRED", "symbols": ["300546.SZ"]},
            {"phase": 3, "state": "UNVISITED", "symbols": sorted(old_checkpoint["unvisited"])},
            {"gate": "PHASE_1_AND_2_PASS_REQUIRED_BEFORE_PHASE_3"},
        ],
        "lineage_guard": {
            "live_daily_manifest_mismatch": "COMPATIBILITY_REASSESSMENT_REQUIRED",
            "unbound_resume_allowed": False,
        },
        "units": units,
    }
    checkpoint_path = expected_stage_root / OLD_CHECKPOINT_NAME
    # The new root is the only path now authorized to be created.
    require_isolated_stage_root(data_root, expected_stage_root, allow_existing=False)
    _recheck_old_artifacts(safe_artifacts, invalidated_artifact)
    validate_old_checkpoint_unchanged(old_checkpoint["path"], old_checkpoint["hash"])
    write_json_atomic(checkpoint_path, checkpoint)
    checkpoint_hash = sha256_file(checkpoint_path)
    receipt = {
        "schema_version": NEW_CHECKPOINT_SCHEMA_VERSION,
        "receipt_type": "R4A9_LINEAGE_SAFE_CHECKPOINT_REBASE",
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": checkpoint_hash,
        "upstream_checkpoint_path": str(old_checkpoint["path"]),
        "upstream_checkpoint_sha256": old_checkpoint["hash"],
        "daily_input_manifest_hash": CURRENT_DAILY_INPUT_MANIFEST_HASH,
        "state_counts": state_counts,
        "safe_artifacts_verified_n": len(safe_artifacts),
        "r4a9_resume_candidate": True,
        "r4a9_resume_authorized": False,
        "network_provider_data_fetch": "NO",
        "r4_precclose_result_write_executed": False,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    receipt_path = expected_stage_root / "resume_receipt.json"
    write_json_atomic(receipt_path, receipt)
    receipt_hash = sha256_file(receipt_path)

    _recheck_old_artifacts(safe_artifacts, invalidated_artifact)
    old_checkpoint_unchanged = validate_old_checkpoint_unchanged(old_checkpoint["path"], old_checkpoint["hash"])
    _require(sha256_file(checkpoint_path) == checkpoint_hash, "NEW_CHECKPOINT_POST_HASH_MISMATCH")
    _require(receipt.get("checkpoint_sha256") == checkpoint_hash, "NEW_RECEIPT_CHECKPOINT_HASH_MISMATCH")

    report = {
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "COMPATIBILITY_VERDICT": COMPATIBILITY_VERDICT,
        "COMPATIBILITY_REPORT_PATH": str(COMPATIBILITY_REPORT_REL),
        "COMPATIBILITY_REPORT_SHA256": compatibility_sha,
        "R3_REPAIR_REPORT_SHA256": r3_report_sha,
        "R4A9_300546_DIAGNOSTIC_SHA256": diagnostic_sha,
        "OLD_CHECKPOINT_PATH": str(old_checkpoint["path"]),
        "OLD_CHECKPOINT_HASH": old_checkpoint["hash"],
        "OLD_CHECKPOINT_MUTATED": not old_checkpoint_unchanged,
        "NEW_CHECKPOINT_PATH": str(checkpoint_path),
        "NEW_CHECKPOINT_HASH": checkpoint_hash,
        "NEW_CHECKPOINT_SCHEMA_VERSION": NEW_CHECKPOINT_SCHEMA_VERSION,
        "NEW_RESUME_RECEIPT_PATH": str(receipt_path),
        "NEW_RESUME_RECEIPT_HASH": receipt_hash,
        "DAILY_INPUT_MANIFEST_HASH": CURRENT_DAILY_INPUT_MANIFEST_HASH,
        "DAILY_COVERAGE_STATUS": "PARTIAL",
        "R3_DAILY_USABLE": True,
        "FORMAL_IDENTITY_N": FORMAL_IDENTITY_N,
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "FULL_QUERY_PLAN_HASH": R4A9_QUERY_PLAN_HASH,
        "ADAPTER_AUTHORITY_SHA": R4A9_CODE_HEAD,
        "QUERY_CONTRACT_VERSION": R4A9_QUERY_CONTRACT_VERSION,
        "SAFE_REUSE_COMPLETE_N": state_counts["SAFE_COMPLETE"],
        "SAFE_ARTIFACT_VERIFIED_N": len(safe_artifacts),
        "RECOMPUTE_REQUIRED_N": state_counts["RECOMPUTE_REQUIRED"],
        "RECOMPUTE_REQUIRED_SYMBOLS": ["002087.SZ"],
        "RECOMPUTE_REQUIRED_REASON": "R3_DAILY_REQUIRED_KEYSET_CHANGED",
        "RECOMPUTE_REQUIRED_OLD_REQUIRED_ROW_N": 2_025,
        "RECOMPUTE_REQUIRED_CURRENT_REQUIRED_ROW_N": len(current_dates["002087.SZ"]),
        "RECOMPUTE_REQUIRED_KEYS": list(R3_REPAIR_RESULT_KEYS),
        "RETRY_REQUIRED_N": state_counts["RETRY_REQUIRED"],
        "RETRY_REQUIRED_SYMBOLS": ["300546.SZ"],
        "RETRY_REQUIRED_REASON": "UPSTREAM_R3_DEFECT_REPAIRED",
        "RETRY_REQUIRED_DIAGNOSTIC": "R3_REQUIRED_KEY_MISMATCH",
        "RETRY_REQUIRED_ANCHOR_KEYS": list(R3_REPAIR_300546_KEYS),
        "UNVISITED_N": state_counts["UNVISITED"],
        "UNVISITED_REPAIRED_SYMBOLS_REMAIN_UNVISITED": ["600647.SH", "600766.SH", "603133.SH"],
        "STATE_COUNTS": state_counts,
        "STATE_TOTAL_N": sum(state_counts.values()),
        "OLD_COMPLETE_ARTIFACT_DELETED_N": 0,
        "OLD_FAILED_ARTIFACT_DELETED_N": 0,
        "OLD_ARTIFACT_SET_MUTATED": False,
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "BAOSTOCK_EXECUTED": False,
        "TDX_EXECUTED": False,
        "TUSHARE_EXECUTED": False,
        "EASTMONEY_EXECUTED": False,
        "CANONICAL_WRITE_EXECUTED": False,
        "CANONICAL_BYTES_MUTATED": False,
        "R4_PRECLOSE_RESULT_WRITE_EXECUTED": False,
        "R4A9_RESUME_EXECUTED": False,
        "R4A9_RESUME_CANDIDATE": True,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
        "R3_DAILY_REPAIR_EXECUTED": False,
        "RESUME_ORDER": [
            "002087.SZ RECOMPUTE_REQUIRED",
            "300546.SZ RETRY_REQUIRED",
            "3315 UNVISITED continuation only after phases 1 and 2 pass",
        ],
    }
    write_reports(repo_root, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        report = run_rebase(
            repo_root=args.repo_root,
            data_root=args.data_root,
            stage_root=args.stage_root,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
        )
    except RebaseError as exc:
        print(f"FAIL_CLOSED:{exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
