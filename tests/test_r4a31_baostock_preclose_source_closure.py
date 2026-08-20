from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest


TOOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "run_r4a31_baostock_preclose_source_closure.py"
)
SPEC = importlib.util.spec_from_file_location("r4a31_closure", TOOL_PATH)
assert SPEC and SPEC.loader
r4a31 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r4a31)


def _reference() -> dict:
    symbol = "000001.SZ"
    rows = [
        {"symbol": symbol, "trade_date": date(2016, 1, 5), "close": 10.0, "prev_close": None},
        {"symbol": symbol, "trade_date": date(2016, 1, 7), "close": 11.0, "prev_close": 10.0},
    ]
    required_key = (symbol, date(2016, 1, 7))
    return {
        "bars": pl.DataFrame(rows),
        "required": pl.DataFrame(rows),
        "required_keys": {required_key},
        "required_by_year": {(symbol, 2016): {required_key}},
        "instrument_map": {
            symbol: {
                "symbol": symbol,
                "list_date": date(1990, 1, 1),
                "delist_date": None,
            }
        },
        "first_trade": {symbol: date(2016, 1, 5)},
        "event_dates": set(),
        "local_duplicate_pk_n": 0,
        "resumption_candidates": [],
        "resumption_candidate_keys": set(),
    }


def _previous_report(unexpected: int = 1) -> dict:
    return {
        "PILOT_SYMBOL_N": 24,
        "PILOT_SYMBOL_HASH": r4a31.PILOT_SYMBOL_HASH,
        "SYMBOL_YEAR_COVERAGE": [
            {
                "symbol": "000001.SZ",
                "year": 2016,
                "window_start": "2016-01-01",
                "window_end": "2016-12-31",
                "required_row_n": 1,
                "baostock_row_n": 1,
                "preclose_non_null_n": 1,
                "missing_required_row_n": 0,
                "unexpected_row_n": unexpected,
                "duplicate_pk_n": 0,
                "identity_failure_n": 0,
                "post_asof_n": 0,
                "status": "FAIL" if unexpected else "PASS",
            }
        ],
        "NORMAL_REQUIRED_ROW_N": 1,
        "NORMAL_EXACT_MATCH_N": 1,
        "NORMAL_MISMATCH_N": 0,
        "NORMAL_UNCOMPARED_N": 0,
        "NORMAL_MISMATCH_SAMPLE": [],
        "OFFICIAL_EVENT_N": 1,
        "OFFICIAL_EVENT_EXACT_N": 1,
        "OFFICIAL_EVENT_MISMATCH_N": 0,
        "OFFICIAL_EVENT_PARITY_STATUS": "PASS",
        "WINDOW_EDGE_STATUS": "PASS",
        "IPO_DETAILS": [],
    }


def _result(rows: list[list[str]], *, error_code: str = "0") -> dict:
    return {
        "plan": {
            "symbol": "000001.SZ",
            "year": 2016,
            "start": "2016-01-01",
            "end": "2016-12-31",
            "bs_code": "sz.000001",
            "required_row_n": 1,
        },
        "error_code": error_code,
        "error_msg": "",
        "rows": rows,
    }


def test_requery_selects_only_previous_unexpected_windows():
    previous = _previous_report(unexpected=1)
    selected = r4a31.select_requery_windows(previous)
    assert len(selected) == 1
    assert selected[0]["symbol"] == "000001.SZ"

    previous["SYMBOL_YEAR_COVERAGE"][0]["unexpected_row_n"] = 0
    assert r4a31.select_requery_windows(previous) == []


def test_tradestatus_zero_extra_is_suspended_superset_not_blocker():
    out = r4a31.process_requery_results(
        [
            _result(
                [
                    ["2016-01-07", "sz.000001", "10.00", "1"],
                    ["2016-01-08", "sz.000001", "10.00", "0"],
                ]
            )
        ],
        _reference(),
        _previous_report(),
    )
    assert out["MISSING_REQUIRED_ROW_N"] == 0
    assert out["PROVIDER_SUSPENDED_SUPERSET_N"] == 1
    assert out["UNEXPECTED_TRADED_ROW_N"] == 0
    assert out["R4A_PROVIDER_SCOPE_COVERAGE_STATUS"] == "PASS"
    assert out["EXTRA_ROW_AUDIT"][0]["classification"] == "PROVIDER_SUSPENDED_SUPERSET"


def test_tradestatus_one_extra_is_unexpected_traded_blocker():
    out = r4a31.process_requery_results(
        [_result([["2016-01-07", "sz.000001", "10.00", "1"], ["2016-01-08", "sz.000001", "10.00", "1"]])],
        _reference(),
        _previous_report(),
    )
    assert out["UNEXPECTED_TRADED_ROW_N"] == 1
    assert out["R4A_PROVIDER_SCOPE_COVERAGE_STATUS"] == "FAIL"
    assert out["EXTRA_ROW_AUDIT"][0]["classification"] == "UNEXPECTED_TRADED_ROW"


def test_unknown_tradestatus_fails_closed():
    out = r4a31.process_requery_results(
        [_result([["2016-01-07", "sz.000001", "10.00", "?"]])],
        _reference(),
        _previous_report(),
    )
    assert out["TRADESTATUS_UNKNOWN_N"] == 1
    assert out["MISSING_REQUIRED_ROW_N"] == 1
    assert out["R4A_PROVIDER_SCOPE_COVERAGE_STATUS"] == "FAIL"


def test_required_row_presence_uses_tradestatus_one():
    out = r4a31.process_requery_results(
        [_result([["2016-01-07", "sz.000001", "10.00", "0"]])],
        _reference(),
        _previous_report(),
    )
    assert out["REQUIRED_PROVIDER_PRESENT_N"] == 0
    assert out["MISSING_REQUIRED_ROW_N"] == 1


def test_wrong_provider_code_is_identity_failure():
    out = r4a31.process_requery_results(
        [_result([["2016-01-07", "sh.000001", "10.00", "1"]])],
        _reference(),
        _previous_report(),
    )
    assert out["IDENTITY_FAILURE_N"] == 1
    assert out["MISSING_REQUIRED_ROW_N"] == 1
    assert out["R4A_PROVIDER_SCOPE_COVERAGE_STATUS"] == "FAIL"


def test_resumption_candidates_use_trading_calendar_and_lifetime(tmp_path):
    calendar_path = tmp_path / "curated" / "trading_calendar" / "trade_date=2016"
    calendar_path.mkdir(parents=True)
    pl.DataFrame(
        {
            "trade_date": [date(2016, 1, 5), date(2016, 1, 6), date(2016, 1, 7)],
            "is_trading": [True, True, True],
        }
    ).write_parquet(calendar_path / "part-merged.parquet")
    reference = _reference()
    out = r4a31.add_resumption_candidates(tmp_path, reference)
    assert out["resumption_candidates"][0]["trade_date"] == "2016-01-07"
    assert out["resumption_candidates"][0]["gap_trading_day_n"] == 1


def test_000564_known_resumption_candidate_is_present_in_real_reference():
    official = r4a31.previous.load_official_event_rows(
        r4a31.previous.OFFICIAL_RECEIPT, list(r4a31.PILOT_SYMBOLS)
    )
    reference = r4a31.previous.load_reference_data(
        Path("/Users/luke808/AI/local-a-share-data-service-data"),
        list(r4a31.PILOT_SYMBOLS),
        official,
    )
    reference = r4a31.add_resumption_candidates(
        Path("/Users/luke808/AI/local-a-share-data-service-data"), reference
    )
    target = [
        row
        for row in reference["resumption_candidates"]
        if row["symbol"] == "000564.SZ" and row["trade_date"] == "2018-07-20"
    ]
    assert len(target) == 1
    assert target[0]["gap_trading_day_n"] == 157


def test_clean_normal_excludes_resumption_mismatch():
    reference = _reference()
    reference["required"] = pl.DataFrame(
        [
            {"symbol": "000001.SZ", "trade_date": date(2016, 1, 5), "close": 10.0, "prev_close": None},
            {"symbol": "000001.SZ", "trade_date": date(2016, 1, 7), "close": 11.0, "prev_close": 10.0},
            {"symbol": "000001.SZ", "trade_date": date(2016, 1, 8), "close": 12.0, "prev_close": 11.0},
        ]
    )
    reference["resumption_candidates"] = [
        {
            "symbol": "000001.SZ",
            "trade_date": "2016-01-07",
            "previous_effective_close": 10.0,
        },
        {
            "symbol": "000001.SZ",
            "trade_date": "2016-01-09",
            "previous_effective_close": 12.0,
        },
    ]
    reference["resumption_candidate_keys"] = {
        ("000001.SZ", date(2016, 1, 7)),
        ("000001.SZ", date(2016, 1, 9)),
    }
    reference["event_dates"] = {("000001.SZ", date(2016, 1, 9))}
    previous = _previous_report()
    previous.update(
        {
            "NORMAL_REQUIRED_ROW_N": 2,
            "NORMAL_EXACT_MATCH_N": 1,
            "NORMAL_MISMATCH_N": 1,
            "NORMAL_MISMATCH_SAMPLE": [
                {
                    "symbol": "000001.SZ",
                    "trade_date": "2016-01-07",
                    "local_previous_close": 10.0,
                    "baostock_preclose": 9.99,
                    "diff": 0.01,
                }
            ],
        }
    )
    out = r4a31.clean_normal_parity(
        reference,
        previous,
        {
            ("000001.SZ", date(2016, 1, 7)): 9.99,
            ("000001.SZ", date(2016, 1, 8)): 11.0,
            ("000001.SZ", date(2016, 1, 9)): 12.0,
        },
    )
    assert out["CLEAN_NORMAL_N"] == 1
    assert out["CLEAN_NORMAL_EXACT_N"] == 1
    assert out["CLEAN_NORMAL_MISMATCH_N"] == 0
    assert out["CLEAN_NORMAL_PARITY_STATUS"] == "PASS"
    assert out["RESUMPTION_CANDIDATE_N"] == 2
    assert out["RESUMPTION_EXACT_N"] == 1
    assert out["RESUMPTION_NONEXACT_N"] == 1


def test_ipo_official_three_row_fixture_is_exact():
    previous = _previous_report()
    previous["IPO_DETAILS"] = [
        {"symbol": "603007.SH", "baostock_preclose": 11.66},
        {"symbol": "688486.SH", "baostock_preclose": 64.76},
        {"symbol": "688489.SH", "baostock_preclose": 78.89},
    ]
    authority = [
        {"symbol": "603007.SH", "listing_date": "2016-08-26", "official_issue_price": 11.66, "authority_url": "sse"},
        {"symbol": "688486.SH", "listing_date": "2023-02-21", "official_issue_price": 64.76, "authority_url": "sse"},
        {"symbol": "688489.SH", "listing_date": "2022-12-02", "official_issue_price": 78.89, "authority_url": "sse"},
    ]
    out = r4a31.ipo_parity(previous, authority)
    assert out["IPO_OFFICIAL_N"] == 3
    assert out["IPO_EXACT_N"] == 3
    assert out["IPO_MISMATCH_N"] == 0
    assert out["IPO_PARITY_STATUS"] == "PASS"
    assert out["SECONDARY_AUTHORITY_SOURCES_N"] == 0


def test_ipo_mismatch_blocks_parity():
    previous = _previous_report()
    previous["IPO_DETAILS"] = [
        {"symbol": "603007.SH", "baostock_preclose": 11.65},
        {"symbol": "688486.SH", "baostock_preclose": 64.76},
        {"symbol": "688489.SH", "baostock_preclose": 78.89},
    ]
    authority = [
        {"symbol": "603007.SH", "listing_date": "2016-08-26", "official_issue_price": 11.66, "authority_url": "sse"},
        {"symbol": "688486.SH", "listing_date": "2023-02-21", "official_issue_price": 64.76, "authority_url": "sse"},
        {"symbol": "688489.SH", "listing_date": "2022-12-02", "official_issue_price": 78.89, "authority_url": "sse"},
    ]
    assert r4a31.ipo_parity(previous, authority)["IPO_PARITY_STATUS"] == "FAIL"


def test_source_decision_can_recommend_candidate_but_never_ready():
    report = {
        "R4A_PROVIDER_SCOPE_COVERAGE_STATUS": "PASS",
        "CLEAN_NORMAL_PARITY_STATUS": "PASS",
        "OFFICIAL_EVENT_PARITY_STATUS": "PASS",
        "IPO_OFFICIAL_N": 3,
        "IPO_MISMATCH_N": 0,
        "WINDOW_EDGE_STATUS": "PASS",
    }
    out = r4a31.source_decision(report)
    assert out["BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION"] == "PROMOTE_CANDIDATE_CANONICAL"
    assert out["R4A_IMPLEMENTATION_READY"] is False


def test_legacy_renderer_accepts_report_without_query_n():
    receipt = json.loads(
        (
            Path("/Users/luke808/ASL-r4a3-baostock-preclose-canonical-pilot-v01")
            / "reports/research/R4A3_BAOSTOCK_PRECLOSE_CANONICAL_PILOT_RECEIPT.json"
        ).read_text()
    )
    report = dict(receipt["report"])
    report.pop("QUERY_N", None)
    rendered = r4a31.previous.render_report(report)
    assert "QUERY_N=242" in rendered


def test_dry_run_has_zero_provider_and_no_market_write(monkeypatch):
    previous_report = _previous_report(unexpected=1)
    identity = {
        "FORMAL_IDENTITY_N": r4a31.FORMAL_IDENTITY_N,
        "FORMAL_IDENTITY_HASH": r4a31.FORMAL_IDENTITY_HASH,
        "IDENTITY_MATCH": True,
        "IDENTITY_SOURCE": "TEST_FROZEN_R3",
    }
    monkeypatch.setattr(r4a31, "load_previous_receipt", lambda path: {"report": previous_report})
    monkeypatch.setattr(r4a31, "load_ipo_authority", lambda path: [])
    monkeypatch.setattr(r4a31.previous, "load_official_event_rows", lambda path, symbols: [])
    monkeypatch.setattr(r4a31.previous, "load_reference_data", lambda root, symbols, rows: _reference())
    monkeypatch.setattr(r4a31, "add_resumption_candidates", lambda root, reference: reference)
    provider_calls: list[str] = []
    provider = type("Provider", (), {"login": lambda self: provider_calls.append("login")})()
    result = r4a31.run_closure(
        root=Path("/tmp/unused-r4a31"),
        dry_run=True,
        provider=provider,
        identity=identity,
        write_outputs=False,
    )
    assert result["STATUS"] == "DRY_RUN_READY"
    assert result["REQUERY_WINDOW_N"] == 1
    assert result["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert result["MARKET_DATA_WRITE"] == "NO"
    assert result["MANIFEST_MUTATION"] == "NO"
    assert provider_calls == []


def test_main_conflicting_modes_exit_two():
    with pytest.raises(SystemExit) as exc:
        r4a31.main(["--dry-run", "--exec"])
    assert exc.value.code == 2


def test_source_has_no_market_data_write_path():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "write_parquet" not in source
    assert "corporate_actions.write" not in source
    assert 'QUERY_FIELDS = "date,code,preclose,tradestatus"' in source
    assert "FULL_242_REQUERY" in source
