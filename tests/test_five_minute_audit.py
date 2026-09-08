from datetime import date, datetime
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("five_minute_audit", ROOT / "src/ashare_data/five_minute_audit.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def row(**overrides):
    base = {"symbol": "000001.SZ", "trade_date": "2026-09-08", "bar_time": datetime(2026, 9, 8, 9, 35),
            "frequency": "5m", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
            "volume": 1, "amount": 10.0, "source": "tdx_protocol", "data_version": "v1"}
    return base | overrides


def test_valid_end_bar_session_and_counts():
    result = module.audit_rows([row()], trade_date=date(2026, 9, 8))
    assert result["invalid_session_time_n"] == 0
    assert result["invalid_ohlc_n"] == 0


def test_rejects_invalid_key_session_source_and_numbers():
    bad = row(bar_time=datetime(2026, 9, 8, 12, 0), source="mock", volume=-1, high=9.0)
    result = module.audit_rows([bad, bad], trade_date=date(2026, 9, 8))
    assert result["duplicate_pk_n"] == 1
    assert result["invalid_session_time_n"] == 2
    assert result["mock_row_n"] == 2
    assert result["invalid_ohlc_n"] == 2
    assert result["invalid_activity_n"] == 2
