"""Local, non-authoritative review aggregation from CNEquity 5m bars."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Callable, Iterable


SCHEMA = "ASL_FAST_REVIEW_5M_DERIVED_V01"
SCOPE = "REVIEW_EVIDENCE_ONLY_NOT_PUBLICATION"


def derive_review_rows(
    bars: Iterable[dict[str, Any]], *, trade_date: date, prior_close: Callable[[str], float | None],
    needs_formal_preclose: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate validated same-day 5m bars without creating formal facts."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bar in bars:
        if str(bar.get("trade_date")) != trade_date.isoformat() or bar.get("frequency") != "5m":
            raise ValueError("REVIEW_ROW_INVALID_SCOPE")
        grouped[str(bar["symbol"])].append(bar)
    ambiguous = needs_formal_preclose or set()
    result: list[dict[str, Any]] = []
    for symbol, rows in sorted(grouped.items()):
        try:
            ordered = sorted(rows, key=lambda row: str(row["bar_time"]))
            if len({str(row["bar_time"]) for row in ordered}) != len(ordered):
                raise ValueError("duplicate 5m primary key")
            if any(float(row[key]) < 0 for row in ordered for key in ("volume", "amount")):
                raise ValueError("negative 5m activity")
            open_ = float(ordered[0]["open"]); close = float(ordered[-1]["close"])
            high = max(float(row["high"]) for row in ordered); low = min(float(row["low"]) for row in ordered)
            if min(open_, close, high, low) <= 0 or high < max(open_, close) or low > min(open_, close):
                raise ValueError("invalid 5m OHLC")
            volume = sum(float(row["volume"]) for row in ordered)
            amount = sum(float(row["amount"]) for row in ordered)
            source = {str(row.get("source", "")) for row in ordered}
            provenance = {str(row.get("data_version", "")) for row in ordered}
            if "" in source or "" in provenance:
                raise ValueError("missing 5m provenance")
        except (KeyError, TypeError, ValueError):
            result.append({"symbol": symbol, "trade_date": trade_date.isoformat(), "row_state": "REVIEW_ROW_INVALID"})
            continue
        prior = prior_close(symbol)
        row_state = "REVIEW_ROW_READY"
        pct = None
        if symbol in ambiguous:
            row_state = "REVIEW_ROW_NEEDS_FORMAL_PRECLOSE"
        elif prior is None or prior <= 0:
            row_state = "REVIEW_ROW_NON_COMPARABLE"
        else:
            pct = (close / prior - 1.0) * 100.0
        result.append({
            "schema": SCHEMA, "scope": SCOPE, "publication_authority": False,
            "symbol": symbol, "trade_date": trade_date.isoformat(),
            "review_open": open_, "review_high": high, "review_low": low, "review_close": close,
            "review_volume": volume, "review_amount": amount,
            "review_prior_canonical_close": prior, "review_pct_change": pct,
            "bar_n": len(ordered),
            "first_bar_time": str(ordered[0]["bar_time"]),
            "last_bar_time": str(ordered[-1]["bar_time"]),
            "row_state": row_state, "source": "CNEQUITY_MINUTE_BARS_5M",
            "source_provenance": sorted(source), "source_data_versions": sorted(provenance),
        })
    return result
