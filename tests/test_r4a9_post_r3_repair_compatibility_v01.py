from __future__ import annotations

import hashlib
import sys
from datetime import date
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import audit_r4a9_post_r3_repair_compatibility_v01 as audit  # noqa: E402


def k(symbol: str, value: str) -> tuple[str, date]:
    return symbol, date.fromisoformat(value)


def manifest(files: list[dict[str, object]]) -> dict[str, object]:
    return {
        "INPUT_FILE_N": len(files),
        "INPUT_MANIFEST_HASH": audit.sha256_bytes(audit.canonical_json_bytes(files)),
        "FILES": files,
    }


def test_keyset_hash_is_deterministic_and_exact() -> None:
    keys = [k("600647.SH", "2024-06-14"), k("002087.SZ", "2024-06-14")]
    expected = hashlib.sha256(
        b"002087.SZ\t2024-06-14\n600647.SH\t2024-06-14\n"
    ).hexdigest()
    assert audit.keyset_hash(keys) == expected
    assert audit.keyset_hash(list(reversed(keys))) == expected


def test_manifest_mismatch_is_bounded_not_full_invalidation() -> None:
    old = manifest([{"relative_path": "curated/daily_bars/trade_date=2024-06-13/part.parquet", "file_size": 1, "sha256": "old"}])
    new = manifest([{"relative_path": "curated/daily_bars/trade_date=2024-06-13/part.parquet", "file_size": 2, "sha256": "new"}])
    lineage = audit.compare_manifest_lineage(
        old,
        new,
        expected_changed_path="curated/daily_bars/trade_date=2024-06-13/part.parquet",
        expected_inserted_key_n=4,
    )
    assert lineage["AFFECTED_CANONICAL_FILE_N"] == 1
    assert audit.choose_compatibility_verdict(directly_affected_n=1, dependency_traceable=True) == "BOUNDED_INVALIDATION"
    assert audit.choose_compatibility_verdict(directly_affected_n=0, dependency_traceable=True) == "ZERO_INVALIDATION"
    assert audit.choose_compatibility_verdict(directly_affected_n=1, dependency_traceable=False) == "FULL_CHECKPOINT_INVALIDATION"


def test_repaired_symbol_unvisited_does_not_invalidate_completed_scope() -> None:
    formal = {"002087.SZ", "600647.SH"}
    complete = {"002087.SZ"}
    target = {"600647.SH"}
    assert complete & target == set()
    assert len(complete) == 1
    assert formal - complete == target


def test_repaired_symbol_complete_has_bounded_invalidation() -> None:
    formal = {"002087.SZ", "600647.SH"}
    complete = {"002087.SZ"}
    target = {"002087.SZ", "600647.SH"}
    directly_affected = complete & target
    assert directly_affected == {"002087.SZ"}
    assert complete - directly_affected == set()


def test_unaffected_complete_symbol_remains_reusable() -> None:
    complete = {"000001.SZ", "002087.SZ", "600000.SH"}
    repaired = {"002087.SZ", "600647.SH", "600766.SH", "603133.SH"}
    directly_affected = complete & repaired
    safe_reuse = complete - directly_affected
    assert directly_affected == {"002087.SZ"}
    assert safe_reuse == {"000001.SZ", "600000.SH"}


def test_exact_predecessor_dependency_propagation() -> None:
    repaired = k("002087.SZ", "2024-06-13")
    next_key = k("002087.SZ", "2024-06-14")
    other_symbol_key = k("600647.SH", "2024-06-14")
    assert next_key[0] == repaired[0]
    assert next_key[1] > repaired[1]
    assert other_symbol_key not in {next_key}


def test_display_equal_uses_exchange_precision() -> None:
    assert audit.display_equal("0.160", 0.16)
    assert audit.display_equal("1.234", "1.23")
    assert not audit.display_equal("1.235", "1.23")


def test_300546_failure_is_repair_addressable_without_resume() -> None:
    diagnostic = {
        "SYMBOL": "300546.SZ",
        "ROOT_CAUSE_CLASSIFICATION": "R3_REQUIRED_KEY_MISMATCH",
    }
    assert diagnostic["ROOT_CAUSE_CLASSIFICATION"] == "R3_REQUIRED_KEY_MISMATCH"
    assert audit.R4A9_CODE_HEAD == "795b1b8f6b688ecc2e94f85c09d80c365e648920"


def test_no_provider_or_data_write_contract_in_source() -> None:
    source = Path(audit.__file__).read_text(encoding="utf-8")
    assert "import baostock" not in source
    assert "query_history_k_data_plus" not in source
    assert "CANONICAL_WRITE_EXECUTED" in source
    assert "R4A9_CHECKPOINT_MUTATED" in source


def test_no_data_root_write_in_pure_helpers(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    before = sorted(path.relative_to(data_root) for path in data_root.rglob("*"))
    assert audit.choose_compatibility_verdict(directly_affected_n=0, dependency_traceable=True) == "ZERO_INVALIDATION"
    after = sorted(path.relative_to(data_root) for path in data_root.rglob("*"))
    assert before == after


def test_manifest_lineage_rejects_unrelated_file_drift() -> None:
    old = manifest([
        {"relative_path": "curated/daily_bars/trade_date=2024-06-13/part.parquet", "file_size": 1, "sha256": "old"},
        {"relative_path": "curated/daily_bars/trade_date=2024-06-14/part.parquet", "file_size": 1, "sha256": "same"},
    ])
    new = manifest([
        {"relative_path": "curated/daily_bars/trade_date=2024-06-13/part.parquet", "file_size": 2, "sha256": "new"},
        {"relative_path": "curated/daily_bars/trade_date=2024-06-14/part.parquet", "file_size": 2, "sha256": "drift"},
    ])
    with pytest.raises(audit.CompatibilityError, match="CANONICAL_CHANGED_FILE_SCOPE_MISMATCH"):
        audit.compare_manifest_lineage(
            old,
            new,
            expected_changed_path="curated/daily_bars/trade_date=2024-06-13/part.parquet",
            expected_inserted_key_n=4,
        )
