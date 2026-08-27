"""Targeted offline tests for the R3 session-authority extraction canary."""

from __future__ import annotations

import copy
import inspect
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_r3_full_session_completeness_authority_extraction_canary_v01 as engine  # noqa: E402


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
    def __init__(self, responses: list[FakeResult] | None = None):
        self.responses = list(responses or [])
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
        if not self.responses:
            return FakeResult([])
        return self.responses.pop(0)


def _request(request_id: str = "R3SAC-000001", order: int = 1, start: str = "2020-01-01") -> dict:
    return {
        "request_id": request_id,
        "request_order": order,
        "symbol": "000001.SZ",
        "bs_code": "sz.000001",
        "calendar_year": int(start[:4]),
        "list_date": "1991-04-03",
        "delist_date": None,
        "start_date": start,
        "end_date": "2020-01-03",
        "calendar_trading_date_n": 2,
        "adjustflag": "3",
        "fields": engine.QUERY_FIELDS,
        "frequency": "d",
        "provider": "baostock",
        "provider_runtime": "baostock-0.9.3",
    }


def _context() -> dict:
    return {
        "trading_dates": [date(2020, 1, 2), date(2020, 1, 3)],
        "lifecycle_by_symbol": {
            "000001.SZ": {
                "symbol": "000001.SZ",
                "effective_start": "2016-01-01",
                "effective_end": "2026-08-17",
            }
        },
    }


def _row(trade_date: str = "2020-01-02", status: str = "1", code: str = "sz.000001") -> list[str]:
    return [trade_date, code, "10", "11", "9", "10.5", "100", "1000", "10.1", status]


def _manifest(requests: list[dict]) -> dict:
    return {
        "TASK": engine.TASK_NAME,
        "CANARY_REQUEST_MANIFEST_HASH": "canary-hash",
        "REQUESTS": requests,
    }


def _data_stage(tmp_path: Path) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    return data_root, data_root / "staging" / engine.STAGING_DIRNAME


def test_authority_constants_and_canary_scope_are_frozen():
    assert engine.BASE_HEAD == "c9b1fc7bd99d9a7c20df350efeb8fd7f88714321"
    assert engine.PLAN_COMMIT == engine.BASE_HEAD
    assert engine.FULL_REQUEST_N == 48345
    assert engine.CANARY_REQUEST_N == 100
    assert engine.DAILY_INPUT_MANIFEST_HASH == "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
    assert engine.FORMAL_IDENTITY_HASH == "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"


def test_canary_selector_is_deterministic_and_full_manifest_only():
    implementation = Path(__file__).resolve().parents[1] / "reports" / "implementation"
    full = json.loads(
        (implementation / engine.PLAN_REQUEST_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    lifecycle = json.loads(
        (implementation / engine.PLAN_LIFECYCLE_NAME).read_text(encoding="utf-8")
    )
    prior = json.loads(
        (Path(__file__).resolve().parents[1] / "reports" / "research" / engine.FEASIBILITY_REQUEST_MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    pairs = {
        (row["symbol"], int(row["start_date"][:4]))
        for row in prior["requests"]
        if row.get("purpose") == "LONG_SUSPENSION_INTERIOR"
    }
    first = engine.select_canary_requests(full["requests"], lifecycle["ROWS"], pairs)
    second = engine.select_canary_requests(full["requests"], lifecycle["ROWS"], pairs)
    assert first == second
    assert len(first) == engine.CANARY_REQUEST_N == 100
    assert len({row["request_id"] for row in first}) == 100
    assert {row["request_id"] for row in first}.issubset(
        {row["request_id"] for row in full["requests"]}
    )
    categories = {category for row in first for category in row["canary_categories"]}
    assert {
        "ACTIVE_SH",
        "ACTIVE_SZ",
        "DELISTED_SH",
        "DELISTED_SZ",
        "RECENT_LISTING_SH",
        "RECENT_LISTING_SZ",
        "LONG_SUSPENSION_RELEVANT",
        "EARLY_REQUEST_ORDER",
        "MIDDLE_REQUEST_ORDER",
        "LATE_REQUEST_ORDER",
    }.issubset(categories)
    assert {row["canary_exchange_scope"] for row in first} == {"SH", "SZ"}
    assert "daily_bars" not in inspect.getsource(engine.select_canary_requests)


def test_canary_manifest_hash_is_deterministic_and_binds_all_authorities():
    implementation = Path(__file__).resolve().parents[1] / "reports" / "implementation"
    full = json.loads(
        (implementation / engine.PLAN_REQUEST_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    lifecycle = json.loads(
        (implementation / engine.PLAN_LIFECYCLE_NAME).read_text(encoding="utf-8")
    )
    prior_path = Path(__file__).resolve().parents[1] / "reports" / "research" / engine.FEASIBILITY_REQUEST_MANIFEST_NAME
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    pairs = {
        (row["symbol"], int(row["start_date"][:4]))
        for row in prior["requests"]
        if row.get("purpose") == "LONG_SUSPENSION_INTERIOR"
    }
    context = {"requests": full["requests"]}
    selected = engine.select_canary_requests(full["requests"], lifecycle["ROWS"], pairs)
    first, first_hash = engine.build_canary_request_manifest(context, selected, engine.sha256_file(prior_path))
    second, second_hash = engine.build_canary_request_manifest(context, selected, engine.sha256_file(prior_path))
    assert first == second
    assert first_hash == second_hash
    assert first["FULL_REQUEST_MANIFEST_HASH"] == engine.FULL_REQUEST_MANIFEST_HASH
    assert first["LIFECYCLE_SESSION_KEYSET_HASH"] == engine.LIFECYCLE_SESSION_KEYSET_HASH
    assert first["CANARY_REQUEST_N"] == 100


def test_exact_allowed_stage_root_and_all_escape_variants(tmp_path: Path):
    data_root, stage_root = _data_stage(tmp_path)
    assert engine.require_isolated_staging_root(data_root, stage_root) == stage_root
    with pytest.raises(engine.CanaryError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        engine.require_isolated_staging_root(data_root, data_root / "curated")
    with pytest.raises(engine.CanaryError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        engine.require_isolated_staging_root(data_root, data_root / "staging" / "other")
    with pytest.raises(engine.CanaryError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        engine.require_isolated_staging_root(data_root, tmp_path / "outside")
    outside = tmp_path / "outside-real"
    outside.mkdir()
    stage_root.parent.mkdir(exist_ok=True)
    stage_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(engine.CanaryError, match="ISOLATED_STAGE_ROOT_FORBIDDEN"):
        engine.require_isolated_staging_root(data_root, stage_root)


def test_direct_run_canary_forbidden_root_fails_before_preflight_or_write(monkeypatch, tmp_path: Path):
    data_root, _ = _data_stage(tmp_path)
    forbidden = data_root / "curated"
    forbidden.mkdir()
    called = []
    monkeypatch.setattr(engine, "_load_and_verify_authority", lambda *_: called.append(True))
    with pytest.raises(engine.CanaryError):
        engine.run_canary(data_root, stage_root=forbidden, provider_factory=lambda: called.append(True))
    assert called == []
    assert list(forbidden.iterdir()) == []


def test_authority_drift_fails_with_zero_provider_calls_and_no_stage_write(monkeypatch, tmp_path: Path):
    data_root, stage_root = _data_stage(tmp_path)
    called = []

    def drift(*_args, **_kwargs):
        raise engine.CanaryError("DAILY_INPUT_MANIFEST_DRIFT")

    monkeypatch.setattr(engine, "_load_and_verify_authority", drift)
    with pytest.raises(engine.CanaryError, match="DAILY_INPUT_MANIFEST_DRIFT"):
        engine.run_canary(
            data_root,
            stage_root=stage_root,
            provider_factory=lambda: called.append(True),
        )
    assert called == []
    assert not stage_root.exists()


def test_provider_request_uses_exact_frozen_parameters_and_bounded_retry():
    request = _request()
    provider = FakeProvider([FakeResult([_row()])])
    result = engine.fetch_one_request(request, provider, sleep_fn=lambda _seconds: None)
    assert result["provider_error_code"] == "0"
    assert result["attempt_n"] == 1
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call == {
        "code": "sz.000001",
        "fields": engine.QUERY_FIELDS,
        "start_date": "2020-01-01",
        "end_date": "2020-01-03",
        "frequency": "d",
        "adjustflag": "3",
    }

    failed_provider = FakeProvider([FakeResult([], "-1", "down") for _ in range(3)])
    failed = engine.fetch_one_request(request, failed_provider, sleep_fn=lambda _seconds: None)
    assert failed["provider_error_code"] == "-1"
    assert failed["attempt_n"] == 3
    assert failed["provider_query_attempt_n"] == 3
    assert len(failed_provider.calls) == 3


def test_duplicate_provider_date_blocks_clean_completion():
    normalized, metrics = engine.normalize_provider_rows(
        _request(),
        [_row(), _row()],
        provider_error_code="0",
        provider_error_message="",
        context=_context(),
    )
    assert metrics["DUPLICATE_PROVIDER_KEY_N"] == 1
    assert metrics["INVALID_PROVIDER_ROW_N"] == 0
    assert normalized["CASES"][0]["classification"] == "UNKNOWN"
    assert normalized["CASES"][0]["basis"] == "DUPLICATE_PROVIDER_ROW"


def test_invalid_tradestatus_is_unknown_with_durable_invalid_reason():
    normalized, metrics = engine.normalize_provider_rows(
        _request(),
        [_row(status="2")],
        provider_error_code="0",
        provider_error_message="",
        context=_context(),
    )
    assert metrics["INVALID_PROVIDER_ROW_N"] == 1
    assert normalized["INVALID_ROWS"][0]["reason"] == "INVALID_TRADESTATUS"
    assert normalized["CASES"][0]["classification"] == "UNKNOWN"
    assert normalized["CASES"][0]["basis"] == "INVALID_TRADESTATUS"


def test_absent_lifetime_row_remains_unknown_and_failure_is_durable():
    normalized, metrics = engine.normalize_provider_rows(
        _request(),
        [],
        provider_error_code="0",
        provider_error_message="",
        context=_context(),
    )
    assert metrics["UNKNOWN_CASE_N"] == 2
    assert all(case["classification"] == "UNKNOWN" for case in normalized["CASES"])
    assert all(case["basis"] == "PROVIDER_ROW_ABSENT_IN_LIFETIME" for case in normalized["CASES"])

    failure, failure_metrics = engine.normalize_provider_rows(
        _request(),
        [],
        provider_error_code="-1",
        provider_error_message="provider down",
        context=_context(),
    )
    assert failure_metrics["UNKNOWN_CASE_N"] == 2
    assert all(case["basis"] == "PROVIDER_ERROR" for case in failure["CASES"])


def test_checkpoint_after_each_request_and_resume_only_skips_valid_complete(tmp_path: Path):
    data_root, stage_root = _data_stage(tmp_path)
    requests = [_request("R3SAC-000001", 1), _request("R3SAC-000002", 2)]
    manifest = _manifest(requests)
    first_provider = FakeProvider([FakeResult([_row()])])
    first = engine.run_extraction_stage(
        _context(),
        manifest,
        "canary-hash",
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: first_provider,
        stop_after=1,
        sleep_fn=lambda _seconds: None,
    )
    checkpoint = json.loads((stage_root / "checkpoint.json").read_text(encoding="utf-8"))
    assert first["run"]["refetched_n"] == 1
    assert checkpoint["request_states"]["R3SAC-000001"]["status"] == "COMPLETE"
    assert checkpoint["request_states"]["R3SAC-000002"]["status"] == "PENDING"
    assert first_provider.logged_in is True and first_provider.logged_out is True

    second_provider = FakeProvider([FakeResult([_row()])])
    second = engine.run_extraction_stage(
        _context(),
        manifest,
        "canary-hash",
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: second_provider,
        sleep_fn=lambda _seconds: None,
    )
    assert second["run"]["skipped_complete_n"] == 1
    assert second["run"]["refetched_n"] == 1
    assert len(second_provider.calls) == 1
    assert engine._resume_parity(second["checkpoint"]) is True


def test_corrupt_receipt_wrong_hash_and_failed_receipt_are_not_skipped(tmp_path: Path):
    data_root, stage_root = _data_stage(tmp_path)
    request = _request()
    manifest = _manifest([request])
    provider = FakeProvider([FakeResult([_row()])])
    engine.run_extraction_stage(
        _context(),
        manifest,
        "canary-hash",
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: provider,
        sleep_fn=lambda _seconds: None,
    )
    assert engine.is_valid_complete_receipt(data_root, stage_root, request, canary_manifest_hash="canary-hash")
    assert not engine.is_valid_complete_receipt(data_root, stage_root, request, canary_manifest_hash="wrong")
    assert not engine.is_valid_complete_receipt(
        data_root,
        stage_root,
        request,
        canary_manifest_hash="canary-hash",
        lifecycle_session_keyset_hash="wrong-keyset",
    )

    normalized_path = stage_root / "normalized_receipts" / "R3SAC-000001.json"
    normalized_path.write_text("{}", encoding="utf-8")
    assert not engine.is_valid_complete_receipt(
        data_root, stage_root, request, canary_manifest_hash="canary-hash"
    )
    raw_path = stage_root / "raw_receipts" / "R3SAC-000001.json"
    raw_path.write_text("{}", encoding="utf-8")
    retry_provider = FakeProvider([FakeResult([_row()])])
    second = engine.run_extraction_stage(
        _context(),
        manifest,
        "canary-hash",
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: retry_provider,
        sleep_fn=lambda _seconds: None,
    )
    assert second["run"]["skipped_complete_n"] == 0
    assert second["run"]["refetched_n"] == 1
    assert len(retry_provider.calls) == 1


def test_canonical_zero_write_and_provider_only_stage_output(tmp_path: Path):
    data_root, stage_root = _data_stage(tmp_path)
    curated = data_root / "curated" / "daily_bars"
    curated.mkdir(parents=True)
    sentinel = curated / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    request = _request()
    provider = FakeProvider([FakeResult([_row()])])
    engine.run_extraction_stage(
        _context(),
        _manifest([request]),
        "canary-hash",
        data_root=data_root,
        stage_root=stage_root,
        provider_factory=lambda: provider,
        sleep_fn=lambda _seconds: None,
    )
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    source = inspect.getsource(engine)
    assert "write_parquet" not in source
    assert "R4A9" in source
    assert not (data_root / "curated" / "daily_bars" / "canonical_write.marker").exists()


def test_partial_canary_cannot_claim_full_extraction():
    source = inspect.getsource(engine)
    assert "CANARY_REQUEST_N" in source
    assert engine.CANARY_REQUEST_N < engine.FULL_REQUEST_N
