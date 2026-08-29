#!/usr/bin/env python3
"""Create the offline R4A9 status-conflict resume checkpoint generation."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ashare_data.r4a_preclose_status_conflict_overlay import (  # noqa: E402
    OVERLAY_CONTRACT, OVERLAY_KEYSET_HASH, OVERLAY_KEY_N, load_proven_traded_status_conflicts,
)
import rebase_r4a9_checkpoint_lineage_v01 as lineage  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
TASK = "R4A_PRECLOSE_PROVEN_TRADED_STATUS_CONFLICT_FALLBACK_V01"
BASE_HEAD = "c58f18475fd6284e6d812388ff042f24c3e6d6ef"
UPSTREAM_CHECKPOINT_SHA256 = "b3cf88438c94a46d077e0d37df6b70cb45d29c741e646264a061dc0ea8d804de"
DAILY_INPUT_MANIFEST_HASH = "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"
UPSTREAM_SAFE_ADAPTER_AUTHORITY_SHA = "795b1b8f6b688ecc2e94f85c09d80c365e648920"
STAGE_NAME = "r4a9-preclose-resume-status-conflict-v01"
SCHEMA = "R4A9_STATUS_CONFLICT_RESUME_V01"


class CheckpointError(RuntimeError):
    pass


def _require(value: bool, code: str) -> None:
    if not value:
        raise CheckpointError(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(path: Path) -> str:
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    _require((before.st_size, before.st_mtime_ns, before.st_ino) == (after.st_size, after.st_mtime_ns, after.st_ino), "CHECKPOINT_FILE_MUTATED_DURING_READ")
    return digest


def _write(stage: Path, name: str, payload: dict[str, Any]) -> Path:
    path = stage / name
    temp = stage / f".{name}.tmp-{os.getpid()}"
    stage.mkdir(parents=True, exist_ok=False) if not stage.exists() else None
    try:
        temp.write_bytes(_canonical(payload))
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise CheckpointError("CHECKPOINT_WRITE_FAILED") from exc
    return path


def require_stage_root(data_root: Path, stage_root: Path) -> Path:
    root = Path(data_root).resolve(strict=True)
    expected = root / "staging" / STAGE_NAME
    requested = Path(stage_root)
    _require(requested.is_absolute(), "CHECKPOINT_STAGE_NOT_ABSOLUTE")
    _require(requested.resolve(strict=False) == expected, "CHECKPOINT_STAGE_ROOT_MISMATCH")
    _require((root / "staging").is_dir() and not (root / "staging").is_symlink(), "CHECKPOINT_STAGING_PARENT_INVALID")
    _require(not requested.is_symlink(), "CHECKPOINT_STAGE_SYMLINK")
    return expected


def _head(repo_root: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True).stdout.strip()


def run_checkpoint(*, repo_root: Path = REPO_ROOT, data_root: Path = DATA_ROOT_DEFAULT, adapter_authority_sha: str) -> dict[str, Any]:
    repo = Path(repo_root).resolve(strict=True)
    root = Path(data_root).resolve(strict=True)
    stage = require_stage_root(root, root / "staging" / STAGE_NAME)
    _require(_head(repo) == adapter_authority_sha, "RESUME_ADAPTER_AUTHORITY_NOT_HEAD")
    _require(adapter_authority_sha != BASE_HEAD and len(adapter_authority_sha) == 40, "RESUME_ADAPTER_AUTHORITY_INVALID")
    _require(not stage.exists(), "CHECKPOINT_STAGE_ALREADY_EXISTS")
    manifest = lineage.build_input_file_manifest(root)
    _require(manifest["INPUT_FILE_N"] == 2580 and manifest["INPUT_MANIFEST_HASH"] == DAILY_INPUT_MANIFEST_HASH, "DAILY_INPUT_MANIFEST_DRIFT")
    overlay = load_proven_traded_status_conflicts(repo, root)
    _require(len(overlay) == OVERLAY_KEY_N, "OVERLAY_KEY_N_MISMATCH")
    upstream_path = root / "staging" / "r4a9-preclose-resume-post-r3-repair-v01" / "manifest.json"
    _require(_sha(upstream_path) == UPSTREAM_CHECKPOINT_SHA256, "UPSTREAM_CHECKPOINT_DRIFT")
    upstream = lineage.load_json(upstream_path)
    expected_counts = {"SAFE_COMPLETE": 2139, "RECOMPUTE_REQUIRED": 1, "RETRY_REQUIRED": 1, "UNVISITED": 3315}
    _require(upstream.get("state_counts") == expected_counts, "UPSTREAM_STATE_COUNTS_DRIFT")
    _require(upstream["units"]["002087.SZ"].get("STATE") == "RECOMPUTE_REQUIRED", "UPSTREAM_002087_STATE_DRIFT")
    _require(upstream["units"]["300546.SZ"].get("STATE") == "RETRY_REQUIRED", "UPSTREAM_300546_STATE_DRIFT")
    checkpoint = copy.deepcopy(upstream)
    checkpoint.update({
        "schema_version": SCHEMA,
        "task": TASK,
        "upstream_checkpoint_path": str(upstream_path),
        "upstream_checkpoint_sha256": UPSTREAM_CHECKPOINT_SHA256,
        "daily_input_manifest_hash": DAILY_INPUT_MANIFEST_HASH,
        "status_conflict_overlay_contract": OVERLAY_CONTRACT,
        "status_conflict_overlay_key_n": OVERLAY_KEY_N,
        "status_conflict_overlay_keyset_hash": OVERLAY_KEYSET_HASH,
        "upstream_safe_adapter_authority_sha": UPSTREAM_SAFE_ADAPTER_AUTHORITY_SHA,
        "resume_adapter_authority_sha": adapter_authority_sha,
        "adapter_authority_sha": adapter_authority_sha,
        "recovery_execution_authorized": False,
    })
    path = _write(stage, "manifest.json", checkpoint)
    checkpoint_sha = _sha(path)
    receipt = {
        "task": TASK, "base_head": BASE_HEAD, "checkpoint_path": str(path), "checkpoint_sha256": checkpoint_sha,
        "daily_input_manifest_hash": DAILY_INPUT_MANIFEST_HASH, "status_conflict_overlay_contract": OVERLAY_CONTRACT,
        "status_conflict_overlay_key_n": OVERLAY_KEY_N, "status_conflict_overlay_keyset_hash": OVERLAY_KEYSET_HASH,
        "upstream_checkpoint_sha256": UPSTREAM_CHECKPOINT_SHA256,
        "upstream_safe_adapter_authority_sha": UPSTREAM_SAFE_ADAPTER_AUTHORITY_SHA,
        "resume_adapter_authority_sha": adapter_authority_sha,
        "safe_complete_n": 2139, "recompute_required_n": 1, "retry_required_n": 1, "unvisited_n": 3315,
        "NETWORK_PROVIDER_DATA_FETCH": "NO", "CANONICAL_WRITE_EXECUTED": False, "R4_PRECLOSE_EXTRACTION_EXECUTED": False,
    }
    receipt_path = _write(stage, "resume_receipt.json", receipt)
    return {**receipt, "resume_receipt_path": str(receipt_path), "resume_receipt_sha256": _sha(receipt_path), "overlay": overlay}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--adapter-authority-sha", required=True)
    args = parser.parse_args()
    try:
        result = run_checkpoint(repo_root=args.repo_root, data_root=args.data_root, adapter_authority_sha=args.adapter_authority_sha)
    except (CheckpointError, lineage.RebaseError, RuntimeError) as exc:
        print(f"FAIL_CLOSED:{exc}")
        return 1
    print(json.dumps({key: value for key, value in result.items() if key != "overlay"}, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
