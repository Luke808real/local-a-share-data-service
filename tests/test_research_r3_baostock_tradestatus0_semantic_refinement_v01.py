from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import research_r3_baostock_tradestatus0_semantic_refinement_v01 as refinement  # noqa: E402


def _provider(symbol: str, trade_date: str, volume: int, amount: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "request_id": "R3SAC-TEST",
        "volume": volume,
        "amount": amount,
        "open": "1.0000",
        "high": "1.0000",
        "low": "1.0000",
        "close": "1.0000",
        "preclose": "1.0000",
        "_contradiction": volume > 0 or float(amount) > 0,
    }


def _invalid(symbol: str, trade_date: str, volume: object, amount: object) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "request_id": "R3SAC-INVALID",
        "volume_raw": volume,
        "amount_raw": amount,
        "reason_code": "NON_NUMERIC_VOLUME_OR_AMOUNT",
        "open": "1.0000",
        "high": "1.0000",
        "low": "1.0000",
        "close": "1.0000",
        "preclose": "1.0000",
    }


def test_three_way_partition_is_mutually_exclusive_and_exhaustive() -> None:
    partitions = refinement.build_status0_partitions(
        [
            _provider("000001.SZ", "2023-01-03", 0, "0.0000"),
            _provider("000002.SZ", "2023-01-03", 10, "0.0000"),
        ],
        [_invalid("000003.SZ", "2023-01-03", "", "")],
    )
    assert {key: len(value) for key, value in partitions.items()} == {
        refinement.ZERO_ZERO: 1,
        refinement.CONTRADICTION: 1,
        refinement.INDETERMINATE: 1,
    }
    keys = [refinement.key_of(row) for rows in partitions.values() for row in rows]
    assert len(keys) == len(set(keys)) == 3


def test_blank_is_not_zero_and_non_numeric_is_not_zero() -> None:
    assert refinement.classify_numeric_pattern("", "") == "VOLUME_BLANK_AMOUNT_BLANK"
    assert refinement.classify_numeric_pattern("0", "0") == "OTHER"
    assert refinement.classify_numeric_pattern("not-a-number", "0") == "NON_NUMERIC_NONBLANK"


def test_positive_value_is_contradiction() -> None:
    metrics = refinement.prior.status0_value_metrics("1", "0")
    assert metrics["contradiction"] is True
    assert refinement.build_status0_partitions(
        [_provider("000001.SZ", "2023-01-03", 1, "0.0000")], []
    )[refinement.CONTRADICTION]


def test_invalid_pattern_profile_is_deterministic() -> None:
    rows = [
        _invalid("000002.SZ", "2023-01-03", "", ""),
        _invalid("000001.SZ", "2023-01-03", "", ""),
        _invalid("000003.SZ", "2023-01-03", "bad", "1"),
    ]
    left = refinement.build_invalid_pattern_profile(rows)
    right = refinement.build_invalid_pattern_profile(list(reversed(rows)))
    assert left == right
    assert left["INVALID_BOTH_BLANK_N"] == 2
    assert left["INVALID_NONBLANK_N"] == 1


def test_three_by_three_canonical_matrix_has_exact_cells() -> None:
    partitions = {
        refinement.ZERO_ZERO: [_provider("000001.SZ", "2023-01-03", 0, "0")],
        refinement.CONTRADICTION: [_provider("000002.SZ", "2023-01-03", 1, "0")],
        refinement.INDETERMINATE: [_invalid("000003.SZ", "2023-01-03", "", "")],
    }
    canonical = {
        ("000001.SZ", date(2023, 1, 3)): {"volume": 0},
        ("000002.SZ", date(2023, 1, 3)): {"volume": 5},
    }
    manifest, summary = refinement.build_cross_canonical_matrix(partitions, canonical)
    assert sum(summary[refinement.ZERO_ZERO][state]["KEY_N"] for state in refinement.CANONICAL_STATES) == 1
    assert summary[refinement.ZERO_ZERO]["CANONICAL_ZERO_VOLUME"]["KEY_N"] == 1
    assert summary[refinement.CONTRADICTION]["CANONICAL_POSITIVE_VOLUME"]["KEY_N"] == 1
    assert summary[refinement.INDETERMINATE]["CANONICAL_ABSENT"]["KEY_N"] == 1
    assert manifest["MANIFEST_HASH"] == refinement.sha256_json({key: value for key, value in manifest.items() if key != "MANIFEST_HASH"})


def test_same_count_different_keyset_changes_hash() -> None:
    left = refinement.keyset_hash([("000001.SZ", date(2023, 1, 3))])
    right = refinement.keyset_hash([("000002.SZ", date(2023, 1, 3))])
    assert left != right


def test_zero_write_is_repository_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    data = tmp_path / "data"
    (data / "curated" / "daily_bars").mkdir(parents=True)
    before = sorted(path.relative_to(data) for path in data.rglob("*"))
    refinement.write_repo_json(repo, "test.json", {"ok": True})
    after = sorted(path.relative_to(data) for path in data.rglob("*"))
    assert before == after
    assert (repo / "reports" / "implementation" / "test.json").is_file()
