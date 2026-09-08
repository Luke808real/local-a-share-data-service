#!/usr/bin/env python3
"""Current-contract, RAW-preserving Daily Facts Phase 1 full-market runner.

The runner deliberately has no publication operation.  It acquires immutable
BaoStock evidence into a separate staging run, then normalizes only from that
persisted evidence.  A promotion implementation must consume a completed run
and its formal quality receipt separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ashare_data.daily_facts_phase1 import (  # noqa: E402
    BaoStockDailyFactsAdapter,
    DailyFactsError,
    FROZEN_BAOSTOCK_RUNTIME_VERSION,
    PROVIDER,
    PROVIDER_FIELDS,
    ProviderRawRow,
    normalize,
)
from ashare_data.local_query import DEFAULT_DATA_ROOT, LocalQuery  # noqa: E402


RUN = "daily_facts_phase1_full_market_v01"
SCHEMA = "ASL_DAILY_FACTS_FULL_MARKET_RUNNER_V01"
RAW_SCHEMA = "ASL_BAOSTOCK_DAILY_FACTS_RAW_V02"
TERMINAL = {"QUALITY_PASS", "PROVIDER_FAIL", "QUALITY_FAIL"}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write a new parquet atomically; existing normalized evidence is immutable."""
    if path.exists():
        raise DailyFactsError("NORMALIZED_DUPLICATE", f"refusing to overwrite {path}")
    import duckdb
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False, dir=path.parent) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
        source = handle.name
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with duckdb.connect(":memory:") as con:
            destination = str(temporary).replace("'", "''")
            con.execute("COPY (SELECT * FROM read_json_auto(?)) TO '" + destination + "' (FORMAT PARQUET)", [source])
        os.replace(temporary, path)
    finally:
        if os.path.exists(source):
            os.unlink(source)
        if temporary.exists():
            temporary.unlink()


def run_paths(root: Path, run_name: str = RUN) -> tuple[Path, Path, Path]:
    staging = root / "staging" / run_name
    return staging, root / "raw" / "baostock" / "daily_facts" / run_name, staging / "progress.sqlite"


def _db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("pragma journal_mode=WAL")
    con.execute("""create table if not exists units (
        symbol text primary key, required_n integer not null, state text not null,
        request_id text, attempts integer not null default 0, started_at text,
        completed_at text, raw_path text, normalized_path text, row_n integer,
        error_code text, parity_status text, schema text not null)""")
    con.execute("create table if not exists metadata (key text primary key, value text not null)")
    return con


def _metadata(con: sqlite3.Connection, key: str) -> Any | None:
    row = con.execute("select value from metadata where key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _set_metadata(con: sqlite3.Connection, key: str, value: Any) -> None:
    con.execute("insert or replace into metadata(key,value) values(?,?)", (key, json.dumps(value, sort_keys=True)))


def published_scope(root: Path) -> tuple[list[str], dict[str, int], date, str, str]:
    """Get the formal R3 scope through the verified LocalQuery allowlist only."""
    with LocalQuery(root) as query:
        rows = query._execute(  # intentionally uses the already-verified published relation
            f"select symbol, count(*) as n from {query._daily_relation} "
            "where regexp_matches(symbol, '^[0-9]{6}\\.(SH|SZ)$') group by symbol order by symbol"
        )
        symbols = [str(row["symbol"]) for row in rows]
        required = {str(row["symbol"]): int(row["n"]) for row in rows}
        status = query.status()
        # This relation contains only pointer-verified published files.  It is
        # deliberately captured after LocalQuery's full manifest validation,
        # rather than constructed from a physical glob.
        daily_relation = query._daily_relation
    if not symbols:
        raise DailyFactsError("REQUIRED_SCOPE_EMPTY", "published R3 contains no formal SH/SZ symbols")
    return symbols, required, date.fromisoformat(status["DAILY_PUBLISHED_AS_OF"]), str(status["DAILY_MANIFEST_HASH"]), daily_relation


def _raw_payload(symbol: str, start: date, end: date, request_id: str, raw: Iterable[ProviderRawRow]) -> dict[str, Any]:
    rows = list(raw)
    return {
        "schema": RAW_SCHEMA,
        "provider": PROVIDER,
        "provider_version": f"baostock-{FROZEN_BAOSTOCK_RUNTIME_VERSION}",
        "provider_fields": list(PROVIDER_FIELDS),
        "request_id": request_id,
        "symbol": symbol,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "fetched_at": rows[0].fetched_at if rows else _utc(),
        "rows": [{"symbol": r.symbol, "trade_date": r.trade_date.isoformat(), "raw": r.raw,
                  "fetched_at": r.fetched_at, "provider_version": r.provider_version} for r in rows],
    }


def _load_raw(path: Path) -> list[ProviderRawRow]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyFactsError("RAW_UNREADABLE", f"cannot read persisted RAW {path}") from exc
    if (payload.get("schema") != RAW_SCHEMA or payload.get("provider") != PROVIDER
            or payload.get("provider_version") != f"baostock-{FROZEN_BAOSTOCK_RUNTIME_VERSION}"
            or tuple(payload.get("provider_fields", ())) != PROVIDER_FIELDS):
        raise DailyFactsError("RAW_CONTRACT_MISMATCH", "persisted RAW violates current provider contract")
    rows: list[ProviderRawRow] = []
    for item in payload.get("rows", []):
        raw = item.get("raw")
        if not isinstance(raw, dict) or set(raw) != set(PROVIDER_FIELDS):
            raise DailyFactsError("RAW_CONTRACT_MISMATCH", "lossless provider values are missing")
        rows.append(ProviderRawRow(str(item["symbol"]), date.fromisoformat(str(item["trade_date"])),
                                   {key: str(raw[key]) for key in PROVIDER_FIELDS}, str(item["fetched_at"]),
                                   str(item["provider_version"])))
    return rows


def _historical_preclose(root: Path, symbol: str) -> dict[tuple[str, str], tuple[float, str]]:
    """Old R4A9 is a diagnostic oracle, never an authority input."""
    path = root / "staging" / "r4a9-preclose-real-full-extraction-v01" / "units" / f"{symbol}.parquet"
    if not path.exists():
        return {}
    import duckdb
    with duckdb.connect(":memory:") as con:
        rows = con.execute("select cast(trade_date as varchar), preclose, provider_tradestatus from read_parquet(?)", [str(path)]).fetchall()
    return {(symbol, str(day)): (float(preclose), str(status)) for day, preclose, status in rows}


def _parity(root: Path, rows: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
    old = _historical_preclose(root, rows[0]["symbol"]) if rows else {}
    overlap = exact = 0
    for row in rows:
        candidate = old.get((row["symbol"], row["trade_date"]))
        if candidate is None:
            continue
        overlap += 1
        if abs(float(row["preclose"]) - candidate[0]) <= 0.000001 and str(row["provider_tradestatus"]) == candidate[1]:
            exact += 1
    differences = overlap - exact
    return ("EXACT" if differences == 0 else "UNEXPLAINED_DIFFERENCE",
            {"OVERLAP_ROW_N": overlap, "EXACT_PARITY_N": exact,
             "EXPECTED_DIFFERENCE_N": 0, "UNEXPLAINED_DIFFERENCE_N": differences})


def _validate(symbol: str, rows: list[dict[str, Any]], required_dates: set[str]) -> None:
    keys = [(row["symbol"], row["trade_date"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise DailyFactsError("DUPLICATE_PRIMARY_KEY", "duplicate normalized primary key")
    actual_dates = {row["trade_date"] for row in rows}
    if actual_dates != required_dates:
        raise DailyFactsError("REQUIRED_KEY_COVERAGE_MISMATCH", f"expected {len(required_dates)} R3 keys, got {len(actual_dates)}")
    for row in rows:
        if row["symbol"] != symbol or row["provider"] != PROVIDER:
            raise DailyFactsError("PROVIDER_IDENTITY_MISMATCH", "normalized provider identity mismatch")
        if row["provider_version"] != f"baostock-{FROZEN_BAOSTOCK_RUNTIME_VERSION}" or not row.get("raw_values"):
            raise DailyFactsError("PROVENANCE_FAILURE", "normalized row lacks frozen raw provenance")
        if row["trade_status"] == "TRADING" and (row["preclose"] is None or float(row["preclose"]) <= 0):
            raise DailyFactsError("INVALID_PRECLOSE", "traded R3 row lacks positive preclose")
        if row["trade_status"] == "UNKNOWN" or row["is_st"] == "UNKNOWN":
            raise DailyFactsError("UNKNOWN_FACT", "unknown tri-state fact cannot pass full quality")


def _recover_interrupted(con: sqlite3.Connection) -> None:
    con.execute("update units set state='RAW_PERSISTED',error_code='INTERRUPTED_AFTER_RAW' where state='FETCHING' and raw_path is not null")
    con.execute("update units set state='NOT_STARTED',error_code='INTERRUPTED_BEFORE_RAW' where state='FETCHING' and raw_path is null")


def execute(root: Path, *, run_name: str = RUN, symbols: list[str] | None = None,
            start: date = date(2016, 1, 1), max_symbols: int = 0,
            retry_quality_fail: bool = False,
            provider_factory: Callable[[], Any] = BaoStockDailyFactsAdapter) -> dict[str, Any]:
    root = root.resolve()
    formal_symbols, required, as_of, manifest_hash, daily_relation = published_scope(root)
    selected = sorted(set(symbols or formal_symbols))
    if not set(selected).issubset(formal_symbols):
        raise DailyFactsError("OUTSIDE_FORMAL_SCOPE", "runner scope includes non-published-R3 symbol")
    staging, raw_root, database = run_paths(root, run_name)
    con = _db(database)
    try:
        plan = {"symbols": selected, "symbol_hash": _sha(selected), "as_of": as_of.isoformat(),
                "start": start.isoformat(), "daily_manifest_hash": manifest_hash, "schema": SCHEMA}
        existing = _metadata(con, "plan")
        if existing is not None and existing != plan:
            raise DailyFactsError("RUN_PLAN_DRIFT", "existing run plan differs from current formal scope")
        _set_metadata(con, "plan", plan)
        _recover_interrupted(con)
        if retry_quality_fail:
            # Recovery after a runner/certification repair never permits a
            # provider refetch: persisted evidence is the sole input.
            con.execute("update units set state='RAW_PERSISTED',error_code='QUALITY_RETRY_FROM_PERSISTED_RAW' where state='QUALITY_FAIL' and raw_path is not null")
        for symbol in selected:
            con.execute("insert or ignore into units(symbol,required_n,state,schema) values(?,?,?,?)",
                        (symbol, required[symbol], "NOT_STARTED", SCHEMA))
        con.commit()
        eligible = con.execute("select symbol,state,required_n,raw_path from units where state in ('NOT_STARTED','RAW_PERSISTED') order by symbol" + (" limit ?" if max_symbols else ""), (() if not max_symbols else (max_symbols,))).fetchall()
        fetched = 0
        import duckdb
        with duckdb.connect(":memory:") as daily_con, provider_factory() as provider:
            for symbol, state, required_n, raw_rel in eligible:
                raw_path = root / raw_rel if raw_rel else raw_root / f"{symbol}.json"
                try:
                    if state == "NOT_STARTED":
                        request_id = str(uuid.uuid4())
                        con.execute("update units set state='FETCHING',request_id=?,attempts=attempts+1,started_at=?,error_code=null where symbol=?", (request_id, _utc(), symbol)); con.commit()
                        raw = provider.fetch(symbol, start, as_of)
                        payload = _raw_payload(symbol, start, as_of, request_id, raw)
                        if raw_path.exists():
                            raise DailyFactsError("RAW_DUPLICATE", "raw evidence already exists for incomplete unit")
                        _atomic_json(raw_path, payload)
                        con.execute("update units set state='RAW_PERSISTED',raw_path=?,row_n=? where symbol=?", (str(raw_path.relative_to(root)), len(raw), symbol)); con.commit()
                        fetched += 1
                    raw = _load_raw(raw_path)
                    # BaoStock can return dates outside this formal R3 scope
                    # (for example a pre-membership history).  Retain them in
                    # RAW, but only normalize the current published key set.
                    expected_dates = {str(row[0]) for row in daily_con.execute(
                        "select cast(trade_date as varchar) from " + daily_relation + " where symbol=?", [symbol]
                    ).fetchall()}
                    facts = [row for row in normalize(raw) if row["trade_date"] in expected_dates]
                    _validate(symbol, facts, expected_dates)
                    parity, _counts = _parity(root, facts)
                    if parity != "EXACT":
                        raise DailyFactsError("UNEXPLAINED_R4A9_PARITY_DIFFERENCE", "historical regression parity mismatch")
                    normalized = staging / "normalized" / f"{symbol}.parquet"
                    if not normalized.exists():
                        _write_parquet(normalized, facts)
                    con.execute("update units set state='NORMALIZED',normalized_path=?,parity_status=? where symbol=?", (str(normalized.relative_to(root)), parity, symbol)); con.commit()
                    con.execute("update units set state='QUALITY_PASS',completed_at=?,error_code=null where symbol=?", (_utc(), symbol)); con.commit()
                except DailyFactsError as exc:
                    state = "PROVIDER_FAIL" if exc.code in {"SOURCE_ERROR", "PROVIDER_SCHEMA_MISMATCH", "PROVIDER_IDENTITY_MISMATCH", "PROVIDER_VERSION_MISMATCH"} else "QUALITY_FAIL"
                    con.execute("update units set state=?,completed_at=?,error_code=? where symbol=?", (state, _utc(), exc.code, symbol)); con.commit()
        states = dict(con.execute("select state,count(*) from units group by state").fetchall())
        return {"run": run_name, "checkpoint_path": str(database), "raw_storage_path": str(raw_root),
                "as_of": as_of.isoformat(), "selected_symbol_n": len(selected), "network_fetched_symbol_n": fetched,
                "states": states, "publication": "STAGING_ONLY_NOT_PUBLISHED"}
    finally:
        con.close()


def discover(root: Path) -> list[dict[str, Any]]:
    """Inventory known artifacts without implying eligibility from row values."""
    root = root.resolve()
    candidates = [
        root / "raw/baostock/daily_facts/daily_facts_phase1_vertical_slice_v01/provider_raw.parquet",
        root / "raw/baostock/daily_facts/daily_facts_phase1_feasibility_v01",
        root / "staging/r4a9-preclose-real-full-extraction-v01/units",
    ]
    result=[]
    for path in candidates:
        if not path.exists():
            continue
        label = "R4A9_NORMALIZED_ONLY" if "r4a9" in str(path) else "EARLIER_RAW_CONTRACT"
        result.append({"path": str(path), "format": "parquet" if path.suffix == ".parquet" else "json-per-symbol" if path.is_dir() and "raw" in str(path) else "parquet-per-symbol",
                       "candidate": label, "authority_eligible": False,
                       "reason": "normalized-only: no raw provider strings" if label == "R4A9_NORMALIZED_ONLY" else "provider provenance is 00.9.30, not current frozen baostock-0.9.3 contract"})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--run-name", default=RUN)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--retry-quality-fail", action="store_true")
    parser.add_argument("--discover", action="store_true")
    args = parser.parse_args()
    if args.discover:
        print(json.dumps(discover(args.data_root), ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(execute(args.data_root, run_name=args.run_name, symbols=args.symbols, max_symbols=args.max_symbols, retry_quality_fail=args.retry_quality_fail), ensure_ascii=False, sort_keys=True))
