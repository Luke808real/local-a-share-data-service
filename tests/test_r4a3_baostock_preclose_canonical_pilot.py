from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl


TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_r4a3_baostock_preclose_canonical_pilot.py"
SPEC = importlib.util.spec_from_file_location("r4a3_pilot", TOOL_PATH)
assert SPEC and SPEC.loader
r4a3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r4a3)


def _normalized_identity() -> dict:
    symbols = list(r4a3.PILOT_SYMBOLS) + [f"{i:06d}.SZ" for i in range(100000, 105432)]
    return {
        "FORMAL_IDENTITY_N": r4a3.FORMAL_IDENTITY_N,
        "FORMAL_IDENTITY_HASH": r4a3.FORMAL_IDENTITY_HASH,
        "IDENTITY_MATCH": True,
        "IDENTITY_SOURCE": "TEST_FROZEN_R3",
        "IDENTITY_RECEIPT_MATCH": True,
        "symbols": symbols,
    }


def _reference(symbol: str = "000001.SZ") -> dict:
    rows = [
        {"symbol": symbol, "trade_date": date(2016, 1, 4), "close": 10.0, "prev_close": None},
        {"symbol": symbol, "trade_date": date(2016, 1, 5), "close": 10.25, "prev_close": 10.0},
    ]
    key = (symbol, date(2016, 1, 5))
    return {
        "bars": pl.DataFrame(rows),
        "required": pl.DataFrame(rows),
        "required_keys": {key},
        "required_by_year": {(symbol, 2016): {key}},
        "instrument_map": {
            s: {
                "symbol": s,
                "name": s,
                "exchange": s[-2:],
                "asset_type": "stock",
                "list_date": date(1990, 1, 1),
                "delist_date": None,
            }
            for s in r4a3.PILOT_SYMBOLS
        },
        "first_trade": {symbol: date(2016, 1, 4)},
        "event_dates": set(),
        "max_gaps": {s: {"max_calendar_gap_days": 0} for s in r4a3.PILOT_SYMBOLS},
        "local_duplicate_pk_n": 0,
    }


def test_frozen_pilot_scope_is_exact_and_bj_free():
    assert len(r4a3.PILOT_SYMBOLS) == 24
    assert len(set(r4a3.PILOT_SYMBOLS)) == 24
    assert r4a3.PILOT_SYMBOL_HASH == "5fa9f5c9ef376f0c453d3f543dc3a8ee9d61f73cec3a0fd35a9bea5081e17843"
    assert all(symbol.endswith((".SH", ".SZ")) for symbol in r4a3.PILOT_SYMBOLS)
    assert {"000002.SZ", "688486.SH"} <= set(r4a3.PILOT_SYMBOLS)


def test_bs_code_rejects_bj_and_noncanonical():
    assert r4a3.bs_code("000002.SZ") == "sz.000002"
    assert r4a3.bs_code("688486.SH") == "sh.688486"
    for bad in ("000002.BJ", "2.SZ", "000002", "ABCDEF.SZ"):
        try:
            r4a3.bs_code(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)


def test_normalize_identity_requires_all_frozen_fields():
    base = {
        "identity_ok": True,
        "IDENTITY_STATUS": "PASS",
        "EXPECTED_SYMBOL_N": r4a3.FORMAL_IDENTITY_N,
        "EXPECTED_SYMBOL_HASH": r4a3.FORMAL_IDENTITY_HASH,
        "symbols": list(_normalized_identity()["symbols"]),
    }
    # The synthetic symbol list cannot silently pass the real frozen hash.
    assert r4a3.normalize_identity(base)["IDENTITY_MATCH"] is False
    for key, value in (("identity_ok", None), ("IDENTITY_STATUS", "FAIL")):
        candidate = dict(base)
        candidate[key] = value
        assert r4a3.normalize_identity(candidate)["IDENTITY_MATCH"] is False


def test_validate_pilot_scope_fails_when_identity_excludes_symbol():
    result = r4a3.validate_pilot_symbols({"symbols": ["000001.SZ"]})
    assert result["PILOT_SYMBOL_SCOPE_MATCH"] is False
    assert any("OUTSIDE_FORMAL_IDENTITY" in item for item in result["PILOT_SYMBOL_FAILURES"])


def test_year_windows_are_inclusive_and_asof_bounded():
    windows = r4a3.year_windows()
    assert windows[0] == (2016, date(2016, 1, 1), date(2016, 12, 31))
    assert windows[-1] == (2026, date(2026, 1, 1), date(2026, 8, 17))
    assert len(windows) == 11


def test_process_provider_results_counts_missing_unexpected_duplicate_and_identity():
    ref = _reference()
    item = {
        "symbol": "000001.SZ",
        "year": 2016,
        "start": "2016-01-01",
        "end": "2016-12-31",
        "bs_code": "sz.000001",
        "required_row_n": 1,
    }
    result = {
        "plan": item,
        "error_code": "0",
        "error_msg": "success",
        "rows": [
            ["2016-01-05", "sz.000001", "10.00", "1"],
            ["2016-01-05", "sz.000001", "10.00", "1"],
            ["2016-01-06", "sz.000001", "10.10", "1"],
            ["2016-01-07", "wrong.000001", "10.10", "1"],
        ],
    }
    out = r4a3.process_provider_results([result], ref)
    assert out["REQUIRED_ROW_N"] == 1
    assert out["DUPLICATE_PK_N"] == 1
    assert out["UNEXPECTED_ROW_N"] == 1
    assert out["IDENTITY_FAILURE_N"] == 1
    assert out["MISSING_REQUIRED_ROW_N"] == 0


def test_normal_parity_exact_and_mismatch_are_not_rounded_into_pass():
    ref = _reference()
    assert r4a3.normal_parity(ref, {("000001.SZ", date(2016, 1, 5)): 10.0})[
        "NORMAL_PARITY_STATUS"
    ] == "PASS"
    mismatch = r4a3.normal_parity(ref, {("000001.SZ", date(2016, 1, 5)): 10.01})
    assert mismatch["NORMAL_MISMATCH_N"] == 1
    assert mismatch["NORMAL_PARITY_STATUS"] == "FAIL"


def test_sol_corrections_supersede_old_r4a21_unresolved_rows():
    rows = r4a3.load_official_event_rows(r4a3.OFFICIAL_RECEIPT, list(r4a3.PILOT_SYMBOLS))
    corrected = {(r["symbol"], r["ex_date"]): r for r in rows}
    assert corrected[("000002.SZ", "2022-08-25")]["official_adjusted_cash_share"] == 0.968802
    assert corrected[("000002.SZ", "2022-08-25")]["official_display_preclose"] == 15.65
    assert corrected[("000002.SZ", "2023-08-25")]["official_adjusted_cash_share"] == 0.674898
    assert corrected[("000002.SZ", "2023-08-25")]["official_display_preclose"] == 13.04
    assert corrected[("000002.SZ", "2022-08-25")]["status"] == "RESOLVED_OFFICIAL_ADJUSTED_BASIS"


def test_official_event_parity_requires_exact_display_value():
    rows = [{
        "symbol": "000002.SZ",
        "ex_date": "2022-08-25",
        "official_display_preclose": 15.65,
        "status": "RESOLVED_OFFICIAL_ADJUSTED_BASIS",
        "differential_dividend": "NO",
        "authority_url": "official",
    }]
    assert r4a3.official_event_parity(rows, {("000002.SZ", date(2022, 8, 25)): 15.65})[
        "OFFICIAL_EVENT_PARITY_STATUS"
    ] == "PASS"
    assert r4a3.official_event_parity(rows, {("000002.SZ", date(2022, 8, 25)): 15.64})[
        "OFFICIAL_EVENT_MISMATCH_N"
    ] == 1


def test_ipo_without_prebound_official_price_is_unknown_not_pass():
    ref = _reference()
    ref["first_trade"] = {"688486.SH": date(2023, 2, 21)}
    ref["instrument_map"]["688486.SH"] = {
        "symbol": "688486.SH",
        "list_date": date(2023, 2, 21),
    }
    out = r4a3.ipo_parity(ref, {("688486.SH", date(2023, 2, 21)): 56.0}, ["688486.SH"])
    assert out["IPO_OFFICIAL_N"] == 0
    assert out["IPO_PARITY_STATUS"] == "UNKNOWN_OFFICIAL_SUBSET_EMPTY"


def test_source_decision_never_sets_r4a_ready():
    report = {
        "COVERAGE_STATUS": "PASS",
        "IDENTITY_COMPLETE": True,
        "NORMAL_PARITY_STATUS": "PASS",
        "OFFICIAL_EVENT_PARITY_STATUS": "PASS",
        "IPO_PARITY_STATUS": "PASS",
        "WINDOW_EDGE_STATUS": "PASS",
        "POST_ASOF_N": 0,
    }
    out = r4a3.source_decision(report)
    assert out["BAOSTOCK_PRECLOSE_ROLE_RECOMMENDATION"] == "PROMOTE_CANDIDATE_CANONICAL"
    assert out["R4A_IMPLEMENTATION_READY"] is False


def test_dry_run_has_no_provider_or_report_write(tmp_path, monkeypatch):
    normalized = _normalized_identity()
    monkeypatch.setattr(r4a3, "load_frozen_identity", lambda root: normalized)
    provider_calls = []
    result = r4a3.run_pilot(
        root=tmp_path,
        dry_run=True,
        reference_data=_reference(),
        provider=type("Provider", (), {"login": lambda self: provider_calls.append("login")})(),
        output_dir=tmp_path / "reports",
        write_outputs=True,
    )
    assert result["STATUS"] == "DRY_RUN_READY"
    assert result["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert result["MARKET_DATA_WRITE"] == "NO"
    assert provider_calls == []
    assert not (tmp_path / "reports").exists()


def test_source_contains_only_bounded_preclose_query_fields():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert 'QUERY_FIELDS = "date,code,preclose,tradestatus"' in source
    assert 'frequency=QUERY_FREQUENCY' in source
    assert 'adjustflag=QUERY_ADJUSTFLAG' in source
    assert "query_history_k_data_plus" in source
    assert "open,high,low,close,volume" not in source


def test_source_has_no_market_data_write_path():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "write_parquet" not in source
    assert "corporate_actions.write" not in source
    assert "FORMAL_PRECLOSE_DATASET_WRITE\": \"NO\"" in source
