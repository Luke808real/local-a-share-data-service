#!/usr/bin/env python3
"""Explicitly scoped live upgrade canary; never writes a production authority.

Valuation uses ONE real clist page, replayed unchanged through the native
adapter and its guard. The pager alone is substituted to bound network scope;
normalization, guard, schema, provenance and compact remain CNEquity-owned.
This is a sample canary, not a full-universe snapshot certification.
"""
from __future__ import annotations
import argparse
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
PRODUCTION = Path('/Users/luke808/AI/local-a-share-data-service-data')
SHA = 'ca5c568f52a4cc1fad8bd812c3c406802c39d2fc'
SH = ZoneInfo('Asia/Shanghai')


def run(work: Path, target: date):
    import importlib.metadata as md
    import polars as pl
    from cnequity.config import load_config
    from cnequity.adapters.eastmoney.clist import _fetch_clist_page, PUSH2_CLIST_HOSTS, clist_rows_to_symbols
    from cnequity.adapters.eastmoney.em_auth import EastMoneyClient
    from cnequity.adapters.eastmoney import valuation
    from cnequity.adapters.tdx_protocol.client import fetch_daily_bars, normalize_with_source
    from cnequity.domain.schemas import validate_dataframe
    from cnequity.domain.snapshot_guard import assert_snapshot_matches_session, observe_snapshot, SnapshotWindowError
    from cnequity.orchestrator.engine import JobEngine
    from cnequity.storage.parquet import StagingWriter
    from cnequity.adapters.eastmoney.corporate_actions import fetch_corporate_actions_eastmoney
    from cnequity.steps.common import with_provenance
    from cnequity.config.loader import WaveConfig
    work = work.resolve()
    if not work.is_relative_to(REPO / '.runtime/cne090') or work.is_relative_to(PRODUCTION):
        raise ValueError('ISOLATED_CANARY_ROOT_REQUIRED')
    root = work / 'lake'
    if (root / 'curated').exists():
        raise ValueError('FRESH_CANARY_ROOT_REQUIRED')
    assert md.version('cnequity') == '0.9.0'
    assert json.loads(md.distribution('cnequity').read_text('direct_url.json'))['vcs_info']['commit_id'] == SHA
    work.mkdir(parents=True, exist_ok=True)
    config_path = work / 'config.toml'
    config_path.write_text((REPO / 'config/cnequity.toml').read_text().replace(str(PRODUCTION), str(root)))
    config = load_config(config_path)
    assert config.data_root == root and not config.tdx_allow_mock
    host_errors = []
    # Native transport/retry per host; first successful actual page defines scope.
    with EastMoneyClient(config=config) as client:
        for host in PUSH2_CLIST_HOSTS:
            try:
                rows, total = _fetch_clist_page(client, host=host, fields=valuation._VALUATION_FIELDS,
                    fs='m:1+t:2,m:0+t:6', page=1, page_size=10)
                break
            except Exception as exc:
                host_errors.append({'host':host,'error':str(exc)})
        else:
            raise RuntimeError('NATIVE_CLIST_SAMPLE_UNAVAILABLE: '+str(host_errors))
    assert len(rows) == 10 and total >= 10
    raw = {'retrieved_at':datetime.now(SH).isoformat(), 'host':host, 'page':1, 'reported_total':total,
           'scope':'first 10 SH/SZ main-board rows; not whole market', 'rows':rows,
           'observation':observe_snapshot(rows).as_dict(), 'prior_host_errors':host_errors}
    (work/'live-page.json').write_text(json.dumps(raw,default=str,indent=2))
    assert_snapshot_matches_session(rows,target)
    negative = {}
    for label, payload, day in [
        ('wrong_session',rows,target-timedelta(days=1)),
        ('future_session',rows,target+timedelta(days=1)),
        ('turnover_reset',[dict(r,f8=0) for r in rows],target),
    ]:
        try: assert_snapshot_matches_session(payload,day)
        except SnapshotWindowError as exc: negative[label]=exc.code
        else: raise AssertionError('negative guard accepted '+label)
    valuation_symbols = [s for s,_ in clist_rows_to_symbols(rows)]
    symbols = sorted(set(valuation_symbols+['600519.SH','002311.SZ','000016.SZ','600301.SH']))
    inst = pl.concat([pl.read_parquet(p) for p in (PRODUCTION/'curated/instruments').rglob('*.parquet')],how='diagonal_relaxed')
    inst = inst.filter(pl.col('symbol').is_in(symbols)).unique('symbol')
    assert set(inst['symbol']) == set(symbols)
    path=root/'curated/instruments/part-canary.parquet';path.parent.mkdir(parents=True);inst.write_parquet(path)
    # A native exact-date fetch, schema and provenance path used by ASL R3.
    engine=JobEngine(config)
    run_id=engine.manifest.start_run('cne090-canary-tdx',{'scope':symbols,'date':str(target)})
    engine.manifest.start_batch(run_id,'sample','daily_bars','daily_bars',symbols,str(target),str(target))
    bars=normalize_with_source(fetch_daily_bars(symbols,target,target,config=config),dataset='daily_bars')
    bars=validate_dataframe(bars,'daily_bars')
    assert bars.height and set(bars['symbol']).issubset(symbols)
    assert set(bars['trade_date']) == {target}
    assert set(bars['source']) == {'tdx_protocol'} and set(bars['data_version']) == {'v2'}
    assert bars.unique(['symbol','trade_date']).height == bars.height
    for row in bars.iter_rows(named=True):
        assert all(math.isfinite(row[k]) for k in ('open','high','low','close','volume','amount'))
        assert 0 < row['low'] <= min(row['open'],row['close']) <= max(row['open'],row['close']) <= row['high']
        assert row['volume'] >= 0 and row['amount'] >= 0
    StagingWriter(config.staging_root).write_batch('daily_bars',run_id,'sample',bars)
    engine.manifest.finish_batch(run_id,'sample','success',rows_read=bars.height,rows_written=bars.height)
    compact=engine.run_step('compact',target,run_id)
    assert compact['status']=='success',compact
    engine.manifest.finish_run(run_id,'success',rows_read=bars.height,rows_written=bars.height)
    # Native daily corporate_actions step deliberately reconciles 30 days.
    # Exercise the native exact-date adapter here to keep this canary bounded;
    # do not alter its registry lookback or pretend a watermark limits it.
    event_run = engine.manifest.start_run('cne090-canary-events', {'date': str(target)})
    engine.manifest.start_batch(event_run, 'events', 'corporate_actions', 'corporate_actions', [], str(target), str(target))
    events = fetch_corporate_actions_eastmoney(target, config=config, run_id=event_run)
    events = validate_dataframe(with_provenance(events, source='eastmoney', data_version='v1'), 'corporate_actions')
    assert events.height and set(events['ex_date']) == {target}
    StagingWriter(config.staging_root).write_batch('corporate_actions', event_run, 'events', events)
    engine.manifest.finish_batch(event_run, 'events', 'success', rows_read=events.height, rows_written=events.height)
    event_compact = engine.run_step('compact', target, event_run)
    assert event_compact['status'] == 'success', event_compact
    engine.manifest.finish_run(event_run, 'success', rows_read=events.height, rows_written=events.height)
    with patch.object(valuation,'fetch_clist_pages',return_value=rows):
        result=engine.run_job('daily',trade_date=target,waves=[WaveConfig(name='canary',parallel=False,
            steps=['trading_status','valuation_metrics','compact'])])
    assert result['status']=='success',result
    datasets={}
    for dataset in ('daily_bars','trading_status','valuation_metrics','corporate_actions'):
        files=list((root/'curated'/dataset).rglob('*.parquet'))
        assert files, dataset+' missing'
        frame=pl.concat([pl.read_parquet(p) for p in files],how='diagonal_relaxed')
        frame=validate_dataframe(frame,dataset)
        dc='ex_date' if dataset=='corporate_actions' else 'trade_date'
        assert set(frame[dc]) == {target}
        from cnequity.domain.schemas import PRIMARY_KEYS
        assert frame.unique(list(PRIMARY_KEYS[dataset])).height==frame.height
        if dataset!='daily_bars':
            assert set(frame['source'])=={'eastmoney'}
            assert set(frame['data_version'])=={'v1'}
        datasets[dataset]={'rows':frame.height,'schema':{k:str(v) for k,v in frame.schema.items()},
                           'records':frame.to_dicts()}
    status={r['symbol']:r for r in datasets['trading_status']['records']}
    assert set(status)==set(symbols)
    for r in status.values():
        assert r['status'] in {'normal','suspended'} and isinstance(r['risk_warning'],bool)
    assert set(symbols)-set(bars['symbol']) <= {s for s,r in status.items() if r['status']=='suspended'}
    values=datasets['valuation_metrics']['records']
    assert {r['symbol'] for r in values}==set(valuation_symbols)
    source={s:item for s,item in clist_rows_to_symbols(rows)}
    for r in values:
        assert r['turnover_rate']==source[r['symbol']]['f8']
        assert r['turnover_rate'] is None or math.isfinite(r['turnover_rate']) and r['turnover_rate']>=0
    report={'status':'PASS','target':str(target),'valuation_transport':'one real page replayed through native adapter/guard',
            'corporate_actions_scope':'native exact-date event feed; daily 30-day reconciliation intentionally not invoked',
            'event_compact':event_compact,'production_publish':False,'scope':symbols,'valuation_scope':valuation_symbols,
            'negative_guards':negative,'job_result':result,'tdx_compact':compact,'datasets':datasets,'raw_observation':raw['observation']}
    (work/'receipt.json').write_text(json.dumps(report,default=str,indent=2,ensure_ascii=False))
    print(json.dumps({'status':'PASS','target':str(target),'rows':{k:v['rows'] for k,v in datasets.items()}},ensure_ascii=False),flush=True)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--date',type=date.fromisoformat,required=True)
    parser.add_argument('--wait-for-settlement',action='store_true')
    args=parser.parse_args()
    if args.wait_for_settlement:
        cutoff=datetime.combine(args.date,datetime.min.time(),tzinfo=SH).replace(hour=15,minute=6)
        while datetime.now(SH)<cutoff:
            remaining=(cutoff-datetime.now(SH)).total_seconds()
            print('Waiting for real settlement; seconds remaining:',int(remaining),flush=True)
            time.sleep(min(30,remaining))
    run(args.work,args.date)
