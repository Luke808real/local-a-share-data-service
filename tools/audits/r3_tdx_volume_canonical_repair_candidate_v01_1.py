#!/usr/bin/env python3
"""Offline revalidation of the existing R3 canonical-repair candidate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_r3_tdx_volume_canonical_repair_candidate_v01 as candidate  # noqa: E402


TASK = "R3_TDX_VOLUME_CANONICAL_REPAIR_CANDIDATE_V01_1"
BASE_HEAD = "0ae0a6f8aa578facdc38950457c672623483406f"
INPUT_MANIFEST_HASH = "f9025a5cbc52d757fdc05d9e6ebb5f3c75c1cd93414a2f6c51bb83314594a6ec"
CHANGED_KEY_MANIFEST_HASH = "f1cdb9d5416535e76631673479ef1fc36539a8626ee18026a581f6e1191b62c7"
AFFECTED_FILE_N = 432
AFFECTED_FILE_MANIFEST_HASH = "899490446e21a7861f27e2498a917efeab1e99cb31e21f442d824fed80e119f4"
CANDIDATE_DATASET_HASH = "6fc5eebe32b7f9f5d8530289ba1590f867e46d02721c6a780e1ed87429b1ea2c"
CHANGED_KEY_N = 1169
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
STAGING_ROOT_NAME = candidate.STAGING_ROOT_NAME


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise candidate.CandidateRepairError(message)


def _load(path: Path) -> Any:
    return candidate.load_json(path)


def _safe_join(root: Path, relative_path: str, label: str) -> Path:
    relative = Path(relative_path)
    _require(not relative.is_absolute() and ".." not in relative.parts, f"{label}_PATH_TRAVERSAL")
    root_resolved = root.resolve(strict=True)
    resolved = (root / relative).resolve(strict=False)
    _require(resolved.is_relative_to(root_resolved), f"{label}_PATH_ESCAPE")
    return resolved


def _validate_existing_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    _require(manifest.get("INPUT_MANIFEST_HASH") == INPUT_MANIFEST_HASH, "CANDIDATE_INPUT_HASH_MISMATCH")
    _require(
        manifest.get("CHANGED_KEY_MANIFEST_HASH") == CHANGED_KEY_MANIFEST_HASH,
        "CANDIDATE_CHANGED_MANIFEST_HASH_MISMATCH",
    )
    _require(manifest.get("AFFECTED_FILE_N") == AFFECTED_FILE_N, "CANDIDATE_AFFECTED_FILE_N_MISMATCH")
    _require(manifest.get("CHANGED_KEY_N") == CHANGED_KEY_N, "CANDIDATE_CHANGED_KEY_N_MISMATCH")
    _require(
        manifest.get("AFFECTED_FILE_MANIFEST_HASH") == AFFECTED_FILE_MANIFEST_HASH,
        "CANDIDATE_FILE_MANIFEST_HASH_MISMATCH",
    )
    _require(
        manifest.get("CANDIDATE_DATASET_HASH") == CANDIDATE_DATASET_HASH,
        "CANDIDATE_DATASET_HASH_MISMATCH",
    )
    records = manifest.get("FILES")
    _require(isinstance(records, list) and len(records) == AFFECTED_FILE_N, "CANDIDATE_FILE_RECORD_N_MISMATCH")
    _require(
        records == sorted(records, key=lambda row: row["relative_path"]),
        "CANDIDATE_FILE_MANIFEST_NOT_SORTED",
    )
    paths = [row.get("relative_path") for row in records]
    _require(all(isinstance(path, str) for path in paths), "CANDIDATE_FILE_PATH_INVALID")
    _require(len(set(paths)) == AFFECTED_FILE_N, "CANDIDATE_FILE_MANIFEST_DUPLICATE")
    _require(
        manifest.get("NETWORK_PROVIDER_DATA_FETCH") == "NO",
        "CANDIDATE_NETWORK_SAFETY_MISMATCH",
    )
    _require(manifest.get("CANONICAL_WRITE_EXECUTED") is False, "CANDIDATE_CANONICAL_WRITE_FLAG_MISMATCH")
    _require(manifest.get("CANONICAL_BYTES_MUTATED") is False, "CANDIDATE_CANONICAL_BYTES_FLAG_MISMATCH")
    return records


def _validate_existing_validation(validation: dict[str, Any]) -> None:
    expected = {
        "INPUT_MANIFEST_HASH": INPUT_MANIFEST_HASH,
        "ROW_COUNT_DELTA": 0,
        "SCHEMA_DELTA": 0,
        "CHANGED_ROW_N": CHANGED_KEY_N,
        "CHANGED_CELL_N": CHANGED_KEY_N,
        "CHANGED_COLUMNS": ["volume"],
        "AFFECTED_FILE_N": AFFECTED_FILE_N,
        "AFFECTED_FILE_MANIFEST_HASH": AFFECTED_FILE_MANIFEST_HASH,
        "CANDIDATE_DATASET_HASH": CANDIDATE_DATASET_HASH,
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "CANONICAL_WRITE_EXECUTED": False,
        "CANONICAL_BYTES_MUTATED": False,
        "300546_MISSING_DAYS_MUTATED": False,
        "R4A9_CHECKPOINT_MUTATED": False,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
    }
    for key, value in expected.items():
        _require(validation.get(key) == value, f"CANDIDATE_VALIDATION_MISMATCH:{key}")


def audit_existing_candidate(
    *, repo_root: Path, data_root: Path, stage_root: Path
) -> dict[str, Any]:
    # This is intentionally the first operation that can precede any report
    # output or candidate-root interaction in this audit path.
    candidate.require_isolated_candidate_root(data_root, stage_root)
    _require(stage_root.is_dir() and not stage_root.is_symlink(), "CANDIDATE_STAGE_MISSING")

    manifest_path = stage_root / "candidate_repair_manifest.json"
    validation_path = stage_root / "candidate_validation_report.json"
    manifest = _load(manifest_path)
    validation = _load(validation_path)
    _require(isinstance(manifest, dict), "CANDIDATE_MANIFEST_INVALID")
    _require(isinstance(validation, dict), "CANDIDATE_VALIDATION_INVALID")
    records = _validate_existing_manifest(manifest)
    _validate_existing_validation(validation)

    live_input = candidate.verify_current_input(data_root, repo_root)
    changed = candidate.load_changed_manifest(repo_root)
    date_to_path = candidate.build_partition_index(live_input)
    expected_by_file = candidate.group_changed_by_file(changed, date_to_path)
    expected_paths = set(expected_by_file)
    manifest_paths = {row["relative_path"] for row in records}
    _require(manifest_paths == expected_paths, "CANDIDATE_MANIFEST_FILE_SET_MISMATCH")

    stage_files = [path for path in stage_root.rglob("*") if path.is_file()]
    stage_symlinks = [path for path in stage_root.rglob("*") if path.is_symlink()]
    _require(not stage_symlinks, "CANDIDATE_STAGE_SYMLINK_PRESENT")
    for relative_path in manifest_paths:
        candidate_path = _safe_join(stage_root, relative_path, "CANDIDATE")
        _require(
            candidate_path.is_file() and not candidate_path.is_symlink(),
            f"CANDIDATE_FILE_MISSING:{relative_path}",
        )
    actual_parquets = {
        str(path.relative_to(stage_root)) for path in stage_files if path.suffix == ".parquet"
    }
    _require(actual_parquets == manifest_paths, "CANDIDATE_PARQUET_FILE_SET_MISMATCH")
    allowed_files = manifest_paths | {manifest_path.name, validation_path.name}
    _require(
        {str(path.relative_to(stage_root)) for path in stage_files} == allowed_files,
        "CANDIDATE_STAGE_FILE_SET_MISMATCH",
    )

    source_by_path = {row["relative_path"]: row for row in live_input["FILES"]}
    changed_key_n = 0
    revalidated_file_n = 0
    for record in records:
        relative_path = record["relative_path"]
        _require(relative_path in source_by_path, f"CANONICAL_SOURCE_MANIFEST_ENTRY_MISSING:{relative_path}")
        source_path = _safe_join(data_root, relative_path, "CANONICAL_SOURCE")
        candidate_path = _safe_join(stage_root, relative_path, "CANDIDATE")
        _require(source_path.is_file(), f"CANONICAL_SOURCE_FILE_MISSING:{relative_path}")
        _require(candidate_path.is_file() and not candidate_path.is_symlink(), f"CANDIDATE_FILE_MISSING:{relative_path}")
        _require(
            candidate.sha256_file(source_path) == record.get("source_sha256") == source_by_path[relative_path]["sha256"],
            f"CANONICAL_SOURCE_SHA_MISMATCH:{relative_path}",
        )
        _require(
            candidate.sha256_file(candidate_path) == record.get("candidate_sha256"),
            f"CANDIDATE_SHA_MISMATCH:{relative_path}",
        )

        source_frame = pl.read_parquet(source_path)
        candidate_frame = pl.read_parquet(candidate_path)
        changes = expected_by_file[relative_path]
        candidate.validate_source_preconditions(source_frame, changes, relative_path)
        diff = candidate._validate_candidate_diff(
            source_frame, candidate_frame, changes, relative_path
        )
        _require(record.get("changed_key_n") == len(changes), f"CHANGED_KEY_N_MISMATCH:{relative_path}")
        _require(diff["ROW_COUNT_DELTA"] == 0, f"ROW_COUNT_DELTA:{relative_path}")
        _require(diff["SCHEMA_DELTA"] == 0, f"SCHEMA_DELTA:{relative_path}")
        _require(diff["CHANGED_ROW_N"] == len(changes), f"CHANGED_ROW_N_MISMATCH:{relative_path}")
        _require(diff["CHANGED_CELL_N"] == len(changes), f"CHANGED_CELL_N_MISMATCH:{relative_path}")
        _require(diff["CHANGED_COLUMNS"] == ["volume"], f"CHANGED_COLUMNS_MISMATCH:{relative_path}")
        changed_key_n += len(changes)
        revalidated_file_n += 1

    _require(changed_key_n == CHANGED_KEY_N, "CHANGED_KEY_N_TOTAL_MISMATCH")
    _require(
        candidate._hash_file_manifest(records) == AFFECTED_FILE_MANIFEST_HASH,
        "AFFECTED_FILE_MANIFEST_HASH_RECOMPUTE_MISMATCH",
    )
    _require(
        candidate._candidate_dataset_hash(records) == CANDIDATE_DATASET_HASH,
        "CANDIDATE_DATASET_HASH_RECOMPUTE_MISMATCH",
    )

    return {
        "REPORT": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "EXECUTION_BASE_HEAD": BASE_HEAD,
        "INPUT_FILE_N": live_input["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": INPUT_MANIFEST_HASH,
        "CHANGED_KEY_MANIFEST_HASH": CHANGED_KEY_MANIFEST_HASH,
        "AFFECTED_FILE_N": AFFECTED_FILE_N,
        "AFFECTED_FILE_MANIFEST_HASH": AFFECTED_FILE_MANIFEST_HASH,
        "CANDIDATE_DATASET_HASH": CANDIDATE_DATASET_HASH,
        "CHANGED_KEY_N": CHANGED_KEY_N,
        "CHANGED_ROW_N": CHANGED_KEY_N,
        "CHANGED_CELL_N": CHANGED_KEY_N,
        "CHANGED_COLUMNS": ["volume"],
        "ROW_COUNT_DELTA": 0,
        "SCHEMA_DELTA": 0,
        "FILES_REVALIDATED_N": revalidated_file_n,
        "CANDIDATE_REUSED": True,
        "CANDIDATE_REBUILT": False,
        "ISOLATED_STAGE_ROOT_ENFORCED": True,
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
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
    }


def markdown_report(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# R3 TDX VOLUME CANONICAL REPAIR CANDIDATE V01.1",
            "",
            f"AUTHOR_STATUS: `{report['AUTHOR_STATUS']}`",
            "",
            f"- BASE_HEAD: `{BASE_HEAD}`",
            f"- INPUT_MANIFEST_HASH: `{INPUT_MANIFEST_HASH}`",
            f"- CHANGED_KEY_MANIFEST_HASH: `{CHANGED_KEY_MANIFEST_HASH}`",
            "",
            "## Existing candidate revalidation",
            "",
            f"- AFFECTED_FILE_N: {AFFECTED_FILE_N}",
            f"- AFFECTED_FILE_MANIFEST_HASH: `{AFFECTED_FILE_MANIFEST_HASH}`",
            f"- CANDIDATE_DATASET_HASH: `{CANDIDATE_DATASET_HASH}`",
            f"- CHANGED_KEY_N: {CHANGED_KEY_N}",
            f"- CHANGED_ROW_N: {CHANGED_KEY_N}",
            f"- CHANGED_CELL_N: {CHANGED_KEY_N}",
            '- CHANGED_COLUMNS: `["volume"]`',
            "- ROW_COUNT_DELTA: 0",
            "- SCHEMA_DELTA: 0",
            f"- FILES_REVALIDATED_N: {report['FILES_REVALIDATED_N']}",
            "",
            "## Safety",
            "",
            "- ISOLATED_STAGE_ROOT_ENFORCED=true",
            "- CANDIDATE_REUSED=true",
            "- CANDIDATE_REBUILT=false",
            "- NETWORK_PROVIDER_DATA_FETCH=NO",
            "- TDX_REFETCH_EXECUTED=false",
            "- BAOSTOCK_EXECUTED=false",
            "- CANONICAL_WRITE_EXECUTED=false",
            "- CANONICAL_BYTES_MUTATED=false",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-head", required=True)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path, default=None)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, text=True, capture_output=True
    ).stdout.strip()
    if current_head != args.base_head:
        print("BASE_HEAD_MISMATCH")
        return 2
    stage_root = args.stage_root or args.data_root / "staging" / STAGING_ROOT_NAME
    try:
        report = audit_existing_candidate(
            repo_root=repo_root, data_root=args.data_root, stage_root=stage_root
        )
        implementation = repo_root / "reports/implementation"
        candidate.write_canonical_json(
            implementation / "R3_TDX_VOLUME_CANONICAL_REPAIR_CANDIDATE_V01_1.json", report
        )
        (implementation / "R3_TDX_VOLUME_CANONICAL_REPAIR_CANDIDATE_V01_1.md").write_text(
            markdown_report(report), encoding="utf-8"
        )
    except candidate.CandidateRepairError as exc:
        print(str(exc))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
