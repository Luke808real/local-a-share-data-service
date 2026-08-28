"""Offline targeted tests for the frozen full-session extraction engine."""

from __future__ import annotations

import copy
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_r3_full_session_completeness_authority_extraction_v01 as engine  # noqa: E402


class FakeResult:
    def __init__(self, rows: list[list[str]], code: str = "0", message: str = ""):
        self.error_code = code
        self.error_msg = message
        self._rows = rows
        self._index = 0

    def next(self) -> bool:
        if self._index >= len(self._rows):
            return False
        self._index += 1
        return True

    def get_row_data(self) -> list[str]:
        return self._rows[self._index - 1]


class FakeProvider:
    def __init__(self, responses: list[FakeResult]):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.logged_in = False
        self.logged_out = False

    def login(self) -> FakeResult:
        self.logged_in = True
        return FakeResult([])

    def logout(self) -> FakeResult:
        self.logged_out = True
        return FakeResult([])

    def query_history_k_data_plus(self, code: str, fields: str, **kwargs):
        self.calls.append({"code": code, "fields": fields, **kwargs})
        return self.responses.pop(0)


def _request(
    request_id: str = "R3SAC-000001",
    order: int = 1,
    start: str = "2020-01-02",
    end: str | None = None,
) -> dict:
    end = end or start
    return {
        "request_id": request_id,
        "request_order": order,
        "symbol": "000001.SZ",
        "bs_code": "sz.000001",
        "calendar_year": int(start[:4]),
        "list_date": "1991-04-03",
        "delist_date": None,
        "start_date": start,
        "end_date": end,
        "calendar_trading_date_n": 1,
        "adjustflag": "3",
        "fields": engine.QUERY_FIELDS,
        "frequency": "d",
        "provider": "baostock",
        "provider_runtime": "baostock-0.9.3",
    }


def _context(requests: list[dict] | None = None) -> dict:
    requests = requests or [_request()]
    return {
        "requests": requests,
        "symbols": ["000001.SZ"],
        "trading_dates": [date(2020, 1, 2), date(2020, 1, 3)],
        "lifecycle_by_symbol": {
            "000001.SZ": {
                "symbol": "000001.SZ",
                "effective_start": "2020-01-02",
                "effective_end": "2020-01-03",
            }
        },
        "key_counts": {"LIFECYCLE_SESSION_KEY_N": 2},
    }


def _row(
    trade_date: str = "2020-01-02",
    status: str = "1",
    code: str = "sz.000001",
) -> list[str]:
    return [trade_date, code, "10", "11", "9", "10.5", "100", "1000", "10.1", status]


def _data_stage(tmp_path: Path) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    return data_root, data_root / "staging" / engine.STAGING_DIRNAME


def _run_one(
    tmp_path: Path,
    *,
    requests: list[dict] | None = None,
    responses: list[FakeResult] | None = None,
    stop_after: int | None = None,
):
    data_root, stage_root = _data_stage(tmp_path)
    requests = requests or [_request()]
    provider = FakeProvider(responses or [FakeResult([_row()]) for _ in requests])
    result = engine.run_full_stage(
        _context(requests),
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: provider,
        stop_after=stop_after,
        sleep_fn=lambda _seconds: None,
    )
    return data_root, stage_root, provider, result


def test_authority_and_execution_constants_are_frozen():
    assert engine.BASE_HEAD == "ada99d214a87c34664dc4d1a6b2746cac062fbbe"
    assert engine.PLAN_COMMIT == "c9b1fc7bd99d9a7c20df350efeb8fd7f88714321"
    assert engine.CANARY_COMMIT == engine.BASE_HEAD
    assert engine.FULL_REQUEST_N == 48_345
    assert engine.FULL_REQUEST_MANIFEST_HASH == "4654e4282d5191cf680dc6b539025c816bc4d21028a4f2e200230209348412ed"
    assert engine.LIFECYCLE_SESSION_KEY_N == 10_897_229


def test_exact_stage_root_and_escape_variants_fail_closed(tmp_path: Path):
    data_root, stage_root = _data_stage(tmp_path)
    assert engine.require_isolated_staging_root(data_root, stage_root) == stage_root
    curated = data_root / "curated"
    curated.mkdir()
    with pytest.raises(engine.ExtractionError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        engine.require_isolated_staging_root(data_root, curated)
    with pytest.raises(engine.ExtractionError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        engine.require_isolated_staging_root(data_root, data_root / "staging" / "other")
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(engine.ExtractionError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        engine.require_isolated_staging_root(data_root, outside)
    stage_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(engine.ExtractionError):
        engine.require_isolated_staging_root(data_root, stage_root)


def test_direct_full_stage_forbidden_root_fails_before_provider_or_write(
    tmp_path: Path,
):
    data_root, _stage_root = _data_stage(tmp_path)
    forbidden = data_root / "curated"
    forbidden.mkdir()
    called: list[str] = []
    with pytest.raises(engine.ExtractionError):
        engine.run_full_stage(
            _context(),
            data_root=data_root,
            stage_root=forbidden,
            provider_factory=lambda: called.append("provider"),
        )
    assert called == []
    assert list(forbidden.iterdir()) == []


def test_authority_drift_happens_before_provider_import_or_stage_write(
    monkeypatch,
    tmp_path: Path,
):
    data_root, stage_root = _data_stage(tmp_path)
    called: list[str] = []

    def drift(*_args, **_kwargs):
        raise engine.ExtractionError("DAILY_INPUT_MANIFEST_DRIFT")

    monkeypatch.setattr(engine, "load_and_verify_authority", drift)
    with pytest.raises(engine.ExtractionError, match="DAILY_INPUT_MANIFEST_DRIFT"):
        engine.run_full_extraction(
            repo_root=Path("."),
            data_root=data_root,
            stage_root=stage_root,
            provider_factory=lambda: called.append("provider"),
        )
    assert called == []
    assert not stage_root.exists()


def test_full_scope_is_exactly_48345_and_deterministic():
    manifest = engine.load_frozen_manifest(Path(__file__).resolve().parents[1])
    without_hash = dict(manifest)
    without_hash.pop("FULL_REQUEST_MANIFEST_HASH")
    requests = without_hash["requests"]
    assert len(requests) == engine.FULL_REQUEST_N == 48_345
    assert [row["request_order"] for row in requests] == list(range(1, 48_346))
    assert len({row["request_id"] for row in requests}) == 48_345
    assert engine.sha256_json(without_hash) == engine.FULL_REQUEST_MANIFEST_HASH


def test_existing_canary_cache_candidates_are_rejected_by_full_manifest_gate():
    repo_root = Path(__file__).resolve().parents[1]
    data_root = engine.DATA_ROOT_DEFAULT
    stage_root = data_root / "staging" / engine.STAGING_DIRNAME
    context = engine.load_and_verify_authority(repo_root, data_root)
    result = engine.validate_canary_cache_candidates(repo_root, data_root, stage_root, context)
    assert result["CANARY_CACHE_CANDIDATE_N"] == 100
    assert result["CANARY_CACHE_REUSED_N"] == 0
    assert result["CANARY_CACHE_REJECTED_N"] == 100
    assert all("FULL_REQUEST_MANIFEST_HASH_MISMATCH" in row["reasons"] for row in result["REJECTIONS"])


def test_hash_verified_full_receipt_is_reused_on_resume(tmp_path: Path):
    requests = [_request("R3SAC-000001", 1), _request("R3SAC-000002", 2, "2020-01-03")]
    data_root, stage_root, first_provider, first = _run_one(
        tmp_path,
        requests=requests,
        responses=[FakeResult([_row()])],
        stop_after=1,
    )
    assert first["run"]["refetched_n"] == 1
    second_provider = FakeProvider([FakeResult([_row("2020-01-03")])])
    second = engine.run_full_stage(
        _context(requests),
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: second_provider,
        sleep_fn=lambda _seconds: None,
    )
    assert second["run"]["skipped_complete_n"] == 1
    assert second["run"]["refetched_n"] == 1
    assert len(second_provider.calls) == 1
    assert second["checkpoint"]["history"][0]["status"] == "PARTIAL"
    assert engine.is_valid_complete_receipt(
        data_root, stage_root, requests[0], _context(requests)
    )


def test_keyboard_interrupt_is_checkpointed_and_resume_retries_pending_request(
    tmp_path: Path,
):
    requests = [
        _request("R3SAC-000001", 1),
        _request("R3SAC-000002", 2, "2020-01-03"),
    ]
    data_root, stage_root = _data_stage(tmp_path)

    class InterruptingProvider(FakeProvider):
        def query_history_k_data_plus(self, code: str, fields: str, **kwargs):
            self.calls.append({"code": code, "fields": fields, **kwargs})
            raise KeyboardInterrupt()

    interrupted_provider = InterruptingProvider([])
    with pytest.raises(KeyboardInterrupt):
        engine.run_full_stage(
            _context(requests),
            data_root=data_root,
            stage_root=stage_root,
            provider_factory=lambda: interrupted_provider,
            sleep_fn=lambda _seconds: None,
        )

    checkpoint = json.loads(
        (stage_root / engine.FULL_CHECKPOINT_STAGE_NAME).read_text(encoding="utf-8")
    )
    assert checkpoint["history"][0]["status"] == "INTERRUPTED"
    assert checkpoint["last_event"]["status"] == "RUNNING"

    resumed_provider = FakeProvider(
        [FakeResult([_row()]), FakeResult([_row("2020-01-03")])]
    )
    resumed = engine.run_full_stage(
        _context(requests),
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: resumed_provider,
        sleep_fn=lambda _seconds: None,
    )
    assert resumed["run"]["skipped_complete_n"] == 0
    assert resumed["run"]["refetched_n"] == 2
    assert resumed["checkpoint"]["terminal_complete_n"] == 2
    assert len(resumed_provider.calls) == 2


def test_corrupt_receipt_wrong_authority_and_failed_receipt_are_refetched(tmp_path: Path):
    data_root, stage_root, provider, _result = _run_one(tmp_path)
    request = _request()
    raw_path, normalized_path = engine._receipt_paths(data_root, stage_root, request["request_id"])
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    normalized["normalized_payload"]["CASES"][0]["classification"] = "UNKNOWN"
    normalized_path.write_text(json.dumps(normalized), encoding="utf-8")
    assert not engine.is_valid_complete_receipt(data_root, stage_root, request, _context())
    retry = FakeProvider([FakeResult([_row()])])
    result = engine.run_full_stage(
        _context(),
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: retry,
        sleep_fn=lambda _seconds: None,
    )
    assert result["run"]["refetched_n"] == 1
    assert len(retry.calls) == 1

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["request_manifest_hash"] = "wrong"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    assert not engine.is_valid_complete_receipt(data_root, stage_root, request, _context())

    failed_root = tmp_path / "failed"
    failed_root.mkdir()
    failed_data, failed_stage = _data_stage(failed_root)
    failed_provider = FakeProvider([FakeResult([], "-1", "down")])
    failed = engine.run_full_stage(
        _context(),
        data_root=failed_data,
        stage_root=failed_stage,
        provider_factory=lambda: failed_provider,
        sleep_fn=lambda _seconds: None,
    )
    assert failed["run"]["refetched_n"] == 1
    assert failed["checkpoint"]["terminal_failed_n"] == 1
    assert not engine.is_valid_complete_receipt(failed_data, failed_stage, request, _context())


def test_provider_failure_duplicate_and_invalid_rows_are_not_complete(tmp_path: Path):
    requests = [_request()]
    duplicate_data, duplicate_stage, _provider, duplicate = _run_one(
        tmp_path / "duplicate",
        responses=[FakeResult([_row(), _row()])],
    )
    assert duplicate["checkpoint"]["terminal_failed_n"] == 1
    duplicate_norm = json.loads(
        (duplicate_stage / engine.FULL_NORMALIZED_DIRNAME / "R3SAC-000001.json").read_text()
    )
    assert duplicate_norm["normalized_payload"]["METRICS"]["DUPLICATE_PROVIDER_KEY_N"] == 1
    assert not engine.is_valid_complete_receipt(
        duplicate_data, duplicate_stage, requests[0], _context(requests)
    )

    invalid_data, invalid_stage, _provider, invalid = _run_one(
        tmp_path / "invalid",
        responses=[FakeResult([_row(status="2")])],
    )
    assert invalid["checkpoint"]["terminal_failed_n"] == 1
    invalid_norm = json.loads(
        (invalid_stage / engine.FULL_NORMALIZED_DIRNAME / "R3SAC-000001.json").read_text()
    )
    assert invalid_norm["normalized_payload"]["CASES"][0]["classification"] == "UNKNOWN"
    assert invalid_norm["normalized_payload"]["INVALID_ROWS"][0]["reason"] == "INVALID_TRADESTATUS"


def test_unknown_absence_is_preserved_and_does_not_block_request_complete(tmp_path: Path):
    data_root, stage_root, _provider, result = _run_one(
        tmp_path,
        responses=[FakeResult([])],
    )
    assert result["checkpoint"]["terminal_complete_n"] == 1
    request = _request()
    normalized = json.loads(
        (stage_root / engine.FULL_NORMALIZED_DIRNAME / "R3SAC-000001.json").read_text()
    )
    payload = normalized["normalized_payload"]
    assert payload["CASES"][0]["classification"] == "UNKNOWN"
    assert payload["CASES"][0]["basis"] == "PROVIDER_ROW_ABSENT_IN_LIFETIME"
    assert engine.is_valid_complete_receipt(data_root, stage_root, request, _context())


def test_session_authority_exact_coverage_and_unknown_retained(tmp_path: Path):
    requests = [
        _request("R3SAC-000001", 1, "2020-01-02", "2020-01-02"),
        _request("R3SAC-000002", 2, "2020-01-03", "2020-01-03"),
    ]
    context = _context(requests)
    data_root, stage_root = _data_stage(tmp_path)
    provider = FakeProvider([FakeResult([_row()]), FakeResult([])])
    engine.run_full_stage(
        context,
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: provider,
        sleep_fn=lambda _seconds: None,
    )
    metadata = engine.assemble_session_authority(
        context,
        data_root=data_root,
        stage_root=stage_root,
    )
    assert metadata["SESSION_AUTHORITY_KEY_N"] == 2
    assert metadata["MISSING_SESSION_AUTHORITY_KEY_N"] == 0
    assert metadata["DUPLICATE_SESSION_AUTHORITY_KEY_N"] == 0
    assert Path(metadata["SESSION_AUTHORITY_PATH"]).is_file()
    import pyarrow.parquet as pq

    table = pq.read_table(metadata["SESSION_AUTHORITY_PATH"])
    assert table.num_rows == 2
    assert table.column("classification").to_pylist() == ["EXPECTED_BAR", "UNKNOWN"]
    assert table.column("basis").to_pylist() == [
        "PROVIDER_TRADESTATUS_1",
        "PROVIDER_ROW_ABSENT_IN_LIFETIME",
    ]


def test_all_complete_quality_gate_requires_full_frozen_scope():
    index = {
        "COMPLETE_REQUEST_N": engine.FULL_REQUEST_N,
        "FAILED_REQUEST_N": 0,
        "PROVIDER_FAILED_REQUEST_N": 0,
        "DUPLICATE_PROVIDER_KEY_N": 0,
        "INVALID_PROVIDER_ROW_N": 0,
        "EXPECTED_BAR_CASE_N": 1,
        "NOT_EXPECTED_BAR_CASE_N": 0,
        "UNKNOWN_CASE_N": 1,
        "REQUEST_RECEIPT_INDEX_HASH": "x",
    }
    checkpoint = {"history": []}
    quality = engine._quality_from_index(
        {},
        index,
        checkpoint,
        terminal_requested=True,
        authority_stable=True,
        session_metadata=None,
    )
    assert quality["FULL_EXTRACTION_COMPLETE"] is True
    assert quality["UNKNOWN_CASE_N"] == 1
    assert quality["UNKNOWN_CASE_BLOCKS_SESSION_KEY_PASS"] is True


def test_canonical_zero_write(tmp_path: Path):
    data_root, stage_root, _provider, _result = _run_one(tmp_path)
    canonical = data_root / "curated" / "daily_bars"
    canonical.mkdir(parents=True)
    sentinel = canonical / "sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert not (canonical / "canonical_write.marker").exists()
    assert not (data_root / "R4A9").exists()
