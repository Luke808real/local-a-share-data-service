from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import audit_r3_baostock_tradestatus0_consistency_v01 as audit  # noqa: E402


def test_status0_zero_volume_zero_amount_is_control_not_contradiction() -> None:
    metrics = audit.status0_value_metrics("0", "0.0000")
    assert metrics["volume_zero"] is True
    assert metrics["amount_zero"] is True
    assert metrics["contradiction"] is False


def test_status0_positive_volume_is_contradiction() -> None:
    metrics = audit.status0_value_metrics("353426", "19589785.9900")
    assert metrics["volume_positive"] is True
    assert metrics["contradiction"] is True


def test_status0_positive_amount_is_contradiction_even_when_volume_zero() -> None:
    metrics = audit.status0_value_metrics("0", "1.00")
    assert metrics["volume_zero"] is True
    assert metrics["amount_positive"] is True
    assert metrics["contradiction"] is True


def test_status0_blank_numeric_fields_are_not_silently_coerced() -> None:
    with pytest.raises(audit.AuditError, match="STATUS0_INVALID_NUMERIC_FIELD"):
        audit.status0_value_metrics("", "")


def test_688065_anchor_is_positive_status0_evidence() -> None:
    anchor = audit._anchor_payload(
        [
            {
                "symbol": "688065.SH",
                "trade_date": "2023-06-15",
                "tradestatus": 0,
                "volume": 353426,
                "amount": "19589785.9900",
            }
        ],
        {},
        audit.CONTRADICTION_ANCHOR,
    )
    assert anchor["provider_tradestatus"] == 0
    assert anchor["provider_volume"] == 353426
    assert anchor["canonical_present"] is False


def test_600651_anchor_is_zero_volume_control() -> None:
    anchor = audit._anchor_payload(
        [
            {
                "symbol": "600651.SH",
                "trade_date": "2016-08-25",
                "tradestatus": 0,
                "volume": 0,
                "amount": "0.0000",
            }
        ],
        {
            audit.ZERO_VOLUME_CONTROL: {
                "volume": 0,
                "amount": 0.0,
                "source": "tdx_protocol",
                "data_version": "v2",
            }
        },
        audit.ZERO_VOLUME_CONTROL,
    )
    assert anchor["provider_tradestatus"] == 0
    assert anchor["provider_volume"] == 0
    assert anchor["provider_amount"] == "0.0000"
    assert anchor["canonical_present"] is True
    assert anchor["canonical_volume"] == 0


def test_receipt_payload_hash_drift_fails_closed() -> None:
    raw_payload = {"rows": [["2023-06-15", "sh.688065", "1"]]}
    normalized_payload = {"CASES": []}
    raw_hash = audit.sha256_json(raw_payload)
    normalized_hash = audit.sha256_json(normalized_payload)
    raw = {
        "raw_payload": raw_payload,
        "raw_sha256": raw_hash,
        "normalized_sha256": normalized_hash,
    }
    normalized = {"normalized_payload": normalized_payload, "normalized_sha256": normalized_hash}
    index = {"raw_sha256": raw_hash, "normalized_sha256": normalized_hash}
    assert audit.verify_receipt_payload_hashes(raw, normalized, index) == (
        raw_hash,
        normalized_hash,
    )
    index["raw_sha256"] = "drift"
    with pytest.raises(audit.AuditError, match="RAW_RECEIPT_PAYLOAD_HASH_DRIFT"):
        audit.verify_receipt_payload_hashes(raw, normalized, index)


def test_json_file_payload_hash_matches_emitted_bytes(tmp_path: Path) -> None:
    payload = {"b": 2, "a": 1}
    audit.write_repo_json(tmp_path, "hash.json", payload)
    assert audit.sha256_json_file_payload(payload) == audit.sha256_file(
        tmp_path / "reports" / "implementation" / "hash.json"
    )


def _provider_row(symbol: str, trade_date: str, volume: int, amount: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "request_id": "R3SAC-000001",
        "volume": volume,
        "amount": amount,
        "open": "1.0000",
        "high": "1.0000",
        "low": "1.0000",
        "close": "1.0000",
        "preclose": "1.0000",
        "_contradiction": volume > 0 or float(amount) > 0,
    }


def test_contradiction_manifest_is_deterministic_and_sorted() -> None:
    rows = [
        _provider_row("688065.SH", "2023-06-15", 353426, "19589785.9900"),
        _provider_row("600651.SH", "2016-08-25", 0, "0.0000"),
        _provider_row("000001.SZ", "2023-06-15", 0, "1.0000"),
    ]
    left, _ = audit.build_contradiction_manifest(rows, {})
    right, _ = audit.build_contradiction_manifest(list(reversed(rows)), {})
    assert left == right
    assert left["KEY_N"] == 2
    assert [row["symbol"] for row in left["ROWS"]] == ["000001.SZ", "688065.SH"]


def test_cross_canonical_categories_are_exact() -> None:
    rows = [
        {"symbol": "000001.SZ", "trade_date": "2023-01-03", "canonical_present": True, "canonical_volume": 10},
        {"symbol": "000002.SZ", "trade_date": "2023-01-03", "canonical_present": True, "canonical_volume": 0},
        {"symbol": "000003.SZ", "trade_date": "2023-01-03", "canonical_present": False, "canonical_volume": None},
    ]
    result = audit.classify_cross_canonical(rows)
    assert result["CONTRADICTION_WITH_CANONICAL_POSITIVE_N"] == 1
    assert result["CONTRADICTION_WITH_CANONICAL_ZERO_N"] == 1
    assert result["CONTRADICTION_WITH_CANONICAL_ABSENT_N"] == 1


def test_repo_report_writer_does_not_write_canonical_data_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    data = tmp_path / "data"
    (data / "curated" / "daily_bars").mkdir(parents=True)
    before = sorted(path.relative_to(data) for path in data.rglob("*"))
    audit.write_repo_json(repo, "status0_test.json", {"ok": True})
    after = sorted(path.relative_to(data) for path in data.rglob("*"))
    assert before == after
    assert (repo / "reports" / "implementation" / "status0_test.json").is_file()
