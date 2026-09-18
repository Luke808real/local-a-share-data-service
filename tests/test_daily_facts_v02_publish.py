from types import SimpleNamespace
import pytest
from ashare_data import daily_facts_v02 as v
from ashare_data import daily_facts_v02_publish as p

DAY = '2026-09-17'
PRIOR = '2026-09-16'
KEY = ('X', DAY)

@pytest.fixture
def sample(monkeypatch):
    bundle = SimpleNamespace(trading_status={KEY: {'status': 'normal', 'risk_warning': False, 'source': 'eastmoney'}},
        corporate_actions={}, reference_price_evidence={}, turnover_rate={KEY: 3.5}, valuation_metrics={},
        r3_manifest_hash='test')
    bars = {'X': {'close': 11.0, 'volume': 100.0, 'amount': 1100.0}}
    lookup = lambda b,d: bars if d == DAY else {'X': {'close': 10.0}}
    monkeypatch.setattr(v, 'r3_bars', lookup)
    monkeypatch.setattr(p, 'r3_bars', lookup)
    rows, _ = v.build_shadow_rows(bundle, DAY, PRIOR)
    return bundle, bars, rows

def cert(sample):
    bundle, bars, rows = sample
    return p.certify_rows(rows, {'X'}, DAY, bundle, bars)

def test_complete_scope_passes(sample):
    assert cert(sample)['PASS']

@pytest.mark.parametrize('field,value,counter', [
    ('preclose', 9.9, 'PRECLOSE_MISMATCH_N'),
    ('pct_chg', 5.0, 'PCT_CHG_MISMATCH_N'),
    ('turnover_rate', 0.0, 'TURNOVER_MISMATCH_N'),
    ('is_st', 'TRUE', 'STATUS_MISMATCH_N'),
    ('trade_status', 'SUSPENDED', 'TRADE_STATUS_CONFLICT_N'),
    ('preclose', float('nan'), 'PRECLOSE_UNRESOLVED_N')])
def test_corrupted_fields_fail(sample, field, value, counter):
    sample[2][0][field] = value
    result = cert(sample)
    assert not result['PASS'] and result['counters'][counter] > 0

def test_duplicate_missing_extra_keys_fail(sample):
    bundle, bars, rows = sample
    result = p.certify_rows(rows+rows, {'Y'}, DAY, bundle, bars)
    for key in ('MISSING_PK_N','EXTRA_PK_N','DUPLICATE_PK_N'):
        assert result['counters'][key] == 1

def test_known_exdate_without_official_evidence_blocks(sample):
    bundle, bars, _ = sample
    bundle.corporate_actions[KEY] = {'cash_dividend': .2}
    rows, _ = v.build_shadow_rows(bundle, DAY, PRIOR)
    assert rows[0]['preclose'] is None
    assert 'CORPORATE_ACTION_EVIDENCE_MISSING' in rows[0]['blockers']
    assert not p.certify_rows(rows, {'X'}, DAY, bundle, bars)['PASS']

def test_official_adjustment_is_recomputed_not_just_hash_checked(sample):
    bundle, bars, _ = sample
    bundle.corporate_actions[KEY] = {'cash_dividend': .2}
    bundle.reference_price_evidence[KEY] = SimpleNamespace(effective_cash_dividend_per_share=.2,
        source_hash='verified', announcement_id='official')
    rows, _ = v.build_shadow_rows(bundle, DAY, PRIOR)
    assert rows[0]['preclose'] == 9.8
    assert p.certify_rows(rows, {'X'}, DAY, bundle, bars)['PASS']
    rows[0]['preclose'] = 10.0
    assert p.certify_rows(rows, {'X'}, DAY, bundle, bars)['counters']['PRECLOSE_MISMATCH_N'] == 1


def test_sparse_late_reference_metadata_survives_parquet(sample, tmp_path):
    import polars as pl
    rows = [dict(sample[2][0], symbol=str(i)) for i in range(101)]
    rows[-1].update(reference_price_source_hash='official-hash', reference_price_announcement_id='id')
    frame = p.publication_frame(rows, 'r3-hash')
    path = tmp_path / 'facts.parquet'
    frame.write_parquet(path)
    loaded = pl.read_parquet(path)
    assert loaded['reference_price_source_hash'][-1] == 'official-hash'
    assert loaded['reference_price_source_hash'].null_count() == 100
    assert loaded.equals(frame)
    assert loaded.schema["blockers"] == pl.String
    import duckdb
    with duckdb.connect(":memory:") as con:
        returned = con.execute("select * from read_parquet(?)", [str(path)]).fetchall()
        assert len(returned) == 101
