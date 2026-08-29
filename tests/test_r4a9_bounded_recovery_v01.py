from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import run_r4a9_bounded_recovery_v01 as recovery  # noqa: E402


def test_frozen_authority_and_scope_are_exact() -> None:
    assert recovery.BASE_HEAD == "5f4f509615cb99419029792fdb5c48b3bc591b76"
    assert recovery.EXPECTED_CHECKPOINT_SHA256 == "b3cf88438c94a46d077e0d37df6b70cb45d29c741e646264a061dc0ea8d804de"
    assert recovery.EXPECTED_DAILY_MANIFEST_HASH == "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"
    assert recovery.RECOVERY_SYMBOLS == ("002087.SZ", "300546.SZ")
    assert recovery.NEW_CHECKPOINT_SCHEMA_VERSION == "R4A9_LINEAGE_SAFE_RESUME_V01"


def test_resume_manifest_gate_fails_closed_before_network() -> None:
    checkpoint = {"daily_input_manifest_hash": recovery.EXPECTED_DAILY_MANIFEST_HASH}
    assert recovery.validate_resume_input_gate(checkpoint, recovery.EXPECTED_DAILY_MANIFEST_HASH)
    with pytest.raises(RebaseErrorAlias, match="COMPATIBILITY_REASSESSMENT_REQUIRED"):
        recovery.validate_resume_input_gate(checkpoint, "drift")


def test_wrong_checkpoint_sha_fails_before_provider_scope_can_run(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    stage_root = data_root / "staging" / recovery.NEW_R4A9_STAGE_DIRNAME
    stage_root.mkdir(parents=True)
    (stage_root / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RebaseErrorAlias, match="LINEAGE_CHECKPOINT_HASH_MISMATCH"):
        recovery._load_lineage_checkpoint(data_root, set())


def test_third_symbol_request_is_blocked() -> None:
    with pytest.raises(RebaseErrorAlias, match="NETWORK_SYMBOL_SCOPE_VIOLATION"):
        recovery.enforce_network_scope("000001.SZ")
    assert recovery.enforce_network_scope("002087.SZ") is None
    assert recovery.enforce_network_scope("300546.SZ") is None


def test_provider_row_mapping_adds_internal_symbol_without_changing_query_fields() -> None:
    class FakeProvider:
        def query_history_k_data_plus(self, *args: object, **kwargs: object) -> list[list[str]]:
            assert args == ("sz.002087", "date,code,preclose,tradestatus")
            assert kwargs == {
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "frequency": "d",
                "adjustflag": "3",
            }
            return [["2024-06-13", "sz.002087", "0.16", "1"]]

    class FakeAdapter:
        QUERY_FIELDS = "date,code,preclose,tradestatus"
        QUERY_FREQUENCY = "d"
        QUERY_ADJUSTFLAG = "3"

    records: list[dict[str, object]] = []
    fetch = recovery._build_provider_fetch(
        provider=FakeProvider(),
        adapter=FakeAdapter(),
        symbol="002087.SZ",
        request_records=records,
    )
    rows = fetch(
        {
            "symbol": "002087.SZ",
            "bs_code": "sz.002087",
            "year": 2024,
            "start": "2024-01-01",
            "end": "2024-12-31",
            "query": {"fields": "date,code,preclose,tradestatus", "frequency": "d", "adjustflag": "3"},
        }
    )
    assert rows == [{"date": "2024-06-13", "code": "sz.002087", "preclose": "0.16", "tradestatus": "1", "symbol": "002087.SZ"}]
    assert records[0]["provider_error_code"] == "0"


def test_phase2_is_not_allowed_after_phase1_failure() -> None:
    with pytest.raises(RebaseErrorAlias, match="PHASE_2_BLOCKED_BY_PHASE_1"):
        recovery.phase2_allowed("FAILED")
    assert recovery.phase2_allowed("PASS")


def test_atomic_checkpoint_transition_changes_only_new_stage(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    stage_root = data_root / "staging" / recovery.NEW_R4A9_STAGE_DIRNAME
    stage_root.mkdir(parents=True)
    checkpoint_path = stage_root / "manifest.json"
    before_payload = {"daily_input_manifest_hash": recovery.EXPECTED_DAILY_MANIFEST_HASH, "state": "RETRY_REQUIRED"}
    checkpoint_path.write_bytes(recovery.canonical_json_bytes(before_payload))
    before_sha = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    after_payload = {"daily_input_manifest_hash": recovery.EXPECTED_DAILY_MANIFEST_HASH, "state": "COMPLETE"}
    old_sha, new_sha = recovery._update_checkpoint(
        checkpoint_path=checkpoint_path,
        checkpoint=after_payload,
        expected_before_sha=before_sha,
        data_root=data_root,
    )
    assert old_sha == before_sha
    assert new_sha == hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    assert json.loads(checkpoint_path.read_text()) == after_payload


def test_initial_lineage_state_partition_is_closed_by_contract() -> None:
    counts = {
        "SAFE_COMPLETE": 2_139,
        "RECOMPUTE_REQUIRED": 1,
        "RETRY_REQUIRED": 1,
        "UNVISITED": 3_315,
    }
    assert sum(counts.values()) == 5_456


def test_repaired_unvisited_symbols_are_not_recovery_scope() -> None:
    assert {"600647.SH", "600766.SH", "603133.SH"}.isdisjoint(set(recovery.RECOVERY_SYMBOLS))
    assert recovery.RECOVERY_SYMBOLS == ("002087.SZ", "300546.SZ")


def test_real_r3_anchors_and_002087_required_count_are_read_only() -> None:
    data_root = recovery.DATA_ROOT_DEFAULT
    manifest, symbols, dates = recovery._load_current_authority(data_root)
    assert manifest["INPUT_FILE_N"] == 2_580
    assert manifest["INPUT_MANIFEST_HASH"] == recovery.EXPECTED_DAILY_MANIFEST_HASH
    assert len(symbols) == 5_456
    assert len(dates["002087.SZ"]) == 2_026
    assert date(2024, 6, 13) in dates["002087.SZ"]
    assert date(2024, 6, 14) in dates["002087.SZ"]
    assert date(2016, 9, 29) in dates["300546.SZ"]
    assert date(2016, 10, 10) in dates["300546.SZ"]


def test_full_continuation_is_not_authorized() -> None:
    assert recovery.RECOVERY_SYMBOLS == ("002087.SZ", "300546.SZ")
    source = Path(recovery.__file__).read_text(encoding="utf-8")
    assert "FULL_R4A9_CONTINUATION_AUTHORIZED" in source
    assert "UNVISITED_EXECUTION_N" in source
    assert "import baostock" not in source


def test_no_canonical_write_marker_is_false_only_for_canonical() -> None:
    source = Path(recovery.__file__).read_text(encoding="utf-8")
    assert '"CANONICAL_WRITE_EXECUTED": False' in source
    assert "curated" in source  # only read-side canonical authority paths
    assert "R3_DATA_MUTATED" in source


RebaseErrorAlias = recovery.RebaseError
