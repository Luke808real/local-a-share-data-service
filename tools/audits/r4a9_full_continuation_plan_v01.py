#!/usr/bin/env python3
"""Freeze and validate the offline R4A9 full-continuation execution plan.

This module creates an immutable membership plan for the exact UNVISITED set
of the post-canary incident checkpoint.  It has no provider imports and the
CLI never executes a provider request.  Progress is deliberately separate
from plan membership so a future restart cannot rebuild membership from a
mutated checkpoint.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import os
import subprocess
from typing import Any, Callable, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

TASK = "R4A9_FULL_CONTINUATION_EXECUTION_PLAN_V01"
PLAN_VERSION = "R4A9_FULL_CONTINUATION_PLAN_V01"
EXPECTED_BASE_HEAD = "2d7d7ccfadb3e8cffb724794659bfaff746e7276"
EXPECTED_BRANCH = "codex/r4a9-full-continuation-execution-plan-v01"

CURRENT_CHECKPOINT_REL = Path(
    "staging/r4a9-preclose-post-canary-incident-v01/manifest.json"
)
PLAN_STAGE_REL = Path("staging/r4a9-preclose-full-continuation-plan-v01")
PLAN_CHECKPOINT_NAME = "manifest.json"

EXPECTED_START_CHECKPOINT_HASH = (
    "d013e171734d9c688e8c370163a617f35ce45c55c134ca6cee655b2a2c1e8f7b"
)
EXPECTED_SAFE_COMPLETE_N = 2_139
EXPECTED_COMPLETE_N = 7
EXPECTED_UNVISITED_N = 3_310
EXPECTED_TOTAL_N = 5_456
EXPECTED_DAILY_INPUT_MANIFEST_HASH = (
    "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"
)
EXPECTED_ADAPTER_AUTHORITY_SHA = "5748318662c0433baf72dec7c368754baf4b27f0"
EXPECTED_OVERLAY_KEYSET_HASH = (
    "49fd7d316e2a09bbb18f0b840d4a5034f3efb2dbba57e9f60255c7a8910b2663"
)


class FullContinuationPlanError(RuntimeError):
    """Raised when an authority, scope, or write-safety gate fails."""


def require(condition: bool, code: str, detail: Any | None = None) -> None:
    if not condition:
        suffix = "" if detail is None else f":{detail}"
        raise FullContinuationPlanError(f"{code}{suffix}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the repository-wide deterministic JSON byte representation."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    require(path.is_file() and not path.is_symlink(), "FILE_EVIDENCE_INVALID", str(path))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_symbol_bytes(symbols: Sequence[str]) -> bytes:
    """Serialize symbols as sorted/newline-delimited UTF-8 with a final LF.

    The final LF is part of the contract.  Empty sequences serialize to empty
    bytes.  This same primitive is used for the set and ordered execution
    hashes; because V01 ordering is canonical sorted order, those hashes are
    intentionally equal for the same membership.
    """

    if not symbols:
        return b""
    return ("\n".join(symbols) + "\n").encode("utf-8")


def symbol_sequence_hash(symbols: Sequence[str]) -> str:
    return sha256_bytes(canonical_symbol_bytes(symbols))


def load_json(path: Path) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), "JSON_EVIDENCE_MISSING", str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FullContinuationPlanError(f"JSON_EVIDENCE_INVALID:{path}") from exc
    require(isinstance(value, dict), "JSON_EVIDENCE_NOT_OBJECT", str(path))
    return value


def state_counts(checkpoint: Mapping[str, Any]) -> dict[str, int]:
    units = checkpoint.get("units")
    require(isinstance(units, dict), "CHECKPOINT_UNITS_INVALID")
    counts: dict[str, int] = {}
    for symbol, entry in units.items():
        require(isinstance(symbol, str) and symbol, "CHECKPOINT_SYMBOL_INVALID", symbol)
        require(isinstance(entry, dict), "CHECKPOINT_UNIT_INVALID", symbol)
        state = entry.get("STATE")
        require(
            state in {"SAFE_COMPLETE", "COMPLETE", "UNVISITED"},
            "CHECKPOINT_STATE_INVALID",
            f"{symbol}:{state}",
        )
        counts[state] = counts.get(state, 0) + 1
    return counts


def _canonical_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    values = tuple(symbols)
    require(all(isinstance(symbol, str) and symbol for symbol in values), "SYMBOL_VALUE_INVALID")
    require(len(values) == len(set(values)), "DUPLICATE_EXECUTION_SYMBOL")
    require(all(symbol.endswith((".SH", ".SZ")) for symbol in values), "SYMBOL_SCOPE_INVALID")
    return tuple(sorted(values))


def start_unvisited_symbols(checkpoint: Mapping[str, Any]) -> tuple[str, ...]:
    """Read the start UNVISITED set exactly once for a new plan."""

    units = checkpoint.get("units")
    require(isinstance(units, dict), "CHECKPOINT_UNITS_INVALID")
    return _canonical_symbols(
        symbol
        for symbol, entry in units.items()
        if isinstance(entry, dict) and entry.get("STATE") == "UNVISITED"
    )


def require_repo_identity(
    repo_root: Path,
    *,
    expected_head: str = EXPECTED_BASE_HEAD,
    expected_branch: str = EXPECTED_BRANCH,
) -> None:
    repo = Path(repo_root).resolve(strict=True)
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FullContinuationPlanError("REPO_IDENTITY_UNAVAILABLE") from exc
    require(head == expected_head, "BASE_HEAD_MISMATCH", head)
    require(branch == expected_branch, "BRANCH_MISMATCH", branch)


def require_isolated_plan_stage_root(data_root: Path, stage_root: Path) -> Path:
    """Allow only ``<data-root>/staging/r4a9-preclose-full-continuation-plan-v01``.

    The check resolves both sides and rejects symlinked existing stage roots.
    It must run before ``mkdir`` or any artifact write.
    """

    root = Path(data_root).expanduser().resolve(strict=True)
    staging_parent = root / "staging"
    require(
        staging_parent.is_dir() and not staging_parent.is_symlink(),
        "STAGING_PARENT_INVALID",
        str(staging_parent),
    )
    requested = Path(stage_root).expanduser()
    require(requested.is_absolute(), "PLAN_STAGE_ROOT_NOT_ABSOLUTE", str(requested))
    require(not requested.is_symlink(), "PLAN_STAGE_ROOT_SYMLINK", str(requested))
    expected = root / PLAN_STAGE_REL
    try:
        resolved_requested = requested.resolve(strict=False)
    except OSError as exc:
        raise FullContinuationPlanError(f"PLAN_STAGE_ROOT_RESOLVE_FAILED:{requested}") from exc
    require(resolved_requested == expected, "ISOLATED_PLAN_STAGE_ROOT_MISMATCH", str(requested))
    if requested.exists():
        require(not requested.is_symlink(), "PLAN_STAGE_ROOT_SYMLINK", str(requested))
        require(requested.is_dir(), "PLAN_STAGE_ROOT_NOT_DIRECTORY", str(requested))
    return expected


def validate_checkpoint_authority(
    checkpoint: Mapping[str, Any],
    *,
    start_checkpoint_hash: str,
    expected_start_checkpoint_hash: str = EXPECTED_START_CHECKPOINT_HASH,
    expected_daily_manifest_hash: str = EXPECTED_DAILY_INPUT_MANIFEST_HASH,
    expected_adapter_authority_sha: str = EXPECTED_ADAPTER_AUTHORITY_SHA,
    expected_overlay_keyset_hash: str = EXPECTED_OVERLAY_KEYSET_HASH,
) -> tuple[str, ...]:
    require(
        start_checkpoint_hash == expected_start_checkpoint_hash,
        "START_CHECKPOINT_HASH_MISMATCH",
        start_checkpoint_hash,
    )
    counts = state_counts(checkpoint)
    require(
        counts
        == {
            "SAFE_COMPLETE": EXPECTED_SAFE_COMPLETE_N,
            "COMPLETE": EXPECTED_COMPLETE_N,
            "UNVISITED": EXPECTED_UNVISITED_N,
        },
        "CHECKPOINT_STATE_COUNTS_MISMATCH",
        counts,
    )
    require(sum(counts.values()) == EXPECTED_TOTAL_N, "CHECKPOINT_TOTAL_MISMATCH")
    stored_counts = checkpoint.get("state_counts")
    if stored_counts is not None:
        require(stored_counts == counts, "CHECKPOINT_STORED_COUNTS_MISMATCH", stored_counts)
    require(checkpoint.get("unvisited_n") == EXPECTED_UNVISITED_N, "CHECKPOINT_UNVISITED_N_MISMATCH")
    require(
        checkpoint.get("daily_input_manifest_hash") == expected_daily_manifest_hash,
        "CHECKPOINT_DAILY_MANIFEST_MISMATCH",
    )
    require(
        checkpoint.get("adapter_authority_sha") == expected_adapter_authority_sha,
        "CHECKPOINT_ADAPTER_AUTHORITY_MISMATCH",
    )
    require(
        checkpoint.get("status_conflict_overlay_keyset_hash") == expected_overlay_keyset_hash,
        "CHECKPOINT_OVERLAY_AUTHORITY_MISMATCH",
    )
    require(
        checkpoint.get("full_r4a9_continuation_authorized") is False,
        "CONTINUATION_ALREADY_AUTHORIZED",
    )
    require(checkpoint.get("r4a9_resume_authorized") is False, "R4A9_RESUME_ALREADY_AUTHORIZED")
    symbols = start_unvisited_symbols(checkpoint)
    require(len(symbols) == EXPECTED_UNVISITED_N, "START_UNVISITED_N_MISMATCH", len(symbols))
    return symbols


def _plan_payload(
    *,
    start_checkpoint_hash: str,
    start_unvisited_set: Sequence[str],
    start_unvisited_set_hash: str,
    daily_input_manifest_hash: str,
    adapter_authority_sha: str,
    overlay_keyset_hash: str,
    ordered_execution_symbols: Sequence[str],
) -> dict[str, Any]:
    return {
        "FULL_CONTINUATION_PLAN_VERSION": PLAN_VERSION,
        "START_CHECKPOINT_HASH": start_checkpoint_hash,
        "START_UNVISITED_SET": list(start_unvisited_set),
        "START_UNVISITED_N": len(start_unvisited_set),
        "START_UNVISITED_SET_HASH": start_unvisited_set_hash,
        "DAILY_INPUT_MANIFEST_HASH": daily_input_manifest_hash,
        "ADAPTER_AUTHORITY_SHA": adapter_authority_sha,
        "OVERLAY_KEYSET_HASH": overlay_keyset_hash,
        "ORDERED_EXECUTION_SYMBOLS": list(ordered_execution_symbols),
        "EXECUTION_SYMBOL_N": len(ordered_execution_symbols),
        "EXECUTION_SYMBOL_HASH": symbol_sequence_hash(ordered_execution_symbols),
    }


@dataclass(frozen=True)
class FullContinuationPlan:
    start_checkpoint_hash: str
    start_unvisited_set: tuple[str, ...]
    start_unvisited_set_hash: str
    daily_input_manifest_hash: str
    adapter_authority_sha: str
    overlay_keyset_hash: str
    ordered_execution_symbols: tuple[str, ...]
    execution_symbol_hash: str
    plan_hash: str

    @property
    def start_unvisited_n(self) -> int:
        return len(self.start_unvisited_set)

    @property
    def execution_symbol_n(self) -> int:
        return len(self.ordered_execution_symbols)

    def payload(self) -> dict[str, Any]:
        return _plan_payload(
            start_checkpoint_hash=self.start_checkpoint_hash,
            start_unvisited_set=self.start_unvisited_set,
            start_unvisited_set_hash=self.start_unvisited_set_hash,
            daily_input_manifest_hash=self.daily_input_manifest_hash,
            adapter_authority_sha=self.adapter_authority_sha,
            overlay_keyset_hash=self.overlay_keyset_hash,
            ordered_execution_symbols=self.ordered_execution_symbols,
        )

    def record(self) -> dict[str, Any]:
        return {**self.payload(), "FULL_CONTINUATION_PLAN_HASH": self.plan_hash}


def freeze_full_continuation_plan(
    checkpoint: Mapping[str, Any],
    *,
    start_checkpoint_hash: str,
    daily_input_manifest_hash: str,
    adapter_authority_sha: str,
    overlay_keyset_hash: str,
    expected_start_checkpoint_hash: str | None = None,
    require_exact_scope: bool = False,
) -> FullContinuationPlan:
    """Freeze membership once; later checkpoint mutations cannot affect it."""

    symbols = start_unvisited_symbols(checkpoint)
    if require_exact_scope:
        require(len(symbols) == EXPECTED_UNVISITED_N, "START_UNVISITED_N_MISMATCH", len(symbols))
    if expected_start_checkpoint_hash is not None:
        require(
            start_checkpoint_hash == expected_start_checkpoint_hash,
            "START_CHECKPOINT_HASH_MISMATCH",
            start_checkpoint_hash,
        )
    ordered = tuple(sorted(symbols))
    require(ordered == symbols, "START_SYMBOL_ORDER_NOT_CANONICAL")
    start_set_hash = symbol_sequence_hash(symbols)
    execution_hash = symbol_sequence_hash(ordered)
    payload = _plan_payload(
        start_checkpoint_hash=start_checkpoint_hash,
        start_unvisited_set=symbols,
        start_unvisited_set_hash=start_set_hash,
        daily_input_manifest_hash=daily_input_manifest_hash,
        adapter_authority_sha=adapter_authority_sha,
        overlay_keyset_hash=overlay_keyset_hash,
        ordered_execution_symbols=ordered,
    )
    require(payload["EXECUTION_SYMBOL_HASH"] == execution_hash, "EXECUTION_HASH_INTERNAL_MISMATCH")
    return FullContinuationPlan(
        start_checkpoint_hash=start_checkpoint_hash,
        start_unvisited_set=symbols,
        start_unvisited_set_hash=start_set_hash,
        daily_input_manifest_hash=daily_input_manifest_hash,
        adapter_authority_sha=adapter_authority_sha,
        overlay_keyset_hash=overlay_keyset_hash,
        ordered_execution_symbols=ordered,
        execution_symbol_hash=execution_hash,
        plan_hash=sha256_bytes(canonical_json_bytes(payload)),
    )


def plan_from_record(record: Mapping[str, Any]) -> FullContinuationPlan:
    required = (
        "START_CHECKPOINT_HASH",
        "START_UNVISITED_SET",
        "START_UNVISITED_SET_HASH",
        "DAILY_INPUT_MANIFEST_HASH",
        "ADAPTER_AUTHORITY_SHA",
        "OVERLAY_KEYSET_HASH",
        "ORDERED_EXECUTION_SYMBOLS",
        "EXECUTION_SYMBOL_HASH",
        "FULL_CONTINUATION_PLAN_HASH",
    )
    for field in required:
        require(field in record, "PLAN_FIELD_MISSING", field)
    start_set = _canonical_symbols(record["START_UNVISITED_SET"])
    ordered = _canonical_symbols(record["ORDERED_EXECUTION_SYMBOLS"])
    require(start_set == ordered, "PLAN_SET_ORDER_MISMATCH")
    require(record.get("START_UNVISITED_N") == len(start_set), "PLAN_START_COUNT_MISMATCH")
    require(record.get("EXECUTION_SYMBOL_N") == len(ordered), "PLAN_EXECUTION_COUNT_MISMATCH")
    require(record["START_UNVISITED_SET_HASH"] == symbol_sequence_hash(start_set), "PLAN_SET_HASH_MISMATCH")
    require(record["EXECUTION_SYMBOL_HASH"] == symbol_sequence_hash(ordered), "PLAN_EXECUTION_HASH_MISMATCH")
    payload = _plan_payload(
        start_checkpoint_hash=str(record["START_CHECKPOINT_HASH"]),
        start_unvisited_set=start_set,
        start_unvisited_set_hash=str(record["START_UNVISITED_SET_HASH"]),
        daily_input_manifest_hash=str(record["DAILY_INPUT_MANIFEST_HASH"]),
        adapter_authority_sha=str(record["ADAPTER_AUTHORITY_SHA"]),
        overlay_keyset_hash=str(record["OVERLAY_KEYSET_HASH"]),
        ordered_execution_symbols=ordered,
    )
    expected_plan_hash = sha256_bytes(canonical_json_bytes(payload))
    require(record["FULL_CONTINUATION_PLAN_HASH"] == expected_plan_hash, "PLAN_HASH_MISMATCH")
    require(record.get("FULL_CONTINUATION_PLAN_VERSION") == PLAN_VERSION, "PLAN_VERSION_MISMATCH")
    return FullContinuationPlan(
        start_checkpoint_hash=str(record["START_CHECKPOINT_HASH"]),
        start_unvisited_set=start_set,
        start_unvisited_set_hash=str(record["START_UNVISITED_SET_HASH"]),
        daily_input_manifest_hash=str(record["DAILY_INPUT_MANIFEST_HASH"]),
        adapter_authority_sha=str(record["ADAPTER_AUTHORITY_SHA"]),
        overlay_keyset_hash=str(record["OVERLAY_KEYSET_HASH"]),
        ordered_execution_symbols=ordered,
        execution_symbol_hash=str(record["EXECUTION_SYMBOL_HASH"]),
        plan_hash=expected_plan_hash,
    )


def validate_persisted_plan(
    record: Mapping[str, Any],
    *,
    expected_start_checkpoint_hash: str = EXPECTED_START_CHECKPOINT_HASH,
    expected_daily_manifest_hash: str = EXPECTED_DAILY_INPUT_MANIFEST_HASH,
    expected_adapter_authority_sha: str = EXPECTED_ADAPTER_AUTHORITY_SHA,
    expected_overlay_keyset_hash: str = EXPECTED_OVERLAY_KEYSET_HASH,
) -> FullContinuationPlan:
    plan = plan_from_record(record)
    require(plan.start_checkpoint_hash == expected_start_checkpoint_hash, "RESUME_START_CHECKPOINT_DRIFT")
    require(plan.start_unvisited_n == EXPECTED_UNVISITED_N, "RESUME_START_SCOPE_DRIFT")
    require(plan.execution_symbol_n == EXPECTED_UNVISITED_N, "RESUME_EXECUTION_SCOPE_DRIFT")
    require(plan.daily_input_manifest_hash == expected_daily_manifest_hash, "RESUME_DAILY_MANIFEST_DRIFT")
    require(plan.adapter_authority_sha == expected_adapter_authority_sha, "RESUME_ADAPTER_DRIFT")
    require(plan.overlay_keyset_hash == expected_overlay_keyset_hash, "RESUME_OVERLAY_DRIFT")
    return plan


def load_persisted_plan(
    path: Path,
    *,
    expected_start_checkpoint_hash: str = EXPECTED_START_CHECKPOINT_HASH,
    expected_daily_manifest_hash: str = EXPECTED_DAILY_INPUT_MANIFEST_HASH,
    expected_adapter_authority_sha: str = EXPECTED_ADAPTER_AUTHORITY_SHA,
    expected_overlay_keyset_hash: str = EXPECTED_OVERLAY_KEYSET_HASH,
) -> FullContinuationPlan:
    return validate_persisted_plan(
        load_json(path),
        expected_start_checkpoint_hash=expected_start_checkpoint_hash,
        expected_daily_manifest_hash=expected_daily_manifest_hash,
        expected_adapter_authority_sha=expected_adapter_authority_sha,
        expected_overlay_keyset_hash=expected_overlay_keyset_hash,
    )


def validate_progress(plan: FullContinuationPlan, progress: Mapping[str, Any]) -> dict[str, Any]:
    """Validate mutable progress without changing immutable plan identity."""

    require(progress.get("FULL_CONTINUATION_PLAN_HASH") == plan.plan_hash, "PROGRESS_PLAN_HASH_MISMATCH")
    completed_indexes = progress.get("COMPLETED_INDEXES", [])
    require(isinstance(completed_indexes, list), "PROGRESS_INDEXES_INVALID")
    require(
        all(isinstance(index, int) and 0 <= index < plan.execution_symbol_n for index in completed_indexes),
        "PROGRESS_INDEX_INVALID",
    )
    require(len(completed_indexes) == len(set(completed_indexes)), "PROGRESS_INDEX_DUPLICATE")
    executed_n = progress.get("EXECUTED_N")
    remaining_n = progress.get("REMAINING_N")
    require(executed_n == len(completed_indexes), "PROGRESS_EXECUTED_COUNT_MISMATCH")
    require(remaining_n == plan.execution_symbol_n - executed_n, "PROGRESS_REMAINING_COUNT_MISMATCH")
    return dict(progress)


def enforce_requested_symbol(plan: FullContinuationPlan, requested_symbol: str) -> None:
    """Must run before a future provider login/request or callback."""

    require(
        requested_symbol in plan.ordered_execution_symbols,
        "FROZEN_EXECUTION_SCOPE_VIOLATION",
        requested_symbol,
    )


def guarded_provider_request(
    plan: FullContinuationPlan,
    requested_symbol: str,
    provider_callback: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Future-runner hook: authorize membership before invoking the callback."""

    enforce_requested_symbol(plan, requested_symbol)
    return provider_callback(*args, **kwargs)


def build_daily_input_manifest(data_root: Path) -> dict[str, Any]:
    """Recompute the local canonical daily manifest without provider access."""

    import sys

    tools_dir = Path(__file__).resolve().parents[1]
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    from rebase_r4a9_checkpoint_lineage_v01 import build_input_file_manifest

    return build_input_file_manifest(Path(data_root))


def build_plan_bound_checkpoint(
    plan: FullContinuationPlan,
    *,
    current_checkpoint_path: Path,
    current_checkpoint_sha256: str,
) -> dict[str, Any]:
    return {
        "ARTIFACT_TYPE": "PLAN_BOUND_PRE_EXECUTION_CHECKPOINT",
        "SCHEMA_VERSION": PLAN_VERSION,
        "PLAN_STATUS": "PENDING",
        "START_CHECKPOINT_PATH": str(CURRENT_CHECKPOINT_REL),
        "START_CHECKPOINT_HASH": current_checkpoint_sha256,
        "UPSTREAM_CHECKPOINT_PATH": str(CURRENT_CHECKPOINT_REL),
        "UPSTREAM_CHECKPOINT_SHA256": current_checkpoint_sha256,
        **plan.record(),
        "EXECUTION_PROGRESS": {
            "STATE": "PENDING",
            "FULL_CONTINUATION_PLAN_HASH": plan.plan_hash,
            "EXECUTED_N": 0,
            "REMAINING_N": plan.execution_symbol_n,
            "COMPLETED_INDEXES": [],
            "FAILED_INDEX": None,
        },
        "FULL_R4A9_CONTINUATION_CANDIDATE": True,
        "FULL_R4A9_CONTINUATION_AUTHORIZED": False,
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "NETWORK_REQUEST_N": 0,
        "CANONICAL_WRITE_EXECUTED": False,
        "R4_NEW_FORMAL_EXTRACTION_EXECUTED": False,
        "CURRENT_CHECKPOINT_MUTATED": False,
        "PRECLOSE_COMPLETE": False,
        "FACTS_READY": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
    }


def _write_atomic(path: Path, data: bytes) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    require(not temp.exists() and not temp.is_symlink(), "TEMP_OUTPUT_ALREADY_EXISTS", str(temp))
    temp.write_bytes(data)
    os.replace(temp, path)


def report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# {report['TASK']}",
        "",
        f"- AUTHOR_STATUS: `{report['AUTHOR_STATUS']}`",
        f"- EXECUTION_BASE_HEAD: `{report['EXECUTION_BASE_HEAD']}`",
        f"- BRANCH: `{report['BRANCH']}`",
        f"- CURRENT_CHECKPOINT_HASH: `{report['CURRENT_CHECKPOINT_HASH']}`",
        f"- FULL_CONTINUATION_PLAN_VERSION: `{report['FULL_CONTINUATION_PLAN_VERSION']}`",
        f"- FULL_CONTINUATION_PLAN_HASH: `{report['FULL_CONTINUATION_PLAN_HASH']}`",
        f"- START_UNVISITED_N: `{report['START_UNVISITED_N']}`",
        f"- START_UNVISITED_SET_HASH: `{report['START_UNVISITED_SET_HASH']}`",
        f"- EXECUTION_SYMBOL_N: `{report['EXECUTION_SYMBOL_N']}`",
        f"- EXECUTION_SYMBOL_HASH: `{report['EXECUTION_SYMBOL_HASH']}`",
        f"- PLAN_BOUND_CHECKPOINT_SHA256: `{report['PLAN_BOUND_CHECKPOINT_SHA256']}`",
        "",
        "## Safety",
        "",
        f"- CURRENT_CHECKPOINT_MUTATED: `{report['CURRENT_CHECKPOINT_MUTATED']}`",
        f"- NETWORK_PROVIDER_DATA_FETCH: `{report['NETWORK_PROVIDER_DATA_FETCH']}`",
        f"- NETWORK_REQUEST_N: `{report['NETWORK_REQUEST_N']}`",
        f"- CANONICAL_WRITE_EXECUTED: `{report['CANONICAL_WRITE_EXECUTED']}`",
        f"- FULL_R4A9_CONTINUATION_CANDIDATE: `{report['FULL_R4A9_CONTINUATION_CANDIDATE']}`",
        f"- FULL_R4A9_CONTINUATION_AUTHORIZED: `{report['FULL_R4A9_CONTINUATION_AUTHORIZED']}`",
        "",
        "The plan artifact is a pre-execution scope lineage checkpoint. It does not authorize provider execution or imply 5456 COMPLETE.",
        "Independent Sol audit is required before any provider execution.",
        "",
    ]
    return "\n".join(lines)


def run_plan(
    *,
    repo_root: Path = REPO_ROOT,
    data_root: Path = DATA_ROOT_DEFAULT,
    stage_root: Path | None = None,
    daily_manifest_builder: Callable[[Path], Mapping[str, Any]] = build_daily_input_manifest,
) -> dict[str, Any]:
    """Run all offline gates and write one new plan-bound checkpoint/report."""

    repo = Path(repo_root).resolve(strict=True)
    root = Path(data_root).expanduser().resolve(strict=True)
    requested_stage = stage_root if stage_root is not None else root / PLAN_STAGE_REL

    # These are intentionally the first writes-related gates.  In particular,
    # the stage guard runs before stage_root.mkdir, report mkdir, or any file
    # replacement.
    require_repo_identity(repo)
    stage = require_isolated_plan_stage_root(root, requested_stage)
    require(not stage.exists(), "PLAN_STAGE_ALREADY_EXISTS", str(stage))

    current_path = root / CURRENT_CHECKPOINT_REL
    current_sha = sha256_file(current_path)
    require(current_sha == EXPECTED_START_CHECKPOINT_HASH, "START_CHECKPOINT_DRIFT", current_sha)
    checkpoint = load_json(current_path)
    symbols = validate_checkpoint_authority(
        checkpoint,
        start_checkpoint_hash=current_sha,
    )
    daily_manifest = dict(daily_manifest_builder(root))
    actual_daily_hash = daily_manifest.get("INPUT_MANIFEST_HASH")
    require(
        actual_daily_hash == EXPECTED_DAILY_INPUT_MANIFEST_HASH,
        "DAILY_INPUT_MANIFEST_DRIFT",
        actual_daily_hash,
    )
    require(len(symbols) == EXPECTED_UNVISITED_N, "START_UNVISITED_N_MISMATCH", len(symbols))

    plan = freeze_full_continuation_plan(
        checkpoint,
        start_checkpoint_hash=current_sha,
        daily_input_manifest_hash=EXPECTED_DAILY_INPUT_MANIFEST_HASH,
        adapter_authority_sha=EXPECTED_ADAPTER_AUTHORITY_SHA,
        overlay_keyset_hash=EXPECTED_OVERLAY_KEYSET_HASH,
        require_exact_scope=True,
    )
    require(plan.execution_symbol_n == EXPECTED_UNVISITED_N, "EXECUTION_SYMBOL_N_MISMATCH")

    # No operation above mutates the start checkpoint; prove that before the
    # first artifact is persisted and once again after all writes.
    require(sha256_file(current_path) == current_sha, "CURRENT_CHECKPOINT_MUTATED_BEFORE_WRITE")
    stage.mkdir(parents=False, exist_ok=False)
    plan_checkpoint = build_plan_bound_checkpoint(
        plan,
        current_checkpoint_path=current_path,
        current_checkpoint_sha256=current_sha,
    )
    plan_path = stage / PLAN_CHECKPOINT_NAME
    _write_atomic(plan_path, canonical_json_bytes(plan_checkpoint) + b"\n")
    plan_checkpoint_sha = sha256_file(plan_path)
    require(sha256_file(current_path) == current_sha, "CURRENT_CHECKPOINT_MUTATED_AFTER_WRITE")

    report: dict[str, Any] = {
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "EXECUTION_BASE_HEAD": EXPECTED_BASE_HEAD,
        "BRANCH": EXPECTED_BRANCH,
        "CURRENT_CHECKPOINT_PATH": str(current_path),
        "CURRENT_CHECKPOINT_HASH": current_sha,
        "FULL_CONTINUATION_PLAN_VERSION": PLAN_VERSION,
        "FULL_CONTINUATION_PLAN_HASH": plan.plan_hash,
        "START_UNVISITED_N": plan.start_unvisited_n,
        "START_UNVISITED_SET_HASH": plan.start_unvisited_set_hash,
        "EXECUTION_SYMBOL_N": plan.execution_symbol_n,
        "EXECUTION_SYMBOL_HASH": plan.execution_symbol_hash,
        "DAILY_INPUT_MANIFEST_HASH": EXPECTED_DAILY_INPUT_MANIFEST_HASH,
        "ADAPTER_AUTHORITY_SHA": EXPECTED_ADAPTER_AUTHORITY_SHA,
        "OVERLAY_KEYSET_HASH": EXPECTED_OVERLAY_KEYSET_HASH,
        "PLAN_BOUND_CHECKPOINT_PATH": str(plan_path),
        "PLAN_BOUND_CHECKPOINT_SHA256": plan_checkpoint_sha,
        "PLAN_BOUND_STATUS": "PENDING_PRE_EXECUTION",
        "PLAN_BOUND_EXECUTED_N": 0,
        "PLAN_BOUND_REMAINING_N": plan.execution_symbol_n,
        "CURRENT_CHECKPOINT_MUTATED": False,
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "NETWORK_REQUEST_N": 0,
        "CANONICAL_WRITE_EXECUTED": False,
        "CANONICAL_BYTES_MUTATED": False,
        "R4_NEW_FORMAL_EXTRACTION_EXECUTED": False,
        "FULL_R4A9_CONTINUATION_CANDIDATE": True,
        "FULL_R4A9_CONTINUATION_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "FACTS_READY": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
    }
    report_json = repo / "reports" / "implementation" / f"{TASK}.json"
    report_md = repo / "reports" / "implementation" / f"{TASK}.md"
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_bytes(canonical_json_bytes(report) + b"\n")
    report_md.write_text(report_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_plan(**vars(args)), ensure_ascii=False, indent=2, sort_keys=True))
    except FullContinuationPlanError as exc:
        print(f"FAIL_CLOSED:{exc}", file=__import__("sys").stderr)
        return 1
    return 0


__all__ = [
    "CURRENT_CHECKPOINT_REL",
    "EXPECTED_ADAPTER_AUTHORITY_SHA",
    "EXPECTED_BASE_HEAD",
    "EXPECTED_BRANCH",
    "EXPECTED_DAILY_INPUT_MANIFEST_HASH",
    "EXPECTED_OVERLAY_KEYSET_HASH",
    "EXPECTED_START_CHECKPOINT_HASH",
    "EXPECTED_TOTAL_N",
    "EXPECTED_UNVISITED_N",
    "FullContinuationPlan",
    "FullContinuationPlanError",
    "PLAN_STAGE_REL",
    "PLAN_VERSION",
    "build_plan_bound_checkpoint",
    "canonical_json_bytes",
    "canonical_symbol_bytes",
    "enforce_requested_symbol",
    "freeze_full_continuation_plan",
    "guarded_provider_request",
    "load_persisted_plan",
    "plan_from_record",
    "require_isolated_plan_stage_root",
    "require_repo_identity",
    "run_plan",
    "sha256_file",
    "start_unvisited_symbols",
    "state_counts",
    "symbol_sequence_hash",
    "validate_checkpoint_authority",
    "validate_persisted_plan",
    "validate_progress",
]


if __name__ == "__main__":
    raise SystemExit(main())
