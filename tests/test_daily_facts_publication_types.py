"""Published Daily Facts physical types cannot drift from certified numeric facts."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("daily_facts_publish", ROOT / "tools/publish_daily_facts_phase1_v01.py")
assert SPEC and SPEC.loader
publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publish)


def test_published_rows_preserve_numeric_facts_and_nulls(tmp_path):
    rows = publish._published_rows([{
        "symbol": "002315.SZ", "trade_date": "2026-09-09", "preclose": "25.48",
        "pct_chg": '"-2.0408"', "turnover_rate": '"2.7005"',
        "pct_chg_calculated": float("nan"), "reference_price_expected": None,
    }])
    assert rows == [{
        "symbol": "002315.SZ", "trade_date": "2026-09-09", "preclose": 25.48,
        "pct_chg": -2.0408, "turnover_rate": 2.7005,
        "pct_chg_calculated": None, "reference_price_expected": None,
    }]
    output = tmp_path / "facts.parquet"
    from run_daily_facts_phase1_full_market import _write_parquet
    _write_parquet(output, rows)
    with duckdb.connect(":memory:") as con:
        types = {row[0]: row[1] for row in con.execute("describe select * from read_parquet(?)", [str(output)]).fetchall()}
    assert types["preclose"] == "DOUBLE"
    assert types["pct_chg"] == "DOUBLE"
    assert types["turnover_rate"] == "DOUBLE"


def test_published_rows_reject_malformed_numeric_fact():
    try:
        publish._published_rows([{"pct_chg": "not-a-number"}])
    except publish.DailyFactsError as exc:
        assert exc.code == "PUBLISHED_FACT_INVALID_NUMERIC"
    else:
        raise AssertionError("malformed numeric fact must fail closed")
