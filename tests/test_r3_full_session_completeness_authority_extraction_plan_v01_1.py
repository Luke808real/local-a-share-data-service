"""Offline tests for the exact R3 session-authority key-universe closure."""

from __future__ import annotations

import copy
import inspect
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))

import r3_full_session_completeness_authority_extraction_plan_v01 as v01  # noqa: E402
import r3_full_session_completeness_authority_extraction_plan_v01_1 as plan  # noqa: E402


def _input_manifest() -> dict[str, object]:
    return {"INPUT_FILE_N": plan.INPUT_FILE_N, "INPUT_MANIFEST_HASH": plan.INPUT_MANIFEST_HASH}


def _fixtures() -> tuple[list[str], dict[str, dict[str, object]], list[date]]:
    symbols = ["000001.SZ", "300546.SZ", "600000.SH", "600005.SH"]
    instruments: dict[str, dict[str, object]] = {
        "000001.SZ": {
            "symbol": "000001.SZ",
            "exchange": "SZ",
            "asset_type": "stock",
            "list_date": date(1991, 4, 3),
            "delist_date": None,
        },
        "300546.SZ": {
            "symbol": "300546.SZ",
            "exchange": "SZ",
            "asset_type": "stock",
            "list_date": date(2016, 9, 28),
            "delist_date": None,
        },
        "600000.SH": {
            "symbol": "600000.SH",
            "exchange": "SH",
            "asset_type": "stock",
            "list_date": date(2015, 12, 30),
            "delist_date": date(2017, 1, 5),
        },
        "600005.SH": {
            "symbol": "600005.SH",
            "exchange": "SH",
            "asset_type": "stock",
            "list_date": date(2016, 1, 4),
            "delist_date": date(2016, 1, 5),
        },
    }
    calendar = [
        date(2016, 1, 4),
        date(2016, 1, 5),
        date(2016, 9, 28),
        date(2016, 12, 30),
        date(2017, 1, 3),
        date(2017, 1, 4),
        date(2017, 1, 5),
        date(2017, 1, 6),
    ]
    return symbols, instruments, calendar


def _fixture_requests() -> tuple[list[str], dict[str, dict[str, object]], list[date], list[dict[str, object]]]:
    symbols, instruments, calendar = _fixtures()
    manifest, _, _ = v01.build_full_request_manifest(symbols, instruments, calendar, _input_manifest())
    return symbols, instruments, calendar, manifest["requests"]


def test_v01_1_persisted_request_manifest_is_self_consistent_and_bound():
    payload = json.loads(plan.MANIFEST_PATH.read_text(encoding="utf-8"))
    persisted_hash = payload.pop("FULL_REQUEST_MANIFEST_HASH")
    assert persisted_hash == plan.sha256_json(payload)
    assert len(payload["requests"]) == 48345
    assert payload["TRADING_DATESET_HASH"]
    assert payload["LIFECYCLE_AUTHORITY_HASH"]
    assert payload["LIFECYCLE_SESSION_KEYSET_HASH"]


def test_frozen_authority_commits_and_identity_constants_are_exact():
    assert plan.BASE_HEAD == "2cf9abefbf6a2696f06fb85ba62cbc0e0f2c0dc8"
    assert plan.FEASIBILITY_COMMIT == "9d7baf69ccdaf323c3221a62cf0487378373e95f"
    assert plan.FORMAL_SYMBOL_N == 5456
    assert plan.FORMAL_IDENTITY_HASH == "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
    assert plan.INPUT_FILE_N == 2580
    assert plan.INPUT_MANIFEST_HASH == "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"


def test_formal_scope_is_shsz_only_and_rejects_bad_metadata():
    symbols, instruments, _ = _fixtures()
    scope = plan.validate_formal_scope(symbols, instruments)
    assert scope["FORMAL_SYMBOL_N"] == 4
    assert scope["SHSZ_ONLY"] is True
    bad = dict(instruments)
    bad["600005.SH"] = {**bad["600005.SH"], "exchange": "BJ"}
    with pytest.raises(plan.PlanError, match="FORMAL_SCOPE_NOT_SHSZ_STOCK_CDR"):
        plan.validate_formal_scope(symbols, bad)


def test_calendar_hash_changes_when_one_date_changes_with_same_count():
    first, first_hash = plan.build_trading_dateset([date(2020, 1, 2), date(2020, 1, 3)])
    second, second_hash = plan.build_trading_dateset([date(2020, 1, 2), date(2020, 1, 6)])
    assert first["TRADING_DATE_N"] == second["TRADING_DATE_N"] == 2
    assert first_hash != second_hash


def test_lifecycle_hash_changes_when_metadata_changes():
    rows = [
        {
            "symbol": "300546.SZ",
            "list_date": date(2016, 9, 28),
            "delist_date": None,
            "effective_start": date(2016, 9, 28),
            "effective_end": date(2026, 8, 17),
        }
    ]
    changed = [{**rows[0], "list_date": date(2016, 9, 29), "effective_start": date(2016, 9, 29)}]
    assert plan.lifecycle_authority_hash(rows) != plan.lifecycle_authority_hash(changed)


def test_lifecycle_clipping_year_boundary_and_exact_key_hash_are_explicit():
    symbols, instruments, calendar, requests = _fixture_requests()
    assert plan.lifecycle_bounds(instruments["300546.SZ"]) == (date(2016, 9, 28), date(2026, 8, 17))
    assert plan.lifecycle_bounds(instruments["600000.SH"]) == (date(2016, 1, 1), date(2017, 1, 5))
    key_hash, counts = plan.exact_session_key_scan(symbols, instruments, calendar, requests)
    assert key_hash == plan.session_keyset_hash(
        (symbol, trading_date)
        for symbol in sorted(symbols)
        for trading_date in calendar
        if plan.lifecycle_bounds(instruments[symbol])
        and plan.lifecycle_bounds(instruments[symbol])[0] <= trading_date <= plan.lifecycle_bounds(instruments[symbol])[1]
    )
    assert counts["REQUEST_COVERAGE_MISSING_KEY_N"] == 0
    assert counts["REQUEST_COVERAGE_DUPLICATE_KEY_N"] == 0
    assert counts["LIFECYCLE_SESSION_KEY_N"] > 0


def test_session_key_hash_changes_when_date_changes_with_same_key_count():
    first = plan.session_keyset_hash(
        [("000001.SZ", date(2020, 1, 2)), ("000001.SZ", date(2020, 1, 3))]
    )
    second = plan.session_keyset_hash(
        [("000001.SZ", date(2020, 1, 2)), ("000001.SZ", date(2020, 1, 6))]
    )
    assert first != second


def test_exact_request_coverage_detects_missing_and_duplicate_keys():
    symbols, instruments, calendar, requests = _fixture_requests()
    missing_requests = copy.deepcopy(requests)
    missing_requests[0]["start_date"] = date(2016, 1, 5)
    _, missing = plan.exact_session_key_scan(symbols, instruments, calendar, missing_requests)
    assert missing["REQUEST_COVERAGE_MISSING_KEY_N"] >= 1

    duplicate_requests = copy.deepcopy(requests)
    duplicate = copy.deepcopy(duplicate_requests[0])
    duplicate["request_id"] = f"R3SAC-{len(duplicate_requests) + 1:06d}"
    duplicate["request_order"] = len(duplicate_requests) + 1
    duplicate_requests.append(duplicate)
    _, duplicate_counts = plan.exact_session_key_scan(symbols, instruments, calendar, duplicate_requests)
    assert duplicate_counts["REQUEST_COVERAGE_DUPLICATE_KEY_N"] > 0


def test_unknown_is_preserved_and_status_contract_is_three_way():
    assert plan.classify_session_key(None, inside_lifetime=True) == {
        "classification": "UNKNOWN",
        "basis": "PROVIDER_ROW_ABSENT_IN_LIFETIME",
    }
    assert plan.classify_session_key(None, inside_lifetime=False) == {
        "classification": "NOT_EXPECTED_BAR",
        "basis": "OUTSIDE_LIFETIME",
    }
    assert plan.classify_session_key({"tradestatus": "1"}, inside_lifetime=True)["classification"] == "EXPECTED_BAR"
    assert plan.classify_session_key({"tradestatus": "0"}, inside_lifetime=True)["classification"] == "NOT_EXPECTED_BAR"
    assert plan.classify_session_key({"tradestatus": "2"}, inside_lifetime=True)["classification"] == "UNKNOWN"


def _valid_receipt(request: dict[str, object], manifest_hash: str, keyset_hash: str) -> tuple[dict, dict, dict]:
    raw = {"rows": [["2020-01-01", request["bs_code"], "1"]]}
    normalized = {"rows": [{"symbol": request["symbol"], "trade_date": "2020-01-01", "tradestatus": 1}]}
    receipt = {
        "request_id": request["request_id"],
        "request_order": request["request_order"],
        "request_manifest_hash": manifest_hash,
        "lifecycle_session_keyset_hash": keyset_hash,
        "attempt_n": 1,
        "provider_error_code": "0",
        "raw_row_n": 1,
        "raw_sha256": plan.sha256_json(raw),
        "normalized_row_n": 1,
        "normalized_sha256": plan.sha256_json(normalized),
        "status": "COMPLETE",
        "completed_at": "2026-08-27T00:00:00Z",
    }
    return receipt, raw, normalized


def test_resume_accepts_only_hash_verified_complete_receipts_with_keyset_binding():
    _, _, _, requests = _fixture_requests()
    request = requests[0]
    receipt, raw, normalized = _valid_receipt(request, "manifest", "keyset")
    assert plan.is_valid_complete_receipt_v01_1(
        request,
        receipt,
        request_manifest_hash="manifest",
        lifecycle_session_keyset_hash="keyset",
        raw_payload=raw,
        normalized_payload=normalized,
    ) is True
    assert plan.is_valid_complete_receipt_v01_1(
        request,
        receipt,
        request_manifest_hash="manifest",
        lifecycle_session_keyset_hash="wrong-keyset",
        raw_payload=raw,
        normalized_payload=normalized,
    ) is False
    corrupt = {**receipt, "raw_sha256": "corrupt"}
    assert plan.is_valid_complete_receipt_v01_1(
        request,
        corrupt,
        request_manifest_hash="manifest",
        lifecycle_session_keyset_hash="keyset",
        raw_payload=raw,
        normalized_payload=normalized,
    ) is False
    failed = {**receipt, "status": "FAILED"}
    assert plan.is_valid_complete_receipt_v01_1(
        request,
        failed,
        request_manifest_hash="manifest",
        lifecycle_session_keyset_hash="keyset",
        raw_payload=raw,
        normalized_payload=normalized,
    ) is False


def test_quality_gate_allows_unknown_but_requires_complete_clean_requests():
    result = plan.quality_gate(
        [{
            "status": "COMPLETE",
            "provider_error_code": "0",
            "duplicate_provider_key_n": 0,
            "invalid_provider_row_n": 0,
        }],
        unknown_case_n=3,
    )
    assert result["ALL_REQUESTS_COMPLETE"] is True
    assert result["QUALITY_GATE_READY_FOR_RECONCILIATION"] is True
    assert result["UNKNOWN_CASE_ALLOWED"] is True
    assert result["UNKNOWN_BLOCKS_KEY_PASS"] is True


def test_checkpoint_contract_contains_staging_layout_and_frozen_resume_rule():
    contract = plan.checkpoint_contract()
    assert contract["state_enum"] == ["PENDING", "RUNNING", "COMPLETE", "FAILED"]
    assert contract["files"]["checkpoint"] == "checkpoint.json"
    assert contract["files"]["raw_receipts"] == "raw_receipts/{request_id}.json"
    assert "lifecycle_session_keyset_hash" in contract["receipt_required_fields"]
    assert "corrupt receipts rerun" in contract["resume_rule"]


def test_future_extraction_authority_gate_is_fail_closed_before_network():
    values = {
        "input_manifest": {**_input_manifest(), "FILES": []},
        "trading_dateset_hash": "dates",
        "lifecycle_hash": "lifecycle",
        "formal_identity_hash": plan.FORMAL_IDENTITY_HASH,
        "request_manifest_hash": "request",
        "frozen_request_manifest_hash": "request",
        "expected_trading_dateset_hash": "dates",
        "expected_lifecycle_hash": "lifecycle",
        "expected_formal_identity_hash": plan.FORMAL_IDENTITY_HASH,
    }
    assert plan.validate_future_extraction_authority(**values)["NETWORK_REQUEST_N"] == 0
    drifted = {**values, "trading_dateset_hash": "drift"}
    with pytest.raises(plan.PlanError, match="EXTRACTION_AUTHORITY_DRIFT"):
        plan.validate_future_extraction_authority(**drifted)


def test_run_plan_input_drift_fails_before_any_output(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        plan,
        "build_input_file_manifest",
        lambda _root: {"INPUT_FILE_N": plan.INPUT_FILE_N, "INPUT_MANIFEST_HASH": "drift"},
    )
    with pytest.raises(plan.PlanError, match="INPUT_MANIFEST_DRIFT"):
        plan.run_plan(tmp_path)


def test_plan_is_provider_free_and_report_output_guard_rejects_external_path(tmp_path: Path):
    source = inspect.getsource(plan)
    assert "import baostock" not in source
    assert "query_history_k_data_plus" not in source
    assert "TdxWireClient" not in source
    assert "import requests" not in source
    with pytest.raises(plan.PlanError, match="OUTPUT_OUTSIDE_REPO_REPORTS"):
        plan.write_json(tmp_path / "outside.json", {"x": 1})


def test_committed_artifacts_prove_exact_scope_and_safety():
    report = json.loads(plan.REPORT_PATH.read_text(encoding="utf-8"))
    lifecycle = json.loads(plan.LIFECYCLE_PATH.read_text(encoding="utf-8"))
    dateset = json.loads(plan.TRADING_DATESET_PATH.read_text(encoding="utf-8"))
    assert len(lifecycle["ROWS"]) == 5456
    symbols = [row["symbol"] for row in lifecycle["ROWS"]]
    assert len(symbols) == len(set(symbols))
    assert all(symbol.endswith((".SH", ".SZ")) for symbol in symbols)
    assert plan.identity_hash(symbols) == plan.FORMAL_IDENTITY_HASH
    assert lifecycle["LIFECYCLE_AUTHORITY_HASH"] == report["LIFECYCLE_AUTHORITY_HASH"]
    assert dateset["TRADING_DATE_N"] == 2580
    assert report["LIFECYCLE_SESSION_KEY_N"] == 10897229
    assert report["OUTSIDE_LIFETIME_SESSION_KEY_N"] == 3179251
    assert report["LIFECYCLE_PLUS_OUTSIDE_EQUALS_GRID"] is True
    assert report["REQUEST_COVERAGE_MISSING_KEY_N"] == 0
    assert report["REQUEST_COVERAGE_DUPLICATE_KEY_N"] == 0
    assert report["SAFETY"]["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert report["SAFETY"]["FULL_EXTRACTION_EXECUTED"] is False
    assert report["SAFETY"]["CANONICAL_WRITE_EXECUTED"] is False
