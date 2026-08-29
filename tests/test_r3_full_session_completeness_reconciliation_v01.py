from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import run_r3_full_session_completeness_reconciliation_v01 as audit  # noqa: E402


def k(symbol: str, value: str) -> tuple[str, date]:
    return symbol, date.fromisoformat(value)


def test_unexpected_canonical_total_uses_addition() -> None:
    assert audit.unexpected_canonical_total(2, 3, 4) == 9


def test_keyset_hash_changes_when_date_changes_even_with_same_count() -> None:
    left = audit.keyset_hash(sorted([k("000001.SZ", "2016-01-04"), k("000002.SZ", "2016-01-05")]))
    right = audit.keyset_hash(sorted([k("000001.SZ", "2016-01-04"), k("000002.SZ", "2016-01-06")]))
    assert left != right


def test_classification_partition_is_exact_and_unknown_is_retained() -> None:
    authority = [
        {"symbol": "000001.SZ", "trade_date": "2016-01-04", "classification": "EXPECTED_BAR"},
        {"symbol": "000001.SZ", "trade_date": "2016-01-05", "classification": "NOT_EXPECTED_BAR"},
        {"symbol": "000002.SZ", "trade_date": "2016-01-04", "classification": "UNKNOWN"},
    ]
    result = audit.reconcile_keysets(
        authority,
        [k("000001.SZ", "2016-01-04"), k("000002.SZ", "2016-01-04")],
    )
    assert result["EXPECTED_KEY_N"] == 1
    assert result["NOT_EXPECTED_KEY_N"] == 1
    assert result["UNKNOWN_KEY_N"] == 1
    assert result["EXPECTED_PRESENT_KEY_N"] == 1
    assert result["MISSING_EXPECTED_KEYS"] == []
    assert result["CANONICAL_ON_UNKNOWN_KEYS"] == [k("000002.SZ", "2016-01-04")]
    assert result["CANONICAL_ON_NOT_EXPECTED_KEYS"] == []


def test_missing_expected_key_is_reported() -> None:
    result = audit.reconcile_keysets(
        [{"symbol": "000001.SZ", "trade_date": "2016-01-04", "classification": "EXPECTED_BAR"}],
        [],
    )
    assert result["EXPECTED_PRESENT_KEY_N"] == 0
    assert result["MISSING_EXPECTED_KEYS"] == [k("000001.SZ", "2016-01-04")]


def test_canonical_on_not_expected_key_is_reported() -> None:
    result = audit.reconcile_keysets(
        [{"symbol": "000001.SZ", "trade_date": "2016-01-04", "classification": "NOT_EXPECTED_BAR"}],
        [k("000001.SZ", "2016-01-04")],
    )
    assert result["CANONICAL_ON_NOT_EXPECTED_KEYS"] == [k("000001.SZ", "2016-01-04")]


def test_canonical_on_unknown_key_is_reported() -> None:
    result = audit.reconcile_keysets(
        [{"symbol": "000001.SZ", "trade_date": "2016-01-04", "classification": "UNKNOWN"}],
        [k("000001.SZ", "2016-01-04")],
    )
    assert result["CANONICAL_ON_UNKNOWN_KEYS"] == [k("000001.SZ", "2016-01-04")]


def test_canonical_outside_authority_key_is_reported() -> None:
    result = audit.reconcile_keysets(
        [{"symbol": "000001.SZ", "trade_date": "2016-01-04", "classification": "EXPECTED_BAR"}],
        [k("000009.SZ", "2016-01-04")],
    )
    assert result["CANONICAL_OUTSIDE_KEYS"] == [k("000009.SZ", "2016-01-04")]


def test_duplicate_canonical_key_is_detected() -> None:
    result = audit.reconcile_keysets(
        [{"symbol": "000001.SZ", "trade_date": "2016-01-04", "classification": "EXPECTED_BAR"}],
        [k("000001.SZ", "2016-01-04"), k("000001.SZ", "2016-01-04")],
    )
    assert result["CANONICAL_ROW_N"] == 2
    assert result["CANONICAL_UNIQUE_KEY_N"] == 1
    assert result["CANONICAL_DUPLICATE_KEY_N"] == 1


def test_equal_global_row_count_does_not_imply_completeness_pass() -> None:
    result = audit.reconcile_keysets(
        [
            {"symbol": "000001.SZ", "trade_date": "2016-01-04", "classification": "EXPECTED_BAR"},
            {"symbol": "000001.SZ", "trade_date": "2016-01-05", "classification": "EXPECTED_BAR"},
        ],
        [k("000001.SZ", "2016-01-04"), k("000009.SZ", "2016-01-04")],
    )
    assert result["EXPECTED_KEY_N"] == result["CANONICAL_ROW_N"] == 2
    assert result["EXPECTED_PRESENT_KEY_N"] == 1
    assert result["MISSING_EXPECTED_KEYS"] == [k("000001.SZ", "2016-01-05")]
    assert result["CANONICAL_OUTSIDE_KEYS"] == [k("000009.SZ", "2016-01-04")]


def test_anchor_status_and_presence_are_reported_without_special_mutation() -> None:
    anchors = {
        k("300546.SZ", "2016-09-29"): {
            "classification": "EXPECTED_BAR",
            "basis": "PROVIDER_TRADESTATUS_1",
        },
        k("300546.SZ", "2016-10-10"): {
            "classification": "EXPECTED_BAR",
            "basis": "PROVIDER_TRADESTATUS_1",
        },
    }
    report = audit._anchor_report(
        {"ANCHORS": anchors},
        {"ANCHOR_CANONICAL_PRESENT": set(anchors)},
    )
    assert report["300546_20160929_STATUS"] == "EXPECTED_BAR"
    assert report["300546_20160929_SESSION_BASIS"] == "PROVIDER_TRADESTATUS_1"
    assert report["300546_20160929_CANONICAL_PRESENT"] is True
    assert report["300546_20161010_STATUS"] == "EXPECTED_BAR"
    assert report["300546_20161010_CANONICAL_PRESENT"] is True


def test_unknown_manifest_is_deterministic_and_contains_required_fields() -> None:
    rows = [
        {
            "symbol": "000002.SZ",
            "trade_date": "2016-01-05",
            "basis": audit.UNKNOWN_BASIS,
            "request_id": "R3SAC-000002",
            "provider_code": "sz.000002",
        },
        {
            "symbol": "000001.SZ",
            "trade_date": "2016-01-04",
            "basis": audit.UNKNOWN_BASIS,
            "request_id": "R3SAC-000001",
            "provider_code": "sz.000001",
        },
    ]
    expected_hash = audit.keyset_hash(sorted([k("000001.SZ", "2016-01-04"), k("000002.SZ", "2016-01-05")]))
    manifest = audit._unknown_manifest(rows, expected_hash)
    assert manifest["KEY_N"] == 2
    assert manifest["KEYSET_HASH"] == expected_hash
    assert [row["symbol"] for row in manifest["ROWS"]] == ["000001.SZ", "000002.SZ"]
    assert set(manifest["ROWS"][0]) == {"symbol", "trade_date", "basis", "request_id", "provider_code"}


def test_authority_hash_drift_fails_before_any_report_write(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    data = tmp_path / "data"
    stage = data / "staging" / audit.STAGING_DIRNAME
    stage.mkdir(parents=True)
    repo.mkdir()

    def loader(_repo: Path, _data: Path) -> dict[str, object]:
        return {
            "input_manifest": {
                "INPUT_FILE_N": 2_580,
                "INPUT_MANIFEST_HASH": "DRIFT",
                "FILES": [],
            }
        }

    with pytest.raises(audit.ReconciliationError, match="DAILY_INPUT_MANIFEST_DRIFT"):
        audit.run_reconciliation(
            repo_root=repo,
            data_root=data,
            stage_root=stage,
            authority_loader=loader,
        )
    assert not (repo / "reports").exists()


def test_exact_allowed_stage_root_passes(tmp_path: Path) -> None:
    data = tmp_path / "data"
    allowed = data / "staging" / audit.STAGING_DIRNAME
    allowed.mkdir(parents=True)
    assert audit.require_exact_stage_root(data, allowed) == allowed.resolve()


@pytest.mark.parametrize("relative", ["curated", "curated/daily_bars", "other-staging"])
def test_forbidden_stage_root_under_data_root_fails(tmp_path: Path, relative: str) -> None:
    data = tmp_path / "data"
    allowed = data / "staging" / audit.STAGING_DIRNAME
    allowed.mkdir(parents=True)
    forbidden = data / relative
    forbidden.mkdir(parents=True)
    with pytest.raises(audit.ReconciliationError):
        audit.require_exact_stage_root(data, forbidden)


def test_stage_root_outside_data_root_fails(tmp_path: Path) -> None:
    data = tmp_path / "data"
    allowed = data / "staging" / audit.STAGING_DIRNAME
    allowed.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(audit.ReconciliationError):
        audit.require_exact_stage_root(data, outside)


def test_symlink_escaping_stage_root_fails(tmp_path: Path) -> None:
    data = tmp_path / "data"
    staging = data / "staging"
    staging.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = staging / audit.STAGING_DIRNAME
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(audit.ReconciliationError):
        audit.require_exact_stage_root(data, link)


def test_direct_run_with_forbidden_root_fails_before_writes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    data = tmp_path / "data"
    repo.mkdir()
    (data / "staging" / audit.STAGING_DIRNAME).mkdir(parents=True)
    forbidden = data / "curated"
    forbidden.mkdir()

    def unexpected_loader(_repo: Path, _data: Path) -> dict[str, object]:
        raise AssertionError("authority loader must not run for forbidden root")

    with pytest.raises(audit.ReconciliationError):
        audit.run_reconciliation(
            repo_root=repo,
            data_root=data,
            stage_root=forbidden,
            authority_loader=unexpected_loader,
        )
    assert not (repo / "reports").exists()


def test_receipt_index_hash_mismatch_fails_closed() -> None:
    with pytest.raises(audit.ReconciliationError):
        audit.verify_receipt_index(
            {
                "REQUESTS": [],
                "REQUEST_RECEIPT_INDEX_HASH": audit.REQUEST_RECEIPT_INDEX_HASH,
            }
        )


def test_session_parquet_sha_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "session_authority.parquet"
    path.write_bytes(b"not-a-parquet-artifact")
    with pytest.raises(audit.ReconciliationError, match="SESSION_AUTHORITY_FILE_SHA256_MISMATCH"):
        audit.verify_session_authority_file_hash(path)


def test_data_root_is_not_written_by_pure_reconciliation_helper(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    before = sorted(path.relative_to(data) for path in data.rglob("*"))
    audit.reconcile_keysets(
        [{"symbol": "000001.SZ", "trade_date": "2016-01-04", "classification": "EXPECTED_BAR"}],
        [k("000001.SZ", "2016-01-04")],
    )
    after = sorted(path.relative_to(data) for path in data.rglob("*"))
    assert before == after
