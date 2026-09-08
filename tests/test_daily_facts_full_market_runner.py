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
    assert status == "NOT_COMPARABLE_NO_OVERLAP" and counts["OVERLAP_ROW_N"] == 0


def _unit(con, symbol="000001.SZ", state="FETCHING", raw_path=None):
    con.execute("insert into units(symbol,required_n,state,raw_path,schema) values(?,?,?,?,?)", (symbol, 1, state, raw_path, runner.SCHEMA)); con.commit()


def test_recovery_adopts_fetching_or_not_started_raw_without_provider(tmp_path):
    staging, raw_root, database = runner.run_paths(tmp_path, "recovery")
    con = runner._db(database)
    for symbol, state in (("000001.SZ", "FETCHING"), ("000002.SZ", "NOT_STARTED")):
        _unit(con, symbol, state)
        rows = _raw(symbol)
        rows[0] = runner.ProviderRawRow(symbol, rows[0].trade_date, {**rows[0].raw, "code": "sz." + symbol[:6]}, rows[0].fetched_at, rows[0].provider_version)
        runner._atomic_json(raw_root / f"{symbol}.json", runner._raw_payload(symbol, date(2016, 1, 1), date(2026, 9, 7), "r", rows))
    runner._recover_interrupted(con, root=tmp_path, raw_root=raw_root, start=date(2016, 1, 1), end=date(2026, 9, 7))
    assert con.execute("select state from units order by symbol").fetchall() == [("RAW_PERSISTED",), ("RAW_PERSISTED",)]


def test_recovery_fails_closed_on_bad_raw_and_resets_only_when_absent(tmp_path):
    _, raw_root, database = runner.run_paths(tmp_path, "recovery")
    con = runner._db(database); _unit(con, "000001.SZ", "FETCHING"); _unit(con, "000002.SZ", "FETCHING")
    runner._atomic_json(raw_root / "000001.SZ.json", {"schema": "bad"})
    runner._recover_interrupted(con, root=tmp_path, raw_root=raw_root, start=date(2016, 1, 1), end=date(2026, 9, 7))
    assert dict(con.execute("select symbol,state from units")) == {"000001.SZ": "QUALITY_FAIL", "000002.SZ": "NOT_STARTED"}
    assert json.loads((raw_root / "000001.SZ.json").read_text()) == {"schema": "bad"}


def test_bounded_source_error_retry_and_non_retryable_error(tmp_path):
    _, _, database = runner.run_paths(tmp_path, "retry")
    con = runner._db(database); _unit(con, state="NOT_STARTED")
    class Flaky:
        calls = 0
        def fetch(self, *_args):
            self.calls += 1
            if self.calls < 3: raise runner.DailyFactsError("SOURCE_ERROR", "temporary")
            return _raw("000001.SZ")
    provider = Flaky()
    assert runner._fetch_with_retry(con, provider, symbol="000001.SZ", start=date(2016, 1, 1), end=date(2026, 9, 7))
    assert provider.calls == 3 and con.execute("select attempts from units").fetchone()[0] == 3
    class BadVersion:
        calls = 0
        def fetch(self, *_args):
            self.calls += 1; raise runner.DailyFactsError("PROVIDER_VERSION_MISMATCH", "no")
    bad = BadVersion()
    with pytest.raises(runner.DailyFactsError):
        runner._fetch_with_retry(con, bad, symbol="000001.SZ", start=date(2016, 1, 1), end=date(2026, 9, 7))
    assert bad.calls == 1


def test_parity_exact_and_difference(monkeypatch, tmp_path):
    facts = runner.normalize(_raw("000001.SZ"))
    monkeypatch.setattr(runner, "_historical_preclose", lambda *_args: {("000001.SZ", "2026-09-07"): (10.0, "1")})
    assert runner._parity(tmp_path, facts)[0] == "EXACT"
    monkeypatch.setattr(runner, "_historical_preclose", lambda *_args: {("000001.SZ", "2026-09-07"): (9.0, "1")})
    assert runner._parity(tmp_path, facts)[0] == "UNEXPLAINED_DIFFERENCE"


def test_full_market_launchagent_is_one_shot_and_cannot_publish():
    wrapper = (ROOT / "tools/run_daily_facts_full_market_local.sh").read_text()
    plist = (ROOT / "ops/com.asl.daily-facts-full-market-phase1.plist").read_text()
    assert "run_daily_facts_phase1_full_market.py" in wrapper
    assert "--publication" not in wrapper and "mcp" not in wrapper.lower()
    assert "com.asl.daily-facts-full-market-phase1" in plist
    assert "WorkingDirectory" in plist and "RunAtLoad" in plist and "KeepAlive" not in plist
