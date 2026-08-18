"""R3 daily foundation control-plane tests.

These tests are bounded and offline: they exercise span grouping, stage-machine
transitions, the service ledger, and fail-closed guards against the real
playground/config. They never access a real data root, any legacy root, or the
market network.
"""

from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import polars as pl
import pytest

import ashare_data.r3_daily as r3
from ashare_data.r3_daily import (
    BASE_HEAD,
    BJ_HISTORICAL_AUTHORITY_VERDICT,
    CONFIG_SHA,
    HISTORICAL_DELISTED_BJ_LABEL,
    LOCK_SHA,
    PINNED_CNEQUITY_SHA,
    PINNED_CNEQUITY_VERSION,
    PLAN_SHA,
    R3_DAILY_AS_OF,
    R3_HISTORY_START,
    R3Error,
    ServiceLedger,
    StageMachine,
    em_daily_tristate,
    effective_span,
    group_by_span,
    roster_closure_receipt,
    v072_exit_verdict,
)


def test_frozen_constants_consistent():
    assert R3_HISTORY_START == date(2016, 1, 1)
    assert R3_DAILY_AS_OF == date(2026, 8, 17)
    assert not R3_DAILY_AS_OF >= date.today()
    assert BASE_HEAD == "0254122a99f0a365d2be12f29a2a59b951497fd3"
    assert PLAN_SHA == "3ab1f184edeea1d0e408c45df4a706248b6558d0"
    assert len(PLAN_SHA) == 40
    assert PINNED_CNEQUITY_SHA == "a18ee0484dfb0801650175471724def3228b8a17"
    assert PINNED_CNEQUITY_VERSION == "0.7.2"
    assert len(LOCK_SHA) == 64
    assert len(CONFIG_SHA) == 64


def test_effective_span():
    assert effective_span(None, None) == (R3_HISTORY_START, R3_DAILY_AS_OF)
    assert effective_span(date(2021, 8, 1), None)[0] == date(2021, 8, 1)
    assert effective_span(None, date(2019, 12, 31))[1] == date(2019, 12, 31)
    assert effective_span(date(2027, 1, 1), None) is None  # 上市晚于 as_of
    assert effective_span(None, date(2015, 12, 31)) is None  # 退市早于窗口


def test_group_by_span_partitions_by_identity():
    spans = {
        "A.SH": (date(2016, 1, 1), date(2026, 8, 17)),
        "B.SZ": (date(2016, 1, 1), date(2026, 8, 17)),
        "C.BJ": (date(2021, 8, 1), date(2026, 8, 17)),
    }
    groups = group_by_span(spans)
    spans_to_symbols = {span: symbols for span, symbols in groups}
    assert set(spans_to_symbols[(date(2016, 1, 1), date(2026, 8, 17))]) == {"A.SH", "B.SZ"}
    assert spans_to_symbols[(date(2021, 8, 1), date(2026, 8, 17))] == ["C.BJ"]


def test_stage_machine_progression(tmp_path):
    machine = StageMachine(tmp_path / "state.json")
    assert machine.load()["status"] == "pending"
    machine.enter("A_instruments")
    machine.complete("A_instruments", {"ok": True})
    machine.enter("B_discovery")
    machine.complete("B_discovery", {"ok": True})
    state = machine.load()
    assert state["completed"] == ["A_instruments", "B_discovery"]
    with pytest.raises(R3Error, match="STAGE_ALREADY_COMPLETE"):
        machine.enter("A_instruments")


def test_stage_machine_refuses_rewind(tmp_path):
    machine = StageMachine(tmp_path / "state.json")
    machine.enter("C_merge")
    machine.complete("C_merge", {})
    with pytest.raises(R3Error, match="STAGE_ORDER"):
        machine.enter("B_discovery")


def test_service_ledger_roundtrip(tmp_path):
    ledger = ServiceLedger(tmp_path / "asl" / "ledger.jsonl")
    ledger.append({"stage": "B_discovery", "attempt": 1, "remaining": 10})
    ledger.append({"stage": "B_discovery", "attempt": 2, "remaining": 0})
    rows = ledger.read()
    assert len(rows) == 2
    assert rows[0]["stage"] == "B_discovery"
    assert all("record_id" in row for row in rows)
    assert ledger.symbols_hash(["B", "A", "B"]) == ledger.symbols_hash(["A", "B"])


def test_forbidden_surface_guard():
    # plain method; does not read instance state
    r3.R3Runner._check_argv_surface(
        None, ["uv", "run", "--frozen", "python", "tools/run_r3_daily_foundation.py", "--stage", "F_daily"]
    )
    with pytest.raises(R3Error, match="FORBIDDEN_SURFACE"):
        r3.R3Runner._check_argv_surface(None, ["cne init --config x.toml"])
    with pytest.raises(R3Error, match="FORBIDDEN_SURFACE"):
        r3.R3Runner._check_argv_surface(None, ["cne backfill minute_bars --config x"])
    with pytest.raises(R3Error, match="FORBIDDEN_SURFACE"):
        r3.R3Runner._check_argv_surface(None, ["cne audit --config x"])


def test_legacy_isolation_guard():
    runner = object.__new__(r3.R3Runner)
    runner.root = Path("/tmp/asl-r3-isolation-test-nonlegacy")
    runner._check_legacy_isolation()
    bad = object.__new__(r3.R3Runner)
    bad.root = Path("/Users/luke808/AI/asl-shared/child")
    with pytest.raises(R3Error, match="LEGACY_ISOLATION"):
        bad._check_legacy_isolation()


def test_r3_module_import_smoke():
    assert hasattr(r3, "R3Runner")
    assert hasattr(r3, "PLAN_SHA")


# ============================================================================
# V07.2 tri-state wrapper / closure / exit-gate contract
# ============================================================================


def _bars_frame(symbol: str = "920001.BJ", rows: int = 3) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol] * rows,
            "trade_date": [date(2026, 8, 17 - i) for i in range(rows)],
            "open": [10.0 + i for i in range(rows)],
            "high": [11.0 + i for i in range(rows)],
            "low": [9.0 + i for i in range(rows)],
            "close": [10.5 + i for i in range(rows)],
            "volume": [1000 + i for i in range(rows)],
        }
    )


def test_plan_sha_is_v072(monkeypatch):
    assert PLAN_SHA == "3ab1f184edeea1d0e408c45df4a706248b6558d0"


def test_known_bj_valid_bars_exists():
    fetcher = lambda symbols, start, end, config=None: _bars_frame(symbols[0])  # noqa: E731
    res = em_daily_tristate("920001.BJ", R3_HISTORY_START, R3_DAILY_AS_OF, fetcher=fetcher)
    assert res["state"] == "EXISTS"
    assert res["reason"] == "valid_bars"


def test_known_bj_empty_is_source_error_empty_known_symbol():
    fetcher = lambda symbols, start, end, config=None: pl.DataFrame()  # noqa: E731
    res = em_daily_tristate("920001.BJ", R3_HISTORY_START, R3_DAILY_AS_OF, fetcher=fetcher)
    assert res["state"] == "SOURCE_ERROR"
    assert res["reason"] == "EMPTY_KNOWN_SYMBOL"


def test_known_bj_invalid_is_source_error_invalid_response():
    bad = _bars_frame().drop("volume")
    fetcher = lambda symbols, start, end, config=None: bad  # noqa: E731
    res = em_daily_tristate("920001.BJ", R3_HISTORY_START, R3_DAILY_AS_OF, fetcher=fetcher)
    assert res["state"] == "SOURCE_ERROR"
    assert res["reason"] == "INVALID_KNOWN_SYMBOL_RESPONSE"


def test_known_bj_exception_is_source_error():
    def raiser(*_a, **_k):
        raise RuntimeError("http 500")

    res = em_daily_tristate("920001.BJ", R3_HISTORY_START, R3_DAILY_AS_OF, fetcher=raiser)
    assert res["state"] == "SOURCE_ERROR"


def test_known_bj_wrapper_never_not_exists():
    def raiser(*_a, **_k):
        raise RuntimeError("boom")

    bad = _bars_frame().drop("volume")
    fetchers = [
        lambda s, st, en, config=None: pl.DataFrame(),
        lambda s, st, en, config=None: bad,
        raiser,
        lambda s, st, en, config=None: None,
    ]
    for fetcher in fetchers:
        res = em_daily_tristate("920001.BJ", R3_HISTORY_START, R3_DAILY_AS_OF, fetcher=fetcher)
        assert res["state"] != "NOT_EXISTS"


def test_wrapper_never_returns_unexplained_missing():
    wrappers = [
        lambda symbols, st, en, config=None: _bars_frame(symbols[0]),
        lambda s, st, en, config=None: pl.DataFrame(),
        lambda s, st, en, config=None: (_ for _ in ()).throw(RuntimeError("x")),
    ]
    for fetcher in wrappers:
        res = em_daily_tristate("920001.BJ", R3_HISTORY_START, R3_DAILY_AS_OF, fetcher=fetcher)
        assert res["state"] != "UNEXPLAINED_MISSING"


def test_unexplained_missing_only_after_retries_via_classifier(monkeypatch):
    runner = object.__new__(r3.R3Runner)
    runner.cfg = type(
        "CfgStub",
        (),
        {
            "manifest_path": Path("/nonexistent/manifest.db"),
            "staging_root": Path("/nonexistent/staging"),
        },
    )()

    class FakeLedger:
        def append(self, record):
            calls.append(record)

    calls: list = []
    runner.ledger = FakeLedger()
    manifest_calls = []
    writer_calls = []
    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: FakeManifest(manifest_calls))
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: FakeWriter(writer_calls))
    monkeypatch.setattr(
        r3, "em_daily_tristate",
        lambda symbol, start, end, config=None: {
            "state": "SOURCE_ERROR", "reason": "EMPTY_KNOWN_SYMBOL", "symbol": symbol, "frame": None,
        },
    )
    with pytest.raises(R3Error, match="UNEXPLAINED_MISSING"):
        runner._em_primary_route("r3x", {"920001.BJ": (R3_HISTORY_START, R3_DAILY_AS_OF)})
    # unwrapped logging happened -> classifier raised after exact retries
    assert len([c for c in calls if c.get("state") == "SOURCE_ERROR"]) >= 3


class FakeManifest:
    def __init__(self, calls):
        self.calls = calls

    def start_batch(self, *args, **kwargs):
        self.calls.append(("start", args, kwargs))

    def finish_batch(self, *args, **kwargs):
        self.calls.append(("finish", args, kwargs))


class FakeWriter:
    def __init__(self, calls):
        self.calls = calls

    def write_batch(self, dataset, run_id, batch_id, frame):
        self.calls.append((batch_id, frame.height))


def test_stage_b_never_calls_sina_discovery(monkeypatch):
    src = inspect.getsource(r3.R3Runner.stage_discovery) + inspect.getsource(
        r3.R3Runner._identity_completion_v072
    )
    assert "discover_delisted" not in src
    for forbidden in ("fetch_daily_bars_sina", "fetch_bars_via_sina", "sina.bars", "quotes_service"):
        assert forbidden not in src
    assert not hasattr(r3, "discover_delisted")


def test_failed_roster_dates_means_not_closed():
    days = [R3_HISTORY_START, date(2016, 1, 4), date(2016, 1, 5)]

    def broken_roster(day):
        if day == days[1]:
            raise RuntimeError("roster failed")
        return {f"{i:06d}.SH" for i in range(2)}

    with pytest.raises(R3Error, match="NOT_CLOSED"):
        roster_closure_receipt(days, broken_roster, stock_basic_symbols=set())


def test_roster_closure_receipt_splits_authority():
    days = [R3_HISTORY_START]
    identity = {"600000.SH", "000001.SZ"}

    def roster(day):
        return {"600000.SH"}

    receipt = roster_closure_receipt(days, roster, stock_basic_symbols=identity)
    assert receipt["closed"] is True
    assert receipt["expected_dates_n"] == 1
    assert receipt["success_dates_n"] == 1
    assert receipt["failed_dates_n"] == 0
    assert "union_symbol_hash" in receipt
    diff = receipt["stock_basic_vs_roster_diff"]
    assert diff["n_identity_not_in_roster"] == 1
    assert "000001.SZ" in diff["identity_not_in_roster"]


def test_stage_e_never_calls_fetch_daily_bars_sina():
    src = inspect.getsource(r3.R3Runner._recover_delisted_daily)
    assert "fetch_daily_bars_sina" not in src
    assert not hasattr(r3, "fetch_daily_bars_sina")


def test_f2_never_calls_fetch_bars_via_sina():
    src = inspect.getsource(r3.R3Runner._em_primary_route)
    assert "fetch_bars_via_sina" not in src
    assert not hasattr(r3, "fetch_bars_via_sina")


def test_f2_primary_is_eastmoney_wrapper(monkeypatch):
    runner = object.__new__(r3.R3Runner)
    runner.cfg = type(
        "CfgStub",
        (),
        {
            "manifest_path": Path("/nonexistent/manifest.db"),
            "staging_root": Path("/nonexistent/staging"),
        },
    )()
    manifest_calls: list = []
    writer_calls: list = []
    ledger_calls: list = []

    class Ledger2:
        def append(self, r):
            ledger_calls.append(r)

    runner.ledger = Ledger2()
    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: FakeManifest(manifest_calls))
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: FakeWriter(writer_calls))

    def ok_fetcher(symbol, start, end, config=None):
        return _bars_frame(symbol)

    monkeypatch.setattr(r3, "em_daily_tristate", ok_fetcher_state := lambda s, st, en, config=None: {
        "state": "EXISTS", "reason": "valid_bars", "symbol": s,
        "frame": _bars_frame(s),
    })
    result = runner._em_primary_route("rid", {"920001.BJ": (R3_HISTORY_START, R3_DAILY_AS_OF)})
    assert result["route"] == "eastmoney_primary"
    assert result["unexplained_after"] == 0
    assert any(c[0] == "em-" for c in writer_calls) or writer_calls  # staged


def test_unknown_carried_means_daily_ready_false():
    verdict = v072_exit_verdict(BJ_HISTORICAL_AUTHORITY_VERDICT, None)
    assert verdict["DAILY_READY"] is False
    assert verdict["R3_EXIT"] == "BLOCKED_BJ_HISTORICAL_IDENTITY"
    assert verdict["R4_EXECUTION"] == "FORBIDDEN"
    assert HISTORICAL_DELISTED_BJ_LABEL == "UNKNOWN_CARRIED"


def test_unknown_carried_blocks_exit():
    verdict = v072_exit_verdict(BJ_HISTORICAL_AUTHORITY_VERDICT, 3)
    assert verdict["DAILY_READY"] is False
    assert verdict["R3_EXIT"] == "BLOCKED_BJ_HISTORICAL_IDENTITY"


def test_exit_requires_zero_unresolved():
    # unresolved=None (null) must not count as 0
    assert v072_exit_verdict("PROVEN", None)["DAILY_READY"] is False
    assert v072_exit_verdict("PROVEN", 0)["DAILY_READY"] is True
