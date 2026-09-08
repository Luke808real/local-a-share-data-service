#!/usr/bin/env python3
"""Resumable, low-rate feasibility collector.  It never publishes facts."""
from __future__ import annotations
import argparse, json, os, sqlite3, sys, time, uuid
from datetime import date, datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from ashare_data.daily_facts_phase1 import BaoStockDailyFactsAdapter, DailyFactsError, normalize
from ashare_data.local_query import DEFAULT_DATA_ROOT

RUN='daily_facts_phase1_feasibility_v01'; SCHEMA='ASL_DAILY_FACTS_FEASIBILITY_V01'
def utc(): return datetime.now(timezone.utc).isoformat()
def db(root):
 p=root/'staging'/RUN/'progress.sqlite'; p.parent.mkdir(parents=True,exist_ok=True)
 c=sqlite3.connect(p); c.execute('create table if not exists requests (symbol text primary key,start text,end text,status text not null,retry_n integer not null default 0,request_id text,started_at text,completed_at text,row_n integer,raw_path text,error_code text,schema text not null)')
 return c
def scope(root,days):
 import duckdb
 with duckdb.connect(':memory:') as c:
  daily=str(root/'curated/daily_bars/**/*.parquet'); inst=str(root/'curated/instruments/**/*.parquet')
  dates=[r[0] for r in c.execute("select distinct trade_date from read_parquet(?) order by trade_date desc limit ?",[daily,days]).fetchall()]
  symbols=[r[0] for r in c.execute("select distinct i.symbol from read_parquet(?) i where i.exchange in ('SH','SZ') and exists(select 1 from read_parquet(?) d where d.symbol=i.symbol) order by i.symbol",[inst,daily]).fetchall()]
 return symbols,min(dates),max(dates)
def run(root,days,max_requests=0):
 symbols,start,end=scope(root,days); con=db(root)
 # A process can die after persisting RUNNING but before writing RAW.  That
 # state is deliberately not success and is safe to retry under a new receipt.
 con.execute("update requests set status='PENDING',error_code='INTERRUPTED_RECOVERABLE' where status='RUNNING'")
 for s in symbols: con.execute('insert or ignore into requests(symbol,start,end,status,schema) values(?,?,?,?,?)',(s,start,end,'PENDING',SCHEMA))
 con.commit(); rows=con.execute("select symbol,retry_n from requests where status='PENDING' order by symbol" + (' limit ?' if max_requests else ''),(() if not max_requests else (max_requests,))).fetchall()
 raw=root/'raw'/'baostock'/'daily_facts'/RUN; raw.mkdir(parents=True,exist_ok=True)
 with BaoStockDailyFactsAdapter() as provider:
  for symbol,retry_n in rows:
   request_id=str(uuid.uuid4()); begun=utc(); con.execute("update requests set status='RUNNING',request_id=?,started_at=? where symbol=?",(request_id,begun,symbol));con.commit()
   try:
    result=normalize(provider.fetch(symbol,start,end)); path=raw/f'{symbol}.json'
    if path.exists(): raise DailyFactsError('RAW_DUPLICATE','existing raw payload for non-success request')
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps({'request_id':request_id,'provider':'BAOSTOCK','symbol':symbol,'requested_start':str(start),'requested_end':str(end),'schema':SCHEMA,'rows':result},sort_keys=True),encoding='utf-8');os.replace(tmp,path)
    con.execute("update requests set status='SUCCESS',completed_at=?,row_n=?,raw_path=?,error_code=null where symbol=?",(utc(),len(result),str(path.relative_to(root)),symbol))
   except DailyFactsError as exc: con.execute("update requests set status='SOURCE_ERROR',completed_at=?,retry_n=retry_n+1,error_code=? where symbol=?",(utc(),exc.code,symbol))
   con.commit(); time.sleep(1)
 summary=dict(con.execute("select status,count(*) from requests group by status").fetchall()); con.close()
 return {'schema':SCHEMA,'universe_n':len(symbols),'start':str(start),'end':str(end),'attempted_n':len(rows),'states':summary,'publication':'FEASIBILITY_ONLY_NOT_PUBLISHED'}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--data-root',type=Path,default=DEFAULT_DATA_ROOT);p.add_argument('--days',type=int,default=5);p.add_argument('--max-requests',type=int,default=0);a=p.parse_args(); print(json.dumps(run(a.data_root.resolve(),a.days,a.max_requests),sort_keys=True))
