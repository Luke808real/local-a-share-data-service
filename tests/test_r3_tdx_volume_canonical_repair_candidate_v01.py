"""Offline tests for the isolated R3 canonical-repair candidate builder."""

from __future__ import annotations

import inspect
import sys
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_r3_tdx_volume_canonical_repair_candidate_v01 as candidate  # noqa: E402


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


def _change(symbol: str = "000001.SZ", old: int = 1000, fresh: int = 900) -> dict:
    return {
        "symbol": symbol,
        "trade_date": "2020-01-02",
        "classification": "AMBIGUOUS",
        "old_volume": old,
        "fresh_tdx_volume": fresh,
        "predicted_corrected_volume": None,
        "fetch_status": "RESOLVED",
    }


def test_input_drift_fails_closed():
    pre = {"INPUT_FILE_N": 1, "INPUT_MANIFEST_HASH": "a", "FILES": [1]}
    post = {"INPUT_FILE_N": 1, "INPUT_MANIFEST_HASH": "b", "FILES": [1]}
    with pytest.raises(candidate.CandidateRepairError, match="INPUT_DRIFT"):
        candidate.require_input_stable(pre, post)


def test_changed_manifest_hash_mismatch_and_duplicate_fail_closed(tmp_path: Path, monkeypatch):
    path = tmp_path / "reports" / "implementation" / candidate.CHANGED_MANIFEST_NAME
    path.parent.mkdir(parents=True)
    rows = [_change()]
    path.write_bytes(candidate.canonical_json_bytes(rows))
    monkeypatch.setattr(candidate, "CHANGED_KEY_N", 1)
    monkeypatch.setattr(candidate, "CHANGED_KEY_MANIFEST_HASH", "wrong")
    with pytest.raises(candidate.CandidateRepairError, match="HASH_MISMATCH"):
        candidate.load_changed_manifest(tmp_path)

    monkeypatch.setattr(candidate, "CHANGED_KEY_MANIFEST_HASH", candidate.sha256_file(path))
    path.write_bytes(candidate.canonical_json_bytes([_change(), _change()]))
    monkeypatch.setattr(candidate, "CHANGED_KEY_N", 2)
    monkeypatch.setattr(candidate, "CHANGED_KEY_MANIFEST_HASH", candidate.sha256_file(path))
    monkeypatch.setattr(candidate, "EXPECTED_PROVABLY_AFFECTED_N", 0)
    monkeypatch.setattr(candidate, "EXPECTED_AMBIGUOUS_N", 2)
    with pytest.raises(candidate.CandidateRepairError, match="DUPLICATE"):
        candidate.load_changed_manifest(tmp_path)


def test_missing_duplicate_and_old_volume_mismatch_fail_closed():
    source = _source_frame()
    with pytest.raises(candidate.CandidateRepairError, match="CARDINALITY"):
        candidate.validate_source_preconditions(
            source, {("000003.SZ", "2020-01-02"): _change("000003.SZ")}, "part.parquet"
        )
    duplicate = pl.concat([source, source.head(1)])
    with pytest.raises(candidate.CandidateRepairError, match="CARDINALITY"):
        candidate.validate_source_preconditions(
            duplicate, {("000001.SZ", "2020-01-02"): _change()}, "part.parquet"
        )
    with pytest.raises(candidate.CandidateRepairError, match="OLD_VOLUME"):
        candidate.validate_source_preconditions(
            source, {("000001.SZ", "2020-01-02"): _change(old=999)}, "part.parquet"
        )


def test_candidate_changes_only_volume_and_preserves_schema_rows_and_source(tmp_path: Path):
    source_path = tmp_path / "source.parquet"
    candidate_path = tmp_path / "candidate" / "part.parquet"
    source = _source_frame()
    source.write_parquet(source_path)
    before = source_path.read_bytes()
    result = candidate.build_candidate_file(
        source_path,
        candidate_path,
        "curated/daily_bars/trade_date=2020-01-02/part.parquet",
        {("000001.SZ", "2020-01-02"): _change()},
    )
    output = pl.read_parquet(candidate_path)
    assert source_path.read_bytes() == before
    assert output.height == source.height
    assert output.schema == source.schema
    assert output.columns == source.columns
    assert output["volume"].to_list() == [900, 2000]
    for column in source.columns:
        if column != "volume":
            assert output[column].equals(source[column])
    assert result["CHANGED_ROW_N"] == result["CHANGED_CELL_N"] == 1
    assert result["CHANGED_COLUMNS"] == ["volume"]
    assert not output.filter(
        (pl.col("symbol") == "300546.SZ")
        & pl.col("trade_date").is_in([candidate.date(2016, 9, 29), candidate.date(2016, 10, 10)])
    ).height


def test_run_candidate_writes_only_isolated_stage_and_not_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_root = tmp_path / "data"
    relative_path = "curated/daily_bars/trade_date=2020-01-02/part.parquet"
    source_path = data_root / relative_path
    source_path.parent.mkdir(parents=True)
    _source_frame().write_parquet(source_path)
    before_files = {
        path.relative_to(data_root): path.read_bytes()
        for path in data_root.rglob("*")
        if path.is_file()
    }
    input_manifest = {
        "INPUT_FILE_N": 1,
        "INPUT_MANIFEST_HASH": "test-input-manifest",
        "FILES": [{"relative_path": relative_path}],
    }
    monkeypatch.setattr(candidate, "verify_current_input", lambda data_root, repo_root: input_manifest)
    monkeypatch.setattr(candidate, "build_input_file_manifest", lambda data_root: input_manifest)
    monkeypatch.setattr(
        candidate,
        "load_changed_manifest",
        lambda repo_root: {("000001.SZ", "2020-01-02"): _change()},
    )
    monkeypatch.setattr(candidate, "CHANGED_KEY_N", 1)
    stage_root = data_root / "staging" / candidate.STAGING_ROOT_NAME

    result = candidate.run_candidate(
        repo_root=tmp_path, data_root=data_root, stage_root=stage_root
    )

    after_files = {
        path.relative_to(data_root): path.read_bytes()
        for path in data_root.rglob("*")
        if path.is_file() and not path.is_relative_to(stage_root)
    }
    assert after_files == before_files
    assert stage_root.is_dir()
    assert result["VALIDATION"]["CANONICAL_WRITE_EXECUTED"] is False
    assert result["VALIDATION"]["CANONICAL_BYTES_MUTATED"] is False


def test_forbidden_300546_keys_are_rejected_from_changed_manifest(tmp_path: Path, monkeypatch):
    path = tmp_path / "reports" / "implementation" / candidate.CHANGED_MANIFEST_NAME
    path.parent.mkdir(parents=True)
    row = _change("300546.SZ")
    row["trade_date"] = "2016-09-29"
    path.write_bytes(candidate.canonical_json_bytes([row]))
    monkeypatch.setattr(candidate, "CHANGED_KEY_N", 1)
    monkeypatch.setattr(candidate, "CHANGED_KEY_MANIFEST_HASH", candidate.sha256_file(path))
    monkeypatch.setattr(candidate, "EXPECTED_PROVABLY_AFFECTED_N", 0)
    monkeypatch.setattr(candidate, "EXPECTED_AMBIGUOUS_N", 1)
    with pytest.raises(candidate.CandidateRepairError, match="FORBIDDEN_300546"):
        candidate.load_changed_manifest(tmp_path)


def test_candidate_tool_has_no_network_or_canonical_write_path():
    source = inspect.getsource(candidate)
    for forbidden in ("TdxWireClient", "baostock", "urllib", "requests", "socket"):
        assert forbidden not in source
    assert "candidate_path" in source
    assert "source_path.write_parquet" not in source
