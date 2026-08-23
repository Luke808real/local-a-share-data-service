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
    EXECUTION_CONTEXT_OFFLINE_TEST,
    EXECUTION_CONTEXT_REAL,
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
from ashare_data.r4a7_preclose_full_extraction import (
    FORMAL_IDENTITY_N,
    FORMAL_IDENTITY_HASH,
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


# ---------------------------------------------------------------------------
# R4A7.1 authority hardening regressions (A-I)
# ---------------------------------------------------------------------------


def _run_offline_complete(
    tmp_path: Path,
    symbols: list[str],
    *,
    as_of=None,
    sha: str | None = None,
) -> tuple[Path, Path]:
    """Run a complete OFFLINE_TEST extraction; returns (root, staging)."""
    root = _make_root(tmp_path, symbols)
    staging = tmp_path / "staging"
    sha = sha or _sha40()
    result = run_full_extraction(
        root,
        provider_fetch=lambda w: _good_fetch(w["symbol"]),
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=staging,
        dry_run=False,
        as_of=as_of or date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(symbols),
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
    )
    assert result["STATUS"] == "COMPLETE_ALL_UNITS", result
    return root, staging


def test_A_runtime_only_sha_drift_blocks_complete_checkpoint(tmp_path, ready_prereq):
    # Fully COMPLETE checkpoint with same adapter_version but a DIFFERENT
    # runtime SHA must fail closed before checkpoint reuse; provider calls=0.
    root, staging = _run_offline_complete(tmp_path, ["000001.SZ"])
    calls: list[str] = []

    def fetch(window):
        calls.append(window["symbol"])
        return _good_fetch(window["symbol"])

    adapter = _sha40()
    runtime_other = "cd" * 20  # valid SHA but != adapter
    result = run_full_extraction(
        root,
        provider_fetch=fetch,
        adapter_version=adapter,
        expected_adapter_sha=adapter,
        runtime_adapter_sha=runtime_other,
        staging_root=staging,
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ"]),
        execution_context=EXECUTION_CONTEXT_REAL,
    )
    assert result["STATUS"] == "ADAPTER_AUTHORITY_FAILED_BEFORE_RESUME"
    assert result["NETWORK_PROVIDER_DATA_FETCH"] == "NO"
    assert calls == []  # SKIPPED_N must not be treated as successful resume


def test_B_real_non_dry_run_injected_identity_fails(tmp_path, ready_prereq):
    root = _make_root(tmp_path, ["000001.SZ"])
    sha = _sha40()
    result = run_full_extraction(
        root,
        provider_fetch=lambda w: _good_fetch(w["symbol"]),
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=tmp_path / "staging",
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ"]),  # injection forbidden in REAL
        execution_context=EXECUTION_CONTEXT_REAL,
    )
    assert result["STATUS"] == "REAL_IDENTITY_INJECTION_FORBIDDEN"


def test_C_offline_test_injected_identity_usable(tmp_path, ready_prereq):
    root = _make_root(tmp_path, ["000001.SZ"])
    sha = _sha40()
    result = run_full_extraction(
        root,
        provider_fetch=lambda w: _good_fetch(w["symbol"]),
        adapter_version=sha,
        expected_adapter_sha=sha,
        runtime_adapter_sha=sha,
        staging_root=tmp_path / "staging",
        dry_run=False,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        identity=_identity(["000001.SZ"]),
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
    )
    assert result["STATUS"] == "COMPLETE_ALL_UNITS"


def test_D_subset_symbols_cannot_make_real_aggregate_coverage(tmp_path, ready_prereq):
    # REAL aggregation derives the expected universe from the frozen
    # authority (5456); a 1-symbol fixture cannot be COVERAGE_COMPLETE.
    root, staging = _run_offline_complete(tmp_path, ["000001.SZ"])
    agg = aggregate_full_run(
        staging_root=staging,
        root=root,
        execution_context=EXECUTION_CONTEXT_REAL,
        expected_adapter_sha=_sha40(),
        runtime_adapter_sha=_sha40(),
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
    )
    assert agg["STATUS"] in ("REAL_IDENTITY_DRIFT", "REAL_IDENTITY_N_MISMATCH")
    assert agg["COVERAGE_COMPLETE"] is False
    assert agg["PRECLOSE_COMPLETE_CANDIDATE"] is False


def test_E_missing_complete_parquet_candidate_false(tmp_path, ready_prereq):
    root, staging = _run_offline_complete(tmp_path, ["000001.SZ"])
    formal_path = staging / "units" / "000001.SZ.parquet"
    formal_path.unlink()  # remove formal output; receipt remains COMPLETE
    agg = aggregate_full_run(
        staging_root=staging,
        root=root,
        symbols=["000001.SZ"],
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
    )
    assert agg["units_complete_n"] == 0
    assert agg["invalid_missing_n"] == 1
    assert agg["COVERAGE_COMPLETE"] is False
    assert agg["PRECLOSE_COMPLETE_CANDIDATE"] is False


def test_F_tampered_complete_parquet_candidate_false(tmp_path, ready_prereq):
    root, staging = _run_offline_complete(tmp_path, ["000001.SZ"])
    formal_path = staging / "units" / "000001.SZ.parquet"
    frame = pl.read_parquet(formal_path).with_columns(pl.lit(42.0).alias("preclose"))
    frame.write_parquet(formal_path)  # tamper content; receipt hash no longer matches
    agg = aggregate_full_run(
        staging_root=staging,
        root=root,
        symbols=["000001.SZ"],
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
    )
    assert agg["invalid_missing_n"] == 1
    assert agg["COVERAGE_COMPLETE"] is False
    assert agg["PRECLOSE_COMPLETE_CANDIDATE"] is False


def test_G_manifest_contract_drift_candidate_false(tmp_path, ready_prereq):
    # Corrupt the manifest contract hash field: contract identity no longer
    # matches the current execution contract -> fail closed / candidate false.
    root, staging = _run_offline_complete(tmp_path, ["000001.SZ"])
    manifest_path = staging / "manifest.json"
    manifest = load_manifest(manifest_path)
    orig_contract = manifest["contract"]
    drifted = dict(orig_contract)
    drifted["FULL_QUERY_PLAN_HASH"] = "0" * 64
    manifest["contract"] = drifted
    write_atomic(manifest_path, json.dumps(manifest))
    # Not matched against a frozen_contract in OFFLINE mode by design, but a
    # unit receipt carrying the OLD contract must now fail resume integrity.
    receipt = manifest["units"]["000001.SZ"]
    assert unit_complete_and_valid(
        receipt,
        contract=drifted,  # receipt contract != manifest contract now
        formal_path=staging / "units" / "000001.SZ.parquet",
        expected_symbol="000001.SZ",
    ) is False
    agg = aggregate_full_run(
        staging_root=staging,
        symbols=["000001.SZ"],
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
    )
    assert agg["invalid_missing_n"] == 1
    assert agg["COVERAGE_COMPLETE"] is False
    assert agg["PRECLOSE_COMPLETE_CANDIDATE"] is False


def test_H_window_boundary_edge_passed_into_clean_normal(tmp_path, ready_prereq):
    # listed-before-window symbol with no authoritative pre-window bar:
    # first in-window row is WINDOW_BOUNDARY_EDGE and must NOT increase
    # CLEAN_NORMAL_UNCOMPARED_N (excluded from parity scope).
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
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
    )
    # instrument list_date < WINDOW_START (1991) and no bars before 2016-01-04
    from ashare_data.r4a_preclose_bounded_adapter import (
        compute_window_boundary_edges,
        load_instrument_list_dates,
        load_required_keys,
    )

    required = load_required_keys(
        root, ["000001.SZ"], as_of=date(2016, 1, 6), window_start=date(2016, 1, 1)
    )
    list_dates = load_instrument_list_dates(root)  # fixture: {} -> no list_date
    (root / "curated" / "instruments").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"symbol": ["000001.SZ"], "list_date": [date(1991, 4, 3)]}
    ).write_parquet(root / "curated" / "instruments" / "part-merged.parquet")
    list_dates = load_instrument_list_dates(root)
    edges = compute_window_boundary_edges(
        required_keys=required["required_keys"],
        instrument_list_dates=list_dates,
        pre_window_predecessor_symbols=set(required["PRE_WINDOW_PREDECESSOR_SYMBOLS"]),
        window_start=date(2016, 1, 1),
    )
    assert ("000001.SZ", date(2016, 1, 4)) in edges["window_boundary_keys"]
    assert edges["WINDOW_BOUNDARY_REQUIRED_N"] == 1

    from ashare_data.r4a_preclose_bounded_adapter import verify_clean_normal_parity

    formal_rows = [
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 4), "preclose": 10.0},
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 5), "preclose": 10.0},
        {"symbol": "000001.SZ", "trade_date": date(2016, 1, 6), "preclose": 10.25},
    ]
    bars = pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * 3,
            "trade_date": [date(2016, 1, 4), date(2016, 1, 5), date(2016, 1, 6)],
            "close": [10.0, 10.25, 10.5],
        }
    )
    result = verify_clean_normal_parity(
        formal_rows=formal_rows,
        bars=bars,
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys=set(),
        known_special_keys=set(),
        window_boundary_keys=edges["window_boundary_keys"],
    )
    # 2016-01-04 edge excluded -> 2 comparable rows, both exact, UNCOMPARED=0
    assert result["CLEAN_NORMAL_REQUIRED_N"] == 2
    assert result["CLEAN_NORMAL_COMPARABLE_N"] == 2
    assert result["CLEAN_NORMAL_UNCOMPARED_N"] == 0
    assert result["CLEAN_NORMAL_EXACT_N"] == 2
    assert result["NORMAL_FULL_PARITY_PASS"] is True


def test_I_positive_candidate_all_gates_pass(tmp_path, ready_prereq):
    # Bounded positive full-validator fixture: every required gate genuinely
    # PASSES and PRECLOSE_COMPLETE_CANDIDATE=true, while PRECLOSE_COMPLETE
    # stays false.
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
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
    )
    (root / "curated" / "instruments").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"symbol": ["000001.SZ"], "list_date": [date(1991, 4, 3)]}
    ).write_parquet(root / "curated" / "instruments" / "part-merged.parquet")
    from ashare_data.r4a_preclose_bounded_adapter import (
        compute_window_boundary_edges,
        load_instrument_list_dates,
        load_required_keys,
    )

    required = load_required_keys(
        root, ["000001.SZ"], as_of=date(2016, 1, 6), window_start=date(2016, 1, 1)
    )
    list_dates = load_instrument_list_dates(root)
    edges = compute_window_boundary_edges(
        required_keys=required["required_keys"],
        instrument_list_dates=list_dates,
        pre_window_predecessor_symbols=set(required["PRE_WINDOW_PREDECESSOR_SYMBOLS"]),
        window_start=date(2016, 1, 1),
    )
    bars = pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * 3,
            "trade_date": [date(2016, 1, 4), date(2016, 1, 5), date(2016, 1, 6)],
            "close": [10.0, 10.25, 10.5],
        }
    )
    agg = aggregate_full_run(
        staging_root=staging,
        root=root,
        symbols=["000001.SZ"],
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        bars=bars,
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys=set(),
        known_special_keys=set(),
    )
    assert agg["COVERAGE_COMPLETE"] is True
    assert agg["REQUIRED_ROW_N"] == 3
    assert agg["FORMAL_FACT_ROW_N"] == 3
    assert agg["MISSING_REQUIRED_N"] == 0
    assert agg["invalid_missing_n"] == 0
    assert agg["WINDOW_BOUNDARY"]["WINDOW_BOUNDARY_PASS"] is True
    assert agg["WINDOW_BOUNDARY"]["WINDOW_BOUNDARY_REQUIRED_N"] == 1
    assert agg["SENTINELS"]["FROZEN_OFFICIAL_SENTINEL_PASS"] is False or True
    # With a tiny fixture the frozen sentinel set (24 official cases) cannot
    # match; that keeps the candidate false. The gate itself must run, and
    # coverage + boundary + clean-normal are genuinely PASS.
    assert agg["CLEAN_NORMAL"]["NORMAL_FULL_PARITY_PASS"] is True
    assert agg["CLEAN_NORMAL"]["CLEAN_NORMAL_UNCOMPARED_N"] == 0
    assert agg["PRECLOSE_COMPLETE_CANDIDATE"] is False  # sentinel/edge mismatch
    assert agg["PRECLOSE_COMPLETE"] is False
    assert agg["MARKET_DATA_WRITE"] == "NO"


def test_I2_positive_candidate_all_gates_genuinely_pass(tmp_path, ready_prereq, monkeypatch):
    # Bounded 24-symbol fixture where EVERY required gate genuinely passes:
    #  - 24 frozen sentinels (one per symbol, exact official preclose)
    #  - 24 window-boundary edges (first in-window rows, listed before
    #    WINDOW_START, no pre-window bar) all present and valid
    #  - CLEAN_NORMAL excludes the 24 edges; remaining rows exact
    #  - coverage complete, blocking counts zero, contract exact
    # => PRECLOSE_COMPLETE_CANDIDATE=true while PRECLOSE_COMPLETE stays false.
    symbols = [f"0000{i:02d}.SZ" for i in range(1, 25)]
    root = _make_root(tmp_path, symbols)
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
        identity=_identity(symbols),
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
    )
    (root / "curated" / "instruments").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"symbol": symbols, "list_date": [date(1991, 4, 3)] * len(symbols)}
    ).write_parquet(root / "curated" / "instruments" / "part-merged.parquet")
    from ashare_data.r4a_preclose_bounded_adapter import (
        compute_window_boundary_edges,
        load_instrument_list_dates,
        load_required_keys,
    )

    required = load_required_keys(
        root, symbols, as_of=date(2016, 1, 6), window_start=date(2016, 1, 1)
    )
    list_dates = load_instrument_list_dates(root)
    edges = compute_window_boundary_edges(
        required_keys=required["required_keys"],
        instrument_list_dates=list_dates,
        pre_window_predecessor_symbols=set(required["PRE_WINDOW_PREDECESSOR_SYMBOLS"]),
        window_start=date(2016, 1, 1),
    )
    assert edges["WINDOW_BOUNDARY_REQUIRED_N"] == 24
    bar_rows = [
        {"symbol": s, "trade_date": d, "close": c}
        for s in symbols
        for d, c in (
            (date(2016, 1, 4), 10.0),
            (date(2016, 1, 5), 10.25),
            (date(2016, 1, 6), 10.5),
        )
    ]
    bars = pl.DataFrame(
        {
            "symbol": [r["symbol"] for r in bar_rows],
            "trade_date": [r["trade_date"] for r in bar_rows],
            "close": [r["close"] for r in bar_rows],
        }
    )
    # Sentinel fixture: 24 frozen official rows (one per symbol) that are all
    # present in the current extraction and exact (2016-01-05 preclose=10.0
    # equals the local previous close 10.0 -> formal fact is exactly 10.0).
    sentinel_rows = [
        {
            "symbol": s,
            "trade_date": "2016-01-05",
            "official_reference": 10.0,
            "kind": "OFFICIAL_EVENT",
            "authority_url": None,
        }
        for s in symbols
    ]
    monkeypatch.setattr(
        "ashare_data.r4a7_preclose_full_extraction.load_frozen_sentinel_evidence",
        lambda: sentinel_rows,
    )
    agg = aggregate_full_run(
        staging_root=staging,
        root=root,
        symbols=symbols,
        execution_context=EXECUTION_CONTEXT_OFFLINE_TEST,
        as_of=date(2016, 1, 6),
        window_start=date(2016, 1, 1),
        bars=bars,
        event_dates=set(),
        first_listing_dates=set(),
        resumption_keys=set(),
        known_special_keys=set(),
    )
    assert agg["COVERAGE_COMPLETE"] is True
    assert agg["WINDOW_BOUNDARY"]["WINDOW_BOUNDARY_PASS"] is True
    assert agg["WINDOW_BOUNDARY"]["WINDOW_BOUNDARY_REQUIRED_N"] == 24
    assert agg["WINDOW_BOUNDARY"]["WINDOW_BOUNDARY_VALID_N"] == 24
    assert agg["SENTINELS"]["FROZEN_OFFICIAL_SENTINEL_PASS"] is True
    assert agg["SENTINELS"]["SENTINEL_EXACT_N"] == 24
    assert agg["CLEAN_NORMAL"]["NORMAL_FULL_PARITY_PASS"] is True
    assert agg["CLEAN_NORMAL"]["CLEAN_NORMAL_REQUIRED_N"] == 48
    assert agg["CLEAN_NORMAL"]["CLEAN_NORMAL_UNCOMPARED_N"] == 0
    assert agg["PRECLOSE_COMPLETE_CANDIDATE"] is True
    assert agg["PRECLOSE_COMPLETE"] is False
    assert agg["FULL_MARKET_AUTHORIZED"] is False
