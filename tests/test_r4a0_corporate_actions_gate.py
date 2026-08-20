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

import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from ashare_data.r4a0_corporate_actions_gate import (
    CNEQUITY_PIN_SHA,
    FORMAL_IDENTITY_HASH,
    evaluate_pin_contract,
    run_gate,
)


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


def write_manifest(root, *, success: bool = True, scope=("2016-01-01", "2026-08-17")):
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
        start_s, end_s = scope
        metadata = json.dumps(
            {
                "trade_date": "2026-08-17",
                "backfill": True,
                "backfill_scope": {"start": start_s, "end": end_s, "symbols": None},
            }
        )
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
                metadata,
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
                start_s,
                end_s,
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


def write_watermark(root, *, start="2016-01-01", end="2026-08-17"):
    state = root / "meta" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "corporate_actions.json").write_text(
        json.dumps({"start": start, "end": end}), encoding="utf-8"
    )


def write_manifest_batches(
    root,
    intervals,
    *,
    failed_intervals=None,
    ok_symbols=None,
    failed_symbols=None,
    chunk_symbols=None,
):
    """Write success run (no window metadata) + per-batch coverage windows.

    `intervals`: list of (start, end) successful batch windows.
    `failed_intervals`: list of (start, end) batches with status != success
    (must never prove completeness).
    `ok_symbols` / `failed_symbols`: optional queried-symbol list written into
    each successful / failed batch's symbols_json (receipted symbol scope).
    `chunk_symbols`: optional per-successful-batch symbol lists (overrides
    `ok_symbols` when provided).
    """
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
    con.execute(
        "INSERT INTO ingestion_runs VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "run1",
            "r3_corporate_actions",
            "success",
            "2026-08-19T00:00:00+00:00",
            "2026-08-19T01:00:00+00:00",
            0,
            0,
            None,
            None,
        ),
    )
    idx = 0
    for status, windows in (
        ("success", intervals),
        ("failed", failed_intervals or []),
    ):
        for k, (s, e) in enumerate(windows):
            if status == "failed":
                sym_json = json.dumps(list(failed_symbols or []), ensure_ascii=False)
            elif chunk_symbols is not None:
                sym_json = json.dumps(list(chunk_symbols[k] or []), ensure_ascii=False)
            else:
                sym_json = json.dumps(list(ok_symbols or []), ensure_ascii=False)
            con.execute(
                "INSERT INTO ingestion_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "run1",
                    f"b{idx}",
                    "corporate_actions",
                    "corporate_actions",
                    status,
                    sym_json,
                    s,
                    e,
                    0,
                    0,
                    0,
                    "2026-08-19T00:00:00+00:00",
                    "2026-08-19T00:30:00+00:00",
                    None,
                    None,
                    0,
                ),
            )
            idx += 1
    con.commit()
    con.close()


def gate_at(root, *, expected=("600519.SH", "000001.SZ"), **kw):
    """Inject a bounded EXPECTED_SYMBOLS scope so tests never depend on the
    real daily_bars identity artifact."""
    return run_gate(root, expected_symbols=list(expected), **kw)


def full_windows(expected, span=("2016-01-01", "2026-08-17")):
    return span


def test_valid_dataset_pass(tmp_path):
    root = tmp_path / "root"
    exp = ["600519.SH", "000001.SZ"]
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4)),
            base_row("000001.SZ", date(2026, 6, 1)),
        ],
    )
    write_manifest_batches(root, [("2016-01-01", "2026-08-17")], ok_symbols=exp)
    report = gate_at(root, expected=exp)
    assert report["R4A0_READY"] is True
    assert report["SCHEMA_STATUS"] == "PASS"
    assert report["SCOPE_STATUS"] == "PASS"
    assert report["COVERAGE_STATUS"] == "PASS"
    assert report["DATE_COVERAGE_PASS"] is True
    assert report["SYMBOL_COVERAGE_PASS"] is True
    assert report["UNIQUENESS_STATUS"] == "PASS"
    assert report["PROVENANCE_STATUS"] == "PASS"
    assert report["ROW_COUNT"] == 2
    assert report["SH_ROWS"] == 1 and report["SZ_ROWS"] == 1 and report["OTHER_ROWS"] == 0

    # A successful full-window receipt with ZERO event rows still proves the
    # symbol was queried (sparse event data): add 600519 -> 0-rows receipt only
    # must not be treated as FAIL by row presence. Covered via E test too.


def test_dataset_missing_fail(tmp_path):
    root = tmp_path / "root"
    report = gate_at(root, expected=["600519.SH"])
    assert report["R4A0_READY"] is False
    assert report["DATASET_EXISTS"] is False
    assert report["BLOCKER"] == "CORPORATE_ACTIONS_DATASET_NOT_BUILT"


def test_required_schema_missing_fail(tmp_path):
    root = tmp_path / "root"
    bad_schema = {k: v for k, v in SCHEMA.items() if k != "cash_dividend"}
    write_parquet(root, [base_row()], schema=bad_schema)
    write_manifest_batches(root, [("2016-01-01", "2026-08-17")], ok_symbols=["600519.SH"])
    report = gate_at(root, expected=["600519.SH"])
    assert report["R4A0_READY"] is False
    assert report["SCHEMA_STATUS"] == "FAIL"
    assert "cash_dividend" in report["missing_fields"]
    assert report["BLOCKER"] == "SCHEMA_INCOMPLETE"


def test_provenance_missing_fail(tmp_path):
    root = tmp_path / "root"
    write_parquet(root, [base_row(source=None, data_version=None)])
    write_manifest_batches(root, [("2016-01-01", "2026-08-17")], ok_symbols=["600519.SH"])
    report = gate_at(root, expected=["600519.SH"])
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
    write_manifest_batches(root, [("2016-01-01", "2026-08-17")], ok_symbols=["600519.SH"])
    report = gate_at(root, expected=["600519.SH"])
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
    write_manifest_batches(root, [("2016-01-01", "2026-08-17")], ok_symbols=["600519.SH"])
    report = gate_at(root, expected=["600519.SH"])
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
    report = gate_at(root, expected=["600519.SH", "000001.SZ"])
    assert report["R4A0_READY"] is False
    assert report["COVERAGE_STATUS"] == "UNKNOWN_PARTIAL"
    assert report["BLOCKER"] == "DATE_COVERAGE_UNPROVEN"


def test_a_partial_successful_run_rejected(tmp_path):
    # successful corporate run whose covered window is only 2026-08-01..2026-08-17
    root = tmp_path / "root"
    write_parquet(root, [base_row("000001.SZ", date(2026, 8, 3))])
    write_manifest_batches(root, [("2026-08-01", "2026-08-17")], ok_symbols=["000001.SZ"])
    report = gate_at(root, expected=["000001.SZ"])
    assert report["COVERAGE_STATUS"] == "UNKNOWN_PARTIAL"
    assert report["PARTIAL_RUN_REJECTED"] is True
    assert report["R4A0_READY"] is False


def test_b_explicit_full_window_coverage_pass(tmp_path):
    # successful run explicitly covering 2016-01-01..2026-08-17
    root = tmp_path / "root"
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4)),
            base_row("000001.SZ", date(2026, 6, 1)),
        ],
    )
    exp = ["600519.SH", "000001.SZ"]
    write_manifest_batches(root, [("2016-01-01", "2026-08-17")], ok_symbols=exp)
    report = gate_at(root, expected=exp)
    assert report["COVERAGE_STATUS"] == "PASS"
    assert report["SYMBOL_COVERAGE_PASS"] is True
    assert report["COVERED_WINDOW"] == {"start": "2016-01-01", "end": "2026-08-17"}
    assert report["R4A0_READY"] is True


def test_c_watermark_start_after_window_rejected(tmp_path):
    # watermark coverage starts 2020-01-01, i.e. after requested 2016-01-01
    root = tmp_path / "root"
    write_parquet(root, [base_row("600519.SH", date(2020, 1, 4))])
    write_watermark(root, start="2020-01-01", end="2026-08-17")
    report = gate_at(root, expected=["600519.SH"])
    assert report["COVERAGE_STATUS"] == "UNKNOWN_PARTIAL"
    assert report["R4A0_READY"] is False


def test_d_wrong_pin_fails_contract():
    wrong = {
        "url": "https://github.com/rootSunc/CNEquity.git",
        "vcs_info": {
            "vcs": "git",
            "commit_id": "deadbeef00000000000000000000000000000000",
            "requested_revision": "deadbeef00000000000000000000000000000000",
        },
    }
    ev = evaluate_pin_contract(
        wrong,
        list(SCHEMA.keys()),
        "tdx_protocol",
        "eastmoney",
        pin_expected=CNEQUITY_PIN_SHA,
    )
    assert ev["PIN_MATCH"] is False
    assert ev["SCHEMA_MATCH"] is True
    assert ev["SOURCE_MATCH"] is True
    assert ev["match"] is False

    ok = {
        "url": "https://github.com/rootSunc/CNEquity.git",
        "vcs_info": {
            "vcs": "git",
            "commit_id": CNEQUITY_PIN_SHA,
            "requested_revision": CNEQUITY_PIN_SHA,
        },
    }
    ev2 = evaluate_pin_contract(
        ok,
        list(SCHEMA.keys()),
        "tdx_protocol",
        "eastmoney",
        pin_expected=CNEQUITY_PIN_SHA,
    )
    assert ev2["PIN_MATCH"] is True
    assert ev2["match"] is True


def test_a_gap_between_successful_runs_rejected(tmp_path):
    # 2016-2018 + 2025-2026, gap 2019-2024 -> must NOT pass as covered
    root = tmp_path / "root"
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4)),
            base_row("000001.SZ", date(2025, 3, 1)),
        ],
    )
    write_manifest_batches(
        root,
        [("2016-01-01", "2018-12-31"), ("2025-01-01", "2026-08-17")],
        ok_symbols=["600519.SH", "000001.SZ"],
    )
    report = gate_at(root, expected=["600519.SH", "000001.SZ"])
    assert report["CONTIGUOUS_COVERAGE"] is False
    assert report["COVERAGE_STATUS"] == "UNKNOWN_PARTIAL"
    assert len(report["COVERAGE_GAPS"]) == 1
    assert report["R4A0_READY"] is False


def test_b_contiguous_full_union_pass(tmp_path):
    # exact-boundary adjacent intervals collectively cover the full window
    root = tmp_path / "root"
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4)),
            base_row("000001.SZ", date(2026, 6, 1)),
        ],
    )
    write_manifest_batches(
        root,
        [("2016-01-01", "2020-12-31"), ("2021-01-01", "2026-08-17")],
        ok_symbols=["600519.SH", "000001.SZ"],
    )
    report = gate_at(root, expected=["600519.SH", "000001.SZ"])
    assert report["CONTIGUOUS_COVERAGE"] is True
    assert report["COVERAGE_STATUS"] == "PASS"
    assert report["SYMBOL_COVERAGE_PASS"] is True
    assert report["COVERAGE_GAPS"] == []
    assert report["R4A0_READY"] is True


def test_c_overlapping_full_union_pass(tmp_path):
    # overlapping successful intervals collectively cover the full window
    root = tmp_path / "root"
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4)),
            base_row("000001.SZ", date(2026, 6, 1)),
        ],
    )
    write_manifest_batches(
        root,
        [("2016-01-01", "2023-06-30"), ("2020-01-01", "2026-08-17")],
        ok_symbols=["600519.SH", "000001.SZ"],
    )
    report = gate_at(root, expected=["600519.SH", "000001.SZ"])
    assert report["COVERAGE_STATUS"] == "PASS"
    assert report["SYMBOL_COVERAGE_PASS"] is True
    assert report["COVERAGE_GAPS"] == []
    assert report["R4A0_READY"] is True


def test_d_failed_interval_ignored(tmp_path):
    # failed evidence fills otherwise missing gap -> ignored -> UNKNOWN_PARTIAL
    root = tmp_path / "root"
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4)),
            base_row("000001.SZ", date(2025, 3, 1)),
        ],
    )
    write_manifest_batches(
        root,
        [("2016-01-01", "2018-12-31"), ("2025-01-01", "2026-08-17")],
        failed_intervals=[("2019-01-01", "2024-12-31")],
        ok_symbols=["600519.SH", "000001.SZ"],
    )
    report = gate_at(root, expected=["600519.SH", "000001.SZ"])
    assert report["COVERAGE_STATUS"] == "UNKNOWN_PARTIAL"
    assert len(report["COVERAGE_GAPS"]) == 1
    assert report["R4A0_READY"] is False


def test_e_formal_cli_enforces_pin_without_flag(tmp_path, monkeypatch):
    import importlib.util

    tools_p = Path(__file__).resolve().parents[1] / "tools"
    tools_p.joinpath("verify_r4a0_corporate_actions_gate.py").resolve()
    spec = importlib.util.spec_from_file_location(
        "verify_r4a0_cli",
        tools_p
        / "verify_r4a0_corporate_actions_gate.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    wrong_cc = {
        "PIN_EXPECTED": CNEQUITY_PIN_SHA,
        "PIN_ACTUAL": "deadbeef00000000000000000000000000000000",
        "PIN_MATCH": False,
        "SCHEMA_MATCH": True,
        "SOURCE_MATCH": True,
        "match": False,
    }
    monkeypatch.setattr(mod, "contract_check", lambda: wrong_cc)
    monkeypatch.setattr(mod, "run_gate", lambda root, **kwargs: {"dummy": True})
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_r4a0_cli", "--config", "config/cnequity.toml"],
    )
    # no --contract-check flag passed: pin enforcement must still fail
    rc = mod.main()
    assert rc == 2


def test_symbol_scope_a_partial_symbol_set_rejected(tmp_path):
    # only 1 of several EXPECTED_SYMBOLS has a full-date successful receipt
    root = tmp_path / "root"
    expected = ["000001.SH", "000002.SH", "000003.SH", "000004.SH"]
    write_parquet(root, [base_row("000001.SH", date(2016, 1, 4))])
    write_manifest_batches(root, [("2016-01-01", "2026-08-17")], ok_symbols=["000001.SH"])
    report = gate_at(root, expected=expected)
    assert report["DATE_COVERAGE_PASS"] is True
    assert report["SYMBOL_COVERAGE_PASS"] is False
    assert report["MISSING_SYMBOL_N"] == 3
    assert len(report["MISSING_SYMBOL_SAMPLE"]) == 3
    assert report["R4A0_READY"] is False


def test_symbol_scope_b_failed_chunk_not_counted(tmp_path):
    # remaining symbols are only in a FAILED chunk -> not counted -> READY=false
    root = tmp_path / "root"
    expected = ["600519.SH", "000001.SZ"]
    write_parquet(root, [base_row("600519.SH", date(2016, 1, 4))])
    write_manifest_batches(
        root,
        [("2016-01-01", "2026-08-17")],
        ok_symbols=["600519.SH"],
        failed_intervals=[("2016-01-01", "2026-08-17")],
        failed_symbols=["000001.SZ"],
    )
    report = gate_at(root, expected=expected)
    assert report["SYMBOL_COVERAGE_PASS"] is False
    assert report["MISSING_SYMBOL_N"] == 1
    assert report["R4A0_READY"] is False


def test_symbol_scope_c_union_covers_all_expected(tmp_path):
    # successful chunks' union covers every expected symbol, each full-date
    root = tmp_path / "root"
    expected = ["600519.SH", "000001.SZ", "000002.SZ"]
    write_parquet(root, [base_row("000002.SZ", date(2026, 6, 1))])
    write_manifest_batches(
        root,
        [("2016-01-01", "2026-08-17"), ("2016-01-01", "2026-08-17")],
        chunk_symbols=[["600519.SH"], ["000001.SZ", "000002.SZ"]],
    )
    report = gate_at(root, expected=expected)
    assert report["SYMBOL_COVERAGE_PASS"] is True
    assert report["SUCCESSFULLY_COVERED_SYMBOL_N"] == 3
    assert report["MISSING_SYMBOL_N"] == 0
    assert report["R4A0_READY"] is True


def test_symbol_scope_d_partial_symbol_rejected(tmp_path):
    # one symbol only has a partial-date receipt -> PARTIAL_SYMBOL_N > 0
    root = tmp_path / "root"
    expected = ["600519.SH", "000001.SZ"]
    write_parquet(
        root,
        [
            base_row("600519.SH", date(2016, 1, 4)),
            base_row("000001.SZ", date(2022, 6, 1)),
        ],
    )
    write_manifest_batches(
        root,
        [("2016-01-01", "2026-08-17"), ("2020-01-01", "2023-12-31")],
        chunk_symbols=[["600519.SH"], ["000001.SZ"]],
    )
    report = gate_at(root, expected=expected)
    assert report["SYMBOL_COVERAGE_PASS"] is False
    assert report["PARTIAL_SYMBOL_N"] == 1
    assert "000001.SZ" in report["PARTIAL_SYMBOL_SAMPLE"]
    assert report["R4A0_READY"] is False


def test_symbol_scope_e_zero_event_rows_still_covered(tmp_path):
    # full-window successful receipt for a symbol with ZERO event rows in
    # parquet must still prove that symbol was queried (sparse event data)
    root = tmp_path / "root"
    expected = ["600519.SH", "000001.SZ"]
    write_parquet(root, [base_row("600519.SH", date(2016, 1, 4))])
    write_manifest_batches(root, [("2016-01-01", "2026-08-17")], ok_symbols=expected)
    report = gate_at(root, expected=expected)
    assert report["SYMBOL_COVERAGE_PASS"] is True
    assert report["SUCCESSFULLY_COVERED_SYMBOL_N"] == 2
    assert report["R4A0_READY"] is True


def test_symbol_scope_f_rows_without_receipt_fail(tmp_path):
    # parquet has rows for the symbol but no successful full-window receipt
    root = tmp_path / "root"
    expected = ["600519.SH"]
    write_parquet(root, [base_row("600519.SH", date(2016, 1, 4))])
    write_manifest_batches(root, [("2020-01-01", "2023-12-31")], ok_symbols=["600519.SH"])
    report = gate_at(root, expected=expected)
    assert report["SYMBOL_COVERAGE_PASS"] is False
    assert report["PARTIAL_SYMBOL_N"] == 1
    assert report["R4A0_READY"] is False


def test_symbol_scope_g_identity_hash_mismatch(tmp_path):
    # frozen R3 formal identity hash mismatch -> FAIL CLOSED
    root = tmp_path / "root"
    write_parquet(root, [base_row("600519.SH", date(2016, 1, 4))])
    write_manifest_batches(root, [("2016-01-01", "2026-08-17")], ok_symbols=["600519.SH"])
    report = run_gate(
        root,
        expected_symbols=["600519.SH"],
        expected_identity_hash="0" * 64,
        expected_identity_n=1,
    )
    assert report["R4A0_READY"] is False
    assert report["IDENTITY_STATUS"] == "FAIL"
    assert report["BLOCKER"] == "FORMAL_IDENTITY_MISMATCH"
