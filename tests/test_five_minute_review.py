from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("five_minute_review", ROOT / "src/ashare_data/five_minute_review.py")
module = importlib.util.module_from_spec(spec); assert spec.loader is not None
sys.modules[spec.name] = module; spec.loader.exec_module(module)


def _bar(time: str, *, close: float, volume: int = 10, amount: float = 100) -> dict:
    return {"symbol": "000001.SZ", "trade_date": "2026-09-08", "bar_time": time, "frequency": "5m",
            "open": 10.0, "high": 12.0, "low": 9.0, "close": close, "volume": volume, "amount": amount,
            "source": "tdx_protocol", "data_version": "v1"}


def test_5m_review_derives_ohlcv_and_never_labels_prior_close_preclose():
    rows = module.derive_review_rows([_bar("2026-09-08 09:35:00", close=11), _bar("2026-09-08 09:40:00", close=12)], trade_date=date(2026, 9, 8), prior_close=lambda _: 10.0)
    row = rows[0]
    assert row["review_open"] == 10 and row["review_close"] == 12
    assert row["review_volume"] == 20 and row["review_amount"] == 200
    assert row["review_pct_change"] == pytest.approx(20) and "preclose" not in row
    assert row["bar_n"] == 2
    assert row["first_bar_time"] == "2026-09-08 09:35:00"
    assert row["publication_authority"] is False


def test_5m_review_keeps_noncomparable_and_reference_price_ambiguity_out_of_ready():
    bars = [_bar("2026-09-08 09:35:00", close=11)]
    assert module.derive_review_rows(bars, trade_date=date(2026, 9, 8), prior_close=lambda _: None)[0]["row_state"] == "REVIEW_ROW_NON_COMPARABLE"
    assert module.derive_review_rows(bars, trade_date=date(2026, 9, 8), prior_close=lambda _: 10.0, needs_formal_preclose={"000001.SZ"})[0]["row_state"] == "REVIEW_ROW_NEEDS_FORMAL_PRECLOSE"


def test_invalid_5m_row_never_becomes_ready_or_zero_default():
    bad = _bar("2026-09-08 09:35:00", close=11, volume=-1)
    assert module.derive_review_rows([bad], trade_date=date(2026, 9, 8), prior_close=lambda _: 10.0)[0]["row_state"] == "REVIEW_ROW_INVALID"
