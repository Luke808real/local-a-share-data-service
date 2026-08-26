"""Targeted tests for the bounded 300546 missing-day repair state machine."""

from __future__ import annotations

import inspect
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_r3_300546_missing_days_repair_v01 as repair  # noqa: E402


def _source_frame(trade_date: str, symbol: str = "000001.SZ") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol, "000002.SZ"],
            "trade_date": [date.fromisoformat(trade_date)] * 2,
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.05, 2.05],
            "volume": [1000, 2000],
            "amount": [100.0, 200.0],
            "source": ["tdx_protocol", "tdx_protocol"],
            "data_version": ["v2", "v2"],
            "fetched_at": [datetime(2026, 1, 1, tzinfo=timezone.utc)] * 2,
        }
    ).with_columns(
        pl.col("trade_date").cast(pl.Date),
        pl.col("fetched_at").cast(pl.Datetime("us", time_zone="UTC")),
    )


def _fact(trade_date: str, volume: int = 100) -> dict:
    return {
        "symbol": "300546.SZ",
        "trade_date": trade_date,
        "open": 32.36 if trade_date == "2016-09-29" else 39.16,
        "high": 32.36 if trade_date == "2016-09-29" else 39.16,
        "low": 32.36 if trade_date == "2016-09-29" else 39.16,
        "close": 32.36 if trade_date == "2016-09-29" else 39.16,
        "volume": volume,
        "amount": 3236.0 if trade_date == "2016-09-29" else 3916.0,
        "preclose": 29.42 if trade_date == "2016-09-29" else 35.60,
        "tradestatus": 1,
        "source": "baostock",
        "data_version": "v2",
        "fetched_at": "2026-08-26T16:43:04.482135Z",
    }


def _authority() -> dict:
    return {
        "INPUT_FILE_N": 2580,
        "INPUT_MANIFEST_HASH": repair.CURRENT_INPUT_MANIFEST_HASH,
        "TARGET_FACTS": [_fact("2016-09-29"), _fact("2016-10-10")],
        "AUTHORITY_PAYLOAD_SHA256": "unused-in-unit-fixture",
    }


class _FakeResult:
    error_code = "0"
    error_msg = "success"

    def __init__(self, rows: list[list[str]]):
        self._rows = rows
        self._index = 0

    def next(self) -> bool:
        if self._index >= len(self._rows):
            return False
        self._index += 1
        return True

    def get_row_data(self) -> list[str]:
        return self._rows[self._index - 1]


class _FakeBaoStock:
    __version__ = "00.9.30"

    def __init__(self, rows: list[list[str]]):
        self.rows = rows
        self.calls: list[tuple] = []
        self.logged_out = False

    def login(self):
        return _FakeResult([])

    def logout(self):
        self.logged_out = True
        return _FakeResult([])

    def query_history_k_data_plus(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeResult(self.rows)


def _raw_rows() -> list[list[str]]:
    return [
        ["2016-09-29", "sz.300546", "32.3600", "32.3600", "32.3600", "32.3600", "100", "3236.0000", "29.4200", "1"],
        ["2016-09-30", "sz.300546", "35.6000", "35.6000", "35.6000", "35.6000", "1600", "56960.0000", "32.3600", "1"],
        ["2016-10-10", "sz.300546", "39.1600", "39.1600", "39.1600", "39.1600", "100", "3916.0000", "35.6000", "1"],
    ]


def test_exact_isolated_stage_root_and_forbidden_paths(tmp_path: Path):
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    allowed = data_root / "staging" / repair.STAGE_ROOT_NAME
    assert repair.require_isolated_stage_root(data_root, allowed) == allowed
    forbidden = [
        data_root / "curated" / "daily_bars",
        data_root / "staging" / "other",
        tmp_path / "outside",
    ]
    for path in forbidden:
        with pytest.raises(repair.RepairError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
            repair.require_isolated_stage_root(data_root, path)


def test_symlink_escape_and_direct_run_forbidden_root_fail_before_write(tmp_path: Path):
    data_root = tmp_path / "data"
    staging = data_root / "staging"
    staging.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = staging / repair.STAGE_ROOT_NAME
    escaped.symlink_to(outside, target_is_directory=True)
    with pytest.raises(repair.RepairError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        repair.require_isolated_stage_root(data_root, escaped)

    before = sorted(path.relative_to(data_root) for path in data_root.rglob("*"))
    with pytest.raises(repair.RepairError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        repair.run_candidate(
            repo_root=tmp_path,
            data_root=data_root,
            stage_root=data_root / "curated" / "daily_bars",
        )
    assert sorted(path.relative_to(data_root) for path in data_root.rglob("*")) == before


def test_baostock_authority_is_one_bounded_query_and_persists_all_facts(monkeypatch, tmp_path: Path):
    data_root = tmp_path / "data"
    canonical = data_root / "curated" / "daily_bars" / "trade_date=2016-09-30" / "part.parquet"
    canonical.parent.mkdir(parents=True)
    _source_frame("2016-09-30").write_parquet(canonical)
    input_manifest = {
        "INPUT_FILE_N": 2580,
        "INPUT_MANIFEST_HASH": repair.CURRENT_INPUT_MANIFEST_HASH,
        "FILES": [],
    }
    fake = _FakeBaoStock(_raw_rows())
    monkeypatch.setattr(repair, "verify_existing_crosscheck_20160930", lambda *args: None)
    payload = repair.fetch_baostock_authority(
        data_root=data_root,
        input_manifest=input_manifest,
        provider=fake,
        now=lambda: datetime(2026, 8, 26, 16, 43, 4, 482135, tzinfo=timezone.utc),
    )
    assert len(fake.calls) == 1
    assert fake.logged_out is True
    assert fake.calls[0][0][0] == "sz.300546"
    assert fake.calls[0][1]["start_date"] == "2016-09-29"
    assert fake.calls[0][1]["end_date"] == "2016-10-10"
    assert [row["trade_date"] for row in payload["TARGET_FACTS"]] == ["2016-09-29", "2016-10-10"]
    assert all(row["tradestatus"] == 1 for row in payload["TARGET_FACTS"])
    assert payload["RESPONSE_ROW_N"] == 3
    assert repair.verify_authority_receipt(payload, input_manifest)["TARGET_KEY_N"] == 2


def test_authority_receipt_hash_mismatch_fails_closed():
    payload = {
        "INPUT_FILE_N": repair.CURRENT_INPUT_FILE_N,
        "INPUT_MANIFEST_HASH": repair.CURRENT_INPUT_MANIFEST_HASH,
        "TARGET_FACTS": [_fact("2016-09-29"), _fact("2016-10-10")],
        "AUTHORITY_PAYLOAD_SHA256": "wrong",
    }
    with pytest.raises(repair.RepairError, match="AUTHORITY_RECEIPT_HASH_MISMATCH"):
        repair.verify_authority_receipt(payload, payload)


def test_exact_insert_diff_rejects_missing_duplicate_and_existing_mutation():
    source = _source_frame("2016-09-29")
    expected = repair.expected_authority_row(_fact("2016-09-29"), source.schema)
    candidate = pl.concat([source, pl.DataFrame([expected], schema=source.schema)], how="vertical")
    result = repair.exact_insert_diff(
        source,
        candidate,
        {("300546.SZ", "2016-09-29"): expected},
        "test",
    )
    assert result == {
        "INSERTED_ROW_N": 1,
        "DELETED_ROW_N": 0,
        "MODIFIED_EXISTING_ROW_N": 0,
        "INSERTED_KEYS": ["300546.SZ:2016-09-29"],
    }
    with pytest.raises(repair.RepairError, match="INSERTED_KEY_SET_MISMATCH"):
        repair.exact_insert_diff(source, source, {("300546.SZ", "2016-09-29"): expected}, "missing")
    with pytest.raises(repair.RepairError, match="DUPLICATE_KEY"):
        repair.exact_insert_diff(
            source,
            pl.concat([source, source.head(1)], how="vertical"),
            {},
            "duplicate",
        )
    mutated = source.with_columns(
        pl.when(pl.col("symbol") == "000001.SZ").then(999).otherwise(pl.col("volume")).alias("volume")
    )
    with pytest.raises(repair.RepairError, match="MODIFIED_EXISTING_ROW"):
        repair.exact_insert_diff(source, mutated, {}, "mutated")


def test_candidate_run_writes_only_two_insertions_to_isolated_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_root = tmp_path / "data"
    staging = data_root / "staging"
    staging.mkdir(parents=True)
    for trade_date in repair.TARGET_DATES:
        relative = f"curated/daily_bars/trade_date={trade_date}/part.parquet"
        path = data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _source_frame(trade_date).write_parquet(path)
    input_manifest = repair.build_input_file_manifest(data_root)
    authority = {
        "TARGET_FACTS": [_fact("2016-09-29"), _fact("2016-10-10")],
        "INPUT_FILE_N": 2580,
        "INPUT_MANIFEST_HASH": repair.CURRENT_INPUT_MANIFEST_HASH,
    }
    monkeypatch.setattr(repair, "require_current_head", lambda _root: None)
    monkeypatch.setattr(repair, "require_current_input", lambda _data, _repo: input_manifest)
    monkeypatch.setattr(repair, "require_target_keys_absent", lambda _data, _manifest: {"DUPLICATE_KEY_N": 0})
    monkeypatch.setattr(repair, "verify_prior_volume_repair", lambda **_kwargs: {"PRIOR_VOLUME_REPAIR_KEY_N": 1169, "PRIOR_VOLUME_REPAIR_MISMATCH_N": 0})
    monkeypatch.setattr(repair, "load_authority_for_stage", lambda **_kwargs: authority)
    before = {path.relative_to(data_root): path.read_bytes() for path in data_root.rglob("*.parquet")}
    stage_root = staging / repair.STAGE_ROOT_NAME
    result = repair.run_candidate(repo_root=tmp_path, data_root=data_root, stage_root=stage_root)
    after_canonical = {
        path.relative_to(data_root): path.read_bytes()
        for path in data_root.rglob("*.parquet")
        if not path.is_relative_to(stage_root)
    }
    assert after_canonical == before
    assert result["CANDIDATE"]["INSERTED_ROW_N"] == 2
    assert result["CANDIDATE"]["DELETED_ROW_N"] == 0
    assert result["CANDIDATE"]["MODIFIED_EXISTING_ROW_N"] == 0
    for trade_date in repair.TARGET_DATES:
        candidate_path = stage_root / repair.CANDIDATE_DIR_NAME / "curated/daily_bars" / f"trade_date={trade_date}" / "part.parquet"
        output = pl.read_parquet(candidate_path)
        assert output.filter(
            (pl.col("symbol") == repair.TARGET_SYMBOL)
            & (pl.col("trade_date") == date.fromisoformat(trade_date))
        ).height == 1


def _promotion_fixture(tmp_path: Path):
    data_root = tmp_path / "data"
    stage_root = data_root / "staging" / repair.STAGE_ROOT_NAME
    candidate_dir = stage_root / repair.CANDIDATE_DIR_NAME
    stage_root.mkdir(parents=True)
    candidate_dir.mkdir()
    records = []
    files = []
    for trade_date in repair.TARGET_DATES:
        relative = f"curated/daily_bars/trade_date={trade_date}/part.parquet"
        source_path = data_root / relative
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source = _source_frame(trade_date)
        source.write_parquet(source_path)
        candidate_path = candidate_dir / relative
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate = repair.build_candidate_frame(source, [_fact(trade_date)])
        candidate.write_parquet(candidate_path)
        files.append({"relative_path": relative, "file_size": source_path.stat().st_size, "sha256": repair.sha256_file(source_path)})
    input_manifest = repair.build_input_file_manifest(data_root)
    records, _ = repair._candidate_file_records(
        data_root=data_root,
        candidate_dir=candidate_dir,
        input_manifest=input_manifest,
        authority={"TARGET_FACTS": [_fact("2016-09-29"), _fact("2016-10-10")]},
        read_and_build=False,
    )
    transaction_root = data_root / "staging" / repair.PROMOTION_ROOT_NAME
    transaction_root.mkdir(parents=True)
    plan = repair.build_promotion_records(
        data_root=data_root,
        candidate_dir=candidate_dir,
        transaction_root=transaction_root,
        pre=input_manifest,
        candidate_records=records,
    )
    repair.prepare_backups(transaction_root, plan)
    return data_root, candidate_dir, transaction_root, input_manifest, plan


def test_mid_promotion_failure_rolls_back_all_replaced_files(tmp_path: Path):
    data_root, candidate_dir, transaction_root, pre, plan = _promotion_fixture(tmp_path)
    promoted: list[str] = []

    def fail_after_first(index: int, _record: dict):
        if index == 0:
            raise repair.RepairError("INJECTED_FAILURE")

    with pytest.raises(repair.RepairError, match="INJECTED_FAILURE"):
        repair.promote_files(
            data_root=data_root,
            candidate_dir=candidate_dir,
            transaction_root=transaction_root,
            records=plan,
            promoted=promoted,
            fault_injector=fail_after_first,
        )
    assert len(promoted) == 1
    repair.rollback_files(
        data_root=data_root,
        transaction_root=transaction_root,
        records=plan,
        promoted=promoted,
    )
    restored = repair.build_input_file_manifest(data_root)
    repair.require_manifest_equal(pre, restored, "ROLLBACK_PRE_INPUT")
    assert repair.load_json(transaction_root / repair.TRANSACTION_STATE_NAME)["TRANSACTION_STATE"] == "ROLLED_BACK"


def test_promotion_input_gate_fails_before_transaction_root_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    stage_root = data_root / "staging" / repair.STAGE_ROOT_NAME
    promotion_root = data_root / "staging" / repair.PROMOTION_ROOT_NAME
    monkeypatch.setattr(repair, "require_current_head", lambda _root: None)
    monkeypatch.setattr(
        repair,
        "require_current_input",
        lambda _data, _repo: (_ for _ in ()).throw(repair.RepairError("INPUT_MANIFEST_DRIFT")),
    )
    with pytest.raises(repair.RepairError, match="INPUT_MANIFEST_DRIFT"):
        repair.run_promotion(
            repo_root=tmp_path,
            data_root=data_root,
            stage_root=stage_root,
            promotion_root=promotion_root,
        )
    assert not promotion_root.exists()


def test_candidate_and_promotion_phases_contain_no_tdx_provider_import():
    source = inspect.getsource(repair)
    assert "TdxWireClient" not in source
    assert "cnequity.adapters.tdx" not in source
    assert "--fetch-baostock" in source
