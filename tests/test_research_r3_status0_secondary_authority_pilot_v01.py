from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import research_r3_status0_secondary_authority_pilot_v01 as pilot  # noqa: E402


def _scope_row(
    symbol: str = "002087.SZ",
    trade_date: str = "2024-06-13",
    classification: str = pilot.CONTRADICTION,
) -> dict:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "classification": classification,
        "selection_reasons": ["YEAR_BOUNDARY_FIRST:2024"],
    }


def _primary(symbol: str = "002087.SZ", trade_date: str = "2024-06-13") -> dict:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "basis": "PROVIDER_TRADESTATUS_0",
        "classification": pilot.STATUS0_CLASSIFICATION,
        "tradestatus": 0,
        "request_id": "R3SAC-00001",
        "request_order": 1,
        "provider_code": "sz.002087",
        "open": "0.17",
        "high": "0.18",
        "low": "0.16",
        "close": "0.16",
        "volume_raw": "",
        "amount_raw": "",
        "preclose": "0.17",
        "raw_receipt_file_sha256": "a" * 64,
        "normalized_receipt_file_sha256": "b" * 64,
        "raw_payload_sha256": "c" * 64,
        "normalized_payload_sha256": "d" * 64,
    }


def _response(spec: dict, items: list[list]) -> pilot.ProviderResponse:
    if spec["endpoint"] == "daily":
        fields = [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "vol",
            "amount",
            "pct_chg",
        ]
    else:
        fields = ["ts_code", "trade_date", "suspend_type", "suspend_timing"]
    payload = {"code": 0, "msg": "", "data": {"fields": fields, "items": items}}
    return pilot.ProviderResponse(
        payload=payload,
        raw_bytes=pilot.canonical_json_bytes(payload),
        http_status=200,
    )


def _secondary_results(
    *,
    daily_items: list[list] | None = None,
    suspend_items: list[list] | None = None,
    symbol: str = "002087.SZ",
    trade_date: str = "2024-06-13",
) -> dict[str, dict]:
    results = {}
    for order, endpoint, items in (
        (1, "daily", daily_items or []),
        (2, "suspend_d", suspend_items or []),
    ):
        spec = {
            "request_id": f"R3S0SAP-{order:04d}",
            "request_order": order,
            "symbol": symbol,
            "trade_date": trade_date,
            "endpoint": endpoint,
        }
        results[endpoint] = pilot.normalize_secondary_observation(
            spec,
            _response(spec, items),
        )
    return results


def test_scope_exactly_52_and_all_eight_contradictions_are_included():
    scope = pilot.verify_scope(Path(__file__).resolve().parents[1])
    assert scope["key_n"] == 52
    assert len(scope["contradiction_keys"]) == 8
    assert sum(row["classification"] == pilot.INDETERMINATE for row in scope["rows"]) == 44


def test_same_source_is_not_independent_and_baostock_secondary_is_rejected():
    flags = pilot.independence_record(
        "baostock",
        {"source": "tdx_protocol"},
    )
    assert flags["same_source_as_primary"] is True
    assert flags["same_source_is_not_independent"] is True

    tushare_flags = pilot.independence_record(
        "TUSHARE_PRO",
        {"source": "TUSHARE"},
    )
    assert tushare_flags["independent_of_baostock_primary"] is True
    assert tushare_flags["same_source_as_canonical"] is True
    assert tushare_flags["same_source_is_not_independent"] is True


def test_secondary_positive_traded_row_is_expected():
    row = pilot.adjudicate_key(
        _scope_row(),
        _primary(),
        None,
        None,
        _secondary_results(
            daily_items=[["002087.SZ", "20240613", "0.17", "0.18", "0.16", "0.16", "0.17", "10", "2", "-5"]],
        ),
    )
    assert row["decision"] == "EXPECTED_BAR"
    assert row["repair_required"] is True


def test_secondary_explicit_suspension_is_not_expected():
    row = pilot.adjudicate_key(
        _scope_row(
            symbol="000004.SZ",
            trade_date="2022-05-05",
            classification=pilot.INDETERMINATE,
        ),
        _primary("000004.SZ", "2022-05-05"),
        None,
        None,
        _secondary_results(
            symbol="000004.SZ",
            trade_date="2022-05-05",
            suspend_items=[["000004.SZ", "20220505", "S", ""]],
        ),
    )
    assert row["decision"] == "NOT_EXPECTED_BAR"
    assert row["repair_required"] is False


def test_conflicting_secondary_daily_and_suspension_is_unknown():
    row = pilot.adjudicate_key(
        _scope_row(),
        _primary(),
        None,
        None,
        _secondary_results(
            daily_items=[["002087.SZ", "20240613", "0.17", "0.18", "0.16", "0.16", "0.17", "10", "2", "-5"]],
            suspend_items=[["002087.SZ", "20240613", "S", ""]],
        ),
    )
    assert row["decision"] == "UNKNOWN"
    assert row["decision_reason"] == "CONFLICTING_SECONDARY_DAILY_AND_SUSPENSION"


def test_secondary_row_absence_is_not_not_expected():
    row = pilot.adjudicate_key(
        _scope_row(
            symbol="000004.SZ",
            trade_date="2022-05-05",
            classification=pilot.INDETERMINATE,
        ),
        _primary("000004.SZ", "2022-05-05"),
        None,
        None,
        _secondary_results(
            symbol="000004.SZ",
            trade_date="2022-05-05",
        ),
    )
    assert row["decision"] == "UNKNOWN"
    assert row["decision_reason"] == "SECONDARY_ROW_ABSENT_WITHOUT_EXPLICIT_SUSPENSION"


def test_eastmoney_positive_kline_is_expected_and_absence_is_unknown():
    spec = {
        "request_id": "R3S0SAP-EM-0001",
        "request_order": 1,
        "symbol": "002087.SZ",
        "trade_date": "2024-06-13",
        "endpoint": "eastmoney_kline",
    }
    payload = {
        "rc": 0,
        "data": {
            "code": "002087",
            "klines": ["2024-06-13,0.17,0.16,0.18,0.16,200615,3286917.84,11.76,-5.88,-0.01,2.46"],
        },
    }
    observed = pilot.normalize_secondary_observation(
        spec,
        pilot.ProviderResponse(payload, pilot.canonical_json_bytes(payload), 200),
    )
    row = pilot.adjudicate_key(
        _scope_row(),
        _primary(),
        None,
        None,
        {"eastmoney_kline": observed},
    )
    assert row["decision"] == "EXPECTED_BAR"

    absent_payload = {"rc": 0, "data": None}
    absent = pilot.normalize_secondary_observation(
        spec,
        pilot.ProviderResponse(
            absent_payload,
            pilot.canonical_json_bytes(absent_payload),
            200,
        ),
    )
    unknown = pilot.adjudicate_key(
        _scope_row(),
        _primary(),
        None,
        None,
        {"eastmoney_kline": absent},
    )
    assert unknown["decision"] == "UNKNOWN"


def test_provider_failure_is_durable_and_receipt_hash_is_verified(tmp_path: Path):
    spec = {
        "request_id": "R3S0SAP-EM-0001",
        "request_order": 1,
        "symbol": "002087.SZ",
        "trade_date": "2024-06-13",
        "endpoint": "eastmoney_kline",
    }
    run_root = tmp_path / "run"
    (run_root / "raw_receipts").mkdir(parents=True)

    def failing_transport(_spec: dict) -> pilot.ProviderResponse:
        raise pilot.ProviderFailure("TEST_PROVIDER_FAILURE")

    observed, attempts = pilot.execute_request(
        spec,
        failing_transport,
        run_root,
        max_retry=0,
        sleep_fn=lambda _seconds: None,
    )
    assert attempts == 1
    assert observed["status"] == "PROVIDER_FAILED"

    observations = {
        (spec["symbol"], date(2024, 6, 13)): {"eastmoney_kline": observed}
    }
    pilot.verify_network_receipts(run_root, [spec], observations)

    receipt_path = run_root / "raw_receipts" / f"{spec['request_id']}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["provider_error_code"] = "TAMPERED"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(pilot.PilotError, match="SECONDARY_RECEIPT_FILE_HASH_DRIFT"):
        pilot.verify_network_receipts(run_root, [spec], observations)


def test_representative_category_coverage_and_partial_pass_never_extrapolates():
    scope = pilot.verify_scope(Path(__file__).resolve().parents[1])
    categories = {
        category
        for row in scope["rows"]
        if row["classification"] == pilot.INDETERMINATE
        for category in pilot._selection_category(row["selection_reasons"])
    }
    assert {
        "YEAR_BOUNDARY",
        "LONG_RUN_ENDPOINT",
        "LIFECYCLE_BOUNDARY",
        "SAME_DATE_CLUSTER",
        "GLOBAL_BOUNDARY",
        "NUMERIC_PATTERN_BOUNDARY",
        "CANONICAL_STATE",
    }.issubset(categories)
    partial = [{"decision": "NOT_EXPECTED_BAR", "provider_failed": False, "invalid_provider_row": False, "duplicate_provider_row": False}]
    assert (
        pilot.double_blank_pattern_status(partial, {})
        == "NOT_SUPPORTED_FOR_EXTRAPOLATION_DESIGN"
    )


def test_request_manifest_is_deterministic_and_excludes_local_reuse():
    rows = [
        _scope_row(),
        _scope_row(
            symbol="000004.SZ",
            trade_date="2022-05-05",
            classification=pilot.INDETERMINATE,
        ),
    ]
    local = {
        ("002087.SZ", date(2024, 6, 13)): {
            "symbol": "002087.SZ",
            "trade_date": "2024-06-13",
        }
    }
    first = pilot.build_request_manifest(rows, local)
    second = pilot.build_request_manifest(list(reversed(rows)), local)
    assert first["REQUEST_MANIFEST_HASH"] == second["REQUEST_MANIFEST_HASH"]
    assert first["NETWORK_KEY_N"] == 1
    assert len(first["REQUESTS"]) == 1
    assert all(item["symbol"] == "000004.SZ" for item in first["REQUESTS"])
    serialized = json.dumps(first, sort_keys=True)
    assert "TUSHARE_TOKEN" not in serialized


def test_isolated_stage_root_guard_rejects_escape_and_curated(tmp_path: Path):
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    allowed = data_root / "staging" / pilot.STAGING_DIRNAME
    assert pilot.require_isolated_stage_root(data_root, allowed) == allowed.resolve()
    with pytest.raises(pilot.PilotError):
        pilot.require_isolated_stage_root(data_root, data_root / "curated")
    with pytest.raises(pilot.PilotError):
        pilot.require_isolated_stage_root(data_root, data_root / "staging" / "other")
    with pytest.raises(pilot.PilotError):
        pilot.require_isolated_stage_root(data_root, tmp_path / "outside")


def test_direct_run_pilot_forbidden_root_fails_before_any_write(tmp_path: Path):
    data_root = tmp_path / "data"
    (data_root / "staging").mkdir(parents=True)
    forbidden = data_root / "curated" / "daily_bars"
    with pytest.raises(pilot.PilotError, match="PILOT_STAGE_ROOT_MISMATCH"):
        pilot.run_pilot(
            repo_root=tmp_path,
            data_root=data_root,
            stage_root=forbidden,
            transport=lambda _: pytest.fail("network must not be reached"),
        )
    assert list(data_root.rglob("*")) == [data_root / "staging"]
