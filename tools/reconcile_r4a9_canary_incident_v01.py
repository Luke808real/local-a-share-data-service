#!/usr/bin/env python3
"""Reconcile the R4A9 continuation-canary scope incident offline.

This validator reads the already-created staging evidence, verifies the five
actual COMPLETE units, records the unauthorized 300548.SZ execution, and
creates a new lineage checkpoint that references (but never overwrites) the
current checkpoint.  It never imports a provider or performs network work.
"""

from __future__ import annotations

import argparse
import copy
from datetime import date
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
CURRENT_STAGE_NAME = "r4a9-preclose-resume-status-conflict-v01"
NEW_STAGE_NAME = "r4a9-preclose-post-canary-incident-v01"
CURRENT_CHECKPOINT_REL = Path("staging") / CURRENT_STAGE_NAME / "manifest.json"
NEW_CHECKPOINT_REL = Path("staging") / NEW_STAGE_NAME / "manifest.json"
TASK = "R4A9_CANARY_INCIDENT_RECONCILIATION_SCOPE_FIX_V01"
BASE_HEAD = "67b50b1f9da457ffc54501faee8b3f1f2069f564"
BRANCH = "codex/r4a9-canary-incident-reconciliation-v01"
EXPECTED_DAILY_MANIFEST_HASH = "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"
ADAPTER_AUTHORITY_SHA = "5748318662c0433baf72dec7c368754baf4b27f0"
OVERLAY_KEYSET_HASH = "49fd7d316e2a09bbb18f0b840d4a5034f3efb2dbba57e9f60255c7a8910b2663"
OVERLAY_SYMBOLS = ("600647.SH", "600766.SH", "603133.SH")
INCIDENT_SYMBOLS = ("300547.SZ", "300548.SZ", *OVERLAY_SYMBOLS)
AUTHORIZED_CANARY_SYMBOLS = ("300547.SZ", *OVERLAY_SYMBOLS)
EXPECTED_STATE_COUNTS = {"SAFE_COMPLETE": 2_139, "COMPLETE": 7, "UNVISITED": 3_310}
EXPECTED_FORMAL_COLUMNS = [
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
FORMAL_COUNTERS = (
    "DUPLICATE_N",
    "IDENTITY_FAILURE_N",
    "INVALID_PRECLOSE_N",
    "MISSING_REQUIRED_N",
    "POST_ASOF_N",
    "TRADESTATUS_UNKNOWN_N",
    "UNEXPECTED_TRADED_N",
    "WINDOW_SCOPE_FAILURE_N",
)
INCIDENT_ID = "R4A9_CANARY_SCOPE_CONTROL_DEFECT_V01"
ROOT_CAUSE = "CONTROL_SYMBOL_RESELECTED_FROM_MUTATING_UNVISITED_SET"
LEGACY_SCOPE_SOURCE = "tools/run_r4a9_status_conflict_continuation_canary_v01.py"
LEGACY_SCOPE_LINES = "40-47, 101"
RUNTIME_REPO = Path("/Users/luke808/ASL-r4a-preclose-status-conflict-runtime-5748318")
RUNTIME_SRC = RUNTIME_REPO / "src"
RUNTIME_ADAPTER = RUNTIME_SRC / "ashare_data" / "r4a_preclose_bounded_adapter.py"
RUNTIME_OVERLAY = RUNTIME_SRC / "ashare_data" / "r4a_preclose_status_conflict_overlay.py"
RUNTIME_ORCHESTRATOR = RUNTIME_SRC / "ashare_data" / "r4a7_preclose_full_extraction.py"
RUNTIME_ADAPTER_SOURCE_SHA = "52322e254329c2b5fef8fdaf02c16490d9b77498b2dda6e9af98ce8ec128e0fe"
RUNTIME_OVERLAY_SOURCE_SHA = "35546e4e353850a71390213423cbd3301b9093261480be0773e2b940316aed79"
RUNTIME_ORCHESTRATOR_SOURCE_SHA = "8973dfda278a41f820f87b958b1849a501676c1a961190975a6aa46e8ad75fc5"
TUSHARE_EVIDENCE_FILE_SHA = "1413ca2a9f14e5fef2f548643eb7e9353d185ffaaba93ee0a2ba40d9a88f87d1"
TUSHARE_SOURCE = "TUSHARE_LOCAL_RAW_PRECLOSE_FALLBACK"
TUSHARE_SOURCE_VERSION = "tushare-1.4.29"
BAOSTOCK_SOURCE = "BAOSTOCK_HISTORY_K_PRECLOSE"
BAOSTOCK_SOURCE_VERSION = "baostock-0.9.3"
OVERLAY_PREVIOUS_CLOSE = {
    "600647.SH": 1.64,
    "600766.SH": 0.40,
    "603133.SH": 0.30,
}
REPORT_JSON_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_r4a9_status_conflict_continuation_canary_v01 as scope_plan  # noqa: E402


class IncidentError(RuntimeError):
    """Raised when a frozen incident gate does not hold."""


def require(condition: bool, code: str, detail: Any | None = None) -> None:
    if not condition:
        suffix = "" if detail is None else f":{detail}"
        raise IncidentError(f"{code}{suffix}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), "JSON_EVIDENCE_MISSING", str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IncidentError(f"JSON_EVIDENCE_INVALID:{path}") from exc
    require(isinstance(value, dict), "JSON_EVIDENCE_NOT_OBJECT", str(path))
    return value


def require_repo_head(repo_root: Path) -> None:
    import subprocess

    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    require(actual == BASE_HEAD, "BASE_HEAD_MISMATCH", actual)


def require_current_stage_root(data_root: Path, stage_root: Path) -> Path:
    root = Path(data_root).expanduser().resolve(strict=True)
    expected = root / "staging" / CURRENT_STAGE_NAME
    requested = Path(stage_root).expanduser()
    require(requested.is_absolute(), "CURRENT_STAGE_ROOT_NOT_ABSOLUTE", str(requested))
    require(requested.resolve(strict=False) == expected, "CURRENT_STAGE_ROOT_MISMATCH", str(requested))
    require(expected.is_dir() and not expected.is_symlink(), "CURRENT_STAGE_ROOT_INVALID", str(expected))
    return expected


def require_new_stage_root(data_root: Path, stage_root: Path) -> Path:
    """Resolve and validate the only directory this task may create/write."""

    root = Path(data_root).expanduser().resolve(strict=True)
    staging_parent = root / "staging"
    require(staging_parent.is_dir() and not staging_parent.is_symlink(), "STAGING_PARENT_INVALID", str(staging_parent))
    expected = staging_parent / NEW_STAGE_NAME
    requested = Path(stage_root).expanduser()
    require(requested.is_absolute(), "NEW_STAGE_ROOT_NOT_ABSOLUTE", str(requested))
    require(not requested.is_symlink(), "NEW_STAGE_ROOT_SYMLINK", str(requested))
    require(requested.resolve(strict=False) == expected, "NEW_STAGE_ROOT_MISMATCH", str(requested))
    if requested.exists():
        require(not requested.is_symlink(), "NEW_STAGE_ROOT_SYMLINK", str(requested))
        require(requested.is_dir(), "NEW_STAGE_ROOT_NOT_DIRECTORY", str(requested))
    return expected


def state_counts(checkpoint: dict[str, Any]) -> dict[str, int]:
    units = checkpoint.get("units")
    require(isinstance(units, dict), "CHECKPOINT_UNITS_INVALID")
    counts: dict[str, int] = {}
    for entry in units.values():
        require(isinstance(entry, dict), "CHECKPOINT_UNIT_INVALID")
        state = str(entry.get("STATE"))
        counts[state] = counts.get(state, 0) + 1
    return counts


def current_checkpoint_snapshot(data_root: Path) -> tuple[Path, dict[str, Any], str]:
    root = Path(data_root).expanduser().resolve(strict=True)
    stage = require_current_stage_root(root, root / "staging" / CURRENT_STAGE_NAME)
    path = stage / "manifest.json"
    require(path.is_file() and not path.is_symlink(), "CURRENT_CHECKPOINT_MISSING", str(path))
    checkpoint_bytes = path.read_bytes()
    checkpoint = load_json(path)
    require(state_counts(checkpoint) == EXPECTED_STATE_COUNTS, "CURRENT_STATE_COUNTS_MISMATCH", state_counts(checkpoint))
    require(checkpoint.get("daily_input_manifest_hash") == EXPECTED_DAILY_MANIFEST_HASH, "CURRENT_DAILY_MANIFEST_FIELD_MISMATCH")
    require(checkpoint.get("adapter_authority_sha") == ADAPTER_AUTHORITY_SHA, "CURRENT_ADAPTER_AUTHORITY_MISMATCH")
    require(checkpoint.get("resume_adapter_authority_sha") == ADAPTER_AUTHORITY_SHA, "CURRENT_ADAPTER_AUTHORITY_MISMATCH")
    require(checkpoint.get("status_conflict_overlay_key_n") == 4, "CURRENT_OVERLAY_COUNT_MISMATCH")
    require(checkpoint.get("status_conflict_overlay_keyset_hash") == OVERLAY_KEYSET_HASH, "CURRENT_OVERLAY_HASH_MISMATCH")
    return path, checkpoint, sha256_bytes(checkpoint_bytes)


def load_recovery_module() -> Any:
    """Load only the previously verified hash functions; never a provider."""

    require(RUNTIME_REPO.is_dir(), "RUNTIME_REPO_MISSING", str(RUNTIME_REPO))
    runtime_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=RUNTIME_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    require(runtime_head == ADAPTER_AUTHORITY_SHA, "RUNTIME_HEAD_MISMATCH", runtime_head)
    runtime_status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=RUNTIME_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    require(runtime_status == "", "RUNTIME_WORKTREE_DIRTY")
    for path, expected in (
        (RUNTIME_ADAPTER, RUNTIME_ADAPTER_SOURCE_SHA),
        (RUNTIME_OVERLAY, RUNTIME_OVERLAY_SOURCE_SHA),
        (RUNTIME_ORCHESTRATOR, RUNTIME_ORCHESTRATOR_SOURCE_SHA),
    ):
        require(sha256_file(path) == expected, "RUNTIME_SOURCE_HASH_MISMATCH", str(path))
    require(str(RUNTIME_SRC) not in sys.path, "RUNTIME_PATH_PRELOADED")
    sys.path.insert(0, str(RUNTIME_SRC))
    try:
        return importlib.import_module("ashare_data.r4a7_preclose_full_extraction")
    except Exception as exc:  # pragma: no cover - environment-specific
        raise IncidentError("RUNTIME_HASH_MODULE_IMPORT_FAILED") from exc


def load_overlay_evidence(data_root: Path) -> dict[tuple[str, date], dict[str, Any]]:
    overlay_module = importlib.import_module("ashare_data.r4a_preclose_status_conflict_overlay")
    evidence = overlay_module.load_proven_traded_status_conflicts(RUNTIME_REPO, data_root.resolve(strict=True))
    require(len(evidence) == 4, "OVERLAY_COUNT_MISMATCH", len(evidence))
    require(overlay_module.keyset_hash(set(evidence)) == OVERLAY_KEYSET_HASH, "OVERLAY_HASH_MISMATCH")
    return evidence


def formal_row_hashes(orchestrator: Any, rows: list[dict[str, Any]]) -> tuple[str, str]:
    try:
        return orchestrator.formal_fact_hash(rows), orchestrator.staged_formal_content_hash(rows)
    except Exception as exc:
        raise IncidentError("FORMAL_HASH_RECOMPUTE_FAILED") from exc


def validate_receipt_hash(path: Path, expected: str, code: str) -> dict[str, Any]:
    receipt = load_json(path)
    require(sha256_file(path) == expected, code, str(path))
    return receipt


def validate_unit(
    *,
    checkpoint: dict[str, Any],
    symbol: str,
    stage_root: Path,
    orchestrator: Any,
    overlay: dict[tuple[str, date], dict[str, Any]],
) -> dict[str, Any]:
    units = checkpoint["units"]
    entry = units.get(symbol)
    require(isinstance(entry, dict), "INCIDENT_UNIT_MISSING", symbol)
    require(entry.get("STATE") == "COMPLETE", "INCIDENT_UNIT_NOT_COMPLETE", symbol)
    formal_path = Path(str(entry.get("formal_path")))
    require(formal_path.is_absolute(), "FORMAL_PATH_NOT_ABSOLUTE", symbol)
    require(formal_path.resolve(strict=False).is_relative_to(stage_root.resolve(strict=True)), "FORMAL_PATH_OUTSIDE_STAGE", symbol)
    require(formal_path.is_file() and not formal_path.is_symlink(), "FORMAL_ARTIFACT_MISSING", symbol)
    actual_file_sha = sha256_file(formal_path)
    require(actual_file_sha == entry.get("FORMAL_FILE_SHA256"), "FORMAL_FILE_SHA_MISMATCH", symbol)

    phase_path = stage_root / "recovery_receipts" / f"{symbol}.phase.json"
    phase = load_json(phase_path)
    require(phase.get("status") == "COMPLETE", "PHASE_RECEIPT_NOT_COMPLETE", symbol)
    require(phase.get("symbol") == symbol, "PHASE_RECEIPT_SYMBOL_MISMATCH", symbol)
    require(phase.get("recovery_kind") == "STATUS_CONFLICT_CONTINUATION_CANARY", "PHASE_RECEIPT_KIND_MISMATCH", symbol)
    require(phase.get("formal_path") == str(formal_path), "PHASE_FORMAL_PATH_MISMATCH", symbol)
    require(phase.get("formal_file_sha256") == actual_file_sha, "PHASE_FORMAL_FILE_SHA_MISMATCH", symbol)
    raw_path = stage_root / "recovery_receipts" / f"{symbol}.raw.json"
    normalized_path = stage_root / "recovery_receipts" / f"{symbol}.normalized.json"
    raw_expected = entry.get("provider_raw_receipt_sha256")
    normalized_expected = entry.get("normalized_receipt_sha256")
    require(phase.get("raw_receipt_sha256") == raw_expected, "PHASE_RAW_RECEIPT_SHA_MISMATCH", symbol)
    require(phase.get("normalized_receipt_sha256") == normalized_expected, "PHASE_NORMALIZED_RECEIPT_SHA_MISMATCH", symbol)
    raw = validate_receipt_hash(raw_path, str(raw_expected), "RAW_RECEIPT_SHA_MISMATCH")
    normalized = validate_receipt_hash(normalized_path, str(normalized_expected), "NORMALIZED_RECEIPT_SHA_MISMATCH")
    require(raw.get("symbol") == symbol, "RAW_RECEIPT_SYMBOL_MISMATCH", symbol)
    require(normalized.get("symbol") == symbol, "NORMALIZED_RECEIPT_SYMBOL_MISMATCH", symbol)
    require(normalized.get("status") == "COMPLETE", "NORMALIZED_STATUS_MISMATCH", symbol)
    require(normalized.get("quality_counters") == {key: 0 for key in FORMAL_COUNTERS}, "NORMALIZED_COUNTERS_NONZERO", symbol)
    require(int(raw.get("provider_failure_n", -1)) == 0, "RAW_PROVIDER_FAILURE_NONZERO", symbol)
    require(int(raw.get("network_request_n", 0)) > 0, "RAW_PROVIDER_REQUEST_N_INVALID", symbol)

    frame = pl.read_parquet(formal_path)
    require(frame.columns == EXPECTED_FORMAL_COLUMNS, "FORMAL_SCHEMA_MISMATCH", symbol)
    rows = frame.to_dicts()
    formal_hash, staged_hash = formal_row_hashes(orchestrator, rows)
    require(len(rows) == int(entry.get("FORMAL_FACT_ROW_N")), "FORMAL_ROW_COUNT_MISMATCH", symbol)
    require(formal_hash == entry.get("FORMAL_FACT_HASH"), "FORMAL_FACT_HASH_MISMATCH", symbol)
    require(staged_hash == entry.get("STAGED_FORMAL_CONTENT_HASH"), "STAGED_FORMAL_HASH_MISMATCH", symbol)
    require(phase.get("formal_fact_hash") == formal_hash, "PHASE_FORMAL_FACT_HASH_MISMATCH", symbol)
    require(phase.get("staged_formal_content_hash") == staged_hash, "PHASE_STAGED_FORMAL_HASH_MISMATCH", symbol)
    require(int(phase.get("required_row_n", -1)) == int(entry.get("REQUIRED_ROW_N")), "PHASE_REQUIRED_ROW_COUNT_MISMATCH", symbol)
    require(int(phase.get("formal_fact_row_n", -1)) == len(rows), "PHASE_FORMAL_ROW_COUNT_MISMATCH", symbol)
    require(phase.get("quality_counters") == normalized.get("quality_counters"), "PHASE_COUNTERS_MISMATCH", symbol)
    require(len(rows) == int(entry.get("REQUIRED_ROW_N")), "REQUIRED_ROW_COUNT_MISMATCH", symbol)
    require(entry.get("adapter_version") == ADAPTER_AUTHORITY_SHA, "UNIT_ADAPTER_MISMATCH", symbol)
    contract = entry.get("contract")
    require(isinstance(contract, dict), "UNIT_CONTRACT_MISSING", symbol)
    require(contract.get("QUERY_CONTRACT_VERSION") == "R4A_PRECLOSE_V01", "UNIT_QUERY_CONTRACT_MISMATCH", symbol)
    require(contract.get("PRIMARY_SOURCE") == BAOSTOCK_SOURCE, "UNIT_PRIMARY_SOURCE_MISMATCH", symbol)
    require(contract.get("SOURCE_VERSION") == BAOSTOCK_SOURCE_VERSION, "UNIT_SOURCE_VERSION_MISMATCH", symbol)
    require(contract.get("ADAPTER_AUTHORITY_SHA") == ADAPTER_AUTHORITY_SHA, "UNIT_CONTRACT_ADAPTER_MISMATCH", symbol)
    require(contract.get("FORMAL_IDENTITY_HASH") == "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f", "UNIT_IDENTITY_MISMATCH", symbol)
    for counter in FORMAL_COUNTERS:
        require(int(entry.get(counter, -1)) == 0, "UNIT_COUNTER_NONZERO", f"{symbol}:{counter}")

    source_counts = {
        str(row["source"]): int(row["len"])
        for row in frame.group_by("source").len().to_dicts()
    }
    result: dict[str, Any] = {
        "symbol": symbol,
        "formal_path": str(formal_path),
        "formal_file_sha256": actual_file_sha,
        "formal_fact_row_n": len(rows),
        "formal_fact_hash": formal_hash,
        "staged_formal_content_hash": staged_hash,
        "provider_raw_receipt_sha256": str(raw_expected),
        "normalized_receipt_sha256": str(normalized_expected),
        "network_request_n": int(raw.get("network_request_n", -1)),
        "quality_counters": normalized.get("quality_counters"),
        "provider_suspended_superset_n": int(entry.get("PROVIDER_SUSPENDED_SUPERSET_N", -1)),
        "source_counts": source_counts,
        "primary_status_conflict_n": int(entry.get("PRIMARY_STATUS_CONFLICT_N", -1)),
    }
    if symbol in {"300547.SZ", "300548.SZ"}:
        require(result["primary_status_conflict_n"] == 0, "NORMAL_CONTROL_CONFLICT_NONZERO", symbol)
        require(source_counts == {BAOSTOCK_SOURCE: len(rows)}, "NORMAL_CONTROL_FALLBACK_PRESENT", symbol)
    else:
        require(result["primary_status_conflict_n"] == 1, "OVERLAY_CONFLICT_COUNT_MISMATCH", symbol)
        require(source_counts.get(TUSHARE_SOURCE, 0) == 1, "OVERLAY_FALLBACK_ROW_COUNT_MISMATCH", symbol)
        require(source_counts.get(BAOSTOCK_SOURCE, 0) == len(rows) - 1, "OVERLAY_PRIMARY_ROW_COUNT_MISMATCH", symbol)
        target = [row for row in rows if row.get("trade_date") == date(2024, 6, 13)]
        require(len(target) == 1, "OVERLAY_FORMAL_ROW_MISSING", symbol)
        target_row = target[0]
        require(target_row.get("source") == TUSHARE_SOURCE, "OVERLAY_FORMAL_SOURCE_MISMATCH", symbol)
        require(target_row.get("source_version") == TUSHARE_SOURCE_VERSION, "OVERLAY_FORMAL_SOURCE_VERSION_MISMATCH", symbol)
        require(float(target_row.get("preclose")) == OVERLAY_PREVIOUS_CLOSE[symbol], "OVERLAY_FALLBACK_PRECLOSE_MISMATCH", symbol)
        overlay_evidence = overlay[(symbol, date(2024, 6, 13))]
        require(overlay_evidence.get("primary_tradestatus") == 0, "OVERLAY_PRIMARY_STATUS_MISMATCH", symbol)
        require(overlay_evidence.get("source") == TUSHARE_SOURCE, "OVERLAY_EVIDENCE_SOURCE_MISMATCH", symbol)
        require(overlay_evidence.get("source_version") == TUSHARE_SOURCE_VERSION, "OVERLAY_EVIDENCE_SOURCE_VERSION_MISMATCH", symbol)
        require(overlay_evidence.get("evidence_file_sha256") == TUSHARE_EVIDENCE_FILE_SHA, "OVERLAY_EVIDENCE_FILE_HASH_MISMATCH", symbol)
        require(overlay_evidence.get("predecessor_parity") is True, "OVERLAY_PREDECESSOR_PARITY_MISMATCH", symbol)
        result["overlay"] = {
            "trade_date": "2024-06-13",
            "formal_preclose": float(target_row["preclose"]),
            "fallback_source": TUSHARE_SOURCE,
            "fallback_source_version": TUSHARE_SOURCE_VERSION,
            "primary_tradestatus": 0,
            "predecessor_parity": True,
            "evidence_file_sha256": TUSHARE_EVIDENCE_FILE_SHA,
        }
    return result


def recover_incident_symbols(checkpoint: dict[str, Any], stage_root: Path) -> tuple[str, ...]:
    """Derive the incident set from the current checkpoint/phase receipts."""

    units = checkpoint["units"]
    symbols: set[str] = set()
    for path in sorted((stage_root / "recovery_receipts").glob("*.phase.json")):
        receipt = load_json(path)
        if receipt.get("recovery_kind") == "STATUS_CONFLICT_CONTINUATION_CANARY":
            require(receipt.get("status") == "COMPLETE", "INCIDENT_PHASE_NOT_COMPLETE", path.name)
            symbols.add(str(receipt.get("symbol")))
    for symbol, entry in units.items():
        if isinstance(entry, dict) and entry.get("RECOVERY_KIND") == "STATUS_CONFLICT_CONTINUATION_CANARY":
            symbols.add(str(symbol))
    require(symbols == set(INCIDENT_SYMBOLS), "INCIDENT_SCOPE_MISMATCH", sorted(symbols))
    require(all(units[symbol].get("STATE") == "COMPLETE" for symbol in symbols), "INCIDENT_COMPLETE_SCOPE_MISMATCH")
    return tuple(sorted(symbols))


def snapshot_complete_artifacts(checkpoint: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Hash every pre-existing COMPLETE/SAFE_COMPLETE artifact for closure."""

    snapshot: dict[str, tuple[str, str]] = {}
    for symbol, entry in checkpoint["units"].items():
        if entry.get("STATE") not in {"SAFE_COMPLETE", "COMPLETE"}:
            continue
        path = Path(str(entry.get("formal_path")))
        require(path.is_file() and not path.is_symlink(), "EXISTING_COMPLETE_ARTIFACT_MISSING", symbol)
        snapshot[str(symbol)] = (str(path), sha256_file(path))
    return snapshot


def compare_complete_artifact_snapshots(
    before: dict[str, tuple[str, str]], checkpoint: dict[str, Any]
) -> tuple[int, int]:
    """Return (mutated, deleted) counts for the previously complete set."""

    mutated = 0
    deleted = 0
    for symbol, (path_string, expected_sha) in before.items():
        path = Path(path_string)
        if not path.is_file() or path.is_symlink():
            deleted += 1
            continue
        if sha256_file(path) != expected_sha:
            mutated += 1
    current_symbols = {
        str(symbol)
        for symbol, entry in checkpoint["units"].items()
        if entry.get("STATE") in {"SAFE_COMPLETE", "COMPLETE"}
    }
    deleted += len(set(before) - current_symbols)
    return mutated, deleted


def reconstruct_initial_canary_snapshot(
    checkpoint: dict[str, Any], stage_root: Path, incident_symbols: tuple[str, ...]
) -> tuple[dict[str, Any], str]:
    """Reconstruct the invocation-start membership without touching the live file.

    The current checkpoint is post-incident.  Each canary unit retains its
    ``UPSTREAM_STATE`` and phase receipt chain, so the original invocation
    snapshot can be rebuilt in memory: all five incident units were UNVISITED
    before their first execution, and the first unconsumed checkpoint hash is
    the chain root.
    """

    phase_receipts: list[dict[str, Any]] = []
    for path in sorted((stage_root / "recovery_receipts").glob("*.phase.json")):
        receipt = load_json(path)
        if receipt.get("recovery_kind") == "STATUS_CONFLICT_CONTINUATION_CANARY":
            require(receipt.get("status") == "COMPLETE", "INCIDENT_PHASE_NOT_COMPLETE", path.name)
            phase_receipts.append(receipt)
    require(len(phase_receipts) == len(incident_symbols), "INCIDENT_PHASE_RECEIPT_N_MISMATCH")
    before_hashes = {str(receipt.get("checkpoint_before_sha256")) for receipt in phase_receipts}
    after_hashes = {str(receipt.get("checkpoint_after_sha256")) for receipt in phase_receipts}
    roots = before_hashes - after_hashes
    require(len(roots) == 1 and "None" not in roots, "INCIDENT_CHECKPOINT_CHAIN_ROOT_AMBIGUOUS", sorted(roots))
    start_checkpoint_hash = next(iter(roots))

    snapshot = copy.deepcopy(checkpoint)
    for symbol in incident_symbols:
        entry = snapshot["units"].get(symbol)
        require(isinstance(entry, dict), "INCIDENT_SNAPSHOT_SYMBOL_MISSING", symbol)
        require(entry.get("UPSTREAM_STATE") == "UNVISITED", "INCIDENT_SNAPSHOT_UPSTREAM_STATE_MISMATCH", symbol)
        entry["STATE"] = "UNVISITED"
    reconstructed_counts = state_counts(snapshot)
    require(
        reconstructed_counts == {"SAFE_COMPLETE": 2_139, "COMPLETE": 2, "UNVISITED": 3_315},
        "INCIDENT_RECONSTRUCTED_STATE_COUNTS_MISMATCH",
        reconstructed_counts,
    )
    return snapshot, start_checkpoint_hash


def build_new_checkpoint(
    current: dict[str, Any],
    *,
    current_path: Path,
    current_sha: str,
    plan: scope_plan.CanaryExecutionPlan,
    unit_results: list[dict[str, Any]],
) -> dict[str, Any]:
    new_checkpoint = copy.deepcopy(current)
    new_checkpoint.update(
        {
            "schema_version": "R4A9_STATUS_CONFLICT_POST_CANARY_INCIDENT_V01",
            "checkpoint_generation": "POST_CANARY_INCIDENT_V01",
            "upstream_checkpoint_path": str(current_path),
            "upstream_checkpoint_sha256": current_sha,
            "incident_id": INCIDENT_ID,
            "incident_root_cause": ROOT_CAUSE,
            "unauthorized_execution_symbol": "300548.SZ",
            "unauthorized_execution_reason": "CONTROL_SYMBOL_RESELECTED_AFTER_CHECKPOINT_MUTATION",
            "unauthorized_execution_data_status": "VALID",
            "incident_disposition": "ADOPT_VALID_RESULT_PRESERVE_LINEAGE",
            "canary_plan": plan.binding(),
            "unit_validation_n": len(unit_results),
            "current_checkpoint_mutated": False,
            "existing_complete_artifact_mutated_n": 0,
            "existing_complete_artifact_deleted_n": 0,
            "full_r4a9_continuation_candidate": True,
            "full_r4a9_continuation_authorized": False,
            "r4a9_resume_authorized": False,
            "network_provider_data_fetch": "NO",
            "baostock_executed": False,
            "tushare_network_executed": False,
            "tdx_executed": False,
            "eastmoney_executed": False,
            "canonical_write_executed": False,
            "r3_data_mutated": False,
            "r4_new_formal_extraction_executed": False,
        }
    )
    return new_checkpoint


def write_new_checkpoint(path: Path, payload: dict[str, Any], data_root: Path) -> str:
    stage_root = require_new_stage_root(data_root, path.parent)
    stage_root.mkdir(parents=False, exist_ok=True)
    require(path.parent == stage_root, "NEW_CHECKPOINT_PATH_MISMATCH", str(path))
    encoded = canonical_json_bytes(payload)
    if path.exists():
        require(not path.is_symlink(), "NEW_CHECKPOINT_SYMLINK", str(path))
        require(path.read_bytes() == encoded, "NEW_CHECKPOINT_EXISTING_MISMATCH", str(path))
    else:
        temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
    digest = sha256_file(path)
    require(digest == sha256_bytes(encoded), "NEW_CHECKPOINT_HASH_RECHECK_FAILED")
    return digest


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {TASK}",
        "",
        "Offline reconciliation of the R4A9 continuation-canary scope-control incident.",
        "",
        f"- BASE_HEAD: `{report['BASE_HEAD']}`",
        f"- CURRENT_CHECKPOINT_HASH: `{report['CURRENT_CHECKPOINT_HASH']}`",
        f"- NEW_CHECKPOINT_HASH: `{report['NEW_CHECKPOINT_HASH']}`",
        f"- INCIDENT_EXECUTED_SYMBOLS: `{', '.join(report['INCIDENT_EXECUTED_SYMBOLS'])}`",
        f"- UNAUTHORIZED_EXECUTION_SYMBOLS: `{', '.join(report['UNAUTHORIZED_EXECUTION_SYMBOLS'])}`",
        f"- UNAUTHORIZED_EXECUTION_DATA_STATUS: `{report['UNAUTHORIZED_EXECUTION_DATA_STATUS']}`",
        f"- INCIDENT_DISPOSITION: `{report['INCIDENT_DISPOSITION']}`",
        f"- ROOT_CAUSE: `{report['ROOT_CAUSE']}`",
        f"- CANARY_PLAN_VERSION: `{report['CANARY_PLAN_VERSION']}`",
        f"- CANARY_PLAN_HASH: `{report['CANARY_PLAN_HASH']}`",
        "",
        "## Unit validation",
        "",
        f"- OVERLAY_UNIT_VALID_N: `{report['OVERLAY_UNIT_VALID_N']}`",
        f"- NORMAL_CONTROL_VALID_N: `{report['NORMAL_CONTROL_VALID_N']}`",
        f"- SAFE_COMPLETE_N / COMPLETE_N / UNVISITED_N: `{report['SAFE_COMPLETE_N']} / {report['COMPLETE_N']} / {report['UNVISITED_N']}`",
        "",
        "## Safety",
        "",
        f"- CURRENT_CHECKPOINT_MUTATED: `{report['CURRENT_CHECKPOINT_MUTATED']}`",
        f"- EXISTING_COMPLETE_ARTIFACT_MUTATED_N: `{report['EXISTING_COMPLETE_ARTIFACT_MUTATED_N']}`",
        f"- EXISTING_COMPLETE_ARTIFACT_DELETED_N: `{report['EXISTING_COMPLETE_ARTIFACT_DELETED_N']}`",
        f"- NETWORK_PROVIDER_DATA_FETCH: `{report['NETWORK_PROVIDER_DATA_FETCH']}`",
        f"- CANONICAL_WRITE_EXECUTED: `{report['CANONICAL_WRITE_EXECUTED']}`",
        f"- FULL_R4A9_CONTINUATION_CANDIDATE: `{report['FULL_R4A9_CONTINUATION_CANDIDATE']}`",
        f"- FULL_R4A9_CONTINUATION_AUTHORIZED: `{report['FULL_R4A9_CONTINUATION_AUTHORIZED']}`",
        "",
        "The current checkpoint and all five existing formal artifacts remain preserved. No remaining symbol was executed.",
    ]
    return "\n".join(lines) + "\n"


def run_incident(*, repo_root: Path = REPO_ROOT, data_root: Path = DATA_ROOT_DEFAULT) -> dict[str, Any]:
    repo = Path(repo_root).resolve(strict=True)
    root = Path(data_root).expanduser().resolve(strict=True)
    require_repo_head(repo)
    current_path, current, current_sha = current_checkpoint_snapshot(root)
    existing_artifacts_before = snapshot_complete_artifacts(current)
    daily_manifest = __import__("run_r4a9_bounded_recovery_v01").build_input_file_manifest(root)
    require(daily_manifest["INPUT_MANIFEST_HASH"] == EXPECTED_DAILY_MANIFEST_HASH, "DAILY_INPUT_MANIFEST_DRIFT", daily_manifest["INPUT_MANIFEST_HASH"])
    incident_symbols = recover_incident_symbols(current, current_path.parent)
    require(incident_symbols == tuple(sorted(INCIDENT_SYMBOLS)), "INCIDENT_SYMBOL_ORDER_MISMATCH", incident_symbols)
    require(set(AUTHORIZED_CANARY_SYMBOLS).issubset(set(incident_symbols)), "AUTHORIZED_SCOPE_MISSING")
    require(set(incident_symbols) - set(AUTHORIZED_CANARY_SYMBOLS) == {"300548.SZ"}, "UNAUTHORIZED_SCOPE_MISMATCH")

    # Importing these frozen local modules is only for hash recomputation and
    # overlay evidence loading.  No provider session/login/query is called.
    orchestrator = load_recovery_module()
    overlay = load_overlay_evidence(root)
    unit_results = [
        validate_unit(
            checkpoint=current,
            symbol=symbol,
            stage_root=current_path.parent,
            orchestrator=orchestrator,
            overlay=overlay,
        )
        for symbol in incident_symbols
    ]
    current_sha_after_validation = sha256_file(current_path)
    require(current_sha_after_validation == current_sha, "CURRENT_CHECKPOINT_MUTATED_DURING_VALIDATION")
    current_counts_after_validation = state_counts(load_json(current_path))
    require(current_counts_after_validation == EXPECTED_STATE_COUNTS, "CURRENT_STATE_COUNTS_CHANGED", current_counts_after_validation)

    initial_snapshot, start_checkpoint_hash = reconstruct_initial_canary_snapshot(
        current, current_path.parent, incident_symbols
    )
    plan = scope_plan.freeze_canary_plan(
        initial_snapshot,
        start_checkpoint_hash=start_checkpoint_hash,
        daily_input_manifest_hash=EXPECTED_DAILY_MANIFEST_HASH,
        adapter_authority_sha=ADAPTER_AUTHORITY_SHA,
        overlay_keyset_hash=OVERLAY_KEYSET_HASH,
    )
    require(plan.ordered_canary_symbols == AUTHORIZED_CANARY_SYMBOLS, "FROZEN_PLAN_SCOPE_MISMATCH", plan.ordered_canary_symbols)

    new_checkpoint_path = root / NEW_CHECKPOINT_REL
    new_stage_root = require_new_stage_root(root, new_checkpoint_path.parent)
    new_payload = build_new_checkpoint(
        current,
        current_path=current_path,
        current_sha=current_sha,
        plan=plan,
        unit_results=unit_results,
    )
    new_checkpoint_sha = write_new_checkpoint(new_checkpoint_path, new_payload, root)
    require(sha256_file(current_path) == current_sha, "CURRENT_CHECKPOINT_MUTATED_AFTER_NEW_LINEAGE")
    for result in unit_results:
        require(sha256_file(Path(result["formal_path"])) == result["formal_file_sha256"], "EXISTING_ARTIFACT_CHANGED", result["symbol"])
    existing_artifacts_mutated, existing_artifacts_deleted = compare_complete_artifact_snapshots(
        existing_artifacts_before, load_json(current_path)
    )
    require(existing_artifacts_mutated == 0, "EXISTING_COMPLETE_ARTIFACT_MUTATED", existing_artifacts_mutated)
    require(existing_artifacts_deleted == 0, "EXISTING_COMPLETE_ARTIFACT_DELETED", existing_artifacts_deleted)

    report: dict[str, Any] = {
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "CURRENT_CHECKPOINT_PATH": str(current_path),
        "CURRENT_CHECKPOINT_HASH": current_sha,
        "NEW_CHECKPOINT_PATH": str(new_checkpoint_path),
        "NEW_CHECKPOINT_HASH": new_checkpoint_sha,
        "DAILY_INPUT_MANIFEST_HASH": EXPECTED_DAILY_MANIFEST_HASH,
        "ADAPTER_AUTHORITY_SHA": ADAPTER_AUTHORITY_SHA,
        "OVERLAY_KEYSET_HASH": OVERLAY_KEYSET_HASH,
        "INCIDENT_ID": INCIDENT_ID,
        "ROOT_CAUSE": ROOT_CAUSE,
        "ROOT_CAUSE_EVIDENCE": {
            "source": LEGACY_SCOPE_SOURCE,
            "lines": LEGACY_SCOPE_LINES,
            "mechanism": "select_canary_symbols() sorted the CURRENT checkpoint UNVISITED set; a later invocation after 300547 became COMPLETE selected 300548 as the next control.",
        },
        "FIXED_SCOPE_EVIDENCE": {
            "source": "tools/run_r4a9_status_conflict_continuation_canary_v01.py",
            "mechanism": "freeze_canary_plan() captures the initial UNVISITED snapshot once; execute_frozen_plan() and guarded_provider_request() use only the frozen plan membership.",
        },
        "INCIDENT_EXECUTED_SYMBOL_N": len(incident_symbols),
        "INCIDENT_EXECUTED_SYMBOLS": list(incident_symbols),
        "INCIDENT_EXECUTED_SYMBOL_HASH": scope_plan.canary_symbol_hash(list(incident_symbols)),
        "AUTHORIZED_CANARY_SYMBOL_N": len(AUTHORIZED_CANARY_SYMBOLS),
        "AUTHORIZED_CANARY_SYMBOLS": list(AUTHORIZED_CANARY_SYMBOLS),
        "UNAUTHORIZED_EXECUTION_SYMBOL_N": 1,
        "UNAUTHORIZED_EXECUTION_SYMBOLS": ["300548.SZ"],
        "UNAUTHORIZED_EXECUTION_SYMBOL_HASH": scope_plan.canary_symbol_hash(["300548.SZ"]),
        "UNAUTHORIZED_EXECUTION_REASON": "CONTROL_SYMBOL_RESELECTED_AFTER_CHECKPOINT_MUTATION",
        "UNAUTHORIZED_EXECUTION_DATA_STATUS": "VALID",
        "INCIDENT_DISPOSITION": "ADOPT_VALID_RESULT_PRESERVE_LINEAGE",
        "OVERLAY_UNIT_VALID_N": sum(symbol in OVERLAY_SYMBOLS for symbol in incident_symbols),
        "NORMAL_CONTROL_VALID_N": sum(symbol in {"300547.SZ", "300548.SZ"} for symbol in incident_symbols),
        "UNIT_VALIDATION": unit_results,
        "PRIOR_RECEIPT_NETWORK_REQUEST_N": sum(int(result["network_request_n"]) for result in unit_results),
        "NETWORK_REQUEST_EXECUTED_N": 0,
        "CANARY_PLAN_VERSION": scope_plan.CANARY_PLAN_VERSION,
        "CANARY_PLAN_HASH": plan.plan_hash,
        "START_CHECKPOINT_HASH": plan.start_checkpoint_hash,
        "START_UNVISITED_SET_HASH": plan.start_unvisited_set_hash,
        "CANARY_PLAN": plan.binding(),
        "SAFE_COMPLETE_N": EXPECTED_STATE_COUNTS["SAFE_COMPLETE"],
        "COMPLETE_N": EXPECTED_STATE_COUNTS["COMPLETE"],
        "UNVISITED_N": EXPECTED_STATE_COUNTS["UNVISITED"],
        "STATE_TOTAL_N": sum(EXPECTED_STATE_COUNTS.values()),
        "CURRENT_CHECKPOINT_MUTATED": False,
        "EXISTING_COMPLETE_ARTIFACT_MUTATED_N": existing_artifacts_mutated,
        "EXISTING_COMPLETE_ARTIFACT_DELETED_N": existing_artifacts_deleted,
        "300548_ARTIFACT_PRESERVED": True,
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "BAOSTOCK_EXECUTED": False,
        "TUSHARE_NETWORK_EXECUTED": False,
        "TDX_EXECUTED": False,
        "EASTMONEY_EXECUTED": False,
        "CANONICAL_WRITE_EXECUTED": False,
        "CANONICAL_BYTES_MUTATED": False,
        "R3_DATA_MUTATED": False,
        "R4_NEW_FORMAL_EXTRACTION_EXECUTED": False,
        "FULL_R4A9_CONTINUATION_CANDIDATE": True,
        "FULL_R4A9_CONTINUATION_AUTHORIZED": False,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "FACTS_READY": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
        "NEW_STAGE_ROOT": str(new_stage_root),
    }
    report_json = repo / "reports" / "implementation" / REPORT_JSON_NAME
    report_md = repo / "reports" / "implementation" / REPORT_MD_NAME
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_bytes(canonical_json_bytes(report))
    report_md.write_text(report_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_incident(repo_root=args.repo_root, data_root=args.data_root), ensure_ascii=False, indent=2, sort_keys=True))
    except IncidentError as exc:
        print(f"FAIL_CLOSED:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
