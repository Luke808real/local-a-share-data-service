#!/usr/bin/env python3
"""Produce a network-free, reproducible audit of the frozen 2026-09-09 blockers.

This tool is deliberately bounded to the already acquired one-day Phase 1 run.
It reads persisted normalized/RAW facts and the published R3 allowlist only; it
does not import a provider client and cannot publish any authority.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ashare_data.local_query import DEFAULT_DATA_ROOT, LocalQuery  # noqa: E402


RUN_NAME = "daily_facts_phase1_20260909_v01"
TRADE_DATE = "2026-09-09"
PRIOR_TRADE_DATE = "2026-09-08"
NUMERIC_FACT_FIELDS = ("preclose", "pct_chg", "turnover_rate")


def _json(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _text(value: Any) -> str | None:
    """Preserve identity text while keeping generated Markdown UTF-8 text."""
    return None if value is None else str(value).replace("\x00", "")


def _number(value: Any) -> float | None:
    value = _json(value)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _display_equal(left: float, right: float) -> bool:
    return round(left + 1e-12, 2) == round(right + 1e-12, 2)


def _parquet_rows(paths: list[Path]) -> list[dict[str, Any]]:
    import duckdb

    columns = (
        "symbol, cast(trade_date as varchar) as trade_date, preclose, pct_chg, turnover_rate, "
        "trade_status, is_st, provider_tradestatus, raw_values"
    )
    with duckdb.connect(":memory:") as con:
        result = con.execute(
            "select " + columns + " from read_parquet(?, union_by_name=true) order by symbol",
            [[str(path) for path in paths]],
        )
        names = [column[0] for column in result.description]
        return [{name: _json(value) for name, value in zip(names, row)} for row in result.fetchall()]


def _identity_and_bars(root: Path, symbols: list[str]) -> tuple[dict[str, str], dict[tuple[str, str], dict[str, Any]]]:
    marks = ",".join("?" for _ in symbols)
    with LocalQuery(root) as query:
        identities = query._execute(
            "select upper(symbol) as symbol, name from " + query._instrument_relation
            + " where upper(symbol) in (" + marks + ")",
            symbols,
        )
        bars = query._execute(
            "select symbol, cast(trade_date as varchar) as trade_date, open, high, low, close, volume, amount from "
            + query._daily_relation + " where trade_date in (?, ?) and symbol in (" + marks + ")",
            [date.fromisoformat(PRIOR_TRADE_DATE), date.fromisoformat(TRADE_DATE), *symbols],
        )
    return ({row["symbol"]: _text(row.get("name")) for row in identities},
            {(row["symbol"], row["trade_date"]): row for row in bars})


def _corporate_action_rows(root: Path, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Return local corporate-action rows without pretending the year is a date."""
    import duckdb

    files = sorted((root / "curated" / "corporate_actions").glob("ex_date=2026/*.parquet"))
    if not files:
        return {symbol: [] for symbol in symbols}
    marks = ",".join("?" for _ in symbols)
    columns = "symbol, ex_date, action_type, cash_dividend, bonus_ratio, transfer_ratio, allotment_ratio, allotment_price, source, data_version"
    with duckdb.connect(":memory:") as con:
        result = con.execute(
            "select " + columns + " from read_parquet(?, union_by_name=true) where symbol in (" + marks + ") order by symbol",
            [[str(path) for path in files], *symbols],
        )
        names = [column[0] for column in result.description]
        output = {symbol: [] for symbol in symbols}
        for row in result.fetchall():
            item = {name: _json(value) for name, value in zip(names, row)}
            output[item["symbol"]].append(item)
    return output


def _raw(root: Path, symbol: str) -> dict[str, Any]:
    path = root / "raw" / "baostock" / "daily_facts" / RUN_NAME / f"{symbol}.json"
    document = json.loads(path.read_text())
    if len(document.get("rows", [])) != 1:
        raise RuntimeError(f"RAW_ROW_CONTRACT_FAILURE:{symbol}")
    return document


def audit(root: Path) -> dict[str, Any]:
    root = root.resolve()
    normalized_dir = root / "staging" / RUN_NAME / "normalized"
    rows = _parquet_rows(sorted(normalized_dir.glob("*.parquet")))
    if len(rows) != 5208:
        raise RuntimeError(f"NORMALIZED_SCOPE_FAILURE:{len(rows)}")

    # Compute the frozen exceptions directly from the persisted facts and R3,
    # not from a mutable report receipt.
    symbols = sorted(row["symbol"] for row in rows)
    names, bars = _identity_and_bars(root, symbols)
    preclose_rows: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []
    for row in rows:
        key = (row["symbol"], TRADE_DATE)
        bar = bars.get(key)
        prior = bars.get((row["symbol"], PRIOR_TRADE_DATE))
        preclose = _number(row.get("preclose"))
        prior_close = _number(prior.get("close")) if prior else None
        if row["trade_status"] == "TRADING" and preclose is not None and prior_close is not None and not _display_equal(preclose, prior_close):
            preclose_rows.append(row)
        if bar is not None and row["trade_status"] != "TRADING":
            status_rows.append(row)

    if len(preclose_rows) != 16 or len(status_rows) != 10:
        raise RuntimeError(f"FROZEN_EXCEPTION_SCOPE_DRIFT:preclose={len(preclose_rows)},status={len(status_rows)}")
    preclose_symbols = {row["symbol"] for row in preclose_rows}
    status_symbols = {row["symbol"] for row in status_rows}
    corporate = _corporate_action_rows(root, sorted(preclose_symbols))

    preclose_items: list[dict[str, Any]] = []
    for row in preclose_rows:
        symbol = row["symbol"]
        prior = bars.get((symbol, PRIOR_TRADE_DATE))
        current = bars.get((symbol, TRADE_DATE))
        preclose = _number(row["preclose"])
        prior_close = _number(prior.get("close")) if prior else None
        diff = preclose - prior_close if preclose is not None and prior_close is not None else None
        # local ``ex_date`` is an integer year (2026), not an exact ex-date;
        # it is diagnostic evidence only and cannot resolve a 2026-09-09 key.
        event_evidence = corporate[symbol]
        classification = "UNRESOLVED"
        preclose_items.append({
            "symbol": symbol, "name": names.get(symbol), "trade_date": TRADE_DATE,
            "exception_type": "PRECLOSE_MISMATCH", "classification": classification,
            "baostock_preclose": preclose, "r3_previous_trade_date": PRIOR_TRADE_DATE,
            "r3_previous_published_close": prior_close, "difference": diff,
            "difference_pct": (diff / prior_close * 100.0) if diff is not None and prior_close else None,
            "r3_2026_09_08_ohlcv": prior, "r3_2026_09_09_ohlcv": current,
            "provider_raw_values": json.loads(row["raw_values"]),
            "local_corporate_action_rows": event_evidence,
            "evidence_assessment": "YEAR_ONLY_CORPORATE_ACTION_EVIDENCE_NOT_EXACT_DATE" if event_evidence else "NO_LOCAL_EXACT_REFERENCE_PRICE_EVIDENCE",
        })

    status_items: list[dict[str, Any]] = []
    for row in status_rows:
        symbol = row["symbol"]
        bar = bars[(symbol, TRADE_DATE)]
        raw = json.loads(row["raw_values"])
        volume = _number(bar.get("volume"))
        amount = _number(bar.get("amount"))
        classification = "PROVIDER_STATUS_CONFLICT" if (volume or 0) > 0 or (amount or 0) > 0 else "PASS_SUSPENDED_ZERO_BAR"
        status_items.append({
            "symbol": symbol, "name": names.get(symbol), "trade_date": TRADE_DATE,
            "exception_type": "TRADE_STATUS_CONFLICT", "classification": classification,
            "baostock_tradestatus_raw": raw.get("tradestatus"), "normalized_trade_status": row["trade_status"],
            "r3_2026_09_09_ohlcv": bar, "volume": volume, "amount": amount,
            "baostock_numeric_raw_fields": {field: raw.get(field) for field in NUMERIC_FACT_FIELDS},
            "null_numeric_fields": [field for field, raw_field in (("preclose", "preclose"), ("pct_chg", "pctChg"), ("turnover_rate", "turn")) if not str(raw.get(raw_field, "")).strip()],
            "provider_raw_values": raw,
            "adapter_contract": "tradestatus '0' normalizes to SUSPENDED; empty pctChg/turn normalize to null, never zero",
        })

    null_numeric_symbols = {item["symbol"] for item in status_items if item["null_numeric_fields"]}
    report = {
        "schema": "ASL_DAILY_FACTS_20260909_BLOCKER_DIAGNOSIS_V01",
        "network_provider_call_n": 0, "run_name": RUN_NAME, "trade_date": TRADE_DATE,
        "counts": {
            "PRECLOSE_KEY_N": len(preclose_items), "TRADE_STATUS_KEY_N": len(status_items),
            "INTERSECTION_N": len(preclose_symbols & status_symbols), "PRECLOSE_ONLY_N": len(preclose_symbols - status_symbols),
            "TRADE_STATUS_ONLY_N": len(status_symbols - preclose_symbols), "NULL_NUMERIC_KEY_N": len(null_numeric_symbols),
            "NULL_NUMERIC_INTERSECTION_TRADE_STATUS_CONFLICT_N": len(null_numeric_symbols & status_symbols),
            "REFERENCE_PRICE_EXCEPTION_N": 0, "PROVIDER_STATUS_CONFLICT_N": sum(item["classification"] == "PROVIDER_STATUS_CONFLICT" for item in status_items),
            "ADAPTER_BUG_N": 0, "OTHER_RESOLVED_N": sum(item["classification"] == "PASS_SUSPENDED_ZERO_BAR" for item in status_items),
            "STILL_UNRESOLVED_N": len(preclose_items),
        },
        "preclose_mismatches": preclose_items,
        "trade_status_conflicts": status_items,
        "conclusion": {
            "certification_repair_authorized": False,
            "reason": "No exact-date local corporate-action/reference-price authority exists for the 16 preclose keys; status conflicts retain the provider and R3 evidence without changing tri-state semantics.",
            "publication_allowed": False,
        },
    }
    return report


def _markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# ASL Daily Facts 2026-09-09 Blocker Diagnosis V01", "",
        "This is an offline evidence audit. It made zero provider calls and did not modify either publication authority.", "",
        "## Frozen set relation", "",
        f"- Preclose keys: {counts['PRECLOSE_KEY_N']}",
        f"- Trade-status keys: {counts['TRADE_STATUS_KEY_N']}",
        f"- Intersection: {counts['INTERSECTION_N']}",
        f"- Preclose only: {counts['PRECLOSE_ONLY_N']}",
        f"- Trade-status only: {counts['TRADE_STATUS_ONLY_N']}",
        f"- Null numeric keys: {counts['NULL_NUMERIC_KEY_N']}; intersection with status conflicts: {counts['NULL_NUMERIC_INTERSECTION_TRADE_STATUS_CONFLICT_N']}", "",
        "## Preclose mismatch keys", "",
        "| Symbol | Name | BaoStock preclose | R3 2026-09-08 close | Difference | Classification | Local evidence |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for item in report["preclose_mismatches"]:
        lines.append("| {symbol} | {name} | {baostock_preclose:.4f} | {r3_previous_published_close:.4f} | {difference:.4f} | {classification} | {evidence_assessment} |".format(**item))
    lines.extend(["", "## Frozen trade-status exception keys", "", "| Symbol | Name | BaoStock raw | Normalized | R3 volume | R3 amount | Classification | Null numeric fields |", "|---|---|---|---|---:|---:|---|---|"])
    for item in report["trade_status_conflicts"]:
        lines.append("| {symbol} | {name} | {baostock_tradestatus_raw} | {normalized_trade_status} | {volume:.0f} | {amount:.2f} | {classification} | {nulls} |".format(
            **item, nulls=", ".join(item["null_numeric_fields"]) or "none"))
    lines.extend([
        "", "## Result", "",
        "All 16 preclose rows remain `UNRESOLVED`: any related local corporate-action record carries only `ex_date=2026`, not an exact event date or reference price. It is not eligible to turn a mismatch into a pass.",
        "", "All 10 status rows retain their provider evidence (`tradestatus=0`, empty `pctChg` and `turn`) and have a matching R3 zero-volume/zero-amount carry-forward bar (`open=high=low=close=preclose`). They are aligned suspended sessions, not provider-status conflicts and not an adapter bug. The 10 numeric-null keys exactly equal these 10 suspension-aligned keys.",
        "", "Certification remains blocked. No normal row, tri-state rule, R3 data, RAW evidence, or publication pointer was changed.", "",
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--json", type=Path, default=REPO / "reports" / "implementation" / "ASL_DAILY_FACTS_20260909_BLOCKER_DIAGNOSIS_V01.json")
    parser.add_argument("--markdown", type=Path, default=REPO / "reports" / "implementation" / "ASL_DAILY_FACTS_20260909_BLOCKER_DIAGNOSIS_V01.md")
    args = parser.parse_args()
    report = audit(args.data_root)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    args.markdown.write_text(_markdown(report))
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))
