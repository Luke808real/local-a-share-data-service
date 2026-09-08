"""Non-authoritative whole-market FastReview evidence via CNEquity.

This module owns neither HTTP nor pagination.  It delegates both to the
pinned CNEquity EastMoney client and clist adapter, and it deliberately has no
connection to ASL publication authority or LocalQuery.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cnequity.adapters.eastmoney.clist import clist_rows_to_symbols, fetch_clist_pages


SCHEMA = "ASL_FAST_REVIEW_SNAPSHOT_V01"
FIELDS = "f12,f13,f2,f3,f5,f6,f7,f8,f15,f16,f17,f18,f20,f21"
REVIEW_SCOPE = "REVIEW_EVIDENCE_ONLY_NOT_PUBLICATION"


class FastReviewError(RuntimeError):
    """A complete, semantically valid review snapshot could not be obtained."""


@dataclass(frozen=True)
class FastReviewSnapshotV01:
    rows: tuple[dict[str, Any], ...]
    acquired_at: str
    provider: str = "CNEQUITY_EASTMONEY_CLIST"
    schema: str = SCHEMA
    scope: str = REVIEW_SCOPE

    @classmethod
    def acquire(cls, client: Any, *, page_size: int = 500) -> "FastReviewSnapshotV01":
        """Acquire only a complete CNEquity-validated full-market snapshot."""
        try:
            raw_rows = fetch_clist_pages(client, fields=FIELDS, page_size=page_size)
        except Exception as exc:
            raise FastReviewError("FAST_REVIEW_SOURCE_ERROR") from exc
        symbols = clist_rows_to_symbols(raw_rows)
        if not raw_rows or len(symbols) != len(raw_rows):
            raise FastReviewError("FAST_REVIEW_IDENTITY_OR_COVERAGE_ERROR")
        if len({symbol for symbol, _row in symbols}) != len(symbols):
            raise FastReviewError("FAST_REVIEW_DUPLICATE_SYMBOL")
        rows = tuple({"symbol": symbol, "provider_row": row} for symbol, row in symbols)
        return cls(rows=rows, acquired_at=datetime.now(timezone.utc).isoformat())

    def validate_semantics(
        self, daily_bar: Callable[[str], dict[str, Any] | None], *, pct_tolerance: float = 0.02,
    ) -> dict[str, Any]:
        """Fail closed unless price and percentage semantics match local bars.

        Volume, amount and turnover are retained as uncalibrated review fields
        until an authorized source-specific unit calibration exists.
        """
        checked = 0
        for item in self.rows:
            row = item["provider_row"]
            try:
                close = float(row["f2"])
                preclose = float(row["f18"])
                pct = float(row["f3"])
            except (KeyError, TypeError, ValueError) as exc:
                raise FastReviewError("FAST_REVIEW_FIELD_SEMANTIC_ERROR") from exc
            if preclose <= 0 or abs(((close / preclose) - 1.0) * 100.0 - pct) > pct_tolerance:
                raise FastReviewError("FAST_REVIEW_PRICE_SEMANTIC_ERROR")
            bar = daily_bar(item["symbol"])
            if bar is None:
                continue
            checked += 1
            for review_field, bar_field in (("f2", "close"), ("f15", "high"), ("f16", "low"), ("f17", "open")):
                try:
                    if abs(float(row[review_field]) - float(bar[bar_field])) > 0.01:
                        raise FastReviewError("FAST_REVIEW_OHLC_SEMANTIC_ERROR")
                except (KeyError, TypeError, ValueError) as exc:
                    raise FastReviewError("FAST_REVIEW_OHLC_SEMANTIC_ERROR") from exc
        if checked == 0:
            raise FastReviewError("FAST_REVIEW_NOT_COMPARABLE")
        return {
            "schema": SCHEMA,
            "overall": "PASS",
            "ohlc_checked_n": checked,
            "preclose_pct_checked_n": len(self.rows),
            "volume_unit": "UNVERIFIED",
            "amount_unit": "UNVERIFIED",
            "turnover_unit": "UNVERIFIED",
        }

    def persist_review_cache(self, path: Path, semantic_receipt: dict[str, Any]) -> None:
        """Persist review evidence only after semantic validation; never publish it."""
        if semantic_receipt.get("overall") != "PASS":
            raise FastReviewError("FAST_REVIEW_SEMANTICS_NOT_PASS")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": self.schema,
            "scope": self.scope,
            "publication_authority": False,
            "provider": self.provider,
            "acquired_at": self.acquired_at,
            "semantic_receipt": semantic_receipt,
            "row_n": len(self.rows),
            "rows": list(self.rows),
        }
        fd, temporary = tempfile.mkstemp(prefix=".tmp-fast-review-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
