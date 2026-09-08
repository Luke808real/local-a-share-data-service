from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("full_runner", ROOT / "tools/run_daily_facts_phase1_full_market.py")
runner = importlib.util.module_from_spec(spec); assert spec.loader is not None
sys.modules[spec.name] = runner; spec.loader.exec_module(runner)


def _raw(symbol: str, day: str = "2026-09-07"):
    return [runner.ProviderRawRow(symbol, date.fromisoformat(day), {"date": day, "code": "sz.000001", "preclose": "10.00", "pctChg": "1.0000", "turn": "2.5", "tradestatus": "1", "isST": "0"}, "2026-09-08T00:00:00+00:00", "baostock-0.9.3")]


def test_raw_contract_rejects_normalized_only_and_preserves_lossless_values(tmp_path):
    raw = tmp_path / "raw.json"
    runner._atomic_json(raw, runner._raw_payload("000001.SZ", date(2026, 9, 7), date(2026, 9, 7), "r", _raw("000001.SZ")))
    rows = runner._load_raw(raw)
    assert rows[0].raw["turn"] == "2.5"
    bad = json.loads(raw.read_text()); bad["provider_version"] = "00.9.30"; raw.write_text(json.dumps(bad))
    with pytest.raises(runner.DailyFactsError, match="current provider contract"):
        runner._load_raw(raw)


def test_quality_rejects_duplicate_unknown_and_partial_symbol():
    facts = runner.normalize(_raw("000001.SZ"))
    with pytest.raises(runner.DailyFactsError, match="expected 2"):
        runner._validate("000001.SZ", facts, {"2026-09-07", "2026-09-08"})
    duplicate = facts + facts
    with pytest.raises(runner.DailyFactsError, match="duplicate"):
        runner._validate("000001.SZ", duplicate, {"2026-09-07"})
    unknown = runner.normalize([runner.ProviderRawRow("000001.SZ", date(2026, 9, 7), {**_raw("000001.SZ")[0].raw, "isST": ""}, "x", "baostock-0.9.3")])
    with pytest.raises(runner.DailyFactsError, match="unknown"):
        runner._validate("000001.SZ", unknown, {"2026-09-07"})


def test_historical_parity_is_diagnostic_not_authority(tmp_path):
    assert runner.discover(tmp_path) == []
    # No historical file means no overlap, and cannot promote anything by itself.
    status, counts = runner._parity(tmp_path, runner.normalize(_raw("000001.SZ")))
    assert status == "EXACT" and counts["OVERLAP_ROW_N"] == 0


def test_full_market_launchagent_is_one_shot_and_cannot_publish():
    wrapper = (ROOT / "tools/run_daily_facts_full_market_local.sh").read_text()
    plist = (ROOT / "ops/com.asl.daily-facts-full-market-phase1.plist").read_text()
    assert "run_daily_facts_phase1_full_market.py" in wrapper
    assert "--publication" not in wrapper and "mcp" not in wrapper.lower()
    assert "com.asl.daily-facts-full-market-phase1" in plist
    assert "WorkingDirectory" in plist and "RunAtLoad" in plist and "KeepAlive" not in plist
