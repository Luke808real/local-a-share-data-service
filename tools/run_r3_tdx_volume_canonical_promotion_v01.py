#!/usr/bin/env python3
"""Promote the audited R3 volume candidate with transaction and rollback gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import polars as pl

TOOLS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_ROOT))
sys.path.insert(0, str(TOOLS_ROOT / "audits"))

import run_r3_tdx_volume_canonical_repair_candidate_v01 as candidate  # noqa: E402
import r3_tdx_volume_canonical_repair_candidate_v01_1 as candidate_audit  # noqa: E402
from r3_tdx_volume_rebuild_scope_v01 import (  # noqa: E402
    build_input_file_manifest,
    manifest_byte_form,
)


TASK = "R3_TDX_VOLUME_CANONICAL_PROMOTION_V01"
BASE_HEAD = "d91a99e7fd5d7f3850a2fed7acaa815eaf708a4e"
CANDIDATE_AUTHORITY_COMMIT = BASE_HEAD
PRE_INPUT_MANIFEST_HASH = "f9025a5cbc52d757fdc05d9e6ebb5f3c75c1cd93414a2f6c51bb83314594a6ec"
CHANGED_KEY_MANIFEST_HASH = "f1cdb9d5416535e76631673479ef1fc36539a8626ee18026a581f6e1191b62c7"
AFFECTED_FILE_N = 432
AFFECTED_FILE_MANIFEST_HASH = "899490446e21a7861f27e2498a917efeab1e99cb31e21f442d824fed80e119f4"
CANDIDATE_DATASET_HASH = "6fc5eebe32b7f9f5d8530289ba1590f867e46d02721c6a780e1ed87429b1ea2c"
CHANGED_KEY_N = 1169
TOTAL_FILE_N = 2580
UNAFFECTED_FILE_N = 2148
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
CANDIDATE_ROOT_NAME = "r3_tdx_volume_canonical_repair_candidate_v01"
PROMOTION_ROOT_NAME = "r3_tdx_volume_canonical_promotion_v01"
EXPECTED_POST_REPORT_NAME = "R3_TDX_VOLUME_CANONICAL_PROMOTION_EXPECTED_POST_INPUT_MANIFEST_V01.json"
OBSERVED_POST_REPORT_NAME = "R3_TDX_VOLUME_CANONICAL_PROMOTION_POST_INPUT_MANIFEST_V01.json"
RECEIPT_REPORT_NAME = "R3_TDX_VOLUME_CANONICAL_PROMOTION_RECEIPT_V01.json"
REPORT_NAME = "R3_TDX_VOLUME_CANONICAL_PROMOTION_V01.json"
REPORT_MD_NAME = "R3_TDX_VOLUME_CANONICAL_PROMOTION_V01.md"
FORBIDDEN_KEYS = {
    ("300546.SZ", "2016-09-29"),
    ("300546.SZ", "2016-10-10"),
}
CANONICAL_SERIALIZATION = (
    "json.dumps(rows, ensure_ascii=True, sort_keys=True, "
    "separators=(',', ':')) sorted by relative_path"
)


class PromotionError(RuntimeError):
    """Fail-closed promotion error."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"UNREADABLE_JSON:{path}") from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PromotionError(message)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise PromotionError(f"TEMP_OUTPUT_ALREADY_EXISTS:{temporary}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_json_atomic(path: Path, payload: Any) -> None:
    write_bytes_atomic(path, canonical_json_bytes(payload))


def read_expected_frozen_input(repo_root: Path) -> dict[str, Any]:
    return load_json(repo_root / "reports/implementation/R3_TDX_VOLUME_REBUILD_INPUT_MANIFEST_V01.json")


def require_manifest_equal(left: dict[str, Any], right: dict[str, Any], label: str) -> None:
    if (
        left.get("INPUT_FILE_N") != right.get("INPUT_FILE_N")
        or left.get("INPUT_MANIFEST_HASH") != right.get("INPUT_MANIFEST_HASH")
        or left.get("FILES") != right.get("FILES")
    ):
        raise PromotionError(f"{label}_FILES_MISMATCH")


def require_pre_input(data_root: Path, repo_root: Path) -> dict[str, Any]:
    frozen = read_expected_frozen_input(repo_root)
    live = build_input_file_manifest(data_root)
    _require(live.get("INPUT_FILE_N") == 2580, "PRE_INPUT_FILE_N_MISMATCH")
    _require(live.get("INPUT_MANIFEST_HASH") == PRE_INPUT_MANIFEST_HASH, "PRE_INPUT_MANIFEST_DRIFT")
    _require(live.get("INPUT_FILE_N") == frozen.get("INPUT_FILE_N"), "PRE_INPUT_FROZEN_N_MISMATCH")
    _require(live.get("FILES") == frozen.get("FILES"), "PRE_INPUT_FROZEN_FILES_MISMATCH")
    return live


def require_exact_root(data_root: Path, root: Path, expected_name: str) -> Path:
    data_input = Path(data_root).expanduser()
    root_input = Path(root).expanduser()
    if not data_input.is_absolute() or not root_input.is_absolute():
        raise PromotionError("PROMOTION_ROOT_NOT_ABSOLUTE")
    if not data_input.is_dir():
        raise PromotionError("DATA_ROOT_NOT_DIRECTORY")
    data_resolved = data_input.resolve(strict=True)
    root_resolved = root_input.resolve(strict=False)
    expected = data_resolved / "staging" / expected_name
    if data_input != data_resolved:
        raise PromotionError("DATA_ROOT_SYMLINK_OR_TRAVERSAL")
    if root_input != root_resolved or root_resolved != expected:
        raise PromotionError("PROMOTION_ROOT_FORBIDDEN")
    if (data_resolved / "staging").is_symlink() or root_input.is_symlink():
        raise PromotionError("PROMOTION_ROOT_SYMLINK")
    return root_resolved


def safe_join(root: Path, relative_path: str, label: str) -> Path:
    relative = Path(relative_path)
    _require(not relative.is_absolute() and ".." not in relative.parts, f"{label}_PATH_TRAVERSAL")
    root_resolved = root.resolve(strict=True)
    resolved = (root / relative).resolve(strict=False)
    _require(resolved.is_relative_to(root_resolved), f"{label}_PATH_ESCAPE")
    return resolved


def require_current_head(repo_root: Path) -> None:
    current = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, text=True, capture_output=True
    ).stdout.strip()
    if current != BASE_HEAD:
        raise PromotionError(f"BASE_HEAD_MISMATCH:{current}")


def candidate_manifest_paths(candidate_root: Path) -> tuple[Path, Path]:
    return (
        candidate_root / "candidate_repair_manifest.json",
        candidate_root / "candidate_validation_report.json",
    )


def candidate_snapshot(candidate_root: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    manifest_path, validation_path = candidate_manifest_paths(candidate_root)
    _require(manifest_path.is_file() and not manifest_path.is_symlink(), "CANDIDATE_MANIFEST_MISSING")
    _require(validation_path.is_file() and not validation_path.is_symlink(), "CANDIDATE_VALIDATION_MISSING")
    file_hashes: list[dict[str, str]] = []
    for record in records:
        relative = record["relative_path"]
        path = safe_join(candidate_root, relative, "CANDIDATE")
        _require(path.is_file() and not path.is_symlink(), f"CANDIDATE_FILE_MISSING:{relative}")
        digest = sha256_file(path)
        _require(digest == record["candidate_sha256"], f"CANDIDATE_SHA_MISMATCH:{relative}")
        file_hashes.append({"relative_path": relative, "sha256": digest})
    return {
        "manifest_sha256": sha256_file(manifest_path),
        "validation_sha256": sha256_file(validation_path),
        "file_hashes": file_hashes,
        "candidate_dataset_hash": candidate._candidate_dataset_hash(records),
    }


def require_candidate_authority(
    *, repo_root: Path, data_root: Path, candidate_root: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    result = candidate_audit.audit_existing_candidate(
        repo_root=repo_root, data_root=data_root, stage_root=candidate_root
    )
    required = {
        "AFFECTED_FILE_N": AFFECTED_FILE_N,
        "AFFECTED_FILE_MANIFEST_HASH": AFFECTED_FILE_MANIFEST_HASH,
        "CANDIDATE_DATASET_HASH": CANDIDATE_DATASET_HASH,
        "CHANGED_KEY_N": CHANGED_KEY_N,
        "CHANGED_ROW_N": CHANGED_KEY_N,
        "CHANGED_CELL_N": CHANGED_KEY_N,
        "CHANGED_COLUMNS": ["volume"],
        "ROW_COUNT_DELTA": 0,
        "SCHEMA_DELTA": 0,
    }
    for key, value in required.items():
        _require(result.get(key) == value, f"CANDIDATE_AUTHORITY_MISMATCH:{key}")
    manifest_path, _ = candidate_manifest_paths(candidate_root)
    manifest = load_json(manifest_path)
    records = manifest.get("FILES")
    _require(isinstance(records, list) and len(records) == AFFECTED_FILE_N, "CANDIDATE_FILE_RECORD_N_MISMATCH")
    _require(candidate._hash_file_manifest(records) == AFFECTED_FILE_MANIFEST_HASH, "CANDIDATE_FILE_MANIFEST_HASH_MISMATCH")
    _require(candidate._candidate_dataset_hash(records) == CANDIDATE_DATASET_HASH, "CANDIDATE_DATASET_HASH_MISMATCH")
    return result, manifest, candidate_snapshot(candidate_root, records)


def build_manifest_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row["relative_path"])
    payload = manifest_byte_form(ordered)
    return {
        "INPUT_FILE_N": len(ordered),
        "INPUT_MANIFEST_HASH": sha256_bytes(payload),
        "CANONICAL_SERIALIZATION": CANONICAL_SERIALIZATION,
        "FILES": ordered,
    }


def build_expected_post_manifest(
    *, data_root: Path, candidate_root: Path, pre: dict[str, Any], candidate_records: list[dict[str, Any]]
) -> dict[str, Any]:
    candidate_by_path = {record["relative_path"]: record for record in candidate_records}
    _require(len(candidate_by_path) == AFFECTED_FILE_N, "CANDIDATE_FILE_SET_DUPLICATE")
    rows: list[dict[str, Any]] = []
    for pre_row in pre["FILES"]:
        relative = pre_row["relative_path"]
        if relative in candidate_by_path:
            candidate_row = candidate_by_path[relative]
            path = safe_join(candidate_root, relative, "CANDIDATE")
            _require(path.is_file() and not path.is_symlink(), f"CANDIDATE_FILE_MISSING:{relative}")
            _require(sha256_file(path) == candidate_row["candidate_sha256"], f"CANDIDATE_SHA_MISMATCH:{relative}")
            rows.append(
                {
                    "relative_path": relative,
                    "file_size": path.stat().st_size,
                    "sha256": candidate_row["candidate_sha256"],
                }
            )
        else:
            rows.append(dict(pre_row))
    expected = build_manifest_from_rows(rows)
    _require(expected["INPUT_FILE_N"] == TOTAL_FILE_N, "EXPECTED_POST_INPUT_FILE_N_MISMATCH")
    _require(
        len(expected["FILES"]) - len(candidate_records) == UNAFFECTED_FILE_N,
        "EXPECTED_POST_PARTITION_MISMATCH",
    )
    return expected


def ensure_clean_transaction_root(transaction_root: Path) -> None:
    if transaction_root.exists() or transaction_root.is_symlink():
        raise PromotionError("RECOVERY_REQUIRED:TRANSACTION_ROOT_EXISTS")


def ensure_output_paths_absent(repo_root: Path) -> None:
    output_names = (
        EXPECTED_POST_REPORT_NAME,
        OBSERVED_POST_REPORT_NAME,
        RECEIPT_REPORT_NAME,
        REPORT_NAME,
        REPORT_MD_NAME,
    )
    implementation = repo_root / "reports/implementation"
    for name in output_names:
        path = implementation / name
        if path.exists() or path.is_symlink():
            raise PromotionError(f"PROMOTION_OUTPUT_ALREADY_EXISTS:{path}")


def write_transaction_state(
    transaction_root: Path, state: str, *, promoted_file_n: int = 0, promotion_complete: bool = False
) -> None:
    write_json_atomic(
        transaction_root / "transaction_state.json",
        {
            "REPORT": TASK,
            "BASE_HEAD": BASE_HEAD,
            "TRANSACTION_STATE": state,
            "PROMOTED_FILE_N": promoted_file_n,
            "PROMOTION_COMPLETE": promotion_complete,
        },
    )


def write_journal(transaction_root: Path, *, state: str, files: list[str], key: str) -> None:
    write_json_atomic(
        transaction_root / f"{key}_journal.json",
        {"REPORT": TASK, "TRANSACTION_STATE": state, key.upper(): sorted(files)},
    )


def build_plan_records(
    *, data_root: Path, candidate_root: Path, transaction_root: Path, pre: dict[str, Any], candidate_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    pre_by_path = {row["relative_path"]: row for row in pre["FILES"]}
    records: list[dict[str, Any]] = []
    for candidate_row in sorted(candidate_records, key=lambda row: row["relative_path"]):
        relative = candidate_row["relative_path"]
        _require(relative.startswith("curated/daily_bars/"), f"CANONICAL_TARGET_SCOPE:{relative}")
        source = safe_join(data_root, relative, "CANONICAL_SOURCE")
        candidate_path = safe_join(candidate_root, relative, "CANDIDATE")
        _require(relative in pre_by_path, f"CANONICAL_TARGET_NOT_IN_PRE_MANIFEST:{relative}")
        _require(source.is_file() and not source.is_symlink(), f"CANONICAL_SOURCE_MISSING:{relative}")
        _require(candidate_path.is_file() and not candidate_path.is_symlink(), f"CANDIDATE_FILE_MISSING:{relative}")
        _require(sha256_file(source) == pre_by_path[relative]["sha256"], f"CANONICAL_SOURCE_SHA_MISMATCH:{relative}")
        _require(sha256_file(candidate_path) == candidate_row["candidate_sha256"], f"CANDIDATE_SHA_MISMATCH:{relative}")
        backup_relative = str(Path("rollback_backup") / relative)
        backup = safe_join(transaction_root, backup_relative, "ROLLBACK_BACKUP")
        records.append(
            {
                "relative_path": relative,
                "source_sha256": pre_by_path[relative]["sha256"],
                "source_file_size": pre_by_path[relative]["file_size"],
                "candidate_sha256": candidate_row["candidate_sha256"],
                "candidate_file_size": candidate_path.stat().st_size,
                "rollback_backup_relative_path": backup_relative,
                "rollback_backup_sha256": None,
                "rollback_backup_file_size": None,
                "rollback_backup_verified": False,
                "promotion_status": "PLANNED",
                "post_sha256": None,
                "rollback_status": "AVAILABLE_AFTER_PREPARE",
            }
        )
        _require(backup.parent == transaction_root / "rollback_backup" / Path(relative).parent, "ROLLBACK_SCOPE_MISMATCH")
    _require(len(records) == AFFECTED_FILE_N, "PROMOTION_PLAN_FILE_N_MISMATCH")
    return records


def copy_verified(source: Path, destination: Path, expected_sha256: str, temporary_suffix: str) -> int:
    _require(source.is_file() and not source.is_symlink(), f"COPY_SOURCE_INVALID:{source}")
    _require(not destination.exists() and not destination.is_symlink(), f"COPY_DESTINATION_EXISTS:{destination}")
    temporary = destination.with_name(f".{destination.name}.{temporary_suffix}.tmp")
    _require(not temporary.exists() and not temporary.is_symlink(), f"COPY_TEMP_EXISTS:{temporary}")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, temporary)
    digest = sha256_file(temporary)
    if digest != expected_sha256:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise PromotionError(f"COPY_SHA_MISMATCH:{source}")
    size = temporary.stat().st_size
    os.replace(temporary, destination)
    _require(sha256_file(destination) == expected_sha256, f"COPY_POST_SHA_MISMATCH:{destination}")
    return size


def prepare_backups(transaction_root: Path, records: list[dict[str, Any]]) -> list[str]:
    backed_up: list[str] = []
    for record in records:
        relative = record["relative_path"]
        source = Path(record["source_path"])
        backup = transaction_root / record["rollback_backup_relative_path"]
        size = copy_verified(source, backup, record["source_sha256"], "backup")
        _require(sha256_file(backup) == record["source_sha256"], f"ROLLBACK_BACKUP_SHA_MISMATCH:{relative}")
        record["rollback_backup_sha256"] = sha256_file(backup)
        record["rollback_backup_file_size"] = size
        record["rollback_backup_verified"] = True
        backed_up.append(relative)
    _require(len(backed_up) == len(records), "ROLLBACK_BACKUP_INCOMPLETE")
    _require(all(record["rollback_backup_verified"] for record in records), "ROLLBACK_BACKUP_NOT_VERIFIED")
    return backed_up


def require_pre_commit_stable(pre: dict[str, Any], pre_commit: dict[str, Any]) -> None:
    require_manifest_equal(pre, pre_commit, "INPUT_DRIFT_BEFORE_PROMOTION")
    _require(pre_commit["INPUT_MANIFEST_HASH"] == PRE_INPUT_MANIFEST_HASH, "INPUT_DRIFT_BEFORE_PROMOTION")


def promote_files(
    *,
    data_root: Path,
    candidate_root: Path,
    transaction_root: Path,
    records: list[dict[str, Any]],
    promoted: list[str],
    fault_injector: Callable[[int, dict[str, Any]], None] | None = None,
) -> None:
    write_transaction_state(transaction_root, "COMMITTING", promoted_file_n=0, promotion_complete=False)
    write_journal(transaction_root, state="COMMITTING", files=promoted, key="promoted_files")
    for index, record in enumerate(records):
        relative = record["relative_path"]
        target = safe_join(data_root, relative, "CANONICAL_TARGET")
        candidate_path = safe_join(candidate_root, relative, "CANDIDATE")
        _require(target.is_file() and not target.is_symlink(), f"CANONICAL_TARGET_MISSING:{relative}")
        _require(sha256_file(target) == record["source_sha256"], f"INPUT_DRIFT_BEFORE_REPLACE:{relative}")
        _require(sha256_file(candidate_path) == record["candidate_sha256"], f"CANDIDATE_SHA_MISMATCH:{relative}")
        temporary = target.with_name(f".{target.name}.r3_tdx_promotion_v01.tmp")
        _require(not temporary.exists() and not temporary.is_symlink(), f"PROMOTION_TEMP_EXISTS:{temporary}")
        shutil.copyfile(candidate_path, temporary)
        if sha256_file(temporary) != record["candidate_sha256"]:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise PromotionError(f"PROMOTION_TEMP_SHA_MISMATCH:{relative}")
        os.replace(temporary, target)
        promoted.append(relative)
        record["promotion_status"] = "PROMOTED"
        write_transaction_state(
            transaction_root, "COMMITTING", promoted_file_n=len(promoted), promotion_complete=False
        )
        write_journal(transaction_root, state="COMMITTING", files=promoted, key="promoted_files")
        if sha256_file(target) != record["candidate_sha256"]:
            raise PromotionError(f"PROMOTION_POST_SHA_MISMATCH:{relative}")
        if fault_injector is not None:
            fault_injector(index, record)


def rollback_files(
    *, data_root: Path, transaction_root: Path, records: list[dict[str, Any]], promoted: list[str]
) -> bool:
    promoted_set = set(promoted)
    write_transaction_state(transaction_root, "ROLLING_BACK", promoted_file_n=len(promoted), promotion_complete=False)
    for record in reversed(records):
        relative = record["relative_path"]
        if relative not in promoted_set:
            continue
        target = safe_join(data_root, relative, "CANONICAL_TARGET")
        backup = transaction_root / record["rollback_backup_relative_path"]
        _require(backup.is_file() and not backup.is_symlink(), f"ROLLBACK_BACKUP_MISSING:{relative}")
        _require(sha256_file(backup) == record["source_sha256"], f"ROLLBACK_BACKUP_SHA_MISMATCH:{relative}")
        temporary = target.with_name(f".{target.name}.r3_tdx_rollback_v01.tmp")
        _require(not temporary.exists() and not temporary.is_symlink(), f"ROLLBACK_TEMP_EXISTS:{temporary}")
        shutil.copyfile(backup, temporary)
        _require(sha256_file(temporary) == record["source_sha256"], f"ROLLBACK_TEMP_SHA_MISMATCH:{relative}")
        os.replace(temporary, target)
        _require(sha256_file(target) == record["source_sha256"], f"ROLLBACK_POST_SHA_MISMATCH:{relative}")
        record["promotion_status"] = "ROLLED_BACK"
        record["rollback_status"] = "RESTORED"
    write_journal(transaction_root, state="ROLLED_BACK", files=sorted(promoted), key="rolled_back_files")
    return True


def validate_post_manifest(
    *, data_root: Path, expected_post: dict[str, Any], pre: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    post = build_input_file_manifest(data_root)
    require_manifest_equal(expected_post, post, "POST_INPUT_MANIFEST")
    pre_by_path = {row["relative_path"]: row for row in pre["FILES"]}
    affected = {record["relative_path"]: record for record in records}
    _require(len(affected) == AFFECTED_FILE_N, "POST_AFFECTED_FILE_SET_MISMATCH")
    for row in post["FILES"]:
        relative = row["relative_path"]
        if relative in affected:
            _require(row["sha256"] == affected[relative]["candidate_sha256"], f"POST_CANDIDATE_SHA_MISMATCH:{relative}")
        else:
            _require(row == pre_by_path[relative], f"UNFFECTED_FILE_MUTATION:{relative}")
    return post


def key_of(row: dict[str, Any]) -> tuple[str, str]:
    value = row["trade_date"]
    return str(row["symbol"]), str(value)[:10]


def validate_semantic_post(
    *, data_root: Path, transaction_root: Path, records: list[dict[str, Any]], changed: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any]:
    changed_by_file: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for key, row in changed.items():
        relative = next(
            (
                record["relative_path"]
                for record in records
                if f"trade_date={key[1]}/" in record["relative_path"]
            ),
            None,
        )
        _require(relative is not None, f"POST_CHANGED_KEY_FILE_MISSING:{key}")
        changed_by_file.setdefault(relative, {})[key] = row
    repaired = 0
    mismatch = 0
    for record in records:
        relative = record["relative_path"]
        backup = safe_join(
            transaction_root, record["rollback_backup_relative_path"], "ROLLBACK_BACKUP"
        )
        target = safe_join(data_root, relative, "CANONICAL_TARGET")
        old = pl.read_parquet(backup)
        post = pl.read_parquet(target)
        changes = changed_by_file[relative]
        candidate.validate_source_preconditions(old, changes, relative)
        diff = candidate._validate_candidate_diff(old, post, changes, relative)
        _require(diff == {"ROW_COUNT_DELTA": 0, "SCHEMA_DELTA": 0, "CHANGED_ROW_N": len(changes), "CHANGED_CELL_N": len(changes), "CHANGED_COLUMNS": ["volume"]}, f"POST_SEMANTIC_DIFF:{relative}")
        for row in post.iter_rows(named=True):
            key = key_of(row)
            if key in changes:
                repaired += 1
                if int(row["volume"]) != int(changes[key]["fresh_tdx_volume"]):
                    mismatch += 1
        old_keys = {key_of(row) for row in old.iter_rows(named=True)}
        post_keys = {key_of(row) for row in post.iter_rows(named=True)}
        _require(not ((post_keys - old_keys) & FORBIDDEN_KEYS), "FORBIDDEN_300546_INSERTION")
    _require(repaired == CHANGED_KEY_N, "POST_REPAIRED_KEY_N_MISMATCH")
    _require(mismatch == 0, "POST_VOLUME_MISMATCH")
    return {"POST_REPAIRED_KEY_N": repaired, "POST_VOLUME_MISMATCH_N": mismatch}


def require_candidate_unchanged(
    *, candidate_root: Path, records: list[dict[str, Any]], before: dict[str, Any]
) -> None:
    after = candidate_snapshot(candidate_root, records)
    _require(after == before, "CANDIDATE_MUTATION")


def receipt_payload(
    *,
    pre: dict[str, Any],
    expected_post: dict[str, Any],
    post: dict[str, Any],
    records: list[dict[str, Any]],
    semantic: dict[str, Any],
    candidate_immutable: bool,
) -> dict[str, Any]:
    return {
        "REPORT": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "CANDIDATE_AUTHORITY_COMMIT": CANDIDATE_AUTHORITY_COMMIT,
        "PRE_INPUT_FILE_N": pre["INPUT_FILE_N"],
        "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
        "EXPECTED_POST_INPUT_FILE_N": expected_post["INPUT_FILE_N"],
        "EXPECTED_POST_INPUT_MANIFEST_HASH": expected_post["INPUT_MANIFEST_HASH"],
        "POST_INPUT_FILE_N": post["INPUT_FILE_N"],
        "POST_INPUT_MANIFEST_HASH": post["INPUT_MANIFEST_HASH"],
        "AFFECTED_FILE_N": AFFECTED_FILE_N,
        "PROMOTED_FILE_N": len(records),
        "UNAFFECTED_FILE_N": UNAFFECTED_FILE_N,
        "CHANGED_KEY_N": CHANGED_KEY_N,
        "POST_REPAIRED_KEY_N": semantic["POST_REPAIRED_KEY_N"],
        "POST_VOLUME_MISMATCH_N": semantic["POST_VOLUME_MISMATCH_N"],
        "ROW_COUNT_DELTA": 0,
        "SCHEMA_DELTA": 0,
        "CHANGED_COLUMNS": ["volume"],
        "TRANSACTION_STATE": "COMMITTED",
        "PROMOTION_COMPLETE": True,
        "ROLLBACK_BACKUP_FILE_N": len(records),
        "ROLLBACK_BACKUP_VERIFIED": all(record["rollback_backup_verified"] for record in records),
        "CANDIDATE_IMMUTABLE": candidate_immutable,
        "INPUT_MANIFEST_HASH": PRE_INPUT_MANIFEST_HASH,
        "CHANGED_KEY_MANIFEST_HASH": CHANGED_KEY_MANIFEST_HASH,
        "AFFECTED_FILE_MANIFEST_HASH": AFFECTED_FILE_MANIFEST_HASH,
        "CANDIDATE_DATASET_HASH": CANDIDATE_DATASET_HASH,
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "TDX_REFETCH_EXECUTED": False,
        "BAOSTOCK_EXECUTED": False,
        "CANONICAL_WRITE_EXECUTED": True,
        "CANONICAL_BYTES_MUTATED": True,
        "300546_MISSING_DAYS_MUTATED": False,
        "R4A9_CHECKPOINT_MUTATED": False,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
        "FILES": records,
    }


def markdown_report(receipt: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# R3 TDX VOLUME CANONICAL PROMOTION V01",
            "",
            f"AUTHOR_STATUS: `{receipt['AUTHOR_STATUS']}`",
            "",
            f"- BASE_HEAD: `{BASE_HEAD}`",
            f"- CANDIDATE_AUTHORITY_COMMIT: `{CANDIDATE_AUTHORITY_COMMIT}`",
            f"- PRE_INPUT_FILE_N: {receipt['PRE_INPUT_FILE_N']}",
            f"- PRE_INPUT_MANIFEST_HASH: `{receipt['PRE_INPUT_MANIFEST_HASH']}`",
            f"- EXPECTED_POST_INPUT_FILE_N: {receipt['EXPECTED_POST_INPUT_FILE_N']}",
            f"- EXPECTED_POST_INPUT_MANIFEST_HASH: `{receipt['EXPECTED_POST_INPUT_MANIFEST_HASH']}`",
            f"- POST_INPUT_FILE_N: {receipt['POST_INPUT_FILE_N']}",
            f"- POST_INPUT_MANIFEST_HASH: `{receipt['POST_INPUT_MANIFEST_HASH']}`",
            "",
            "## Promotion result",
            "",
            f"- AFFECTED_FILE_N: {receipt['AFFECTED_FILE_N']}",
            f"- PROMOTED_FILE_N: {receipt['PROMOTED_FILE_N']}",
            f"- UNAFFECTED_FILE_N: {receipt['UNAFFECTED_FILE_N']}",
            f"- CHANGED_KEY_N: {receipt['CHANGED_KEY_N']}",
            f"- POST_REPAIRED_KEY_N: {receipt['POST_REPAIRED_KEY_N']}",
            f"- POST_VOLUME_MISMATCH_N: {receipt['POST_VOLUME_MISMATCH_N']}",
            "- ROW_COUNT_DELTA: 0",
            "- SCHEMA_DELTA: 0",
            '- CHANGED_COLUMNS: `["volume"]`',
            f"- TRANSACTION_STATE: `{receipt['TRANSACTION_STATE']}`",
            f"- PROMOTION_COMPLETE: {str(receipt['PROMOTION_COMPLETE']).lower()}",
            "",
            "## Safety",
            "",
            f"- ROLLBACK_BACKUP_FILE_N: {receipt['ROLLBACK_BACKUP_FILE_N']}",
            f"- ROLLBACK_BACKUP_VERIFIED: {str(receipt['ROLLBACK_BACKUP_VERIFIED']).lower()}",
            f"- CANDIDATE_IMMUTABLE: {str(receipt['CANDIDATE_IMMUTABLE']).lower()}",
            "- NETWORK_PROVIDER_DATA_FETCH=NO",
            "- CANONICAL_WRITE_EXECUTED=true",
            "- CANONICAL_BYTES_MUTATED=true",
            "- 300546_MISSING_DAYS_MUTATED=false",
            "- R4A9_CHECKPOINT_MUTATED=false",
            "- R4A9_RESUME_AUTHORIZED=false",
            "- PRECLOSE_COMPLETE=false",
            "- PRODUCTION=false",
            "- FORWARD=false",
            "- TRADEPLAN=false",
            "",
        ]
    )


def collect_preflight(
    *, repo_root: Path, data_root: Path, candidate_root: Path
) -> dict[str, Any]:
    pre = require_pre_input(data_root, repo_root)
    _, candidate_manifest, candidate_before = require_candidate_authority(
        repo_root=repo_root, data_root=data_root, candidate_root=candidate_root
    )
    pre_after_audit = build_input_file_manifest(data_root)
    require_manifest_equal(pre, pre_after_audit, "PRE_INPUT_DURING_CANDIDATE_AUDIT")
    expected_post = build_expected_post_manifest(
        data_root=data_root,
        candidate_root=candidate_root,
        pre=pre,
        candidate_records=candidate_manifest["FILES"],
    )
    return {
        "pre": pre,
        "candidate_manifest": candidate_manifest,
        "candidate_before": candidate_before,
        "expected_post": expected_post,
    }


def run_promotion(
    *,
    repo_root: Path,
    data_root: Path,
    candidate_root: Path,
    transaction_root: Path,
    fault_injector: Callable[[int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    require_current_head(repo_root)
    candidate.require_isolated_candidate_root(data_root, candidate_root)
    require_exact_root(data_root, transaction_root, PROMOTION_ROOT_NAME)
    ensure_clean_transaction_root(transaction_root)
    ensure_output_paths_absent(repo_root)

    context = collect_preflight(repo_root=repo_root, data_root=data_root, candidate_root=candidate_root)
    pre = context["pre"]
    candidate_manifest = context["candidate_manifest"]
    candidate_records = candidate_manifest["FILES"]
    expected_post = context["expected_post"]

    transaction_root.mkdir(parents=True, exist_ok=False)
    transaction_created = True
    promoted: list[str] = []
    records: list[dict[str, Any]] = []
    try:
        write_transaction_state(transaction_root, "PREPARING")
        records = build_plan_records(
            data_root=data_root,
            candidate_root=candidate_root,
            transaction_root=transaction_root,
            pre=pre,
            candidate_records=candidate_records,
        )
        plan = {
            "REPORT": TASK,
            "BASE_HEAD": BASE_HEAD,
            "TRANSACTION_STATE": "PREPARING",
            "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
            "EXPECTED_POST_INPUT_MANIFEST_HASH": expected_post["INPUT_MANIFEST_HASH"],
            "EXPECTED_POST_INPUT_FILE_N": expected_post["INPUT_FILE_N"],
            "AFFECTED_FILE_N": AFFECTED_FILE_N,
            "FILES": records,
        }
        write_json_atomic(transaction_root / "promotion_plan.json", plan)
        write_json_atomic(transaction_root / "expected_post_input_manifest.json", expected_post)
        write_json_atomic(
            repo_root / "reports/implementation" / EXPECTED_POST_REPORT_NAME, expected_post
        )
        for record in records:
            record["source_path"] = str(data_root / record["relative_path"])
        backed_up = prepare_backups(transaction_root, records)
        _require(len(backed_up) == AFFECTED_FILE_N, "ROLLBACK_BACKUP_INCOMPLETE")
        write_json_atomic(transaction_root / "promotion_plan.json", {**plan, "TRANSACTION_STATE": "PREPARED", "FILES": records})
        write_transaction_state(transaction_root, "PREPARED")
        write_journal(transaction_root, state="PREPARED", files=backed_up, key="backed_up_files")

        pre_commit = build_input_file_manifest(data_root)
        require_pre_commit_stable(pre, pre_commit)
        require_candidate_unchanged(
            candidate_root=candidate_root, records=candidate_records, before=context["candidate_before"]
        )
        promote_files(
            data_root=data_root,
            candidate_root=candidate_root,
            transaction_root=transaction_root,
            records=records,
            promoted=promoted,
            fault_injector=fault_injector,
        )
        _require(len(promoted) == AFFECTED_FILE_N, "PROMOTED_FILE_N_MISMATCH")
        post = validate_post_manifest(
            data_root=data_root, expected_post=expected_post, pre=pre, records=records
        )
        post_by_path = {row["relative_path"]: row for row in post["FILES"]}
        for record in records:
            record["post_sha256"] = post_by_path[record["relative_path"]]["sha256"]
            record["rollback_status"] = "AVAILABLE"
        changed = candidate_audit.candidate.load_changed_manifest(repo_root)
        semantic = validate_semantic_post(
            data_root=data_root,
            transaction_root=transaction_root,
            records=records,
            changed=changed,
        )
        require_candidate_unchanged(
            candidate_root=candidate_root, records=candidate_records, before=context["candidate_before"]
        )
        receipt = receipt_payload(
            pre=pre,
            expected_post=expected_post,
            post=post,
            records=records,
            semantic=semantic,
            candidate_immutable=True,
        )
        write_json_atomic(transaction_root / "observed_post_input_manifest.json", post)
        write_json_atomic(transaction_root / "promotion_receipt.json", receipt)
        write_json_atomic(
            repo_root / "reports/implementation" / OBSERVED_POST_REPORT_NAME, post
        )
        write_json_atomic(repo_root / "reports/implementation" / RECEIPT_REPORT_NAME, receipt)
        write_json_atomic(repo_root / "reports/implementation" / REPORT_NAME, receipt)
        write_bytes_atomic(
            repo_root / "reports/implementation" / REPORT_MD_NAME,
            markdown_report(receipt).encode("utf-8"),
        )
        write_transaction_state(
            transaction_root, "COMMITTED", promoted_file_n=AFFECTED_FILE_N, promotion_complete=True
        )
        return receipt
    except Exception as exc:
        if transaction_created:
            try:
                if promoted:
                    rollback_files(
                        data_root=data_root,
                        transaction_root=transaction_root,
                        records=records,
                        promoted=promoted,
                    )
                    restored = build_input_file_manifest(data_root)
                    require_manifest_equal(pre, restored, "ROLLBACK_PRE_INPUT")
                    write_transaction_state(transaction_root, "ROLLED_BACK", promoted_file_n=0, promotion_complete=False)
                else:
                    write_transaction_state(transaction_root, "RECOVERY_REQUIRED", promoted_file_n=0, promotion_complete=False)
            except Exception as rollback_exc:
                try:
                    write_transaction_state(
                        transaction_root,
                        "RECOVERY_REQUIRED",
                        promoted_file_n=len(promoted),
                        promotion_complete=False,
                    )
                except Exception:
                    pass
                raise PromotionError(f"RECOVERY_REQUIRED:{rollback_exc}") from exc
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError(f"PROMOTION_FAILED:{exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-head", required=True)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--candidate-root", type=Path, default=None)
    parser.add_argument("--transaction-root", type=Path, default=None)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    candidate_root = args.candidate_root or args.data_root / "staging" / CANDIDATE_ROOT_NAME
    transaction_root = args.transaction_root or args.data_root / "staging" / PROMOTION_ROOT_NAME
    if args.base_head != BASE_HEAD:
        print("BASE_HEAD_ARGUMENT_MISMATCH")
        return 2
    try:
        receipt = run_promotion(
            repo_root=repo_root,
            data_root=args.data_root,
            candidate_root=candidate_root,
            transaction_root=transaction_root,
        )
    except (PromotionError, candidate.CandidateRepairError) as exc:
        print(str(exc))
        return 2
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
