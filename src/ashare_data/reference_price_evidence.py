"""Exact-date official reference-price evidence for bounded Daily Facts gates.

This is intentionally a small verifier, not a corporate-action framework.  It
accepts only official, hash-bound cash-dividend evidence and exposes a
deterministic expected exchange reference price for a specific symbol/date.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


SCHEMA = "ASL_DAILY_FACTS_REFERENCE_PRICE_EVIDENCE_V01"
SOURCE = "CNINFO_OFFICIAL_DISCLOSURE"
FORMULA = "CASH_DIVIDEND_SUBTRACTION_V01"


class ReferencePriceEvidenceError(RuntimeError):
    """Stable error for unusable official reference-price evidence."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any, field: str) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ReferencePriceEvidenceError(f"REFERENCE_PRICE_EVIDENCE_INVALID_{field}") from exc
    if not math.isfinite(value):
        raise ReferencePriceEvidenceError(f"REFERENCE_PRICE_EVIDENCE_INVALID_{field}")
    return value


@dataclass(frozen=True)
class CashDividendReferenceEvidence:
    symbol: str
    record_date: str
    ex_dividend_date: str
    effective_cash_dividend_per_share: float
    source_hash: str
    source_relative_path: str
    source_url: str
    announcement_id: str
    provenance: str

    def expected_reference_price(self, previous_close: float) -> float:
        return previous_close - self.effective_cash_dividend_per_share


def _record(root: Path, value: dict[str, Any]) -> CashDividendReferenceEvidence:
    required = {
        "symbol", "record_date", "ex_dividend_date", "distribution_type", "formula",
        "effective_cash_dividend_per_share", "source", "source_hash", "source_relative_path",
        "source_url", "announcement_id", "calculation_provenance",
    }
    if not required <= set(value):
        raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_REQUIRED_FIELD_MISSING")
    if value["source"] != SOURCE or value["distribution_type"] != "CASH_DIVIDEND" or value["formula"] != FORMULA:
        raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_UNSUPPORTED_CONTRACT")
    try:
        date.fromisoformat(str(value["record_date"]))
        date.fromisoformat(str(value["ex_dividend_date"]))
    except ValueError as exc:
        raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_DATE_INVALID") from exc
    source_path = root / str(value["source_relative_path"])
    if not source_path.is_file() or _sha256(source_path) != value["source_hash"]:
        raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_SOURCE_HASH_MISMATCH")
    source_url = str(value["source_url"])
    if not source_url.startswith("https://static.cninfo.com.cn/"):
        raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_SOURCE_NOT_OFFICIAL")
    adjustment = _number(value["effective_cash_dividend_per_share"], "ADJUSTMENT")
    if adjustment < 0:
        raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_INVALID_ADJUSTMENT")
    return CashDividendReferenceEvidence(
        symbol=str(value["symbol"]), record_date=str(value["record_date"]), ex_dividend_date=str(value["ex_dividend_date"]),
        effective_cash_dividend_per_share=adjustment, source_hash=str(value["source_hash"]),
        source_relative_path=str(value["source_relative_path"]), source_url=source_url,
        announcement_id=str(value["announcement_id"]), provenance=str(value["calculation_provenance"]),
    )


def load_reference_price_evidence(root: Path, path: Path) -> dict[tuple[str, str], CashDividendReferenceEvidence]:
    """Load evidence only when every record and its official PDF verify."""
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_UNREADABLE") from exc
    if document.get("schema") != SCHEMA or not isinstance(document.get("records"), list):
        raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_SCHEMA_INVALID")
    output: dict[tuple[str, str], CashDividendReferenceEvidence] = {}
    for value in document["records"]:
        if not isinstance(value, dict):
            raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_SCHEMA_INVALID")
        item = _record(root, value)
        key = (item.symbol, item.ex_dividend_date)
        if key in output:
            raise ReferencePriceEvidenceError("REFERENCE_PRICE_EVIDENCE_DUPLICATE_KEY")
        output[key] = item
    return output
