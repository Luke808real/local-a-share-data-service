import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'tools/audits/query_incremental_publication_audit_v01.py'
spec = importlib.util.spec_from_file_location('incremental_audit', PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def batch(symbols, start='2026-08-18', end='2026-08-18'):
    return dict(symbols_json=json.dumps(symbols), window_start=start, window_end=end)


def test_requested_vs_observed_is_not_suspension_or_missing_bar_proof():
    requested = audit.requested_membership([batch(['000001.SZ', '000002.SZ'])], '2026-08-18')
    result = audit.coverage(requested, {'000001.SZ', '600000.SH'}, '2026-08-18')
    assert result['requested_not_observed_keys'] == [{'symbol': '000002.SZ', 'trade_date': '2026-08-18'}]
    assert result['observed_not_requested_symbols'] == ['600000.SH']
    assert result['suspension_asserted'] is False
    assert result['true_missing_bars_asserted'] is False
    assert result['classification'] == 'REQUESTED_NOT_OBSERVED_REASON_UNESTABLISHED'


@pytest.mark.parametrize('batches', [
    [batch(['000001.SZ', '000001.SZ'])],
    [batch(['000001.SZ']), batch(['000001.SZ'])],
    [batch(['000001.SZ'], end='2026-08-19')],
])
def test_reject_duplicate_or_mixed_requested_scope(batches):
    with pytest.raises(ValueError):
        audit.requested_membership(batches, '2026-08-18')


def test_wal_refused_before_database_open(tmp_path):
    meta = tmp_path / 'meta'
    meta.mkdir()
    (meta / 'manifest.db-wal').touch()
    with pytest.raises(ValueError, match='sidecar present'):
        audit.audit(tmp_path)


def test_numeric_gates_do_not_accept_nan_or_negative_values():
    from datetime import date, datetime, timezone
    import pyarrow as pa
    row = dict(symbol='000001.SZ', trade_date=date(2026, 8, 18), open=2.,
               high=3., low=1., close=2., volume=-1, amount=-2.,
               source='tdx_protocol', data_version='v2', fetched_at=datetime.now(timezone.utc))
    invalid = dict(row, close=float('nan'))
    q = audit.file_quality(pa.Table.from_pylist([row, invalid], schema=audit.SCHEMA), '2026-08-18')
    assert q['duplicate_keys'] == 1
    assert q['nonfinite_rows'] == 1
    assert q['negative_volume_rows'] >= 1
    assert q['negative_amount_rows'] >= 1
