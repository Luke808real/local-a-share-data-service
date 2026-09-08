"""Fail-closed structural audit helpers for a one-day CNEquity 5m tip.

These helpers produce review evidence only.  They never confer publication
authority and deliberately distinguish incomplete symbols from malformed rows.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, time
from math import isfinite
from typing import Any, Iterable


SESSION_END_TIMES = frozenset(
    [time(9, minute) for minute in range(35, 60, 5)]
    + [time(10, minute) for minute in range(0, 60, 5)]
    + [time(11, minute) for minute in range(0, 31, 5)]
    + [time(13, minute) for minute in range(5, 60, 5)]
    + [time(14, minute) for minute in range(0, 60, 5)]
    + [time(15, 0)]
)
EXPECTED_BARS_PER_NORMAL_SESSION = len(SESSION_END_TIMES)


def audit_rows(rows: Iterable[dict[str, Any]], *, trade_date: date) -> dict[str, Any]:
    """Return deterministic structural counts for rows from one 5m partition."""
    keys: set[tuple[str, str]] = set()
    duplicate_n = invalid_scope_n = invalid_frequency_n = invalid_session_time_n = 0
    mock_row_n = invalid_ohlc_n = invalid_activity_n = invalid_provenance_n = 0
    per_symbol: dict[str, int] = defaultdict(int)
    source_values: set[str] = set()
    version_values: set[str] = set()
    for row in rows:
        symbol = str(row.get("symbol", ""))
        timestamp = str(row.get("bar_time", ""))
        key = (symbol, timestamp)
        if key in keys:
            duplicate_n += 1
        keys.add(key)
        per_symbol[symbol] += 1
        if str(row.get("trade_date")) != trade_date.isoformat():
            invalid_scope_n += 1
        if row.get("frequency") != "5m":
            invalid_frequency_n += 1
        try:
            bar_time = row["bar_time"].time()
        except (AttributeError, KeyError, TypeError):
            invalid_session_time_n += 1
        else:
            if bar_time not in SESSION_END_TIMES:
                invalid_session_time_n += 1
        source = str(row.get("source", ""))
        version = str(row.get("data_version", ""))
        source_values.add(source)
        version_values.add(version)
        if not source or source.lower() in {"mock", "unknown", "fabricated"}:
            mock_row_n += 1
        if not version:
            invalid_provenance_n += 1
        try:
            open_, high, low, close = (float(row[name]) for name in ("open", "high", "low", "close"))
            volume, amount = float(row["volume"]), float(row["amount"])
        except (KeyError, TypeError, ValueError):
            invalid_ohlc_n += 1
            invalid_activity_n += 1
            continue
        if not all(isfinite(value) for value in (open_, high, low, close)) or min(open_, high, low, close) <= 0:
            invalid_ohlc_n += 1
        elif high < max(open_, close, low) or low > min(open_, close, high):
            invalid_ohlc_n += 1
        if not all(isfinite(value) for value in (volume, amount)) or volume < 0 or amount < 0:
            invalid_activity_n += 1
    counts = Counter(per_symbol.values())
    return {
        "row_n": sum(per_symbol.values()), "symbol_n": len(per_symbol),
        "duplicate_pk_n": duplicate_n, "invalid_scope_n": invalid_scope_n,
        "invalid_frequency_n": invalid_frequency_n, "invalid_session_time_n": invalid_session_time_n,
        "mock_row_n": mock_row_n, "invalid_provenance_n": invalid_provenance_n,
        "invalid_ohlc_n": invalid_ohlc_n, "invalid_activity_n": invalid_activity_n,
        "bar_count_48_symbol_n": counts[EXPECTED_BARS_PER_NORMAL_SESSION],
        "bar_count_lt48_symbol_n": sum(n for bars, n in counts.items() if bars < EXPECTED_BARS_PER_NORMAL_SESSION),
        "bar_count_gt48_symbol_n": sum(n for bars, n in counts.items() if bars > EXPECTED_BARS_PER_NORMAL_SESSION),
        "source_values": sorted(source_values), "data_version_values": sorted(version_values),
    }
