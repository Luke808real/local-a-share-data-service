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


def test_stage_machine_refuses_forward_skip(tmp_path):
    machine = StageMachine(tmp_path / "state.json")
    with pytest.raises(R3Error, match="STAGE_PREREQUISITE"):
        machine.enter("B_discovery")


def test_stage_machine_strict_prefix_order(tmp_path):
    order = [
        "A_instruments", "B_discovery", "C_merge", "C2_enrich",
        "D_calendar", "E_delisted", "F_daily", "G_coverage",
    ]
    machine = StageMachine(tmp_path / "state2.json")
    for stage in order:
        machine.enter(stage)
        machine.complete(stage, {})
    state = machine.load()
    assert state["completed"] == order
    # trying any prior stage again is a prerequisite violation (already complete)
    with pytest.raises(R3Error, match="STAGE_ALREADY_COMPLETE"):
        machine.enter("C_merge")


def test_stage_machine_missing_immediate_predecessor(tmp_path):
    machine = StageMachine(tmp_path / "state3.json")
    machine.enter("A_instruments")
    machine.complete("A_instruments", {})
    # A done but B missing: C skipped forward => blocked
    with pytest.raises(R3Error, match="STAGE_PREREQUISITE"):
        machine.enter("C_merge")
    # exact prefix allows B
    machine.enter("B_discovery")
    machine.complete("B_discovery", {})
    machine.enter("C_merge")


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
        self.status_by_batch = {}
        self.superseded = []

    def start_batch(self, *args, **kwargs):
        batch_id = kwargs.get("batch_id") or (args[1] if len(args) > 1 else None)
        if batch_id:
            self.status_by_batch[batch_id] = "running"
        self.calls.append(("start", args, kwargs))

    def finish_batch(self, *args, **kwargs):
        if args and len(args) >= 3:
            batch_id, status = args[1], args[2]
            self.status_by_batch[batch_id] = status
        self.calls.append(("finish", args, kwargs))

    def supersede_batches(self, run_id, batch_ids, *, superseded_by):
        count = 0
        for bid in batch_ids:
            if self.status_by_batch.get(bid) in ("failed", "warning", "stale"):
                self.status_by_batch[bid] = "superseded"
                count += 1
        self.superseded.append({"by": superseded_by, "ids": list(batch_ids)})
        return count

    def incomplete_blocking(self):
        return sum(
            1 for s in self.status_by_batch.values() if s in ("failed", "warning", "stale")
        )

    def incomplete_batch_counts_by_dataset(self, run_id):
        return {
            "daily_bars": sum(
                1
                for s in self.status_by_batch.values()
                if s in ("failed", "warning", "stale", "running")
            )
        }


class FakeWriter:
    def __init__(self, calls):
        self.calls = calls

    def write_batch(self, dataset, run_id, batch_id, frame):
        self.calls.append((batch_id, frame))


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

    receipt = roster_closure_receipt(days, broken_roster, stock_basic_symbols=set())
    assert receipt["closed"] is False
    assert receipt["failed_dates_n"] == 1
    assert receipt["unresolved_n"] == 3  # 1 failed date + 2 roster-only symbols


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
    assert receipt["identity_not_in_roster_n"] == 0
    assert receipt["roster_not_in_identity_n"] == 0
    assert receipt["unresolved_n"] == 0


def test_roster_empty_day_not_closed():
    days = [R3_HISTORY_START, date(2016, 1, 4)]

    def empty_roster(day):
        return set()

    receipt = roster_closure_receipt(days, empty_roster, stock_basic_symbols=set())
    assert receipt["closed"] is False
    assert receipt["failed_dates_n"] == 2


def test_roster_unresolved_identity_residual_not_closed():
    days = [R3_HISTORY_START]

    def roster(day):
        return {"600000.SH"}

    receipt = roster_closure_receipt(
        days, roster, stock_basic_symbols={"600000.SH", "000001.SZ"}
    )
    assert receipt["closed"] is False
    assert receipt["identity_not_in_roster_n"] == 1
    assert receipt["roster_not_in_identity_n"] == 0
    assert receipt["identity_not_in_roster_sample"] == ["000001.SZ"]
    assert receipt["identity_not_in_roster_hash"] != "0" * 64


def test_roster_exact_reconciled_closed():
    days = [R3_HISTORY_START, date(2016, 1, 4)]
    identity = {"600000.SH", "000001.SZ"}

    def roster(day):
        return {"600000.SH", "000001.SZ"}

    receipt = roster_closure_receipt(days, roster, stock_basic_symbols=identity)
    assert receipt["closed"] is True
    assert receipt["unresolved_n"] == 0


def test_roster_union_unknown_not_closed():
    days = [R3_HISTORY_START]

    def roster(day):
        return {"UNKNOWN.SH"}

    receipt = roster_closure_receipt(days, roster, stock_basic_symbols={"600000.SH"})
    assert receipt["closed"] is False
    assert receipt["roster_not_in_identity_n"] == 1
    assert "UNKNOWN.SH" in receipt["roster_not_in_identity_sample"]


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
    assert verdict["BJ_HISTORICAL_GATE"] == "BLOCKED"
    assert verdict["blocker"] == "HISTORICAL_DELISTED_BJ_UNKNOWN_CARRIED"
    assert verdict["R4_EXECUTION"] == "FORBIDDEN"
    assert HISTORICAL_DELISTED_BJ_LABEL == "UNKNOWN_CARRIED"


def test_unknown_carried_blocks_exit():
    verdict = v072_exit_verdict(BJ_HISTORICAL_AUTHORITY_VERDICT, 3)
    assert verdict["BJ_HISTORICAL_GATE"] == "BLOCKED"
    assert verdict["bj_historical_unresolved_n"] == 3
    assert "DAILY_READY" not in verdict
    assert "R3_EXIT" not in verdict


def test_exit_requires_zero_unresolved():
    # unresolved=None (null) must not count as 0
    assert v072_exit_verdict("PROVEN", None)["BJ_HISTORICAL_GATE"] == "BLOCKED"
    assert v072_exit_verdict("PROVEN", 0)["BJ_HISTORICAL_GATE"] == "PASS"


def test_exit_gate_exposes_bj_historical_gate():
    blocked = v072_exit_verdict(BJ_HISTORICAL_AUTHORITY_VERDICT, None)
    assert blocked["BJ_HISTORICAL_GATE"] == "BLOCKED"
    assert "DAILY_READY" not in blocked
    passed = v072_exit_verdict("PROVEN", 0)
    assert passed["BJ_HISTORICAL_GATE"] == "PASS"
    assert "DAILY_READY" not in passed
    assert passed["blocker"] is None


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

    class LedgerC2:
        def append(self, rec):
            return None

    runner.ledger = LedgerC2()

    manifest_calls: list = []
    writer_calls: list = []
    membership_df = pl.DataFrame(
        [
            {
                "symbol": m["symbol"], "name": m["name"], "exchange": "BJ",
                "asset_type": "stock", "list_date": m["list_date"],
                "delist_date": None, "prev_symbol": None,
                "source": "eastmoney", "data_version": "v1",
                "fetched_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
            }
            for m in membership
        ]
    )
    monkeypatch.setattr(
        r3, "load_curated_instruments",
        lambda cfg: pl.concat([non_bj, membership_df], how="diagonal_relaxed"),
    )
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


def _stage_b_runner(tmp_path, monkeypatch, *, close=True) -> "object":
    import types

    runner = object.__new__(r3.R3Runner)
    runner.meta = tmp_path / "asl" / "r3"
    runner.meta.mkdir(parents=True)
    runner.daily_as_of = R3_DAILY_AS_OF
    runner.history_start = R3_HISTORY_START
    runner.cfg = types.SimpleNamespace()

    class LedgerX:
        def __init__(self):
            self.calls = []

        def append(self, rec):
            self.calls.append(rec)

    runner.ledger = LedgerX()
    monkeypatch.setattr(r3.R3Runner, "_prepare_network_env", lambda self: None)
    monkeypatch.setattr(r3, "list_trading_dates", lambda cfg, s, e: [R3_HISTORY_START])

    def fake_basics():
        return pl.DataFrame(
            {
                "symbol": ["600000.SH", "600001.SH"],
                "name": ["A", "B"],
                "exchange": ["SH", "SH"],
                "asset_type": ["stock", "stock"],
                "list_date": [date(2000, 1, 1), date(2000, 1, 1)],
                "delist_date": [None, date(2019, 12, 31)] if not close else [None, date(2019, 12, 31)],
                "prev_symbol": [None, None],
            }
        )

    monkeypatch.setattr(
        "cnequity.adapters.baostock.instruments.fetch_instrument_basics",
        fake_basics,
    )

    def fake_roster(day):
        return {"600000.SH", "600001.SH"} if close else {"600000.SH"}

    monkeypatch.setattr(
        "cnequity.adapters.baostock.delisted_bars.roster_on",
        fake_roster,
    )

    class FakeClient:
        def close(self):
            return None

    monkeypatch.setattr(
        "cnequity.adapters.eastmoney.em_auth.EastMoneyClient",
        lambda config=None: FakeClient(),
    )
    monkeypatch.setattr(
        "cnequity.adapters.eastmoney.clist.fetch_clist_pages",
        lambda client, fields: [{"f12": "920001", "f13": 2, "f14": "BJ ONE", "f26": "20240101"}],
    )
    monkeypatch.setattr(
        "cnequity.adapters.eastmoney.clist.clist_rows_to_symbols",
        lambda rows: [("920001.BJ", rows[0])],
    )
    return runner


def test_stage_b_not_closed_persists_receipt(monkeypatch, tmp_path):
    runner = _stage_b_runner(tmp_path, monkeypatch, close=False)
    monkeypatch.setattr(
        r3, "known_delisted_instruments",
        lambda cfg, asof: {"600001.SH": date(2019, 12, 31)},
    )
    with pytest.raises(R3Error, match="NOT_CLOSED"):
        runner._identity_completion_v072()
    receipt_path = runner.meta / "r3-identity-receipt.json"
    assert receipt_path.exists()  # persisted even on NOT_CLOSED
    payload = json.loads(receipt_path.read_text())
    closure = payload["shsz_closure"]
    assert closure["closed"] is False
    assert closure["roster_not_in_identity_n"] == 0
    assert closure["identity_not_in_roster_n"] in (0, 1)
    assert "identity_not_in_roster_hash" in closure


def test_stage_b_formal_identity_drift_persists(monkeypatch, tmp_path):
    runner = _stage_b_runner(tmp_path, monkeypatch, close=True)
    # Stage-A evidence disagrees with the fresh stock_basic map
    monkeypatch.setattr(
        r3, "known_delisted_instruments",
        lambda cfg, asof: {"600002.SH": date(2020, 6, 30)},
    )
    with pytest.raises(R3Error, match="FORMAL_IDENTITY_DRIFT"):
        runner._identity_completion_v072()
    payload = json.loads((runner.meta / "r3-identity-receipt.json").read_text())
    drift = payload["formal_drift"]
    assert drift["extra_n"] >= 1 or drift["missing_n"] >= 1
    assert "shsz_formal_delisted_hash" in payload


def test_stage_e_uses_persisted_formal_map(monkeypatch, tmp_path):
    runner = object.__new__(r3.R3Runner)
    runner.meta = tmp_path / "asl" / "r3"
    runner.meta.mkdir(parents=True)
    (runner.meta / "r3-identity-receipt.json").write_text(
        json.dumps({"shsz_formal_delisted": {"600001.SH": "2019-12-31"}})
    )
    formal_map = runner._load_shsz_formal_map()
    assert formal_map == {"600001.SH": date(2019, 12, 31)}
    src = inspect.getsource(r3.R3Runner._recover_delisted_daily)
    assert "self._load_shsz_formal_map()" in src


def test_c2_no_nonbj_foundation_blocked(monkeypatch, tmp_path):
    import types

    meta = tmp_path / "asl" / "r3"
    meta.mkdir(parents=True)
    (meta / "r3-identity-receipt.json").write_text(
        json.dumps(
            {
                "bj_current_membership": [
                    {"symbol": "920001.BJ", "name": "X", "exchange": "BJ",
                     "asset_type": "stock", "list_date": "2025-01-01", "delist_date": None}
                ]
            },
            default=str,
        )
    )
    runner = object.__new__(r3.R3Runner)
    runner.meta = meta
    runner.daily_as_of = R3_DAILY_AS_OF
    runner.cfg = types.SimpleNamespace()
    # curated instruments contain ONLY BJ (no SH/SZ foundation)
    bj_only = pl.DataFrame(
        {
            "symbol": ["920999.BJ"],
            "name": ["Y"],
            "exchange": ["BJ"],
            "asset_type": ["stock"],
            "list_date": [date(2020, 1, 1)],
            "delist_date": [None],
            "prev_symbol": [None],
            "source": ["eastmoney"],
            "data_version": ["v1"],
            "fetched_at": [datetime(2026, 1, 1, tzinfo=timezone.utc)],
        }
    )
    monkeypatch.setattr(r3, "load_curated_instruments", lambda cfg: bj_only)
    with pytest.raises(R3Error, match="BLOCKED_ALL_A_UNIVERSE"):
        runner._enrich_bj_metadata()


def test_c2_nonbj_provenance_unchanged(monkeypatch, tmp_path):
    import types

    meta = tmp_path / "asl" / "r3"
    meta.mkdir(parents=True)
    (meta / "r3-identity-receipt.json").write_text(
        json.dumps(
            {
                "bj_current_membership": [
                    {"symbol": "920001.BJ", "name": "X", "exchange": "BJ",
                     "asset_type": "stock", "list_date": "2025-01-01",
                     "delist_date": None, "prev_symbol": None}
                ]
            },
            default=str,
        )
    )
    non_bj = pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "name": ["A"],
            "exchange": ["SH"],
            "asset_type": ["stock"],
            "list_date": [date(2000, 1, 1)],
            "delist_date": [None],
            "prev_symbol": [None],
            "source": ["tdx_protocol"],
            "data_version": ["v2"],
            "fetched_at": [datetime(2026, 2, 1, tzinfo=timezone.utc)],
        }
    )
    runner = object.__new__(r3.R3Runner)
    runner.meta = meta
    runner.daily_as_of = R3_DAILY_AS_OF
    runner.history_start = R3_HISTORY_START
    runner.cfg = types.SimpleNamespace(
        manifest_path=tmp_path / "m.db",
        staging_root=tmp_path / "staging",
    )
    runner._new_run = lambda job: "cid"
    manifest_calls = []
    writer_calls = []
    bj_member_df = pl.DataFrame(
        [
            {
                "symbol": "920001.BJ", "name": "X", "exchange": "BJ",
                "asset_type": "stock", "list_date": date(2025, 1, 1),
                "delist_date": None, "prev_symbol": None,
                "source": "eastmoney", "data_version": "v1",
                "fetched_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
            }
        ]
    )
    monkeypatch.setattr(
        r3, "load_curated_instruments",
        lambda cfg: pl.concat([non_bj, bj_member_df], how="diagonal_relaxed"),
    )
    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: FakeManifest(manifest_calls))
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: FakeWriter(writer_calls))
    monkeypatch.setattr(r3.R3Runner, "_compact", lambda self, rid: {"status": "success"})
    runner.ledger = type("Led", (), {"append": lambda self, r: None})()
    runner._enrich_bj_metadata()
    assert writer_calls and writer_calls[-1][0] == "c2-enrich-bj"
    staged = writer_calls[-1][1]
    non_bj_staged = staged.filter(pl.col("symbol").eq("600000.SH"))
    for col in ("symbol", "name", "exchange", "asset_type", "list_date",
                "delist_date", "prev_symbol", "source", "data_version", "fetched_at"):
        assert non_bj_staged[col].to_list() == non_bj[col].to_list()  # field-for-field


def test_c2_extra_bj_post_fail_closed(monkeypatch, tmp_path):
    import types

    meta = tmp_path / "asl" / "r3"
    meta.mkdir(parents=True)
    (meta / "r3-identity-receipt.json").write_text(
        json.dumps(
            {
                "bj_current_membership": [
                    {"symbol": "920001.BJ", "name": "X", "exchange": "BJ",
                     "asset_type": "stock", "list_date": "2025-01-01",
                     "delist_date": None, "prev_symbol": None}
                ]
            },
            default=str,
        )
    )
    non_bj = pl.DataFrame(
        {
            "symbol": ["600000.SH"], "name": ["A"], "exchange": ["SH"],
            "asset_type": ["stock"], "list_date": [date(2000, 1, 1)],
            "delist_date": [None], "prev_symbol": [None],
            "source": ["tdx_protocol"], "data_version": ["v2"],
            "fetched_at": [datetime(2026, 2, 1, tzinfo=timezone.utc)],
        }
    )
    runner = object.__new__(r3.R3Runner)
    runner.meta = meta
    runner.daily_as_of = R3_DAILY_AS_OF
    runner.history_start = R3_HISTORY_START
    runner.cfg = types.SimpleNamespace(
        manifest_path=tmp_path / "m.db",
        staging_root=tmp_path / "staging",
    )
    runner._new_run = lambda job: "cid"
    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: FakeManifest([]))
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: FakeWriter([]))
    monkeypatch.setattr(r3.R3Runner, "_compact", lambda self, rid: {"status": "success"})
    runner.ledger = type("Led", (), {"append": lambda self, r: None})()

    def post_with_extra(cfg):
        extra = non_bj.clone()
        return extra

    # first call (pre) returns non_bj; second call (post) returns non_bj + stray BJ
    calls = {"n": 0}

    def load_cur(cfg):
        calls["n"] += 1
        if calls["n"] > 1:
            stray = pl.DataFrame(
                {
                    "symbol": ["920999.BJ"], "name": ["Z"], "exchange": ["BJ"],
                    "asset_type": ["stock"], "list_date": [date(2020, 1, 1)],
                    "delist_date": [None], "prev_symbol": [None],
                    "source": ["eastmoney"], "data_version": ["v1"],
                    "fetched_at": [datetime(2026, 2, 1, tzinfo=timezone.utc)],
                }
            )
            return pl.concat([non_bj, stray], how="diagonal_relaxed")
        return non_bj

    monkeypatch.setattr(r3, "load_curated_instruments", load_cur)
    with pytest.raises(R3Error, match="C2_POSTCHECK"):
        runner._enrich_bj_metadata()


def test_f2_fail_then_success_supersedes_prior_failed(monkeypatch):
    runner = object.__new__(r3.R3Runner)
    runner.cfg = type(
        "CfgStub",
        (),
        {
            "manifest_path": Path("/nonexistent/manifest.db"),
            "staging_root": Path("/nonexistent/staging"),
        },
    )()

    class LedgerF2:
        def __init__(self):
            self.calls = []

        def append(self, r):
            self.calls.append(r)

    ledger = LedgerF2()
    runner.ledger = ledger
    manifest = FakeManifest([])
    writer_calls: list = []
    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: manifest)
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: FakeWriter(writer_calls))
    calls = {"n": 0}

    def flaky(symbol, start, end, config=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"state": "SOURCE_ERROR", "reason": "EMPTY_KNOWN_SYMBOL",
                    "symbol": symbol, "frame": None}
        return {"state": "EXISTS", "reason": "valid_bars", "symbol": symbol,
                "frame": _bars_frame(symbol)}

    monkeypatch.setattr(r3, "em_daily_tristate", flaky)
    result = runner._em_primary_route("rid", {"920001.BJ": (R3_HISTORY_START, R3_DAILY_AS_OF)})
    assert result["unexplained_after"] == 0
    assert manifest.superseded  # prior failed batch superseded
    assert manifest.incomplete_blocking() == 0
    assert any(r.get("event") == "ATTEMPT_SUPERSEDE" for r in ledger.calls)


def test_f2_zero_volume_only_unexplained(monkeypatch):
    runner = object.__new__(r3.R3Runner)
    runner.cfg = type(
        "CfgStub",
        (),
        {
            "manifest_path": Path("/nonexistent/manifest.db"),
            "staging_root": Path("/nonexistent/staging"),
        },
    )()

    class LedgerZ:
        def __init__(self):
            self.calls = []

        def append(self, r):
            self.calls.append(r)

    runner.ledger = LedgerZ()
    writer_calls: list = []
    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: FakeManifest([]))
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: FakeWriter(writer_calls))
    zero = _bars_frame().with_columns(pl.lit(0).alias("volume"))

    def zero_fetcher(symbol, start, end, config=None):
        return {"state": "EXISTS", "reason": "valid_bars", "symbol": symbol, "frame": zero}

    monkeypatch.setattr(r3, "em_daily_tristate", zero_fetcher)
    with pytest.raises(R3Error, match="UNEXPLAINED_MISSING"):
        runner._em_primary_route("rid", {"920001.BJ": (R3_HISTORY_START, R3_DAILY_AS_OF)})
    assert not writer_calls


def test_stage_daily_prepares_network_env(monkeypatch, tmp_path):
    runner = object.__new__(r3.R3Runner)
    runner.meta = tmp_path / "asl" / "r3"

    class StubMachine:
        def enter(self, stage):
            return {}

        def complete(self, stage, evidence):
            return {}

    runner.machine = StubMachine()
    prepared = []
    monkeypatch.setattr(r3.R3Runner, "_prepare_network_env",
                        lambda self: prepared.append(True))
    seen = {}
    monkeypatch.setattr(r3.R3Runner, "_fetch_daily_bars_per_route",
                        lambda self: seen.setdefault("called", True))
    runner.stage_daily()
    assert prepared  # network prep happened before the route
    assert seen.get("called") is True


def test_verifier_stale_tip_and_exchange_counts():
    from tools.verify_r3_daily_foundation import daily_tip_verdict, exchange_counts_from

    assert daily_tip_verdict(date(2026, 8, 17))["ok"] is True
    stale = daily_tip_verdict(date(2026, 8, 14))
    assert stale["ok"] is False
    assert stale["DAILY_TIP_STALE"] is True

    active = pl.DataFrame(
        {
            "symbol": ["600000.SH", "601398.SH", "000001.SZ", "920001.BJ"],
            "name": ["a", "b", "c", "d"],
            "exchange": ["SH", "SH", "SZ", "BJ"],
            "asset_type": ["stock"] * 4,
            "list_date": [date(2000, 1, 1)] * 4,
            "delist_date": [None] * 4,
        }
    )
    counts = exchange_counts_from(active)
    assert counts == {"SH": 2, "SZ": 1, "BJ": 1}


def _stage_e_ctx(monkeypatch, tmp_path, *, fetch_impl, formal_map, instruments_df):
    import types

    runner = object.__new__(r3.R3Runner)
    runner.meta = tmp_path / "asl" / "r3"
    runner.meta.mkdir(parents=True)
    (runner.meta / "r3-identity-receipt.json").write_text(
        json.dumps({"shsz_formal_delisted": {k: v.isoformat() for k, v in formal_map.items()}})
    )
    runner.daily_as_of = R3_DAILY_AS_OF
    runner.history_start = R3_HISTORY_START
    runner.cfg = types.SimpleNamespace(
        manifest_path=tmp_path / "m.db",
        staging_root=tmp_path / "staging",
    )
    runner._new_run = lambda job: "erid"
    runner._compact = lambda rid: {"status": "success"}

    class LedgerE:
        def __init__(self):
            self.calls = []

        def append(self, r):
            self.calls.append(r)

    ledger = LedgerE()
    runner.ledger = ledger
    manifest = FakeManifest([])
    writer = FakeWriter([])
    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: manifest)
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: writer)
    monkeypatch.setattr(r3, "load_delisted_catalog", lambda cfg: {})
    monkeypatch.setattr(r3, "load_curated_instruments", lambda cfg: instruments_df)
    monkeypatch.setattr(
        "cnequity.adapters.baostock.delisted_bars.fetch_delisted_bars",
        fetch_impl,
    )
    captured = {"fetch_calls": 0}
    return runner, ledger, manifest, writer, captured


def _instruments_with(formal_map):
    rows = []
    for sym, d in formal_map.items():
        rows.append(
            {
                "symbol": sym, "name": "X", "exchange": sym.split(".")[1],
                "asset_type": "stock", "list_date": date(2000, 1, 1),
                "delist_date": d, "prev_symbol": None,
                "source": "baostock", "data_version": "v1",
                "fetched_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        )
    return pl.DataFrame(rows)


def _bs_ok(rows_symbols, captured):
    def impl(symbols, start, end, config=None):
        captured["fetch_calls"] += 1
        sym = symbols[0]
        if sym not in rows_symbols:
            return [], []
        return [
            {
                "symbol": sym, "trade_date": date(2019, 12, 30),
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                "volume": 100, "amount": 1050.0,
            }
        ], []
    return impl


def test_stage_e_prefetch_manifest_ledger_before_fetch(monkeypatch, tmp_path):
    formal = {"600001.SH": date(2019, 12, 31)}
    calls = {"fetch": 0}

    def fetch_fail(symbols, start, end, config=None):
        calls["fetch"] += 1
        raise RuntimeError("boom")

    runner, ledger, manifest, _w, _ = _stage_e_ctx(
        monkeypatch, tmp_path, fetch_impl=fetch_fail,
        formal_map=formal, instruments_df=_instruments_with(formal),
    )
    with pytest.raises(R3Error, match="E_UNRESOLVED"):
        runner._recover_delisted_daily()
    start_events = [r for r in ledger.calls if r.get("event") == "ATTEMPT_START"]
    assert start_events and start_events[0]["status"] == "running"
    assert start_events[0]["adapter"] == "baostock"
    assert start_events[0]["ownership"] == "RECOVERY_REQUIRED"
    assert calls["fetch"] >= 3  # exact retries
    assert manifest.calls and manifest.calls[0][0] == "start"


def test_stage_e_fail_then_retry_success_lineage(monkeypatch, tmp_path):
    formal = {"600001.SH": date(2019, 12, 31)}
    seq = {"n": 0}

    def fetch_flaky(symbols, start, end, config=None):
        seq["n"] += 1
        if seq["n"] == 1:
            raise RuntimeError("boom1")
        sym = symbols[0]
        return [
            {
                "symbol": sym, "trade_date": date(2019, 12, 30),
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                "volume": 100, "amount": 1050.0,
            }
        ], []

    runner, ledger, manifest, _w, _ = _stage_e_ctx(
        monkeypatch, tmp_path, fetch_impl=fetch_flaky,
        formal_map=formal, instruments_df=_instruments_with(formal),
    )
    runner._recover_delisted_daily()
    assert manifest.superseded  # prior failed batch resolved
    assert manifest.incomplete_blocking() == 0
    assert any(r.get("event") == "ATTEMPT_SUPERSEDE" for r in ledger.calls)


def test_stage_e_expected_no_data_terminal_evidence(monkeypatch, tmp_path):
    formal = {"600001.SH": date(2019, 12, 31), "600002.SH": date(2015, 6, 30)}
    capped = {"fetch_calls": 0}
    impl = _bs_ok({"600001.SH"}, capped)
    runner, ledger, manifest, _w, _ = _stage_e_ctx(
        monkeypatch, tmp_path, fetch_impl=impl,
        formal_map=formal, instruments_df=_instruments_with(formal),
    )
    receipt = runner._recover_delisted_daily()
    assert receipt["expected_no_data_before_window"] == 1
    no_data_events = [r for r in ledger.calls if r.get("event") == "EXPECTED_NO_DATA_TERMINAL"]
    assert {r["symbol"] for r in no_data_events} == {"600002.SH"}
    assert capped["fetch_calls"] == 1  # no provider request for the no-data symbol


def test_stage_e_complete_before_compact_guard(monkeypatch, tmp_path):
    formal = {"600001.SH": date(2019, 12, 31)}
    capped = {"fetch_calls": 0}
    runner, ledger, manifest, _w, _ = _stage_e_ctx(
        monkeypatch, tmp_path, fetch_impl=_bs_ok({"600001.SH"}, capped),
        formal_map=formal, instruments_df=_instruments_with(formal),
    )
    result = runner._recover_delisted_daily()
    assert result["unresolved"] == 0
    assert manifest.incomplete_blocking() == 0


def _f1_ctx(monkeypatch, impl):
    import types

    runner = object.__new__(r3.R3Runner)
    runner.cfg = types.SimpleNamespace(batch_size=1, failover_enabled=False)
    runner.history_start = R3_HISTORY_START
    runner.daily_as_of = R3_DAILY_AS_OF

    class LedgerF1:
        def __init__(self):
            self.calls = []

        def append(self, r):
            self.calls.append(r)

    ledger = LedgerF1()
    runner.ledger = ledger
    calls = {"n": 0}

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return impl(calls["n"])

    monkeypatch.setattr(
        r3, "fetch_daily_bars_parallel", wrapped
    )
    return runner, ledger, calls


def test_f1_ledger_per_attempt_and_retry_lineage(monkeypatch):
    def impl(n):
        if n == 1:
            return {"failed_symbols": ["600000.SH"], "rows_written": 0}
        return {"failed_symbols": [], "rows_written": 5}

    runner, ledger, _ = _f1_ctx(monkeypatch, impl)
    result = runner._tdx_route(
        "rid", {"600000.SH": (R3_HISTORY_START, R3_DAILY_AS_OF)}
    )
    assert result["failed_after"] == 0
    starts = [r for r in ledger.calls if r.get("event") == "ATTEMPT_START"]
    ends = [r for r in ledger.calls if r.get("event") == "ATTEMPT_END"]
    assert [s["attempt"] for s in starts] == [1, 2]
    assert ends[0]["failed_after"] == 1
    assert ends[1]["failed_after"] == 0


def test_f1_retry_failed_scope_only_strict_decrease(monkeypatch):
    def impl(n):
        return {"failed_symbols": ["600000.SH"], "rows_written": 0}

    runner, ledger, _ = _f1_ctx(monkeypatch, impl)
    with pytest.raises(R3Error, match="F1_STRICT_DECREASE"):
        runner._tdx_route(
            "rid", {"600000.SH": (R3_HISTORY_START, R3_DAILY_AS_OF)}
        )


def test_f1_failed_after_blocks_and_no_compact(monkeypatch):
    def impl(n):
        if n == 1:
            return {"failed_symbols": ["600000.SH", "600001.SH", "600002.SH"], "rows_written": 0}
        if n == 2:
            return {"failed_symbols": ["600000.SH", "600001.SH"], "rows_written": 0}
        return {"failed_symbols": ["600000.SH"], "rows_written": 0}

    runner, ledger, _ = _f1_ctx(monkeypatch, impl)
    with pytest.raises(R3Error, match="F1_FAILED_AFTER"):
        runner._tdx_route(
            "rid", {"600000.SH": (R3_HISTORY_START, R3_DAILY_AS_OF),
                    "600001.SH": (R3_HISTORY_START, R3_DAILY_AS_OF),
                    "600002.SH": (R3_HISTORY_START, R3_DAILY_AS_OF)}
        )


def test_cdr_roster_not_observable_not_blocking(monkeypatch, tmp_path):
    # formal identity includes a 689xxx CDR; roster only sees common stocks
    runner = _stage_b_runner(tmp_path, monkeypatch, close=True)
    # replace basics with stock + CDR
    basics = pl.DataFrame(
        {
            "symbol": ["600000.SH", "689001.SH"],
            "name": ["A", "CDR-A"],
            "exchange": ["SH", "SH"],
            "asset_type": ["stock", "cdr"],
            "list_date": [date(2000, 1, 1), date(2021, 1, 1)],
            "delist_date": [None, None],
            "prev_symbol": [None, None],
        }
    )
    monkeypatch.setattr(
        "cnequity.adapters.baostock.instruments.fetch_instrument_basics",
        lambda: basics,
    )

    def roster_sh(day):
        return {"600000.SH"}

    monkeypatch.setattr(
        "cnequity.adapters.baostock.delisted_bars.roster_on",
        roster_sh,
    )
    monkeypatch.setattr(r3, "known_delisted_instruments", lambda cfg, asof: {})
    receipt = runner._identity_completion_v072()
    assert "689001.SH" in set(receipt["roster_not_observable_identity_sample"]) or \
        "689001.SH" in set(receipt.get("formal_identity", []) or [])
    assert receipt["formal_identity_n"] == 2
    assert receipt["roster_expected_n"] == 1
    assert receipt["shsz_closure"]["closed"] is True  # CDR not a false blocker


def test_common_stock_missing_still_blocks(monkeypatch, tmp_path):
    runner = _stage_b_runner(tmp_path, monkeypatch, close=True)
    basics = pl.DataFrame(
        {
            "symbol": ["600000.SH", "600001.SH"],
            "name": ["A", "B"],
            "exchange": ["SH", "SH"],
            "asset_type": ["stock", "stock"],
            "list_date": [date(2000, 1, 1), date(2000, 1, 1)],
            "delist_date": [None, None],
            "prev_symbol": [None, None],
        }
    )
    monkeypatch.setattr(
        "cnequity.adapters.baostock.instruments.fetch_instrument_basics",
        lambda: basics,
    )
    monkeypatch.setattr(
        "cnequity.adapters.baostock.delisted_bars.roster_on",
        lambda day: {"600000.SH"},
    )
    monkeypatch.setattr(r3, "known_delisted_instruments", lambda cfg, asof: {})
    with pytest.raises(R3Error, match="NOT_CLOSED"):
        runner._identity_completion_v072()


def test_c2_controller_terminal_only_after_postproof_failure(monkeypatch, tmp_path):
    import types

    meta = tmp_path / "asl" / "r3"
    meta.mkdir(parents=True)
    (meta / "r3-identity-receipt.json").write_text(
        json.dumps(
            {
                "bj_current_membership": [
                    {"symbol": "920001.BJ", "name": "X", "exchange": "BJ",
                     "asset_type": "stock", "list_date": "2025-01-01",
                     "delist_date": None, "prev_symbol": None}
                ]
            },
            default=str,
        )
    )
    non_bj = pl.DataFrame(
        {
            "symbol": ["600000.SH"], "name": ["A"], "exchange": ["SH"],
            "asset_type": ["stock"], "list_date": [date(2000, 1, 1)],
            "delist_date": [None], "prev_symbol": [None],
            "source": ["tdx_protocol"], "data_version": ["v2"],
            "fetched_at": [datetime(2026, 2, 1, tzinfo=timezone.utc)],
        }
    )
    runner = object.__new__(r3.R3Runner)
    runner.meta = meta
    runner.daily_as_of = R3_DAILY_AS_OF
    runner.history_start = R3_HISTORY_START
    runner.cfg = types.SimpleNamespace(
        manifest_path=tmp_path / "m.db",
        staging_root=tmp_path / "staging",
    )
    runner._new_run = lambda job: "cid"
    runner.ledger = type("Led", (), {
        "calls": [],
        "append": lambda self, r: self.calls.append(r),
    })()
    monkeypatch.setattr(r3.R3Runner, "_compact", lambda self, rid: {"status": "success"})
    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: FakeManifest([]))
    monkeypatch.setattr(r3, "StagingWriter", lambda *a, **k: FakeWriter([]))
    calls = {"n": 0}

    def load_cur(cfg):
        calls["n"] += 1
        if calls["n"] > 1:
            stray = pl.DataFrame(
                {
                    "symbol": ["920999.BJ"], "name": ["Z"], "exchange": ["BJ"],
                    "asset_type": ["stock"], "list_date": [date(2020, 1, 1)],
                    "delist_date": [None], "prev_symbol": [None],
                    "source": ["eastmoney"], "data_version": ["v1"],
                    "fetched_at": [datetime(2026, 2, 1, tzinfo=timezone.utc)],
                }
            )
            return pl.concat([non_bj, stray], how="diagonal_relaxed")
        return non_bj

    monkeypatch.setattr(r3, "load_curated_instruments", load_cur)
    with pytest.raises(R3Error, match="C2_POSTCHECK"):
        runner._enrich_bj_metadata()
    completes = [r for r in runner.ledger.calls if r.get("event") == "CONTROLLER_COMPLETE"]
    assert completes and completes[-1]["status"] == "failed"


# ============================================================================
# V04 final preflight gate fixes
# ============================================================================


def _f1_batch_runner(monkeypatch, impl_result):
    import types

    runner = object.__new__(r3.R3Runner)
    runner.cfg = types.SimpleNamespace(batch_size=10, failover_enabled=False)
    runner.history_start = R3_HISTORY_START
    runner.daily_as_of = R3_DAILY_AS_OF

    class LedgerF:
        def __init__(self):
            self.calls = []

        def append(self, rec):
            self.calls.append(rec)

    runner.ledger = LedgerF()
    calls = {"n": 0}

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return impl_result

    monkeypatch.setattr(r3, "fetch_daily_bars_parallel", wrapped)
    return runner, calls


def test_f1_mixed_spans_never_share_batch(monkeypatch):
    runner, _ = _f1_batch_runner(monkeypatch, {"failed_symbols": [], "rows_written": 9})
    _tdx_spans = {
        "A.SH": (date(2021, 8, 1), R3_DAILY_AS_OF),
        "B.SZ": (R3_HISTORY_START, R3_DAILY_AS_OF),
    }
    result = runner._tdx_route("rid", _tdx_spans)
    assert result["failed_after"] == 0
    starts = [x for x in runner.ledger.calls if x.get("event") == "ATTEMPT_START"]
    assert len(starts) == 1
    windows = [(sw["window_start"], sw["window_end"]) for sw in starts[0]["specs_windows"]]
    assert (date(2021, 8, 1).isoformat(), R3_DAILY_AS_OF.isoformat()) in windows
    # B's 2016 start is preserved (not truncated to A's span)
    assert (R3_HISTORY_START.isoformat(), R3_DAILY_AS_OF.isoformat()) in windows
    assert len(windows) == 2


def test_f1_ledger_windows_equal_batchspec_windows(monkeypatch):
    runner, _ = _f1_batch_runner(monkeypatch, {"failed_symbols": [], "rows_written": 9})
    runner._tdx_route("rid", {"A.SH": (date(2021, 8, 1), R3_DAILY_AS_OF)})
    start = [x for x in runner.ledger.calls if x.get("event") == "ATTEMPT_START"][0]
    parsed = {
        (sw["window_start"], sw["window_end"]) for sw in start["specs_windows"]
    }
    assert parsed == {(date(2021, 8, 1).isoformat(), R3_DAILY_AS_OF.isoformat())}


def test_second_writer_blocked(tmp_path):
    runner1 = object.__new__(r3.R3Runner)
    runner2 = object.__new__(r3.R3Runner)
    runner1.meta = tmp_path / "m1"
    runner2.meta = tmp_path / "m1"
    fd1 = runner1._acquire_lock()
    try:
        with pytest.raises(R3Error, match="WRITER_LOCKED"):
            runner2._acquire_lock()
    finally:
        runner1._release_lock(fd1)


def test_lock_released_after_exception(tmp_path, monkeypatch):
    runner = object.__new__(r3.R3Runner)
    runner.meta = tmp_path / "m2"

    def boom(self):
        raise R3Error("STAGE_X", "boom")

    monkeypatch.setattr(r3.R3Runner, "stage_instruments", boom)
    with pytest.raises(R3Error, match="STAGE_X"):
        runner.run_writer_stage("A_instruments")
    assert runner.service_lock_active() is False


def test_preflight_lock_proof_readonly(monkeypatch, tmp_path):
    # reuse the preflight readonly harness, then assert no lock file created
    import types

    root = tmp_path / "target-root"
    root.mkdir()
    (root / "curated").mkdir()

    class DummyVfs:
        f_bavail = 3 * 2**20
        f_frsize = 64 * 1024

    runner = object.__new__(r3.R3Runner)
    runner.cfg = types.SimpleNamespace(
        data_root=root, workers=1, tdx_allow_mock=False,
        manifest_path=root / "meta" / "manifest.db", meta_root=root / "meta",
        minute_bars_enabled=False, minute_bars_frequencies=[],
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
    monkeypatch.setattr("cnequity.domain.datasets.is_dataset_enabled",
                        lambda name, cfg: False)
    monkeypatch.setattr(os, "statvfs", lambda p: DummyVfs())
    monkeypatch.setattr(r3, "git_sha", lambda p: "0" * 40)
    monkeypatch.setattr(r3, "target_tree_snapshot",
                        lambda rootp, exclude=None: {"digest": "0" * 64, "entries": 0, "lines": []})
    monkeypatch.setattr(r3, "zero_data_layout_errors", lambda rootp: [])
    monkeypatch.setattr(r3.R3Runner, "_check_legacy_isolation", lambda self: None)
    monkeypatch.setattr(r3.R3Runner, "_check_argv_surface", lambda self, argv: None)
    monkeypatch.setattr(r3, "approved_callable_contract",
                        lambda: {"verified": True, "count": 10, "hash": "x" * 64,
                                 "runtime_pin": {"verified": True}, "identities": []})
    receipt = runner.preflight()
    assert receipt["service_writer_lock_active"] is False
    assert not (runner.meta / "runner.lock").exists()  # preflight never creates it


def test_stage_e_zero_volume_only_unresolved(monkeypatch, tmp_path):
    formal = {"600001.SH": date(2019, 12, 31)}

    def zero_bs(symbols, start, end, config=None):
        sym = symbols[0]
        return [
            {
                "symbol": sym, "trade_date": date(2019, 12, 30),
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                "volume": 0, "amount": 0.0,
            }
        ], []

    runner, ledger, manifest, writer, _ = _stage_e_ctx(
        monkeypatch, tmp_path, fetch_impl=zero_bs,
        formal_map=formal, instruments_df=_instruments_with(formal),
    )
    with pytest.raises(R3Error, match="E_UNRESOLVED"):
        runner._recover_delisted_daily()
    # no success recovery batch was written (only failed attempts)
    assert not any(bid.startswith("e-baostock-") and True for bid, _ in writer.calls)
    ends = [r for r in ledger.calls if r.get("event") == "ATTEMPT_END"]
    assert all(e["status"] == "failed" for e in ends)
    assert all(e["reason"] == "ZERO_VOLUME_ONLY" for e in ends)


def test_callable_contract_happy_path():
    contract = r3.approved_callable_contract()
    assert contract["verified"] is True
    assert contract["count"] == 10
    assert len(contract["hash"]) == 64


def test_callable_contract_module_mismatch(monkeypatch):
    bogus = (
        ("JobEngine.run_job", "cnequity.orchestrator.engine", "JobEngine.run_job",
         "cnequity.not.the.engine",
         r3.APPROVED_CALLABLES[0][4]),
    )
    monkeypatch.setattr(r3, "APPROVED_CALLABLES", bogus)
    with pytest.raises(R3Error, match="RUNTIME_CONTRACT_DRIFT"):
        r3.approved_callable_contract()


def test_callable_contract_signature_mismatch(monkeypatch):
    bogus = (
        ("JobEngine.run_job", "cnequity.orchestrator.engine", "JobEngine.run_job",
         "cnequity.orchestrator.engine",
         "(definitely, not, the, signature)"),
    )
    monkeypatch.setattr(r3, "APPROVED_CALLABLES", bogus)
    with pytest.raises(R3Error, match="RUNTIME_CONTRACT_DRIFT"):
        r3.approved_callable_contract()


# ============================================================================
# interrupted control-plane recovery (V01)
# ============================================================================


def _incident_ctx(
    monkeypatch,
    tmp_path,
    *,
    identity_receipt=False,
    batches=(("instruments", "success"), ("compact", "success")),
    run_status="running",
    success_rows=None,
    a_compact_status="success",
    current="B_discovery",
    completed=None,
):
    import types

    completed = completed if completed is not None else ["A_instruments"]
    meta = tmp_path / "meta" / "asl" / "r3"
    meta.mkdir(parents=True)
    db_path = tmp_path / "manifest.db"
    manifest = r3.Manifest(db_path)
    rid = manifest.start_run("r3_instruments", {"trade_date": R3_DAILY_AS_OF.isoformat(), "backfill": True})
    for ds, st in batches:
        bid = f"b-{ds}"
        manifest.start_batch(
            rid, bid, task_id=ds, dataset=ds,
            symbols=["600000.SH"],
            window_start=R3_HISTORY_START.isoformat(),
            window_end=R3_DAILY_AS_OF.isoformat(),
            blocks_compaction=True,
        )
        manifest.finish_batch(rid, bid, st, rows_read=1, rows_written=1)
    if run_status == "success":
        manifest.finish_run(
            rid, "success",
            rows_read=success_rows if success_rows is not None else 1,
            rows_written=success_rows if success_rows is not None else 1,
        )
    state = {
        "status": "running",
        "current": current,
        "completed": list(completed),
        "started_at": "2026-08-18T02:10:37.433982+00:00",
        "evidence": {
            "A_instruments": {
                "job": {
                    "run_id": rid,
                    "result": {"rows_read": 1, "rows_written": 1, "status": "success"},
                },
                "compact": {"status": a_compact_status},
            }
        },
    }
    (meta / "execution-state.json").write_text(json.dumps(state))
    if identity_receipt:
        (meta / "r3-identity-receipt.json").write_text(json.dumps({"x": 1}))
    # legacy evidence preserved
    (meta / "r3-discovery-pacing.json").write_text(json.dumps({"legacy": True}))
    (meta / "service-ledger.jsonl").write_text(
        json.dumps({"legacy": "B_discovery_partial", "stage": "B_discovery"}) + "\n"
    )

    runner = object.__new__(r3.R3Runner)
    runner.cfg = types.SimpleNamespace(
        manifest_path=db_path, staging_root=tmp_path / "staging",
        meta_root=tmp_path / "meta",
    )
    runner.meta = meta
    runner.state_path = meta / "execution-state.json"
    runner.ledger = r3.ServiceLedger(meta / "service-ledger.jsonl")
    runner.machine = r3.StageMachine(runner.state_path)
    runner.plan_sha = PLAN_SHA
    runner.daily_as_of = R3_DAILY_AS_OF
    runner.history_start = R3_HISTORY_START
    return runner, manifest, rid, meta


def _network_sentinels(monkeypatch):
    sent = []
    for name in ("fetch_daily_bars_parallel", "em_daily_tristate"):
        def boom(*a, **k):
            sent.append(name)
            raise AssertionError(f"network fn called: {name}")
        monkeypatch.setattr(r3, name, boom)
    return sent


def test_abandon_exact_incident(tmp_path):
    state = {
        "status": "running", "current": "B_discovery",
        "completed": ["A_instruments"],
        "started_at": "t0", "evidence": {"A_instruments": {}},
    }
    p = tmp_path / "s.json"
    p.write_text(json.dumps(state))
    machine = r3.StageMachine(p)
    out = machine.abandon_current(
        "B_discovery", reason="LEGACY_SINA_PARTIAL_SUPERSEDED_BY_V07_2",
        replacement="V07.2_identity_completion",
    )
    assert out["current"] is None
    assert out["status"] == "pending"
    assert out["completed"] == ["A_instruments"]  # B NOT added
    assert out["abandoned"][0]["stage"] == "B_discovery"


def test_abandon_refuses_wrong_current(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"status": "running", "current": "C_merge",
                             "completed": ["A_instruments", "B_discovery"]}))
    machine = r3.StageMachine(p)
    with pytest.raises(R3Error, match="RECOVERY_STATE_MISMATCH"):
        machine.abandon_current("B_discovery", reason="x")


def test_abandon_refuses_wrong_prefix(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"status": "running", "current": "B_discovery",
                             "completed": []}))
    machine = r3.StageMachine(p)
    with pytest.raises(R3Error, match="RECOVERY_STATE_MISMATCH"):
        machine.abandon_current("B_discovery", reason="x")


def test_a_manifest_finalize_success_and_idempotent(monkeypatch, tmp_path):
    runner, manifest, rid, meta = _incident_ctx(monkeypatch, tmp_path)
    first = runner.finalize_completed_a_manifest()
    assert first["status"] == "success"
    assert first["idempotent"] is False
    second = runner.finalize_completed_a_manifest()
    assert second["idempotent"] is True
    assert manifest.get_run(rid)["status"] == "success"


def test_a_manifest_incomplete_batch_blocks(monkeypatch, tmp_path):
    runner, manifest, rid, meta = _incident_ctx(
        monkeypatch, tmp_path,
        batches=(("instruments", "success"), ("compact", "failed")),
    )
    with pytest.raises(R3Error, match="A_MANIFEST_RECOVERY_MISMATCH"):
        runner.finalize_completed_a_manifest()


def test_a_manifest_compact_evidence_blocks(monkeypatch, tmp_path):
    runner, manifest, rid, meta = _incident_ctx(
        monkeypatch, tmp_path, a_compact_status="warning",
    )
    with pytest.raises(R3Error, match="A_MANIFEST_RECOVERY_MISMATCH"):
        runner.finalize_completed_a_manifest()


def test_already_success_a_idempotent(monkeypatch, tmp_path):
    runner, manifest, rid, meta = _incident_ctx(
        monkeypatch, tmp_path, run_status="success",
    )
    out = runner.finalize_completed_a_manifest()
    assert out["idempotent"] is True
    assert out["status"] == "success"


def test_recovery_preserves_legacy_and_final_state(monkeypatch, tmp_path):
    runner, manifest, rid, meta = _incident_ctx(monkeypatch, tmp_path)
    pacing_before = (meta / "r3-discovery-pacing.json").read_bytes()
    ledger_before = (meta / "service-ledger.jsonl").read_bytes()
    result = runner.recover_interrupted_control_plane()
    assert (meta / "r3-discovery-pacing.json").read_bytes() == pacing_before
    # ledger gains a recovery append but legacy record prefix preserved
    ledger_after = (meta / "service-ledger.jsonl").read_text()
    assert "B_discovery_partial" in ledger_after
    assert result["legacy_b_evidence_preserved"] is True
    after = runner.machine.load()
    assert after["completed"] == ["A_instruments"]
    assert after["current"] is None
    assert after["abandoned"][0]["replacement"] == "V07.2_identity_completion"


def test_recovery_identity_receipt_blocks(monkeypatch, tmp_path):
    runner, manifest, rid, meta = _incident_ctx(
        monkeypatch, tmp_path, identity_receipt=True,
    )
    with pytest.raises(R3Error, match="B_RECOVERY_NOT_SAFE"):
        runner.recover_interrupted_control_plane()


def test_recovery_wrong_current_blocks(monkeypatch, tmp_path):
    runner, manifest, rid, meta = _incident_ctx(
        monkeypatch, tmp_path, current="C_merge",
    )
    with pytest.raises(R3Error, match="B_RECOVERY_NOT_SAFE"):
        runner.recover_interrupted_control_plane()


def test_recovery_no_provider_calls_and_releases_lock(monkeypatch, tmp_path):
    sent = _network_sentinels(monkeypatch)
    runner, manifest, rid, meta = _incident_ctx(
        monkeypatch, tmp_path,
        a_compact_status="warning",  # finalize fails -> exception path
    )
    with pytest.raises(R3Error, match="A_MANIFEST_RECOVERY_MISMATCH"):
        runner.recover_interrupted_control_plane()
    assert sent == []
    assert runner.service_lock_active() is False  # lock released after exception


def test_future_stage_a_terminalizes_manifest(monkeypatch, tmp_path):
    import types

    runner = object.__new__(r3.R3Runner)
    runner.cfg = types.SimpleNamespace(manifest_path=tmp_path / "m.db")
    runner.meta = tmp_path / "meta" / "asl" / "r3"

    class StubMachine:
        def enter(self, s):
            return {}

        def complete(self, s, ev):
            return {}

    runner.machine = StubMachine()
    manifest_calls = []

    class FakeManifest2:
        def finish_run(self, run_id, status, rows_read=0, rows_written=0, error_message=None):
            manifest_calls.append((run_id, status, rows_read, rows_written))

    monkeypatch.setattr(r3, "Manifest", lambda *a, **k: FakeManifest2())
    monkeypatch.setattr(
        r3.R3Runner, "_run_single_step_runjob",
        lambda self, steps: {"run_id": "arun", "result": {"rows_read": 7, "rows_written": 7, "status": "success"}},
    )
    monkeypatch.setattr(r3.R3Runner, "_compact", lambda self, rid: {"status": "success"})
    runner.stage_instruments()
    assert manifest_calls == [("arun", "success", 7, 7)]


def test_recovery_receipt_write_failure_leaves_b_running(tmp_path, monkeypatch):
    runner, manifest, rid, meta = _incident_ctx(monkeypatch, tmp_path)
    real = r3.atomic_write_json

    def guarded(path, text):
        if "control-plane-recovery-v01.json" in str(path):
            raise RuntimeError("receipt write failed")
        return real(path, text)

    monkeypatch.setattr(r3, "atomic_write_json", guarded)
    with pytest.raises(RuntimeError, match="receipt write failed"):
        runner.recover_interrupted_control_plane()
    after = runner.machine.load()
    assert after["current"] == "B_discovery"
    assert after["status"] == "running"
    assert after["completed"] == ["A_instruments"]
    with pytest.raises(R3Error, match="STAGE_IN_PROGRESS"):
        runner.machine.enter("B_discovery")  # B still blocked


def test_recovery_final_state_matches_receipt(tmp_path, monkeypatch):
    runner, manifest, rid, meta = _incident_ctx(monkeypatch, tmp_path)
    result = runner.recover_interrupted_control_plane()
    assert result["state_after"] == {
        "status": "pending", "current": None, "completed": ["A_instruments"],
    }
    after = runner.machine.load()
    assert after["status"] == "pending"
    assert after["current"] is None
    assert after["completed"] == ["A_instruments"]


def test_recovery_abandon_failure_state(tmp_path, monkeypatch):
    runner, manifest, rid, meta = _incident_ctx(monkeypatch, tmp_path)

    def boom_abandon(self, stage, **kw):
        raise R3Error("ABANDON_BOOM", "abandon failed")

    monkeypatch.setattr(r3.StageMachine, "abandon_current", boom_abandon)
    with pytest.raises(R3Error, match="ABANDON_BOOM"):
        runner.recover_interrupted_control_plane()
    assert (meta / "control-plane-recovery-v01.json").exists()
    after = runner.machine.load()
    assert after["completed"] == ["A_instruments"]  # B never added
    assert after["current"] == "B_discovery"  # abandon failed -> unchanged
    assert not (meta / "r3-identity-receipt.json").exists()


def test_run_job_exception_terminalizes_manifest(tmp_path, monkeypatch):
    import types

    db_path = tmp_path / "manifest.db"
    manifest = r3.Manifest(db_path)
    rid = manifest.start_run("r3_instruments", {})

    class FakeEngine:
        def run_job(self, *a, **k):
            raise RuntimeError("run_job boom")

    monkeypatch.setattr(r3, "JobEngine", lambda cfg: FakeEngine())
    runner = object.__new__(r3.R3Runner)
    runner.cfg = types.SimpleNamespace(manifest_path=db_path)
    runner.daily_as_of = R3_DAILY_AS_OF
    runner._new_run = lambda job: rid
    with pytest.raises(RuntimeError, match="run_job boom"):
        runner._run_single_step_runjob(["instruments"])
    row = manifest.get_run(rid)
    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert row["error_message"]


def test_compact_exception_terminalizes_manifest(tmp_path, monkeypatch):
    import types

    db_path = tmp_path / "manifest.db"
    manifest = r3.Manifest(db_path)
    rid = manifest.start_run("r3_instruments", {})

    def boom_compact(self, rid_):
        raise R3Error("COMPACT_FAILED", "boom compact")

    runner = object.__new__(r3.R3Runner)
    runner.cfg = types.SimpleNamespace(manifest_path=db_path)
    monkeypatch.setattr(
        r3.R3Runner, "_run_single_step_runjob",
        lambda self, steps: {"run_id": rid, "result": {"rows_read": 1, "rows_written": 1}},
    )
    monkeypatch.setattr(r3.R3Runner, "_compact", boom_compact)
    with pytest.raises(R3Error, match="COMPACT_FAILED"):
        runner._run_single_step_terminal(["instruments"])
    row = manifest.get_run(rid)
    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert row["error_message"]


def test_a_success_rows_mismatch_blocks(monkeypatch, tmp_path):
    runner, manifest, rid, meta = _incident_ctx(
        monkeypatch, tmp_path, run_status="success", success_rows=99,
    )
    with pytest.raises(R3Error, match="A_MANIFEST_RECOVERY_MISMATCH"):
        runner.finalize_completed_a_manifest()


def test_a_success_rows_equal_idempotent(monkeypatch, tmp_path):
    runner, manifest, rid, meta = _incident_ctx(
        monkeypatch, tmp_path, run_status="success", success_rows=1,
    )
    out = runner.finalize_completed_a_manifest()
    assert out["idempotent"] is True
