from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))
import r4a9_full_continuation_plan_v01 as plan  # noqa: E402


def _symbols(n: int = plan.EXPECTED_UNVISITED_N) -> tuple[str, ...]:
    return tuple(f"{index:04d}.SZ" for index in range(n))


def _checkpoint(symbols: tuple[str, ...] | None = None) -> dict[str, object]:
    values = symbols or _symbols()
    units: dict[str, dict[str, str]] = {
        f"safe{index:04d}.SH": {"STATE": "SAFE_COMPLETE"} for index in range(plan.EXPECTED_SAFE_COMPLETE_N)
    }
    units.update(
        {f"complete{index:04d}.SH": {"STATE": "COMPLETE"} for index in range(plan.EXPECTED_COMPLETE_N)}
    )
    units.update({symbol: {"STATE": "UNVISITED"} for symbol in values})
    return {
        "units": units,
        "state_counts": {
            "SAFE_COMPLETE": plan.EXPECTED_SAFE_COMPLETE_N,
            "COMPLETE": plan.EXPECTED_COMPLETE_N,
            "UNVISITED": len(values),
        },
        "unvisited_n": len(values),
        "daily_input_manifest_hash": plan.EXPECTED_DAILY_INPUT_MANIFEST_HASH,
        "adapter_authority_sha": plan.EXPECTED_ADAPTER_AUTHORITY_SHA,
        "status_conflict_overlay_keyset_hash": plan.EXPECTED_OVERLAY_KEYSET_HASH,
        "full_r4a9_continuation_authorized": False,
        "r4a9_resume_authorized": False,
    }


def _plan() -> plan.FullContinuationPlan:
    return plan.freeze_full_continuation_plan(
        _checkpoint(),
        start_checkpoint_hash=plan.EXPECTED_START_CHECKPOINT_HASH,
        daily_input_manifest_hash=plan.EXPECTED_DAILY_INPUT_MANIFEST_HASH,
        adapter_authority_sha=plan.EXPECTED_ADAPTER_AUTHORITY_SHA,
        overlay_keyset_hash=plan.EXPECTED_OVERLAY_KEYSET_HASH,
        require_exact_scope=True,
    )


def test_exact_3310_membership_is_frozen_and_sorted() -> None:
    frozen = _plan()
    assert frozen.record()["FULL_CONTINUATION_PLAN_VERSION"] == "R4A9_FULL_CONTINUATION_PLAN_V01"
    assert frozen.start_unvisited_n == 3310
    assert frozen.execution_symbol_n == 3310
    assert frozen.start_unvisited_set == _symbols()
    assert frozen.ordered_execution_symbols == _symbols()
    assert frozen.start_unvisited_set_hash == frozen.execution_symbol_hash


def test_plan_is_deterministic_and_binds_all_authorities() -> None:
    first = _plan()
    second = _plan()
    assert first.plan_hash == second.plan_hash
    assert first.payload() == second.payload()
    for field in (
        "START_CHECKPOINT_HASH",
        "DAILY_INPUT_MANIFEST_HASH",
        "ADAPTER_AUTHORITY_SHA",
        "OVERLAY_KEYSET_HASH",
        "START_UNVISITED_SET",
        "ORDERED_EXECUTION_SYMBOLS",
    ):
        assert field in first.payload()


@pytest.mark.parametrize(
    "field, value",
    [
        ("start_checkpoint_hash", "e" * 64),
        ("daily_input_manifest_hash", "f" * 64),
        ("adapter_authority_sha", "1" * 40),
        ("overlay_keyset_hash", "2" * 64),
    ],
)
def test_authority_change_changes_plan_hash(field: str, value: str) -> None:
    kwargs = {
        "start_checkpoint_hash": plan.EXPECTED_START_CHECKPOINT_HASH,
        "daily_input_manifest_hash": plan.EXPECTED_DAILY_INPUT_MANIFEST_HASH,
        "adapter_authority_sha": plan.EXPECTED_ADAPTER_AUTHORITY_SHA,
        "overlay_keyset_hash": plan.EXPECTED_OVERLAY_KEYSET_HASH,
    }
    kwargs[field] = value
    changed = plan.freeze_full_continuation_plan(_checkpoint(), **kwargs)
    assert changed.plan_hash != _plan().plan_hash


def test_mutating_checkpoint_after_freeze_does_not_change_plan() -> None:
    checkpoint = _checkpoint()
    frozen = _plan()
    original_hash = frozen.plan_hash
    checkpoint["units"][_symbols()[0]]["STATE"] = "COMPLETE"  # type: ignore[index]
    checkpoint["units"]["9999.SZ"] = {"STATE": "UNVISITED"}  # type: ignore[index]
    assert frozen.plan_hash == original_hash
    assert "9999.SZ" not in frozen.ordered_execution_symbols
    assert frozen.ordered_execution_symbols == _symbols()


def test_plan_dataclass_and_membership_are_immutable() -> None:
    frozen = _plan()
    with pytest.raises(FrozenInstanceError):
        frozen.plan_hash = "0" * 64  # type: ignore[misc]
    with pytest.raises(AttributeError):
        frozen.ordered_execution_symbols.append("9999.SZ")  # type: ignore[attr-defined]


def test_outside_plan_is_blocked_before_provider_callback() -> None:
    calls: list[str] = []

    def provider() -> str:
        calls.append("called")
        return "bad"

    with pytest.raises(plan.FullContinuationPlanError, match="FROZEN_EXECUTION_SCOPE_VIOLATION"):
        plan.guarded_provider_request(_plan(), "9999.SZ", provider)
    assert calls == []


def test_completed_progress_does_not_change_plan_identity() -> None:
    frozen = _plan()
    progress = {
        "FULL_CONTINUATION_PLAN_HASH": frozen.plan_hash,
        "EXECUTED_N": 2,
        "REMAINING_N": 3308,
        "COMPLETED_INDEXES": [0, 1],
    }
    assert plan.validate_progress(frozen, progress) == progress
    assert frozen.plan_hash == _plan().plan_hash


def test_plan_from_record_round_trip_and_tamper_fail_closed(tmp_path: Path) -> None:
    frozen = _plan()
    record = {
        **frozen.record(),
        "EXECUTION_PROGRESS": {
            "STATE": "PENDING",
            "FULL_CONTINUATION_PLAN_HASH": frozen.plan_hash,
            "EXECUTED_N": 0,
            "REMAINING_N": 3310,
            "COMPLETED_INDEXES": [],
        },
    }
    path = tmp_path / "manifest.json"
    path.write_bytes(plan.canonical_json_bytes(record) + b"\n")
    loaded = plan.plan_from_record(record)
    assert loaded.plan_hash == frozen.plan_hash
    assert loaded.ordered_execution_symbols == frozen.ordered_execution_symbols
    tampered = json.loads(path.read_text())
    tampered["ORDERED_EXECUTION_SYMBOLS"][0] = "9999.SZ"
    with pytest.raises(
        plan.FullContinuationPlanError,
        match="PLAN_START_SET_ORDER_NOT_CANONICAL|PLAN_EXECUTION_ORDER_NOT_CANONICAL|PLAN_SET_ORDER_MISMATCH|PLAN_EXECUTION_HASH_MISMATCH|PLAN_HASH_MISMATCH",
    ):
        plan.plan_from_record(tampered)

    reordered = json.loads(path.read_text())
    reordered["START_UNVISITED_SET"] = list(reversed(reordered["START_UNVISITED_SET"]))
    with pytest.raises(plan.FullContinuationPlanError, match="PLAN_START_SET_ORDER_NOT_CANONICAL"):
        plan.plan_from_record(reordered)


def test_self_consistent_nonfrozen_plan_is_rejected() -> None:
    record = _plan().record()
    with pytest.raises(
        plan.FullContinuationPlanError,
        match="RESUME_START_UNVISITED_SET_IDENTITY_DRIFT",
    ):
        plan.validate_persisted_plan(record)


def test_real_frozen_plan_identity_is_pinned() -> None:
    path = (
        plan.DATA_ROOT_DEFAULT
        / plan.PLAN_STAGE_REL
        / plan.PLAN_CHECKPOINT_NAME
    )
    if not path.is_file():
        pytest.skip("local plan-bound checkpoint unavailable")
    loaded = plan.load_persisted_plan(path)
    assert loaded.start_unvisited_set_hash == plan.EXPECTED_START_UNVISITED_SET_HASH
    assert loaded.execution_symbol_hash == plan.EXPECTED_EXECUTION_SYMBOL_HASH
    assert loaded.plan_hash == plan.EXPECTED_FULL_CONTINUATION_PLAN_HASH


def test_resume_authority_drift_is_not_complete() -> None:
    record = _plan().record()
    record["DAILY_INPUT_MANIFEST_HASH"] = "0" * 64
    with pytest.raises(plan.FullContinuationPlanError, match="PLAN_HASH_MISMATCH|RESUME_DAILY_MANIFEST_DRIFT"):
        plan.validate_persisted_plan(record)


def test_isolated_stage_root_allows_only_exact_path(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    expected = data_root / "staging" / "r4a9-preclose-full-continuation-plan-v01"
    assert plan.require_isolated_plan_stage_root(data_root, expected) == expected
    with pytest.raises(plan.FullContinuationPlanError, match="ISOLATED_PLAN_STAGE_ROOT_MISMATCH"):
        plan.require_isolated_plan_stage_root(data_root, data_root / "curated" / "daily_bars")
    with pytest.raises(plan.FullContinuationPlanError, match="ISOLATED_PLAN_STAGE_ROOT_MISMATCH"):
        plan.require_isolated_plan_stage_root(data_root, data_root / "staging" / "other")
    with pytest.raises(plan.FullContinuationPlanError, match="ISOLATED_PLAN_STAGE_ROOT_MISMATCH"):
        plan.require_isolated_plan_stage_root(data_root, tmp_path / "outside")


def test_symlink_stage_escape_is_rejected(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    link = data_root / "staging" / "r4a9-preclose-full-continuation-plan-v01"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(plan.FullContinuationPlanError, match="PLAN_STAGE_ROOT_SYMLINK"):
        plan.require_isolated_plan_stage_root(data_root, link)


def test_checkpoint_authority_drift_fails_closed() -> None:
    checkpoint = _checkpoint()
    checkpoint["adapter_authority_sha"] = "0" * 40
    with pytest.raises(plan.FullContinuationPlanError, match="CHECKPOINT_ADAPTER_AUTHORITY_MISMATCH"):
        plan.validate_checkpoint_authority(
            checkpoint,
            start_checkpoint_hash=plan.EXPECTED_START_CHECKPOINT_HASH,
        )
    checkpoint = _checkpoint()
    checkpoint["status_conflict_overlay_keyset_hash"] = "0" * 64
    with pytest.raises(plan.FullContinuationPlanError, match="CHECKPOINT_OVERLAY_AUTHORITY_MISMATCH"):
        plan.validate_checkpoint_authority(
            checkpoint,
            start_checkpoint_hash=plan.EXPECTED_START_CHECKPOINT_HASH,
        )


def test_daily_manifest_drift_fails_closed() -> None:
    checkpoint = _checkpoint()
    with pytest.raises(plan.FullContinuationPlanError, match="CHECKPOINT_DAILY_MANIFEST_MISMATCH"):
        checkpoint["daily_input_manifest_hash"] = "0" * 64
        plan.validate_checkpoint_authority(
            checkpoint,
            start_checkpoint_hash=plan.EXPECTED_START_CHECKPOINT_HASH,
        )


def test_current_checkpoint_bytes_are_read_only_when_freezing_real_artifact() -> None:
    data_root = plan.DATA_ROOT_DEFAULT if hasattr(plan, "DATA_ROOT_DEFAULT") else Path(
        "/Users/luke808/AI/local-a-share-data-service-data"
    )
    path = data_root / plan.CURRENT_CHECKPOINT_REL
    if not path.is_file():
        pytest.skip("local R4A9 checkpoint unavailable")
    before = plan.sha256_file(path)
    checkpoint = plan.load_json(path)
    symbols = plan.start_unvisited_symbols(checkpoint)
    assert len(symbols) == 3310
    assert before == plan.EXPECTED_START_CHECKPOINT_HASH
    assert plan.sha256_file(path) == before


def test_freeze_path_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def poisoned_socket(*args: object, **kwargs: object) -> object:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", poisoned_socket)
    frozen = _plan()
    assert frozen.execution_symbol_n == 3310


def test_plan_bound_checkpoint_is_pre_execution_and_unauthorized(tmp_path: Path) -> None:
    frozen = _plan()
    record = plan.build_plan_bound_checkpoint(
        frozen,
        current_checkpoint_path=tmp_path / "start.json",
        current_checkpoint_sha256=plan.EXPECTED_START_CHECKPOINT_HASH,
    )
    assert record["PLAN_STATUS"] == "PENDING"
    assert record["EXECUTION_PROGRESS"]["EXECUTED_N"] == 0  # type: ignore[index]
    assert record["EXECUTION_PROGRESS"]["REMAINING_N"] == 3310  # type: ignore[index]
    assert record["FULL_R4A9_CONTINUATION_AUTHORIZED"] is False
    assert record["NETWORK_REQUEST_N"] == 0
