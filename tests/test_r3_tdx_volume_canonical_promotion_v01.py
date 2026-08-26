"""Offline transaction, rollback, and fail-closed tests for R3 promotion."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import run_r3_tdx_volume_canonical_promotion_v01 as promotion  # noqa: E402
import run_r3_tdx_volume_canonical_repair_candidate_v01 as candidate  # noqa: E402


def _source_frame(symbol: str, trade_date: str, volume: int) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol],
            "trade_date": [date.fromisoformat(trade_date)],
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.05],
            "volume": [volume],
            "amount": [100.0],
            "source": ["tdx_protocol"],
            "data_version": ["v2"],
            "fetched_at": [date(2026, 1, 1)],
        }
    ).with_columns(
        pl.col("fetched_at").cast(pl.Datetime("us")),
    )


def _change(symbol: str, trade_date: str, old: int = 1000, fresh: int = 900) -> dict:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "classification": "AMBIGUOUS",
        "old_volume": old,
        "fresh_tdx_volume": fresh,
        "predicted_corrected_volume": None,
        "fetch_status": "RESOLVED",
    }


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    affected_file_n: int = 1,
    create_transaction: bool = True,
):
    data_root = tmp_path / "data"
    candidate_root = data_root / "staging" / promotion.CANDIDATE_ROOT_NAME
    transaction_root = data_root / "staging" / promotion.PROMOTION_ROOT_NAME
    records: list[dict] = []
    changed: dict[tuple[str, str], dict] = {}
    for index, trade_date in enumerate(("2020-01-02", "2020-01-03")):
        symbol = f"00000{index + 1}.SZ"
        relative = f"curated/daily_bars/trade_date={trade_date}/part.parquet"
        source_path = data_root / relative
        source_path.parent.mkdir(parents=True, exist_ok=True)
        _source_frame(symbol, trade_date, 1000 + index).write_parquet(source_path)
        if index < affected_file_n:
            changed[(symbol, trade_date)] = _change(
                symbol, trade_date, old=1000 + index, fresh=900 + index
            )
            candidate_path = candidate_root / relative
            records.append(
                candidate.build_candidate_file(
                    source_path,
                    candidate_path,
                    relative,
                    {(symbol, trade_date): changed[(symbol, trade_date)]},
                )
            )
    pre = promotion.build_input_file_manifest(data_root)
    monkeypatch.setattr(promotion, "AFFECTED_FILE_N", affected_file_n)
    monkeypatch.setattr(promotion, "TOTAL_FILE_N", 2)
    monkeypatch.setattr(promotion, "UNAFFECTED_FILE_N", 2 - affected_file_n)
    monkeypatch.setattr(promotion, "CHANGED_KEY_N", len(changed))
    monkeypatch.setattr(promotion, "PRE_INPUT_MANIFEST_HASH", pre["INPUT_MANIFEST_HASH"])
    plan_root = transaction_root
    if not create_transaction:
        plan_root = data_root / "staging" / "fixture-plan-root"
    plan_root.mkdir(parents=True)
    plan_records = promotion.build_plan_records(
        data_root=data_root,
        candidate_root=candidate_root,
        transaction_root=plan_root,
        pre=pre,
        candidate_records=records,
    )
    for record in plan_records:
        record["source_path"] = str(data_root / record["relative_path"])
    expected_post = promotion.build_expected_post_manifest(
        data_root=data_root,
        candidate_root=candidate_root,
        pre=pre,
        candidate_records=records,
    )
    candidate.write_canonical_json(
        candidate_root / "candidate_repair_manifest.json",
        {"FILES": records},
    )
    candidate.write_canonical_json(
        candidate_root / "candidate_validation_report.json",
        {"REPORT": "test"},
    )
    context = {
        "pre": pre,
        "candidate_manifest": {"FILES": records},
        "candidate_before": promotion.candidate_snapshot(candidate_root, records),
        "expected_post": expected_post,
    }
    return {
        "data_root": data_root,
        "candidate_root": candidate_root,
        "transaction_root": transaction_root,
        "records": plan_records,
        "candidate_records": records,
        "changed": changed,
        "pre": pre,
        "expected_post": expected_post,
        "context": context,
    }


def test_pre_input_drift_no_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    candidate_root = data_root / "staging" / promotion.CANDIDATE_ROOT_NAME
    transaction_root = data_root / "staging" / promotion.PROMOTION_ROOT_NAME
    before = list(data_root.rglob("*"))
    monkeypatch.setattr(promotion, "require_current_head", lambda repo_root: None)
    monkeypatch.setattr(
        promotion,
        "collect_preflight",
        lambda **kwargs: (_ for _ in ()).throw(promotion.PromotionError("PRE_INPUT_MANIFEST_DRIFT")),
    )
    with pytest.raises(promotion.PromotionError, match="PRE_INPUT_MANIFEST_DRIFT"):
        promotion.run_promotion(
            repo_root=tmp_path,
            data_root=data_root,
            candidate_root=candidate_root,
            transaction_root=transaction_root,
        )
    assert list(data_root.rglob("*")) == before
    assert not transaction_root.exists()


def test_candidate_hash_drift_no_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    candidate_root = data_root / "staging" / promotion.CANDIDATE_ROOT_NAME
    transaction_root = data_root / "staging" / promotion.PROMOTION_ROOT_NAME
    monkeypatch.setattr(promotion, "require_current_head", lambda repo_root: None)
    monkeypatch.setattr(
        promotion,
        "collect_preflight",
        lambda **kwargs: (_ for _ in ()).throw(promotion.PromotionError("CANDIDATE_SHA_MISMATCH")),
    )
    with pytest.raises(promotion.PromotionError, match="CANDIDATE_SHA_MISMATCH"):
        promotion.run_promotion(
            repo_root=tmp_path,
            data_root=data_root,
            candidate_root=candidate_root,
            transaction_root=transaction_root,
        )
    assert not transaction_root.exists()


def test_backup_incomplete_no_canonical_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = _fixture(tmp_path, monkeypatch, affected_file_n=1)
    records = fixture["records"] + [
        {
            "relative_path": "curated/daily_bars/trade_date=2020-01-03/part.parquet",
            "source_path": str(fixture["data_root"] / "missing.parquet"),
            "source_sha256": "missing",
            "rollback_backup_relative_path": "rollback_backup/curated/daily_bars/trade_date=2020-01-03/part.parquet",
            "rollback_backup_verified": False,
        }
    ]
    before = (fixture["data_root"] / fixture["records"][0]["relative_path"]).read_bytes()
    with pytest.raises(promotion.PromotionError, match="COPY_SOURCE_INVALID"):
        promotion.prepare_backups(fixture["transaction_root"], records)
    assert (fixture["data_root"] / fixture["records"][0]["relative_path"]).read_bytes() == before


def test_precommit_toc_tou_drift_no_canonical_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = _fixture(tmp_path, monkeypatch, affected_file_n=1, create_transaction=False)
    (tmp_path / "reports" / "implementation").mkdir(parents=True)
    monkeypatch.setattr(promotion, "require_current_head", lambda repo_root: None)
    drift = dict(fixture["pre"])
    drift["INPUT_MANIFEST_HASH"] = "drift"
    monkeypatch.setattr(promotion, "collect_preflight", lambda **kwargs: fixture["context"])
    monkeypatch.setattr(promotion, "build_input_file_manifest", lambda data_root: drift)
    target = fixture["data_root"] / fixture["records"][0]["relative_path"]
    before = target.read_bytes()
    with pytest.raises(promotion.PromotionError, match="INPUT_DRIFT_BEFORE_PROMOTION"):
        promotion.run_promotion(
            repo_root=tmp_path,
            data_root=fixture["data_root"],
            candidate_root=fixture["candidate_root"],
            transaction_root=tmp_path / "data" / "staging" / promotion.PROMOTION_ROOT_NAME,
        )
    assert target.read_bytes() == before
    state = json.loads(
        (fixture["data_root"] / "staging" / promotion.PROMOTION_ROOT_NAME / "transaction_state.json").read_text()
    )
    assert state["TRANSACTION_STATE"] == "RECOVERY_REQUIRED"


def test_single_file_replacement_success_and_volume_only_semantics(tmp_path: Path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch, affected_file_n=1)
    promotion.prepare_backups(fixture["transaction_root"], fixture["records"])
    promoted: list[str] = []
    promotion.promote_files(
        data_root=fixture["data_root"],
        candidate_root=fixture["candidate_root"],
        transaction_root=fixture["transaction_root"],
        records=fixture["records"],
        promoted=promoted,
    )
    assert len(promoted) == 1
    post = promotion.validate_post_manifest(
        data_root=fixture["data_root"],
        expected_post=fixture["expected_post"],
        pre=fixture["pre"],
        records=fixture["records"],
    )
    semantic = promotion.validate_semantic_post(
        data_root=fixture["data_root"],
        transaction_root=fixture["transaction_root"],
        records=fixture["records"],
        changed=fixture["changed"],
    )
    assert post["INPUT_FILE_N"] == 2
    assert semantic == {"POST_REPAIRED_KEY_N": 1, "POST_VOLUME_MISMATCH_N": 0}


def test_mid_promotion_failure_full_rollback_restores_pre_manifest(tmp_path: Path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch, affected_file_n=2)
    promotion.prepare_backups(fixture["transaction_root"], fixture["records"])
    promoted: list[str] = []

    def fail_after_first(index: int, record: dict):
        if index == 0:
            raise promotion.PromotionError("INJECTED_MID_PROMOTION_FAILURE")

    with pytest.raises(promotion.PromotionError, match="INJECTED_MID_PROMOTION_FAILURE"):
        promotion.promote_files(
            data_root=fixture["data_root"],
            candidate_root=fixture["candidate_root"],
            transaction_root=fixture["transaction_root"],
            records=fixture["records"],
            promoted=promoted,
            fault_injector=fail_after_first,
        )
    assert len(promoted) == 1
    promotion.rollback_files(
        data_root=fixture["data_root"],
        transaction_root=fixture["transaction_root"],
        records=fixture["records"],
        promoted=promoted,
    )
    restored = promotion.build_input_file_manifest(fixture["data_root"])
    promotion.require_manifest_equal(fixture["pre"], restored, "ROLLBACK_PRE_INPUT")


def test_incomplete_transaction_requires_recovery(tmp_path: Path):
    data_root = tmp_path / "data"
    transaction_root = data_root / "staging" / promotion.PROMOTION_ROOT_NAME
    transaction_root.mkdir(parents=True)
    with pytest.raises(promotion.PromotionError, match="RECOVERY_REQUIRED"):
        promotion.ensure_clean_transaction_root(transaction_root)


def test_post_manifest_mismatch_is_not_committed(tmp_path: Path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch, affected_file_n=1)
    promotion.prepare_backups(fixture["transaction_root"], fixture["records"])
    promoted: list[str] = []
    promotion.promote_files(
        data_root=fixture["data_root"],
        candidate_root=fixture["candidate_root"],
        transaction_root=fixture["transaction_root"],
        records=fixture["records"],
        promoted=promoted,
    )
    bad_expected = dict(fixture["expected_post"])
    bad_expected["INPUT_MANIFEST_HASH"] = "wrong"
    with pytest.raises(promotion.PromotionError, match="POST_INPUT_MANIFEST"):
        promotion.validate_post_manifest(
            data_root=fixture["data_root"],
            expected_post=bad_expected,
            pre=fixture["pre"],
            records=fixture["records"],
        )


def test_unaffected_file_mutation_detected(tmp_path: Path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch, affected_file_n=1)
    promotion.prepare_backups(fixture["transaction_root"], fixture["records"])
    promoted: list[str] = []
    promotion.promote_files(
        data_root=fixture["data_root"],
        candidate_root=fixture["candidate_root"],
        transaction_root=fixture["transaction_root"],
        records=fixture["records"],
        promoted=promoted,
    )
    unaffected = fixture["data_root"] / "curated/daily_bars/trade_date=2020-01-03/part.parquet"
    _source_frame("000002.SZ", "2020-01-03", 7777).write_parquet(unaffected)
    observed = promotion.build_input_file_manifest(fixture["data_root"])
    with pytest.raises(promotion.PromotionError, match="UNFFECTED_FILE_MUTATION"):
        promotion.validate_post_manifest(
            data_root=fixture["data_root"],
            expected_post=observed,
            pre=fixture["pre"],
            records=fixture["records"],
        )


def test_candidate_mutation_detected(tmp_path: Path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch, affected_file_n=1)
    candidate_path = fixture["candidate_root"] / fixture["records"][0]["relative_path"]
    before = promotion.candidate_snapshot(fixture["candidate_root"], fixture["candidate_records"])
    candidate_path.write_bytes(b"candidate-tampered")
    with pytest.raises((promotion.PromotionError, candidate.CandidateRepairError), match="CANDIDATE"):
        promotion.require_candidate_unchanged(
            candidate_root=fixture["candidate_root"],
            records=fixture["candidate_records"],
            before=before,
        )


def test_300546_missing_days_remain_uninserted(tmp_path: Path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch, affected_file_n=1)
    promotion.prepare_backups(fixture["transaction_root"], fixture["records"])
    promoted: list[str] = []
    promotion.promote_files(
        data_root=fixture["data_root"],
        candidate_root=fixture["candidate_root"],
        transaction_root=fixture["transaction_root"],
        records=fixture["records"],
        promoted=promoted,
    )
    semantic = promotion.validate_semantic_post(
        data_root=fixture["data_root"],
        transaction_root=fixture["transaction_root"],
        records=fixture["records"],
        changed=fixture["changed"],
    )
    assert semantic["POST_VOLUME_MISMATCH_N"] == 0
