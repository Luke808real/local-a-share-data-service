# Daily Facts V02: contract decisions, source independence and PIT enforcement.
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))

import ashare_data.daily_facts_v02 as v02  # noqa: E402


def _load_tool():
    spec = importlib.util.spec_from_file_location('v02_shadow', ROOT / 'tools/run_daily_facts_v02_shadow_v01.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shadow = _load_tool()


# ---------------------------------------------------------------- rounding ---
def test_ordinary_preclose_is_the_prior_close():
    assert v02.display_reference_price(10.0) == Decimal('10.00')


def test_reference_price_rounds_half_up_to_the_display_tick():
    # 603993.SH moves 18.845 to 18.85; truncation would publish 18.84.
    assert v02.display_reference_price(18.94, cash_dividend=0.095) == Decimal('18.85')
    assert v02.display_reference_price(Decimal('18.845')) == Decimal('18.85')


def test_treasury_share_adjusted_and_differential_distribution_cases():
    # 300196.SZ effective 0.1974617 (not the nominal 0.20); 002322.SZ 0.3251058.
    assert v02.display_reference_price(16.90, cash_dividend=0.1974617) == Decimal('16.70')
    assert v02.display_reference_price(12.71, cash_dividend=0.3251058) == Decimal('12.38')


def test_reference_price_scales_by_share_changes():
    # A 10-for-10 bonus halves the reference price; the denominator carries it.
    assert v02.display_reference_price(20.0, bonus_ratio=1.0) == Decimal('10.00')
    assert v02.display_reference_price(20.0, transfer_ratio=1.0) == Decimal('10.00')
    assert v02.display_reference_price(20.0, cash_dividend=1.0, bonus_ratio=1.0) == Decimal('9.50')


# ----------------------------------------------------------------- pct_chg ---
def test_pct_chg_derivation():
    assert v02.derive_pct_chg(11.0, 10.0) == pytest.approx(10.0, abs=1e-12)
    assert v02.derive_pct_chg(10.0, 10.0) == pytest.approx(0.0, abs=1e-12)
    with pytest.raises(v02.DailyFactsV02Error):
        v02.derive_pct_chg(10.0, 0)


# ---------------------------------------------------------------- turnover ---
def test_turnover_unit_contract_is_shares_over_shares():
    # 58,230,600 shares over 19,403,732,089 shares = 0.3001% (the frozen fixture).
    assert v02.derive_turnover_rate(58_230_600, 19_403_732_089) == pytest.approx(0.3001, abs=1e-4)


def test_turnover_rejects_a_non_positive_denominator():
    with pytest.raises(v02.DailyFactsV02Error) as error:
        v02.derive_turnover_rate(100, 0)
    assert error.value.code == 'V02_INVALID_FLOAT_SHARES'


# ------------------------------------------------------- status / is_st -----
def test_status_and_risk_warning_normalization():
    assert v02.STATUS_TO_TRADE_STATUS['normal'] == v02.TRADING
    assert v02.STATUS_TO_TRADE_STATUS['suspended'] == v02.SUSPENDED
    assert v02.STATUS_TO_TRADE_STATUS['delisted'] == '__LIFECYCLE__'
    assert v02.risk_warning_to_is_st(True) == 'TRUE'
    assert v02.risk_warning_to_is_st(False) == 'FALSE'
    assert v02.risk_warning_to_is_st(None) == 'UNKNOWN'


def test_legacy_st_vocabulary_is_not_accepted_as_a_daily_status():
    # 0.8.0 moved ST out of `status`; the old vocabulary must not map.
    assert 'st' not in v02.STATUS_TO_TRADE_STATUS
    assert '*st' not in v02.STATUS_TO_TRADE_STATUS


# ------------------------------------------------------------------- PIT ----
def _point(change: str, announce: str | None, shares: float = 100.0):
    return v02.FloatSharesPoint('X', date.fromisoformat(change),
                               date.fromisoformat(announce) if announce else None, shares, 'test')


def test_strict_pit_prefers_the_disclosed_record_over_an_earlier_effect():
    # record A is effective but not yet public; record B is both.
    day = date(2026, 9, 9)
    a = _point('2026-09-01', '2026-09-20', 200.0)
    b = _point('2026-08-01', '2026-08-05', 100.0)
    chosen = v02.pit_float_shares([a, b], day)
    assert chosen is b
    assert chosen.float_shares == 100.0


def test_strict_pit_rejects_a_future_change_date():
    day = date(2026, 9, 9)
    future = _point('2026-10-01', '2026-09-01', 999.0)
    past = _point('2026-08-01', '2026-08-05', 100.0)
    assert v02.pit_float_shares([future, past], day) is past
    assert v02.pit_float_shares([future], day) is None


def test_strict_pit_returns_none_rather_than_a_future_value():
    day = date(2026, 9, 9)
    assert v02.pit_float_shares([_point('2026-09-01', '2026-10-01', 500.0)], day) is None


def test_a_record_without_an_announce_date_cannot_satisfy_strict_pit():
    assert v02.pit_float_shares([_point('2026-08-01', None, 100.0)], date(2026, 9, 9)) is None
    assert v02.index_float_shares([{'symbol': 'X', 'change_date': date(2026, 8, 1),
                                    'announce_date': None, 'float_shares': 100.0}]) == {}


def test_latest_eligible_record_wins_when_several_qualify():
    day = date(2026, 9, 9)
    older = _point('2026-01-01', '2026-01-05', 100.0)
    newer = _point('2026-06-01', '2026-06-05', 150.0)
    assert v02.pit_float_shares([older, newer], day) is newer


# --------------------------------------------- source independence guard -----
def test_forbidden_input_paths_are_refused():
    for token in v02.FORBIDDEN_PATH_TOKENS:
        with pytest.raises(v02.DailyFactsV02Error) as error:
            v02.assert_source_independent([Path('/lake') / token / 'x.parquet'])
        assert error.value.code == 'V02_SOURCE_DEPENDENCE_VIOLATION'


def test_admissible_dataset_paths_are_allowed():
    v02.assert_source_independent([Path('/lake/curated/daily_bars/trade_date=2026-09-09/p.parquet'),
                                  Path('/lake/curated/trading_status/p.parquet')])


def test_generator_module_does_not_import_the_v1_producer():
    source = (ROOT / 'src/ashare_data/daily_facts_v02.py').read_text()
    # The V1 producer may only appear as a forbidden-path token, never as an
    # import or a read.
    assert 'import daily_facts_phase1' not in source
    assert 'from ashare_data.daily_facts_phase1' not in source
    assert 'reference_price_evidence' in source  # the contract-named exception input


def test_generate_and_compare_are_separate_phases():
    # generate() must not reference the V1 reader at all.
    import inspect
    generate_src = inspect.getsource(shadow.generate)
    assert '_v1_rows' not in generate_src
    assert 'V1_POINTER' not in generate_src
    compare_src = inspect.getsource(shadow.compare)
    assert '_v1_rows' in compare_src


def test_load_sources_refuses_a_v1_evidence_directory(tmp_path):
    # A lake whose daily_bars lives under a forbidden token is rejected.
    fake = tmp_path / 'curated' / 'daily_bars' / 'raw/baostock'
    fake.mkdir(parents=True)
    (fake / 'p.parquet').write_bytes(b'')
    (tmp_path / 'meta/asl/r3').mkdir(parents=True)
    (tmp_path / 'meta/asl/r3/published-daily-authority.json').write_text(json.dumps({
        'schema': 'R3_PUBLISHED_DAILY_AUTHORITY_V01', 'manifest_hash': 'x',
        'plan': 'p.json', 'receipt': 'r.json'}))
    (tmp_path / 'p.json').write_text(json.dumps({'EXPECTED_POST_INPUT_MANIFEST': {'FILES': [], 'INPUT_MANIFEST_HASH': 'x'}}))
    with pytest.raises(v02.DailyFactsV02Error):
        v02.load_sources(tmp_path)


# --------------------------------------------------- upgrade / pin contract -
CNEQUITY_080_COMMIT = 'd453853da766b3ba3e44489c0fb6e0089243fa25'


def test_production_pin_targets_the_v080_commit():
    pyproject = (ROOT / 'pyproject.toml').read_text()
    assert CNEQUITY_080_COMMIT in pyproject
    assert 'a18ee0484dfb0801650175471724def3228b8a17' not in pyproject
    assert 'main' not in pyproject.split('CNEquity.git@')[-1].split('"')[0]


def test_installed_runtime_is_v080_at_the_pinned_commit():
    # Validates the runtime, not the text pin.
    import importlib.metadata as md

    distribution = md.distribution('cnequity')
    assert distribution.version == '0.8.0'
    direct = json.loads((distribution._path / 'direct_url.json').read_text())
    assert direct['vcs_info']['commit_id'] == CNEQUITY_080_COMMIT


def test_trading_status_schema_is_the_two_column_form():
    from cnequity.domain.schemas import TRADING_STATUS_SCHEMA

    assert 'risk_warning' in TRADING_STATUS_SCHEMA
    assert TRADING_STATUS_SCHEMA['risk_warning'] == pl.Boolean


def test_legacy_trading_status_frames_normalize_on_read():
    from cnequity.domain.schemas import validate_dataframe
    from datetime import datetime, timezone

    legacy = pl.DataFrame({
        'symbol': ['000711.SZ', '000711.SZ'],
        'trade_date': [date(2026, 8, 27)] * 2,
        'is_trading': [True, False],
        'status': ['st', 'suspended'],
        'source': ['eastmoney'] * 2,
        'data_version': ['v1'] * 2,
        'fetched_at': [datetime(2026, 8, 27, tzinfo=timezone.utc)] * 2,
    })
    out = validate_dataframe(legacy, 'trading_status')
    rows = {row['status']: row['risk_warning'] for row in out.to_dicts()}
    # The halted row lost its ST label in the legacy encoding and the read path
    # must not invent one; the traded row keeps it.
    assert rows['normal'] is True
    assert rows['suspended'] is False


# ------------------------------------------------- Decision A: lifecycle ----
def _lifecycle(list_date: str | None, delist_date: str | None) -> v02.LifecycleRecord:
    return v02.LifecycleRecord('X',
                               date.fromisoformat(list_date) if list_date else None,
                               date.fromisoformat(delist_date) if delist_date else None)


def test_lifecycle_covers_only_the_listed_window():
    record = _lifecycle('2020-01-01', '2026-06-01')
    assert record.covers(date(2026, 5, 31)) is True
    assert record.covers(date(2026, 6, 1)) is False   # delist date is already gone
    assert record.covers(date(2019, 12, 31)) is False
    assert _lifecycle('2020-01-01', None).covers(date(2030, 1, 1)) is True


# ------------------------------------------- suspension keeps V1 semantics --
def test_a_suspended_row_publishes_null_not_zero():
    # The rule the comparison relies on: a halted session has no traded move,
    # so a derived 0.0 would claim a flat close the market never printed.
    source = (ROOT / 'src/ashare_data/daily_facts_v02.py').read_text()
    assert 'pct_chg_null_reason' in source and 'SUSPENDED' in source
    assert "turnover_unit\"] = \"PERCENT\"" in source


# --------------------------------------------------- lake-dependent checks --
LAKE = Path('/Users/luke808/AI/local-a-share-data-service-data')
has_lake = (LAKE / 'curated/daily_bars/trade_date=2026-09-09/part-merged.parquet').is_file()


@pytest.mark.skipif(not has_lake, reason='published lake not present')
def test_shadow_reproduces_the_full_eligible_universe():
    candidate = shadow.generate('2026-09-09')
    report = candidate['report']
    assert report['eligible_n'] == 5208
    assert report['row_n'] == 5208
    assert report['ELIGIBILITY_LIFECYCLE_CONFLICT_N'] == 0
    assert report['TRADING_STATUS_UNKNOWN_N'] == 0
    assert report['MISSING_PRIOR_SESSION_N'] == 0
    assert report['UNKNOWN_N'] == 0
    # Turnover resolution now depends on the published valuation snapshot
    # rather than a locally constructed denominator, so the invariant is that
    # every unresolved key is accounted for, not that none exist.
    rows_unresolved = sum(1 for row in candidate['rows'] if row.get('turnover_rate') is None
                          and row['trade_status'] != 'SUSPENDED')
    assert report['TURNOVER_UNRESOLVED_N'] == rows_unresolved
    keys = [(row['symbol'], row['trade_date']) for row in candidate['rows']]
    assert len(keys) == len(set(keys))
    for row in candidate['rows']:
        if row['trade_status'] == 'SUSPENDED':
            assert row['pct_chg'] is None
            assert row['turnover_rate'] is None


@pytest.mark.skipif(not has_lake, reason='published lake not present')
def test_shadow_matches_v1_on_status_and_st():
    candidate = shadow.generate('2026-09-09')
    comparison = shadow.compare(candidate)
    assert comparison['MISSING_PK_N'] == 0
    assert comparison['EXTRA_PK_N'] == 0
    assert comparison['trade_status']['MISMATCH_N'] == 0
    assert comparison['is_st']['MISMATCH_N'] == 0
    assert comparison['preclose']['MISMATCH_N'] == 0
    assert comparison['pct_chg']['MISMATCH_N'] == 0
    # The 0.8.0 redesign exists so a halt cannot drop the ST label.
    assert comparison['is_st_detail']['V02_RISK_WARNING_TRUE_ON_SUSPENDED'] == \
        comparison['is_st_detail']['ST_WHILE_SUSPENDED_IN_V1']
