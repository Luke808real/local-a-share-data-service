"""Offline provenance tests for R3 TDX staging V01.1."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))

import r3_tdx_volume_targeted_refetch_staging_v01_1 as audit  # noqa: E402


def _frozen_rows() -> list[dict]:
    return [
        {"symbol": "000001.SZ", "trade_date": "2020-01-02"},
        {"symbol": "000001.SZ", "trade_date": "2020-01-03"},
        {"symbol": "600000.SH", "trade_date": "2020-01-02"},
    ]


def _results() -> list[dict]:
    return [
        {
            "symbol": "000001.SZ",
            "trade_date": "2020-01-02",
            "classification": "PROVABLY_AFFECTED",
            "old_volume": 4800,
            "fresh_tdx_volume": 3300,
            "predicted_corrected_volume": 3300,
            "fetch_status": "RESOLVED",
            "fresh_vs_old": "CHANGED",
            "fresh_equals_predicted_corrected": True,
        },
        {
            "symbol": "000001.SZ",
            "trade_date": "2020-01-03",
            "classification": "AMBIGUOUS",
            "old_volume": 1000,
            "fresh_tdx_volume": 1000,
            "predicted_corrected_volume": None,
            "fetch_status": "RESOLVED",
            "fresh_vs_old": "UNCHANGED",
            "fresh_equals_predicted_corrected": None,
        },
        {
            "symbol": "600000.SH",
            "trade_date": "2020-01-02",
            "classification": "AMBIGUOUS",
            "old_volume": 2000,
            "fresh_tdx_volume": 1900,
            "predicted_corrected_volume": None,
            "fetch_status": "RESOLVED",
            "fresh_vs_old": "CHANGED",
            "fresh_equals_predicted_corrected": None,
        },
    ]


def _request() -> dict:
    return {
        "REQUESTS": [
            {
                "symbol": "000001.SZ",
                "expected_target_keys": [
                    {"trade_date": "2020-01-02"},
                    {"trade_date": "2020-01-03"},
                ],
            },
            {
                "symbol": "600000.SH",
                "expected_target_keys": [{"trade_date": "2020-01-02"}],
            },
        ]
    }


def _expectations(request_hash: str, staging_hash: str) -> dict:
    return {
        "request_manifest_hash": request_hash,
        "staging_output_hash": staging_hash,
        "target_key_n": 3,
        "target_symbol_n": 2,
        "resolved_n": 3,
        "changed_n": 2,
        "unchanged_n": 1,
        "missing_n": 0,
        "duplicate_n": 0,
        "provider_failed_n": 0,
        "invalid_n": 0,
        "affected_n": 1,
        "affected_prediction_mismatch_n": 0,
    }


def _write_stage(tmp_path: Path, *, results: list[dict] | None = None) -> tuple[Path, dict]:
    root = tmp_path / "stage"
    request = _request()
    rows = _results() if results is None else results
    request_hash = audit.write_canonical_json(root / "request_manifest.json", request)
    staging_hash = audit.write_canonical_json(root / "per_key_staging_results.json", rows)
    receipt = {
        "REQUEST_MANIFEST_HASH": request_hash,
        "STAGING_OUTPUT_HASH": staging_hash,
        "INPUT_MANIFEST_HASH": audit.INPUT_MANIFEST_HASH,
        "REPAIR_SUPERSET_MANIFEST_HASH": audit.REPAIR_SUPERSET_MANIFEST_HASH,
    }
    audit.write_canonical_json(root / "execution_receipt.json", receipt)
    audit.write_canonical_json(root / "reconciliation.json", {"STAGING_COMPLETE": True})
    audit.write_canonical_json(
        root / "quality_gate.json", {"QUALITY_PASS": True, "CANONICAL_WRITE_EXECUTED": False}
    )
    return root, _expectations(request_hash, staging_hash)


def test_authority_commit_semantics_are_distinct():
    binding = audit.validate_authority_values(
        target_freeze_v01_commit=audit.TARGET_FREEZE_V01_COMMIT,
        target_freeze_v01_1_commit=audit.TARGET_FREEZE_V01_1_COMMIT,
        staging_implementation_commit=audit.STAGING_IMPLEMENTATION_COMMIT,
    )
    assert binding["FORMAL_STAGING_AUTHORITY"].endswith(audit.TARGET_FREEZE_V01_1_COMMIT)
    with pytest.raises(audit.OfflineAuditError, match="V01_1_COMMIT"):
        audit.validate_authority_values(
            target_freeze_v01_commit=audit.TARGET_FREEZE_V01_COMMIT,
            target_freeze_v01_1_commit=audit.TARGET_FREEZE_V01_COMMIT,
            staging_implementation_commit=audit.STAGING_IMPLEMENTATION_COMMIT,
        )


def test_existing_artifact_hash_mismatch_fails_closed(tmp_path: Path):
    root, expectations = _write_stage(tmp_path)
    (root / "request_manifest.json").write_bytes(b"[]")
    with pytest.raises(audit.OfflineAuditError, match="REQUEST_MANIFEST_HASH_MISMATCH"):
        audit.verify_staging_artifacts(root, _frozen_rows(), expectations=expectations)


def test_changed_unchanged_exact_partition_and_manifest_determinism(tmp_path: Path):
    root, expectations = _write_stage(tmp_path)
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    verified = audit.verify_staging_artifacts(root, _frozen_rows(), expectations=expectations)
    after = {path.name: path.read_bytes() for path in root.iterdir()}
    assert after == before  # verifier is strictly read-only against staging
    assert verified["PARTITION"] == {
        "CHANGED_UNION_UNCHANGED_EQUALS_FROZEN": True,
        "CHANGED_INTERSECTION_UNCHANGED_EMPTY": True,
    }
    first_rows, first_hash = audit.build_changed_key_manifest(verified["CHANGED_ROWS"])
    second_rows, second_hash = audit.build_changed_key_manifest(list(reversed(verified["CHANGED_ROWS"])))
    assert first_rows == second_rows
    assert first_hash == second_hash
    assert [row["symbol"] for row in first_rows] == ["000001.SZ", "600000.SH"]


def test_duplicate_or_missing_staging_key_fails_closed(tmp_path: Path):
    duplicated = _results()
    duplicated[-1] = dict(duplicated[0])
    root, expectations = _write_stage(tmp_path, results=duplicated)
    with pytest.raises(audit.OfflineAuditError, match="STAGING_DUPLICATE_OR_MISSING_KEY"):
        audit.verify_staging_artifacts(root, _frozen_rows(), expectations=expectations)


def test_module_has_zero_provider_network_or_canonical_write_path():
    source = inspect.getsource(audit)
    for forbidden in ("TdxWireClient", "baostock", "socket", "urllib", "requests"):
        assert forbidden not in source
    assert "write_canonical_json(staging" not in source
