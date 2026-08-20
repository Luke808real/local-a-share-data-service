"""Offline tests for the R4A0 bounded corporate_actions execution adapter.

Fake engine/manifest + injected identity + mocked pin only. No real provider,
no real data root, no manifest on the real lake, no network.
"""

from __future__ import annotations

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
                "window_start": window_start,
                "window_end": window_end,
            }
        )

    def finish_batch(self, *args, **kwargs):
        pass


class FakeEngine:
    def __init__(self):
        self.manifest = FakeManifest()
        self.calls = []
        self.contexts = []
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
            # simulate the pinned step writing child chunk receipts from its
            # explicit symbol scope
            for i in range(max(1, len(got))):
                self.manifest.start_batch(
                    run_id,
                    f"chunk-{i:04d}",
                    task_id="corporate_actions_chunk",
                    dataset="corporate_actions",
                    symbols=got,
                    window_start="2016-01-01",
                    window_end="2026-08-17",
                )
            return dict(self.corp_result)
        if name == "compact":
            return {"status": "success"}
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
    report = adapter.run_bounded_pilot(
        VALID,
        root=root,
        cfg=cfg_factory(),
        engine=eng,
        identity=make_identity(VALID),
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
    assert cfg.failover_enabled is False
    assert report["FAILOVER_BACKUP_ENABLED"] is False
    assert report["FAILOVER_BACKUP_UNBOUNDED"] is True


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
    assert report["MANIFEST_WRITE"] is False
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
    assert report["NETWORK_PROVIDER_DATA_FETCH"] == 0
    assert report["REAL_ROOT_WRITE"] is False
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
    )
    assert report["STATUS"] == "PILOT_COMPLETE"
    assert report["PILOT_COMPLETE"] is True
    assert report["step_result"]["rows_written"] == 0
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
