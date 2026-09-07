from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "tools/run_r3_frozen_shsz_daily_recovery_v01.py"
spec = importlib.util.spec_from_file_location("recovery", PATH)
recovery = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(recovery)


def test_eligible_symbols_are_lifecycle_bound_to_frozen_authority() -> None:
    records = {
        "000001.SZ": {"list_date": "1991-01-01", "delist_date": None},
        "000002.SZ": {"list_date": "1991-01-01", "delist_date": "2026-08-30"},
        "000003.SZ": {"list_date": "2026-09-02", "delist_date": None},
    }
    assert recovery.eligible_symbols("2026-08-31", records, expected_n=1) == ("000001.SZ",)


def test_quality_does_not_convert_unobserved_symbols_to_suspension() -> None:
    rows = [{"symbol": "000001.SZ", "trade_date": "2026-08-31", "open": 1.0, "high": 2.0, "low": 1.0, "close": 2.0, "volume": 1, "amount": 1.0, "source": "tdx_protocol", "data_version": "v2"}]
    result = recovery.quality(rows, "2026-08-31", {"000001.SZ", "000002.SZ"})
    assert result["requested_not_observed"] == ["000002.SZ"]
    assert result["observed_n"] == 1


def test_secondary_wrong_code_or_date_is_source_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        error_code = "0"
        fields = ["date", "code", "tradestatus"]
        error_msg = ""
        def next(self): return not hasattr(self, "done") and not setattr(self, "done", True)
        def get_row_data(self): return ["2026-08-31", "sz.000002", "0"]
    class BS:
        def login(self): return type("Login", (), {"error_code": "0"})()
        def logout(self): pass
        def query_history_k_data_plus(self, *args, **kwargs): return Result()
    monkeypatch.setitem(__import__("sys").modules, "baostock", BS())
    result = recovery.classify_secondary(["000001.SZ"], "2026-08-31")
    assert result[0]["final_classification"] == "SOURCE_ERROR"


@pytest.mark.parametrize(
    ("rows", "expected"),
    [([], "UNKNOWN"), ([['2026-08-31', 'sz.000001', '1']], "BAR_PRESENT"), ([['2026-08-31', 'sz.000001', '0']], "SUSPENDED")],
)
def test_secondary_classification_never_auto_converts_missing_to_suspended(monkeypatch: pytest.MonkeyPatch, rows, expected: str) -> None:
    class Result:
        error_code = "0"
        fields = ["date", "code", "tradestatus"]
        error_msg = ""
        def __init__(self): self.index = 0
        def next(self):
            if self.index >= len(rows): return False
            self.index += 1
            return True
        def get_row_data(self): return rows[self.index - 1]
    class BS:
        def login(self): return type("Login", (), {"error_code": "0"})()
        def logout(self): pass
        def query_history_k_data_plus(self, *args, **kwargs): return Result()
    monkeypatch.setitem(__import__("sys").modules, "baostock", BS())
    assert recovery.classify_secondary(["000001.SZ"], "2026-08-31")[0]["final_classification"] == expected


def test_batch_plan_retries_only_nonterminal_frozen_symbols() -> None:
    eligible = tuple(f"0000{i:02d}.SZ" for i in range(1, 7))
    batches = recovery._batch_plan("2026-08-31", eligible, {"000001.SZ", "000002.SZ"}, ("000003.SZ",))
    assert batches == [("000003.SZ",), ("000004.SZ", "000005.SZ", "000006.SZ")]
