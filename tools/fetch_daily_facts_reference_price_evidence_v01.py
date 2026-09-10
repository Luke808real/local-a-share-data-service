#!/usr/bin/env python3
"""Fetch bounded, official CNInfo disclosure originals for frozen 2026-09-09 keys.

This is not a market-data provider.  It only retrieves the sixteen official
corporate-action disclosure PDFs required to explain already persisted
BaoStock/R3 reference-price mismatches.  It never touches Daily Facts RAW,
normalization, R3 data, or an authority pointer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ashare_data.local_query import DEFAULT_DATA_ROOT  # noqa: E402


SCHEMA = "ASL_OFFICIAL_REFERENCE_PRICE_SOURCE_RECEIPT_V01"
RUN_NAME = "daily_facts_phase1_20260909_v01"
TARGETS = (
    "001400.SZ", "002073.SZ", "002315.SZ", "002322.SZ", "002441.SZ", "002833.SZ", "002841.SZ", "300196.SZ",
    "300622.SZ", "301151.SZ", "600114.SH", "603992.SH", "603993.SH", "605377.SH", "688128.SH", "688271.SH",
)
SEARCH_START = "2026-08-15"
SEARCH_END = "2026-09-10"
CNINFO_SEARCH = "https://www.cninfo.com.cn/new/fulltextSearch/full"
CNINFO_STATIC = "https://static.cninfo.com.cn/"


class EvidenceFetchError(RuntimeError):
    pass


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _request(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise EvidenceFetchError(f"CNINFO_HTTP_{response.status}")
        return response.read()


def _search(symbol: str, search_start: str = SEARCH_START, search_end: str = SEARCH_END) -> list[dict[str, Any]]:
    code = symbol.split(".", 1)[0]
    params = {
        "searchkey": f"{code} 权益分派 实施公告",
        "sdate": search_start, "edate": search_end, "isfulltext": "true",
        "sortName": "", "sortType": "", "pageNum": "1", "pageSize": "30", "type": "",
    }
    payload = json.loads(_request(CNINFO_SEARCH + "?" + urllib.parse.urlencode(params)).decode("utf-8"))
    records = payload.get("announcements") or []
    candidates = [
        item for item in records
        if item.get("secCode") == code
        and re.search(r"权益.*分派.*实施公告$", re.sub(r"<[^>]+>", "", str(item.get("announcementTitle", ""))))
        and item.get("adjunctUrl", "").endswith(".PDF")
    ]
    if len(candidates) != 1:
        raise EvidenceFetchError(f"CNINFO_CANDIDATE_CARDINALITY:{symbol}:{len(candidates)}")
    return candidates


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def fetch(root: Path, *, execute: bool, run_name: str = RUN_NAME, symbols: Iterable[str] = TARGETS,
          search_start: str = SEARCH_START, search_end: str = SEARCH_END) -> dict[str, Any]:
    root = root.resolve()
    targets = tuple(symbols)
    if not targets:
        raise EvidenceFetchError("NO_TARGET_SYMBOLS")
    destination = root / "raw" / "official_disclosures" / run_name / "cninfo"
    records: list[dict[str, Any]] = []
    for symbol in targets:
        candidate = _search(symbol, search_start, search_end)[0]
        adjunct = str(candidate["adjunctUrl"]).lstrip("/")
        source_url = CNINFO_STATIC + adjunct
        payload = _request(source_url)
        if not payload.startswith(b"%PDF"):
            raise EvidenceFetchError(f"CNINFO_NOT_PDF:{symbol}")
        filename = _safe_filename(str(candidate["announcementId"])) + ".pdf"
        relative_path = Path("raw") / "official_disclosures" / run_name / "cninfo" / symbol / filename
        record = {
            "symbol": symbol, "announcement_id": str(candidate["announcementId"]),
            "announcement_title": re.sub(r"<[^>]+>", "", str(candidate["announcementTitle"])),
            "announcement_time_ms": candidate.get("announcementTime"), "source": "CNINFO_OFFICIAL_DISCLOSURE",
            "source_url": source_url, "source_hash": _sha256(payload), "source_relative_path": relative_path.as_posix(),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }
        if execute:
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and _sha256(path.read_bytes()) != record["source_hash"]:
                raise EvidenceFetchError(f"IMMUTABLE_SOURCE_HASH_DRIFT:{symbol}")
            if not path.exists():
                path.write_bytes(payload)
        records.append(record)
    receipt = {"schema": SCHEMA, "run_name": run_name, "target_symbol_n": len(targets), "records": records}
    receipt["receipt_hash"] = _sha256(json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode())
    if execute:
        path = destination / "source_receipt.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-name", default=RUN_NAME)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--search-start", default=SEARCH_START)
    parser.add_argument("--search-end", default=SEARCH_END)
    args = parser.parse_args()
    try:
        print(json.dumps(fetch(args.data_root, execute=args.execute, run_name=args.run_name,
                               symbols=args.symbols or TARGETS, search_start=args.search_start,
                               search_end=args.search_end), ensure_ascii=False, sort_keys=True))
    except EvidenceFetchError as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(2)
