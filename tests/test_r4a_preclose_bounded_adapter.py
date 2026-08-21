"""Targeted offline regression tests for the R4A bounded preclose adapter.

All tests are offline: they use a fake provider and in-memory fixtures. The
real BaoStock session is never constructed or called. No market data is
written; only in-memory adapter results are asserted.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import polars as pl

from ashare_data.r4a_preclose_bounded_adapter import (
    AS_OF,
    FROZEN_000564_CASE,
    WINDOW_START,
    build_formal_facts,
    build_query_plan,
    compute_resumption_candidates,
    display_equal,
    load_frozen_sentinel_evidence,
    load_pilot_symbols,
    load_required_keys,
    normalize_baostock_preclose_rows,
    quality_gate_pass,
    real_root_identity,
    run_bounded_adapter,
    verify_clean_normal_parity,
    verify_frozen_sentinels,
)


def _required_keys() -> set[tuple[str, date]]:
    return {
        ("000001.SZ", date(2016, 1, 5)),
        ("000001.SZ", date(2016, 1, 6)),
        ("000001.SZ", date(2016, 1, 7)),
    }


def _formal_required() -> dict[str, object]:
    return {
        "required_keys": _required_keys(),
        "required_row_n": len(_required_keys()),
        "duplicate_required_key_n": 0,
        "identity_scope_mismatch": [],
        "identity_scope_mismatch_n": 0,
        "post_asof_n": 0,
        "symbols": ["000001.SZ"],
    }


def _provider_row(
    symbol: str,
    trade_date: date,
    *,
    preclose: object,
    tradestatus: object = "1",
    code: str | None = None,
) -> dict[str, object]:
    date_value = trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date)
    return {
        "symbol": symbol,
        "date": date_value,
        "code": code if code is not None else _bs_code(symbol),
        "preclose": preclose,
        "tradestatus": tradestatus,
    }


def _bs_code(symbol: str) -> str:
    code, exchange = symbol.split(".")
    return ("sh" if exchange == "SH" else "sz") + "." + code


def _normalize(
    rows: list[dict[str, object]],
    required: set[tuple[str, date]] | None = None,
) -> dict[str, object]:
    return normalize_baostock_preclose_rows(
        rows,
        required if required is not None else _required_keys(),
        ["000001.SZ"],
        as_of=AS_OF,
    )


def _bars(symbol: str = "000001.SZ") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol] * 3,
            "trade_date": [date(2016, 1, 5), date(2016, 1, 6), date(2016, 1, 7)],
            "close": [10.0, 10.25, 10.5],
        }
    )


def _formal_rows() -> list[dict[str, object]]:
    return [
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 6), "preclose": 10.0},
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 7), "preclose": 10.25},
    ]


# ---------------------------------------------------------------------------
# 1-11. Provider normalization and formal fact contract
# ---------------------------------------------------------------------------


def test_1_required_status1_valid_preclose_yields_formal_fact():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 7), preclose=10.25),
        ]
    )
    counts = result["counts"]
    assert counts["ELIGIBLE_REQUIRED_ROW"] == 2
    formal = build_formal_facts(result["eligible_rows"], adapter_version="TEST", fetched_at="2026-08-21T00:00:00")
    assert len(formal) == 2
    assert formal[0]["provider_tradestatus"] == 1
    assert formal[0]["coverage_status"] == "COVERED"
    assert formal[0]["preclose"] == 10.0


def test_2_status0_outside_required_is_suspended_superset_nonblocking():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 3), preclose=10.0, tradestatus="0"),
        ]
    )
    counts = result["counts"]
    assert counts["PROVIDER_SUSPENDED_SUPERSET"] == 1
    assert counts["ELIGIBLE_REQUIRED_ROW"] == 1
    formal = build_formal_facts(result["eligible_rows"], adapter_version="TEST", fetched_at="t")
    assert all(row["provider_tradestatus"] == 1 for row in formal)


def test_3_status1_outside_required_is_unexpected_traded_blocker():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 3), preclose=10.0, tradestatus="1"),
        ]
    )
    assert result["counts"]["UNEXPECTED_TRADED"] == 1
    gate = quality_gate_pass(
        required_row_n=3,
        formal_fact_row_n=1,
        missing_required_n=2,
        counts=result["counts"],
        formal_rows=build_formal_facts(result["eligible_rows"], adapter_version="T", fetched_at="t"),
    )
    assert gate is False


def test_4_unknown_status_fails_closed():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0, tradestatus="2"),
            _provider_row("000001.SZ", date(2016, 1, 7), preclose=10.25),
        ]
    )
    counts = result["counts"]
    assert counts["TRADESTATUS_UNKNOWN"] == 1
    assert counts["ELIGIBLE_REQUIRED_ROW"] == 1
    gate = quality_gate_pass(
        required_row_n=3,
        formal_fact_row_n=1,
        missing_required_n=2,
        counts=counts,
        formal_rows=build_formal_facts(result["eligible_rows"], adapter_version="T", fetched_at="t"),
    )
    assert gate is False


def test_5_required_key_missing_fails_closed():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
        ]
    )
    assert result["counts"]["ELIGIBLE_REQUIRED_ROW"] == 1
    assert result["missing_required_n"] == 2


def test_6_wrong_provider_code_or_date_is_identity_failure():
    bad_code = _normalize(
        [
            _provider_row(
                "000001.SZ", date(2016, 1, 6), preclose=10.0, code="sz.000002"
            ),
            _provider_row("000001.SZ", date(2016, 1, 7), preclose=10.25),
        ]
    )
    assert bad_code["counts"]["IDENTITY_FAILURE"] == 1
    bad_date = _normalize(
        [
            _provider_row("000001.SZ", "not-a-date", preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 7), preclose=10.25),
        ]
    )
    assert bad_date["counts"]["IDENTITY_FAILURE"] == 1


def test_7_duplicate_provider_pk_fails_closed():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 7), preclose=10.25),
        ]
    )
    assert result["counts"]["DUPLICATE"] == 1
    assert result["counts"]["ELIGIBLE_REQUIRED_ROW"] == 2


def test_8_invalid_preclose_fails_closed():
    for bad in (None, 0, -1, float("nan"), float("inf"), "abc"):
        result = _normalize(
            [
                _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
                _provider_row("000001.SZ", date(2016, 1, 7), preclose=bad),
            ]
        )
        assert result["counts"]["INVALID_PRECLOSE"] == 1, bad


def test_9_post_asof_fails_closed():
    future = _normalize(
        [
            _provider_row("000001.SZ", date(2026, 12, 31), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 7), preclose=10.25),
        ]
    )
    assert future["counts"]["POST_ASOF"] == 1


def test_10_audit_rows_never_enter_formal_rows():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 3), preclose=10.0, tradestatus="0"),
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
        ]
    )
    assert len(result["audit_rows"]) == 1
    formal = build_formal_facts(result["eligible_rows"], adapter_version="T", fetched_at="t")
    assert [row["trade_date"] for row in formal] == [date(2016, 1, 6)]


def test_11_formal_schema_always_coverage_covered_and_status1():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 5), preclose=9.9),
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 7), preclose=10.25),
        ]
    )
    formal = build_formal_facts(result["eligible_rows"], adapter_version="T", fetched_at="t")
    assert len(formal) == 3
    assert all(row["coverage_status"] == "COVERED" for row in formal)
    assert all(row["provider_tradestatus"] == 1 for row in formal)
    assert all(isinstance(row["preclose"], float) and math.isfinite(row["preclose"]) for row in formal)


# ---------------------------------------------------------------------------
# 12-17. Clean-NORMAL parity and resumption
# ---------------------------------------------------------------------------


def _parity(exclude: set[tuple[str, date]]) -> dict[str, object]:
    return verify_clean_normal_parity(
        formal_rows=_formal_rows(),
        bars=_bars(),
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys=set(),
        known_special_keys=exclude,
    )


def test_12_clean_normal_exact_passes():
    result = _parity(set())
    assert result["CLEAN_NORMAL_N"] == 2
    assert result["CLEAN_NORMAL_MISMATCH_N"] == 0
    assert result["CLEAN_NORMAL_PARITY_STATUS"] == "PASS"


def test_13_clean_normal_mismatch_fails():
    rows = [
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 6), "preclose": 9.5},
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 7), "preclose": 10.25},
    ]
    result = verify_clean_normal_parity(
        formal_rows=rows,
        bars=_bars(),
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys=set(),
        known_special_keys=set(),
    )
    assert result["CLEAN_NORMAL_MISMATCH_N"] == 1
    assert result["CLEAN_NORMAL_PARITY_STATUS"] == "FAIL"


def test_14_corporate_action_row_excluded_from_normal():
    result = verify_clean_normal_parity(
        formal_rows=_formal_rows(),
        bars=_bars(),
        event_dates={( "000001.SZ", date(2016, 1, 6))},
        first_listing_dates=set(),
        resumption_keys=set(),
        known_special_keys=set(),
    )
    assert result["CLEAN_NORMAL_N"] == 1


def test_15_ipo_row_excluded_from_normal():
    result = verify_clean_normal_parity(
        formal_rows=_formal_rows(),
        bars=_bars(),
        event_dates=set(),
        first_listing_dates={( "000001.SZ", date(2016, 1, 7))},
        resumption_keys=set(),
        known_special_keys=set(),
    )
    assert result["CLEAN_NORMAL_N"] == 1


def test_16_resumption_row_excluded_from_normal():
    result = verify_clean_normal_parity(
        formal_rows=_formal_rows(),
        bars=_bars(),
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys={( "000001.SZ", date(2016, 1, 7))},
        known_special_keys=set(),
    )
    assert result["CLEAN_NORMAL_N"] == 1


def test_17_000564_frozen_case_is_resumption_not_normal():
    bars = pl.DataFrame(
        {
            "symbol": ["000564.SZ"] * 3,
            "trade_date": [date(2018, 7, 12), date(2018, 7, 20), date(2018, 7, 23)],
            "close": [4.78, 4.77, 4.75],
        }
    )
    trading_dates = {
        date(2018, 7, 12),
        date(2018, 7, 13),
        date(2018, 7, 16),
        date(2018, 7, 17),
        date(2018, 7, 18),
        date(2018, 7, 19),
        date(2018, 7, 20),
        date(2018, 7, 23),
    }
    instrument_map = {
        "000564.SZ": {
            "list_date": date(1990, 1, 1),
            "delist_date": None,
        }
    }
    res = compute_resumption_candidates(
        trading_dates=trading_dates,
        bars=bars,
        instrument_map=instrument_map,
    )
    assert ("000564.SZ", date(2018, 7, 20)) in res["resumption_candidate_keys"]
    result = verify_clean_normal_parity(
        formal_rows=[
            {"symbol": "000564.SZ", "trade_date": date(2018, 7, 20), "preclose": 4.77},
            {"symbol": "000564.SZ", "trade_date": date(2018, 7, 23), "preclose": 4.77},
        ],
        bars=bars,
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys=res["resumption_candidate_keys"],
        known_special_keys=set(),
    )
    # 000564 2018-07-20 is excluded from CLEAN_NORMAL; only 2018-07-23 remains.
    assert result["CLEAN_NORMAL_N"] == 1
    assert FROZEN_000564_CASE["trade_date"] == "2018-07-20"


# ---------------------------------------------------------------------------
# 18-19. Official sentinels
# ---------------------------------------------------------------------------


def test_18_sentinels_all_exact_passes():
    sentinels = load_frozen_sentinel_evidence(Path(__file__).resolve().parents[1])
    assert len(sentinels) == 20 + 3 + 1
    result = verify_frozen_sentinels(sentinels)
    assert result["SENTINEL_N"] == 24
    assert result["SENTINEL_MISMATCH_N"] == 0
    assert result["FROZEN_OFFICIAL_SENTINEL_PASS"] is True


def test_19_one_sentinel_mismatch_fails_closed():
    sentinels = load_frozen_sentinel_evidence(Path(__file__).resolve().parents[1])
    sentinels[0]["baostock_preclose"] = sentinels[0]["official_reference"] + 1.0
    result = verify_frozen_sentinels(sentinels)
    assert result["SENTINEL_MISMATCH_N"] == 1
    assert result["FROZEN_OFFICIAL_SENTINEL_PASS"] is False


# ---------------------------------------------------------------------------
# 20-21. Query plan determinism and dry-run write/provider safety
# ---------------------------------------------------------------------------


def test_20_deterministic_query_plan_and_hash():
    symbols = ["000001.SZ", "600000.SH"]
    first = build_query_plan(symbols, window_start=WINDOW_START, as_of=AS_OF)
    second = build_query_plan(symbols, window_start=WINDOW_START, as_of=AS_OF)
    assert first["QUERY_PLAN_HASH"] == second["QUERY_PLAN_HASH"]
    assert first["QUERY_WINDOW_N"] == second["QUERY_WINDOW_N"]
    assert first["query_plan"] == second["query_plan"]


def test_21_dry_run_zero_provider_and_zero_write(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_fetch(_window: dict[str, object]) -> list[dict[str, object]]:
        calls.append("fetch")
        return []

    result = run_bounded_adapter(
        root=tmp_path,
        symbols=["000001.SZ"],
        provider_fetch=fake_fetch,
        dry_run=True,
        adapter_version="TEST",
        fetched_at="t",
    )
    assert result["STATUS"] == "DRY_RUN_OK"
    assert result["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert result["MARKET_DATA_WRITE"] == "NO"
    assert calls == []


def test_quality_gate_suspended_superset_nonblocking():
    counts = {
        "ELIGIBLE_REQUIRED_ROW": 3,
        "PROVIDER_SUSPENDED_SUPERSET": 5,
        "UNEXPECTED_TRADED": 0,
        "TRADESTATUS_UNKNOWN": 0,
        "IDENTITY_FAILURE": 0,
        "POST_ASOF": 0,
        "DUPLICATE": 0,
        "INVALID_PRECLOSE": 0,
    }
    formal = build_formal_facts(
        [
            {"symbol": "000001.SZ", "trade_date": d, "preclose": 10.0}
            for d in (date(2016, 1, 5), date(2016, 1, 6), date(2016, 1, 7))
        ],
        adapter_version="T",
        fetched_at="t",
    )
    assert quality_gate_pass(
        required_row_n=3,
        formal_fact_row_n=3,
        missing_required_n=0,
        counts=counts,
        formal_rows=formal,
    ) is True


def test_load_required_keys_from_fixture(tmp_path):
    (tmp_path / "curated" / "daily_bars" / "symbol=000001.SZ").mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * 2,
            "trade_date": [date(2016, 1, 4), date(2016, 1, 5)],
        }
    ).write_parquet(tmp_path / "curated" / "daily_bars" / "symbol=000001.SZ" / "part.parquet")
    loaded = load_required_keys(tmp_path, ["000001.SZ"], as_of=AS_OF, window_start=WINDOW_START)
    assert loaded["required_row_n"] == 2
    assert loaded["duplicate_required_key_n"] == 0


def test_load_required_keys_detects_duplicate_and_post_asof(tmp_path):
    from ashare_data.r4a_preclose_bounded_adapter import AS_OF

    (tmp_path / "curated" / "daily_bars" / "symbol=000001.SZ").mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * 4,
            "trade_date": [
                date(2016, 1, 4),
                date(2016, 1, 4),
                date(2016, 1, 5),
                date(2026, 12, 31),
            ],
        }
    ).write_parquet(tmp_path / "curated" / "daily_bars" / "symbol=000001.SZ" / "part.parquet")
    loaded = load_required_keys(tmp_path, ["000001.SZ"], as_of=AS_OF, window_start=WINDOW_START)
    assert loaded["duplicate_required_key_n"] == 1
    assert loaded["post_asof_n"] == 1
    assert loaded["required_row_n"] == 2


def test_real_root_identity_is_read_only_shape(tmp_path):
    # no receipt => not ready; does not touch any provider
    result = real_root_identity(tmp_path)
    assert result["R4A0_READY"] is False
