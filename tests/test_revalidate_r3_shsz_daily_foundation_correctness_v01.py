"""Offline tests for R3 SH/SZ daily-foundation correctness revalidation."""

from __future__ import annotations

import ast
import json
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import revalidate_r3_shsz_daily_foundation_correctness_v01 as audit  # noqa: E402


def _manifest(hash_value: str = "h", files: list[dict] | None = None) -> dict:
    return {
        "INPUT_FILE_N": 1,
        "INPUT_MANIFEST_HASH": hash_value,
        "FILES": files or [{"relative_path": "curated/daily_bars/x.parquet", "file_size": 1, "sha256": "x"}],
    }


def _good_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["000001.SZ", "300546.SZ"],
            "trade_date": [date(2016, 9, 28), date(2016, 9, 29)],
            "open": [10.0, 32.36],
            "high": [10.5, 32.36],
            "low": [9.5, 32.36],
            "close": [10.2, 32.36],
            "volume": [1000, 100],
            "amount": [10000.0, 3236.0],
            "source": ["tdx_protocol", "baostock"],
            "data_version": ["v2", "v2"],
            "fetched_at": [datetime(2026, 8, 26), datetime(2026, 8, 26)],
        }
    ).with_columns(
        pl.col("trade_date").cast(pl.Date),
        pl.col("fetched_at").cast(pl.Datetime("us", "UTC")),
    )


def test_authority_constants_are_frozen():
    assert audit.BASE_HEAD == "3859d195855d50386595be5efbb6d5541ec23977"
    assert audit.INPUT_FILE_N == 2580
    assert audit.INPUT_MANIFEST_HASH.startswith("ba720d8f")
    assert audit.SHSZ_IDENTITY_N == 5456
    assert audit.REPAIRED_VOLUME_KEY_N == 1169


def test_input_manifest_equality_requires_complete_files():
    pre = _manifest(files=[{"relative_path": "a", "file_size": 1, "sha256": "x"}])
    assert audit.input_manifests_equal(pre, dict(pre))
    post = dict(pre)
    post["FILES"] = [{"relative_path": "a", "file_size": 2, "sha256": "x"}]
    assert not audit.input_manifests_equal(pre, post)
    post = dict(pre)
    post["INPUT_FILE_N"] = 2
    assert not audit.input_manifests_equal(pre, post)


def test_pre_post_drift_fails_closed_before_report_write():
    states = [
        _manifest(files=[{"relative_path": "a", "file_size": 1, "sha256": "x"}]),
        _manifest(files=[{"relative_path": "a", "file_size": 2, "sha256": "y"}]),
    ]

    def builder(_: Path) -> dict:
        return states.pop(0)

    with pytest.raises(audit.InputDriftDuringRevalidation, match="INPUT_DRIFT_DURING_REVALIDATION"):
        audit.run_prescan_postscan(Path("/unused"), manifest_builder=builder, scanner=lambda _: {"ok": True})


def test_pre_validator_blocks_scan_on_initial_input_drift():
    called = []
    bad = _manifest(files=[{"relative_path": "a", "file_size": 1, "sha256": "x"}])

    def reject(_: dict) -> None:
        raise audit.RevalidationError("INPUT_MANIFEST_HASH_MISMATCH")

    with pytest.raises(audit.RevalidationError, match="INPUT_MANIFEST_HASH_MISMATCH"):
        audit.run_prescan_postscan(
            Path("/unused"),
            manifest_builder=lambda _: bad,
            pre_validator=reject,
            scanner=lambda _: called.append(True),
        )
    assert called == []


def test_daily_quality_accepts_clean_frame():
    result = audit.validate_daily_frame(_good_frame())
    assert result["TOTAL_ROW_N"] == 2
    assert result["ACTUAL_KEY_N"] == 2
    assert result["DUPLICATE_KEY_N"] == 0
    assert result["FINITE_OHLC_BAD_ROW_N"] == 0
    assert result["INVALID_VOLUME_ROW_N"] == 0
    assert result["INVALID_AMOUNT_ROW_N"] == 0
    assert result["OHLC_ORDER_BAD_ROW_N"] == 0


def test_daily_quality_detects_duplicate_malformed_and_invalid_rows():
    bad = pl.concat([_good_frame(), _good_frame().head(1)]).with_columns(
        pl.Series("symbol", ["000001.SZ", "bad", "000001.SZ"]),
        pl.Series("volume", [1000, -1, 1000]),
    )
    result = audit.validate_daily_frame(bad)
    assert result["DUPLICATE_KEY_N"] == 1
    assert result["MALFORMED_SYMBOL_ROW_N"] == 1
    assert result["INVALID_VOLUME_ROW_N"] == 1


def test_changed_manifest_hash_mismatch_and_duplicate_fail_closed(tmp_path: Path):
    path = tmp_path / "changed.json"
    row = {
        "symbol": "000001.SZ",
        "trade_date": "2016-01-04",
        "classification": "AMBIGUOUS",
        "old_volume": 100,
        "fresh_tdx_volume": 200,
        "predicted_corrected_volume": None,
        "fetch_status": "RESOLVED",
    }
    path.write_text(json.dumps([row]), encoding="utf-8")
    original = audit.CHANGED_MANIFEST_HASH
    original_reference = audit.CHANGED_MANIFEST_REFERENCE
    audit.CHANGED_MANIFEST_HASH = "not-this-hash"
    audit.CHANGED_MANIFEST_REFERENCE = "changed.json"
    try:
        with pytest.raises(audit.RevalidationError, match="CHANGED_MANIFEST_HASH_MISMATCH"):
            audit.load_changed_rows(tmp_path)
    finally:
        audit.CHANGED_MANIFEST_HASH = original
        audit.CHANGED_MANIFEST_REFERENCE = original_reference


def test_changed_volume_missing_and_old_mismatch_are_explicit():
    rows = [
        {
            "symbol": "000001.SZ",
            "trade_date": "2016-01-04",
            "classification": "AMBIGUOUS",
            "old_volume": 100,
            "fresh_tdx_volume": 200,
            "predicted_corrected_volume": None,
            "fetch_status": "RESOLVED",
        }
    ]
    daily = _good_frame().select(["symbol", "trade_date", "volume"])
    result = audit.revalidate_changed_volumes(daily, rows)
    assert result["REPAIRED_VOLUME_MISSING_N"] == 1
    assert not result["REPAIRED_VOLUME_EXACT"]

    rows[0]["symbol"] = "000001.SZ"
    rows[0]["trade_date"] = "2016-09-28"
    rows[0]["fresh_tdx_volume"] = 10
    result = audit.revalidate_changed_volumes(daily, rows)
    assert result["REPAIRED_VOLUME_MISMATCH_N"] == 1
    assert result["CURRENT_EQUALS_RETAINED_OLD_N"] == 0


def test_changed_manifest_duplicate_keys_are_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    rows = [
        {
            "symbol": "000001.SZ",
            "trade_date": "2016-01-04",
            "classification": "AMBIGUOUS",
            "old_volume": 100,
            "fresh_tdx_volume": 200,
            "predicted_corrected_volume": None,
            "fetch_status": "RESOLVED",
        }
    ] * audit.REPAIRED_VOLUME_KEY_N
    path = tmp_path / "changed.json"
    path.write_bytes(audit.canonical_json_bytes(rows))
    monkeypatch.setattr(audit, "CHANGED_MANIFEST_HASH", audit.sha256_file(path))
    monkeypatch.setattr(audit, "CHANGED_MANIFEST_REFERENCE", "changed.json")
    with pytest.raises(audit.RevalidationError, match="CHANGED_MANIFEST_DUPLICATE_KEY"):
        audit.load_changed_rows(tmp_path)


def test_frozen_identity_hash_is_order_independent():
    assert audit.identity_hash(["000002.SZ", "000001.SZ", "000001.SZ"]) == audit.identity_hash(
        ["000001.SZ", "000002.SZ"]
    )


def test_full_completeness_unknown_is_not_pass():
    result = audit.completeness_result(
        actual_key_n=2,
        actual_symbol_n=1,
        calendar={"TRADING_DATE_N_IN_WINDOW": 2},
        identity={"SHSZ_IDENTITY_N": 5456},
    )
    assert result["EXPECTED_KEY_N"] == "UNKNOWN"
    assert result["MISSING_TRADING_KEY_N"] == "UNKNOWN"
    assert result["AUTHORITY_STATUS"] != "PASS"


def test_no_provider_or_canonical_writer_imports():
    tree = ast.parse(Path(audit.__file__).read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    forbidden = ("baostock", "requests", "urllib", "socket", "httpx")
    assert not [name for name in imports if name == "baostock" or name.startswith(forbidden)]
    source = Path(audit.__file__).read_text(encoding="utf-8")
    assert ".write_parquet" not in source
    assert "os.replace" not in source
    assert "curated/daily_bars" not in source or "relative_path" in source


def test_safety_report_values_are_non_mutating():
    assert audit.TARGET_KEYS == (("300546.SZ", date(2016, 9, 29)), ("300546.SZ", date(2016, 10, 10)))
