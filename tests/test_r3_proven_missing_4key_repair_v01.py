"""Fail-closed tests for the bounded four-key R3 repair."""

from __future__ import annotations

import hashlib
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_r3_proven_missing_4key_repair_v01 as repair  # noqa: E402


def _fact(symbol: str, volume: int = 100) -> dict:
    return {
        "canonical": {
            "symbol": symbol,
            "trade_date": date(2024, 6, 13),
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.0,
            "volume": volume,
            "amount": 100.0,
            "source": "tushare",
            "data_version": "v2",
            "fetched_at": datetime(2026, 7, 31, tzinfo=timezone.utc),
        }
    }


def _facts() -> dict[tuple[str, date], dict]:
    return {
        (symbol, date(2024, 6, 13)): _fact(symbol, 100 + index)
        for index, (symbol, _trade_day) in enumerate(repair.TARGET_KEYS)
    }


def _frame(include_target: bool = False) -> pl.DataFrame:
    rows = [
        {
            "symbol": "000001.SZ",
            "trade_date": date(2024, 6, 13),
            "open": 2.0,
            "high": 2.1,
            "low": 1.9,
            "close": 2.0,
            "volume": 2000,
            "amount": 4000.0,
            "source": "baostock",
            "data_version": "v2",
            "fetched_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
        }
    ]
    if include_target:
        rows.append(_facts()[repair.TARGET_KEYS[0]]["canonical"])
    return pl.DataFrame(rows, schema=repair.CANONICAL_SCHEMA)


def test_exact_four_key_scope_and_frozen_keyset_hash():
    assert repair.TARGET_KEY_N == 4
    assert len(repair.TARGET_KEYS) == 4
    assert repair.keyset_hash(repair.TARGET_KEYS) == repair.TARGET_KEYSET_HASH


def test_authority_artifacts_and_exception_registry_are_frozen():
    repo = Path(__file__).resolve().parents[1]
    authority = repair.load_repair_authority(repo)
    assert set(repair.TARGET_KEYS).issubset(authority["authority_rows"])
    registry = repair.build_exception_registry(repo)
    assert registry["KNOWN_HISTORICAL_QUALITY_EXCEPTION_N"] == 15047
    assert registry["KNOWN_HISTORICAL_QUALITY_EXCEPTION_KEYSET_HASH"] == (
        "4f2832a314d34dc0eb797d56ef69dcee12c977621febe838b2f046acd75ad195"
    )
    assert registry["SCOPES_DISJOINT"] is True


def test_frozen_input_drift_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    data_root = tmp_path / "data"
    (data_root / "curated/daily_bars").mkdir(parents=True)
    drifted = {
        "INPUT_FILE_N": repair.INPUT_FILE_N,
        "INPUT_MANIFEST_HASH": "drift",
        "FILES": [],
    }
    monkeypatch.setattr(repair, "build_input_file_manifest", lambda _root: drifted)
    with pytest.raises(repair.RepairError, match="INPUT_MANIFEST_DRIFT"):
        repair.require_frozen_input(repo, data_root)


def test_existing_target_key_fails_closed(tmp_path: Path):
    data_root = tmp_path / "data"
    path = data_root / "curated/daily_bars/trade_date=2024-06-13/part.parquet"
    path.parent.mkdir(parents=True)
    _frame(include_target=True).write_parquet(path)
    manifest = {
        "FILES": [{"relative_path": "curated/daily_bars/trade_date=2024-06-13/part.parquet"}]
    }
    with pytest.raises(repair.RepairError, match="TARGET_KEY_ALREADY_EXISTS"):
        repair.inspect_target_precondition(data_root, manifest)


def test_incomplete_repair_source_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    source = tmp_path / "tushare.parquet"
    row = {
        "provider": "TUSHARE",
        "provider_version": "1.4.29",
        "fetched_at": datetime(2026, 7, 31, tzinfo=timezone.utc),
        "ingest_run_id": "run",
        "source_unit": "unknown",
        "normalized_unit": "yuan;shares;yuan",
        "row_hash": "row",
        "code": "002087",
        "trade_date": date(2024, 6, 13),
        "open": 0.17,
        "high": 0.18,
        "low": 0.16,
        "close": 0.16,
        "preclose": 0.17,
        "volume": 20061549,
        "amount": 3286918,
        "trade_status": True,
    }
    pl.DataFrame([row]).write_parquet(source)
    file_hash = repair.sha256_file(source)
    monkeypatch.setattr(repair, "TUSHARE_FILE", source)
    monkeypatch.setattr(repair, "LOCAL_TUSHARE_FILE_SHA256", file_hash)
    target = repair.TARGET_KEYS[0]
    evidence = {
        "secondary_observation": {
            "daily": {
                "provider": "TUSHARE",
                "provider_version": "1.4.29",
                "symbol": target[0],
                "trade_date": target[1].isoformat(),
                "trade_status": True,
                "file": str(source),
                "file_sha256": file_hash,
            }
        }
    }
    authority = {"evidence_rows": {target: evidence}}
    with pytest.raises(repair.RepairError, match="TUSHARE_ROW_SOURCE_UNIT_UNKNOWN"):
        repair.load_tushare_facts(authority)


def test_candidate_is_exactly_four_insertions_and_preserves_existing_cells():
    source = _frame()
    candidate = repair.build_candidate_frame(source, _facts())
    diff = repair.exact_insert_diff(source, candidate, _facts())
    assert diff["INSERTED_ROW_N"] == 4
    assert diff["DELETED_ROW_N"] == 0
    assert diff["MODIFIED_EXISTING_ROW_N"] == 0
    assert diff["CHANGED_CELL_N"] == 4
    assert diff["CHANGED_COLUMNS"] == []
    assert candidate.schema == source.schema
    assert candidate.height == source.height + 4


def test_existing_row_mutation_and_duplicate_key_fail_closed():
    source = _frame()
    candidate = repair.build_candidate_frame(source, _facts())
    mutated = candidate.with_columns(
        pl.when(pl.col("symbol") == "000001.SZ")
        .then(pl.lit(99.0))
        .otherwise(pl.col("open"))
        .alias("open")
    )
    with pytest.raises(repair.RepairError, match="MODIFIED_EXISTING_ROW"):
        repair.exact_insert_diff(source, mutated, _facts())
    duplicate = pl.concat([candidate, candidate.head(1)], how="vertical")
    with pytest.raises(repair.RepairError, match="DUPLICATE_KEY_IN_CANDIDATE"):
        repair.exact_insert_diff(source, duplicate, _facts())


def test_forbidden_stage_root_fails_before_any_write(tmp_path: Path):
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    outside = tmp_path / "outside"
    forbidden = data_root / "curated/daily_bars"
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    with pytest.raises(repair.RepairError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        repair.run_candidate(repo_root=tmp_path, data_root=data_root, stage_root=forbidden)
    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert after == before
    assert not outside.exists()


def test_same_file_hash_contract_is_deterministic():
    value = {"a": 1, "b": ["x", "y"]}
    assert repair.sha256_json(value) == repair.sha256_json(value)
    assert repair.sha256_bytes(repair.canonical_json_bytes(value)) == hashlib.sha256(
        b'{"a":1,"b":["x","y"]}'
    ).hexdigest()


def test_repair_tool_has_no_provider_client_or_network_import():
    source = (Path(__file__).resolve().parents[1] / "tools" / "run_r3_proven_missing_4key_repair_v01.py").read_text()
    assert "import baostock" not in source
    assert "import tushare" not in source
    assert "urllib.request" not in source
