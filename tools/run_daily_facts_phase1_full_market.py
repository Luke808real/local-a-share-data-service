#!/usr/bin/env python3
"""Historical-bootstrap migration runner; not the future daily updater.

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
    DailyFactsError,
    FROZEN_BAOSTOCK_RUNTIME_VERSION,
    PROVIDER,
    PROVIDER_FIELDS,
    ProviderRawRow,
    normalize,
)
from ashare_data.cnequity_bridge import CNEquityBaoStockDailyFactsBridge, DailyFactsRequest  # noqa: E402
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
        error_code text, parity_status text, schema text not null,
        required_start text, required_end text)""")
    con.execute("create table if not exists metadata (key text primary key, value text not null)")
    columns = {row[1] for row in con.execute("pragma table_info(units)")}
    for name in ("last_error_code", "last_error_at", "required_start", "required_end"):
        if name not in columns:
            con.execute(f"alter table units add column {name} text")
    return con


def _metadata(con: sqlite3.Connection, key: str) -> Any | None:
    row = con.execute("select value from metadata where key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _set_metadata(con: sqlite3.Connection, key: str, value: Any) -> None:
    con.execute("insert or replace into metadata(key,value) values(?,?)", (key, json.dumps(value, sort_keys=True)))


def published_scope(root: Path, *, start: date | None = None, end: date | None = None) -> tuple[list[str], dict[str, int], date, str, str]:
    """Get the formal R3 scope through the verified LocalQuery allowlist only."""
    with LocalQuery(root) as query:
        predicates = ["regexp_matches(symbol, '^[0-9]{6}\\.(SH|SZ)$')"]
        if start is not None:
            predicates.append(f"trade_date >= date '{start.isoformat()}'")
        if end is not None:
            predicates.append(f"trade_date <= date '{end.isoformat()}'")
        where = " and ".join(predicates)
        rows = query._execute(  # intentionally uses the already-verified published relation
            f"select symbol, count(*) as n from {query._daily_relation} where {where} group by symbol order by symbol"
        )
        symbols = [str(row["symbol"]) for row in rows]
        required = {str(row["symbol"]): int(row["n"]) for row in rows}
        status = query.status()
        # This relation contains only pointer-verified published files.  It is
        # deliberately captured after LocalQuery's full manifest validation,
        # rather than constructed from a physical glob.
        daily_relation = f"(select * from {query._daily_relation} where {where})"
    if not symbols:
        raise DailyFactsError("REQUIRED_SCOPE_EMPTY", "published R3 contains no formal SH/SZ symbols")
    return symbols, required, date.fromisoformat(status["DAILY_PUBLISHED_AS_OF"]), str(status["DAILY_MANIFEST_HASH"]), daily_relation


def published_ranges(daily_relation: str) -> dict[str, tuple[date, date]]:
    """Read exact per-symbol R3 dates once, outside the provider critical path."""
    import duckdb
    with duckdb.connect(":memory:") as con:
        rows = con.execute(
            "select symbol,min(trade_date),max(trade_date) from " + daily_relation + " group by symbol"
        ).fetchall()
    return {str(symbol): (start, end) for symbol, start, end in rows}


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


def _valid_raw_for_unit(path: Path, *, symbol: str, required_start: date, required_end: date) -> list[ProviderRawRow]:
    """Validate an immutable RAW file before it can change checkpoint state."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyFactsError("RAW_CONTRACT_FAILURE", "persisted RAW is unreadable") from exc
    try:
        fetched_start = date.fromisoformat(str(payload.get("requested_start")))
        fetched_end = date.fromisoformat(str(payload.get("requested_end")))
    except ValueError as exc:
        raise DailyFactsError("RAW_CONTRACT_FAILURE", "persisted RAW has invalid request range") from exc
    if (payload.get("symbol") != symbol or fetched_start > required_start
            or fetched_end < required_end):
        raise DailyFactsError("RAW_CONTRACT_FAILURE", "persisted RAW has wrong request scope")
    try:
        rows = _load_raw(path)
    except DailyFactsError as exc:
        raise DailyFactsError("RAW_CONTRACT_FAILURE", str(exc)) from exc
    if any(row.symbol != symbol or not fetched_start <= row.trade_date <= fetched_end for row in rows):
        raise DailyFactsError("RAW_CONTRACT_FAILURE", "persisted RAW escaped symbol/date scope")
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
    state = "NOT_COMPARABLE_NO_OVERLAP" if overlap == 0 else "EXACT" if differences == 0 else "UNEXPLAINED_DIFFERENCE"
    return (state,
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


def _record_error(con: sqlite3.Connection, symbol: str, code: str) -> None:
    con.execute("update units set error_code=?,last_error_code=?,last_error_at=? where symbol=?", (code, code, _utc(), symbol))


def _adopt_raw_if_present(con: sqlite3.Connection, *, root: Path, raw_root: Path, symbol: str,
                          raw_rel: str | None, required_start: date, required_end: date) -> bool:
    """Adopt valid immutable evidence, fail closed on malformed evidence."""
    raw_path = root / raw_rel if raw_rel else raw_root / f"{symbol}.json"
    if not raw_path.exists():
        return False
    try:
        rows = _valid_raw_for_unit(raw_path, symbol=symbol, required_start=required_start, required_end=required_end)
    except DailyFactsError:
        con.execute("update units set state='QUALITY_FAIL',completed_at=? where symbol=?", (_utc(), symbol))
        _record_error(con, symbol, "RAW_CONTRACT_FAILURE")
        return True
    con.execute("update units set state='RAW_PERSISTED',raw_path=?,row_n=?,error_code=null where symbol=?",
                (str(raw_path.relative_to(root)), len(rows), symbol))
    return True


def _recover_interrupted(con: sqlite3.Connection, *, root: Path, raw_root: Path) -> None:
    """Recover every crash state based on immutable deterministic RAW paths."""
    for symbol, state, raw_rel, required_start, required_end in con.execute("select symbol,state,raw_path,required_start,required_end from units where state in ('FETCHING','NOT_STARTED')"):
        adopted = _adopt_raw_if_present(con, root=root, raw_root=raw_root, symbol=symbol,
                                        raw_rel=raw_rel, required_start=date.fromisoformat(required_start), required_end=date.fromisoformat(required_end))
        if not adopted and state == "FETCHING":
            con.execute("update units set state='NOT_STARTED' where symbol=?", (symbol,))
            _record_error(con, symbol, "INTERRUPTED_BEFORE_RAW")
    con.commit()


def _production_provider() -> CNEquityBaoStockDailyFactsBridge:
    """The only production provider entry point: a pinned, paced CNEquity session."""
    from cnequity.config import load_config

    return CNEquityBaoStockDailyFactsBridge(config=load_config(ROOT / "config/cnequity.toml"))


def _acquire_batch(con: sqlite3.Connection, *, root: Path, raw_root: Path, provider: Any,
                   requests: list[DailyFactsRequest]) -> int:
    """One explicit ASL checkpoint batch maps to one CNEquity session sweep.

    CNEquity alone retries individual provider requests.  ASL records only the
    terminal outcome of that sweep; an operator must explicitly open any later
    retry cycle.
    """
    for request in requests:
        con.execute(
            "update units set state='FETCHING',request_id=?,attempts=attempts+1,started_at=? where symbol=?",
            (str(uuid.uuid4()), _utc(), request.symbol),
        )
    con.commit()
    try:
        grouped, failed = provider.fetch_batch(requests)
    except DailyFactsError as exc:
        grouped, failed = {}, tuple(request.symbol for request in requests)
        error_code = exc.code
    else:
        error_code = "SOURCE_ERROR"
    failed_set = set(failed)
    fetched = 0
    for request in requests:
        if request.symbol in failed_set:
            con.execute("update units set state='PROVIDER_FAIL',completed_at=?,error_code=? where symbol=?",
                        (_utc(), error_code, request.symbol))
            continue
        raw = grouped.get(request.symbol)
        if raw is None:
            con.execute("update units set state='PROVIDER_FAIL',completed_at=?,error_code='SOURCE_ERROR' where symbol=?",
                        (_utc(), request.symbol))
            continue
        raw_path = raw_root / f"{request.symbol}.json"
        if raw_path.exists():
            con.execute("update units set state='QUALITY_FAIL',completed_at=?,error_code='RAW_DUPLICATE' where symbol=?",
                        (_utc(), request.symbol))
            continue
        request_id = con.execute("select request_id from units where symbol=?", (request.symbol,)).fetchone()[0]
        _atomic_json(raw_path, _raw_payload(request.symbol, request.required_start, request.required_end, request_id, raw))
        con.execute("update units set state='RAW_PERSISTED',raw_path=?,row_n=?,error_code=null where symbol=?",
                    (str(raw_path.relative_to(root)), len(raw), request.symbol))
        fetched += 1
    con.commit()
    return fetched


def execute(root: Path, *, run_name: str = RUN, symbols: list[str] | None = None,
            start: date = date(2016, 1, 1), end: date | None = None, max_symbols: int = 0,
            retry_quality_fail: bool = False, retry_provider_fail: bool = False,
            acquire_only: bool = False, postprocess_only: bool = False,
            provider_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    if acquire_only and postprocess_only:
        raise DailyFactsError("INVALID_MODE", "acquire-only and postprocess-only are exclusive")
    root = root.resolve()
    formal_symbols, required, as_of, manifest_hash, daily_relation = (
        published_scope(root) if start == date(2016, 1, 1) and end is None
        else published_scope(root, start=start, end=end)
    )
    ranges = published_ranges(daily_relation)
    selected = sorted(set(symbols or formal_symbols))
    if not set(selected).issubset(formal_symbols):
        raise DailyFactsError("OUTSIDE_FORMAL_SCOPE", "runner scope includes non-published-R3 symbol")
    staging, raw_root, database = run_paths(root, run_name)
    con = _db(database)
    try:
        plan = {"symbols": selected, "symbol_hash": _sha(selected), "as_of": as_of.isoformat(),
                "start": start.isoformat(), "end": (end or as_of).isoformat(), "daily_manifest_hash": manifest_hash, "schema": SCHEMA}
        existing = _metadata(con, "plan")
        if existing is not None and existing != plan:
            raise DailyFactsError("RUN_PLAN_DRIFT", "existing run plan differs from current formal scope")
        _set_metadata(con, "plan", plan)
        # First store deterministic published-R3 request boundaries.  Existing
        # broad RAW remains valid because validation accepts a containing range.
        for symbol in selected:
            if symbol not in ranges:
                raise DailyFactsError("REQUIRED_SCOPE_EMPTY", f"missing R3 range for {symbol}")
            required_start, required_end = ranges[symbol]
            con.execute("insert or ignore into units(symbol,required_n,state,schema,required_start,required_end) values(?,?,?,?,?,?)",
                        (symbol, required[symbol], "NOT_STARTED", SCHEMA, required_start.isoformat(), required_end.isoformat()))
            con.execute("update units set required_start=coalesce(required_start,?),required_end=coalesce(required_end,?) where symbol=?",
                        (required_start.isoformat(), required_end.isoformat(), symbol))
        con.commit()
        _recover_interrupted(con, root=root, raw_root=raw_root)
        if retry_quality_fail:
            # Recovery after a runner/certification repair never permits a
            # provider refetch: persisted evidence is the sole input.
            con.execute("update units set state='RAW_PERSISTED',error_code='QUALITY_RETRY_FROM_PERSISTED_RAW' where state='QUALITY_FAIL' and raw_path is not null")
        if retry_provider_fail:
            # An operator explicitly opens a new bounded retry cycle.  Prior
            # attempts/error timestamps remain in the ledger; valid RAW wins.
            for symbol, raw_rel in con.execute("select symbol,raw_path from units where state='PROVIDER_FAIL'"):
                required_start, required_end = ranges[symbol]
                if not _adopt_raw_if_present(con, root=root, raw_root=raw_root, symbol=symbol,
                                             raw_rel=raw_rel, required_start=required_start, required_end=required_end):
                    con.execute("update units set state='NOT_STARTED',completed_at=null,error_code='EXPLICIT_PROVIDER_RETRY' where symbol=?", (symbol,))
        con.commit()
        states = "('RAW_PERSISTED')" if postprocess_only else "('NOT_STARTED','RAW_PERSISTED')"
        eligible = con.execute("select symbol,state,required_n,raw_path,required_start,required_end from units where state in " + states + " order by symbol" + (" limit ?" if max_symbols else ""), (() if not max_symbols else (max_symbols,))).fetchall()
        fetched = 0
        selected_eligible = {row[0] for row in eligible}
        pending: list[DailyFactsRequest] = []
        if not postprocess_only:
            for symbol, state, _required_n, raw_rel, required_start_text, required_end_text in eligible:
                required_start, required_end = date.fromisoformat(required_start_text), date.fromisoformat(required_end_text)
                if state == "NOT_STARTED" and not _adopt_raw_if_present(
                    con, root=root, raw_root=raw_root, symbol=symbol, raw_rel=raw_rel,
                    required_start=required_start, required_end=required_end,
                ):
                    pending.append(DailyFactsRequest(symbol, required_start, required_end))
            con.commit()
            if pending:
                factory = provider_factory or _production_provider
                with factory() as provider:
                    batch_size = int(getattr(provider.config, "baostock_batch_size", 0))
                    if batch_size <= 0:
                        raise DailyFactsError("INVALID_CNEQUITY_CONFIG", "BaoStock batch size must be positive")
                    for index in range(0, len(pending), batch_size):
                        fetched += _acquire_batch(
                            con, root=root, raw_root=raw_root, provider=provider,
                            requests=pending[index:index + batch_size],
                        )
        if not acquire_only:
            import duckdb
            with duckdb.connect(":memory:") as daily_con:
                current = con.execute(
                    "select symbol,raw_path,required_start,required_end from units "
                    "where state='RAW_PERSISTED' and symbol in (" + ",".join("?" for _ in selected_eligible) + ") order by symbol",
                    tuple(sorted(selected_eligible)),
                ).fetchall() if selected_eligible else []
                for symbol, raw_rel, required_start_text, required_end_text in current:
                    required_start, required_end = date.fromisoformat(required_start_text), date.fromisoformat(required_end_text)
                    raw_path = root / raw_rel
                    try:
                        raw = _valid_raw_for_unit(raw_path, symbol=symbol, required_start=required_start, required_end=required_end)
                        # BaoStock can return dates outside this formal R3 scope
                        # (for example a pre-membership history).  Retain them in
                        # RAW, but only normalize the current published key set.
                        expected_dates = {str(row[0]) for row in daily_con.execute(
                            "select cast(trade_date as varchar) from " + daily_relation + " where symbol=?", [symbol]
                        ).fetchall()}
                        facts = [row for row in normalize(raw) if row["trade_date"] in expected_dates]
                        _validate(symbol, facts, expected_dates)
                        parity, _counts = _parity(root, facts)
                        if parity == "UNEXPLAINED_DIFFERENCE":
                            raise DailyFactsError("UNEXPLAINED_R4A9_PARITY_DIFFERENCE", "historical regression parity mismatch")
                        normalized = staging / "normalized" / f"{symbol}.parquet"
                        if not normalized.exists():
                            _write_parquet(normalized, facts)
                        con.execute("update units set state='NORMALIZED',normalized_path=?,parity_status=? where symbol=?", (str(normalized.relative_to(root)), parity, symbol)); con.commit()
                        con.execute("update units set state='QUALITY_PASS',completed_at=?,error_code=null where symbol=?", (_utc(), symbol)); con.commit()
                    except DailyFactsError as exc:
                        con.execute("update units set state='QUALITY_FAIL',completed_at=?,error_code=? where symbol=?", (_utc(), exc.code, symbol)); con.commit()
        states = dict(con.execute("select state,count(*) from units group by state").fetchall())
        return {"run": run_name, "checkpoint_path": str(database), "raw_storage_path": str(raw_root),
                "as_of": as_of.isoformat(), "selected_symbol_n": len(selected), "network_fetched_symbol_n": fetched,
                "states": states, "mode": "ACQUIRE_ONLY" if acquire_only else "POSTPROCESS_ONLY" if postprocess_only else "FULL_PIPELINE", "publication": "STAGING_ONLY_NOT_PUBLISHED"}
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
    parser.add_argument("--start", type=date.fromisoformat, default=date(2016, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--retry-quality-fail", action="store_true")
    parser.add_argument("--retry-provider-fail", action="store_true")
    parser.add_argument("--acquire-only", action="store_true")
    parser.add_argument("--postprocess-only", action="store_true")
    parser.add_argument("--discover", action="store_true")
    args = parser.parse_args()
    if args.discover:
        print(json.dumps(discover(args.data_root), ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(execute(args.data_root, run_name=args.run_name, symbols=args.symbols, start=args.start, end=args.end, max_symbols=args.max_symbols, retry_quality_fail=args.retry_quality_fail, retry_provider_fail=args.retry_provider_fail, acquire_only=args.acquire_only, postprocess_only=args.postprocess_only), ensure_ascii=False, sort_keys=True))
