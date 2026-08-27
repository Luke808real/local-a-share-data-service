"""Targeted offline tests for the R3 session-authority feasibility pilot."""

from __future__ import annotations

import inspect
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import research_r3_full_session_completeness_authority_feasibility_v01 as authority  # noqa: E402


def _snapshot() -> dict:
    return {
        "formal_symbols": ["000001.SZ", "300546.SZ"],
        "identity_n": 2,
        "identity_hash": authority.identity_hash(["000001.SZ", "300546.SZ"]),
        "calendar_dates": [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)],
        "calendar_n": 3,
        "symbols": {
            "000001.SZ": {
                "symbol": "000001.SZ",
                "name": "fixture",
                "exchange": "SZ",
                "asset_type": "stock",
                "list_date": date(1991, 1, 1),
                "delist_date": None,
                "observed_dates": [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)],
                "observed_date_set": {date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)},
                "row_n": 3,
                "expected_lifetime_session_n": 3,
                "missing_lifetime_session_n": 0,
                "max_gap_trading_n": 0,
                "gap_before": None,
                "gap_after": None,
            },
            "300546.SZ": {
                "symbol": "300546.SZ",
                "name": "fixture",
                "exchange": "SZ",
                "asset_type": "stock",
                "list_date": date(2019, 1, 1),
                "delist_date": None,
                "observed_dates": [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)],
                "observed_date_set": {date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)},
                "row_n": 3,
                "expected_lifetime_session_n": 3,
                "missing_lifetime_session_n": 0,
                "max_gap_trading_n": 0,
                "gap_before": None,
                "gap_after": None,
            },
        },
    }


def _request(symbol: str = "000001.SZ", adjustflag: str = "3") -> dict:
    return {
        "symbol": symbol,
        "bs_code": authority.bs_code(symbol),
        "categories": ["FIXTURE"],
        "purpose": "ACTIVE_NORMAL" if adjustflag == "3" else "ADJUSTFLAG_COMPARISON",
        "start_date": "2020-01-02",
        "end_date": "2020-01-06",
        "exchange_trading_date_n": 3,
        "list_date": "1991-01-01",
        "delist_date": None,
        "canonical_observed_row_n": 3,
        "canonical_gap_context": {},
        "frequency": "d",
        "adjustflag": adjustflag,
        "request_order": 1,
    }


def _row(day: str, code: str = "sz.000001", status: str = "1") -> list[str]:
    return [day, code, "10.0", "10.5", "9.5", "10.2", "100", "1020.0", "10.0", status]


def test_canonical_serialization_is_deterministic():
    left = {"b": 2, "a": [date(2020, 1, 2), True]}
    right = {"a": [date(2020, 1, 2), True], "b": 2}
    assert authority.canonical_json_bytes(left) == authority.canonical_json_bytes(right)
    assert authority.sha256_json(left) == authority.sha256_json(right)


def test_request_manifest_is_frozen_target_only_and_adjustflag_is_bounded():
    snapshot = _snapshot()
    input_manifest = {"INPUT_FILE_N": 2580, "INPUT_MANIFEST_HASH": authority.INPUT_MANIFEST_HASH}
    selected = {"000001.SZ": ["ACTIVE_NORMAL"], "300546.SZ": ["KNOWN_300546_GAP"]}
    manifest, digest = authority.build_request_manifest(snapshot, selected, input_manifest)
    assert {row["symbol"] for row in manifest["requests"]} == set(selected)
    assert len(manifest["requests"]) == 3
    assert sum(row["adjustflag"] == "1" for row in manifest["requests"]) == 1
    assert digest == authority.sha256_json(manifest)
    reversed_manifest, reversed_digest = authority.build_request_manifest(
        snapshot,
        {"300546.SZ": ["KNOWN_300546_GAP"], "000001.SZ": ["ACTIVE_NORMAL"]},
        input_manifest,
    )
    assert reversed_manifest == manifest
    assert reversed_digest == digest


def test_status_contract_distinguishes_status_zero_and_absence():
    snapshot = _snapshot()
    requests = [_request()]
    raw = {
        "REQUESTS": [
            {
                "request": requests[0],
                "final_error_code": "0",
                "final_error_msg": "success",
                "rows": [_row("2020-01-02", status="1"), _row("2020-01-03", status="0")],
            }
        ],
        "provider": {},
        "login": {"error_code": "0"},
        "logout": {"error_code": "0"},
    }
    normalized = authority.normalize_receipt(raw, snapshot)
    cases = {row["trade_date"]: row for row in normalized["CASES"]}
    assert cases[date(2020, 1, 2)]["classification"] == "EXPECTED_BAR"
    assert cases[date(2020, 1, 3)]["classification"] == "NOT_EXPECTED_BAR"
    assert cases[date(2020, 1, 6)]["classification"] == "UNKNOWN"
    assert cases[date(2020, 1, 6)]["basis"] == "PROVIDER_ROW_ABSENT_IN_LIFETIME"


def test_duplicate_and_unknown_provider_rows_are_fail_closed():
    snapshot = _snapshot()
    request = _request()
    raw = {
        "REQUESTS": [
            {
                "request": request,
                "final_error_code": "0",
                "final_error_msg": "success",
                "rows": [_row("2020-01-02"), _row("2020-01-02"), _row("2020-01-03", status="2")],
            }
        ],
        "provider": {},
        "login": {"error_code": "0"},
        "logout": {"error_code": "0"},
    }
    normalized = authority.normalize_receipt(raw, snapshot)
    result = normalized["REQUESTS"][0]
    assert result["duplicate_provider_key_n"] == 1
    assert result["invalid_row_n"] == 1
    aggregate = authority.aggregate_semantics(normalized, snapshot)
    assert aggregate["DUPLICATE_PROVIDER_KEY_N"] == 1
    assert aggregate["TRADESTATUS_UNKNOWN_N"] == 1
    assert authority.decision(aggregate, {"tradestatus_set_equal": True})[0] == "BLOCKED"


def test_provider_failure_is_retained_and_fail_closed():
    snapshot = _snapshot()
    request = _request()
    raw = {
        "REQUESTS": [
            {
                "request": request,
                "final_error_code": "-1",
                "final_error_msg": "provider down",
                "rows": [],
            }
        ],
        "provider": {},
        "login": {"error_code": "0"},
        "logout": {"error_code": "0"},
    }
    normalized = authority.normalize_receipt(raw, snapshot)
    aggregate = authority.aggregate_semantics(normalized, snapshot)
    assert aggregate["PROVIDER_FAILED_REQUEST_N"] == 1
    assert authority.decision(aggregate, {"tradestatus_set_equal": True})[0] == "BLOCKED"


def test_adjustflag_comparison_checks_date_and_status_parity():
    snapshot = _snapshot()
    base = _request()
    alternate = _request(adjustflag="1")
    raw = {
        "REQUESTS": [
            {
                "request": base,
                "final_error_code": "0",
                "final_error_msg": "success",
                "rows": [_row("2020-01-02"), _row("2020-01-03", status="0")],
            },
            {
                "request": alternate,
                "final_error_code": "0",
                "final_error_msg": "success",
                "rows": [_row("2020-01-02"), _row("2020-01-03", status="0")],
            },
        ],
        "provider": {},
        "login": {"error_code": "0"},
        "logout": {"error_code": "0"},
    }
    normalized = authority.normalize_receipt(raw, snapshot)
    comparison = authority.adjustflag_comparison(normalized)
    assert comparison["date_set_equal"] is True
    assert comparison["tradestatus_set_equal"] is True
    assert comparison["adjustflag_changes_session_coverage"] is False


def test_offline_and_report_paths_have_zero_market_write_or_network_boundary():
    source = inspect.getsource(authority)
    offline_source = inspect.getsource(authority.run_offline_report)
    assert "write_parquet" not in source
    assert "os.replace" not in source
    assert "TdxWireClient" not in source
    assert "fetch_pilot(" not in offline_source
    assert "import baostock" not in offline_source


def test_report_writer_rejects_data_root_or_other_output_path(tmp_path: Path):
    with pytest.raises(authority.FeasibilityError, match="RESEARCH_OUTPUT_OUTSIDE_REPO_REPORTS"):
        authority.write_json(tmp_path / "data-root" / "report.json", {"x": 1})


def test_decision_never_maps_unknown_to_pass():
    aggregate = {
        "PROVIDER_FAILED_REQUEST_N": 0,
        "DUPLICATE_PROVIDER_KEY_N": 0,
        "INVALID_PROVIDER_ROW_N": 0,
        "TRADESTATUS_UNKNOWN_N": 0,
        "SUSPENSION_REQUEST_N": 1,
        "SUSPENSION_ROW_ABSENT_IN_LIFETIME_N": 1,
        "LIFECYCLE_OUTSIDE_CASE_N": 1,
    }
    feasibility, recommendation, reasons = authority.decision(
        aggregate, {"tradestatus_set_equal": True}
    )
    assert feasibility == "BLOCKED"
    assert recommendation == "DO_NOT_START_FULL_EXTRACTION"
    assert reasons["unknown_row_absence"] == "EXPLICIT_UNKNOWN_IN_LIFETIME"


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [("000001.SZ", "sz.000001"), ("600000.SH", "sh.600000")],
)
def test_baostock_code_is_explicit_shsz_only(symbol: str, expected: str):
    assert authority.bs_code(symbol) == expected
