"""Executable compatibility gate for CNEquity 0.8 daily partial recovery."""
from datetime import date, datetime, timezone
import json

import polars as pl
import pytest


def _rows(symbols):
    return pl.DataFrame({"symbol": symbols, "trade_date": [date(2026, 9, 8)] * len(symbols),
                         "open": [1.0] * len(symbols), "high": [1.0] * len(symbols),
                         "low": [1.0] * len(symbols), "close": [1.0] * len(symbols),
                         "volume": [1] * len(symbols), "amount": [1.0] * len(symbols),
                         "source": ["tdx_protocol"] * len(symbols), "data_version": ["v1"] * len(symbols),
                         "fetched_at": [datetime(2026, 9, 8, tzinfo=timezone.utc)] * len(symbols)})


def test_v080_partial_stage_narrow_retry_and_cross_run_reuse(tmp_path):
    from cnequity.orchestrator.manifest import Manifest
    from cnequity.orchestrator.worker_pool import DailyBarCoverageError, _failed_symbols_for_error, _require_daily_bar_symbol_coverage, _stage_daily_bar_rows
    from cnequity.steps.bars import _reuse_successful_daily_bars
    from cnequity.config import load_config
    root = tmp_path / "lake"; root.mkdir()
    config = tmp_path / "cne.toml"
    config.write_text('[data]\nroot = "'+str(root)+'"\n[universe]\ndefault = "all_a"\n')
    cfg = load_config(config); manifest = Manifest(cfg.manifest_path)
    calendar = cfg.derived_root / "trading_calendar" / "trade_date=2026"
    calendar.mkdir(parents=True)
    pl.DataFrame({"trade_date": [20260908], "is_trading": [True], "source": ["fixture"],
                  "data_version": ["v1"], "fetched_at": [datetime(2026, 9, 8, tzinfo=timezone.utc)]}).write_parquet(calendar / "part.parquet")
    run1, batch, symbols = manifest.start_run("fixture-run1"), "batch1", ["A", "B", "C", "D"]
    manifest.start_batch(run1, batch, batch, "daily_bars", symbols, "2026-09-08", "2026-09-08")
    returned = _rows(["A", "B", "D"])
    try:
        _require_daily_bar_symbol_coverage(returned, symbols)
    except DailyBarCoverageError as exc:
        assert list(exc.missing_symbols) == ["C"]
        _stage_daily_bar_rows(cfg.staging_root, run1, batch, returned)
        manifest.finish_batch(run1, batch, "failed", error_message=json.dumps({"failed_symbols": _failed_symbols_for_error(exc, symbols)}))
    staged = pl.read_parquet(cfg.staging_root / "daily_bars" / f"run_id={run1}" / f"part-{batch}.parquet")
    assert sorted(staged["symbol"].to_list()) == ["A", "B", "D"]
    assert json.loads(manifest.get_batch(run1,batch)["symbols_json"]) == symbols
    _stage_daily_bar_rows(cfg.staging_root, run1, batch, _rows(["C"]))
    staged = pl.read_parquet(cfg.staging_root / "daily_bars" / f"run_id={run1}" / f"part-{batch}.parquet")
    assert sorted(staged["symbol"].to_list()) == symbols and staged.height == 4
    # A failed batch is terminal until explicitly restarted for retry. A late
    # success callback must not erase a recorded failure (both 0.8 and 0.9).
    manifest.finish_batch(run1, batch, "success")
    assert manifest.get_batch(run1, batch)["status"] == "failed"
    manifest.start_batch(run1, batch, batch, "daily_bars", symbols, "2026-09-08", "2026-09-08")
    manifest.finish_batch(run1, batch, "success")
    assert manifest.get_batch(run1, batch)["status"] == "success"
    run2=manifest.start_run("fixture-run2")
    reused=_reuse_successful_daily_bars(cfg,run2,symbols,date(2026,9,8),date(2026,9,8))
    assert reused == set(symbols)
