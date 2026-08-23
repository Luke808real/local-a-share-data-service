"""Targeted tests for the R4A7 resumable full-universe preclose orchestrator.

All tests are offline: fake provider, tmp_path staging, injected identity.
No BaoStock login/query ever runs; no real data root is written.
"""

from __future__ import annotations

import json
import types
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from ashare_data.r4a7_preclose_full_extraction import (
    MANIFEST_SCHEMA_VERSION,
    aggregate_full_run,
    build_full_query_plan,
    contract_identity,
    formal_fact_hash,
    load_manifest,
    run_full_extraction,
    unit_complete_and_valid,
    write_atomic,
)


def _identity(symbols: list[str]) -> dict:
    return {
        "IDENTITY_STATUS": "PASS",
        "EXPECTED_SYMBOL_N": len(symbols),
        "EXPECTED_SYMBOL_HASH": "x" * 64,
        "IDENTITY_SOURCE": "INJECTED",
        "identity_ok": True,
        "symbols": symbols,
    }


def _bar_rows(symbol: str) -> list[dict]:
    return [
        {"symbol": symbol, "trade_date": date(2016, 1, 4), "close": 10.0},
        {"symbol": symbol, "trade_date": date(2016, 1, 5), "close": 10.25},
        {"symbol": symbol, "trade_date": date(2016, 1, 6), "close": 10.5},
    ]


def _make_root(tmp_path: Path, symbols: list[str]) -> Path:
    root = tmp_path / "root"
    for symbol in symbols:
        part = root / "curated" / "daily_bars" / f"symbol={symbol}"
        part.mkdir(parents=True)
        pl.DataFrame(_bar_rows(symbol)).write_parquet(part / "part.parquet")
    (root / "meta" / "asl" / "r3").mkdir(parents=True)
    manifest = root / "meta" / "manifest.db"
    manifest.touch()
    return root


def _provider_row(symbol: str, trade_date: date, preclose: float, tradestatus: str = "1") -> dict:
    code, exchange = symbol.split(".")
    return {
        "symbol": symbol,
        "date": trade_date.isoformat(),
        "code": ("sh" if exchange == "SH" else "sz") + "." + code,
        "preclose": str(preclose),
        "tradestatus": tradestatus,
    }


def _good_fetch(symbol: str) -> list[dict]:
    # 3 required rows (2016-01-04..06): preclose == previous local close.
    return [
        _provider_row(symbol, date(2016, 1, 4), 10.0),
        _provider_row(symbol, date(2016, 1, 5), 10.0),
        _provider_row(symbol, date(2016, 1, 6), 10.25),
    ]


def _sha40() -> str:
    return "ab" * 20


@pytest.fixture()
def ready_prereq(monkeypatch):
    monkeypatch.setattr(
        "ashare_data.r4a_preclose_bounded_adapter.r4a0_prerequisite",
        lambda _root: {
            "R4A0_READY": True,
            "R4A0_GATE_STATUS": "PASS",
            "R4A0_BLOCKER": "",
            "R3_IDENTITY_MATCH": True,
            "FORMAL_IDENTITY_N": 5456,
            "FORMAL_IDENTITY_HASH": "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f",
        },
    )


def test_A_deterministic_plan_twice_identical_hashes(tmp_path):
    root = _make_root(tmp_path, ["000001.SZ", "000002.SZ"])
    ident = _identity(["000002.SZ", "000001.SZ"])
    first = build_full_query_plan(root, identity=ident)
    second = build_full_query_plan(root, identity=ident)
    assert first["FULL_SYMBOL_N"] == second["FULL_SYMBOL_N"] == 2
    assert first["FULL_SYMBOL_HASH"] == second["FULL_SYMBOL_HASH"]
    assert first["FULL_QUERY_WINDOW_N"] == second["FULL_QUERY_WINDOW_N"]
    assert first["FULL_QUERY_PLAN_HASH"] == second["FULL_QUERY_PLAN_HASH"]
    # plan windows sorted by symbol then year
    symbols = [w["symbol"] for w in first["query_plan"]]
    assert symbols == sorted(symbols)


def test_B_resume_skips_complete_reruns_incomplete(tmp_path, ready_prereq):
    root = _make_root(tmp_path, ["000001.SZ"])
    staging = tmp_path / "staging"
    sha = _sha40()
    symbols = ["000001.SZ"]

    def fetch(window):
        return _good_fetch(window["symbol"])

    first = run_full_extraction(
        root,
        provider_fetch=fetch,
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(symbols),
    )
    assert first["STATUS"] == "COMPLETE_ALL_UNITS"
    assert first["COMPLETE_N"] == 1
    assert first["EXECUTED_N"] == 1

    calls: list[str] = []

    def counted_fetch(window):
        calls.append(window["symbol"])
        return _good_fetch(window["symbol"])

    second = run_full_extraction(
        root,
        provider_fetch=counted_fetch,
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(symbols),
    )
    assert second["SKIPPED_N"] == 1
    assert second["EXECUTED_N"] == 0
    assert calls == []  # valid completed unit is skipped, no provider call

    # Incomplete unit (drop receipt) reruns.
    manifest_path = staging / "manifest.json"
    manifest = load_manifest(manifest_path)
    manifest["units"]["000001.SZ"] = {"STATE": "PENDING"}
    write_atomic(manifest_path, json.dumps(manifest))
    third = run_full_extraction(
        root,
        provider_fetch=counted_fetch,
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(symbols),
    )
    assert third["EXECUTED_N"] == 1
    assert calls == ["000001.SZ"]


def test_B2_corrupted_receipt_fails_closed(tmp_path, ready_prereq):
    root = _make_root(tmp_path, ["000001.SZ"])
    staging = tmp_path / "staging"
    sha = _sha40()
    manifest_path = staging / "manifest.json"
    write_atomic(manifest_path, "{not json")
    with pytest.raises(RuntimeError, match="CHECKPOINT_CORRUPT"):
        run_full_extraction(
            root,
            provider_fetch=lambda _w: [],
            adapter_version=sha,
            expected_adapter_sha=sha,
            runtime_adapter_sha=sha,
            staging_root=staging,
            dry_run=True,
            identity=_identity(["000001.SZ"]),
        )


def test_B3_hash_mismatch_fails_validation(tmp_path, ready_prereq):
    root = _make_root(tmp_path, ["000001.SZ"])
    staging = tmp_path / "staging"
    sha = _sha40()
    run_full_extraction(
        root,
        provider_fetch=lambda w: _good_fetch(w["symbol"]),
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ"]),
    )
    formal_path = staging / "units" / "000001.SZ.parquet"
    # Tamper the staged output: content hash must no longer match receipt.
    frame = pl.read_parquet(formal_path).with_columns(
        pl.lit(999.0).alias("preclose")
    )
    frame.write_parquet(formal_path)
    manifest_path = staging / "manifest.json"
    manifest = load_manifest(manifest_path)
    receipt = manifest["units"]["000001.SZ"]
    assert unit_complete_and_valid(
        receipt,
        contract=receipt["contract"],
        formal_path=formal_path,
        expected_symbol="000001.SZ",
    ) is False


def test_C_interrupted_write_no_false_complete(tmp_path, ready_prereq):
    root = _make_root(tmp_path, ["000001.SZ", "000002.SZ"])
    staging = tmp_path / "staging"
    sha = _sha40()
    calls = {"n": 0}

    def fetch(window):
        calls["n"] += 1
        if window["symbol"] == "000002.SZ":
            raise RuntimeError("interrupted provider (simulated crash)")
        return _good_fetch(window["symbol"])

    result = run_full_extraction(
        root,
        provider_fetch=fetch,
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ", "000002.SZ"]),
    )
    assert result["STATUS"] == "STOPPED_UNIT_FAILED"
    assert result["units"]["000001.SZ"]["STATE"] == "COMPLETE"
    assert result["units"]["000002.SZ"]["STATE"] == "FAILED"
    manifest = load_manifest(staging / "manifest.json")
    assert manifest["units"]["000002.SZ"]["STATE"] == "FAILED"
    assert manifest["units"]["000001.SZ"]["STATE"] == "COMPLETE"
    # failed unit is NOT complete and must rerun on resume.
    assert unit_complete_and_valid(
        manifest["units"]["000002.SZ"],
        contract=manifest["contract"],
        formal_path=staging / "units" / "000002.SZ.parquet",
        expected_symbol="000002.SZ",
    ) is False


def test_D_provider_error_no_silent_retry(tmp_path, ready_prereq):
    root = _make_root(tmp_path, ["000001.SZ"])
    staging = tmp_path / "staging"
    sha = _sha40()
    attempts = {"n": 0}

    def failing_fetch(window):
        attempts["n"] += 1
        raise RuntimeError("PROVIDER_ERROR boom")

    result = run_full_extraction(
        root,
        provider_fetch=failing_fetch,
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ"]),
    )
    assert result["STATUS"] == "STOPPED_UNIT_FAILED"
    assert attempts["n"] == 1  # no silent retry
    manifest = load_manifest(staging / "manifest.json")
    assert "PROVIDER_ERROR" in manifest["units"]["000001.SZ"]["error"]
    assert manifest["units"]["000001.SZ"]["STATE"] == "FAILED"


def test_E_contract_drift_blocks_reuse(tmp_path, ready_prereq):
    root = _make_root(tmp_path, ["000001.SZ"])
    staging = tmp_path / "staging"
    sha = _sha40()
    as_of_a = date(2016, 1, 6)
    result = run_full_extraction(
        root,
        provider_fetch=lambda w: _good_fetch(w["symbol"]),
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=False,
        as_of=as_of_a,
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ"]),
    )
    assert result["STATUS"] == "COMPLETE_ALL_UNITS"
    # Contract drift: different AS_OF changes the executable contract.
    drifted = run_full_extraction(
        root,
        provider_fetch=lambda w: _good_fetch(w["symbol"]),
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=True,
        as_of=date(2017, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ"]),
    )
    assert drifted["STATUS"] == "CHECKPOINT_CONTRACT_DRIFT"
    # Adapter authority drift also blocks reuse.
    drifted2 = run_full_extraction(
        root,
        provider_fetch=lambda w: _good_fetch(w["symbol"]),
        adapter_version="cd" * 20,
        expected_adapter_sha="cd" * 20,
        runtime_adapter_sha="cd" * 20,
        staging_root=staging,
        dry_run=True,
        as_of=as_of_a,
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ"]),
    )
    assert drifted2["STATUS"] == "CHECKPOINT_CONTRACT_DRIFT"


def test_F_r4a6_pilot_plan_hash_golden():
    # R4A6 real pilot QUERY_PLAN_HASH for the frozen 24-symbol manifest is
    # frozen evidence; the orchestrator must reproduce the identical plan
    # hash for the same symbol set (no network, no fetch).
    import json as _json
    from ashare_data.r4a_preclose_bounded_adapter import (
        AS_OF as _AS_OF,
        WINDOW_START as _WS,
        load_pilot_symbols,
    )

    repo_root = Path(__file__).resolve().parents[1]
    pilot = load_pilot_symbols(repo_root)
    plan = build_full_query_plan(
        Path("/nonexistent"),
        as_of=_AS_OF,
        window_start=_WS,
        identity=_identity(pilot["pilot_symbols"]),
    )
    assert plan["FULL_SYMBOL_N"] == 24
    # R4A6 receipt frozen QUERY_PLAN_HASH=1228af76...
    assert plan["FULL_QUERY_WINDOW_N"] == 264
    assert plan["FULL_QUERY_PLAN_HASH"] == (
        "1228af76ccdfee032de437c0f47ab343248f9c94b0a15961bd6fc18fd99e2e01"
    )
    # Deterministic formal hash contract (same sorted-tuple semantics).
    rows = [
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 4), "preclose": 11.99},
        {"symbol": "000002.SZ", "trade_date": date(2016, 1, 4), "preclose": 24.43},
    ]
    h1 = formal_fact_hash(rows)
    h2 = formal_fact_hash(list(reversed(rows)))
    assert h1 == h2


def test_aggregate_full_run_counts_and_candidate(tmp_path, ready_prereq):
    root = _make_root(tmp_path, ["000001.SZ"])
    staging = tmp_path / "staging"
    sha = _sha40()
    run_full_extraction(
        root,
        provider_fetch=lambda w: _good_fetch(w["symbol"]),
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ"]),
    )
    agg = aggregate_full_run(
        staging_root=staging,
        symbols=["000001.SZ"],
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
    )
    assert agg["units_complete_n"] == 1
    assert agg["REQUIRED_ROW_N"] == 3
    assert agg["FORMAL_FACT_ROW_N"] == 3
    assert agg["MISSING_REQUIRED_N"] == 0
    assert agg["PROVIDER_SUSPENDED_SUPERSET_N"] == 0
    assert agg["COVERAGE_COMPLETE"] is True
    # Gates need context/fixture data in this bounded test; without them the
    # validator reports NOT_RUN and the candidate stays false (UNKNOWN != PASS).
    assert agg["WINDOW_BOUNDARY"]["status"] in (
        "SKIPPED_INCOMPLETE", "NOT_RUN", "NOT_RUN_NO_ROOT"
    )
    assert agg["PRECLOSE_COMPLETE_CANDIDATE"] is False
    assert agg["PRECLOSE_COMPLETE"] is False
    assert agg["MARKET_DATA_WRITE"] == "NO"
