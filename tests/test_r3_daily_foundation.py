"""R3 daily foundation control-plane tests.

These tests are bounded and offline: they exercise span grouping, stage-machine
transitions, the service ledger, and fail-closed guards against the real
playground/config. They never access a real data root, any legacy root, or the
market network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import ashare_data.r3_daily as r3
from ashare_data.r3_daily import (
    BASE_HEAD,
    CONFIG_SHA,
    LOCK_SHA,
    PINNED_CNEQUITY_SHA,
    PINNED_CNEQUITY_VERSION,
    PLAN_SHA,
    R3_DAILY_AS_OF,
    R3_HISTORY_START,
    R3Error,
    ServiceLedger,
    StageMachine,
    effective_span,
    group_by_span,
)


def test_frozen_constants_consistent():
    assert R3_HISTORY_START == date(2016, 1, 1)
    assert R3_DAILY_AS_OF == date(2026, 8, 17)
    assert not R3_DAILY_AS_OF >= date.today()
    assert BASE_HEAD == "0254122a99f0a365d2be12f29a2a59b951497fd3"
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
