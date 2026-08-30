from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import reconcile_r4a9_canary_incident_v01 as incident  # noqa: E402
import run_r4a9_status_conflict_continuation_canary_v01 as scope_plan  # noqa: E402


def _checkpoint(*symbols: str) -> dict[str, object]:
    return {"units": {symbol: {"STATE": "UNVISITED"} for symbol in symbols}}


def _initial_checkpoint() -> dict[str, object]:
    return _checkpoint("300547.SZ", *scope_plan.OVERLAY_SYMBOLS)


def _plan() -> scope_plan.CanaryExecutionPlan:
    return scope_plan.freeze_canary_plan(
        _initial_checkpoint(),
        start_checkpoint_hash="a" * 64,
        daily_input_manifest_hash="b" * 64,
        adapter_authority_sha="c" * 40,
        overlay_keyset_hash="d" * 64,
    )


def test_control_symbol_is_selected_once_from_initial_snapshot() -> None:
    checkpoint = _initial_checkpoint()
    plan = _plan()
    assert plan.normal_control_symbol == "300547.SZ"
    checkpoint["units"]["300547.SZ"]["STATE"] = "COMPLETE"  # type: ignore[index]
    checkpoint["units"]["300548.SZ"] = {"STATE": "UNVISITED"}  # type: ignore[index]
    assert plan.ordered_canary_symbols == (
        "300547.SZ",
        "600647.SH",
        "600766.SH",
        "603133.SH",
    )
    assert plan.initial_unvisited_symbols == (
        "300547.SZ",
        "600647.SH",
        "600766.SH",
        "603133.SH",
    )


def test_frozen_canary_symbols_are_immutable() -> None:
    plan = _plan()
    with pytest.raises(FrozenInstanceError):
        plan.plan_hash = "e" * 64  # type: ignore[misc]
    with pytest.raises(AttributeError):
        plan.ordered_canary_symbols.append("300548.SZ")  # type: ignore[attr-defined]


def test_execution_plan_hash_is_deterministic_and_binds_start_state() -> None:
    first = _plan()
    second = _plan()
    assert first.plan_hash == second.plan_hash
    assert first.binding() == second.binding()

    changed_start = scope_plan.freeze_canary_plan(
        _initial_checkpoint(),
        start_checkpoint_hash="e" * 64,
        daily_input_manifest_hash="b" * 64,
        adapter_authority_sha="c" * 40,
        overlay_keyset_hash="d" * 64,
    )
    assert changed_start.plan_hash != first.plan_hash

    changed_set = _initial_checkpoint()
    changed_set["units"]["300548.SZ"] = {"STATE": "UNVISITED"}  # type: ignore[index]
    changed_unvisited = scope_plan.freeze_canary_plan(
        changed_set,
        start_checkpoint_hash="a" * 64,
        daily_input_manifest_hash="b" * 64,
        adapter_authority_sha="c" * 40,
        overlay_keyset_hash="d" * 64,
    )
    assert changed_unvisited.start_unvisited_set_hash != first.start_unvisited_set_hash
    assert changed_unvisited.plan_hash != first.plan_hash


def test_fifth_symbol_is_blocked_before_provider_callback() -> None:
    calls: list[str] = []

    def provider() -> str:
        calls.append("called")
        return "unexpected"

    with pytest.raises(scope_plan.CanaryScopeError, match="FROZEN_CANARY_SYMBOL_SCOPE_VIOLATION"):
        scope_plan.guarded_provider_request(_plan(), "300548.SZ", provider)
    assert calls == []


def test_checkpoint_mutation_does_not_change_current_invocation_membership() -> None:
    checkpoint = _initial_checkpoint()
    plan = _plan()
    executed: list[str] = []

    def run_unit(symbol: str, phase_index: int, frozen: scope_plan.CanaryExecutionPlan) -> dict[str, object]:
        assert frozen is plan
        assert phase_index == len(executed) + 1
        executed.append(symbol)
        checkpoint["units"][symbol]["STATE"] = "COMPLETE"  # type: ignore[index]
        if symbol == "300547.SZ":
            checkpoint["units"]["300548.SZ"] = {"STATE": "UNVISITED"}  # type: ignore[index]
        return {"symbol": symbol}

    outcomes = scope_plan.execute_frozen_plan(plan, checkpoint, run_unit)
    assert executed == list(plan.ordered_canary_symbols)
    assert [outcome["symbol"] for outcome in outcomes] == list(plan.ordered_canary_symbols)
    assert all(outcome["CANARY_PLAN_HASH"] == plan.plan_hash for outcome in outcomes)


def test_future_resume_invocation_can_create_a_new_plan() -> None:
    checkpoint = _initial_checkpoint()
    first = _plan()
    checkpoint["units"]["300547.SZ"]["STATE"] = "COMPLETE"  # type: ignore[index]
    checkpoint["units"]["300548.SZ"] = {"STATE": "UNVISITED"}  # type: ignore[index]
    resumed = scope_plan.freeze_canary_plan(
        checkpoint,
        start_checkpoint_hash="f" * 64,
        daily_input_manifest_hash="b" * 64,
        adapter_authority_sha="c" * 40,
        overlay_keyset_hash="d" * 64,
    )
    assert resumed.normal_control_symbol == "300548.SZ"
    assert resumed.ordered_canary_symbols != first.ordered_canary_symbols
    assert resumed.plan_hash != first.plan_hash


def test_phase_receipt_binding_is_exact() -> None:
    plan = _plan()
    binding = plan.phase_binding(1, "300547.SZ")
    assert binding == {
        "CANARY_PLAN_HASH": plan.plan_hash,
        "PHASE_INDEX": 1,
        "AUTHORIZED_SYMBOL": "300547.SZ",
    }
    with pytest.raises(scope_plan.CanaryScopeError, match="PHASE_SYMBOL_MISMATCH"):
        plan.phase_binding(1, "600647.SH")


def test_current_incident_scope_is_recovered_from_real_receipts() -> None:
    path = incident.DATA_ROOT_DEFAULT / incident.CURRENT_CHECKPOINT_REL
    if not path.exists():
        pytest.skip("local R4A9 incident staging is unavailable")
    checkpoint = incident.load_json(path)
    actual = incident.recover_incident_symbols(checkpoint, path.parent)
    assert actual == tuple(sorted(incident.INCIDENT_SYMBOLS))
    assert set(actual) - set(incident.AUTHORIZED_CANARY_SYMBOLS) == {"300548.SZ"}


def test_initial_plan_snapshot_is_reconstructed_from_receipt_chain() -> None:
    path = incident.DATA_ROOT_DEFAULT / incident.CURRENT_CHECKPOINT_REL
    if not path.exists():
        pytest.skip("local R4A9 incident staging is unavailable")
    checkpoint = incident.load_json(path)
    actual = incident.recover_incident_symbols(checkpoint, path.parent)
    snapshot, start_hash = incident.reconstruct_initial_canary_snapshot(checkpoint, path.parent, actual)
    assert start_hash == "60a8f49cc7bfdbed59bb5a6e3123eaa11d8a85856a70dee55d0c4dacda11e171"
    assert incident.state_counts(snapshot) == {"SAFE_COMPLETE": 2139, "COMPLETE": 2, "UNVISITED": 3315}
    plan = scope_plan.freeze_canary_plan(
        snapshot,
        start_checkpoint_hash=start_hash,
        daily_input_manifest_hash=incident.EXPECTED_DAILY_MANIFEST_HASH,
        adapter_authority_sha=incident.ADAPTER_AUTHORITY_SHA,
        overlay_keyset_hash=incident.OVERLAY_KEYSET_HASH,
    )
    assert plan.ordered_canary_symbols == tuple(incident.AUTHORIZED_CANARY_SYMBOLS)


def test_real_five_units_validate_offline_without_provider_calls() -> None:
    path = incident.DATA_ROOT_DEFAULT / incident.CURRENT_CHECKPOINT_REL
    if not path.exists():
        pytest.skip("local R4A9 incident staging is unavailable")
    checkpoint = incident.load_json(path)
    orchestrator = incident.load_recovery_module()
    overlay = incident.load_overlay_evidence(incident.DATA_ROOT_DEFAULT)
    actual = incident.recover_incident_symbols(checkpoint, path.parent)
    results = [
        incident.validate_unit(
            checkpoint=checkpoint,
            symbol=symbol,
            stage_root=path.parent,
            orchestrator=orchestrator,
            overlay=overlay,
        )
        for symbol in actual
    ]
    assert len(results) == 5
    assert sum(result["primary_status_conflict_n"] == 1 for result in results) == 3
    assert sum(result["primary_status_conflict_n"] == 0 for result in results) == 2


def test_complete_artifact_snapshot_detects_mutation_and_deletion(tmp_path: Path) -> None:
    artifact = tmp_path / "unit.parquet"
    artifact.write_bytes(b"stable")
    checkpoint = {"units": {"X": {"STATE": "COMPLETE", "formal_path": str(artifact)}}}
    before = incident.snapshot_complete_artifacts(checkpoint)
    assert incident.compare_complete_artifact_snapshots(before, checkpoint) == (0, 0)
    artifact.write_bytes(b"changed")
    assert incident.compare_complete_artifact_snapshots(before, checkpoint) == (1, 0)
    artifact.unlink()
    assert incident.compare_complete_artifact_snapshots(before, checkpoint) == (0, 1)


def test_new_lineage_checkpoint_keeps_valid_but_unauthorized_distinction() -> None:
    current = _initial_checkpoint()
    plan = _plan()
    payload = incident.build_new_checkpoint(
        current,
        current_path=incident.CURRENT_CHECKPOINT_REL,
        current_sha="a" * 64,
        plan=plan,
        unit_results=[],
    )
    assert payload["unauthorized_execution_symbol"] == "300548.SZ"
    assert payload["unauthorized_execution_data_status"] == "VALID"
    assert payload["incident_disposition"] == "ADOPT_VALID_RESULT_PRESERVE_LINEAGE"
    assert payload["full_r4a9_continuation_authorized"] is False


def test_new_stage_root_rejects_curated_elsewhere_and_outside(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    expected = data_root / "staging" / incident.NEW_STAGE_NAME
    assert incident.require_new_stage_root(data_root, expected) == expected
    with pytest.raises(incident.IncidentError, match="NEW_STAGE_ROOT_MISMATCH"):
        incident.require_new_stage_root(data_root, data_root / "curated" / "daily_bars")
    with pytest.raises(incident.IncidentError, match="NEW_STAGE_ROOT_MISMATCH"):
        incident.require_new_stage_root(data_root, tmp_path / "outside")


def test_new_stage_root_rejects_symlink_escape_and_write_guard_runs_first(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    expected = data_root / "staging" / incident.NEW_STAGE_NAME
    try:
        expected.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink unsupported")
    with pytest.raises(incident.IncidentError, match="NEW_STAGE_ROOT_SYMLINK|NEW_STAGE_ROOT_MISMATCH"):
        incident.require_new_stage_root(data_root, expected)
    assert not (outside / "manifest.json").exists()


def test_new_checkpoint_write_rejects_forbidden_root_before_mkdir(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    forbidden = data_root / "curated" / "manifest.json"
    with pytest.raises(incident.IncidentError, match="NEW_STAGE_ROOT_MISMATCH"):
        incident.write_new_checkpoint(forbidden, {"state": "invalid"}, data_root)
    assert not forbidden.parent.exists()


def test_scope_module_has_no_provider_execution_import() -> None:
    source = Path(scope_plan.__file__).read_text(encoding="utf-8").lower()
    assert "baostock" not in source
    assert "provider_call" in source


def test_incident_symbol_hash_is_order_sensitive_and_deterministic() -> None:
    symbols = ["300547.SZ", "300548.SZ", "600647.SH", "600766.SH", "603133.SH"]
    assert incident.scope_plan.canary_symbol_hash(symbols) == incident.scope_plan.canary_symbol_hash(symbols)
    assert incident.scope_plan.canary_symbol_hash(symbols) != incident.scope_plan.canary_symbol_hash(list(reversed(symbols)))
    assert json.loads(incident.canonical_json_bytes({"b": 2, "a": 1})) == {"a": 1, "b": 2}
