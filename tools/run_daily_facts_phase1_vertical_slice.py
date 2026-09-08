#!/usr/bin/env python3
"""Execute the bounded, evidence-first Daily Facts Phase 1 vertical slice."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ashare_data.daily_facts_phase1 import (  # noqa: E402
    DailyFactsError, quality_receipt, normalize, reconcile,
)
from ashare_data.cnequity_bridge import CNEquityBaoStockDailyFactsBridge  # noqa: E402
from ashare_data.local_query import DEFAULT_DATA_ROOT, LocalQuery  # noqa: E402
from cnequity.config import load_config  # noqa: E402

RUN = "daily_facts_phase1_vertical_slice_v01"
START, END = date(2026, 8, 7), date(2026, 9, 7)
CORE = ("002580.SZ", "601888.SH", "000001.SZ", "600519.SH")


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _sha(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _write_parquet(path: Path, rows: list[dict]) -> None:
    import duckdb
    if path.exists(): raise RuntimeError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".jsonl", delete=False, dir=path.parent) as raw:
        for row in rows: raw.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
        raw_path = raw.name
    try:
        with duckdb.connect(":memory:") as con:
            destination = str(path).replace("'", "''")
            con.execute("COPY (SELECT * FROM read_json_auto(?)) TO '" + destination + "' (FORMAT PARQUET)", [raw_path])
    finally:
        os.unlink(raw_path)


def _manifest(root: Path, files: list[Path]) -> dict:
    records=[]
    for path in sorted(files):
        records.append({"relative_path":str(path.relative_to(root)),"file_size":path.stat().st_size,"sha256":hashlib.file_digest(path.open("rb"),"sha256").hexdigest()})
    return {"schema":"ASL_DAILY_FACTS_MANIFEST_V01","file_n":len(records),"files":records,"manifest_hash":_sha(records)}


def _auto_samples(root: Path) -> dict[str, str]:
    """Select from formal identity and existing published R3 facts, never names alone."""
    import duckdb
    with LocalQuery(root) as query:
        instruments = query._instrument_files  # published LocalQuery identity input, read-only
        relation = "read_parquet([" + ",".join(repr(str(p)) for p in instruments) + "])"
        daily = "read_parquet('" + str(root / "curated/daily_bars/**/*.parquet") + "')"
        with duckdb.connect(":memory:") as con:
            st_candidates=[r[0] for r in con.execute("select symbol from " + relation + " where name ilike '%ST%' order by symbol limit 10").fetchall()]
            suspended=[r[0] for r in con.execute("""with b as (select symbol, trade_date from """ + daily + " where trade_date between date '2026-08-21' and date '2026-08-25') select symbol from b group by symbol having count(*)=2 and bool_or(trade_date=date '2026-08-21') and bool_or(trade_date=date '2026-08-25') and not bool_or(trade_date=date '2026-08-24') order by symbol limit 100""").fetchall()]
            corporate=[r[0] for r in con.execute("select distinct symbol from read_parquet(?) where ex_date=2026 order by symbol limit 100", [[str(p) for p in (root / 'curated/corporate_actions').rglob('*.parquet')]]).fetchall()]
        # Formal identity and published-daily membership are intersected in one
        # local query.  Re-entering LocalQuery per candidate would repeatedly
        # verify the entire R3 manifest and is not a different authority check.
        def formal(candidates):
            if not candidates: return []
            placeholders=','.join('?' for _ in candidates)
            with duckdb.connect(":memory:") as con:
                return [r[0] for r in con.execute(
                    "select distinct i.symbol from " + relation + " i where i.symbol in (" + placeholders + ") and exists (select 1 from " + daily + " d where d.symbol=i.symbol) order by i.symbol", candidates).fetchall()]
        st_candidates=formal(st_candidates)
        suspended=formal(suspended)
        corporate=formal(corporate)
    with CNEquityBaoStockDailyFactsBridge(config=load_config(ROOT / "config/cnequity.toml")) as adapter:
        st=next((s for s in st_candidates if any(r["is_st"] == "TRUE" for r in normalize(adapter.fetch(s, END, END)))), None)
        suspension=next((
            s for s in suspended
            if any(r["trade_status"] == "SUSPENDED"
                   for r in normalize(adapter.fetch(s, date(2026, 8, 24), date(2026, 8, 24))))
        ), None)
        if st is None:
            st=next((s for s in suspended if any(r["is_st"] == "TRUE" for r in normalize(adapter.fetch(s, START, END)))), None)
    if not st or not suspension or not corporate:
        raise DailyFactsError("SAMPLE_SELECTION_FAILED", "cannot prove required vertical-slice samples")
    # Corporate action data currently records only a year; selection is retained
    # as a research sample and cannot explain a date-specific preclose mismatch.
    return {"st":st,"suspension":suspension,"corporate_action_year_only":corporate[0]}


def execute(root: Path) -> dict:
    samples=_auto_samples(root)
    symbols=list(dict.fromkeys([*CORE,*samples.values()]))
    raw_rows=[]; facts=[]; bars=[]
    with CNEquityBaoStockDailyFactsBridge(config=load_config(ROOT / "config/cnequity.toml")) as adapter, LocalQuery(root) as query:
        for symbol in symbols:
            raw=adapter.fetch(symbol,START,END); raw_rows += raw; facts += normalize(raw)
            bars += query.bars(symbol,"2026-08-06",END.isoformat())["rows"]
    reconcile(facts,bars)
    receipt=quality_receipt(facts,requested_symbols=symbols,start=START,end=END)
    receipt["sample_roles"]=samples
    receipt["corporate_action_date_precision"]="YEAR_ONLY_NOT_USED_AS_MISMATCH_EXPLANATION"
    staging=root/"staging"/RUN; raw_root=root/"raw"/"baostock"/"daily_facts"/RUN
    if staging.exists() or raw_root.exists(): raise RuntimeError("run directory already exists; refuse overwrite")
    _write_parquet(raw_root/"provider_raw.parquet", [{"symbol":r.symbol,"trade_date":r.trade_date.isoformat(),"provider":"BAOSTOCK","raw_values":json.dumps(r.raw,sort_keys=True),"fetched_at":r.fetched_at,"provider_version":r.provider_version,"schema_version":"ASL_BAOSTOCK_DAILY_FACTS_RAW_V01"} for r in raw_rows])
    _write_parquet(staging/"canonical.parquet",facts)
    _atomic_json(staging/"quality_receipt.json",receipt)
    result={"run":RUN,"symbols":symbols,"sample_roles":samples,"quality_receipt":receipt,"published":False}
    if not receipt["PASS"]:
        _atomic_json(staging/"execution_receipt.json",result); return result
    published=[]
    for day in sorted({r["trade_date"] for r in facts}):
        target=root/"curated/daily_facts"/f"trade_date={day}"/"part-vertical-slice-v01.parquet"
        _write_parquet(target,[r for r in facts if r["trade_date"]==day]); published.append(target)
    manifest=_manifest(root,published); _atomic_json(staging/"manifest.json",manifest)
    plan={"schema":"ASL_DAILY_FACTS_PUBLICATION_PLAN_V01","scope":"VERTICAL_SLICE","manifest":manifest,"quality_receipt":"staging/"+RUN+"/quality_receipt.json","provider_raw":"raw/baostock/daily_facts/"+RUN+"/provider_raw.parquet","published_as_of":END.isoformat(),"symbols":symbols}
    _atomic_json(staging/"promotion_plan.json",plan)
    promotion={"schema":"ASL_DAILY_FACTS_PROMOTION_RECEIPT_V01","STATE":"COMMITTED","manifest_hash":manifest["manifest_hash"],"quality_hash":_sha(receipt),"quality":receipt,"scope":"VERTICAL_SLICE","published_as_of":END.isoformat()}
    _atomic_json(staging/"promotion_receipt.json",promotion)
    pointer={"schema":"ASL_PUBLISHED_DAILY_FACTS_AUTHORITY_V01","plan":"staging/"+RUN+"/promotion_plan.json","receipt":"staging/"+RUN+"/promotion_receipt.json","manifest_hash":manifest["manifest_hash"]}
    _atomic_json(root/"meta/asl/daily_facts/published-daily-facts-authority.json",pointer)
    result.update({"published":True,"manifest_hash":manifest["manifest_hash"]}); _atomic_json(staging/"execution_receipt.json",result); return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--data-root",type=Path,default=DEFAULT_DATA_ROOT); args=parser.parse_args()
    try: print(json.dumps(execute(args.data_root.resolve()),ensure_ascii=False,sort_keys=True))
    except DailyFactsError as exc: print(json.dumps({"error":exc.code}),file=sys.stderr); raise SystemExit(2)
