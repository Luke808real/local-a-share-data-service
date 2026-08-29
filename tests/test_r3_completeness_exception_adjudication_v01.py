from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import research_r3_completeness_exception_adjudication_v01 as audit  # noqa: E402


def key(symbol: str, value: str) -> tuple[str, date]:
    return symbol, date.fromisoformat(value)


def test_unexpected_total_uses_addition() -> None:
    assert audit.unexpected_canonical_total(2, 3, 4) == 9


def test_frozen_exception_manifest_scope_and_hash_are_exact() -> None:
    keys = [key("000001.SZ", "2016-01-04"), key("000002.SZ", "2016-01-05")]
    payload = {
        "TASK": audit.reconciliation.TASK,
        "MANIFEST_KIND": "UNKNOWN_SESSION_KEYS",
        "KEY_N": 2,
        "KEYSET_HASH": audit.reconciliation.keyset_hash(keys),
        "KEYS": [{"symbol": s, "trade_date": d.isoformat()} for s, d in keys],
    }
    assert audit.validate_frozen_exception_manifest(
        payload,
        expected_kind="UNKNOWN_SESSION_KEYS",
        expected_n=2,
        expected_hash=payload["KEYSET_HASH"],
    ) == keys


def test_frozen_exception_hash_drift_fails_closed() -> None:
    payload = {
        "TASK": audit.reconciliation.TASK,
        "MANIFEST_KIND": "UNKNOWN_SESSION_KEYS",
        "KEY_N": 1,
        "KEYSET_HASH": "wrong",
        "KEYS": [{"symbol": "000001.SZ", "trade_date": "2016-01-04"}],
    }
    with pytest.raises(audit.AdjudicationError, match="HASH_MISMATCH"):
        audit.validate_frozen_exception_manifest(
            payload,
            expected_kind="UNKNOWN_SESSION_KEYS",
            expected_n=1,
            expected_hash="wrong",
        )


def test_two_key_exact_scope_is_required() -> None:
    expected = sorted(audit.CONFLICT_KEYS)
    expected_hash = audit.reconciliation.keyset_hash(expected)
    audit.validate_exact_scope(expected, expected, expected_hash)
    with pytest.raises(audit.AdjudicationError, match="SCOPE_NOT_EXACT"):
        audit.validate_exact_scope(expected[:1], expected, expected_hash)


def test_thirty_nine_key_exact_scope_is_required() -> None:
    rows = [(f"{index:06d}.SZ", date(2026, 1, 1)) for index in range(39)]
    expected_hash = audit.reconciliation.keyset_hash(sorted(rows))
    audit.validate_exact_scope(rows, rows, expected_hash)
    with pytest.raises(audit.AdjudicationError, match="SCOPE_HASH_MISMATCH"):
        audit.validate_exact_scope(rows, rows, "wrong")


def test_same_source_is_not_independent_confirmation() -> None:
    assert (
        audit.independence_status("baostock", "baostock")
        == "SAME_SOURCE_NOT_INDEPENDENT_CONFIRMATION"
    )


def test_unknown_is_retained_without_independent_evidence() -> None:
    assert audit.adjudicate_unknown_case() == "UNKNOWN"


def test_conflicting_status_and_positive_canonical_bar_is_unresolved() -> None:
    canonical = {"volume": 353400, "amount": 19589786.0}
    provider = {"volume": "353426", "amount": "19589785.9900", "tradestatus": 0}
    assert audit.classify_canonical_conflict(canonical, provider) == "UNRESOLVED"


def test_zero_placeholder_with_status_zero_is_physical_canonical_valid() -> None:
    canonical = {"volume": 0, "amount": 0.0}
    provider = {"volume": "0", "amount": "0.0000", "tradestatus": 0}
    assert audit.classify_canonical_conflict(canonical, provider) == "CANONICAL_VALID"


def test_no_canonical_write_from_report_writer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    data = tmp_path / "data"
    (data / "curated" / "daily_bars").mkdir(parents=True)
    before = sorted(path.relative_to(data) for path in data.rglob("*"))
    audit.write_repo_json(repo, "test_exception_report.json", {"ok": True})
    after = sorted(path.relative_to(data) for path in data.rglob("*"))
    assert before == after
    assert (repo / "reports" / "implementation" / "test_exception_report.json").is_file()
