"""Direct no-network contract tests for the frozen R3 incremental updater."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("r3_incremental", REPO / "tools/run_r3_frozen_shsz_incremental_v01.py")
runner = importlib.util.module_from_spec(spec); assert spec.loader is not None
sys.modules[spec.name] = runner; spec.loader.exec_module(runner)


def _authority(tmp_path: Path, records: list[dict]) -> None:
    symbols = sorted({item["symbol"] for item in records})
    runner.FORMAL_IDENTITY_N = len(symbols)
    runner.FORMAL_IDENTITY_HASH = runner.sha256_bytes(runner.canonical(symbols))
    path = tmp_path / "authority.json"
    path.write_text(json.dumps({"FORMAL_SYMBOL_N": len(symbols), "FORMAL_IDENTITY_HASH": runner.FORMAL_IDENTITY_HASH, "requests": records}))
    runner.AUTHORITY = path


def _records() -> list[dict]:
    return [
        {"symbol": "000001.SZ", "list_date": "2010-01-01", "delist_date": None},
        {"symbol": "600000.SH", "list_date": "2026-09-09", "delist_date": None},
        {"symbol": "000001.SZ", "list_date": "2010-01-01", "delist_date": None},
    ]


def test_frozen_authority_and_lifecycle_guards(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "AUTHORITY", tmp_path / "authority.json")
    monkeypatch.setattr(runner, "ACTIVE_AUTHORITY_POINTER", tmp_path / "active.json")
    _authority(tmp_path, _records())
    symbols, records = runner.frozen_authority()
    assert symbols == ("000001.SZ", "600000.SH")
    assert runner.eligible_symbols("2026-09-08", records) == ("000001.SZ",)
    assert runner.eligible_symbols("2026-09-09", records) == symbols
    bad = _records(); bad[-1] = {**bad[-1], "delist_date": "2026-01-01"}
    _authority(tmp_path, bad)
    with pytest.raises(runner.IncrementalError, match="LIFECYCLE_CONFLICT"):
        runner.frozen_authority()


def test_frozen_authority_rejects_count_hash_and_exchange(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "AUTHORITY", tmp_path / "authority.json")
    monkeypatch.setattr(runner, "ACTIVE_AUTHORITY_POINTER", tmp_path / "active.json")
    _authority(tmp_path, [{"symbol": "000001.SZ", "list_date": "2010-01-01", "delist_date": None}])
    payload = json.loads(runner.AUTHORITY.read_text()); payload["FORMAL_SYMBOL_N"] = 2; runner.AUTHORITY.write_text(json.dumps(payload))
    with pytest.raises(runner.IncrementalError, match="AUTHORITY_N_MISMATCH"):
        runner.frozen_authority()
    payload["FORMAL_SYMBOL_N"] = 1; payload["FORMAL_IDENTITY_HASH"] = "0" * 64; runner.AUTHORITY.write_text(json.dumps(payload))
    with pytest.raises(runner.IncrementalError, match="AUTHORITY_HASH_MISMATCH"):
        runner.frozen_authority()
    payload["FORMAL_IDENTITY_HASH"] = runner.FORMAL_IDENTITY_HASH; payload["requests"][0]["symbol"] = "000001.BJ"; runner.AUTHORITY.write_text(json.dumps(payload))
    with pytest.raises(runner.IncrementalError, match="SYMBOL_INVALID"):
        runner.frozen_authority()


def _frame(symbols: list[str], day: str = "2026-09-09") -> pl.DataFrame:
    return pl.DataFrame({"symbol": symbols, "trade_date": [date.fromisoformat(day)] * len(symbols),
                         "open": [10.0] * len(symbols), "high": [11.0] * len(symbols), "low": [9.0] * len(symbols),
                         "close": [10.5] * len(symbols), "volume": [100] * len(symbols), "amount": [1000.0] * len(symbols),
                         "source": ["tdx_protocol"] * len(symbols), "data_version": ["v2"] * len(symbols)})


@pytest.mark.parametrize("mutator,code", [
    (lambda x: x.with_columns(pl.lit(date(2026, 9, 8)).alias("trade_date")), "DATE_MISMATCH"),
    (lambda x: x.with_columns(pl.lit("600000.SH").alias("symbol")), "SCOPE_VIOLATION"),
])
def test_primary_frame_scope_and_date_guards(mutator, code):
    with pytest.raises(runner.IncrementalError, match=code):
        runner._validate_frame(mutator(_frame(["000001.SZ"])), "2026-09-09", {"000001.SZ"})


def test_structural_quality_detects_all_hard_failures():
    frame = _frame(["000001.SZ", "000001.SZ"]).with_columns([
        pl.Series("open", [12.0, 10.0]), pl.Series("volume", [-1, 100]), pl.Series("amount", [-1.0, 1000.0]),
        pl.Series("source", ["wrong", "tdx_protocol"]), pl.Series("data_version", ["v1", "v2"]),
    ])
    result = runner.structural_quality(frame, "2026-09-09", {"000001.SZ"})
    assert result["duplicate"] == result["ohlc_invalid"] == result["negative_volume"] == result["negative_amount"] == 1
    assert result["provenance_failure"] == 1


def test_secondary_classification_is_strict(monkeypatch):
    class Result:
        fields = ["date", "code", "tradestatus"]
        def __init__(self, code="0", rows=None): self.error_code, self.error_msg, self.rows = code, "x", rows or []
        def next(self): return bool(self.rows)
        def get_row_data(self): return self.rows.pop(0)
    responses = iter([
        Result(rows=[["2026-09-09", "sz.000001", "0"]]), Result(rows=[["2026-09-09", "sz.000002", "1"]]),
        Result(code="1"), Result(rows=[]), Result(rows=[["2026-09-09", "sz.000005", "0"], ["2026-09-09", "sz.000005", "0"]]),
        Result(rows=[["2026-09-08", "sz.000006", "0"]]),
    ])
    fake = SimpleNamespace(login=lambda: SimpleNamespace(error_code="0"), logout=lambda: None,
        query_history_k_data_plus=lambda *_a, **_k: next(responses))
    monkeypatch.setitem(sys.modules, "baostock", fake)
    result = runner.classify_secondary([f"00000{i}.SZ" for i in range(1, 7)], "2026-09-09")
    assert [row["final_classification"] for row in result] == ["SUSPENDED", "BAR_PRESENT", "SOURCE_ERROR", "UNKNOWN", "UNKNOWN", "SOURCE_ERROR"]


def test_writer_lock_and_calendar_non_trading(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    calendar = tmp_path / "curated/trading_calendar/trade_date=2026"; calendar.mkdir(parents=True)
    pl.DataFrame({"trade_date": [date(2026, 9, 12)], "is_trading": [False]}).write_parquet(calendar / "part-merged.parquet")
    assert runner.calendar_state("2026-09-12") == "NON_TRADING_DAY"
    with runner.writer_lock():
        with pytest.raises(runner.IncrementalError, match="WRITER_LOCKED"):
            with runner.writer_lock():
                pass


def test_recovery_rejects_unknown_curated_orphan(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path); monkeypatch.setattr(runner, "STAGE", tmp_path / "staging/r3")
    (tmp_path / "curated/daily_bars/trade_date=2026-09-10").mkdir(parents=True)
    (tmp_path / "curated/daily_bars/trade_date=2026-09-10/part-merged.parquet").write_bytes(b"unknown")
    with pytest.raises(runner.IncrementalError, match="CURATED_WITHOUT_COMMITTED_EVIDENCE"):
        runner.recover_promotion("2026-09-10")


def test_recovery_rejects_uncommitted_candidate_without_overwrite(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path); monkeypatch.setattr(runner, "STAGE", tmp_path / "staging/r3")
    candidate = runner.STAGE / "trade_date=2026-09-10/part-merged.parquet"
    candidate.parent.mkdir(parents=True); candidate.write_bytes(b"unbound")
    with pytest.raises(runner.IncrementalError, match="CANDIDATE_WITHOUT_COMMITTED_EVIDENCE"):
        runner.recover_promotion("2026-09-10")


def _record(root: Path, path: Path) -> dict:
    return {"relative_path": path.relative_to(root).as_posix(), "file_size": path.stat().st_size,
            "sha256": runner.sha256_file(path)}


def test_recovery_completes_only_exact_evidenced_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path); monkeypatch.setattr(runner, "STAGE", tmp_path / "staging/r3")
    old_file = tmp_path / "curated/daily_bars/trade_date=2026-09-09/part-merged.parquet"
    old_file.parent.mkdir(parents=True); _frame(["000001.SZ"]).write_parquet(old_file)
    old_files = [_record(tmp_path, old_file)]; old_hash = runner.sha256_bytes(runner.canonical(old_files))
    old_stage = tmp_path / "meta/asl/r3/old"; old_stage.mkdir(parents=True)
    (old_stage / "plan.json").write_text(json.dumps({"EXPECTED_POST_INPUT_MANIFEST": {"FILES": old_files, "INPUT_MANIFEST_HASH": old_hash}}))
    (old_stage / "receipt.json").write_text(json.dumps({"STATE": "COMMITTED", "POST_INPUT_MANIFEST_HASH": old_hash}))
    pointer = tmp_path / "meta/asl/r3/published-daily-authority.json"; pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"schema": "R3_PUBLISHED_DAILY_AUTHORITY_V01", "manifest_hash": old_hash,
                                   "plan": "meta/asl/r3/old/plan.json", "receipt": "meta/asl/r3/old/receipt.json"}))
    stage = runner.STAGE / "trade_date=2026-09-10"; stage.mkdir(parents=True)
    candidate = stage / "part-merged.parquet"; _frame(["600000.SH"], "2026-09-10").write_parquet(candidate)
    destination = tmp_path / "curated/daily_bars/trade_date=2026-09-10/part-merged.parquet"
    files = sorted([*old_files, _record(tmp_path, candidate)], key=lambda row: row["relative_path"].replace("staging/r3/trade_date=2026-09-10", "curated/daily_bars/trade_date=2026-09-10"))
    files[-1] = {**files[-1], "relative_path": destination.relative_to(tmp_path).as_posix()}
    expected_hash = runner.sha256_bytes(runner.canonical(files))
    (stage / "promotion_plan.json").write_text(json.dumps({"EXPECTED_POST_INPUT_MANIFEST": {"FILES": files, "INPUT_MANIFEST_HASH": expected_hash}}))
    (stage / "promotion_receipt.json").write_text(json.dumps({"STATE": "COMMITTED", "OLD_INPUT_MANIFEST_HASH": old_hash, "POST_INPUT_MANIFEST_HASH": expected_hash}))
    outcome = runner.recover_promotion("2026-09-10")
    assert outcome["state"] == "ORPHAN_MATCHES_EXPECTED_CANDIDATE"
    assert destination.exists() and not candidate.exists()
    assert json.loads(pointer.read_text())["manifest_hash"] == expected_hash
    assert runner.recover_promotion("2026-09-10")["state"] == "ALREADY_PUBLISHED"


def test_active_authority_pointer_rejects_lifecycle_drift(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path); monkeypatch.setattr(runner, "AUTHORITY", tmp_path / "authority.json")
    monkeypatch.setattr(runner, "ACTIVE_AUTHORITY_POINTER", tmp_path / "active.json")
    _authority(tmp_path, _records())
    artifact = tmp_path / "authority-version.json"
    active = {"authority_version": "R3_SHSZ_AUTHORITY_V01", "symbol_n": 2,
              "identity_hash": runner.FORMAL_IDENTITY_HASH, "quality": {"PASS": True},
              "lifecycle": [{"symbol": "000001.SZ", "list_date": "2010-01-01", "delist_date": None},
                            {"symbol": "600000.SH", "list_date": "2025-01-01", "delist_date": None}]}
    artifact.write_text(json.dumps(active))
    runner.ACTIVE_AUTHORITY_POINTER.write_text(json.dumps({"schema": "ASL_ACTIVE_R3_SHSZ_AUTHORITY_V01",
        "authority": artifact.relative_to(tmp_path).as_posix(), "authority_version": active["authority_version"],
        "authority_hash": runner.sha256_bytes(runner.canonical(active))}))
    with pytest.raises(runner.IncrementalError, match="AUTHORITY_REFRESH_REQUIRED"):
        runner.frozen_authority()
