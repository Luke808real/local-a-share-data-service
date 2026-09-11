# CNEquity valuation_metrics turnover_rate additive field (EastMoney clist f8).
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

LAKE = Path('/Users/luke808/AI/local-a-share-data-service-data')
has_lake = (LAKE / 'curated/daily_bars/trade_date=2026-09-09/part-merged.parquet').is_file()


# ------------------------------------------------------------ f8 parsing ----
def test_f8_is_requested_by_the_valuation_adapter():
    from cnequity.adapters.eastmoney.valuation import _VALUATION_FIELDS

    fields = _VALUATION_FIELDS.split(',')
    assert 'f8' in fields, 'the clist request must carry the turnover field'
    # The pre-existing valuation fields must survive the addition.
    for existing in ('f12', 'f13', 'f9', 'f23', 'f45', 'f20', 'f21'):
        assert existing in fields


def test_f8_parses_a_number_and_becomes_percent_unchanged():
    from cnequity.adapters.eastmoney.common import _to_float

    # f8 is already in percent points; it must not be divided by 100.
    assert _to_float(2.35) == 2.35
    assert _to_float('2.35') == 2.35


def test_f8_null_and_invalid_cases_stay_null_not_zero():
    from cnequity.adapters.eastmoney.common import _to_float

    for bad in (None, '', '-', '--', 'n/a', float('nan'), float('inf')):
        assert _to_float(bad) is None, f'{bad!r} must not become a value'
    # A genuine observed zero is preserved, which is why this distinction matters.
    assert _to_float(0) == 0.0
    assert _to_float('0.00') == 0.0


def test_valuation_row_mapping_includes_turnover_rate():
    from cnequity.adapters.eastmoney.valuation import fetch_valuation_metrics
    import inspect

    source = inspect.getsource(fetch_valuation_metrics)
    assert '"turnover_rate"' in source
    assert "item.get(\"f8\")" in source


# --------------------------------------------------------------- schema -----
def test_schema_has_turnover_rate_as_float64():
    from cnequity.domain.schemas import VALUATION_METRICS_SCHEMA

    assert VALUATION_METRICS_SCHEMA['turnover_rate'] == pl.Float64


def test_existing_valuation_columns_are_unchanged():
    from cnequity.domain.schemas import VALUATION_METRICS_SCHEMA

    for column in ('symbol', 'trade_date', 'pe_ttm', 'pb', 'ps_ttm', 'total_mv', 'float_mv'):
        assert column in VALUATION_METRICS_SCHEMA
    assert VALUATION_METRICS_SCHEMA['pe_ttm'] == pl.Float64
    assert VALUATION_METRICS_SCHEMA['float_mv'] == pl.Float64


def test_primary_key_is_unchanged_by_the_addition():
    from cnequity.domain.schemas import PRIMARY_KEYS

    assert PRIMARY_KEYS['valuation_metrics'] == ['symbol', 'trade_date']


def test_turnover_unit_contract_is_percent():
    from cnequity.domain.datasets import DATASETS

    assert DATASETS['valuation_metrics'].unit_contract['turnover_rate'] == 'PERCENT'


def test_the_new_column_is_additively_typed():
    from cnequity.domain.datasets import DATASETS

    # An additive column must not change the dataset's compatibility class.
    assert DATASETS['valuation_metrics'].compatibility == 'additive'


# ------------------------------------------------- contract / no new cost ---
def test_contract_vintage_still_validates_the_registry():
    from cnequity.domain.contracts import validate_contract

    errors = validate_contract(None)
    assert errors == [], errors


def test_adapter_uses_one_batched_page_walk_and_no_per_symbol_call():
    import inspect
    from cnequity.adapters.eastmoney import valuation

    source = inspect.getsource(valuation)
    # The only acquisition call is the bulk page walker.
    assert 'fetch_clist_pages' in source
    assert 'query_history_k_data_plus' not in source
    assert 'fetch_per_symbol' not in source


# --------------------------------------------------------- V02 integration --
def test_v02_turnover_source_is_the_cnEquity_dataset():
    import ashare_data.daily_facts_v02 as v02

    assert v02.RULE_TURNOVER == 'CNEQUITY_VALUATION_METRICS_TURNOVER_V02'
    # The superseded derivation is retained for audit interpretation only.
    assert v02.RULE_TURNOVER_DERIVED_AUDIT_ONLY == 'VOLUME_OVER_PIT_FLOAT_SHARES_V02'
    assert v02.RULE_TURNOVER != v02.RULE_TURNOVER_DERIVED_AUDIT_ONLY


def test_source_bundle_carries_the_published_turnover():
    import dataclasses
    import ashare_data.daily_facts_v02 as v02

    names = {field.name for field in dataclasses.fields(v02.SourceBundle)}
    assert 'turnover_rate' in names


def test_null_turnover_is_absent_rather_than_zero():
    import ashare_data.daily_facts_v02 as v02

    # A row whose turnover is null must not enter the mapping at all, so a
    # downstream lookup cannot confuse "no value" with "traded nothing".
    import inspect
    source = inspect.getsource(v02.load_sources)
    assert 'turnover_value is not None' in source


def test_missing_published_turnover_blocks_rather_than_defaulting():
    import inspect
    import ashare_data.daily_facts_v02 as v02

    source = inspect.getsource(v02.build_shadow_rows)
    assert 'TURNOVER_UNRESOLVED' in source
    assert 'PROVIDER_VALUE_ABSENT' in source


# ------------------------------------------------------- lake-backed checks --
@pytest.mark.skipif(not has_lake, reason='published lake not present')
def test_suspended_rows_keep_null_turnover():
    import importlib.util as iu

    spec = iu.spec_from_file_location('shadow_tool', ROOT / 'tools/run_daily_facts_v02_shadow_v01.py')
    tool = iu.module_from_spec(spec)
    sys.modules[spec.name] = tool
    spec.loader.exec_module(tool)
    candidate = tool.generate('2026-09-09')
    suspended = [row for row in candidate['rows'] if row['trade_status'] == 'SUSPENDED']
    assert len(suspended) == 10
    for row in suspended:
        assert row['turnover_rate'] is None
        assert row['turnover_null_reason'] == 'SUSPENDED'
        assert row['pct_chg'] is None


@pytest.mark.skipif(not has_lake, reason='published lake not present')
def test_four_fields_remain_equivalent_after_the_turnover_change():
    import importlib.util as iu

    spec = iu.spec_from_file_location('shadow_tool2', ROOT / 'tools/run_daily_facts_v02_shadow_v01.py')
    tool = iu.module_from_spec(spec)
    sys.modules[spec.name] = tool
    spec.loader.exec_module(tool)
    comparison = tool.compare(tool.generate('2026-09-09'))
    assert comparison['EXPECTED_N'] == 5208
    assert comparison['V02_ROW_N'] == 5208
    assert comparison['preclose']['MISMATCH_N'] == 0
    assert comparison['pct_chg']['MISMATCH_N'] == 0
    assert comparison['trade_status']['MISMATCH_N'] == 0
    assert comparison['is_st']['MISMATCH_N'] == 0
    # The 0.8.0 redesign still protects the ST label on a halted session.
    assert comparison['is_st_detail']['V02_RISK_WARNING_TRUE_ON_SUSPENDED'] == \
        comparison['is_st_detail']['ST_WHILE_SUSPENDED_IN_V1']


@pytest.mark.skipif(not has_lake, reason='published lake not present')
def test_valuation_metrics_has_no_stale_pre_patch_partition():
    files = sorted((LAKE / 'curated/valuation_metrics').rglob('*.parquet'))
    for path in files:
        frame = pl.read_parquet(str(path))
        # Any partition present must carry the new column, so a pre-patch
        # file can never sit in the curated tree and fail validation on read.
        assert 'turnover_rate' in frame.columns, path

