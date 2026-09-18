#!/usr/bin/env python3
"""Read-only production authority/LocalQuery/MCP compatibility evidence."""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'src'))
ROOT=Path('/Users/luke808/AI/local-a-share-data-service-data')
EXPECTED_R3='e5364e8237e0776b139477f2284b93587bee8b0b5232b0122c583a2b28600a2b'
EXPECTED_FACTS='6126f20103eae277f5e23f95f2e314a26f8b6c90b37cfb3e292e01f3dc36710b'


def verify():
    import polars as pl
    import duckdb
    from ashare_data.local_query import LocalQuery
    from ashare_data.mcp_server import create_server
    pointers=[ROOT/'meta/asl/r3/published-daily-authority.json',ROOT/'meta/asl/daily_facts/published-daily-facts-authority.json']
    before={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in pointers}
    fptr=json.loads(pointers[1].read_text())
    plan=json.loads((ROOT/fptr['plan']).read_text())
    paths=[str(ROOT/f['relative_path']) for f in plan['manifest']['files']]
    dayfile=next(p for p in paths if 'trade_date=2026-09-17/' in p)
    frame=pl.read_parquet(dayfile)
    samples={'normal':'002580.SZ','ex_dividend':'002311.SZ','suspended':'600301.SH','st_suspended':'000016.SZ'}
    samples['st_trading']=frame.filter((pl.col('is_st')=='TRUE')&(pl.col('trade_status')=='TRADING'))['symbol'][0]
    outputs={}
    with LocalQuery(ROOT) as q:
        s=q.status()
        assert s['DAILY_MANIFEST_HASH']==EXPECTED_R3 and s['DAILY_FACTS_MANIFEST_HASH']==EXPECTED_FACTS
        assert s['DAILY_MANIFEST_FILE_N']==2603 and s['DAILY_PUBLISHED_AS_OF']=='2026-09-17'
        assert s['DAILY_FACTS_PUBLISHED_AS_OF']=='2026-09-17' and s['DAILY_FACTS_SCHEMA']=='ASL_DAILY_FACTS_V02'
        for label,symbol in samples.items():
            outputs[label]={'instrument':q.instrument(symbol),'bars':q.bars(symbol,'2026-09-16','2026-09-17'),
                'latest':q.latest(symbol,limit=1,as_of='2026-09-17'),'facts':q.facts(symbol,'2026-09-17')}
            facts=outputs[label]['facts']['facts']
            assert isinstance(facts['preclose'],float)
            if facts['trade_status']=='SUSPENDED':
                assert facts['pct_chg'] is None and facts['turnover_rate'] is None
            else: assert isinstance(facts['pct_chg'],float) and isinstance(facts['turnover_rate'],float)
        outputs['old_v1']=q.facts('002315.SZ','2026-09-09')
    with duckdb.connect(':memory:') as c:
        types=dict((r[0],r[1]) for r in c.execute('describe select * from read_parquet(?,union_by_name=true)',[paths]).fetchall())
        assert all(types[k]=='DOUBLE' for k in ('preclose','pct_chg','turnover_rate'))
        for symbol in samples.values():
            assert len(c.execute('select * from read_parquet(?,union_by_name=true) where symbol=? and trade_date=?',[paths,symbol,'2026-09-17']).fetchall())==1
        assert frame['reference_price_source_hash'].is_not_null().sum()==24
    async def mcp():
        server=create_server();out={}
        cases=[('status',{}),('instrument',{'symbol':samples['normal']}),
               ('bars',{'symbol':samples['normal'],'start':'2026-09-16','end':'2026-09-17'}),
               ('latest',{'symbol':samples['normal'],'limit':1})]
        cases.extend(('facts',{'symbol':symbol,'trade_date':'2026-09-17'}) for symbol in samples.values())
        cases.append(('facts',{'symbol':'002315.SZ','trade_date':'2026-09-09'}))
        for n,(name,args) in enumerate(cases):
            r=await server.call_tool(name,args)
            assert not r.isError,(name,r)
            out[str(n)]={'tool':name,'args':args,'result':r.structuredContent}
        return out
    results=asyncio.run(mcp())
    after={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in pointers}
    assert before==after
    return {'STATUS':'PASS','status':s,'authority_before':before,'authority_after':after,
            'samples':samples,'local_query':outputs,'mcp':results,'union_numeric_types':{k:types[k] for k in ('preclose','pct_chg','turnover_rate')},
            'sparse_reference_rows':24,'provider_requests':0}

if __name__=='__main__':
    out=verify()
    Path(sys.argv[1]).write_text(json.dumps(out,default=str,ensure_ascii=False,indent=2))
    print('READ_ONLY_LOCALQUERY_MCP_PASS')
