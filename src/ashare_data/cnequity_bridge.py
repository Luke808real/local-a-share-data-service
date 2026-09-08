"""Thin CNEquity acquisition bridge for ASL Daily Facts evidence.

ASL owns fact normalization/certification only.  Provider login, watchdogs,
socket timeout, retry, pacing and relogin are delegated to the exact pinned
CNEquity BaoStock session implementation.
"""
from __future__ import annotations

import importlib.metadata
from datetime import date, datetime, timezone
from typing import Any

from cnequity.adapters.baostock._session import fetch_per_symbol, to_baostock_symbol

from ashare_data.daily_facts_phase1 import (
    DailyFactsError, FROZEN_BAOSTOCK_RUNTIME_VERSION, PROVIDER_FIELDS, ProviderRawRow,
)


def _year_windows(start: date, end: date) -> list[tuple[date, date]]:
    return [(max(start, date(year, 1, 1)), min(end, date(year, 12, 31)))
            for year in range(start.year, end.year + 1)]


class CNEquityBaoStockDailyFactsBridge:
    """Current-contract fields through CNEquity's owned BaoStock session."""

    def __init__(self, *, config: Any | None = None) -> None:
        self.config = config
        self.provider_version = f"baostock-{FROZEN_BAOSTOCK_RUNTIME_VERSION}"

    def __enter__(self) -> "CNEquityBaoStockDailyFactsBridge":
        try:
            installed = importlib.metadata.version("baostock")
        except importlib.metadata.PackageNotFoundError as exc:
            raise DailyFactsError("SOURCE_ERROR", "baostock distribution metadata is unavailable") from exc
        if installed != FROZEN_BAOSTOCK_RUNTIME_VERSION:
            raise DailyFactsError("PROVIDER_VERSION_MISMATCH", f"expected baostock {FROZEN_BAOSTOCK_RUNTIME_VERSION}, got {installed}")
        self.provider_version = f"baostock-{installed}"
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def fetch(self, symbol: str, start: date, end: date) -> list[ProviderRawRow]:
        expected_code = to_baostock_symbol(symbol)

        def fetch_one(bs: Any, _symbol: str, window_start: date, window_end: date) -> list[dict[str, Any]] | None:
            output: list[dict[str, Any]] = []
            for bounded_start, bounded_end in _year_windows(window_start, window_end):
                try:
                    result = bs.query_history_k_data_plus(
                        expected_code, ",".join(PROVIDER_FIELDS), start_date=bounded_start.isoformat(),
                        end_date=bounded_end.isoformat(), frequency="d", adjustflag="3",
                    )
                except Exception:
                    return None
                if str(getattr(result, "error_code", "")) != "0" or tuple(getattr(result, "fields", ())) != PROVIDER_FIELDS:
                    return None
                while result.next():
                    values = result.get_row_data()
                    if not isinstance(values, (list, tuple)) or len(values) != len(PROVIDER_FIELDS):
                        return None
                    raw = {name: str(value) for name, value in zip(PROVIDER_FIELDS, values)}
                    try:
                        trade_date = date.fromisoformat(raw["date"])
                    except ValueError:
                        return None
                    if raw["code"].strip().lower() != expected_code or not bounded_start <= trade_date <= bounded_end:
                        return None
                    output.append({"trade_date": trade_date, "raw": raw})
            return output

        rows, failed = fetch_per_symbol([symbol], start, end, fetch_one, config=self.config, label="ASL daily facts")
        if failed:
            raise DailyFactsError("SOURCE_ERROR", f"CNEquity BaoStock failed: {symbol}")
        fetched_at = datetime.now(timezone.utc).isoformat()
        raw_rows = [ProviderRawRow(symbol, row["trade_date"], row["raw"], fetched_at, self.provider_version) for row in rows]
        if len({(row.symbol, row.trade_date) for row in raw_rows}) != len(raw_rows):
            raise DailyFactsError("DUPLICATE_PROVIDER_ROW", "duplicate BaoStock primary key")
        return raw_rows
