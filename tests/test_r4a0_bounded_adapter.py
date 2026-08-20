"""Offline tests for the R4A0 bounded corporate_actions execution adapter.

Fake engine/manifest + injected identity + mocked pin only. No real provider,
no real data root, no manifest on the real lake, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ashare_data import r4a0_bounded_adapter as adapter


VALID = [
    "600000.SH",
    "600004.SH",
    "600006.SH",
    "600007.SH",
    "600008.SH",
    "600009.SH",
    "000001.SZ",
    "000002.SZ",
    "000004.SZ",
    "000005.SZ",
    "000006.SZ",
    "000007.SZ",
    "600005.SH",
    "600068.SH",
    "600069.SH",
    "600070.SH",
    "600074.SH",
    "600077.SH",
    "000018.SZ",
    "000022.SZ",
    "000023.SZ",
    "000033.SZ",
    "600123.SH",
    "000039.SZ",
]


def make_identity(symbols, *, match=True):
    return {
        "FORMAL_IDENTITY_N": len(symbols),
        "FORMAL_IDENTITY_HASH": "0" * 64,
        "IDENTITY_MATCH": match,
        "symbols": sorted(set(symbols)),
        "IDENTITY_SOURCE": "TEST",
    }


class FakeManifest:
    def __init__(self):
        self.runs = []
        self.batches = []
        self.finishes = []

    def start_run(self, job_name, metadata):
        self.runs.append({"job_name": job_name, "metadata": metadata})
        return "run-fake-1"

    def finish_run(self, run_id, status, rows_read=0, rows_written=0, error_message=None):
        self.finishes.append(
            {
                "run_id": run_id,
                "status": status,
                "error_message": error_message,
            }
        )

    def start_batch(
        self,
        run_id,
        batch_id,
        task_id=None,
        dataset=None,
        symbols=None,
        window_start=None,
        window_end=None,
        blocks_compaction=False,
    ):
        self.batches.append(
            {
                "run_id": run_id,
                "batch_id": batch_id,
                "task_id": task_id,
                "dataset": dataset,
                "symbols": list(symbols or []),
                "symbols_json": json.dumps(list(symbols or []), ensure_ascii=False),
                "window_start": window_start,
                "window_end": window_end,
                "status": "success",
            }
        )

    def finish_batch(self, *args, **kwargs):
        pass

    def get_batches_for_run(self, run_id):
        return [dict(b) for b in self.batches if b["run_id"] == run_id]


class FakeEngine:
    def __init__(self):
        self.manifest = FakeManifest()
        self.calls = []
        self.contexts = []
        self.compact_result = {"status": "success"}
        self.chunk_symbols = None
        self.chunk_window = ("2016-01-01", "2026-08-17")
        self.corp_result = {
            "status": "success",
            "rows_read": 0,
            "rows_written": 0,
            "failed_symbols": [],
        }

    def run_step(self, name, trade_date, run_id, context=None):
        self.calls.append(name)
        ctx = dict(context or {})
        self.contexts.append((name, ctx))
        if name == "corporate_actions":
            got = ctx.get("_retry_symbols") or []
            write_symbols = (
                list(got) if self.chunk_symbols is None else list(self.chunk_symbols)
            )
            w_start, w_end = self.chunk_window
            # simulate the pinned step writing child chunk receipts from its
            # explicit symbol scope
            for i in range(max(1, len(write_symbols))):
                self.manifest.start_batch(
                    run_id,
                    f"chunk-{i:04d}",
                    task_id="corporate_actions_chunk",
                    dataset="corporate_actions",
                    symbols=write_symbols,
                    window_start=w_start,
                    window_end=w_end,
                )
            return dict(self.corp_result)
        if name == "compact":
            return dict(self.compact_result)
        return {"status": "success"}


def cfg_factory():
    return SimpleNamespace(
        failover_enabled=True,
        _backfill=False,
        _backfill_start=None,
        _backfill_end=None,
    )


def tmp_config_file(tmp_path, root: Path):
    p = tmp_path / "cnequity.toml"
    p.write_text(f'root = "{root}"\n', encoding="utf-8")
    return p


def test_valid_24_scope_accepted(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    cfg_path = tmp_config_file(tmp_path, root)
    report = adapter.run_bounded_pilot(
        VALID,
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    assert report["STATUS"] == "PILOT_COMPLETE"
    assert report["PILOT_COMPLETE"] is True
    assert report["REQUESTED_SYMBOL_N"] == 24


def test_more_than_24_rejected_before_engine(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    symbols = VALID + ["600009.SH"]
    report = adapter.run_bounded_pilot(
        symbols,
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    assert report["STATUS"] == "BOUNDED_SCOPE_VIOLATION"
    assert eng.manifest.runs == [] and eng.calls == []


def test_empty_symbol_list_rejected(tmp_path):
    root = tmp_path / "root"
    report = adapter.run_bounded_pilot(
        [],
        root=root,
        cfg=cfg_factory(),
        identity=make_identity(VALID),
    )
    assert report["STATUS"] == "BOUNDED_SCOPE_VIOLATION"


def test_symbol_outside_identity_rejected(tmp_path):
    root = tmp_path / "root"
    report = adapter.run_bounded_pilot(
        ["600123.SH"],
        root=root,
        cfg=cfg_factory(),
        identity=make_identity(["600000.SH", "000001.SZ"]),
    )
    assert report["STATUS"] == "BOUNDED_SCOPE_VIOLATION"
    assert any("outside frozen identity" in e for e in report["errors"])


def test_bj_rejected(tmp_path):
    root = tmp_path / "root"
    report = adapter.run_bounded_pilot(
        ["430001.BJ"],
        root=root,
        cfg=cfg_factory(),
        identity=make_identity(VALID),
    )
    assert report["STATUS"] == "BOUNDED_SCOPE_VIOLATION"
    assert any("non-canonical or BJ" in e for e in report["errors"])


def test_duplicate_symbols_rejected(tmp_path):
    root = tmp_path / "root"
    report = adapter.run_bounded_pilot(
        ["600000.SH", "600000.SH"],
        root=root,
        cfg=cfg_factory(),
        identity=make_identity(["600000.SH"]),
    )
    assert report["STATUS"] == "BOUNDED_SCOPE_VIOLATION"
    assert any("duplicate" in e for e in report["errors"])


def test_identity_hash_mismatch_rejected(tmp_path):
    root = tmp_path / "root"
    report = adapter.run_bounded_pilot(
        VALID[:1],
        root=root,
        cfg=cfg_factory(),
        identity=make_identity(VALID, match=False),
    )
    assert report["STATUS"] == "FORMAL_IDENTITY_MISMATCH"


def test_pin_mismatch_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        adapter,
        "verify_pin",
        lambda: {
            "PIN_EXPECTED": adapter.PIN_EXPECTED,
            "PIN_ACTUAL": "deadbeef",
            "PIN_MATCH": False,
        },
    )
    root = tmp_path / "root"
    eng = FakeEngine()
    report = adapter.run_bounded_pilot(
        VALID[:1],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    assert report["STATUS"] == "BOUNDED_ADAPTER_BLOCKED_PIN_MISMATCH"
    assert eng.manifest.runs == [] and eng.calls == []


def test_exact_subset_reaches_execution_context(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    subset = VALID[:3]
    adapter.run_bounded_pilot(
        subset,
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    ca_ctx = [c for (name, c) in eng.contexts if name == "corporate_actions"]
    assert ca_ctx and ca_ctx[0]["_retry_symbols"] == subset


def test_manifest_run_metadata_records_exact_subset(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    subset = VALID[:4]
    adapter.run_bounded_pilot(
        subset,
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    meta = eng.manifest.runs[0]["metadata"]
    assert meta["backfill_scope"]["symbols"] == subset
    assert meta["backfill_scope"]["start"] == "2016-01-01"
    assert meta["backfill_scope"]["end"] == "2026-08-17"


def test_child_receipt_symbols_remain_exact_subset(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    subset = VALID[:5]
    adapter.run_bounded_pilot(
        subset,
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    assert eng.manifest.batches
    for b in eng.manifest.batches:
        assert b["dataset"] == "corporate_actions"
        assert b["symbols"] == subset


def test_no_full_universe_fallback(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    subset = VALID[:2]
    adapter.run_bounded_pilot(
        subset,
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    # the ONLY symbol source is the explicit context scope; no load_symbols
    for (name, c) in eng.contexts:
        if name == "corporate_actions":
            assert set(c.get("_retry_symbols") or []) == set(subset)


def test_failover_disabled_for_bounded_run(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    cfg = cfg_factory()
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg,
        engine=eng,
        identity=make_identity(VALID),
    )
    assert report["FAILOVER_BACKUP_ENABLED"] is False
    assert report["FAILOVER_BACKUP_UNBOUNDED"] is True
    # the bounded run disabled the backup, then restored the caller's value
    assert report["CONFIG_STATE_RESTORED"] is True
    assert cfg.failover_enabled is True


def test_persistent_config_unchanged(tmp_path):
    root = tmp_path / "root"
    cfg_path = tmp_config_file(tmp_path, root)
    before = adapter.sha256_text(cfg_path)
    eng = FakeEngine()
    report = adapter.run_bounded_pilot(
        VALID[:1],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    after = adapter.sha256_text(cfg_path)
    assert before == after
    assert report["PERSISTENT_CONFIG_CHANGED"] is False
    assert report["config_sha256_before"] == report["config_sha256_after"]


def test_dry_run_zero_manifest_write(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        dry_run=True,
        engine=eng,
        identity=make_identity(VALID),
    )
    assert report["DRY_RUN_STATUS"] == "OK"
    assert report["MANIFEST_WRITE"] == "NO"
    assert eng.manifest.runs == [] and eng.manifest.finishes == []
    assert eng.calls == []


def test_dry_run_zero_provider_call(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        dry_run=True,
        engine=eng,
        identity=make_identity(VALID),
    )
    assert report["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert report["REAL_ROOT_WRITE"] == "NO"
    assert report["CONFIG_STATE_RESTORED"] is True
    assert eng.calls == []


def test_step_failure_not_complete(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    eng.corp_result = {
        "status": "failed",
        "rows_read": 1,
        "rows_written": 1,
        "failed_symbols": ["600000.SH"],
    }
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    assert report["STATUS"] == "PILOT_INCOMPLETE"
    assert report["PILOT_COMPLETE"] is False
    assert report["failed_symbols"] == ["600000.SH"]
    assert report["final_status"] == "failed"
    # provider (corporate_actions) execution path was entered -> NOT "NO"
    assert report["PROVIDER_STEP_ENTERED"] == "YES"
    assert report["NETWORK_PROVIDER_DATA_FETCH"] != "NO"
    assert report["MANIFEST_WRITE"] == "YES"


def test_compact_finish_ordering_matches_pinned_lifecycle(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    assert eng.calls == ["corporate_actions", "compact"]
    assert eng.manifest.runs and eng.manifest.finishes
    assert eng.manifest.finishes[-1]["run_id"] == "run-fake-1"


def test_zero_event_success_keeps_receipt_semantics(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    cfg_path = tmp_config_file(tmp_path, root)
    eng.corp_result = {
        "status": "success",
        "rows_read": 0,
        "rows_written": 0,
        "failed_symbols": [],
    }
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    assert report["STATUS"] == "PILOT_COMPLETE"
    assert report["PILOT_COMPLETE"] is True
    assert report["step_result"]["rows_written"] == 0
    assert report["receipt_post_check"]["STATUS"] == "OK"
    # receipts still carry the exact symbols (0 rows is a legal sparse result)
    assert eng.manifest.batches
    for b in eng.manifest.batches:
        assert b["symbols"] == VALID[:2]


def test_no_direct_downloader_in_source():
    src = Path(adapter.__file__).read_text(encoding="utf-8")
    for banned in (
        "fetch_corporate_actions_tdx",
        "fetch_corporate_actions_eastmoney",
        "import requests",
        "from cnequity.adapters.tdx_protocol",
        "urllib",
        "corporate_actions_tdx",
    ):
        assert banned not in src, f"banned downloader path in adapter source: {banned}"
    # wrapper-only proof: the ONLY fetch path is the pinned registered step
    assert 'run_step("corporate_actions"' in src


# ---- identity execution contract -----------------------------------------
def raw_identity_shape(
    n=None,
    *,
    identity_hash=None,
    identity_ok=True,
    status="PASS",
):
    n = adapter.FORMAL_IDENTITY_N if n is None else n
    syms = sorted({f"{i:06d}.SH" for i in range(n)})
    return {
        "identity_ok": identity_ok,
        "IDENTITY_STATUS": status,
        "EXPECTED_SYMBOL_N": n,
        "EXPECTED_SYMBOL_HASH": (
            adapter.FORMAL_IDENTITY_HASH if identity_hash is None else identity_hash
        ),
        "IDENTITY_SOURCE": "RAW_TEST",
        "symbols": syms,
    }


def test_canonical_valid_identity_pass():
    nrm = adapter.normalize_bounded_identity(raw_identity_shape())
    assert nrm["IDENTITY_MATCH"] is True
    assert nrm["FORMAL_IDENTITY_N"] == adapter.FORMAL_IDENTITY_N
    assert nrm["FORMAL_IDENTITY_HASH"] == adapter.FORMAL_IDENTITY_HASH
    assert len(nrm["symbols"]) == adapter.FORMAL_IDENTITY_N


def test_identity_hash_mismatch_reject():
    nrm = adapter.normalize_bounded_identity(
        raw_identity_shape(identity_hash="0" * 64)
    )
    assert nrm["IDENTITY_MATCH"] is False


def test_identity_n_mismatch_reject():
    nrm = adapter.normalize_bounded_identity(
        raw_identity_shape(n=adapter.FORMAL_IDENTITY_N - 1)
    )
    assert nrm["IDENTITY_MATCH"] is False


def test_identity_missing_identity_ok_reject():
    raw = raw_identity_shape()
    del raw["identity_ok"]
    assert adapter.normalize_bounded_identity(raw)["IDENTITY_MATCH"] is False


def test_identity_symbols_length_mismatch_reject():
    raw = raw_identity_shape()
    raw["symbols"] = raw["symbols"][: adapter.FORMAL_IDENTITY_N - 1]
    assert adapter.normalize_bounded_identity(raw)["IDENTITY_MATCH"] is False


def test_real_identity_shape_passes_real_adapter_identity_scope_gate(tmp_path):
    # Real load_expected_identity-shaped dict -> canonical normalizer -> the
    # real run_bounded_pilot must pass its identity gate and bounded-scope gate
    # (no FORMAL_IDENTITY_MISMATCH), and must NOT enter the provider.
    shape = raw_identity_shape()
    two = shape["symbols"][:2]
    report = adapter.run_bounded_pilot(
        two,
        root=tmp_path / "root",
        cfg=cfg_factory(),
        dry_run=True,
        identity=shape,
    )
    assert report["STATUS"] != "FORMAL_IDENTITY_MISMATCH"
    assert report["STATUS"] == "READY"
    assert report["DRY_RUN_STATUS"] == "OK"
    assert report["NETWORK_PROVIDER_DATA_FETCH"] == "NO"


# ---- CLI mode tests --------------------------------------------------------
def _load_cli():
    import importlib.util

    tool_p = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "run_r4a0_corporate_actions_pilot.py"
    )
    spec = importlib.util.spec_from_file_location("r4a0_cli", tool_p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _cli_run(monkeypatch, argv, report=None):
    mod = _load_cli()
    calls = []

    def fake_pilot(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return report if report is not None else {"STATUS": "READY"}

    monkeypatch.setattr(mod, "run_bounded_pilot", fake_pilot)
    monkeypatch.setattr("sys.argv", ["r4a0_cli"] + argv)
    rc = mod.main()
    return rc, calls, mod


def test_cli_default_dry_run(monkeypatch):
    rc, calls, _ = _cli_run(monkeypatch, ["--symbols", "600000.SH"])
    assert rc == 0
    assert calls and calls[0]["kwargs"]["dry_run"] is True


def test_cli_dry_run_flag(monkeypatch):
    rc, calls, _ = _cli_run(monkeypatch, ["--dry-run", "--symbols", "600000.SH"])
    assert rc == 0
    assert calls and calls[0]["kwargs"]["dry_run"] is True


def test_cli_exec_reachable(monkeypatch):
    rc, calls, _ = _cli_run(monkeypatch, ["--exec", "--symbols", "600000.SH"])
    assert rc == 0
    assert calls is not None and len(calls) == 1
    assert calls[0]["kwargs"]["dry_run"] is False


def test_cli_conflicting_flags_rejected(monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        _cli_run(monkeypatch, ["--dry-run", "--exec", "--symbols", "600000.SH"])
    assert excinfo.value.code == 2


# ---- execution hardening tests --------------------------------------------
def test_compact_failed_incomplete(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    eng.compact_result = {"status": "failed"}
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    assert report["STATUS"] == "PILOT_INCOMPLETE"
    assert report["PILOT_COMPLETE"] is False
    assert report["compact_status"] == "failed"


def test_compact_warning_incomplete(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    eng.compact_result = {"status": "warning"}
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    assert report["STATUS"] == "PILOT_INCOMPLETE"
    assert report["PILOT_COMPLETE"] is False
    assert report["compact_status"] == "warning"


def test_incomplete_never_reports_network_no(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    eng.compact_result = {"status": "failed"}
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
    )
    assert report["PILOT_COMPLETE"] is False
    # the corporate provider step was entered -> network never "NO"
    assert report["PROVIDER_STEP_ENTERED"] == "YES"
    assert report["NETWORK_PROVIDER_DATA_FETCH"] in ("YES", "UNKNOWN")
    assert report["NETWORK_PROVIDER_REQUEST_COUNT"] == "UNVERIFIED"


def test_real_execution_manifest_write_yes(tmp_path):
    root = tmp_path / "root"
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=FakeEngine(),
        identity=make_identity(VALID),
    )
    assert report["MANIFEST_WRITE"] == "YES"


def test_real_execution_real_root_write_yes(tmp_path):
    root = tmp_path / "root"
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=FakeEngine(),
        identity=make_identity(VALID),
    )
    assert report["REAL_ROOT_WRITE"] == "YES"


def test_success_config_restored(tmp_path):
    root = tmp_path / "root"
    cfg = cfg_factory()
    cfg_path = tmp_config_file(tmp_path, root)
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg,
        engine=FakeEngine(),
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    assert report["STATUS"] == "PILOT_COMPLETE"
    assert cfg.failover_enabled is True
    assert cfg._backfill is False
    assert cfg._backfill_start is None
    assert cfg._backfill_end is None


def test_failure_config_restored(tmp_path):
    root = tmp_path / "root"
    cfg = cfg_factory()
    eng = FakeEngine()
    eng.corp_result = {
        "status": "failed",
        "failed_symbols": ["600000.SH"],
        "rows_read": 0,
        "rows_written": 0,
    }
    adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg,
        engine=eng,
        identity=make_identity(VALID),
    )
    assert cfg.failover_enabled is True


def test_exception_config_restored(tmp_path):
    root = tmp_path / "root"
    cfg = cfg_factory()

    class RaisingEngine(FakeEngine):
        def run_step(self, name, trade_date, run_id, context=None):
            self.calls.append(name)
            raise RuntimeError("boom")

    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg,
        engine=RaisingEngine(),
        identity=make_identity(VALID),
    )
    assert report["STATUS"] == "EXECUTION_ERROR"
    assert report["PILOT_COMPLETE"] is False
    assert report["PROVIDER_STEP_ENTERED"] == "YES"
    assert report["NETWORK_PROVIDER_DATA_FETCH"] == "UNKNOWN"
    assert cfg.failover_enabled is True


def test_dry_run_config_restored(tmp_path):
    root = tmp_path / "root"
    cfg = cfg_factory()
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg,
        dry_run=True,
        identity=make_identity(VALID),
    )
    assert report["DRY_RUN_STATUS"] == "OK"
    assert cfg.failover_enabled is True
    assert report["CONFIG_STATE_RESTORED"] is True


def test_config_hash_mutation_boundary_breach(tmp_path, monkeypatch):
    root = tmp_path / "root"
    seq = {"n": 0}

    def fake_sha(path):
        seq["n"] += 1
        return "AA" if seq["n"] == 1 else "BB"

    monkeypatch.setattr(adapter, "sha256_text", fake_sha)
    cfg = cfg_factory()
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg,
        engine=FakeEngine(),
        identity=make_identity(VALID),
        config_path=tmp_path / "cnequity.toml",
    )
    assert report["STATUS"] == "WRITE_BOUNDARY_BREACH"
    assert report["PILOT_COMPLETE"] is False
    assert report["PERSISTENT_CONFIG_CHANGED"] is True
    assert report["CONFIG_INTEGRITY_STATUS"] == "CHANGED"
    assert cfg.failover_enabled is True  # restored even on boundary breach


# ---- V02: receipt / runtime-contract hardening ----------------------------
def test_receipt_real_sqlite3_row_contract(tmp_path):
    import sqlite3

    from datetime import date as _date

    from cnequity.orchestrator.manifest import Manifest

    db = tmp_path / "manifest.db"
    m = Manifest(db)
    run_id = m.start_run("backfill", {"backfill_scope": {}})
    m.start_batch(
        run_id,
        "b1",
        "corporate_actions_chunk",
        "corporate_actions",
        symbols=["600000.SH"],
        window_start="2016-01-01",
        window_end="2026-08-17",
        blocks_compaction=False,
    )
    m.finish_batch(run_id, "b1", "success", rows_read=0, rows_written=0)
    rows = m.get_batches_for_run(run_id)
    assert rows and isinstance(rows[0], sqlite3.Row)

    class Eng:
        manifest = m

    post = adapter.receipt_post_check(
        Eng(), run_id, ["600000.SH"], _date(2016, 1, 1), _date(2026, 8, 17)
    )
    assert post["STATUS"] == "OK"
    assert post["no_unexpected_symbols"] is True
    assert post["each_requested_symbol_receipted"] is True
    assert post["window_exact"] is True


def test_receipt_missing_symbol_incomplete(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    eng.chunk_symbols = ["600000.SH"]  # second requested symbol missing
    cfg_path = tmp_config_file(tmp_path, root)
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    assert report["receipt_post_check"]["STATUS"] == "MISMATCH"
    assert report["receipt_post_check"]["each_requested_symbol_receipted"] is False
    assert report["PILOT_COMPLETE"] is False
    assert report["STATUS"] == "PILOT_INCOMPLETE"


def test_receipt_unexpected_symbol_incomplete(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    eng.chunk_symbols = VALID[:2] + ["999999.SZ"]
    cfg_path = tmp_config_file(tmp_path, root)
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    assert report["receipt_post_check"]["STATUS"] == "MISMATCH"
    assert report["receipt_post_check"]["no_unexpected_symbols"] is False
    assert report["PILOT_COMPLETE"] is False
    assert report["STATUS"] == "PILOT_INCOMPLETE"


def test_receipt_wrong_window_incomplete(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    eng.chunk_window = ("2016-01-01", "2020-01-01")
    cfg_path = tmp_config_file(tmp_path, root)
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    assert report["receipt_post_check"]["STATUS"] == "MISMATCH"
    assert report["receipt_post_check"]["window_exact"] is False
    assert report["PILOT_COMPLETE"] is False
    assert report["STATUS"] == "PILOT_INCOMPLETE"


def test_receipt_manifest_read_exception_unknown_incomplete(tmp_path):
    root = tmp_path / "root"

    class RaisingManifest(FakeManifest):
        def get_batches_for_run(self, run_id):
            raise RuntimeError("manifest boom")

    eng = FakeEngine()
    eng.manifest = RaisingManifest()
    cfg_path = tmp_config_file(tmp_path, root)
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    assert report["receipt_post_check"]["STATUS"] == "UNKNOWN"
    assert report["PILOT_COMPLETE"] is False
    assert report["STATUS"] == "PILOT_INCOMPLETE"


def test_receipt_zero_event_exact_success_complete(tmp_path):
    root = tmp_path / "root"
    eng = FakeEngine()
    eng.corp_result = {
        "status": "success",
        "rows_read": 0,
        "rows_written": 0,
        "failed_symbols": [],
    }
    cfg_path = tmp_config_file(tmp_path, root)
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    assert report["receipt_post_check"]["STATUS"] == "OK"
    assert report["PILOT_COMPLETE"] is True
    assert report["STATUS"] == "PILOT_COMPLETE"


def test_config_unknown_blocks_complete(tmp_path, monkeypatch):
    root = tmp_path / "root"
    # config path exists but hash cannot be produced for neither side
    monkeypatch.setattr(adapter, "sha256_text", lambda _p: None)
    eng = FakeEngine()
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
        config_path=tmp_config_file(tmp_path, root),
    )
    assert report["CONFIG_INTEGRITY_STATUS"] == "UNKNOWN"
    assert report["PILOT_COMPLETE"] is False
    assert report["STATUS"] == "PILOT_INCOMPLETE"


def test_exception_run_finalized_failed_no_orphan(tmp_path):
    root = tmp_path / "root"
    cfg = cfg_factory()

    class FailingAfterStart(FakeEngine):
        def run_step(self, name, trade_date, run_id, context=None):
            self.calls.append(name)
            if name == "corporate_actions":
                raise RuntimeError("boom after run started")
            return {"status": "success"}

    eng = FailingAfterStart()
    cfg_path = tmp_config_file(tmp_path, root)
    report = adapter.run_bounded_pilot(
        VALID[:2],
        root=root,
        cfg=cfg,
        engine=eng,
        identity=make_identity(VALID),
        config_path=cfg_path,
    )
    assert report["STATUS"] == "EXECUTION_ERROR"
    assert report["EXCEPTION_RUN_FINALIZATION"] == "FAILED"
    assert report["PROVIDER_STEP_ENTERED"] == "YES"
    # no silent RUNNING orphan: the started run was finalized FAILED
    assert eng.manifest.finishes
    assert eng.manifest.finishes[-1]["status"] == "failed"
    assert cfg.failover_enabled is True
