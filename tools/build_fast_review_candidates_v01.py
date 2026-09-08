#!/usr/bin/env python3
"""Build non-authoritative same-day structural candidate evidence locally."""
from __future__ import annotations
import json, sys
from datetime import date
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from ashare_data.daily_review_features import derive_features, generic_candidates
from ashare_data.local_query import LocalQuery

def main():
 root=Path('/Users/luke808/AI/local-a-share-data-service-data'); day=date(2026,9,8)
 snap=json.loads((root/'meta/asl/fast_review/5m/trade_date=2026-09-08.json').read_text())
 ready={r['symbol']:r for r in snap['rows'] if r['row_state']=='REVIEW_ROW_READY'}
 with LocalQuery(root) as q:
  symbols=sorted(ready); values=','.join('(?)' for _ in symbols)
  sql='select symbol,cast(trade_date as varchar) trade_date,open,high,low,close,volume,amount from '+q._daily_allowlist(end=date(2026,9,7))+' where symbol in ('+values+') and trade_date >= date \'2026-08-01\' order by symbol,trade_date'
  rows=q._execute(sql,symbols)
 grouped={s:[] for s in symbols}
 for r in rows: grouped[r['symbol']].append(r)
 for s,r in ready.items(): grouped[s].append({'trade_date':day.isoformat(),'open':r['review_open'],'high':r['review_high'],'low':r['review_low'],'close':r['review_close'],'volume':r['review_volume'],'amount':r['review_amount']})
 features=derive_features(grouped); candidates=generic_candidates(features)
 for r in candidates: r['five_minute_review']=ready[r['symbol']]
 out={'schema':'ASL_FAST_REVIEW_CANDIDATES_V01','scope':'REVIEW_EVIDENCE_ONLY_NOT_PUBLICATION','publication_authority':False,'trade_date':day.isoformat(),'feature_row_n':len(features),'candidate_n':len(candidates),'candidates':candidates}
 path=root/'meta/asl/fast_review/candidates/trade_date=2026-09-08.json'; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(out,sort_keys=True,separators=(',',':')))
 print(json.dumps({k:v for k,v in out.items() if k!='candidates'},sort_keys=True))
if __name__=='__main__': main()
