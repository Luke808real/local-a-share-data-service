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


class _BaoStock:
    def login(self):
        return SimpleNamespace(error_code="0")

    def logout(self):
        return SimpleNamespace(error_code="0")


def test_frozen_preclose_source_identity_is_explicit() -> None:
    assert module.PROVIDER == "BAOSTOCK_HISTORY_K_PRECLOSE"
    assert module.FROZEN_BAOSTOCK_RUNTIME_VERSION == "0.9.3"


def test_adapter_records_frozen_distribution_version_not_module_version() -> None:
    provider = _BaoStock()
    with patch.dict(sys.modules, {"baostock": provider}), patch.object(
        module.importlib.metadata, "version", return_value="0.9.3"
    ):
        with module.BaoStockDailyFactsAdapter() as adapter:
            assert adapter.provider_version == "baostock-0.9.3"


def test_adapter_fails_closed_on_baostock_version_drift() -> None:
    provider = _BaoStock()
    with patch.dict(sys.modules, {"baostock": provider}), patch.object(
        module.importlib.metadata, "version", return_value="0.9.4"
    ), pytest.raises(module.DailyFactsError, match="expected baostock 0.9.3") as error:
        module.BaoStockDailyFactsAdapter().__enter__()
    assert error.value.code == "PROVIDER_VERSION_MISMATCH"
