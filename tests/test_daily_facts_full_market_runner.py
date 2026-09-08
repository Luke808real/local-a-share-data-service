from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

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
    con.execute("insert into units(symbol,required_n,state,raw_path,schema,required_start,required_end) values(?,?,?,?,?,?,?)", (symbol, 1, state, raw_path, runner.SCHEMA, "2016-01-01", "2026-09-07")); con.commit()


def test_recovery_adopts_fetching_or_not_started_raw_without_provider(tmp_path):
    staging, raw_root, database = runner.run_paths(tmp_path, "recovery")
    con = runner._db(database)
    for symbol, state in (("000001.SZ", "FETCHING"), ("000002.SZ", "NOT_STARTED")):
        _unit(con, symbol, state)
        rows = _raw(symbol)
        rows[0] = runner.ProviderRawRow(symbol, rows[0].trade_date, {**rows[0].raw, "code": "sz." + symbol[:6]}, rows[0].fetched_at, rows[0].provider_version)
        runner._atomic_json(raw_root / f"{symbol}.json", runner._raw_payload(symbol, date(2016, 1, 1), date(2026, 9, 7), "r", rows))
    runner._recover_interrupted(con, root=tmp_path, raw_root=raw_root)
    assert con.execute("select state from units order by symbol").fetchall() == [("RAW_PERSISTED",), ("RAW_PERSISTED",)]


def test_recovery_fails_closed_on_bad_raw_and_resets_only_when_absent(tmp_path):
    _, raw_root, database = runner.run_paths(tmp_path, "recovery")
    con = runner._db(database); _unit(con, "000001.SZ", "FETCHING"); _unit(con, "000002.SZ", "FETCHING")
    runner._atomic_json(raw_root / "000001.SZ.json", {"schema": "bad"})
    runner._recover_interrupted(con, root=tmp_path, raw_root=raw_root)
    assert dict(con.execute("select symbol,state from units")) == {"000001.SZ": "QUALITY_FAIL", "000002.SZ": "NOT_STARTED"}
    assert json.loads((raw_root / "000001.SZ.json").read_text()) == {"schema": "bad"}


def test_cnequity_failure_is_one_terminal_cycle_not_outer_retry(tmp_path):
    _, raw_root, database = runner.run_paths(tmp_path, "retry")
    con = runner._db(database); _unit(con, state="NOT_STARTED")
    class Provider:
        calls = 0
        def fetch_batch(self, requests):
            self.calls += 1
            return {}, (requests[0].symbol,)
    provider = Provider()
    request = runner.DailyFactsRequest("000001.SZ", date(2016, 1, 1), date(2026, 9, 7))
    assert runner._acquire_batch(con, root=tmp_path, raw_root=raw_root, provider=provider, requests=[request]) == 0
    assert provider.calls == 1
    assert con.execute("select state,attempts from units").fetchone() == ("PROVIDER_FAIL", 1)


def test_parity_exact_and_difference(monkeypatch, tmp_path):
    facts = runner.normalize(_raw("000001.SZ"))
    monkeypatch.setattr(runner, "_historical_preclose", lambda *_args: {("000001.SZ", "2026-09-07"): (10.0, "1")})
    assert runner._parity(tmp_path, facts)[0] == "EXACT"
    monkeypatch.setattr(runner, "_historical_preclose", lambda *_args: {("000001.SZ", "2026-09-07"): (9.0, "1")})
    assert runner._parity(tmp_path, facts)[0] == "UNEXPLAINED_DIFFERENCE"


def test_broader_raw_is_accepted_but_narrower_raw_fails(tmp_path):
    raw = tmp_path / "raw.json"
    runner._atomic_json(raw, runner._raw_payload("000001.SZ", date(2016, 1, 1), date(2026, 9, 7), "r", _raw("000001.SZ")))
    assert runner._valid_raw_for_unit(raw, symbol="000001.SZ", required_start=date(2020, 1, 1), required_end=date(2026, 9, 7))
    with pytest.raises(runner.DailyFactsError, match="wrong request scope"):
        runner._valid_raw_for_unit(raw, symbol="000001.SZ", required_start=date(2010, 1, 1), required_end=date(2026, 9, 7))


def test_acquire_only_then_postprocess_uses_one_provider_call(monkeypatch, tmp_path):
    import duckdb
    daily = tmp_path / "daily.parquet"
    with duckdb.connect(":memory:") as con:
        con.execute("create table d(symbol varchar,trade_date date)")
        con.execute("insert into d values ('000001.SZ',date '2026-09-07')")
        con.execute("copy d to ? (format parquet)", [str(daily)])
    relation = "read_parquet('" + str(daily) + "')"
    monkeypatch.setattr(runner, "published_scope", lambda _root: (["000001.SZ"], {"000001.SZ": 1}, date(2026, 9, 7), "test", relation))
    class Provider:
        calls = 0
        config = SimpleNamespace(baostock_batch_size=20)
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def fetch_batch(self, requests):
            self.calls += 1
            return {request.symbol: _raw(request.symbol) for request in requests}, ()
    provider = Provider()
    result = runner.execute(tmp_path, run_name="mode", acquire_only=True, provider_factory=lambda: provider)
    assert result["states"] == {"RAW_PERSISTED": 1} and provider.calls == 1
    assert not (tmp_path / "staging/mode/normalized/000001.SZ.parquet").exists()
    result = runner.execute(tmp_path, run_name="mode", postprocess_only=True, provider_factory=lambda: (_ for _ in ()).throw(AssertionError("no provider")))
    assert result["states"] == {"QUALITY_PASS": 1} and provider.calls == 1


def test_persisted_raw_and_quality_pass_require_zero_new_provider_calls(monkeypatch, tmp_path):
    import duckdb
    daily = tmp_path / "daily.parquet"
    with duckdb.connect(":memory:") as con:
        con.execute("create table d(symbol varchar,trade_date date)")
        con.execute("insert into d values ('000001.SZ',date '2026-09-07')")
        con.execute("copy d to ? (format parquet)", [str(daily)])
    relation = "read_parquet('" + str(daily) + "')"
    monkeypatch.setattr(runner, "published_scope", lambda _root: (["000001.SZ"], {"000001.SZ": 1}, date(2026, 9, 7), "test", relation))
    class Provider:
        config = SimpleNamespace(baostock_batch_size=20)
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def fetch_batch(self, requests): return {request.symbol: _raw(request.symbol) for request in requests}, ()
    runner.execute(tmp_path, run_name="reuse", acquire_only=True, provider_factory=Provider)
    no_provider = lambda: (_ for _ in ()).throw(AssertionError("existing evidence must not fetch"))
    assert runner.execute(tmp_path, run_name="reuse", acquire_only=True, provider_factory=no_provider)["network_fetched_symbol_n"] == 0
    runner.execute(tmp_path, run_name="reuse", postprocess_only=True, provider_factory=no_provider)
    assert runner.execute(tmp_path, run_name="reuse", acquire_only=True, provider_factory=no_provider)["network_fetched_symbol_n"] == 0


def test_production_provider_loads_the_real_cnequity_config():
    provider = runner._production_provider()
    assert provider.config.source_intervals["baostock"] == 1.0
    assert provider.config.baostock_batch_size == 20
    assert provider.config.baostock_batch_rest_seconds == 120.0
    assert provider.deadline == 300.0


def test_full_market_launchagent_is_one_shot_and_cannot_publish():
    wrapper = (ROOT / "tools/run_daily_facts_full_market_local.sh").read_text()
    plist = (ROOT / "ops/com.asl.daily-facts-full-market-phase1.plist").read_text()
    assert "run_daily_facts_phase1_full_market.py" in wrapper
    assert "--publication" not in wrapper and "mcp" not in wrapper.lower()
    assert "com.asl.daily-facts-full-market-phase1" in plist
    assert "WorkingDirectory" in plist and "RunAtLoad" in plist and "KeepAlive" not in plist
