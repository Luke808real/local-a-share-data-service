#!/usr/bin/env python3
"""Fail-closed certification of the nine already-physical R3 daily partitions.

This is deliberately a narrow evidence/promotion tool.  It never fetches a
date outside the frozen 42-key list and it never rewrites a parquet partition.
"""
from __future__ import annotations

import argparse, hashlib, json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path

BASE_HEAD = "6c12694b35d2a0efa0365fff38ca7de85c2e3397"
ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
STAGE = ROOT / "staging/r3_incremental_publication_cert_v01"
OLD_PLAN = ROOT / "staging/r3_proven_missing_4key_repair_v01/transaction/promotion_plan.json"
OLD_RECEIPT = ROOT / "staging/r3_proven_missing_4key_repair_v01/transaction/promotion_receipt.json"
AUDIT = Path(__file__).resolve().parents[1] / "reports/implementation/QUERY_INCREMENTAL_PUBLICATION_AUDIT_V01.json"
EXPECTED_HASH = "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"
EXPECTED_AUDIT_SHA256 = "9e7abb573d857e28ed51b71781cfe63e6ba7a8e925a884e97ecc549dec82b3d1"

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

def audit_evidence():
    raw=AUDIT.read_bytes()
    actual_sha256=digest(raw)
    if actual_sha256 != EXPECTED_AUDIT_SHA256:
        raise RuntimeError("AUDIT_EVIDENCE_HASH_MISMATCH")
    report=json.loads(raw)
    target_dates=["2026-08-18","2026-08-19","2026-08-20","2026-08-21","2026-08-24","2026-08-25","2026-08-26","2026-08-27","2026-08-28"]
    if report.get("verdict") != "NOT_CERTIFIED_FOR_PUBLICATION" or [d.get("date") for d in report.get("days",[])] != target_dates:
        raise RuntimeError("AUDIT_EVIDENCE_INVALID")
    out=[]
    for day in report["days"]:
        quality=day.get("quality",{})
        if not day.get("structural_quality_pass") or any(quality.get(k) != 0 for k in ("duplicate_keys","ohlc_bad_rows","negative_volume_rows","negative_amount_rows","provenance_bad_rows","symbol_bad_rows","date_mismatch_rows","null_rows")):
            raise RuntimeError("AUDIT_QUALITY_GATE_FAILED")
        out += [(x["symbol"], x["trade_date"]) for x in day["requested_not_observed_keys"]]
    if len(out)!=42 or len(set(out))!=42 or report.get("requested_not_observed_n")!=42:
        raise RuntimeError("AUDIT_UNRESOLVED_SET_INVALID")
    return sorted(out), {"path":str(AUDIT),"sha256":actual_sha256,"target_dates":target_dates}

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
            if r.get("code") != x["requested_code"] or r.get("date") != x["trade_date"]:
                c="SOURCE_ERROR"
            else: c="SUSPENDED" if r.get("tradestatus")=="0" else "BAR_PRESENT"
        out.append({**x,"final_classification":c})
    return out

def validate(classified):
    bad=[x for x in classified if x["final_classification"] not in {"SUSPENDED","BAR_PRESENT"}]
    if bad: raise RuntimeError("PUBLICATION_BLOCKED:"+bad[0]["final_classification"])
    # BAR_PRESENT needs a repair; this tool cannot invent a canonical conversion.
    bars=[x for x in classified if x["final_classification"]=="BAR_PRESENT"]
    if bars: raise RuntimeError("PUBLICATION_BLOCKED:TARGETED_REPAIR_REQUIRED")

def promote(classified, audit):
    old=old_manifest(); files=list(old["FILES"])
    pending=sorted(ROOT.glob("curated/daily_bars/trade_date=2026-08-*/part-merged.parquet"))
    pending=[p for p in pending if p.parent.name >= "trade_date=2026-08-18"]
    if len(pending)!=9: raise RuntimeError("PENDING_FILE_INVENTORY_DRIFT")
    for p in pending:
        files.append({"relative_path":str(p.relative_to(ROOT)),"file_size":p.stat().st_size,"sha256":sha_file(p)})
    files.sort(key=lambda x:x["relative_path"])
    manifest={"INPUT_FILE_N":len(files),"INPUT_MANIFEST_HASH":digest(canonical(files)),"CANONICAL_SERIALIZATION":"json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(',', ':')) sorted by relative_path","FILES":files}
    quality={"STRUCTURAL_PASS":all(x["final_classification"]=="SUSPENDED" for x in classified),"COVERAGE_PASS":len(classified)==42,"PROVENANCE_PASS":bool(audit.get("sha256")),"UNRESOLVED_KEY_N":0,"SOURCE_ERROR_N":sum(x["final_classification"]=="SOURCE_ERROR" for x in classified),"MAX_TRADE_DATE":"2026-08-28"}
    if not all((quality["STRUCTURAL_PASS"],quality["COVERAGE_PASS"],quality["PROVENANCE_PASS"],quality["SOURCE_ERROR_N"]==0)):
        raise RuntimeError("QUALITY_GATE_FAILED")
    receipt={"TASK":"ASL_R3_INCREMENTAL_PUBLICATION_CERTIFICATION_V01","STATE":"COMMITTED","OLD_INPUT_MANIFEST_HASH":old["INPUT_MANIFEST_HASH"],"POST_INPUT_FILE_N":len(files),"POST_INPUT_MANIFEST_HASH":manifest["INPUT_MANIFEST_HASH"],"EXPECTED_POST_INPUT_FILE_N":len(files),"EXPECTED_POST_INPUT_MANIFEST_HASH":manifest["INPUT_MANIFEST_HASH"],"QUALITY":quality,"AUDIT_EVIDENCE":audit,"CLASSIFICATION_SHA256":digest(canonical(classified))}
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
    old_manifest(); items,audit=audit_evidence()
    if not a.execute:
        print(json.dumps({"status":"READY","key_n":len(items),"keys":items})); return
    result=classify(query_baostock(items)); atomic_json(STAGE/"provider_receipt.json", {"scope":items,"audit_evidence":audit,"network_request_n":len(items),"results":result})
    if sorted((x["symbol"],x["trade_date"]) for x in result) != items: raise RuntimeError("QUERY_SCOPE_DRIFT")
    validate(result); manifest=promote(result,audit)
    print(json.dumps({"status":"PROMOTED","file_n":manifest["INPUT_FILE_N"],"manifest_hash":manifest["INPUT_MANIFEST_HASH"]}))
if __name__=="__main__": main()
