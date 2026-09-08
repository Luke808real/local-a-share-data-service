#!/usr/bin/env python3
"""Offline-only aggregation for the frozen feasibility ledger."""
from __future__ import annotations
import hashlib,json,sqlite3,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ashare_data.daily_facts_phase1 import reconcile
RUN='daily_facts_phase1_feasibility_v01'
def main(root):
 d=root/'staging'/RUN; db=d/'progress.sqlite'; c=sqlite3.connect(db)
 states=dict(c.execute('select status,count(*) from requests group by status'))
 if sum(states.values())!=5456 or set(states)-{'SUCCESS','SOURCE_ERROR'}: raise SystemExit('ledger is not terminal')
 rows=[]
 for (p,) in c.execute("select raw_path from requests where status='SUCCESS' order by symbol"):
  rows.extend(json.loads((root/p).read_text())['rows'])
 import duckdb
 with duckdb.connect(':memory:') as con:
  bars=[dict(zip([x[0] for x in con.description],r)) for r in con.execute("select symbol,cast(trade_date as varchar) trade_date,close from read_parquet(?) where trade_date between date '2026-08-28' and date '2026-09-07'",[str(root/'curated/daily_bars/**/*.parquet')]).fetchall()]
 reconcile(rows,bars)
 expected_bars={(b['symbol'],b['trade_date']) for b in bars if b['trade_date']>='2026-09-01'}
 keys={(r['symbol'],r['trade_date']) for r in rows}
 status_by_key={(r['symbol'],r['trade_date']):r['trade_status'] for r in rows}
 def coverage(field):
  status_fact=field in {'trade_status','is_st'}
  expected={key for key in keys if status_fact or (key in expected_bars and status_by_key[key]=='TRADING')}
  scoped=[r for r in rows if (r['symbol'],r['trade_date']) in expected]
  vals=[r.get(field) for r in scoped]; valid=sum(v is not None and v not in {'UNKNOWN'} for v in vals)
  return {'EXPECTED_N':len(expected),'RETURNED_N':len(scoped),'VALID_N':valid,'MISSING_N':len(expected-keys),'UNKNOWN_N':sum(v=='UNKNOWN' for v in vals),'SOURCE_ERROR_N':states.get('SOURCE_ERROR',0),'COVERAGE_PCT':valid/len(expected)*100 if expected else 0}
 pre=Counter(r.get('preclose_reconciliation') for r in rows); pct=Counter(r.get('pct_chg_reconciliation') for r in rows)
 turns=[r['turnover_rate'] for r in rows if isinstance(r.get('turnover_rate'),(int,float))]
 turns.sort()
 def quant(q): return turns[round((len(turns)-1)*q)] if turns else None
 prior={}
 for symbol in {b['symbol'] for b in bars}:
  values=sorted((b for b in bars if b['symbol']==symbol),key=lambda b:b['trade_date']); last=None
  for bar in values:
   if last is not None: prior[(symbol,bar['trade_date'])]=last
   last=bar['close']
 mismatches=[]
 for r in rows:
  if r.get('preclose_reconciliation') not in {'MISMATCH','UNRESOLVED'}: continue
  old=prior.get((r['symbol'],r['trade_date'])); diff=None if old is None else r['preclose']-old
  mismatches.append({'symbol':r['symbol'],'trade_date':r['trade_date'],'baostock_preclose':r['preclose'],'tdx_previous_close':old,'absolute_diff':None if diff is None else abs(diff),'relative_diff':None if not old else diff/old,'classification':r['preclose_reconciliation'],'evidence':'POSSIBLE_CORPORATE_ACTION only when local year-level record exists'})
 report={'run_id':RUN,'ledger_sha256':hashlib.sha256(db.read_bytes()).hexdigest(),'universe_n':5456,'date_range':['2026-09-01','2026-09-07'],'states':states,'request_n':5456,'success_n':states.get('SUCCESS',0),'failure_n':states.get('SOURCE_ERROR',0),'empty_success_n':sum(not json.loads((root/p).read_text())['rows'] for (p,) in c.execute("select raw_path from requests where status='SUCCESS'")),'coverage':{x:coverage(x) for x in ('preclose','pct_chg','turnover_rate','trade_status','is_st')},'preclose':dict(pre),'pct_chg':dict(pct),'turnover':{'VALID_N':len(turns),'NULL_N':sum(r.get('turnover_rate') is None for r in rows),'ZERO_N':sum(r.get('turnover_rate')==0 for r in rows),'NEGATIVE_N':sum(isinstance(r.get('turnover_rate'),(int,float)) and r['turnover_rate']<0 for r in rows),'MIN':quant(0),'P01':quant(.01),'P50':quant(.5),'P95':quant(.95),'P99':quant(.99),'MAX':quant(1)},'trade_status':dict(Counter(r['trade_status'] for r in rows)),'is_st':dict(Counter(r['is_st'] for r in rows)),'top_mismatches':mismatches,'publication':'FEASIBILITY_ONLY_NOT_PUBLISHED'}
 (d/'ledger_freeze.json').write_text(json.dumps({'run_id':RUN,'ledger_sha256':report['ledger_sha256'],'states':states,'frozen_at_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()},sort_keys=True))
 (d/'feasibility_report.json').write_text(json.dumps(report,sort_keys=True))
 print(json.dumps(report,sort_keys=True))
if __name__=='__main__': main(Path('/Users/luke808/AI/local-a-share-data-service-data'))
