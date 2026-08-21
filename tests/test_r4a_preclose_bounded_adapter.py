"""Targeted offline regression tests for the hardened R4A preclose adapter.

All tests are offline: they use a fake provider and in-memory fixtures. The
real BaoStock session is never constructed or called. No market data is
written; only in-memory adapter results are asserted.

This file keeps the R4A5 targeted coverage and adds the R4A5.1 hardening
regressions (prerequisite gating, current-extraction sentinels, global PK
duplicate detection, clean-normal fail-closed, window identity, plan-hash
coverage, and the real-wrapper shape via a fake module).
"""

from __future__ import annotations

import math
import sys
import types
from datetime import date
from pathlib import Path

import polars as pl

from ashare_data.r4a_preclose_bounded_adapter import (
    AS_OF,
    FROZEN_000564_CASE,
    WINDOW_START,
    BaostockSessionProvider,
    adapter_authority_status,
    build_formal_facts,
    build_query_plan,
    compute_resumption_candidates,
    display_equal,
    load_frozen_sentinel_evidence,
    load_pilot_symbols,
    load_required_keys,
    normalize_baostock_preclose_rows,
    quality_gate_pass,
    r4a0_prerequisite,
    r3_identity_match,
    run_bounded_adapter,
    verify_clean_normal_parity,
    verify_frozen_sentinels,
    parse_date,
)


def _required_keys() -> set[tuple[str, date]]:
    return {
        ("000001.SZ", date(2016, 1, 5)),
        ("000001.SZ", date(2016, 1, 6)),
        ("000001.SZ", date(2016, 1, 7)),
    }


def _bs_code(symbol: str) -> str:
    code, exchange = symbol.split(".")
    return ("sh" if exchange == "SH" else "sz") + "." + code


def _provider_row(
    symbol: str,
    trade_date: date | str,
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


def _parity(exclude: set[tuple[str, date]]) -> dict[str, object]:
    return verify_clean_normal_parity(
        formal_rows=_formal_rows(),
        bars=_bars(),
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys=set(),
        known_special_keys=exclude,
    )


def _make_root(tmp_path: Path) -> Path:
    """Minimal real-root fixture that passes the audited R4A0 gate."""
    (tmp_path / "curated" / "daily_bars" / "symbol=000001.SZ").mkdir(parents=True)
    (tmp_path / "curated" / "corporate_actions" / "symbol=000001.SZ").mkdir(parents=True)
    (tmp_path / "meta" / "asl" / "r3").mkdir(parents=True)
    (tmp_path / "manifest.db").touch()
    pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * 2,
            "trade_date": [date(2016, 1, 4), date(2016, 1, 5)],
        }
    ).write_parquet(tmp_path / "curated" / "daily_bars" / "symbol=000001.SZ" / "part.parquet")
    return tmp_path


# ---------------------------------------------------------------------------
# 1-11. Provider normalization and formal fact contract (kept)
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
    formal = build_formal_facts(result["eligible_rows"], adapter_version="TEST", fetched_at="t")
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


def test_3_status1_outside_required_is_unexpected_traded_blocker():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 3), preclose=10.0, tradestatus="1"),
        ]
    )
    assert result["counts"]["UNEXPECTED_TRADED"] == 1


def test_4_unknown_status_fails_closed():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0, tradestatus="2"),
            _provider_row("000001.SZ", date(2016, 1, 7), preclose=10.25),
        ]
    )
    assert result["counts"]["TRADESTATUS_UNKNOWN"] == 1


def test_5_required_key_missing_fails_closed():
    result = _normalize([_provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0)])
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
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2026, 12, 31), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 1, 7), preclose=10.25),
        ]
    )
    assert result["counts"]["POST_ASOF"] == 1


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
# 12-17. Clean-NORMAL parity and resumption (kept)
# ---------------------------------------------------------------------------


def test_12_clean_normal_exact_passes():
    result = _parity(set())
    assert result["CLEAN_NORMAL_REQUIRED_N"] == 2
    assert result["CLEAN_NORMAL_MISMATCH_N"] == 0
    assert result["NORMAL_FULL_PARITY_PASS"] is True


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
    assert result["NORMAL_FULL_PARITY_PASS"] is False


def test_14_corporate_action_row_excluded_from_normal():
    result = verify_clean_normal_parity(
        formal_rows=_formal_rows(),
        bars=_bars(),
        event_dates={("000001.SZ", date(2016, 1, 6))},
        first_listing_dates=set(),
        resumption_keys=set(),
        known_special_keys=set(),
    )
    assert result["CLEAN_NORMAL_REQUIRED_N"] == 1


def test_15_ipo_row_excluded_from_normal():
    result = verify_clean_normal_parity(
        formal_rows=_formal_rows(),
        bars=_bars(),
        event_dates=set(),
        first_listing_dates={("000001.SZ", date(2016, 1, 7))},
        resumption_keys=set(),
        known_special_keys=set(),
    )
    assert result["CLEAN_NORMAL_REQUIRED_N"] == 1


def test_16_resumption_row_excluded_from_normal():
    result = verify_clean_normal_parity(
        formal_rows=_formal_rows(),
        bars=_bars(),
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys={("000001.SZ", date(2016, 1, 7))},
        known_special_keys=set(),
    )
    assert result["CLEAN_NORMAL_REQUIRED_N"] == 1


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
    instrument_map = {"000564.SZ": {"list_date": date(1990, 1, 1), "delist_date": None}}
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
    assert result["CLEAN_NORMAL_REQUIRED_N"] == 1
    assert FROZEN_000564_CASE["trade_date"] == "2018-07-20"


# ---------------------------------------------------------------------------
# 18-19. Official sentinels now compare against CURRENT formal rows
# ---------------------------------------------------------------------------


def test_18_24_current_sentinels_exact_passes():
    sentinels = load_frozen_sentinel_evidence(Path(__file__).resolve().parents[1])
    assert len(sentinels) == 24
    current = [
        {
            "symbol": s["symbol"],
            "trade_date": parse_date(s["trade_date"]),
            "preclose": s["official_reference"],
        }
        for s in sentinels
    ]
    result = verify_frozen_sentinels(sentinels, current)
    assert result["SENTINEL_REQUIRED_N"] == 24
    assert result["SENTINEL_PRESENT_N"] == 24
    assert result["SENTINEL_EXACT_N"] == 24
    assert result["SENTINEL_MISSING_N"] == 0
    assert result["SENTINEL_MISMATCH_N"] == 0
    assert result["FROZEN_OFFICIAL_SENTINEL_PASS"] is True


def test_19_current_sentinel_mismatch_fails_closed():
    sentinels = load_frozen_sentinel_evidence(Path(__file__).resolve().parents[1])
    current = []
    for index, s in enumerate(sentinels):
        preclose = s["official_reference"]
        if index == 0:
            preclose = s["official_reference"] + 1.0
        current.append(
            {
                "symbol": s["symbol"],
                "trade_date": parse_date(s["trade_date"]),
                "preclose": preclose,
            }
        )
    result = verify_frozen_sentinels(sentinels, current)
    assert result["SENTINEL_MISMATCH_N"] == 1
    assert result["FROZEN_OFFICIAL_SENTINEL_PASS"] is False


# ---------------------------------------------------------------------------
# 20-21. Query plan determinism and dry-run write/provider safety (kept)
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

    root = _make_root(tmp_path)
    result = run_bounded_adapter(
        root=root,
        symbols=["000001.SZ"],
        provider_fetch=fake_fetch,
        dry_run=True,
        adapter_version="TEST",
        fetched_at="t",
    )
    assert result["DRY_RUN_STATUS"] in ("OK", "BLOCKED")
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
        "WINDOW_SCOPE_FAILURE": 0,
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


# ---------------------------------------------------------------------------
# R4A5.1 hardening regressions (A-N)
# ---------------------------------------------------------------------------


def _plain_root(tmp_path: Path) -> Path:
    (tmp_path / "curated" / "daily_bars").mkdir(parents=True)
    return tmp_path


def test_A_r3_identity_pass_but_r4a0_gate_false_blocks_and_zero_provider(tmp_path):
    root = _plain_root(tmp_path)
    # R4A0 gate false (no corporate_actions, no manifest); R3 identity unknown.
    calls: list[str] = []

    def fake_fetch(_window: dict[str, object]) -> list[dict[str, object]]:
        calls.append("fetch")
        return []

    result = run_bounded_adapter(
        root=root,
        symbols=["000001.SZ"],
        provider_fetch=fake_fetch,
        dry_run=False,
        adapter_version="TEST",
        fetched_at="t",
    )
    assert result["R4A0_READY"] is False
    assert result["STATUS"] == "R4A0_PREREQUISITE_FAILED"
    assert result["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert result["FORMAL_FACT_ROW_N"] == 0
    assert calls == []


def test_B_dry_run_r4a0_false_is_blocked_nonzero(tmp_path):
    root = _plain_root(tmp_path)
    result = run_bounded_adapter(
        root=root,
        symbols=["000001.SZ"],
        provider_fetch=None,
        dry_run=True,
        adapter_version="TEST",
        fetched_at="t",
    )
    assert result["DRY_RUN_STATUS"] == "BLOCKED"
    assert result["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert result["FROZEN_OFFICIAL_SENTINEL_RUNTIME_STATUS"] == "NOT_RUN_DRY_RUN"


def test_C_old_receipt_exact_but_current_sentinel_mismatch_fails():
    sentinels = load_frozen_sentinel_evidence(Path(__file__).resolve().parents[1])
    # Old receipt said exact, but the CURRENT extraction has a wrong preclose.
    current = [
        {
            "symbol": s["symbol"],
            "trade_date": parse_date(s["trade_date"]),
            "preclose": s["official_reference"] + (0.5 if s["kind"] == "OFFICIAL_EVENT" else 0.0),
        }
        for s in sentinels
    ]
    result = verify_frozen_sentinels(sentinels, current)
    assert result["SENTINEL_MISMATCH_N"] > 0
    assert result["FROZEN_OFFICIAL_SENTINEL_PASS"] is False


def test_D_current_sentinel_missing_fails():
    sentinels = load_frozen_sentinel_evidence(Path(__file__).resolve().parents[1])
    current = [
        {
            "symbol": s["symbol"],
            "trade_date": parse_date(s["trade_date"]),
            "preclose": s["official_reference"],
        }
        for s in sentinels[:-1]
    ]
    result = verify_frozen_sentinels(sentinels, current)
    assert result["SENTINEL_MISSING_N"] == 1
    assert result["FROZEN_OFFICIAL_SENTINEL_PASS"] is False


def test_E_24_current_sentinels_exact_passes_runtime():
    sentinels = load_frozen_sentinel_evidence(Path(__file__).resolve().parents[1])
    current = [
        {
            "symbol": s["symbol"],
            "trade_date": parse_date(s["trade_date"]),
            "preclose": s["official_reference"],
        }
        for s in sentinels
    ]
    result = verify_frozen_sentinels(sentinels, current)
    assert result["SENTINEL_REQUIRED_N"] == 24
    assert result["SENTINEL_PRESENT_N"] == result["SENTINEL_EXACT_N"] == 24
    assert result["FROZEN_OFFICIAL_SENTINEL_PASS"] is True


def test_F_status0_status0_duplicate_is_duplicate():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 3), preclose=10.0, tradestatus="0"),
            _provider_row("000001.SZ", date(2016, 1, 3), preclose=10.0, tradestatus="0"),
        ]
    )
    assert result["counts"]["DUPLICATE"] == 1
    assert result["counts"]["PROVIDER_SUSPENDED_SUPERSET"] == 1


def test_G_status0_status1_same_pk_is_duplicate_and_fails():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0, tradestatus="0"),
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0, tradestatus="1"),
        ]
    )
    assert result["counts"]["DUPLICATE"] == 1
    counts = result["counts"]
    gate = quality_gate_pass(
        required_row_n=3,
        formal_fact_row_n=len(result["eligible_rows"]),
        missing_required_n=result["missing_required_n"],
        counts=counts,
        formal_rows=build_formal_facts(result["eligible_rows"], adapter_version="T", fetched_at="t"),
    )
    assert gate is False


def test_H_invalid_valid_same_pk_is_duplicate_and_fails():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose="not-a-number"),
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
        ]
    )
    assert result["counts"]["DUPLICATE"] == 1
    # The invalid row is independently classified before the duplicate sweep;
    # both INVALID_PRECLOSE (blocker) and DUPLICATE (blocker) may be nonzero.
    assert result["counts"]["INVALID_PRECLOSE"] >= 1
    gate = quality_gate_pass(
        required_row_n=3,
        formal_fact_row_n=0,
        missing_required_n=result["missing_required_n"],
        counts=result["counts"],
        formal_rows=build_formal_facts(result["eligible_rows"], adapter_version="T", fetched_at="t"),
    )
    assert gate is False


def test_I_unexpected_duplicate_is_duplicate_and_fails():
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 3), preclose=10.0, tradestatus="1"),
            _provider_row("000001.SZ", date(2016, 1, 3), preclose=10.0, tradestatus="1"),
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
        ]
    )
    assert result["counts"]["DUPLICATE"] == 1
    assert result["counts"]["UNEXPECTED_TRADED"] == 1


def test_J_clean_normal_previous_close_missing_is_uncompared_fail():
    rows = [
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 5), "preclose": 10.0},
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 6), "preclose": 10.0},
    ]
    result = verify_clean_normal_parity(
        formal_rows=rows,
        bars=_bars(),
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys=set(),
        known_special_keys=set(),
    )
    # 2016-01-05 is the first traded row: no local previous close.
    assert result["CLEAN_NORMAL_UNCOMPARED_N"] == 1
    assert result["NORMAL_FULL_PARITY_PASS"] is False


def test_K_all_clean_normal_comparable_exact_passes():
    result = _parity(set())
    assert result["CLEAN_NORMAL_REQUIRED_N"] == result["CLEAN_NORMAL_COMPARABLE_N"]
    assert result["CLEAN_NORMAL_UNCOMPARED_N"] == 0
    assert result["NORMAL_FULL_PARITY_PASS"] is True


def test_L_provider_row_outside_requested_window_fails():
    window = {"start": "2016-01-05", "end": "2016-01-07"}
    result = _normalize(
        [
            _provider_row("000001.SZ", date(2016, 1, 6), preclose=10.0),
            _provider_row("000001.SZ", date(2016, 2, 1), preclose=10.5),
        ]
    )
    # normalize without window context does not scope-check; the real path does.
    # Verify window-scope failure through the adapter-level check.
    from ashare_data.r4a_preclose_bounded_adapter import _identity_and_window_failure

    issue = _identity_and_window_failure(
        _provider_row("000001.SZ", date(2016, 2, 1), preclose=10.5),
        expected_code=_bs_code("000001.SZ"),
        requested_window=window,
    )
    assert issue == "WINDOW_SCOPE_FAILURE"
    assert result["counts"]["ELIGIBLE_REQUIRED_ROW"] == 1


def test_M_query_contract_change_changes_plan_hash():
    symbols = ["000001.SZ", "600000.SH"]
    base = build_query_plan(symbols, window_start=WINDOW_START, as_of=AS_OF)
    # A change to adjustflag/source-version contract must change the hash.
    plan = build_query_plan(symbols, window_start=WINDOW_START, as_of=AS_OF)
    original_hash = plan["QUERY_PLAN_HASH"]
    plan2 = build_query_plan(symbols, window_start=WINDOW_START, as_of=AS_OF)
    assert plan2["QUERY_PLAN_HASH"] == original_hash
    assert base["QUERY_PLAN_HASH"] == original_hash
    # monkeypatch a different contract value and rebuild
    import ashare_data.r4a_preclose_bounded_adapter as mod

    old = mod.QUERY_ADJUSTFLAG
    try:
        mod.QUERY_ADJUSTFLAG = "2"
        changed = build_query_plan(symbols, window_start=WINDOW_START, as_of=AS_OF)
        assert changed["QUERY_PLAN_HASH"] != original_hash
    finally:
        mod.QUERY_ADJUSTFLAG = old


def test_N_real_wrapper_fake_module_login_query_logout():
    fake = types.SimpleNamespace()

    class FakeResult:
        def __init__(self, rows):
            self.error_code = "0"
            self.rows = rows
            self._i = 0

        def next(self):
            if self._i >= len(self.rows):
                return False
            self._row = self.rows[self._i]
            self._i += 1
            return True

        def get_row_data(self):
            return self._row

    fake.login_result = types.SimpleNamespace(error_code="0", error_msg="")

    def _login():
        return fake.login_result

    def _query(code, fields, **kwargs):
        return FakeResult(
            [
                ["2016-01-06", code, "10.00", "1"],
                ["2016-01-07", code, "10.25", "1"],
            ]
        )

    def _logout():
        return types.SimpleNamespace(error_code="0")

    fake.login = _login
    fake.query_history_k_data_plus = _query
    fake.logout = _logout
    sys.modules["baostock"] = fake
    try:
        provider = BaostockSessionProvider()
        provider.login()
        rows = provider.query_history_k_data_plus(
            "sz.000001",
            "date,code,preclose,tradestatus",
            start_date="2016-01-01",
            end_date="2016-12-31",
            frequency="d",
            adjustflag="3",
        )
        provider.logout()
        assert len(rows) == 2
        assert rows[0][0] == "2016-01-06"
        assert rows[0][1] == "sz.000001"
        assert provider._logged_in is False
    finally:
        sys.modules.pop("baostock", None)


def test_real_wrapper_error_code_nonzero_fails():
    fake = types.SimpleNamespace()
    fake.login_result = types.SimpleNamespace(error_code="1", error_msg="boom")

    def _login():
        return fake.login_result

    fake.login = _login
    sys.modules["baostock"] = fake
    try:
        provider = BaostockSessionProvider()
        try:
            provider.login()
            raised = False
        except RuntimeError:
            raised = True
        assert raised
    finally:
        sys.modules.pop("baostock", None)


def test_adapter_authority_requires_exact_sha():
    ok = adapter_authority_status(
        "a" * 40,
        expected_sha="a" * 40,
        runtime_sha="a" * 40,
    )
    assert ok["ADAPTER_AUTHORITY_PASS"] is True
    assert ok["ADAPTER_AUTHORITY_MODE"] == "EXACT_SHA"
    mismatch = adapter_authority_status(
        "b" * 40,
        expected_sha="a" * 40,
        runtime_sha="a" * 40,
    )
    assert mismatch["ADAPTER_AUTHORITY_PASS"] is False
    fixture = adapter_authority_status("TEST", expected_sha=None, runtime_sha=None)
    assert fixture["ADAPTER_AUTHORITY_PASS"] is True
    assert fixture["ADAPTER_AUTHORITY_MODE"] == "OFFLINE_FIXTURE"
