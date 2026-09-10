"""Exact-date official reference-price evidence is fail-closed by contract."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "src")
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from ashare_data.daily_facts_phase1 import reconcile
from ashare_data.reference_price_evidence import (
    FORMULA,
    SCHEMA,
    SOURCE,
    ReferencePriceEvidenceError,
    load_reference_price_evidence,
)


def _document(root: Path, *, ex_date: str = "2026-09-09", adjustment: float = 0.1974617, source_hash: str | None = None) -> Path:
    source = root / "raw/official_disclosures/run/cninfo/300196.SZ/notice.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF-formal-source")
    payload = {
        "schema": SCHEMA,
        "records": [{
            "symbol": "300196.SZ", "record_date": "2026-09-08", "ex_dividend_date": ex_date,
            "distribution_type": "CASH_DIVIDEND", "formula": FORMULA,
            "effective_cash_dividend_per_share": adjustment, "source": SOURCE,
            "source_hash": source_hash or hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_relative_path": source.relative_to(root).as_posix(),
            "source_url": "https://static.cninfo.com.cn/finalpage/2026-09-02/notice.PDF",
            "announcement_id": "notice", "calculation_provenance": "OFFICIAL_TREASURY_SHARE_DILUTED_REFERENCE_ADJUSTMENT",
        }],
    }
    path = root / "staging/reference_price_evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _bar(day: str, close: float) -> dict[str, object]:
    return {"symbol": "300196.SZ", "trade_date": day, "close": close}


def _fact(preclose: float) -> dict[str, object]:
    return {"symbol": "300196.SZ", "trade_date": "2026-09-09", "preclose": preclose, "pct_chg": 0.0,
            "turnover_rate": 1.0, "trade_status": "TRADING", "is_st": "FALSE"}


def test_official_differential_dividend_adjustment_reconciles_without_using_nominal_dividend(tmp_path):
    evidence = load_reference_price_evidence(tmp_path, _document(tmp_path))
    facts = reconcile([_fact(16.70)], [_bar("2026-09-08", 16.90), _bar("2026-09-09", 16.70)], reference_price_evidence=evidence)
    assert facts[0]["preclose_reconciliation"] == "MATCH_REFERENCE_PRICE_EXCEPTION"
    assert facts[0]["quality_status"] == "PASS"
    assert facts[0]["preclose_quality"] == "PASS_REFERENCE_PRICE_EXCEPTION"
    assert facts[0]["reference_price_announcement_id"] == "notice"
    assert facts[0]["reference_price_expected"] == pytest.approx(16.7025383)


def test_ordinary_continuity_remains_a_normal_match_without_exception_evidence():
    facts = reconcile([_fact(16.90)], [_bar("2026-09-08", 16.90), _bar("2026-09-09", 16.90)])
    assert facts[0]["preclose_reconciliation"] == "MATCH"


def test_wrong_ex_date_or_missing_evidence_cannot_resolve_a_mismatch(tmp_path):
    wrong_date = load_reference_price_evidence(tmp_path, _document(tmp_path, ex_date="2026-09-10"))
    for evidence in (wrong_date, {}):
        facts = reconcile([_fact(16.70)], [_bar("2026-09-08", 16.90), _bar("2026-09-09", 16.70)], reference_price_evidence=evidence)
        assert facts[0]["preclose_reconciliation"] == "MISMATCH"
        assert facts[0]["quality_status"] == "UNRESOLVED"


def test_bad_official_source_hash_fails_closed(tmp_path):
    with pytest.raises(ReferencePriceEvidenceError, match="SOURCE_HASH_MISMATCH"):
        load_reference_price_evidence(tmp_path, _document(tmp_path, source_hash="0" * 64))


def test_reference_price_mismatch_remains_unresolved_even_with_verified_evidence(tmp_path):
    evidence = load_reference_price_evidence(tmp_path, _document(tmp_path))
    facts = reconcile([_fact(16.71)], [_bar("2026-09-08", 16.90), _bar("2026-09-09", 16.71)], reference_price_evidence=evidence)
    assert facts[0]["preclose_reconciliation"] == "MISMATCH"
