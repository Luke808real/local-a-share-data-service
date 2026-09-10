"""No-network tests for the staging-only Daily Facts certification gate."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import duckdb


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
spec = importlib.util.spec_from_file_location("daily_facts_cert", REPO / "tools/certify_daily_facts_phase1_v01.py")
cert = importlib.util.module_from_spec(spec); assert spec.loader is not None
sys.modules[spec.name] = cert; spec.loader.exec_module(cert)


def test_incomplete_ledger_fails_closed_without_provider_or_publication(monkeypatch, tmp_path):
    start = end = date(2026, 9, 9)
    symbols = ["000001.SZ"]
    monkeypatch.setattr(cert, "published_scope", lambda *_args, **_kwargs: (symbols, {symbols[0]: 1}, end, "r3-hash", "unused"))
    staging, _raw, database = cert.run_paths(tmp_path, "run")
    con = cert._db(database)
    try:
        plan = {"symbols": symbols, "symbol_hash": cert._sha(symbols), "as_of": end.isoformat(),
                "start": start.isoformat(), "end": end.isoformat(), "daily_manifest_hash": "r3-hash", "schema": cert.SCHEMA.replace("CERTIFICATION", "FULL_MARKET_RUNNER")}
        # The run schema is asserted via the persisted plan fields, not trusted
        # because a file happens to exist under staging.
        cert._set_metadata(con, "plan", plan)
        con.execute("insert into units(symbol,required_n,state,attempts,schema,required_start,required_end) values(?,?,?,?,?,?,?)",
                    (symbols[0], 1, "NOT_STARTED", 0, "ASL_DAILY_FACTS_FULL_MARKET_RUNNER_V01", start.isoformat(), end.isoformat()))
        con.commit()
    finally:
        con.close()
    result = cert.certify(tmp_path, run_name="run", start=start, end=end, execute=True)
    assert result["PASS"] is False and result["error"] == "LEDGER_NOT_FULLY_QUALITY_PASS"
    assert result["provider_network_request_n"] == 0
    assert (staging / "certification_receipt.json").is_file()
    assert not (tmp_path / "meta/asl/daily_facts/published-daily-facts-authority.json").exists()


def test_parquet_date_is_canonicalized_before_reconciliation(tmp_path):
    path = tmp_path / "facts.parquet"
    with duckdb.connect(":memory:") as con:
        con.execute("create table x(symbol varchar, trade_date date)")
        con.execute("insert into x values ('000001.SZ', date '2026-09-09')")
        con.execute("copy x to ? (format parquet)", [str(path)])
    assert cert._rows([path])[0]["trade_date"] == "2026-09-09"


def test_nullable_parquet_nan_is_not_coerced_to_zero_or_malformed():
    assert cert._null_numeric(float("nan")) is True
    assert cert._numeric(float("nan")) is None
    assert cert._numeric("3.25") == 3.25
