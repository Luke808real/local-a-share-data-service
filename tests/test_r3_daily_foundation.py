"""R3 daily foundation control-plane tests.

These tests are bounded and offline: they exercise span grouping, stage-machine
transitions, the service ledger, and fail-closed guards against the real
playground/config. They never access a real data root, any legacy root, or the
market network.
"""

from __future__ import annotations

import inspect
import json
import os
from datetime import date, datetime, timezone
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
    identity = {"600000.SH"}

    def roster(day):
        return {"600000.SH"}

    receipt = roster_closure_receipt(days, roster, stock_basic_symbols=identity)
    assert receipt["closed"] is True
    assert receipt["expected_dates_n"] == 1
    assert receipt["success_dates_n"] == 1
    assert receipt["failed_dates_n"] == 0
    assert "union_symbol_hash" in receipt
    diff = receipt["stock_basic_vs_roster_diff"]
    assert diff["n_identity_not_in_roster"] == 0
    assert receipt["unresolved_n"] == 0


def test_roster_empty_day_not_closed():
    days = [R3_HISTORY_START, date(2016, 1, 4)]

    def empty_roster(day):
        return set()

    with pytest.raises(R3Error, match="NOT_CLOSED"):
        roster_closure_receipt(days, empty_roster, stock_basic_symbols=set())


def test_roster_unresolved_identity_residual_not_closed():
    days = [R3_HISTORY_START]

    def roster(day):
        return {"600000.SH"}

    with pytest.raises(R3Error, match="NOT_CLOSED"):
        roster_closure_receipt(
            days, roster, stock_basic_symbols={"600000.SH", "000001.SZ"}
        )


def test_roster_exact_reconciled_closed():
    days = [R3_HISTORY_START, date(2016, 1, 4)]
    identity = {"600000.SH", "000001.SZ"}

    def roster(day):
        return {"600000.SH", "000001.SZ"}

    receipt = roster_closure_receipt(days, roster, stock_basic_symbols=identity)
    assert receipt["closed"] is True
    assert receipt["unresolved_n"] == 0


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


def test_exit_gate_exposes_bj_historical_gate():
    blocked = v072_exit_verdict(BJ_HISTORICAL_AUTHORITY_VERDICT, None)
    assert blocked["BJ_HISTORICAL_GATE"] == "BLOCKED"
    assert blocked["R3_EXIT"] == "BLOCKED_BJ_HISTORICAL_IDENTITY"
    passed = v072_exit_verdict("PROVEN", 0)
    assert passed["BJ_HISTORICAL_GATE"] == "PASS"


def test_stage_e_formal_delisted_present_in_instruments_is_target():
    formal = {"AX0001.SH": date(2019, 12, 31), "BX0001.SH": date(2018, 6, 30)}
    catalog = {}
    instruments = pl.DataFrame(
        {
            "symbol": ["AX0001.SH", "CUR.SH", "NEW.BJ"],
            "name": ["X", "C", "N"],
            "exchange": ["SH", "SH", "BJ"],
            "asset_type": ["stock", "stock", "stock"],
            "list_date": [date(2000, 1, 1), date(2020, 1, 1), date(2024, 1, 1)],
            "delist_date": [date(2019, 12, 31), None, None],
            "prev_symbol": [None, None, None],
            "source": ["sina", "tdx_protocol", "sina"],
            "data_version": ["v1", "v1", "v1"],
            "fetched_at": [datetime(2026, 1, 1, tzinfo=timezone.utc)] * 3,
        }
    )
    part = r3.stage_e_target_partition(
        formal, catalog, instruments, R3_DAILY_AS_OF, R3_HISTORY_START
    )
    assert "AX0001.SH" in part["targets"]
    assert "AX0001.SH" in part["recover"]
    assert part["sina_catalog_role"] == "CROSSCHECK_ONLY"


def test_c2_builds_bj_from_stage_b_receipt(monkeypatch, tmp_path):
    import types

    meta = tmp_path / "asl" / "r3"
    meta.mkdir(parents=True)
    membership = [
        {
            "symbol": "920001.BJ", "name": "BJ ONE", "exchange": "BJ",
            "asset_type": "stock", "list_date": date(2025, 1, 1), "delist_date": None,
        },
        {
            "symbol": "920002.BJ", "name": "BJ TWO", "exchange": "BJ",
            "asset_type": "stock", "list_date": date(2025, 3, 1), "delist_date": None,
        },
    ]
    (meta / "r3-identity-receipt.json").write_text(
        json.dumps({"bj_current_membership": membership}, default=str)
    )

    non_bj = pl.DataFrame(
        {
            "symbol": ["600000.SH", "000001.SZ"],
            "name": ["A", "B"],
            "exchange": ["SH", "SZ"],
            "asset_type": ["stock", "stock"],
            "list_date": [date(2000, 1, 1), date(2000, 1, 1)],
            "delist_date": [None, None],
            "prev_symbol": [None, None],
            "source": ["tdx_protocol", "tdx_protocol"],
            "data_version": ["v1", "v1"],
            "fetched_at": [datetime(2026, 1, 1, tzinfo=timezone.utc)] * 2,
        }
    )
    runner = object.__new__(r3.R3Runner)
    runner.cfg = types.SimpleNamespace(
        manifest_path=tmp_path / "manifest.db",
        staging_root=tmp_path / "staging",
    )
    runner.meta = meta
    runner.daily_as_of = R3_DAILY_AS_OF
    runner.history_start = R3_HISTORY_START
    runner._new_run = lambda job: "c2rid"  # type: ignore[attr-defined]

    manifest_calls: list = []
    writer_calls: list = []
    monkeypatch.setattr(r3, "load_curated_instruments", lambda cfg: non_bj)
    monkeypatch.setattr(
        r3, "Manifest", lambda *a, **k: FakeManifest(manifest_calls)
    )
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: FakeWriter(writer_calls))
    monkeypatch.setattr(r3.R3Runner, "_compact", lambda self, rid: {"status": "success"})

    result = runner._enrich_bj_metadata()
    assert result["bj_rows"] == 2
    assert writer_calls and writer_calls[-1][0] == "c2-enrich-bj"
    called_symbols = set(manifest_calls[0][2]["symbols"])
    assert "920001.BJ" in called_symbols and "920002.BJ" in called_symbols


def test_unexplained_missing_out_of_span_only(monkeypatch):
    runner = object.__new__(r3.R3Runner)
    runner.cfg = type(
        "CfgStub",
        (),
        {
            "manifest_path": Path("/nonexistent/manifest.db"),
            "staging_root": Path("/nonexistent/staging"),
        },
    )()

    class Ledger3:
        def __init__(self):
            self.calls = []

        def append(self, r):
            if r.get("event") == "ATTEMPT_START" and r["symbol"] == "920001.BJ":
                self.calls.append(r)

    ledger = Ledger3()
    runner.ledger = ledger
    writer_calls: list = []
    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: FakeManifest([]))
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: FakeWriter(writer_calls))
    # wrapper returns EXISTS but all rows are OUTSIDE the requested span
    out_of_span = _bars_frame()
    out_of_span = out_of_span.with_columns(
        pl.lit(date(2030, 1, 1)).alias("trade_date")
    )

    def exists_out_of_span(symbol, start, end, config=None):
        return {"state": "EXISTS", "reason": "valid_bars", "symbol": symbol, "frame": out_of_span}

    monkeypatch.setattr(r3, "em_daily_tristate", exists_out_of_span)
    with pytest.raises(R3Error, match="UNEXPLAINED_MISSING"):
        runner._em_primary_route("rid", {"920001.BJ": (R3_HISTORY_START, R3_DAILY_AS_OF)})
    assert not writer_calls  # nothing staged
    assert len(ledger.calls) >= 3  # exact retries with ATTEMPT_START lineage


def test_preflight_readonly_filesystem_and_env(monkeypatch, tmp_path):
    import types
    import inspect

    root = tmp_path / "target-root"
    root.mkdir()
    (root / "curated").mkdir()
    proxies_before = {
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "socks5h://127.0.0.1:9",
        "NO_PROXY": "localhost",
    }
    for k, v in proxies_before.items():
        os.environ[k] = v
    before_tree = sorted(p.name for p in root.rglob("*"))

    class DummyVfs:
        f_bavail = 3 * 2**20
        f_frsize = 64 * 1024

    runner = object.__new__(r3.R3Runner)
    runner.cfg = types.SimpleNamespace(
        data_root=root,
        workers=1,
        tdx_allow_mock=False,
        manifest_path=root / "meta" / "manifest.db",
        meta_root=root / "meta",
        minute_bars_enabled=False,
        minute_bars_frequencies=[],
        trade_ticks_enabled=False,
    )
    runner.root = root
    runner.meta = root / "meta" / "asl" / "r3"
    runner.state_path = runner.meta / "execution-state.json"
    runner.plan_sha = PLAN_SHA
    runner.repo_root = tmp_path
    runner.config_path = Path("/nonexistent/config/cnequity.toml")
    runner.machine = StageMachine(runner.state_path)
    monkeypatch.setattr(r3, "runtime_provenance", lambda cfg, cp: {"ok": True})
    monkeypatch.setattr(
        "cnequity.domain.datasets.is_dataset_enabled",
        lambda name, cfg: False,
    )
    monkeypatch.setattr(os, "statvfs", lambda p: DummyVfs())
    monkeypatch.setattr(
        r3, "git_sha", lambda p: "0" * 40
    )
    monkeypatch.setattr(
        r3, "target_tree_snapshot",
        lambda rootp, exclude=None: {
            "digest": "0" * 64,
            "entries": 0,
            "lines": [],
        },
    )
    monkeypatch.setattr(r3, "zero_data_layout_errors", lambda rootp: [])
    monkeypatch.setattr(
        r3.R3Runner, "_check_legacy_isolation", lambda self: None
    )
    monkeypatch.setattr(
        r3.R3Runner, "_check_argv_surface", lambda self, argv: None
    )

    receipt = runner.preflight()
    after_tree = sorted(p.name for p in root.rglob("*"))
    assert before_tree == after_tree  # filesystem unchanged
    for k, v in proxies_before.items():
        assert os.environ.get(k) == v  # proxy env unchanged
    assert receipt["plan_sha"] == PLAN_SHA
