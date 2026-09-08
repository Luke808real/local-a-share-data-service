"""Thin CNEquity acquisition bridge for ASL Daily Facts evidence.

ASL owns fact normalization/certification only.  Provider login, watchdogs,
socket timeout, retry, pacing and relogin are delegated to the exact pinned
CNEquity BaoStock session implementation.
"""
from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from cnequity.adapters.baostock._session import fetch_per_symbol, to_baostock_symbol

from ashare_data.daily_facts_phase1 import (
    DailyFactsError, FROZEN_BAOSTOCK_RUNTIME_VERSION, PROVIDER_FIELDS, ProviderRawRow,
)


def _year_windows(start: date, end: date) -> list[tuple[date, date]]:
    return [(max(start, date(year, 1, 1)), min(end, date(year, 12, 31)))
            for year in range(start.year, end.year + 1)]


@dataclass(frozen=True)
class DailyFactsRequest:
    symbol: str
    required_start: date
    required_end: date


class CNEquityBaoStockDailyFactsBridge:
    """Current-contract fields through CNEquity's owned BaoStock session."""

    def __init__(self, *, config: Any, deadline: float = 300.0) -> None:
        if config is None:
            raise DailyFactsError("INVALID_CNEQUITY_CONFIG", "CNEquity config is required for provider pacing")
        if deadline < 300.0:
            raise DailyFactsError("INVALID_PROVIDER_DEADLINE", "annual history requires a >=300 second CNEquity deadline")
        self.config = config
        self.deadline = deadline
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
        result, failed = self.fetch_batch([DailyFactsRequest(symbol, start, end)])
        if failed:
            raise DailyFactsError("SOURCE_ERROR", f"CNEquity BaoStock failed: {symbol}")
        return result[symbol]

    def fetch_batch(self, requests: list[DailyFactsRequest]) -> tuple[dict[str, list[ProviderRawRow]], tuple[str, ...]]:
        """Fetch a checkpoint batch in one CNEquity-managed BaoStock session."""
        if not requests:
            return {}, ()
        by_symbol = {request.symbol: request for request in requests}
        if len(by_symbol) != len(requests):
            raise DailyFactsError("DUPLICATE_PROVIDER_REQUEST", "duplicate Daily Facts symbol in CNEquity batch")
        if any(request.required_start > request.required_end for request in requests):
            raise DailyFactsError("INVALID_PROVIDER_SCOPE", "Daily Facts request start is after end")
        symbols = [request.symbol for request in requests]
        batch_start = min(request.required_start for request in requests)
        batch_end = max(request.required_end for request in requests)

        def fetch_one(bs: Any, symbol: str, _window_start: date, _window_end: date) -> list[dict[str, Any]] | None:
            request = by_symbol[symbol]
            expected_code = to_baostock_symbol(symbol)
            output: list[dict[str, Any]] = []
            for bounded_start, bounded_end in _year_windows(request.required_start, request.required_end):
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
                    output.append({"symbol": symbol, "trade_date": trade_date, "raw": raw})
            return output

        try:
            rows, failed = fetch_per_symbol(
                symbols, batch_start, batch_end, fetch_one, config=self.config,
                label="ASL daily facts", deadline=self.deadline, rest_after_batch=True,
            )
        except Exception as exc:
            raise DailyFactsError("SOURCE_ERROR", "CNEquity BaoStock batch failed") from exc
        fetched_at = datetime.now(timezone.utc).isoformat()
        raw_rows = [ProviderRawRow(row["symbol"], row["trade_date"], row["raw"], fetched_at, self.provider_version) for row in rows]
        if any(row.symbol not in by_symbol for row in raw_rows):
            raise DailyFactsError("PROVIDER_IDENTITY_MISMATCH", "BaoStock returned an out-of-batch symbol")
        if len({(row.symbol, row.trade_date) for row in raw_rows}) != len(raw_rows):
            raise DailyFactsError("DUPLICATE_PROVIDER_ROW", "duplicate BaoStock primary key")
        result = {symbol: [] for symbol in symbols}
        for row in raw_rows:
            result[row.symbol].append(row)
        return result, tuple(failed)
