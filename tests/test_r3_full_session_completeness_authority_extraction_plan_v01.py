"""Offline tests for the frozen full-session extraction request plan."""

from __future__ import annotations

import inspect
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))

import r3_full_session_completeness_authority_extraction_plan_v01 as plan  # noqa: E402


def _input_manifest() -> dict:
    return {"INPUT_FILE_N": plan.INPUT_FILE_N, "INPUT_MANIFEST_HASH": plan.INPUT_MANIFEST_HASH}


def _fixtures() -> tuple[list[str], dict[str, dict], list[date]]:
    symbols = ["000001.SZ", "300546.SZ", "600000.SH", "600005.SH"]
    instruments = {
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


def test_manifest_is_deterministic_and_hash_excludes_self_reference():
    symbols, instruments, calendar = _fixtures()
    first, first_hash, _ = plan.build_full_request_manifest(
        symbols, instruments, calendar, _input_manifest()
    )
    reversed_instruments = {key: instruments[key] for key in reversed(symbols)}
    second, second_hash, _ = plan.build_full_request_manifest(
        list(reversed(symbols)), reversed_instruments, list(calendar), _input_manifest()
    )
    assert first == second
    assert first_hash == second_hash
    persisted = {**first, "FULL_REQUEST_MANIFEST_HASH": first_hash}
    persisted_hash = persisted.pop("FULL_REQUEST_MANIFEST_HASH")
    assert persisted_hash == plan.sha256_json(persisted)


def test_frozen_authority_constants_are_exact():
    assert plan.BASE_HEAD == "9d7baf69ccdaf323c3221a62cf0487378373e95f"
    assert plan.FEASIBILITY_COMMIT == plan.BASE_HEAD
    assert plan.FORMAL_SYMBOL_N == 5456
    assert plan.FORMAL_IDENTITY_HASH == "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
    assert plan.INPUT_FILE_N == 2580
    assert plan.INPUT_MANIFEST_HASH == "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"


def test_formal_scope_rejects_bj_and_non_stock_symbols():
    symbols, instruments, _ = _fixtures()
    scope = plan.validate_formal_scope(symbols, instruments)
    assert scope["FORMAL_SYMBOL_N"] == 4
    assert scope["SHSZ_ONLY"] is True
    bad = dict(instruments)
    bad["600005.SH"] = {**bad["600005.SH"], "exchange": "BJ"}
    with pytest.raises(plan.PlanError, match="FORMAL_SCOPE_NOT_SHSZ_STOCK_CDR"):
        plan.validate_formal_scope(symbols, bad)


def test_lifecycle_clipping_and_year_boundary_are_explicit():
    symbols, instruments, calendar = _fixtures()
    manifest, _, coverage = plan.build_full_request_manifest(
        symbols, instruments, calendar, _input_manifest()
    )
    assert manifest["EXECUTION_BASELINE"] == "CALENDAR_YEAR_WINDOWED"
    by_symbol = {}
    for request in manifest["requests"]:
        by_symbol.setdefault(request["symbol"], []).append(request)
    assert by_symbol["300546.SZ"][0]["start_date"] == date(2016, 9, 28)
    assert by_symbol["600005.SH"][0]["end_date"] == date(2016, 1, 5)
    assert [row["calendar_year"] for row in by_symbol["600000.SH"]] == [2016, 2017]
    assert by_symbol["600000.SH"][0]["end_date"] == date(2016, 12, 31)
    assert by_symbol["600000.SH"][1]["start_date"] == date(2017, 1, 1)
    assert coverage["REQUEST_COVERAGE_MISSING_KEY_N"] == 0
    assert coverage["REQUEST_COVERAGE_DUPLICATE_KEY_N"] == 0
    assert coverage["REQUEST_COVERAGE_EXACT"] is True


def test_manifest_does_not_use_current_canonical_gaps():
    source = inspect.getsource(plan.build_full_request_manifest)
    assert "daily_bars" not in source
    assert "gap" not in source.lower()
    assert "calendar_trading_date_n" in source


def test_input_manifest_drift_fails_closed_before_loading_plan_inputs(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        plan,
        "build_input_file_manifest",
        lambda _root: {"INPUT_FILE_N": 2580, "INPUT_MANIFEST_HASH": "drift"},
    )
    with pytest.raises(plan.PlanError, match="INPUT_MANIFEST_DRIFT"):
        plan.run_plan(tmp_path)


def test_request_fields_and_ordering_are_frozen():
    symbols, instruments, calendar = _fixtures()
    manifest, _, _ = plan.build_full_request_manifest(
        symbols, instruments, calendar, _input_manifest()
    )
    requests = manifest["requests"]
    assert [row["request_order"] for row in requests] == list(range(1, len(requests) + 1))
    assert all(row["adjustflag"] == "3" for row in requests)
    assert all(row["frequency"] == "d" for row in requests)
    assert all(row["fields"] == plan.QUERY_FIELDS for row in requests)
    assert all(row["provider_runtime"] == plan.PROVIDER_RUNTIME for row in requests)
    assert all(row["request_id"].startswith("R3SAC-") for row in requests)


def test_unknown_is_preserved_and_not_expected_is_lifecycle_only():
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


def _valid_receipt(request: dict, manifest_hash: str) -> tuple[dict, dict, dict]:
    raw = {"rows": [["2020-01-01", request["bs_code"], "1"]]}
    normalized = {"rows": [{"symbol": request["symbol"], "trade_date": "2020-01-01", "tradestatus": 1}]}
    receipt = {
        "request_id": request["request_id"],
        "request_order": request["request_order"],
        "request_manifest_hash": manifest_hash,
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


def test_resume_accepts_only_hash_verified_complete_receipts():
    symbols, instruments, calendar = _fixtures()
    manifest, manifest_hash, _ = plan.build_full_request_manifest(
        symbols, instruments, calendar, _input_manifest()
    )
    request = manifest["requests"][0]
    receipt, raw, normalized = _valid_receipt(request, manifest_hash)
    assert plan.is_valid_complete_receipt(
        request,
        receipt,
        request_manifest_hash=manifest_hash,
        raw_payload=raw,
        normalized_payload=normalized,
    ) is True
    assert plan.is_valid_complete_receipt(
        request, receipt, request_manifest_hash=manifest_hash
    ) is False
    missing = dict(receipt)
    missing.pop("raw_sha256")
    assert plan.is_valid_complete_receipt(request, missing, request_manifest_hash=manifest_hash) is False
    corrupt = {**receipt, "raw_sha256": "corrupt"}
    assert plan.is_valid_complete_receipt(
        request,
        corrupt,
        request_manifest_hash=manifest_hash,
        raw_payload=raw,
        normalized_payload=normalized,
    ) is False
    failed = {**receipt, "status": "FAILED"}
    assert plan.is_valid_complete_receipt(request, failed, request_manifest_hash=manifest_hash) is False


def test_quality_gate_allows_unknown_but_not_key_pass():
    result = plan.quality_gate(
        [{"status": "COMPLETE", "provider_error_code": "0", "duplicate_provider_key_n": 0, "invalid_provider_row_n": 0}],
        unknown_case_n=3,
    )
    assert result["ALL_REQUESTS_COMPLETE"] is True
    assert result["QUALITY_GATE_READY_FOR_RECONCILIATION"] is True
    assert result["UNKNOWN_CASE_ALLOWED"] is True
    assert result["UNKNOWN_BLOCKS_KEY_PASS"] is True


def test_incomplete_provider_quality_gate_is_not_ready():
    result = plan.quality_gate(
        [
            {"status": "COMPLETE", "provider_error_code": "0", "duplicate_provider_key_n": 0, "invalid_provider_row_n": 0},
            {"status": "FAILED", "provider_error_code": "-1", "duplicate_provider_key_n": 0, "invalid_provider_row_n": 0},
        ],
        unknown_case_n=0,
    )
    assert result["ALL_REQUESTS_COMPLETE"] is False
    assert result["PROVIDER_FAILED_REQUEST_N"] == 1
    assert result["QUALITY_GATE_READY_FOR_RECONCILIATION"] is False


def test_checkpoint_contract_has_required_states_and_paths():
    contract = plan.checkpoint_contract()
    assert contract["state_enum"] == ["PENDING", "RUNNING", "COMPLETE", "FAILED"]
    assert contract["initial_state"] == "PENDING"
    assert contract["files"]["checkpoint"] == "checkpoint.json"
    assert contract["files"]["raw_receipts"] == "raw_receipts/{request_id}.json"
    assert set(plan.REQUIRED_RECEIPT_FIELDS).issubset(contract["receipt_required_fields"])
    assert "corrupt receipts rerun" in contract["resume_rule"]


def test_plan_is_provider_free_and_output_guard_rejects_external_path(tmp_path: Path):
    source = inspect.getsource(plan)
    assert "import baostock" not in source
    assert "query_history_k_data_plus" not in source
    assert "TdxWireClient" not in source
    assert "socket" not in source
    assert "import requests" not in source
    with pytest.raises(plan.PlanError, match="OUTPUT_OUTSIDE_REPO_REPORTS"):
        plan.write_json(tmp_path / "outside.json", {"x": 1})


def test_safety_contract_is_offline_and_non_promoting():
    symbols, instruments, calendar = _fixtures()
    scope = plan.validate_formal_scope(symbols, instruments)
    manifest, digest, coverage = plan.build_full_request_manifest(
        symbols, instruments, calendar, _input_manifest()
    )
    report = plan.build_report(
        input_manifest=_input_manifest(),
        identity_source={"authority": "fixture"},
        scope=scope,
        manifest_hash=digest,
        coverage={**coverage, **scope},
    )
    assert report["SAFETY"]["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert report["SAFETY"]["FULL_EXTRACTION_EXECUTED"] is False
    assert report["SAFETY"]["CANONICAL_WRITE_EXECUTED"] is False
    assert report["SAFETY"]["R4A9_RESUME_AUTHORIZED"] is False
    assert report["FULL_REQUEST_N"] == len(manifest["requests"])
