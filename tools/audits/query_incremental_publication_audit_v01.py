"""Bounded offline evidence audit; never certifies suspension or publication.

Reads nine daily partitions and read-only immutable SQLite receipts. Only the
explicit --output report is written; baseline historical parquet is not read.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path('/Users/luke808/AI/local-a-share-data-service-data')
DATES = tuple(f'2026-08-{d}' for d in ('18', '19', '20', '21', '24', '25', '26', '27', '28'))
BASELINE_HASH = 'dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045'
SCHEMA = pa.schema([
    ('symbol', pa.string()), ('trade_date', pa.date32()),
    *[(k, pa.float64()) for k in ('open', 'high', 'low', 'close')],
    ('volume', pa.int64()), ('amount', pa.float64()),
    ('source', pa.string()), ('data_version', pa.string()),
    ('fetched_at', pa.timestamp('us', tz='UTC')),
])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def requested_membership(batches, day):
    """Reject overlapping scopes, duplicate membership and mixed windows."""
    members = set()
    for b in batches:
        require(b['window_start'] == b['window_end'] == day, 'mixed batch window')
        symbols = json.loads(b['symbols_json'])
        require(isinstance(symbols, list) and symbols and
                all(isinstance(s, str) and s for s in symbols), 'invalid symbols')
        require(len(set(symbols)) == len(symbols), 'duplicate requested membership')
        require(not members.intersection(symbols), 'duplicate requested membership')
        members.update(symbols)
    require(members, 'empty requested membership')
    return members


def coverage(requested, observed, day):
    return {
        'requested_n': len(requested), 'observed_symbol_n': len(observed),
        'requested_not_observed_keys': [
            {'symbol': s, 'trade_date': day} for s in sorted(requested - observed)
        ],
        'observed_not_requested_symbols': sorted(observed - requested),
        'classification': 'REQUESTED_NOT_OBSERVED_REASON_UNESTABLISHED',
        'true_missing_bars_asserted': False, 'suspension_asserted': False,
    }


def file_quality(table, day):
    gates = Counter({k: 0 for k in (
        'null_rows', 'duplicate_keys', 'date_mismatch_rows', 'nonfinite_rows',
        'negative_volume_rows', 'negative_amount_rows', 'ohlc_bad_rows',
        'nonpositive_volume_rows', 'provenance_bad_rows', 'symbol_bad_rows')})
    seen = set()
    for r in table.to_pylist():
        key = (r['symbol'], r['trade_date'])
        gates['duplicate_keys'] += key in seen
        seen.add(key)
        gates['null_rows'] += any(v is None for v in r.values())
        gates['date_mismatch_rows'] += r['trade_date'] != date.fromisoformat(day)
        values = [r[k] for k in ('open', 'high', 'low', 'close', 'volume', 'amount')]
        finite = all(v is not None and math.isfinite(v) for v in values)
        gates['nonfinite_rows'] += not finite
        for field in ('volume', 'amount'):
            v = r[field]
            gates[f'negative_{field}_rows'] += v is not None and math.isfinite(v) and v < 0
        v = r['volume']
        gates['nonpositive_volume_rows'] += v is not None and math.isfinite(v) and v <= 0
        if finite:
            gates['ohlc_bad_rows'] += not (r['low'] <= r['open'] <= r['high'] and
                                          r['low'] <= r['close'] <= r['high'])
        gates['provenance_bad_rows'] += r['source'] != 'tdx_protocol' or r['data_version'] != 'v2'
        s = r['symbol']
        gates['symbol_bad_rows'] += not (isinstance(s, str) and len(s) == 9 and
                                       s[:6].isdigit() and s[6:] in ('.SH', '.SZ'))
    # Arrow string and large_string are equivalent textual values, with
    # different offset widths. CNEquity/Polars emits large_string parquet.
    normalized_schema = pa.schema([
        (field.name, pa.string() if pa.types.is_large_string(field.type) else field.type)
        for field in table.schema
    ])
    return {'schema_matches': normalized_schema.equals(SCHEMA, check_metadata=False),
            **dict(gates)}


def audit(root=ROOT):
    root = Path(root)
    db = root / 'meta/manifest.db'
    sidecars = [Path(str(db) + suffix) for suffix in ('-wal', '-shm', '-journal')]

    def no_sidecars():
        require(not any(p.exists() for p in sidecars), 'SQLite sidecar present: refuse immutable audit')

    no_sidecars()
    initial_db = snapshot(db)
    tx = root / 'staging/r3_proven_missing_4key_repair_v01/transaction'
    evidence = {}
    for name in ('promotion_plan.json', 'promotion_receipt.json'):
        path = tx / name
        data = path.read_bytes()
        evidence[name] = (path, data, json.loads(data))
    manifest = evidence['promotion_plan.json'][2]['EXPECTED_POST_INPUT_MANIFEST']
    files = manifest['FILES']
    require(len(files) == manifest['INPUT_FILE_N'] == 2580, 'baseline file count')
    paths = [r['relative_path'] for r in files]
    require(paths == sorted(set(paths)), 'baseline paths not unique/sorted')
    digest = sha(json.dumps(files, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode())
    require(digest == manifest['INPUT_MANIFEST_HASH'] == BASELINE_HASH, 'baseline FILES hash mismatch')
    receipt = evidence['promotion_receipt.json'][2]
    require(receipt['STATE'] == 'COMMITTED' and receipt['CANONICAL_WRITE_EXECUTED'] is True,
            'baseline not committed')
    require(receipt['POST_INPUT_FILE_N'] == receipt['EXPECTED_POST_INPUT_FILE_N'] == 2580 and
            receipt['POST_INPUT_MANIFEST_HASH'] == receipt['EXPECTED_POST_INPUT_MANIFEST_HASH'] == digest,
            'baseline receipt mismatch')
    days = []
    conn = sqlite3.connect(db.as_uri() + '?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        runs = list(conn.execute("""SELECT * FROM ingestion_runs WHERE run_id IN
            (SELECT DISTINCT run_id FROM ingestion_batches WHERE dataset='daily_bars'
             AND window_start <= ? AND window_end >= ?)""", (DATES[-1], DATES[0])))
        require(len(runs) == 9, 'expected exactly nine overlapping daily runs')
        by_day = {}
        for run in runs:
            meta = json.loads(run['metadata_json'])
            day = meta.get('window_start')
            require(run['job_name'] == 'r3_daily_incremental' and day in DATES and
                    meta.get('window_end') == day and day not in by_day, 'mixed or duplicate run scope')
            by_day[day] = (run, meta)
        for day in DATES:
            run, meta = by_day[day]
            batches = list(conn.execute("SELECT * FROM ingestion_batches WHERE run_id=? AND dataset='daily_bars' ORDER BY batch_id", (run['run_id'],)))
            requested = requested_membership(batches, day)
            partition = root / 'curated/daily_bars' / ('trade_date=' + day)
            parquet_files = sorted(partition.glob('*.parquet'))
            require(len(parquet_files) == 1 and parquet_files[0].name == 'part-merged.parquet', 'unexpected partition file inventory')
            path = parquet_files[0]
            before = snapshot(path)
            data = path.read_bytes()
            table = pq.ParquetFile(pa.BufferReader(data)).read()
            require(before == snapshot(path), 'partition changed during read')
            require(set(table.column_names) == set(SCHEMA.names), 'unexpected columns')
            observed = set(table['symbol'].to_pylist())
            quality = file_quality(table, day)
            cov = coverage(requested, observed, day)
            days.append({
                'date': day, 'run_id': run['run_id'], 'run_status': run['status'],
                'started_at': run['started_at'], 'finished_at': run['finished_at'],
                'run_rows_written': run['rows_written'], 'run_metadata': meta,
                'batch_status_counts': dict(Counter(b['status'] for b in batches)),
                'batch_rows_written': sum(b['rows_written'] for b in batches),
                'receipt_row_counts_match_file': run['rows_written'] ==
                    sum(b['rows_written'] for b in batches) == table.num_rows,
                'short_batches': [{k: b[k] for k in ('batch_id', 'status', 'rows_written', 'retry_count', 'error_message')} |
                                  {'requested_n': len(json.loads(b['symbols_json']))}
                                  for b in batches if b['rows_written'] < len(json.loads(b['symbols_json']))],
                'file': str(path), 'file_size': len(data), 'sha256': sha(data),
                'rows': table.num_rows, 'quality': quality, **cov,
                'structural_quality_pass': quality['schema_matches'] and not any(
                    v for k, v in quality.items() if k != 'schema_matches'),
            })
    finally:
        conn.close()
        no_sidecars()
        require(snapshot(db) == initial_db, 'manifest DB changed during audit')
    for path, data, _ in evidence.values():
        require(path.read_bytes() == data, 'baseline evidence changed during audit')
    for d in days:
        require(sha(Path(d['file']).read_bytes()) == d['sha256'], 'incremental file drift during audit')
    no_sidecars()
    require(snapshot(db) == initial_db, 'manifest DB changed during audit')
    return {
        'audit': 'QUERY_INCREMENTAL_PUBLICATION_AUDIT_V01',
        'verdict': 'NOT_CERTIFIED_FOR_PUBLICATION',
        'scope': {'dates': list(DATES), 'data_root': str(root), 'network_provider_calls': 0,
                  'canonical_writes': 0, 'historical_parquet_rows_read': 0},
        'sqlite': {'path': str(db), 'uri_options': 'mode=ro&immutable=1',
                   'no_sidecars_before_and_after': True, 'stat_unchanged': True},
        'baseline': {'file_n': 2580, 'files_hash': digest,
                     'files_json_pointer': '/EXPECTED_POST_INPUT_MANIFEST/FILES',
                     'evidence': {name: {'path': str(p), 'sha256': sha(data)}
                                  for name, (p, data, _) in evidence.items()},
                     'receipt_state': receipt['STATE'],
                     'all_file_byte_validation': 'NOT_RUN_QUERY_CORE_RESPONSIBILITY'},
        'rows': sum(d['rows'] for d in days),
        'requested_not_observed_n': sum(len(d['requested_not_observed_keys']) for d in days),
        'missing_semantics': 'Requested membership minus observed rows; not proof of missing traded bars or suspension.',
        'coverage_status': 'UNKNOWN', 'publication_gate_pass': False,
        'days': days,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    expected = Path(__file__).resolve().parents[2] / 'reports/implementation/QUERY_INCREMENTAL_PUBLICATION_AUDIT_V01.json'
    require(args.output.resolve() == expected, 'output must be the owned report path')
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n')
    print(json.dumps({'verdict': report['verdict'], 'rows': report['rows'],
                      'requested_not_observed_n': report['requested_not_observed_n']}))


if __name__ == '__main__':
    main()
