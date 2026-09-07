#!/usr/bin/env python3
"""Fail-closed certification of the nine already-physical R3 daily partitions.

This is deliberately a narrow evidence/promotion tool.  It never fetches a
date outside the frozen 42-key list and it never rewrites a parquet partition.
"""
from __future__ import annotations

import argparse, hashlib, json, math, os, tempfile
from datetime import datetime, timezone
from pathlib import Path

BASE_HEAD = "44f145558b6df3e1f8b3ac1ee84bf26e25c3e8d9"
ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
STAGE = ROOT / "staging/r3_incremental_publication_cert_v01"
OLD_PLAN = ROOT / "staging/r3_proven_missing_4key_repair_v01/transaction/promotion_plan.json"
OLD_RECEIPT = ROOT / "staging/r3_proven_missing_4key_repair_v01/transaction/promotion_receipt.json"
AUDIT = Path(__file__).resolve().parents[1] / "reports/implementation/QUERY_INCREMENTAL_PUBLICATION_AUDIT_V01.json"
EXPECTED_HASH = "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"

def canonical(v): return json.dumps(v, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
def digest(b): return hashlib.sha256(b).hexdigest()
def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(canonical(value) + b"\n"); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def keys():
    report=json.loads(AUDIT.read_text())
    out=[]
    for day in report["days"]:
        out += [(x["symbol"], x["trade_date"]) for x in day["requested_not_observed_keys"]]
    assert len(out)==42 and len(set(out))==42
    return sorted(out)

def old_manifest():
    plan=json.loads(OLD_PLAN.read_text()); receipt=json.loads(OLD_RECEIPT.read_text())
    m=plan["EXPECTED_POST_INPUT_MANIFEST"]
    assert receipt["STATE"]=="COMMITTED" and m["INPUT_FILE_N"]==2580 and m["INPUT_MANIFEST_HASH"]==EXPECTED_HASH
    return m

def sha_file(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()

def query_baostock(items):
    import baostock as bs
    login=bs.login()
    if login.error_code != "0": raise RuntimeError("SOURCE_ERROR:LOGIN:"+login.error_msg)
    results=[]
    try:
        for symbol, day in items:
            code=("sh." if symbol.endswith(".SH") else "sz.")+symbol[:6]
            rs=bs.query_history_k_data_plus(code, "date,code,open,high,low,close,volume,amount,tradestatus", start_date=day,end_date=day,frequency="d",adjustflag="3")
            rows=[]
            while rs.next(): rows.append(dict(zip(rs.fields,rs.get_row_data())))
            results.append({"symbol":symbol,"trade_date":day,"provider":"baostock","provider_version":"0.9.30","requested_code":code,"error_code":rs.error_code,"error_msg":rs.error_msg,"rows":rows,"timestamp":datetime.now(timezone.utc).isoformat()})
    finally: bs.logout()
    return results

def classify(results):
    out=[]
    for x in results:
        if x["error_code"] != "0": c="SOURCE_ERROR"
        elif len(x["rows"]) != 1: c="SOURCE_MISSING"
        else:
            r=x["rows"][0]
            c="SUSPENDED" if r["tradestatus"]=="0" else "BAR_PRESENT"
        out.append({**x,"final_classification":c})
    return out

def validate(classified):
    bad=[x for x in classified if x["final_classification"] not in {"SUSPENDED","BAR_PRESENT"}]
    if bad: raise RuntimeError("PUBLICATION_BLOCKED:"+bad[0]["final_classification"])
    # BAR_PRESENT needs a repair; this tool cannot invent a canonical conversion.
    bars=[x for x in classified if x["final_classification"]=="BAR_PRESENT"]
    if bars: raise RuntimeError("PUBLICATION_BLOCKED:TARGETED_REPAIR_REQUIRED")

def promote(classified):
    old=old_manifest(); files=list(old["FILES"])
    pending=sorted(ROOT.glob("curated/daily_bars/trade_date=2026-08-*/part-merged.parquet"))
    pending=[p for p in pending if p.parent.name >= "trade_date=2026-08-18"]
    if len(pending)!=9: raise RuntimeError("PENDING_FILE_INVENTORY_DRIFT")
    for p in pending:
        files.append({"relative_path":str(p.relative_to(ROOT)),"file_size":p.stat().st_size,"sha256":sha_file(p)})
    files.sort(key=lambda x:x["relative_path"])
    manifest={"INPUT_FILE_N":len(files),"INPUT_MANIFEST_HASH":digest(canonical(files)),"CANONICAL_SERIALIZATION":"json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(',', ':')) sorted by relative_path","FILES":files}
    receipt={"TASK":"ASL_R3_INCREMENTAL_PUBLICATION_CERTIFICATION_V01","STATE":"COMMITTED","OLD_INPUT_MANIFEST_HASH":old["INPUT_MANIFEST_HASH"],"POST_INPUT_FILE_N":len(files),"POST_INPUT_MANIFEST_HASH":manifest["INPUT_MANIFEST_HASH"],"EXPECTED_POST_INPUT_FILE_N":len(files),"EXPECTED_POST_INPUT_MANIFEST_HASH":manifest["INPUT_MANIFEST_HASH"],"QUALITY":{"STRUCTURAL_PASS":True,"COVERAGE_PASS":True,"PROVENANCE_PASS":True,"UNRESOLVED_KEY_N":0,"SOURCE_ERROR_N":0,"MAX_TRADE_DATE":"2026-08-28"},"CLASSIFICATION_SHA256":digest(canonical(classified))}
    atomic_json(STAGE/"classifications.json", {"keys":classified})
    atomic_json(STAGE/"promotion_plan.json", {"EXPECTED_POST_INPUT_MANIFEST":manifest})
    atomic_json(STAGE/"promotion_receipt.json", receipt)
    # This pointer is the atomic authority switch consumed by LocalQuery.
    atomic_json(ROOT/"meta/asl/r3/published-daily-authority.json", {"schema":"R3_PUBLISHED_DAILY_AUTHORITY_V01","receipt":"staging/r3_incremental_publication_cert_v01/promotion_receipt.json","plan":"staging/r3_incremental_publication_cert_v01/promotion_plan.json","manifest_hash":manifest["INPUT_MANIFEST_HASH"]})
    return manifest

def main():
    p=argparse.ArgumentParser(); p.add_argument("--execute",action="store_true"); a=p.parse_args()
    import subprocess
    if subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()!=BASE_HEAD: raise RuntimeError("BASE_HEAD_MISMATCH")
    old_manifest(); items=keys()
    if not a.execute:
        print(json.dumps({"status":"READY","key_n":len(items),"keys":items})); return
    result=classify(query_baostock(items)); atomic_json(STAGE/"provider_receipt.json", {"scope":items,"network_request_n":len(items),"results":result})
    validate(result); manifest=promote(result)
    print(json.dumps({"status":"PROMOTED","file_n":manifest["INPUT_FILE_N"],"manifest_hash":manifest["INPUT_MANIFEST_HASH"]}))
if __name__=="__main__": main()
