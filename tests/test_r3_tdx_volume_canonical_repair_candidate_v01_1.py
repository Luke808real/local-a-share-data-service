"""Offline safety and reuse tests for R3 candidate V01.1."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))

import run_r3_tdx_volume_canonical_repair_candidate_v01 as candidate  # noqa: E402
import r3_tdx_volume_canonical_repair_candidate_v01_1 as audit  # noqa: E402


def _source_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["000001.SZ", "000002.SZ"],
            "trade_date": [datetime(2020, 1, 2), datetime(2020, 1, 2)],
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.05, 2.05],
            "volume": [1000, 2000],
            "amount": [100.0, 200.0],
            "source": ["tdx_protocol", "tdx_protocol"],
            "data_version": ["v2", "v2"],
            "fetched_at": [datetime(2026, 1, 1), datetime(2026, 1, 1)],
        }
    ).with_columns(
        pl.col("trade_date").cast(pl.Date),
        pl.col("fetched_at").cast(pl.Datetime("us")),
    )


def _change() -> dict:
    return {
        "symbol": "000001.SZ",
        "trade_date": "2020-01-02",
        "classification": "AMBIGUOUS",
        "old_volume": 1000,
        "fresh_tdx_volume": 900,
        "predicted_corrected_volume": None,
        "fetch_status": "RESOLVED",
    }


def test_exact_allowed_candidate_staging_path_passes(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    stage_root = data_root / "staging" / candidate.STAGING_ROOT_NAME
    assert candidate.require_isolated_candidate_root(data_root, stage_root) == stage_root


@pytest.mark.parametrize(
    "relative_path",
    [
        "curated",
        "curated/daily_bars",
        "staging/other-candidate",
        "other",
    ],
)
def test_forbidden_stage_roots_fail_closed(tmp_path: Path, relative_path: str):
    data_root = tmp_path / "data"
    data_root.mkdir()
    with pytest.raises(candidate.CandidateRepairError, match="ISOLATED_STAGE_ROOT"):
        candidate.require_isolated_candidate_root(data_root, data_root / relative_path)


def test_stage_root_outside_data_root_fails_closed(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside" / candidate.STAGING_ROOT_NAME
    with pytest.raises(candidate.CandidateRepairError, match="ISOLATED_STAGE_ROOT"):
        candidate.require_isolated_candidate_root(data_root, outside)


def test_relative_and_parent_traversal_stage_roots_fail_closed(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    with pytest.raises(candidate.CandidateRepairError, match="ISOLATED_STAGE_ROOT_NOT_ABSOLUTE"):
        candidate.require_isolated_candidate_root(data_root, Path("staging") / candidate.STAGING_ROOT_NAME)
    traversed = data_root / "staging" / candidate.STAGING_ROOT_NAME / ".." / "other"
    with pytest.raises(candidate.CandidateRepairError, match="ISOLATED_STAGE_ROOT"):
        candidate.require_isolated_candidate_root(data_root, traversed)


def test_symlink_escaping_staging_fails_closed(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    stage_root = data_root / "staging" / candidate.STAGING_ROOT_NAME
    stage_root.parent.mkdir()
    try:
        stage_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(candidate.CandidateRepairError, match="ISOLATED_STAGE_ROOT"):
        candidate.require_isolated_candidate_root(data_root, stage_root)


def test_direct_run_candidate_forbidden_root_fails_before_any_write(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    forbidden = data_root / "curated" / "daily_bars"
    with pytest.raises(candidate.CandidateRepairError, match="ISOLATED_STAGE_ROOT"):
        candidate.run_candidate(repo_root=tmp_path, data_root=data_root, stage_root=forbidden)
    assert not list(data_root.rglob("*"))


def _patch_small_audit_authority(monkeypatch: pytest.MonkeyPatch, records: list[dict]) -> None:
    monkeypatch.setattr(audit, "INPUT_MANIFEST_HASH", "test-input")
    monkeypatch.setattr(audit, "CHANGED_KEY_MANIFEST_HASH", "test-changed")
    monkeypatch.setattr(audit, "AFFECTED_FILE_N", 1)
    monkeypatch.setattr(audit, "CHANGED_KEY_N", 1)
    monkeypatch.setattr(audit, "AFFECTED_FILE_MANIFEST_HASH", candidate._hash_file_manifest(records))
    monkeypatch.setattr(audit, "CANDIDATE_DATASET_HASH", candidate._candidate_dataset_hash(records))


def _write_small_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    relative_path = "curated/daily_bars/trade_date=2020-01-02/part.parquet"
    source_path = data_root / relative_path
    source_path.parent.mkdir(parents=True)
    source = _source_frame()
    source.write_parquet(source_path)
    stage_root = data_root / "staging" / candidate.STAGING_ROOT_NAME
    candidate_path = stage_root / relative_path
    record = candidate.build_candidate_file(
        source_path,
        candidate_path,
        relative_path,
        {("000001.SZ", "2020-01-02"): _change()},
    )
    records = [record]
    _patch_small_audit_authority(monkeypatch, records)
    input_manifest = {
        "INPUT_FILE_N": 1,
        "INPUT_MANIFEST_HASH": "test-input",
        "FILES": [{"relative_path": relative_path, "sha256": record["source_sha256"]}],
    }
    monkeypatch.setattr(audit.candidate, "verify_current_input", lambda data_root, repo_root: input_manifest)
    monkeypatch.setattr(
        audit.candidate,
        "load_changed_manifest",
        lambda repo_root: {("000001.SZ", "2020-01-02"): _change()},
    )
    manifest = {
        "REPORT": candidate.TASK,
        "INPUT_MANIFEST_HASH": audit.INPUT_MANIFEST_HASH,
        "CHANGED_KEY_MANIFEST_HASH": audit.CHANGED_KEY_MANIFEST_HASH,
        "AFFECTED_FILE_N": 1,
        "CHANGED_KEY_N": 1,
        "FILES": records,
        "AFFECTED_FILE_MANIFEST_HASH": audit.AFFECTED_FILE_MANIFEST_HASH,
        "CANDIDATE_DATASET_HASH": audit.CANDIDATE_DATASET_HASH,
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "CANONICAL_WRITE_EXECUTED": False,
        "CANONICAL_BYTES_MUTATED": False,
    }
    validation = {
        "INPUT_MANIFEST_HASH": audit.INPUT_MANIFEST_HASH,
        "ROW_COUNT_DELTA": 0,
        "SCHEMA_DELTA": 0,
        "CHANGED_ROW_N": 1,
        "CHANGED_CELL_N": 1,
        "CHANGED_COLUMNS": ["volume"],
        "AFFECTED_FILE_N": 1,
        "AFFECTED_FILE_MANIFEST_HASH": audit.AFFECTED_FILE_MANIFEST_HASH,
        "CANDIDATE_DATASET_HASH": audit.CANDIDATE_DATASET_HASH,
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
    candidate.write_canonical_json(stage_root / "candidate_repair_manifest.json", manifest)
    candidate.write_canonical_json(stage_root / "candidate_validation_report.json", validation)
    return data_root, stage_root, relative_path, candidate_path


def test_existing_candidate_reuse_passes_without_rebuild(tmp_path: Path, monkeypatch):
    data_root, stage_root, _, _ = _write_small_candidate(tmp_path, monkeypatch)
    result = audit.audit_existing_candidate(
        repo_root=tmp_path, data_root=data_root, stage_root=stage_root
    )
    assert result["CANDIDATE_REUSED"] is True
    assert result["CANDIDATE_REBUILT"] is False
    assert result["FILES_REVALIDATED_N"] == 1


def test_existing_manifest_hash_mismatch_fails_closed(tmp_path: Path, monkeypatch):
    data_root, stage_root, _, _ = _write_small_candidate(tmp_path, monkeypatch)
    manifest = candidate.load_json(stage_root / "candidate_repair_manifest.json")
    manifest["CANDIDATE_DATASET_HASH"] = "wrong"
    candidate.write_canonical_json(stage_root / "candidate_repair_manifest.json", manifest)
    with pytest.raises(candidate.CandidateRepairError, match="DATASET_HASH"):
        audit.audit_existing_candidate(repo_root=tmp_path, data_root=data_root, stage_root=stage_root)


def test_candidate_file_missing_fails_closed(tmp_path: Path, monkeypatch):
    data_root, stage_root, relative_path, candidate_path = _write_small_candidate(tmp_path, monkeypatch)
    candidate_path.unlink()
    with pytest.raises(candidate.CandidateRepairError, match="CANDIDATE_FILE_MISSING"):
        audit.audit_existing_candidate(repo_root=tmp_path, data_root=data_root, stage_root=stage_root)


def test_candidate_sha_mismatch_fails_closed(tmp_path: Path, monkeypatch):
    data_root, stage_root, _, candidate_path = _write_small_candidate(tmp_path, monkeypatch)
    candidate_path.write_bytes(b"tampered")
    with pytest.raises(candidate.CandidateRepairError, match="CANDIDATE_SHA_MISMATCH"):
        audit.audit_existing_candidate(repo_root=tmp_path, data_root=data_root, stage_root=stage_root)


def test_current_input_drift_fails_closed(tmp_path: Path, monkeypatch):
    data_root, stage_root, _, _ = _write_small_candidate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        audit.candidate,
        "verify_current_input",
        lambda data_root, repo_root: (_ for _ in ()).throw(
            candidate.CandidateRepairError("INPUT_MANIFEST_GATE_FAILED")
        ),
    )
    with pytest.raises(candidate.CandidateRepairError, match="INPUT_MANIFEST_GATE_FAILED"):
        audit.audit_existing_candidate(repo_root=tmp_path, data_root=data_root, stage_root=stage_root)
