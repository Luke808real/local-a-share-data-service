"""Daily maintenance entry point: date-scoped planning and fail-closed gates."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


maintenance = _load("daily_maintenance", "tools/run_asl_daily_maintenance.py")
publish = _load("daily_facts_publish_scope", "tools/publish_daily_facts_phase1_v01.py")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def _r3_authority(root: Path, day: str = "2026-09-10") -> dict:
    partition = root / "curated/daily_bars" / f"trade_date={day}" / "part-merged.parquet"
    partition.parent.mkdir(parents=True, exist_ok=True)
    partition.write_bytes(b"r3-partition-bytes")
    files = [{"relative_path": partition.relative_to(root).as_posix(),
              "file_size": partition.stat().st_size,
              "sha256": maintenance.sha256_file(partition)}]
    manifest = {"FILES": files, "INPUT_FILE_N": len(files),
                "INPUT_MANIFEST_HASH": maintenance.canonical_sha(files)}
    plan = root / "staging/r3_v01/trade_date=2026-09-10/promotion_plan.json"
    receipt = root / "staging/r3_v01/trade_date=2026-09-10/promotion_receipt.json"
    _write(plan, {"EXPECTED_POST_INPUT_MANIFEST": manifest})
    _write(receipt, {"STATE": "COMMITTED",
                     "POST_INPUT_MANIFEST_HASH": manifest["INPUT_MANIFEST_HASH"],
                     "QUALITY": {"STRUCTURAL_PASS": True, "COVERAGE_PASS": True, "PROVENANCE_PASS": True,
                                 "UNRESOLVED_KEY_N": 0, "SOURCE_ERROR_N": 0}})
    _write(root / "meta/asl/r3/published-daily-authority.json", {
        "schema": "R3_PUBLISHED_DAILY_AUTHORITY_V01", "manifest_hash": manifest["INPUT_MANIFEST_HASH"],
        "plan": plan.relative_to(root).as_posix(), "receipt": receipt.relative_to(root).as_posix()})
    return manifest


def _facts_authority(root: Path, day: str = "2026-09-10", *, scope: str | None = None, quality_pass: bool = True) -> dict:
    partition = root / "curated/daily_facts" / f"trade_date={day}" / "part-full-eligible-v01.parquet"
    partition.parent.mkdir(parents=True, exist_ok=True)
    partition.write_bytes(b"facts-partition-bytes")
    files = [{"relative_path": partition.relative_to(root).as_posix(),
              "file_size": partition.stat().st_size, "sha256": maintenance.sha256_file(partition)}]
    manifest = {"file_n": len(files), "files": files, "manifest_hash": maintenance.canonical_sha(files)}
    staging = root / "staging/daily_facts_phase1_20260910_v01"
    plan, receipt = staging / "promotion_plan.json", staging / "promotion_receipt.json"
    _write(plan, {"manifest": manifest, "scope": scope or f"{day}_FULL_ELIGIBLE"})
    _write(receipt, {"STATE": "COMMITTED", "status": "FULL_ELIGIBLE_ONE_DAY_PUBLISHED",
                     "scope": scope or f"{day}_FULL_ELIGIBLE", "manifest_hash": manifest["manifest_hash"],
                     "quality": {"PASS": quality_pass}})
    _write(root / "meta/asl/daily_facts/published-daily-facts-authority.json", {
        "schema": "ASL_PUBLISHED_DAILY_FACTS_AUTHORITY_V01", "manifest_hash": manifest["manifest_hash"],
        "plan": plan.relative_to(root).as_posix(), "receipt": receipt.relative_to(root).as_posix()})
    return manifest


def test_publication_and_run_names_are_derived_from_the_date():
    assert publish.publication_scope(date(2026, 9, 10)) == "2026-09-10_FULL_ELIGIBLE"
    assert publish.publication_scope(date(2026, 9, 9)) == "2026-09-09_FULL_ELIGIBLE"
    assert maintenance.facts_run_name(date(2026, 9, 10)) == "daily_facts_phase1_20260910_v01"


def test_r3_state_and_verification_track_partition_drift(tmp_path):
    _r3_authority(tmp_path)
    state = maintenance.r3_state(tmp_path, "2026-09-10")
    assert state["day_present"] is True
    assert state["published_as_of"] == "2026-09-10"
    evidence = maintenance.verify_r3_publication(tmp_path, "2026-09-10")
    assert evidence["file_n"] == 1
    # Same length, different bytes: the hash gate must be the one that fires.
    (tmp_path / evidence["partition"]).write_bytes(b"r3-partition-BYTES")
    with pytest.raises(maintenance.MaintenanceError) as error:
        maintenance.verify_r3_publication(tmp_path, "2026-09-10")
    assert error.value.code == "R3_PARTITION_HASH_DRIFT"


def test_facts_verification_requires_scope_and_certified_quality(tmp_path):
    _facts_authority(tmp_path)
    assert maintenance.verify_facts_publication(tmp_path, "2026-09-10",
                                                expect_scope="2026-09-10_FULL_ELIGIBLE")["file_n"] == 1
    with pytest.raises(maintenance.MaintenanceError) as error:
        maintenance.verify_facts_publication(tmp_path, "2026-09-10", expect_scope="2026-09-09_FULL_ELIGIBLE")
    assert error.value.code == "FACTS_SCOPE_MISMATCH"

    other = tmp_path / "quality"
    other.mkdir()
    _facts_authority(other, quality_pass=False)
    with pytest.raises(maintenance.MaintenanceError) as error:
        maintenance.verify_facts_publication(other, "2026-09-10", expect_scope="2026-09-10_FULL_ELIGIBLE")
    assert error.value.code == "FACTS_RECEIPT_NOT_PASS"


def test_absent_authority_reports_no_facts_publication(tmp_path):
    state = maintenance.facts_state(tmp_path, "2026-09-10")
    assert state["published"] is False and state["day_present"] is False
    with pytest.raises(maintenance.MaintenanceError) as error:
        maintenance.verify_facts_publication(tmp_path, "2026-09-10", expect_scope="2026-09-10_FULL_ELIGIBLE")
    assert error.value.code == "FACTS_PUBLICATION_MISSING"


def test_manifest_binding_rejects_a_pointer_without_matching_plan(tmp_path):
    manifest = _r3_authority(tmp_path)
    pointer = tmp_path / "meta/asl/r3/published-daily-authority.json"
    document = json.loads(pointer.read_text())
    document["manifest_hash"] = "0" * 64
    pointer.write_text(json.dumps(document))
    with pytest.raises(maintenance.MaintenanceError) as error:
        maintenance.r3_state(tmp_path, "2026-09-10")
    assert error.value.code == "R3_MANIFEST_BINDING_INVALID"
    assert manifest["INPUT_MANIFEST_HASH"] != "0" * 64


def test_previous_trading_day_uses_the_persisted_calendar(tmp_path):
    calendar = tmp_path / "curated/trading_calendar/trade_date=2026"
    calendar.mkdir(parents=True)
    pl.DataFrame({
        "trade_date": [date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)],
        "is_trading": [True, True, True, False],
    }).write_parquet(calendar / "part-merged.parquet")
    assert maintenance.previous_trading_day(tmp_path, date(2026, 9, 11)) == date(2026, 9, 10)
    assert maintenance.previous_trading_day(tmp_path, date(2026, 9, 10)) == date(2026, 9, 9)


def test_writer_lock_rejects_a_second_writer(tmp_path):
    path = tmp_path / maintenance.LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(maintenance.MaintenanceError) as error:
            with maintenance.writer_lock(tmp_path):
                raise AssertionError("a second writer must not enter the mutation window")
        assert error.value.code == "EOD_UPDATE_RUNNING"
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    with maintenance.writer_lock(tmp_path):
        pass


def test_planning_refuses_a_future_trade_date(tmp_path):
    future = date.today().replace(year=date.today().year + 1).isoformat()
    with pytest.raises(maintenance.MaintenanceError) as error:
        maintenance.maintain(future, execute=False, root=tmp_path, mcp_url=None)
    assert error.value.code == "FUTURE_TRADE_DATE_BLOCKED"
