"""Targeted tests for the R4A0 corporate_actions availability gate.

Scenarios required by the R4A0 task contract:
  1. valid dataset -> PASS
  2. dataset missing -> FAIL
  3. required schema missing -> FAIL
  4. provenance missing -> FAIL
  5. true duplicate action -> FAIL
  6. legal same-date multi-component action is NOT flagged duplicate
  7. coverage UNKNOWN/PARTIAL -> READY=false
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import polars as pl
import pytest

from ashare_data.r4a0_corporate_actions_gate import run_gate


SCHEMA = {
    "symbol": pl.Utf8,
    "ex_date": pl.Date,
    "action_type": pl.Utf8,
    "cash_dividend": pl.Float64,
    "bonus_ratio": pl.Float64,
    "transfer_ratio": pl.Float64,
    "allotment_ratio": pl.Float64,
    "allotment_price": pl.Float64,
    "source": pl.Utf8,
    "data_version": pl.Utf8,
    "fetched_at": pl.Datetime("us", time_zone="UTC"),
}

FETCHED = datetime(2026, 8, 19, tzinfo=timezone.utc)


def base_row(
    symbol: str = "600519.SH",
    ex_date: date = date(2016, 1, 4),
    action_type: str = "CASH_DIVIDEND",
    **overrides,
) -> dict:
    row = {
        "symbol": symbol,
        "ex_date": ex_date,
        "action_type": action_type,
        "cash_dividend": 0.5,
        "bonus_ratio": 0.0,
        "transfer_ratio": 0.0,
        "allotment_ratio": 0.0,
        "allotment_price": 0.0,
        "source": "tdx_protocol",
        "data_version": "v0.7.2",
        "fetched_at": FETCHED,
    }
    row.update(overrides)
    return row


def write_parquet(root, rows, schema=None):
    d = root / "curated" / "corporate_actions" / "2016"
    d.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(rows, schema=schema if schema is not None else SCHEMA)
    df.write_parquet(d / "part.parquet")


def write_manifest(root, *, success: bool = True):
    meta = root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(meta / "manifest.db")
    con.execute(
        "CREATE TABLE ingestion_runs (run_id TEXT, job_name TEXT, status TEXT, "
        "started_at TEXT, finished_at TEXT, rows_read INTEGER, rows_written INTEGER, "
        "error_message TEXT, metadata_json TEXT)"
    )
    con.execute(
        "CREATE TABLE ingestion_batches (run_id TEXT, batch_id TEXT, task_id TEXT, "
        "dataset TEXT, status TEXT, symbols_json TEXT, window_start TEXT, "
        "window_end TEXT, rows_read INTEGER, rows_written INTEGER, retry_count INTEGER, "
        "started_at TEXT, finished_at TEXT, error_message TEXT, heartbeat_at TEXT, "
        "blocks_compaction INTEGER)"
    )
    if success:
        con.execute(
            "INSERT INTO ingestion_runs VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "run1",
                "r3_corporate_actions",
                "success",
                "2026-08-19T00:00:00+00:00",
                "2026-08-19T01:00:00+00:00",
                10,
                10,
                None,
                '{"trade_date":"2026-08-17","backfill":true,'
                '"backfill_scope":{"start":"2016-01-01","end":null,"symbols":null}}',
            ),
        )
        con.execute(
            "INSERT INTO ingestion_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "run1",
                "b1",
                "corporate_actions",
                "corporate_actions",
                "success",
                "[]",
                "2016-01-01",
                "2026-08-17",
                10,
                10,
                0,
                "2026-08-19T00:00:00+00:00",
                "2026-08-19T00:30:00+00:00",
                None,
                None,
                0,
            ),
        )
    con.commit()
    con.close()


def test_valid_dataset_pass(tmp_path):
    root = tmp_path / "root"
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4)),
            base_row("000001.SZ", date(2026, 6, 1)),
        ],
    )
    write_manifest(root)
    report = run_gate(root)
    assert report["R4A0_READY"] is True
    assert report["SCHEMA_STATUS"] == "PASS"
    assert report["SCOPE_STATUS"] == "PASS"
    assert report["COVERAGE_STATUS"] == "PASS"
    assert report["UNIQUENESS_STATUS"] == "PASS"
    assert report["PROVENANCE_STATUS"] == "PASS"
    assert report["ROW_COUNT"] == 2
    assert report["SH_ROWS"] == 1 and report["SZ_ROWS"] == 1 and report["OTHER_ROWS"] == 0


def test_dataset_missing_fail(tmp_path):
    root = tmp_path / "root"
    report = run_gate(root)
    assert report["R4A0_READY"] is False
    assert report["DATASET_EXISTS"] is False
    assert report["BLOCKER"] == "CORPORATE_ACTIONS_DATASET_NOT_BUILT"


def test_required_schema_missing_fail(tmp_path):
    root = tmp_path / "root"
    bad_schema = {k: v for k, v in SCHEMA.items() if k != "cash_dividend"}
    write_parquet(root, [base_row()], schema=bad_schema)
    write_manifest(root)
    report = run_gate(root)
    assert report["R4A0_READY"] is False
    assert report["SCHEMA_STATUS"] == "FAIL"
    assert "cash_dividend" in report["missing_fields"]
    assert report["BLOCKER"] == "SCHEMA_INCOMPLETE"


def test_provenance_missing_fail(tmp_path):
    root = tmp_path / "root"
    write_parquet(root, [base_row(source=None, data_version=None)])
    write_manifest(root)
    report = run_gate(root)
    assert report["R4A0_READY"] is False
    assert report["PROVENANCE_STATUS"] == "FAIL"
    assert report["UNRESOLVED_N"] >= 1


def test_true_duplicate_action_fail(tmp_path):
    root = tmp_path / "root"
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4), "CASH_DIVIDEND"),
            base_row("600519.SH", date(2016, 1, 4), "CASH_DIVIDEND", cash_dividend=0.9),
        ],
    )
    write_manifest(root)
    report = run_gate(root)
    assert report["R4A0_READY"] is False
    assert report["DUPLICATE_ACTION_N"] == 1
    assert report["UNIQUENESS_STATUS"] == "FAIL"
    assert report["BLOCKER"] == "DUPLICATE_ACTION_ROWS"


def test_legal_same_date_multi_component_not_duplicate(tmp_path):
    root = tmp_path / "root"
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4), "CASH_DIVIDEND"),
            base_row(
                "600519.SH",
                date(2016, 1, 4),
                "BONUS_SHARE",
                cash_dividend=0.0,
                bonus_ratio=0.3,
            ),
        ],
    )
    write_manifest(root)
    report = run_gate(root)
    assert report["DUPLICATE_ACTION_N"] == 0
    assert report["DUPLICATE_EXDATE_GROUP_N"] == 1
    assert report["UNIQUENESS_STATUS"] == "PASS"
    assert report["R4A0_READY"] is True


def test_coverage_unknown_partial_ready_false(tmp_path):
    root = tmp_path / "root"
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4)),
            base_row("000001.SZ", date(2026, 6, 1)),
        ],
    )
    # no manifest, no watermark -> historical coverage unprovable
    report = run_gate(root)
    assert report["R4A0_READY"] is False
    assert report["COVERAGE_STATUS"] == "UNKNOWN_PARTIAL"
    assert report["BLOCKER"] == "COVERAGE_UNPROVEN"
