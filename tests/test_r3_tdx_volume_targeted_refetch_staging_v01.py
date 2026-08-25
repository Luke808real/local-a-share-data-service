"""Targeted tests for R3 TDX refetch staging V01.

These tests use synthetic provider observations only.  They never contact TDX
and never write a curated/canonical path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_r3_tdx_volume_targeted_refetch_staging_v01 as staging  # noqa: E402


RUNTIME = {
    "PACKAGE": "cnequity",
    "VERSION": "0.7.2",
    "COMMIT": staging.CORRECTED_RUNTIME_COMMIT,
    "DAILY_VOLUME_SEMANTIC": "IEEE754_FLOAT32_LOTS_THEN_INT_LOTS_X_100_SHARES",
}


def _target_rows() -> list[dict]:
    return [
        {
            "symbol": "000001.SZ",
            "trade_date": "2020-01-02",
            "classification": "PROVABLY_AFFECTED",
            "current_volume": 4800,
            "corrected_volume_if_provably_affected": 3300,
        },
        {
            "symbol": "000001.SZ",
            "trade_date": "2020-01-03",
            "classification": "AMBIGUOUS",
            "current_volume": 1000,
            "corrected_volume_if_provably_affected": None,
        },
        {
            "symbol": "600000.SH",
            "trade_date": "2020-01-02",
            "classification": "AMBIGUOUS",
            "current_volume": 2000,
            "corrected_volume_if_provably_affected": None,
        },
    ]


def _small_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staging, "REPAIR_SUPERSET_KEY_N", 3)
    monkeypatch.setattr(staging, "TDX_TARGET_SYMBOL_N", 2)


def _request_for_one_symbol(monkeypatch: pytest.MonkeyPatch) -> dict:
    _small_contract(monkeypatch)
    manifest = staging.build_request_manifest(_target_rows(), RUNTIME)
    return next(row for row in manifest["REQUESTS"] if row["symbol"] == "000001.SZ")


def test_request_manifest_is_deterministic_and_target_bound(monkeypatch: pytest.MonkeyPatch):
    _small_contract(monkeypatch)
    first = staging.build_request_manifest(_target_rows(), RUNTIME)
    second = staging.build_request_manifest(list(reversed(_target_rows())), RUNTIME)

    assert staging.canonical_json_bytes(first) == staging.canonical_json_bytes(second)
    assert first["TARGET_KEY_N"] == 3
    assert first["TARGET_SYMBOL_N"] == 2
    assert first["TDX_PAGE_REQUEST_ESTIMATE"] == 11_600
    assert first["TDX_PAGE_REQUEST_ESTIMATE_IS_HARD_CAP"] is False
    assert [row["symbol"] for row in first["REQUESTS"]] == ["000001.SZ", "600000.SH"]
    assert first["REQUESTS"][0]["expected_target_keys"] == [
        {
            "trade_date": "2020-01-02",
            "classification": "PROVABLY_AFFECTED",
            "old_volume": 4800,
            "predicted_corrected_volume": 3300,
        },
        {
            "trade_date": "2020-01-03",
            "classification": "AMBIGUOUS",
            "old_volume": 1000,
            "predicted_corrected_volume": None,
        },
    ]


def test_reconcile_stages_only_frozen_keys_and_records_in_span_extras(monkeypatch: pytest.MonkeyPatch):
    request = _request_for_one_symbol(monkeypatch)
    outcome = staging.reconcile_symbol(
        request,
        [
            {"symbol": "000001.SZ", "trade_date": "2020-01-02", "fresh_tdx_volume": 3300, "volume_error": None},
            {"symbol": "000001.SZ", "trade_date": "2020-01-03", "fresh_tdx_volume": 1000, "volume_error": None},
            {"symbol": "000001.SZ", "trade_date": "2020-01-06", "fresh_tdx_volume": 900, "volume_error": None},
        ],
        provider_failed=None,
        page_request_n=2,
        tdx_hosts=["host:7709"],
        runtime=RUNTIME,
    )

    assert [row["trade_date"] for row in outcome["RESULTS"]] == ["2020-01-02", "2020-01-03"]
    assert outcome["IN_SPAN_NON_TARGET_ROW_N"] == 1
    assert [row["fetch_status"] for row in outcome["RESULTS"]] == ["RESOLVED", "RESOLVED"]
    assert outcome["RESULTS"][0]["fresh_equals_predicted_corrected"] is True


def test_duplicate_missing_and_provider_failure_are_never_resolved(monkeypatch: pytest.MonkeyPatch):
    request = _request_for_one_symbol(monkeypatch)
    duplicate = staging.reconcile_symbol(
        request,
        [
            {"symbol": "000001.SZ", "trade_date": "2020-01-02", "fresh_tdx_volume": 3300, "volume_error": None},
            {"symbol": "000001.SZ", "trade_date": "2020-01-02", "fresh_tdx_volume": 3300, "volume_error": None},
        ],
        provider_failed=None,
        page_request_n=1,
        tdx_hosts=[],
        runtime=RUNTIME,
    )
    assert [row["fetch_status"] for row in duplicate["RESULTS"]] == [
        "DUPLICATE_PROVIDER_ROW",
        "MISSING_TARGET_KEY",
    ]

    failed = staging.reconcile_symbol(
        request,
        [],
        provider_failed="attempt=2:TdxSourceError:down",
        page_request_n=2,
        tdx_hosts=[],
        runtime=RUNTIME,
    )
    assert {row["fetch_status"] for row in failed["RESULTS"]} == {"PROVIDER_FAILED"}
    assert failed["COUNTERS"]["PROVIDER_FAILED_KEY_N"] == 2


def test_unexpected_symbol_and_affected_prediction_mismatch_fail_quality(
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request_for_one_symbol(monkeypatch)
    outcome = staging.reconcile_symbol(
        request,
        [
            {"symbol": "600000.SH", "trade_date": "2020-01-02", "fresh_tdx_volume": 3300, "volume_error": None},
            {"symbol": "000001.SZ", "trade_date": "2020-01-02", "fresh_tdx_volume": 4800, "volume_error": None},
        ],
        provider_failed=None,
        page_request_n=1,
        tdx_hosts=[],
        runtime=RUNTIME,
    )
    assert outcome["UNEXPECTED_SYMBOL_N"] == 1
    assert outcome["RESULTS"][0]["fresh_equals_predicted_corrected"] is False

    reconciliation = staging.reconcile_all(
        outcome["RESULTS"], target_key_n=2, target_symbol_n=1
    )
    assert reconciliation["UNEXPECTED_SYMBOL_N"] == 1
    assert reconciliation["PROVABLY_AFFECTED_PREDICTION_MISMATCH_N"] == 1
    assert reconciliation["STAGING_COMPLETE"] is False


def test_staging_root_guard_and_staging_write_leave_curated_bytes_unchanged(tmp_path: Path):
    data_root = tmp_path / "data"
    curated = data_root / "curated" / "daily_bars" / "part.parquet"
    curated.parent.mkdir(parents=True)
    curated.write_bytes(b"canonical-bytes")
    before = curated.read_bytes()
    staging_root = data_root / "staging" / staging.STAGING_DIRNAME

    staging.require_isolated_staging_root(data_root, staging_root)
    staging.write_canonical_json(staging_root / "task-owned.json", {"ok": True})
    assert curated.read_bytes() == before

    with pytest.raises(staging.TargetedRefetchError, match="forbidden"):
        staging.require_isolated_staging_root(data_root, curated.parent)


def test_resume_checkpoint_parity_and_zero_canonical_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _small_contract(monkeypatch)
    target_rows = _target_rows()
    input_manifest = {
        "INPUT_FILE_N": 1,
        "INPUT_MANIFEST_HASH": staging.INPUT_MANIFEST_HASH,
        "FILES": [{"relative_path": "curated/daily_bars/part.parquet", "sha256": "x"}],
        "CANONICAL_SERIALIZATION": "test",
    }
    authority = {"RUNTIME": RUNTIME, "LIVE_INPUT_MANIFEST": input_manifest}
    monkeypatch.setattr(staging, "_read_frozen_authority", lambda _repo, _root: (target_rows, authority))
    monkeypatch.setattr(staging, "build_input_file_manifest", lambda _root: input_manifest)

    calls: list[str] = []

    def fake_fetcher(request: dict) -> dict:
        calls.append(request["symbol"])
        provider_rows = [
            {
                "symbol": request["symbol"],
                "trade_date": key["trade_date"],
                "fresh_tdx_volume": key["predicted_corrected_volume"] or key["old_volume"],
                "volume_error": None,
            }
            for key in request["expected_target_keys"]
        ]
        return {
            "provider_rows": provider_rows,
            "provider_failed": None,
            "page_request_n": 1,
            "tdx_hosts": ["host:7709"],
            "in_span_non_target_row_n": 0,
            "attempt_n": 1,
        }

    data_root = tmp_path / "data"
    staging_root = data_root / "staging" / staging.STAGING_DIRNAME
    first = staging.run_staging(
        repo_root=tmp_path,
        data_root=data_root,
        staging_root=staging_root,
        fetcher=fake_fetcher,
    )
    assert calls == ["000001.SZ", "600000.SH"]
    assert first["RECONCILIATION"]["STAGING_COMPLETE"] is True
    assert first["QUALITY_GATE"]["CANONICAL_WRITE_EXECUTED"] is False

    second = staging.run_staging(
        repo_root=tmp_path,
        data_root=data_root,
        staging_root=staging_root,
        fetcher=lambda _: pytest.fail("resume must not refetch a terminal symbol"),
    )
    assert second["STAGING_OUTPUT_HASH"] == first["STAGING_OUTPUT_HASH"]
    checkpoint = staging.load_json(staging_root / "checkpoint.json")
    assert checkpoint["COMPLETED_SYMBOL_N"] == 2
