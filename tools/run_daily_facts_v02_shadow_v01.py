#!/usr/bin/env python3
# Daily Facts V02 shadow generation and V1 comparison for one date.
#
# Phase separation is enforced by construction: generate() reads only admissible
# V02 inputs and writes a frozen candidate artifact, while compare() is a
# separate function that opens the V1 golden facts. The generator cannot see
# the answer it is later compared against.
#
# Read-only with respect to every production authority. No pointer is written.
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))

from ashare_data.daily_facts_v02 import (  # noqa: E402
    RULE_PRECLOSE_REFERENCE_PRICE,
    SCHEMA,
    SUSPENDED,
    UNKNOWN,
    LifecycleRecord,
    build_shadow_rows,
    canonical_sha,
    load_sources,
    prior_session,
)

DATA_ROOT = Path('/Users/luke808/AI/local-a-share-data-service-data')
EVIDENCE = Path('staging/daily_facts_phase1_20260909_v01/reference_price_evidence.json')
V1_POINTER = Path('meta/asl/daily_facts/published-daily-facts-authority.json')
CALENDAR_GLOB = 'curated/trading_calendar'
#: Prior sessions examined when the immediately preceding one holds no row.
WALK_BACK_SESSIONS = 12

# preclose and turnover are published at display precision, so exact equality
# is the right test for them; pct_chg keeps the frozen numeric tolerance.
COMPUTE_TOLERANCE = 1e-9
PCT_CHG_TOLERANCE = 0.02


class ShadowError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def load_lifecycle(root: Path) -> dict[str, LifecycleRecord]:
    # Formal listed lifecycle per symbol, from the instruments catalog.
    import polars as pl

    files = sorted((root / 'curated/instruments').rglob('*.parquet'))
    frame = pl.read_parquet([str(path) for path in files])
    records: dict[str, LifecycleRecord] = {}
    for row in frame.to_dicts():
        symbol = str(row.get('symbol') or '')
        if not symbol:
            continue
        records[symbol] = LifecycleRecord(symbol, _as_date(row.get('list_date')), _as_date(row.get('delist_date')))
    return records


def load_calendar(root: Path) -> list[date]:
    # Trading sessions from the persisted calendar, ascending.
    import polars as pl

    files = sorted((root / CALENDAR_GLOB).rglob('*.parquet'))
    frame = pl.read_parquet([str(path) for path in files])
    sessions = frame.filter(pl.col('is_trading') == True)['trade_date'].to_list()
    return sorted(value for value in sessions if isinstance(value, date))


def generate(day: str, *, root: Path = DATA_ROOT, evidence: Path | None = None) -> dict[str, Any]:
    # Build the V02 candidate for one date. Never opens the V1 facts.
    started = time.time()
    evidence_path = root / (evidence or EVIDENCE)
    bundle = load_sources(root, evidence_path=evidence_path)
    sessions = load_calendar(root)
    prior = prior_session(day, calendar=sessions)
    if prior is None:
        raise ShadowError('V02_NO_PRIOR_SESSION', day)
    # Bound the walk-back so a long halt cannot reach arbitrarily far, while
    # staying far enough back to cross an ordinary suspension.
    earlier = [value.isoformat() for value in sessions if value < date.fromisoformat(day)]
    prior_days = list(reversed(earlier))[:WALK_BACK_SESSIONS]
    lifecycle = load_lifecycle(root)
    rows, report = build_shadow_rows(bundle, day, prior, lifecycle=lifecycle, prior_days=prior_days)
    report['elapsed_s'] = round(time.time() - started, 2)
    return {
        'schema': SCHEMA,
        'trade_date': day,
        'scope': 'FULL_ELIGIBLE',
        'rows': rows,
        'report': report,
        'source_provenance': bundle.provenance(),
        'rows_hash': canonical_sha(rows),
    }



def _v1_rows(root: Path) -> dict[str, dict[str, Any]]:
    # The certified V1 facts, resolved through its own published pointer.
    import polars as pl

    pointer = json.loads((root / V1_POINTER).read_text())
    plan = json.loads((root / pointer['plan']).read_text())
    files = [root / item['relative_path'] for item in plan['manifest']['files']]
    # The authority mixes a vertical-slice corpus with the full-eligible
    # partition, and their fetched_at dtype differs (String vs Datetime), so
    # the files are concatenated diagonally rather than read as one dataset.
    frame = pl.concat([pl.read_parquet(str(path)) for path in files], how='diagonal_relaxed')
    return {str(row['symbol']): row for row in frame.to_dicts()}


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _quantiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {'P50_DIFF': None, 'P95_DIFF': None, 'P99_DIFF': None}
    ordered = sorted(abs(value) for value in values)

    def pick(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
        return ordered[index]

    return {'P50_DIFF': pick(0.50), 'P95_DIFF': pick(0.95), 'P99_DIFF': pick(0.99)}


def _classify_turnover_divergence(v1_day: dict[str, dict[str, Any]], v02: dict[str, dict[str, Any]],
                                  *, root: Path, day: str) -> dict[str, Any]:
    # Explain each turnover divergence by identifying the denominator basis the
    # other vendor used. Reads only admissible V02 inputs plus the V1 comparison
    # values it is already handed.
    import polars as pl

    bars = pl.read_parquet(root / ('curated/daily_bars/trade_date=' + day + '/part-merged.parquet'))
    volume = {str(r['symbol']): r['volume'] for r in bars.to_dicts()}
    struct_files = sorted((root / 'curated/share_structure').rglob('*.parquet'))
    struct = pl.read_parquet([str(p) for p in struct_files])
    target = _as_date(day)
    selected: dict[str, dict[str, Any]] = {}
    eligible = struct.filter((pl.col('change_date') <= target) & (pl.col('announce_date') <= target))
    for row in eligible.sort('change_date').to_dicts():
        selected[str(row['symbol'])] = row

    buckets: dict[str, list[str]] = {}
    detail: list[dict[str, Any]] = []
    for symbol in sorted(set(v1_day) & set(v02)):
        a = _numeric(v02[symbol].get('turnover_rate'))
        b = _numeric(v1_day[symbol].get('turnover_rate'))
        if b is None or b <= 0 or a is None:
            continue
        if abs(a - b) <= 1e-3:
            continue
        vol = volume.get(symbol)
        row = selected.get(symbol)
        if not vol or not row:
            buckets.setdefault('NO_EVIDENCE', []).append(symbol)
            continue
        implied = vol / (b / 100.0)
        causes = []
        for column, label in (('free_float_shares', 'FREE_FLOAT_BASIS'),
                              ('restricted_shares', 'RESTRICTED_BASIS'),
                              ('total_shares', 'TOTAL_SHARES_BASIS')):
            value = row.get(column)
            if value and abs(float(value) - implied) / implied <= 1e-3:
                causes.append(label)
        cause = causes[0] if causes else 'UNRESOLVED_DENOMINATOR_BASIS'
        buckets.setdefault(cause, []).append(symbol)
        detail.append({'symbol': symbol, 'v1_implied_float': implied,
                       'chosen_float_shares': row.get('float_shares'),
                       'free_float_shares': row.get('free_float_shares'),
                       'total_shares': row.get('total_shares'), 'cause': cause})
    return {
        'COUNTS': {key: len(value) for key, value in sorted(buckets.items())},
        'SYMBOLS': {key: value for key, value in sorted(buckets.items())},
        'DETAIL': detail,
    }


def compare(candidate: dict[str, Any], *, root: Path = DATA_ROOT) -> dict[str, Any]:
    # Compare a frozen candidate against the certified V1 facts. This is the
    # only function that reads V1, and generate() never calls it.
    day = candidate['trade_date']
    v1 = _v1_rows(root)
    v1_day = {symbol: row for symbol, row in v1.items() if str(row.get('trade_date')) == day}
    v02 = {str(row['symbol']): row for row in candidate['rows']}

    keys_v1, keys_v02 = set(v1_day), set(v02)
    missing = sorted(keys_v1 - keys_v02)
    extra = sorted(keys_v02 - keys_v1)
    result: dict[str, Any] = {
        'schema': 'ASL_DAILY_FACTS_V02_SHADOW_COMPARISON_V01',
        'trade_date': day,
        'EXPECTED_N': len(v1_day),
        'V02_ROW_N': len(v02),
        'MISSING_PK_N': len(missing),
        'EXTRA_PK_N': len(extra),
        'DUPLICATE_PK_N': len(candidate['rows']) - len(v02),
        'MISSING_PK_SYMBOLS': missing,
        'EXTRA_PK_SYMBOLS': extra,
    }
    common = sorted(keys_v1 & keys_v02)

    # ---- preclose ----------------------------------------------------------
    preclose_mismatch: list[dict[str, Any]] = []
    preclose_diffs: list[float] = []
    preclose_match_n = 0
    for symbol in common:
        a = _numeric(v02[symbol].get('preclose'))
        b = _numeric(v1_day[symbol].get('preclose'))
        if a is None and b is None:
            preclose_match_n += 1
            continue
        if a is None or b is None:
            preclose_mismatch.append({'symbol': symbol, 'v02': a, 'v1': b, 'cause': 'NULL_ASYMMETRY'})
            continue
        diff = abs(a - b)
        preclose_diffs.append(diff)
        if diff <= COMPUTE_TOLERANCE:
            preclose_match_n += 1
        else:
            v1row = v1_day[symbol]
            if str(v1row.get('preclose_quality')) == 'PASS_REFERENCE_PRICE_EXCEPTION':
                cause = 'V1_REFERENCE_PRICE_EXCEPTION'
            else:
                cause = 'V1_UNEXPLAINED_BASIS'
            preclose_mismatch.append({'symbol': symbol, 'v02': a, 'v1': b, 'diff': diff, 'cause': cause})
    result['preclose'] = {
        'MATCH_N': preclose_match_n,
        'MISMATCH_N': len(preclose_mismatch),
        'MAX_ABS_DIFF': max(preclose_diffs) if preclose_diffs else 0.0,
        'MISMATCHES': preclose_mismatch,
    }

    # ---- pct_chg -----------------------------------------------------------
    pct_diffs: list[float] = []
    pct_mismatches: list[dict[str, Any]] = []
    pct_match_n = 0
    for symbol in common:
        a = _numeric(v02[symbol].get('pct_chg'))
        b = _numeric(v1_day[symbol].get('pct_chg'))
        if a is None and b is None:
            pct_match_n += 1
            continue
        if a is None or b is None:
            pct_mismatches.append({'symbol': symbol, 'v02': a, 'v1': b, 'cause': 'NULL_ASYMMETRY'})
            continue
        diff = abs(a - b)
        pct_diffs.append(diff)
        if diff <= PCT_CHG_TOLERANCE:
            pct_match_n += 1
        else:
            pct_mismatches.append({'symbol': symbol, 'v02': a, 'v1': b, 'diff': diff, 'cause': 'BEYOND_TOLERANCE'})
    result['pct_chg'] = {
        'MATCH_N': pct_match_n,
        'MISMATCH_N': len(pct_mismatches),
        'MAX_ABS_DIFF': max(pct_diffs) if pct_diffs else 0.0,
        'TOLERANCE': PCT_CHG_TOLERANCE,
        'MISMATCHES': pct_mismatches,
    }


    # ---- turnover_rate -----------------------------------------------------
    turnover_diffs: list[float] = []
    turnover_mismatches: list[dict[str, Any]] = []
    comparable = turnover_match_n = null_expected = unresolved = 0
    for symbol in common:
        a = _numeric(v02[symbol].get('turnover_rate'))
        b = _numeric(v1_day[symbol].get('turnover_rate'))
        if b is None:
            null_expected += 1
            if a is not None:
                turnover_mismatches.append({'symbol': symbol, 'v02': a, 'v1': None,
                                            'cause': 'V1_NULL_BUT_V02_DERIVED'})
            continue
        if a is None:
            unresolved += 1
            turnover_mismatches.append({'symbol': symbol, 'v02': None, 'v1': b,
                                        'cause': 'TURNOVER_UNRESOLVED'})
            continue
        comparable += 1
        diff = abs(a - b)
        turnover_diffs.append(diff)
        if diff <= 1e-3:
            turnover_match_n += 1
        else:
            turnover_mismatches.append({'symbol': symbol, 'v02': a, 'v1': b, 'diff': diff,
                                        'cause': 'DENOMINATOR_OR_ROUNDING_DIVERGENCE'})
    result['turnover_rate'] = {
        'COMPARABLE_N': comparable,
        'MATCH_N': turnover_match_n,
        'MISMATCH_N': len(turnover_mismatches),
        'NULL_EXPECTED_N': null_expected,
        'UNRESOLVED_N': unresolved,
        'MAX_ABS_DIFF': max(turnover_diffs) if turnover_diffs else 0.0,
        'MISMATCHES': sorted(turnover_mismatches, key=lambda item: -(item.get('diff') or 0))[:40],
    }
    result['turnover_rate'].update(_quantiles(turnover_diffs))

    # Root-cause classification for every turnover divergence. The comparison
    # is against a *different vendor's* turnover basis, so a disagreement is
    # classified by testing which share_structure column reproduces V1's
    # implied denominator, rather than being left as an unexplained count.
    result['turnover_rate']['ROOT_CAUSE'] = _classify_turnover_divergence(
        v1_day, v02, root=root, day=day,
    )

    # ---- trade_status ------------------------------------------------------
    ts_mismatches: list[dict[str, Any]] = []
    ts_match_n = ts_unknown = 0
    for symbol in common:
        a = str(v02[symbol].get('trade_status'))
        b = str(v1_day[symbol].get('trade_status'))
        if a == UNKNOWN:
            ts_unknown += 1
            continue
        if a == b:
            ts_match_n += 1
        else:
            ts_mismatches.append({'symbol': symbol, 'v02': a, 'v1': b,
                                  'v1_provider_tradestatus': str(v1_day[symbol].get('provider_tradestatus')),
                                  'cause': 'SEMANTIC_DIVERGENCE'})
    result['trade_status'] = {'MATCH_N': ts_match_n, 'MISMATCH_N': len(ts_mismatches),
                              'UNKNOWN_N': ts_unknown, 'MISMATCHES': ts_mismatches}

    # ---- is_st -------------------------------------------------------------
    st_mismatches: list[dict[str, Any]] = []
    st_match_n = st_unknown = 0
    for symbol in common:
        a = str(v02[symbol].get('is_st'))
        b = str(v1_day[symbol].get('is_st'))
        if a == UNKNOWN:
            st_unknown += 1
            continue
        if a == b:
            st_match_n += 1
        else:
            st_mismatches.append({'symbol': symbol, 'v02': a, 'v1': b,
                                  'v02_status': str(v02[symbol].get('status_dataset_value')),
                                  'v1_provider_is_st': str(v1_day[symbol].get('provider_is_st')),
                                  'cause': 'SEMANTIC_DIVERGENCE'})
    result['is_st'] = {'MATCH_N': st_match_n, 'MISMATCH_N': len(st_mismatches),
                       'UNKNOWN_N': st_unknown, 'MISMATCHES': st_mismatches}

    # ---- suspension and reference-price verification -----------------------
    v1_suspended = sorted(s for s, row in v1_day.items() if str(row.get('trade_status')) == SUSPENDED)
    v02_suspended = sorted(s for s, row in v02.items() if str(row.get('trade_status')) == SUSPENDED)
    suspension: dict[str, Any] = {
        'V1_SUSPENDED_N': len(v1_suspended),
        'V02_SUSPENDED_N': len(v02_suspended),
        'V1_ONLY': sorted(set(v1_suspended) - set(v02_suspended)),
        'V02_ONLY': sorted(set(v02_suspended) - set(v1_suspended)),
        'ROWS': [],
    }
    for symbol in sorted(set(v1_suspended) | set(v02_suspended)):
        row = v02.get(symbol) or {}
        suspension['ROWS'].append({
            'symbol': symbol,
            'v02_trade_status': row.get('trade_status'),
            'v02_pct_chg': row.get('pct_chg'),
            'v02_turnover_rate': row.get('turnover_rate'),
            'v1_turnover_rate': _numeric((v1_day.get(symbol) or {}).get('turnover_rate')),
        })
    result['suspension'] = suspension

    exceptions = sorted(s for s, row in v02.items()
                        if row.get('preclose_rule_id') == RULE_PRECLOSE_REFERENCE_PRICE)
    result['reference_price'] = {
        'V02_REFERENCE_PRICE_EXCEPTION_N': len(exceptions),
        'SYMBOLS': exceptions,
        'V1_REFERENCE_PRICE_EXCEPTION_N': sum(
            1 for row in v1_day.values() if row.get('preclose_quality') == 'PASS_REFERENCE_PRICE_EXCEPTION'),
    }

    # ---- ST / risk_warning deep check --------------------------------------
    v1_st = {s for s, row in v1_day.items() if str(row.get('is_st')) == 'TRUE'}
    v02_st = {s for s, row in v02.items() if str(row.get('is_st')) == 'TRUE'}
    st_suspended = sorted(s for s in v1_st if str(v1_day[s].get('provider_tradestatus')) == '0')
    result['is_st_detail'] = {
        'V1_IS_ST_TRUE_N': len(v1_st),
        'V02_IS_ST_TRUE_N': len(v02_st),
        'V1_ONLY': sorted(v1_st - v02_st),
        'V02_ONLY': sorted(v02_st - v1_st),
        'ST_WHILE_SUSPENDED_IN_V1': st_suspended,
        'V02_RISK_WARNING_TRUE_ON_SUSPENDED': sorted(s for s in v02_suspended
                                                     if str(v02[s].get('is_st')) == 'TRUE'),
    }
    result['classification'] = {
        'UNEXPLAINED_PRECLOSE_MISMATCH_N': sum(1 for item in preclose_mismatch
                                               if item['cause'] == 'V1_UNEXPLAINED_BASIS'),
        'UNEXPLAINED_TRADE_STATUS_MISMATCH_N': len(ts_mismatches),
        'UNEXPLAINED_IS_ST_MISMATCH_N': len(st_mismatches),
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Daily Facts V02 shadow generate + compare')
    parser.add_argument('--trade-date', required=True)
    parser.add_argument('--data-root', type=Path, default=DATA_ROOT)
    parser.add_argument('--candidate-out', type=Path, default=None)
    parser.add_argument('--comparison-out', type=Path, default=None)
    args = parser.parse_args(argv)

    candidate = generate(args.trade_date, root=args.data_root)
    candidate_path = args.candidate_out or Path('/tmp/v02_shadow_candidate_' + args.trade_date + '.json')
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False, sort_keys=True, default=str))
    print(json.dumps({'phase': 'generate', 'path': str(candidate_path),
                      'rows': len(candidate['rows']), 'rows_hash': candidate['rows_hash'],
                      'report': candidate['report'],
                      'source_provenance': candidate['source_provenance']},
                     ensure_ascii=False, sort_keys=True, default=str), flush=True)

    comparison = compare(candidate, root=args.data_root)
    comparison_path = args.comparison_out or Path('/tmp/v02_shadow_comparison_' + args.trade_date + '.json')
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, sort_keys=True, default=str))
    summary = {key: value for key, value in comparison.items() if not isinstance(value, (list, dict))}
    for field in ('preclose', 'pct_chg', 'turnover_rate', 'trade_status', 'is_st'):
        summary[field] = {k: v for k, v in comparison[field].items() if k != 'MISMATCHES'}
    summary['classification'] = comparison['classification']
    # The per-symbol root-cause tables live in the JSON artifact; printing
    # them would bury the counts this command exists to report.
    summary['turnover_rate']['ROOT_CAUSE_COUNTS'] = \
        comparison['turnover_rate']['ROOT_CAUSE']['COUNTS']
    summary['turnover_rate'].pop('ROOT_CAUSE', None)
    summary['is_st_detail_counts'] = {k: (len(v) if isinstance(v, list) else v)
                                      for k, v in comparison['is_st_detail'].items()}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
