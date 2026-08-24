"""Targeted tests for the R3 TDX volume anomaly audit tool (pure logic)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audits"))

from r3_tdx_volume_anomaly_audit_v01 import (  # noqa: E402
    classify_volume_row,
    listing_distance_buckets,
    select_bounded_sample,
    select_matched_controls,
)


def _cl(vwap_ratio: float, *, close: float = 10.0) -> dict:
    """Row where low=high=close=10 and amount/volume gives vwap_ratio*close."""
    volume = 1000.0
    amount = vwap_ratio * close * volume
    return classify_volume_row(
        open_=close, high=close, low=close, close=close,
        volume=volume, amount=amount,
    )


def test_clean_row_is_clean():
    r = _cl(1.0)
    assert r["HARD"] is False
    assert r["FLAVOR"] == []


def test_hard_below_threshold():
    # VWAP = 0.4 * low = 4.0 < low*0.5 = 5 -> HARD_BELOW
    r = _cl(0.4)
    assert r["HARD"] is True
    assert "HARD_BELOW" in r["FLAVOR"]


def test_hard_above_threshold():
    # VWAP = 3.0 * high = 30 > high*2.0 = 20 -> HARD_ABOVE
    r = _cl(3.0)
    assert r["HARD"] is True
    assert "HARD_ABOVE" in r["FLAVOR"]


def test_soft_out_of_range_not_hard():
    # VWAP = 0.9 * low = 9 inside [5,20] margins? no: low*0.5=5, so 9 >= 5
    # and <= 20 => not hard. VWAP=9 < low=10 => SOFT_OUT_OF_RANGE.
    r = classify_volume_row(
        open_=10.0, high=10.0, low=10.0, close=10.0, volume=1000.0, amount=9000.0
    )
    assert r["HARD"] is False
    assert "SOFT_OUT_OF_RANGE" in r["FLAVOR"]


def test_one_price_hard_ratio():
    # one-price row with amount/(close*volume) = 0.49 < 0.5 -> HARD
    r = classify_volume_row(
        open_=10.0, high=10.0, low=10.0, close=10.0, volume=1000.0, amount=4900.0
    )
    assert r["HARD"] is True
    assert any(f.startswith("HARD_ONE_PRICE") for f in r["FLAVOR"])


def test_one_price_soft_ratio_not_hard():
    r = classify_volume_row(
        open_=10.0, high=10.0, low=10.0, close=10.0, volume=1000.0, amount=9500.0
    )
    assert r["HARD"] is False
    assert any(f.startswith("SOFT_ONE_PRICE") for f in r["FLAVOR"])


def test_margin_noise_not_hard():
    # VWAP slightly outside [low, high] but far inside 0.5x/2.0x margins.
    r = classify_volume_row(
        open_=10.0, high=11.0, low=9.0, close=10.0, volume=1000.0, amount=10950.0
    )
    assert r["HARD"] is False


def test_listing_buckets(tmp_path):
    root = tmp_path / "root"
    (root / "curated" / "instruments").mkdir(parents=True)
    pl.DataFrame(
        {"symbol": ["AAA.SZ", "BBB.SH"], "list_date": [date(2016, 1, 1), date(2015, 6, 1)]}
    ).write_parquet(root / "curated" / "instruments" / "part-merged.parquet")
    keys = {
        ("AAA.SZ", date(2016, 1, 2)),   # 1 day -> 0-5
        ("AAA.SZ", date(2016, 1, 20)),  # 19 days -> 6-20
        ("BBB.SH", date(2015, 6, 21)),  # 20 days -> 6-20
        ("BBB.SH", date(2016, 1, 1)),   # 214 days -> 61-250
        ("AAA.SZ", date(2017, 1, 1)),   # 366 days -> >250
    }
    out = listing_distance_buckets(root, keys)
    b = out["buckets"]
    assert b["0-5"] == 1
    assert b["6-20"] == 2
    assert b["61-250"] == 1
    assert b[">250"] == 1
    assert b["21-60"] == 0
    assert out["total"] == 5


def test_bounded_sample_deterministic():
    keys = {
        ("A.SZ", date(2016, 1, 1)),
        ("A.SZ", date(2016, 1, 2)),
        ("A.SZ", date(2016, 1, 3)),
        ("B.SZ", date(2016, 2, 1)),
        ("C.SZ", date(2016, 3, 1)),
        ("300546.SZ", date(2016, 9, 30)),
        ("300546.SZ", date(2016, 10, 11)),
        ("300546.SZ", date(2016, 10, 12)),
    }
    s1 = select_bounded_sample(keys, top_n=2, dates_per_symbol=2)
    s2 = select_bounded_sample(keys, top_n=2, dates_per_symbol=2)
    assert s1["selected_keys"] == s2["selected_keys"]
    # A has 3 -> first 2 dates; 300546 included explicitly -> first 2 dates.
    assert ("A.SZ", "2016-01-01") in s1["selected_keys"]
    assert ("A.SZ", "2016-01-02") in s1["selected_keys"]
    assert ("300546.SZ", "2016-09-30") in s1["selected_keys"]
    assert ("300546.SZ", "2016-10-11") in s1["selected_keys"]
    assert "300546.SZ" in s1["selected_symbols"]


def test_matched_controls_prefer_later_same_symbol():
    clean = pl.DataFrame(
        {
            "symbol": ["A.SZ", "A.SZ", "B.SZ", "B.SZ"],
            "trade_date": [
                date(2016, 1, 10),
                date(2016, 2, 1),
                date(2016, 3, 1),
                date(2016, 4, 1),
            ],
            "hard": [False, False, False, False],
        }
    )
    anomaly = {("A.SZ", date(2016, 1, 5)), ("B.SZ", date(2016, 2, 15))}
    out = select_matched_controls(clean, anomaly, controls_total=2)
    assert out["control_n"] == 2
    # nearest later same-symbol rows: A.SZ 2016-01-10, B.SZ 2016-03-01
    assert ("A.SZ", "2016-01-10") in [
        (c["symbol"], c["trade_date"]) for c in out["controls"]
    ]
    assert ("B.SZ", "2016-03-01") in [
        (c["symbol"], c["trade_date"]) for c in out["controls"]
    ]


def test_matched_controls_exact_count():
    clean = pl.DataFrame(
        {
            "symbol": ["A.SZ", "A.SZ", "A.SZ", "A.SZ", "A.SZ"],
            "trade_date": [
                date(2016, 1, 10),
                date(2016, 1, 15),
                date(2016, 2, 1),
                date(2016, 3, 1),
                date(2016, 4, 1),
            ],
            "hard": [False, False, False, False, False],
        }
    )
    anomaly = {
        ("A.SZ", date(2016, 1, 5)),
        ("A.SZ", date(2016, 1, 8)),
        ("A.SZ", date(2016, 1, 9)),
        ("A.SZ", date(2016, 1, 12)),
        ("A.SZ", date(2016, 1, 20)),
    }
    out = select_matched_controls(clean, anomaly, controls_total=4)
    assert out["control_n"] == 4  # exactly 4, deterministic
    dates = sorted(c["trade_date"] for c in out["controls"])
    assert dates == sorted(dates)
