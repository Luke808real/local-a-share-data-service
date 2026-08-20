"""Offline tests for the resumable full-bootstrap orchestrator.

Uses the real pinned Manifest on a tmp manifest.db for resume authority,
injected identities, fake adapters, and a fake formal gate. No provider, no
real data root, no network.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cnequity.orchestrator.manifest import Manifest

from ashare_data import r4a0_full_bootstrap_orchestrator as orch


FORMAL_N = 5456


def make_identity(n=FORMAL_N, *, match=True):
    syms = sorted({f"{i:06d}.SH" for i in range(n)})
    return {
        "IDENTITY_MATCH": match,
        "identity_ok": match,
        "symbols": syms,
        "EXPECTED_SYMBOL_N": n,
        "EXPECTED_SYMBOL_HASH": "0" * 64,
        "FORMAL_IDENTITY_N": n,
        "FORMAL_IDENTITY_HASH": "0" * 64,
        "IDENTITY_SOURCE": "TEST",
    }


def cfg_factory():
    return SimpleNamespace(failover_enabled=True)


def _add_chunk(m, run_id, symbols, status="success", window=("2016-01-01", "2026-08-17")):
    import uuid

    bid = "b-" + uuid.uuid4().hex[:12]
    m.start_batch(
        run_id,
        bid,
        "corporate_actions_chunk",
        "corporate_actions",
        symbols=list(symbols),
        window_start=window[0],
        window_end=window[1],
        blocks_compaction=False,
    )
    m.finish_batch(run_id, bid, status, rows_read=0, rows_written=0)


def write_manifest(tmp_path, specs):
    m = Manifest(tmp_path / "manifest.db")
    run_id = m.start_run("backfill", {"backfill_scope": {}})
    for spec in specs:
        _add_chunk(m, run_id, **spec)
    return m


class FakeAdapter:
    def __init__(self, result=None, fails_at=None, result_after=None):
        self.calls = []
        self.result = result or {}
        self.fails_at = fails_at
        self.result_after = result_after or {}

    def __call__(self, symbols, **kwargs):
        self.calls.append(list(symbols))
        if self.fails_at is not None and len(self.calls) >= self.fails_at:
            return dict(self.result_after)
        return dict(
            {
                "STATUS": "PILOT_COMPLETE",
                "PILOT_COMPLETE": True,
                "receipt_post_check": {"STATUS": "OK"},
                "CONFIG_INTEGRITY_STATUS": "OK",
                "run_id": f"run-{len(self.calls)}",
                "final_status": "success",
                "failed_symbols": [],
            },
            **self.result,
        )


def gate_true(root=None):
    return {"R4A0_READY": True}


def gate_false(root=None):
    return {"R4A0_READY": False}


def test_frozen_5456_identity_accepted(tmp_path):
    ident = make_identity()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["STATUS"] == "READY"
    assert r["EXPECTED_SYMBOL_N"] == FORMAL_N


def test_pilot_24_receipts_covered_24(tmp_path):
    ident = make_identity()
    pilot = ident["symbols"][:24]
    m = write_manifest(tmp_path, [{"symbols": pilot}])
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["COVERED_SYMBOL_N"] == 24


def test_remaining_5432(tmp_path):
    ident = make_identity()
    pilot = ident["symbols"][:24]
    write_manifest(tmp_path, [{"symbols": pilot}])
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["REMAINING_SYMBOL_N"] == FORMAL_N - 24
    assert r["COVERED_SYMBOL_N"] == 24


def test_failed_receipt_not_covered(tmp_path):
    ident = make_identity(30)
    write_manifest(tmp_path, [{"symbols": ident["symbols"][:5], "status": "failed"}])
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["COVERED_SYMBOL_N"] == 0
    assert r["REMAINING_SYMBOL_N"] == 30


def test_warning_receipt_not_covered(tmp_path):
    ident = make_identity(30)
    write_manifest(tmp_path, [{"symbols": ident["symbols"][:5], "status": "warning"}])
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["COVERED_SYMBOL_N"] == 0


def test_wrong_window_receipt_not_covered(tmp_path):
    ident = make_identity(30)
    write_manifest(
        tmp_path,
        [{"symbols": ident["symbols"][:5], "window": ("2016-01-01", "2020-01-01")}],
    )
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["COVERED_SYMBOL_N"] == 0


def test_zero_event_success_receipt_is_covered(tmp_path):
    # a success full-window receipt with zero event rows is still coverage
    ident = make_identity(30)
    write_manifest(tmp_path, [{"symbols": ident["symbols"][:4]}])
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["COVERED_SYMBOL_N"] == 4


def test_deterministic_sorted_plan(tmp_path):
    ident = make_identity(100)
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    all_syms = [s for c in r["chunks"] for s in c["symbols"]]
    assert all_syms == sorted(all_syms)
    assert r["CHUNK_COUNT"] == 5  # 100 // 24 = 4 + 1(4)


def test_identical_state_identical_plan_hash(tmp_path):
    ident = make_identity(100)
    r1 = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    r2 = orch.run_full_bootstrap(
        tmp_path / "root2", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest2.db",
    )
    assert r1["CHUNK_PLAN_HASH"] == r2["CHUNK_PLAN_HASH"]


def test_final_chunk_lt_24(tmp_path):
    ident = make_identity(25)
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["CHUNK_COUNT"] == 2
    assert r["chunks"][-1]["symbol_n"] == 1


def test_no_chunk_gt_24(tmp_path):
    ident = make_identity(201)
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert all(c["symbol_n"] <= 24 for c in r["chunks"])


def test_already_covered_never_in_plan(tmp_path):
    ident = make_identity(60)
    covered = ident["symbols"][:24]
    write_manifest(tmp_path, [{"symbols": covered}])
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    plan_syms = {s for c in r["chunks"] for s in c["symbols"]}
    assert plan_syms.isdisjoint(set(covered))
    assert r["REMAINING_SYMBOL_N"] == 36


def test_unknown_receipt_symbol_fail_closed(tmp_path):
    ident = make_identity(30)
    write_manifest(tmp_path, [{"symbols": ["999999.SH"]}])
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["STATUS"] == "UNKNOWN_RECEIPT_SYMBOL"


def test_identity_mismatch_fail_closed(tmp_path):
    ident = make_identity(match=False)
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["STATUS"] == "FORMAL_IDENTITY_MISMATCH"


def test_dry_run_zero_adapter_provider_write(tmp_path):
    ident = make_identity(30)
    ad = FakeAdapter()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
    )
    assert r["DRY_RUN_STATUS"] == "OK"
    assert ad.calls == []
    assert r["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert r["MANIFEST_WRITE"] == "NO" and r["REAL_ROOT_WRITE"] == "NO"


def test_exec_reachable_under_fake_adapter_complete(tmp_path):
    ident = make_identity(30)
    ad = FakeAdapter()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_true,
    )
    assert ad.calls
    assert r["STATUS"] == "FULL_BOOTSTRAP_COMPLETE"
    assert r["FULL_BOOTSTRAP_COMPLETE"] is True


def test_successful_chunk_advances(tmp_path):
    ident = make_identity(30)
    ad = FakeAdapter()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_false,
    )
    assert len(ad.calls) == 2  # 30 -> chunk 24 + chunk 6
    assert r["STATUS"] == "FULL_BOOTSTRAP_INCOMPLETE"
    assert r["FULL_BOOTSTRAP_COMPLETE"] is False
    assert r["progress"][-1]["CHUNK_INDEX"] == 2


def test_incomplete_chunk_stops_immediately(tmp_path):
    ident = make_identity(60)
    ad = FakeAdapter(
        fails_at=2,
        result_after={
            "STATUS": "PILOT_INCOMPLETE",
            "PILOT_COMPLETE": False,
            "receipt_post_check": {"STATUS": "MISMATCH"},
            "CONFIG_INTEGRITY_STATUS": "OK",
            "failed_symbols": ["000001.SH"],
        },
    )
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_false,
    )
    assert len(ad.calls) == 2  # stopped at second chunk
    assert r["STATUS"] == "FULL_BOOTSTRAP_STOPPED"
    assert r["stop_chunk_index"] == 2


def test_receipt_mismatch_stops(tmp_path):
    ident = make_identity(30)
    ad = FakeAdapter(
        result={
            "STATUS": "PILOT_INCOMPLETE",
            "PILOT_COMPLETE": False,
            "receipt_post_check": {"STATUS": "MISMATCH"},
            "CONFIG_INTEGRITY_STATUS": "OK",
        }
    )
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_false,
    )
    assert len(ad.calls) == 1
    assert r["STATUS"] == "FULL_BOOTSTRAP_STOPPED"


def test_config_unknown_stops(tmp_path):
    ident = make_identity(30)
    ad = FakeAdapter(
        result={
            "STATUS": "PILOT_INCOMPLETE",
            "PILOT_COMPLETE": False,
            "receipt_post_check": {"STATUS": "OK"},
            "CONFIG_INTEGRITY_STATUS": "UNKNOWN",
        }
    )
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_false,
    )
    assert len(ad.calls) == 1
    assert r["STATUS"] == "FULL_BOOTSTRAP_STOPPED"
    assert r["stop_reason"] == "CONFIG_UNKNOWN_OR_CHANGED"


def test_restart_recomputes_from_manifest(tmp_path):
    ident = make_identity(60)
    covered = ident["symbols"][:24]
    write_manifest(tmp_path, [{"symbols": covered}])
    # restart with MORE coverage actually written to manifest
    m = Manifest(tmp_path / "manifest.db")
    run2 = m.start_run("backfill", {"backfill_scope": {}})
    _add_chunk(m, run2, ident["symbols"][24:48])
    r = orch.run_full_bootstrap(
        tmp_path / "root2", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["COVERED_SYMBOL_N"] == 48
    assert r["REMAINING_SYMBOL_N"] == 12


def test_manifest_authority_overrides_stale_progress(tmp_path):
    # orchestrator has no progress-file input; coverage always derives from
    # the manifest, so a "stale" external progress can never inflate coverage.
    ident = make_identity(30)
    write_manifest(tmp_path, [{"symbols": ident["symbols"][:4]}])
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["COVERED_SYMBOL_N"] == 4  # manifest authority, not any progress file
    assert r["REMAINING_SYMBOL_N"] == 26


def test_no_direct_provider_import():
    src = Path(orch.__file__).read_text(encoding="utf-8")
    for banned in (
        "fetch_corporate_actions_tdx",
        "fetch_corporate_actions_eastmoney",
        "from cnequity.adapters.tdx_protocol",
        "import requests",
    ):
        assert banned not in src
    # the ONLY fetch path is the pinned adapter
    assert "run_bounded_pilot" in src


def test_final_gate_false_not_complete(tmp_path):
    ident = make_identity(10)
    ad = FakeAdapter()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_false,
    )
    assert r["STATUS"] == "FULL_BOOTSTRAP_INCOMPLETE"
    assert r["FULL_BOOTSTRAP_COMPLETE"] is False


def test_final_gate_true_complete(tmp_path):
    ident = make_identity(10)
    ad = FakeAdapter()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_true,
    )
    assert r["STATUS"] == "FULL_BOOTSTRAP_COMPLETE"
    assert r["FULL_BOOTSTRAP_COMPLETE"] is True


# ---- hardening V01 additions ---------------------------------------------
def _load_cli():
    import importlib.util

    tool_p = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "run_r4a0_full_bootstrap_plan.py"
    )
    spec = importlib.util.spec_from_file_location("full_bootstrap_cli", tool_p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _cli_run(monkeypatch, argv):
    mod = _load_cli()
    calls = []

    def fake_orch(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return {"STATUS": "READY"}

    monkeypatch.setattr(mod, "run_full_bootstrap", fake_orch)
    monkeypatch.setattr("sys.argv", ["full_bootstrap_cli"] + argv)
    rc = mod.main()
    return rc, calls


def test_cli_default_dry_run(monkeypatch):
    rc, calls = _cli_run(monkeypatch, ["--config", "config/cnequity.toml"])
    assert rc == 0
    assert calls and calls[0]["kwargs"]["dry_run"] is True


def test_cli_explicit_dry_run(monkeypatch):
    rc, calls = _cli_run(
        monkeypatch, ["--dry-run", "--config", "config/cnequity.toml"]
    )
    assert rc == 0
    assert calls and calls[0]["kwargs"]["dry_run"] is True


def test_cli_exec_reachable(monkeypatch):
    rc, calls = _cli_run(monkeypatch, ["--exec", "--config", "config/cnequity.toml"])
    assert rc == 0
    assert calls is not None and len(calls) == 1
    assert calls[0]["kwargs"]["dry_run"] is False


def test_cli_conflicting_flags_rejected(monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        _cli_run(
            monkeypatch,
            ["--dry-run", "--exec", "--config", "config/cnequity.toml"],
        )
    assert excinfo.value.code == 2


def test_production_default_gate_wired(tmp_path, monkeypatch):
    """run_gate_fn=None must use the real audited run_gate (default_formal_gate)."""
    ident = make_identity(10)
    seen = []

    def fake_default(root):
        seen.append(root)
        return {"R4A0_READY": True}

    monkeypatch.setattr(orch, "default_formal_gate", fake_default)
    ad = FakeAdapter()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=None,
    )
    assert r["STATUS"] == "FULL_BOOTSTRAP_COMPLETE"
    assert seen  # default (production) gate was actually invoked


def test_start_gate_correctness_blocker_stops_before_adapter(tmp_path):
    ident = make_identity(30)
    ad = FakeAdapter()

    def bad_gate(root=None):
        return {
            "R4A0_READY": False,
            "IDENTITY_STATUS": "FAIL",
            "SCHEMA_STATUS": "PASS",
            "SCOPE_STATUS": "PASS",
            "UNIQUENESS_STATUS": "PASS",
            "PROVENANCE_STATUS": "PASS",
        }

    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=bad_gate,
    )
    assert r["STATUS"] == "START_GATE_FAILURE"
    assert ad.calls == []


def test_periodic_gate_every_10(tmp_path):
    ident = make_identity(250)
    ad = FakeAdapter()
    calls = {"n": 0}

    def cov_gate(root=None):
        calls["n"] += 1
        return {"R4A0_READY": False}

    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=cov_gate, gate_every=2,
    )
    # chunks = ceil(250/24)=11; start(1) + periodic at 2,4,6,8,10 (5) + final(1)
    assert r["STATUS"] == "FULL_BOOTSTRAP_INCOMPLETE"
    assert r["periodic_gates"]
    assert len(ad.calls) == 11


def test_periodic_gate_correctness_failure_stops(tmp_path):
    ident = make_identity(60)
    ad = FakeAdapter()
    state = {"n": 0}

    def flaky_gate(root=None):
        state["n"] += 1
        if state["n"] == 2:  # first periodic gate
            return {
                "R4A0_READY": False,
                "SCHEMA_STATUS": "FAIL",
            }
        return {"R4A0_READY": False}

    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=flaky_gate, gate_every=1,
    )
    assert r["STATUS"] == "PERIODIC_GATE_FAILURE"
    assert "SCHEMA" in r["periodic_blockers"]


def test_zero_remaining_resume_complete_with_gate_true(tmp_path):
    ident = make_identity(10)
    write_manifest(tmp_path, [{"symbols": ident["symbols"]}])  # all covered
    ad = FakeAdapter()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_true,
    )
    assert ad.calls == []
    assert r["STATUS"] == "FULL_BOOTSTRAP_COMPLETE"
    assert r["FULL_BOOTSTRAP_COMPLETE"] is True


def test_zero_remaining_resume_incomplete_with_gate_false(tmp_path):
    ident = make_identity(10)
    write_manifest(tmp_path, [{"symbols": ident["symbols"]}])
    ad = FakeAdapter()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_false,
    )
    assert ad.calls == []
    assert r["STATUS"] == "FULL_BOOTSTRAP_INCOMPLETE"
    assert r["FULL_BOOTSTRAP_COMPLETE"] is False


def test_real_telemetry_not_no(tmp_path):
    ident = make_identity(10)
    ad = FakeAdapter()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_false,
    )
    assert r["MANIFEST_WRITE"] == "YES"
    assert r["REAL_ROOT_WRITE"] == "YES"
    assert r["NETWORK_PROVIDER_DATA_FETCH"] == "UNKNOWN"
    assert r["NETWORK_PROVIDER_REQUEST_COUNT"] == "UNVERIFIED"


def test_protected_dataset_mutation_boundary_breach(tmp_path, monkeypatch):
    ident = make_identity(10)
    ad = FakeAdapter()
    state = {"n": 0}

    def fake_inventory(root):
        state["n"] += 1
        return "BEFORE" if state["n"] == 1 else "AFTER"

    monkeypatch.setattr(orch, "protected_inventory_hash", fake_inventory)
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_true,
    )
    assert r["STATUS"] == "WRITE_BOUNDARY_BREACH"
    assert r["FULL_BOOTSTRAP_COMPLETE"] is False


def test_config_mutation_boundary_breach(tmp_path, monkeypatch):
    ident = make_identity(10)
    ad = FakeAdapter()
    state = {"n": 0}

    def fake_config_sha(path):
        state["n"] += 1
        return "AAA" if state["n"] == 1 else "BBB"

    monkeypatch.setattr(orch, "config_sha", fake_config_sha)
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=False, identity=ident,
        manifest_path=tmp_path / "manifest.db", adapter_callable=ad,
        run_gate_fn=gate_true,
        config_path=tmp_path / "cnequity.toml",
    )
    assert r["STATUS"] == "WRITE_BOUNDARY_BREACH"


def test_wrong_dataset_receipt_not_coverage(tmp_path):
    ident = make_identity(30)
    m = Manifest(tmp_path / "manifest.db")
    run_id = m.start_run("backfill", {"backfill_scope": {}})
    import uuid

    _add_chunk(m, run_id, ident["symbols"][:5], status="success")
    con = sqlite3.connect(tmp_path / "manifest.db")
    con.execute(
        "UPDATE ingestion_batches SET dataset = 'other' WHERE run_id = ?",
        (run_id,),
    )
    con.commit()
    con.close()
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["COVERED_SYMBOL_N"] == 0


def test_manifest_wal_pending_fail_closed(tmp_path):
    ident = make_identity(10)
    (tmp_path / "manifest.db").write_bytes(b"x")  # db exists
    (tmp_path / "manifest.db-wal").write_bytes(b"abc")  # non-empty wal
    r = orch.run_full_bootstrap(
        tmp_path / "root", cfg=cfg_factory(), dry_run=True, identity=ident,
        manifest_path=tmp_path / "manifest.db",
    )
    assert r["STATUS"] == "MANIFEST_READ_FAILURE"
