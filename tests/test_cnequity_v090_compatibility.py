"""Upgrade regression for the native failed -> running -> success lifecycle."""
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


def test_v090_partial_stage_retry_lifecycle_and_cross_run_reuse(tmp_path):
    from cnequity.orchestrator.manifest import Manifest
    from cnequity.orchestrator.worker_pool import DailyBarCoverageError, _failed_symbols_for_error, _require_daily_bar_symbol_coverage, _stage_daily_bar_rows
    from cnequity.steps.bars import _reuse_successful_daily_bars
    from cnequity.config import load_config
    root = tmp_path / "lake"; root.mkdir()
    config = tmp_path / "cne.toml"
    config.write_text('[data]\nroot = "'+str(root)+'"\n[universe]\ndefault = "all_a"\n')
    cfg = load_config(config); manifest = Manifest(cfg.manifest_path)
    calendar = cfg.curated_root / "trading_calendar" / "trade_date=2026"
    calendar.mkdir(parents=True)
    pl.DataFrame({"trade_date": [date(2026, 9, 8)], "is_trading": [True], "source": ["fixture"],
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
    # A terminal failure cannot be resurrected by a delayed success callback.
    manifest.finish_batch(run1, batch, "success")
    assert manifest.get_batch(run1, batch)["status"] == "failed"
    manifest.start_batch(run1, batch, batch, "daily_bars", symbols, "2026-09-08", "2026-09-08")
    manifest.finish_batch(run1, batch, "success")
    assert manifest.get_batch(run1, batch)["status"] == "success"
    run2=manifest.start_run("fixture-run2")
    reused=_reuse_successful_daily_bars(cfg,run2,symbols,date(2026,9,8),date(2026,9,8))
    assert reused == set(symbols)


def test_v090_native_schema_provenance_and_strict_missing_fields(tmp_path):
    from cnequity.adapters.tdx_protocol.client import normalize_with_source
    from cnequity.domain.schemas import validate_dataframe
    from cnequity.storage.parquet import StagingWriter
    from cnequity.storage.atomic import write_parquet_atomic
    frame=normalize_with_source(_rows(['600519.SH']).drop('source','data_version','fetched_at'),dataset='daily_bars')
    valid=validate_dataframe(frame,'daily_bars')
    assert set(valid['source'])=={'tdx_protocol'} and set(valid['data_version'])=={'v2'}
    assert valid['volume'][0]==1  # already shares, must not multiply twice
    import pytest
    with pytest.raises((ValueError,TypeError)):
        validate_dataframe(valid.drop('volume'),'daily_bars')
    StagingWriter(tmp_path/'staging').write_batch('daily_bars','r','b',valid)
    staged=pl.read_parquet(tmp_path/'staging/daily_bars/run_id=r/part-b.parquet')
    assert staged.equals(valid)
    write_parquet_atomic(tmp_path/'atomic.parquet',valid)
    assert pl.read_parquet(tmp_path/'atomic.parquet').equals(valid)


def test_v090_config_retains_limits_and_disallows_implicit_routing_changes():
    from pathlib import Path
    from cnequity.config import load_config
    from cnequity.config.loader import validate_config
    cfg=load_config(Path(__file__).resolve().parents[1]/'config/cnequity.toml')
    assert validate_config(cfg)==[]
    spec=cfg.tdx_rate_limit_spec()
    assert spec.min_interval==0.1 and spec.concurrency_limit==1 and spec.lock_timeout==15
    assert not cfg.eastmoney_direct_fallback
    assert not cfg.ths_official_backfill_enabled
    assert not cfg.tdx_allow_mock


def test_v090_metadata_break_does_not_change_required_core_storage_shapes():
    from pathlib import Path
    from cnequity.domain.contracts import build_contract,validate_contract
    previous=json.loads((Path(__file__).resolve().parents[1]/'patches/cnequity/contracts_registry_after_patch.json').read_text())
    new=build_contract()
    assert validate_contract(new)==[]
    for name in ('daily_bars','instruments','trading_status','trading_calendar','corporate_actions','valuation_metrics','share_structure'):
        old_ds=previous['datasets'][name];new_ds=new['datasets'][name]
        for key in ('columns','primary_key','unit_contract','pit','pit_grade'):
            assert old_ds[key]==new_ds[key],(name,key)
        if old_ds['pit_quality']=='strict' and not old_ds['pit']:
            assert new_ds['pit_quality']=='not_applicable'
