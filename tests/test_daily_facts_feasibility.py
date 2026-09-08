import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('feasibility',ROOT/'tools/run_daily_facts_phase1_feasibility.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

def test_progress_ledger_is_idempotent(tmp_path):
    con=module.db(tmp_path)
    con.execute("insert into requests(symbol,start,end,status,schema) values('000001.SZ','2026-09-01','2026-09-07','SUCCESS',?)",(module.SCHEMA,));con.commit()
    con.execute("insert or ignore into requests(symbol,start,end,status,schema) values('000001.SZ','2026-09-01','2026-09-07','PENDING',?)",(module.SCHEMA,));con.commit()
    assert con.execute('select count(*),max(status) from requests').fetchone()==(1,'SUCCESS')
