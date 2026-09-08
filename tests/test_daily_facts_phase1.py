"""Focused frozen-provider contract tests for Daily Facts Phase 1."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("daily_facts_phase1", ROOT / "src/ashare_data/daily_facts_phase1.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_frozen_preclose_source_identity_is_explicit() -> None:
    assert module.PROVIDER == "BAOSTOCK_HISTORY_K_PRECLOSE"
    assert module.FROZEN_BAOSTOCK_RUNTIME_VERSION == "0.9.3"
    assert "import baostock" not in (ROOT / "src/ashare_data/daily_facts_phase1.py").read_text()


def test_bridge_records_frozen_distribution_version_and_delegates_session() -> None:
    from ashare_data import cnequity_bridge as bridge
    with patch.object(bridge.importlib.metadata, "version", return_value="0.9.3"):
        with bridge.CNEquityBaoStockDailyFactsBridge() as adapter:
            assert adapter.provider_version == "baostock-0.9.3"
    assert "fetch_per_symbol" in Path(bridge.__file__).read_text()


def test_bridge_delegates_a_bounded_request_to_cnequity_and_preserves_raw_values() -> None:
    from datetime import date
    from ashare_data import cnequity_bridge as bridge

    class Result:
        error_code = "0"
        fields = bridge.PROVIDER_FIELDS

        def __init__(self) -> None:
            self._seen = False

        def next(self) -> bool:
            if self._seen:
                return False
            self._seen = True
            return True

        def get_row_data(self) -> list[str]:
            return ["2026-09-07", "sz.002580", "19.14", "9.9791", "12.8277", "1", "0"]

    class Session:
        def query_history_k_data_plus(self, code: str, fields: str, **kwargs: object) -> Result:
            assert code == "sz.002580"
            assert fields == ",".join(bridge.PROVIDER_FIELDS)
            assert kwargs["start_date"] == "2026-09-07"
            assert kwargs["end_date"] == "2026-09-07"
            return Result()

    captured: dict[str, object] = {}

    def fake_fetch_per_symbol(symbols: list[str], start: date, end: date, fetch_one: object, **kwargs: object):
        captured.update({"symbols": symbols, "start": start, "end": end, **kwargs})
        assert callable(fetch_one)
        return fetch_one(Session(), symbols[0], start, end), []

    with patch.object(bridge.importlib.metadata, "version", return_value="0.9.3"), patch.object(
        bridge, "fetch_per_symbol", side_effect=fake_fetch_per_symbol
    ):
        with bridge.CNEquityBaoStockDailyFactsBridge(config=SimpleNamespace(name="test")) as adapter:
            rows = adapter.fetch("002580.SZ", date(2026, 9, 7), date(2026, 9, 7))

    assert captured["symbols"] == ["002580.SZ"]
    assert captured["label"] == "ASL daily facts"
    assert rows[0].raw == {
        "date": "2026-09-07", "code": "sz.002580", "preclose": "19.14", "pctChg": "9.9791",
        "turn": "12.8277", "tradestatus": "1", "isST": "0",
    }


def test_bridge_fails_closed_on_baostock_version_drift() -> None:
    from ashare_data import cnequity_bridge as bridge
    with patch.object(bridge.importlib.metadata, "version", return_value="0.9.4"), pytest.raises(bridge.DailyFactsError, match="expected baostock 0.9.3") as error:
        bridge.CNEquityBaoStockDailyFactsBridge().__enter__()
    assert error.value.code == "PROVIDER_VERSION_MISMATCH"
