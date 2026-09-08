"""Generic local structural features for review evidence, not strategy signals."""
from __future__ import annotations

from statistics import mean
from typing import Any, Iterable


SCHEMA = "ASL_FAST_REVIEW_DAILY_FEATURES_V01"
SCOPE = "REVIEW_EVIDENCE_ONLY_NOT_PUBLICATION"


def derive_features(rows_by_symbol: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for symbol, rows in sorted(rows_by_symbol.items()):
        rows = sorted(rows, key=lambda row: str(row["trade_date"]))
        if len(rows) < 21:
            continue
        current = rows[-1]
        close = float(current["close"]); high = float(current["high"]); low = float(current["low"])
        volume = float(current["volume"]); amount = float(current["amount"])
        closes = [float(row["close"]) for row in rows]
        volumes = [float(row["volume"]) for row in rows]
        amounts = [float(row["amount"]) for row in rows]
        prior_close = closes[-2]
        ma5, ma10, ma20 = mean(closes[-5:]), mean(closes[-10:]), mean(closes[-20:])
        volume_base, amount_base = mean(volumes[-6:-1]), mean(amounts[-6:-1])
        pct = (close / prior_close - 1.0) * 100.0 if prior_close > 0 else None
        result.append({
            "schema": SCHEMA, "scope": SCOPE, "publication_authority": False,
            "symbol": symbol, "trade_date": str(current["trade_date"]),
            "pct_change": pct, "intraday_range_pct": (high / low - 1.0) * 100.0 if low > 0 else None,
            "volume_ratio_vs_5d": volume / volume_base if volume_base > 0 else None,
            "amount_ratio_vs_5d": amount / amount_base if amount_base > 0 else None,
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "distance_to_ma5": (close / ma5 - 1.0) * 100.0,
            "distance_to_ma10": (close / ma10 - 1.0) * 100.0,
            "distance_to_ma20": (close / ma20 - 1.0) * 100.0,
            "distance_to_recent_5d_high": (close / max(closes[-5:]) - 1.0) * 100.0,
            "distance_to_recent_20d_high": (close / max(closes[-20:]) - 1.0) * 100.0,
            "recent_daily_max_pct": max((closes[i] / closes[i - 1] - 1.0) * 100.0 for i in range(-5, 0)),
            "recent_volume_expansion": volume / max(volumes[-6:-1]) if max(volumes[-6:-1]) > 0 else None,
        })
    return result


def generic_candidates(features: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """A transparent structural screen, explicitly not a buy/sell recommendation."""
    return [row for row in features if all((
        row["pct_change"] is not None and row["pct_change"] > 0,
        row["distance_to_ma5"] >= 0,
        row["volume_ratio_vs_5d"] is not None and row["volume_ratio_vs_5d"] >= 1.2,
        row["distance_to_recent_20d_high"] >= -10.0,
    ))]
