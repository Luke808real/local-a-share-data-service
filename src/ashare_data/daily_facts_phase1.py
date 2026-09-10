"""Bounded Daily Facts Phase 1 ingestion and certification primitives.

This module is intentionally outside the read-only query path.  It accepts
BaoStock evidence only through a narrow, typed contract and never changes R3
daily files or their publication authority.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING, Any, Iterable, Mapping

if TYPE_CHECKING:
    from ashare_data.reference_price_evidence import CashDividendReferenceEvidence


SCHEMA = "ASL_DAILY_FACTS_PHASE1_V01"
# The R4A frozen contract makes BaoStock's unadjusted historical ``preclose``
# the canonical preclose evidence.  Other Phase 1 fields share the same row,
# but must not obscure the preclose source identity in provenance.
PROVIDER = "BAOSTOCK_HISTORY_K_PRECLOSE"
FROZEN_BAOSTOCK_RUNTIME_VERSION = "0.9.3"
PROVIDER_FIELDS = ("date", "code", "preclose", "pctChg", "turn", "tradestatus", "isST")
CANONICAL_FACT_FIELDS = ("preclose", "pct_chg", "turnover_rate", "trade_status", "is_st")


class DailyFactsError(RuntimeError):
    """Stable error code for a provider or certification failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def baostock_code(symbol: str) -> str:
    code, separator, exchange = symbol.partition(".")
    if separator != "." or exchange not in {"SH", "SZ"} or len(code) != 6 or not code.isdigit():
        raise DailyFactsError("INVALID_SYMBOL", "expected canonical SH/SZ symbol")
    return f"{'sh' if exchange == 'SH' else 'sz'}.{code}"


def _number(value: Any, field: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise DailyFactsError("MALFORMED_PROVIDER_FIELD", f"invalid {field}") from exc
    if not math.isfinite(parsed):
        raise DailyFactsError("MALFORMED_PROVIDER_FIELD", f"invalid {field}")
    return parsed


def _date(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise DailyFactsError("MALFORMED_PROVIDER_FIELD", "invalid provider date") from exc


def _display_equal(left: float, right: float) -> bool:
    try:
        quant = Decimal("0.01")
        return Decimal(str(left)).quantize(quant, rounding=ROUND_HALF_UP) == Decimal(str(right)).quantize(quant, rounding=ROUND_HALF_UP)
    except Exception:
        return False


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ProviderRawRow:
    symbol: str
    trade_date: date
    raw: dict[str, str]
    fetched_at: str
    provider_version: str


def normalize(raw_rows: Iterable[ProviderRawRow]) -> list[dict[str, Any]]:
    """Map BaoStock raw values to tri-state canonical facts without defaults."""
    normalized: list[dict[str, Any]] = []
    for item in raw_rows:
        raw = item.raw
        status_code = raw["tradestatus"].strip()
        trade_status = {"1": "TRADING", "0": "SUSPENDED"}.get(status_code, "UNKNOWN")
        st_code = raw["isST"].strip()
        is_st = {"1": "TRUE", "0": "FALSE"}.get(st_code, "UNKNOWN")
        normalized.append({
            "symbol": item.symbol, "trade_date": item.trade_date.isoformat(),
            "preclose": _number(raw["preclose"], "preclose"),
            "pct_chg": _number(raw["pctChg"], "pctChg"),
            # BaoStock's `turn` is documented and frozen here as percentage points.
            "turnover_rate": _number(raw["turn"], "turn"),
            "turnover_unit": "PERCENT", "trade_status": trade_status, "is_st": is_st,
            "provider": PROVIDER, "provider_code": raw["code"], "provider_tradestatus": status_code,
            "provider_is_st": st_code, "raw_values": json.dumps(raw, sort_keys=True, separators=(",", ":")),
            "fetched_at": item.fetched_at, "provider_version": item.provider_version,
            "schema_version": SCHEMA,
        })
    return normalized


def reconcile(rows: list[dict[str, Any]], daily_rows: Iterable[dict[str, Any]], *, corporate_action_year_symbols: set[str] | None = None,
              reference_price_evidence: Mapping[tuple[str, str], "CashDividendReferenceEvidence"] | None = None) -> list[dict[str, Any]]:
    """Attach independent TDX comparison results; no provider value is overwritten."""
    daily_rows = list(daily_rows)
    corporate_action_year_symbols = corporate_action_year_symbols or set()
    reference_price_evidence = reference_price_evidence or {}
    bars = {(str(r["symbol"]), str(r["trade_date"])): r for r in daily_rows}
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for bar in daily_rows:
        by_symbol.setdefault(str(bar["symbol"]), []).append(bar)
    for values in by_symbol.values():
        values.sort(key=lambda r: str(r["trade_date"]))
    previous: dict[tuple[str, str], float] = {}
    previous_dates: dict[tuple[str, str], str] = {}
    for symbol, values in by_symbol.items():
        last: float | None = None
        last_date: str | None = None
        for bar in values:
            key = (symbol, str(bar["trade_date"]))
            if last is not None and last_date is not None:
                previous[key] = last
                previous_dates[key] = last_date
            value = bar.get("close")
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                last = float(value)
                last_date = str(bar["trade_date"])

    for row in rows:
        key = (row["symbol"], row["trade_date"])
        bar = bars.get(key)
        if row["trade_status"] == "UNKNOWN" or row["is_st"] == "UNKNOWN":
            row["preclose_reconciliation"] = "UNKNOWN"
            row["pct_chg_reconciliation"] = "UNKNOWN"
            row["quality_status"] = "UNKNOWN"
            continue
        if row["trade_status"] == "SUSPENDED":
            row["preclose_reconciliation"] = "NOT_COMPARABLE"
            row["pct_chg_reconciliation"] = "NOT_COMPARABLE"
            row["quality_status"] = "PASS"
            continue
        if bar is None or row["preclose"] is None or row["pct_chg"] is None or row["turnover_rate"] is None:
            row["preclose_reconciliation"] = "UNKNOWN"
            row["pct_chg_reconciliation"] = "UNKNOWN"
            row["quality_status"] = "UNKNOWN"
            continue
        prior = previous.get(key)
        if prior is None:
            row["preclose_reconciliation"] = "NOT_COMPARABLE"
        elif _display_equal(float(row["preclose"]), prior):
            row["preclose_reconciliation"] = "MATCH"
        elif (evidence := reference_price_evidence.get(key)) is not None:
            if evidence.record_date != previous_dates.get(key):
                row["preclose_reconciliation"] = "MISMATCH"
            else:
                expected = evidence.expected_reference_price(prior)
                row["reference_price_expected"] = expected
                row["reference_price_provenance"] = evidence.provenance
                row["reference_price_source_hash"] = evidence.source_hash
                row["reference_price_source_url"] = evidence.source_url
                row["reference_price_announcement_id"] = evidence.announcement_id
                row["preclose_reconciliation"] = "MATCH_REFERENCE_PRICE_EXCEPTION" if _display_equal(float(row["preclose"]), expected) else "MISMATCH"
        elif row["symbol"] in corporate_action_year_symbols:
            # Existing local corporate_actions only holds a year, not an ex-date.
            row["preclose_reconciliation"] = "UNRESOLVED"
        else:
            row["preclose_reconciliation"] = "MISMATCH"
        close = bar.get("close")
        if not isinstance(close, (int, float)) or float(row["preclose"]) <= 0:
            row["pct_chg_reconciliation"] = "UNKNOWN"
        else:
            calculated = (float(close) / float(row["preclose"]) - 1.0) * 100.0
            row["pct_chg_calculated"] = calculated
            row["pct_chg_reconciliation"] = "MATCH" if abs(calculated - float(row["pct_chg"])) <= 0.02 else "MISMATCH"
        row["quality_status"] = "PASS" if row["preclose_reconciliation"] in {"MATCH", "MATCH_REFERENCE_PRICE_EXCEPTION", "NOT_COMPARABLE"} and row["pct_chg_reconciliation"] == "MATCH" else "UNRESOLVED"
        row["preclose_quality"] = (
            "PASS_NORMAL_CONTINUITY" if row["preclose_reconciliation"] == "MATCH"
            else "PASS_REFERENCE_PRICE_EXCEPTION" if row["preclose_reconciliation"] == "MATCH_REFERENCE_PRICE_EXCEPTION"
            else "NOT_COMPARABLE" if row["preclose_reconciliation"] == "NOT_COMPARABLE"
            else "UNRESOLVED"
        )
    return rows


def quality_receipt(rows: list[dict[str, Any]], *, requested_symbols: list[str], start: date, end: date) -> dict[str, Any]:
    keys = [(r["symbol"], r["trade_date"]) for r in rows]
    counters = {
        "DUPLICATE_N": len(keys) - len(set(keys)),
        "UNKNOWN_N": sum(r["quality_status"] == "UNKNOWN" for r in rows),
        "UNRESOLVED_N": sum(r["quality_status"] == "UNRESOLVED" for r in rows),
        "SOURCE_ERROR_N": 0,
        "PROVENANCE_FAILURE_N": sum(not all(r.get(k) for k in ("provider", "raw_values", "fetched_at", "provider_version", "schema_version")) for r in rows),
    }
    structural = counters["DUPLICATE_N"] == 0 and all(r["schema_version"] == SCHEMA for r in rows)
    coverage = all(any(r["symbol"] == symbol for r in rows) for symbol in requested_symbols)
    provenance = counters["PROVENANCE_FAILURE_N"] == 0
    passed = structural and coverage and provenance and counters["UNKNOWN_N"] == 0 and counters["UNRESOLVED_N"] == 0
    return {"schema": "ASL_DAILY_FACTS_PHASE1_QUALITY_V01", "scope": "VERTICAL_SLICE", "start": start.isoformat(), "end": end.isoformat(), "requested_symbols": sorted(requested_symbols), "row_n": len(rows), "quality": counters, "STRUCTURAL_PASS": structural, "COVERAGE_PASS": coverage, "PROVENANCE_PASS": provenance, "PASS": passed, "rows_hash": _sha(rows)}
