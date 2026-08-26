#!/usr/bin/env python3
"""Build an isolated parquet candidate for the audited R3 volume repair.

The tool is offline and deliberately writes only under the task-owned staging
root.  It does not import any provider client and never opens canonical files
for writing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent / "audits"))
from r3_tdx_volume_rebuild_scope_v01 import build_input_file_manifest  # noqa: E402


TASK = "R3_TDX_VOLUME_CANONICAL_REPAIR_CANDIDATE_V01"
BASE_HEAD = "1fd460515687346957de3eb5d1231c1f4df461ef"
STAGING_AUDIT_COMMIT = BASE_HEAD
INPUT_MANIFEST_HASH = "f9025a5cbc52d757fdc05d9e6ebb5f3c75c1cd93414a2f6c51bb83314594a6ec"
CHANGED_KEY_MANIFEST_HASH = "f1cdb9d5416535e76631673479ef1fc36539a8626ee18026a581f6e1191b62c7"
CHANGED_KEY_N = 1169
EXPECTED_PROVABLY_AFFECTED_N = 32
EXPECTED_AMBIGUOUS_N = 1137
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
STAGING_ROOT_NAME = "r3_tdx_volume_canonical_repair_candidate_v01"
CHANGED_MANIFEST_NAME = "R3_TDX_VOLUME_TARGETED_REFETCH_CHANGED_MANIFEST_V01_1.json"
REQUIRED_COLUMNS = {
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
}
FORBIDDEN_MISSING_DAYS = {
    ("300546.SZ", "2016-09-29"),
    ("300546.SZ", "2016-10-10"),
}


class CandidateRepairError(RuntimeError):
    """Fail-closed candidate construction error."""


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
        raise CandidateRepairError(f"UNREADABLE_JSON:{path}") from exc


def write_canonical_json(path: Path, payload: Any) -> str:
    data = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256_bytes(data)


def date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def key_of(row: dict[str, Any]) -> tuple[str, str]:
    return str(row["symbol"]), date_text(row["trade_date"])


def require_input_stable(pre: dict[str, Any], post: dict[str, Any]) -> None:
    if (
        pre.get("INPUT_FILE_N") != post.get("INPUT_FILE_N")
        or pre.get("INPUT_MANIFEST_HASH") != post.get("INPUT_MANIFEST_HASH")
        or pre.get("FILES") != post.get("FILES")
    ):
        raise CandidateRepairError("INPUT_DRIFT_DURING_CANDIDATE_BUILD")


def verify_current_input(data_root: Path, repo_root: Path) -> dict[str, Any]:
    frozen_path = repo_root / "reports/implementation/R3_TDX_VOLUME_REBUILD_INPUT_MANIFEST_V01.json"
    frozen = load_json(frozen_path)
    live = build_input_file_manifest(data_root)
    if (
        live.get("INPUT_FILE_N") != 2580
        or live.get("INPUT_MANIFEST_HASH") != INPUT_MANIFEST_HASH
        or live.get("INPUT_FILE_N") != frozen.get("INPUT_FILE_N")
        or live.get("FILES") != frozen.get("FILES")
    ):
        raise CandidateRepairError("INPUT_MANIFEST_GATE_FAILED")
    return live


def load_changed_manifest(repo_root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = repo_root / "reports/implementation" / CHANGED_MANIFEST_NAME
    if sha256_file(path) != CHANGED_KEY_MANIFEST_HASH:
        raise CandidateRepairError("CHANGED_KEY_MANIFEST_HASH_MISMATCH")
    rows = load_json(path)
    if not isinstance(rows, list) or len(rows) != CHANGED_KEY_N:
        raise CandidateRepairError("CHANGED_KEY_N_MISMATCH")
    if rows != sorted(rows, key=lambda row: (row["symbol"], row["trade_date"])):
        raise CandidateRepairError("CHANGED_MANIFEST_NOT_DETERMINISTICALLY_SORTED")
    keys = [key_of(row) for row in rows]
    if len(set(keys)) != CHANGED_KEY_N:
        raise CandidateRepairError("CHANGED_MANIFEST_DUPLICATE_KEY")
    if sum(row.get("classification") == "PROVABLY_AFFECTED" for row in rows) != EXPECTED_PROVABLY_AFFECTED_N:
        raise CandidateRepairError("CHANGED_AFFECTED_N_MISMATCH")
    if sum(row.get("classification") == "AMBIGUOUS" for row in rows) != EXPECTED_AMBIGUOUS_N:
        raise CandidateRepairError("CHANGED_AMBIGUOUS_N_MISMATCH")
    for row in rows:
        if row.get("fetch_status") != "RESOLVED":
            raise CandidateRepairError("CHANGED_UNRESOLVED_ROW")
        if int(row["fresh_tdx_volume"]) == int(row["old_volume"]):
            raise CandidateRepairError("CHANGED_ROW_IS_NOT_CHANGED")
        if key_of(row) in FORBIDDEN_MISSING_DAYS:
            raise CandidateRepairError("FORBIDDEN_300546_KEY_IN_CHANGED_MANIFEST")
    return {key_of(row): row for row in rows}


def build_partition_index(input_manifest: dict[str, Any]) -> dict[str, str]:
    date_to_path: dict[str, str] = {}
    pattern = re.compile(r"trade_date=(\d{4}-\d{2}-\d{2})/")
    for file_row in input_manifest["FILES"]:
        relative = file_row["relative_path"]
        match = pattern.search(relative)
        if match is None:
            continue
        partition_date = match.group(1)
        if partition_date in date_to_path:
            raise CandidateRepairError("CANONICAL_PARTITION_DATE_DUPLICATE")
        date_to_path[partition_date] = relative
    return date_to_path


def group_changed_by_file(
    changed: dict[tuple[str, str], dict[str, Any]], date_to_path: dict[str, str]
) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    by_file: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for key, row in changed.items():
        relative = date_to_path.get(key[1])
        if relative is None:
            raise CandidateRepairError("CHANGED_KEY_CANONICAL_FILE_MISSING")
        by_file[relative][key] = row
    return dict(sorted(by_file.items()))


def validate_source_preconditions(
    source: pl.DataFrame, changes: dict[tuple[str, str], dict[str, Any]], relative_path: str
) -> None:
    if set(source.columns) != REQUIRED_COLUMNS or source.columns != [
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
    ]:
        raise CandidateRepairError(f"SCHEMA_PRECONDITION_FAILED:{relative_path}")
    for key, change in changes.items():
        hits = [row for row in source.iter_rows(named=True) if key_of(row) == key]
        if len(hits) != 1:
            raise CandidateRepairError(f"CANONICAL_KEY_CARDINALITY_FAILED:{relative_path}:{key}")
        if int(hits[0]["volume"]) != int(change["old_volume"]):
            raise CandidateRepairError(f"CANONICAL_OLD_VOLUME_MISMATCH:{relative_path}:{key}")


def _validate_candidate_diff(
    source: pl.DataFrame,
    candidate: pl.DataFrame,
    changes: dict[tuple[str, str], dict[str, Any]],
    relative_path: str,
) -> dict[str, Any]:
    if source.height != candidate.height:
        raise CandidateRepairError(f"ROW_COUNT_DELTA:{relative_path}")
    if source.columns != candidate.columns or source.schema != candidate.schema:
        raise CandidateRepairError(f"SCHEMA_DELTA:{relative_path}")
    for column in source.columns:
        if column != "volume" and not source.get_column(column).equals(candidate.get_column(column)):
            raise CandidateRepairError(f"NON_VOLUME_CELL_DELTA:{relative_path}:{column}")
    changed_keys: set[tuple[str, str]] = set()
    changed_cells = 0
    source_rows = source.iter_rows(named=True)
    candidate_rows = candidate.iter_rows(named=True)
    for source_row, candidate_row in zip(source_rows, candidate_rows):
        key = key_of(source_row)
        if key_of(candidate_row) != key:
            raise CandidateRepairError(f"KEY_CELL_DELTA:{relative_path}:{key}")
        if int(source_row["volume"]) != int(candidate_row["volume"]):
            changed_cells += 1
            changed_keys.add(key)
        if key in changes:
            if int(candidate_row["volume"]) != int(changes[key]["fresh_tdx_volume"]):
                raise CandidateRepairError(f"FRESH_VOLUME_NOT_APPLIED:{relative_path}:{key}")
        elif source_row["volume"] != candidate_row["volume"]:
            raise CandidateRepairError(f"UNEXPECTED_VOLUME_DELTA:{relative_path}:{key}")
    expected_keys = set(changes)
    if changed_keys != expected_keys or changed_cells != len(expected_keys):
        raise CandidateRepairError(f"EXACT_VOLUME_DIFF_FAILED:{relative_path}")
    return {
        "ROW_COUNT_DELTA": 0,
        "SCHEMA_DELTA": 0,
        "CHANGED_ROW_N": len(changed_keys),
        "CHANGED_CELL_N": changed_cells,
        "CHANGED_COLUMNS": ["volume"],
    }


def build_candidate_file(
    source_path: Path,
    candidate_path: Path,
    relative_path: str,
    changes: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    source = pl.read_parquet(source_path)
    validate_source_preconditions(source, changes, relative_path)
    new_volumes: list[int] = []
    for row in source.iter_rows(named=True):
        key = key_of(row)
        new_volumes.append(
            int(changes[key]["fresh_tdx_volume"]) if key in changes else int(row["volume"])
        )
    candidate = source.with_columns(
        pl.Series("volume", new_volumes, dtype=source.schema["volume"])
    )
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_parquet(candidate_path)
    written = pl.read_parquet(candidate_path)
    diff = _validate_candidate_diff(source, written, changes, relative_path)
    return {
        "relative_path": relative_path,
        "candidate_relative_path": relative_path,
        "source_sha256": sha256_file(source_path),
        "candidate_sha256": sha256_file(candidate_path),
        "changed_key_n": len(changes),
        "row_count": source.height,
        "schema": {name: str(dtype) for name, dtype in source.schema.items()},
        **diff,
    }


def _hash_file_manifest(records: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(records))


def _candidate_dataset_hash(records: list[dict[str, Any]]) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            [
                {"relative_path": row["relative_path"], "candidate_sha256": row["candidate_sha256"]}
                for row in records
            ]
        )
    )


def run_candidate(
    *, repo_root: Path, data_root: Path, stage_root: Path
) -> dict[str, Any]:
    if stage_root.exists():
        raise CandidateRepairError("CANDIDATE_STAGE_ALREADY_EXISTS")
    temporary_root = stage_root.with_name(stage_root.name + ".in_progress")
    if temporary_root.exists() and not temporary_root.is_dir():
        raise CandidateRepairError("CANDIDATE_IN_PROGRESS_ROOT_NOT_DIRECTORY")

    input_pre = verify_current_input(data_root, repo_root)
    changed = load_changed_manifest(repo_root)
    date_to_path = build_partition_index(input_pre)
    by_file = group_changed_by_file(changed, date_to_path)
    # A prior invocation may have completed the parquet loop but failed before
    # the final input recheck/publication.  Reuse that task-owned temporary
    # root only after rebuilding and revalidating every expected file below;
    # no deletion or silent partial acceptance is allowed.
    temporary_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    try:
        for relative_path, changes in by_file.items():
            source_path = data_root / relative_path
            if not source_path.is_file():
                raise CandidateRepairError(f"CANONICAL_SOURCE_FILE_MISSING:{relative_path}")
            candidate_path = temporary_root / relative_path
            records.append(
                build_candidate_file(source_path, candidate_path, relative_path, changes)
            )
        records.sort(key=lambda row: row["relative_path"])
        expected_candidate_paths = {
            str((temporary_root / relative_path).relative_to(temporary_root))
            for relative_path in by_file
        }
        actual_candidate_paths = {
            str(path.relative_to(temporary_root))
            for path in temporary_root.rglob("*.parquet")
        }
        if actual_candidate_paths != expected_candidate_paths:
            raise CandidateRepairError("CANDIDATE_FILE_SET_MISMATCH")
        input_post = build_input_file_manifest(data_root)
        require_input_stable(input_pre, input_post)
        replacement_manifest = {
            "REPORT": TASK,
            "INPUT_MANIFEST_HASH": INPUT_MANIFEST_HASH,
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "CANONICAL_WRITE_EXECUTED": False,
            "CANONICAL_BYTES_MUTATED": False,
            "CHANGED_KEY_MANIFEST_HASH": CHANGED_KEY_MANIFEST_HASH,
            "AFFECTED_FILE_N": len(records),
            "CHANGED_KEY_N": sum(row["changed_key_n"] for row in records),
            "FILES": records,
            "AFFECTED_FILE_MANIFEST_HASH": _hash_file_manifest(records),
            "CANDIDATE_DATASET_HASH": _candidate_dataset_hash(records),
        }
        if replacement_manifest["CHANGED_KEY_N"] != CHANGED_KEY_N:
            raise CandidateRepairError("CANDIDATE_CHANGED_KEY_N_MISMATCH")
        validation = {
            "REPORT": TASK,
            "INPUT_FILE_N": input_pre["INPUT_FILE_N"],
            "INPUT_MANIFEST_HASH": INPUT_MANIFEST_HASH,
            "ROW_COUNT_DELTA": 0,
            "SCHEMA_DELTA": 0,
            "CHANGED_ROW_N": CHANGED_KEY_N,
            "CHANGED_CELL_N": CHANGED_KEY_N,
            "CHANGED_COLUMNS": ["volume"],
            "AFFECTED_FILE_N": len(records),
            "AFFECTED_FILE_MANIFEST_HASH": replacement_manifest["AFFECTED_FILE_MANIFEST_HASH"],
            "CANDIDATE_DATASET_HASH": replacement_manifest["CANDIDATE_DATASET_HASH"],
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
        write_canonical_json(temporary_root / "candidate_repair_manifest.json", replacement_manifest)
        write_canonical_json(temporary_root / "candidate_validation_report.json", validation)
        os.replace(temporary_root, stage_root)
        return {"MANIFEST": replacement_manifest, "VALIDATION": validation}
    except Exception:
        raise


def markdown_report(result: dict[str, Any]) -> str:
    manifest = result["MANIFEST"]
    validation = result["VALIDATION"]
    return "\n".join(
        [
            "# R3 TDX VOLUME CANONICAL REPAIR CANDIDATE V01",
            "",
            "AUTHOR_STATUS: `PASS_PENDING_SOL_AUDIT`",
            "",
            f"- BASE_HEAD: `{BASE_HEAD}`",
            f"- STAGING_AUDIT_COMMIT: `{STAGING_AUDIT_COMMIT}`",
            f"- INPUT_MANIFEST_HASH: `{INPUT_MANIFEST_HASH}`",
            f"- CHANGED_KEY_MANIFEST_HASH: `{CHANGED_KEY_MANIFEST_HASH}`",
            "",
            "## Candidate binding",
            "",
            f"- AFFECTED_FILE_N: {manifest['AFFECTED_FILE_N']}",
            f"- AFFECTED_FILE_MANIFEST_HASH: `{manifest['AFFECTED_FILE_MANIFEST_HASH']}`",
            f"- CANDIDATE_DATASET_HASH: `{manifest['CANDIDATE_DATASET_HASH']}`",
            f"- ROW_COUNT_DELTA: {validation['ROW_COUNT_DELTA']}",
            f"- SCHEMA_DELTA: {validation['SCHEMA_DELTA']}",
            f"- CHANGED_ROW_N: {validation['CHANGED_ROW_N']}",
            f"- CHANGED_CELL_N: {validation['CHANGED_CELL_N']}",
            "- CHANGED_COLUMNS: `[\"volume\"]`",
            "",
            "## Safety",
            "",
            "- NETWORK_PROVIDER_DATA_FETCH=NO",
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
    repo_root = Path(__file__).resolve().parents[1]
    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, text=True, capture_output=True
    ).stdout.strip()
    if current_head != args.base_head:
        print("BASE_HEAD_MISMATCH")
        return 2
    stage_root = args.stage_root or args.data_root / "staging" / STAGING_ROOT_NAME
    try:
        result = run_candidate(repo_root=repo_root, data_root=args.data_root, stage_root=stage_root)
        implementation = repo_root / "reports/implementation"
        report = {
            "REPORT": TASK,
            "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
            "EXECUTION_BASE_HEAD": BASE_HEAD,
            "STAGING_AUDIT_COMMIT": STAGING_AUDIT_COMMIT,
            **result["MANIFEST"],
            **result["VALIDATION"],
        }
        write_canonical_json(implementation / "R3_TDX_VOLUME_CANONICAL_REPAIR_CANDIDATE_V01.json", report)
        (implementation / "R3_TDX_VOLUME_CANONICAL_REPAIR_CANDIDATE_V01.md").write_text(
            markdown_report(result), encoding="utf-8"
        )
    except CandidateRepairError as exc:
        print(str(exc))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
