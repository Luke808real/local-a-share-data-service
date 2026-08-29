from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

import pytest


import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import rebase_r4a9_checkpoint_lineage_v01 as rebase  # noqa: E402


def _synthetic_rebase_inputs() -> tuple[set[str], dict[str, object], dict[str, dict[str, object]], dict[str, object], dict[str, set[date]]]:
    safe = {f"9{index:04d}.SH" for index in range(2_139)}
    unvisited = {f"8{index:04d}.SH" for index in range(3_312)} | {
        "600647.SH",
        "600766.SH",
        "603133.SH",
    }
    formal = safe | unvisited | {"002087.SZ", "300546.SZ"}
    old_units = {symbol: {"symbol": symbol, "STATE": "COMPLETE"} for symbol in safe}
    old_units["002087.SZ"] = {"symbol": "002087.SZ", "STATE": "COMPLETE"}
    old_units["300546.SZ"] = {"symbol": "300546.SZ", "STATE": "FAILED"}
    old = {
        "hash": rebase.OLD_CHECKPOINT_SHA256,
        "complete": safe | {"002087.SZ"},
        "failed": {"300546.SZ"},
        "unvisited": unvisited,
        "units": old_units,
    }
    artifact = {
        "formal_path": "/tmp/formal.parquet",
        "formal_file_sha256": "artifact-sha",
    }
    safe_artifacts = {symbol: artifact.copy() for symbol in safe}
    invalidated = artifact.copy()
    dates = {date(2016, 1, 1) + timedelta(days=index) for index in range(2_026)}
    dates.update({date(2024, 6, 13), date(2024, 6, 14)})
    while len(dates) > 2_026:
        dates.remove(max(dates - {date(2024, 6, 13), date(2024, 6, 14)}))
    return formal, old, safe_artifacts, invalidated, {
        "002087.SZ": dates,
        "300546.SZ": {date(2016, 9, 29), date(2016, 10, 10)},
    }


def test_authority_constants_are_current_task_values() -> None:
    assert rebase.BASE_HEAD == "baeedc4bb17ddbd4d04b53df29bd434a2808ca0e"
    assert rebase.CURRENT_DAILY_INPUT_MANIFEST_HASH == "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"
    assert rebase.COMPATIBILITY_VERDICT == "BOUNDED_INVALIDATION"
    assert rebase.NEW_CHECKPOINT_SCHEMA_VERSION == "R4A9_LINEAGE_SAFE_RESUME_V01"


def test_rebased_partition_is_exact_and_repaired_unvisited_stays_unvisited() -> None:
    formal, old, safe_artifacts, invalidated, dates = _synthetic_rebase_inputs()
    units, counts = rebase.build_rebased_units(
        formal_symbols=formal,
        old_checkpoint=old,
        directly_affected={"002087.SZ"},
        safe_artifacts=safe_artifacts,
        invalidated_artifact=invalidated,
        current_required_row_n=2_026,
        current_dates=dates,
    )
    assert counts == {
        "SAFE_COMPLETE": 2_139,
        "RECOMPUTE_REQUIRED": 1,
        "RETRY_REQUIRED": 1,
        "UNVISITED": 3_315,
    }
    assert len(units) == 5_456
    assert units["002087.SZ"]["STATE"] == "RECOMPUTE_REQUIRED"
    assert units["300546.SZ"]["STATE"] == "RETRY_REQUIRED"
    assert units["300546.SZ"]["REASON"] == "UPSTREAM_R3_DEFECT_REPAIRED"
    assert all(units[symbol]["STATE"] == "UNVISITED" for symbol in ("600647.SH", "600766.SH", "603133.SH"))


def test_safe_reuse_requires_directly_affected_to_be_old_complete() -> None:
    with pytest.raises(rebase.RebaseError, match="DIRECTLY_AFFECTED_NOT_COMPLETE"):
        rebase.compute_safe_reuse_set({"000001.SZ"}, {"002087.SZ"})


def test_exact_allowed_stage_root_passes(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    expected = data_root / "staging" / rebase.NEW_R4A9_STAGE_DIRNAME
    assert rebase.require_isolated_stage_root(data_root, expected) == expected.resolve()


@pytest.mark.parametrize(
    "relative",
    [
        "curated/daily_bars",
        "staging/other-stage",
    ],
)
def test_forbidden_stage_roots_fail_closed(tmp_path: Path, relative: str) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    with pytest.raises(rebase.RebaseError, match="ISOLATED_STAGE_ROOT_MISMATCH"):
        rebase.require_isolated_stage_root(data_root, data_root / relative)


def test_stage_root_outside_data_root_fails_closed(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    with pytest.raises(rebase.RebaseError, match="ISOLATED_STAGE_ROOT_MISMATCH"):
        rebase.require_isolated_stage_root(
            data_root,
            tmp_path / "outside" / rebase.NEW_R4A9_STAGE_DIRNAME,
        )


def test_symlink_escaping_staging_fails_closed_when_supported(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    requested = data_root / "staging" / rebase.NEW_R4A9_STAGE_DIRNAME
    try:
        requested.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this platform")
    with pytest.raises(rebase.RebaseError, match="ISOLATED_STAGE_ROOT_MISMATCH"):
        rebase.require_isolated_stage_root(data_root, requested)


def test_direct_run_forbidden_root_fails_before_any_write(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    forbidden = data_root / "curated" / "daily_bars"
    with pytest.raises(rebase.RebaseError, match="ISOLATED_STAGE_ROOT_MISMATCH"):
        rebase.run_rebase(repo_root=repo_root, data_root=data_root, stage_root=forbidden)
    assert sorted(path.relative_to(data_root) for path in data_root.rglob("*")) == [Path("staging")]


def test_resume_daily_manifest_mismatch_fails_closed() -> None:
    checkpoint = {"daily_input_manifest_hash": rebase.CURRENT_DAILY_INPUT_MANIFEST_HASH}
    assert rebase.validate_resume_input_gate(checkpoint, rebase.CURRENT_DAILY_INPUT_MANIFEST_HASH)
    with pytest.raises(rebase.RebaseError, match="COMPATIBILITY_REASSESSMENT_REQUIRED"):
        rebase.validate_resume_input_gate(checkpoint, "different-daily-manifest")


def test_old_checkpoint_mutation_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(b"before")
    before = hashlib.sha256(b"before").hexdigest()
    assert rebase.validate_old_checkpoint_unchanged(path, before)
    path.write_bytes(b"after")
    with pytest.raises(rebase.RebaseError, match="OLD_CHECKPOINT_MUTATED"):
        rebase.validate_old_checkpoint_unchanged(path, before)


def test_missing_reused_artifact_fails_closed(tmp_path: Path) -> None:
    old_root = tmp_path / "old"
    old_root.mkdir()
    entry = {
        "formal_path": str(old_root / "missing.parquet"),
        "MISSING_REQUIRED_N": 0,
        "UNEXPECTED_TRADED_N": 0,
        "TRADESTATUS_UNKNOWN_N": 0,
        "IDENTITY_FAILURE_N": 0,
        "WINDOW_SCOPE_FAILURE_N": 0,
        "DUPLICATE_N": 0,
        "POST_ASOF_N": 0,
        "INVALID_PRECLOSE_N": 0,
        "adapter_version": rebase.R4A9_CODE_HEAD,
        "contract": rebase.EXPECTED_OLD_CONTRACT,
    }
    with pytest.raises(rebase.RebaseError, match="FORMAL_ARTIFACT_MISSING"):
        rebase.verify_formal_artifact(entry, expected_symbol="000001.SZ", old_stage_root=old_root)


def test_no_provider_or_r4_execution_path_in_source() -> None:
    source = Path(rebase.__file__).read_text(encoding="utf-8")
    assert "import baostock" not in source
    assert "query_history_k_data_plus" not in source
    assert "CANONICAL_WRITE_EXECUTED" in source
    assert "R4A9_RESUME_AUTHORIZED" in source


def test_old_checkpoint_and_new_safety_contract_constants() -> None:
    assert rebase.OLD_CHECKPOINT_SHA256 == "d09c53dbca0b49ecb070b01c2740d1f122e9a327bfb9f7bdabca8739d4c17d6e"
    assert rebase.R4A9_QUERY_PLAN_HASH == "9773875fbae9494bc1d9477cd18633dbccb92112733d9a1077fc3a43bcc38a60"
    assert rebase.R4A9_CODE_HEAD == "795b1b8f6b688ecc2e94f85c09d80c365e648920"
