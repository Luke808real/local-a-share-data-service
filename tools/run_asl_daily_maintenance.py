#!/usr/bin/env python3
"""Single-writer daily maintenance entry point for the ASL R3 and Daily Facts authorities.

The command orchestrates the existing fail-closed primitives; it never
re-implements acquisition, normalization, certification or publication:

    resolve trade date
    -> inspect authority
    -> R3 incremental update
    -> R3 publication verification
    -> Daily Facts acquisition
    -> offline certification (bounded official evidence only when required)
    -> pointer-last Daily Facts publication
    -> local query and MCP smoke

Guarantees:

* single writer: an exclusive lock covers the whole mutation window;
* idempotent and resume-safe: an already published date performs no provider
  request, no partition write and no pointer switch, and an interrupted run
  resumes from the persisted ledgers;
* fail closed: any gate failure stops the chain and reports a BLOCKED status;
* pointer last: both authorities are switched only after their evidence is
  committed and verified.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from ashare_data.daily_facts_phase1 import DailyFactsError, reconcile  # noqa: E402
from ashare_data.local_query import DEFAULT_DATA_ROOT, LocalQuery, QueryError  # noqa: E402
from certify_daily_facts_phase1_v01 import certify as certify_daily_facts  # noqa: E402
from publish_daily_facts_phase1_v01 import publish as publish_daily_facts  # noqa: E402
from run_daily_facts_phase1_full_market import execute as acquire_daily_facts  # noqa: E402
from run_daily_facts_phase1_full_market import published_scope, run_paths  # noqa: E402
from run_r3_frozen_shsz_incremental_v01 import IncrementalError  # noqa: E402
from run_r3_frozen_shsz_incremental_v01 import calendar_state  # noqa: E402
from run_r3_frozen_shsz_incremental_v01 import run as run_r3_incremental  # noqa: E402

SCHEMA = "ASL_DAILY_MAINTENANCE_V01"
DATA_ROOT = DEFAULT_DATA_ROOT
R3_POINTER = Path("meta/asl/r3/published-daily-authority.json")
FACTS_POINTER = Path("meta/asl/daily_facts/published-daily-facts-authority.json")
LOCK_PATH = Path("meta/asl/daily-maintenance.lock")
CALENDAR = Path("curated/trading_calendar")
R3_DAILY_NAME = "part-merged.parquet"
FACTS_DAILY_NAME = "part-full-eligible-v01.parquet"
SMOKE_SYMBOLS = ("002580.SZ", "601888.SH", "000001.SZ", "600519.SH")
BAR_FIELDS = ("open", "high", "low", "close", "volume", "amount")
FACT_FIELDS = ("preclose", "pct_chg", "turnover_rate", "trade_status", "is_st")


class MaintenanceError(RuntimeError):
    """A maintenance gate failed; the caller must not continue the chain."""

    def __init__(self, code: str, detail: Any | None = None) -> None:
        super().__init__(code if detail is None else f"{code}:{detail}")
        self.code = code
        self.detail = detail


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require(condition: bool, code: str, detail: Any | None = None) -> None:
    if not condition:
        raise MaintenanceError(code, detail)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaintenanceError("AUTHORITY_UNREADABLE", str(path)) from exc


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


@contextlib.contextmanager
def writer_lock(root: Path) -> Iterator[None]:
    """One maintenance writer per host; a concurrent run is a no-op failure."""
    path = root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MaintenanceError("EOD_UPDATE_RUNNING", "another daily maintenance writer holds the lock") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def previous_trading_day(root: Path, day: date) -> date | None:
    """Previous trading session strictly before ``day`` from the persisted calendar."""
    import polars as pl

    frames = []
    for year in {day.year, day.year - 1}:
        path = root / CALENDAR / f"trade_date={year}" / "part-merged.parquet"
        if path.is_file():
            frames.append(pl.read_parquet(path).select(["trade_date", "is_trading"]))
    _require(bool(frames), "TRADING_CALENDAR_AUTHORITY_MISSING", day.isoformat())
    frame = pl.concat(frames).unique(subset=["trade_date"]).sort("trade_date")
    prior = frame.filter((pl.col("trade_date") < day) & (pl.col("is_trading") == True))  # noqa: E712
    _require(prior.height > 0, "TRADING_CALENDAR_PREDECESSOR_MISSING", day.isoformat())
    return prior.item(prior.height - 1, "trade_date")


def r3_state(root: Path, day: str) -> dict[str, Any]:
    """Read the R3 publication authority; never mutates it."""
    pointer = _read_json(root / R3_POINTER)
    _require(pointer.get("schema") == "R3_PUBLISHED_DAILY_AUTHORITY_V01", "R3_POINTER_INVALID")
    plan = _read_json(root / pointer["plan"])
    receipt = _read_json(root / pointer["receipt"])
    manifest = plan.get("EXPECTED_POST_INPUT_MANIFEST") or {}
    files = manifest.get("FILES")
    _require(isinstance(files, list) and bool(files), "R3_MANIFEST_INVALID")
    recomputed = canonical_sha(files)
    _require(recomputed == pointer.get("manifest_hash") == manifest.get("INPUT_MANIFEST_HASH"), "R3_MANIFEST_BINDING_INVALID")
    relative = f"curated/daily_bars/trade_date={day}/{R3_DAILY_NAME}"
    record = next((item for item in files if item.get("relative_path") == relative), None)
    dates = {Path(item["relative_path"]).parts[2].split("=", 1)[1] for item in files}
    return {
        "manifest_hash": pointer["manifest_hash"],
        "manifest_file_n": manifest.get("INPUT_FILE_N"),
        "published_as_of": max(dates),
        "receipt_state": receipt.get("STATE"),
        "day_present": record is not None,
        "day_record": record,
        "pointer": pointer,
    }


def facts_state(root: Path, day: str) -> dict[str, Any]:
    """Read the independent Daily Facts authority; absent means not ready."""
    pointer_path = root / FACTS_POINTER
    if not pointer_path.is_file():
        return {"published": False, "day_present": False, "scope": None, "manifest_hash": None}
    pointer = _read_json(pointer_path)
    _require(pointer.get("schema") == "ASL_PUBLISHED_DAILY_FACTS_AUTHORITY_V01", "FACTS_POINTER_INVALID")
    plan = _read_json(root / pointer["plan"])
    receipt = _read_json(root / pointer["receipt"])
    manifest = plan.get("manifest") or {}
    files = manifest.get("files")
    _require(isinstance(files, list) and bool(files), "FACTS_MANIFEST_INVALID")
    recomputed = canonical_sha(files)
    _require(recomputed == pointer.get("manifest_hash") == manifest.get("manifest_hash") == receipt.get("manifest_hash"),
             "FACTS_MANIFEST_BINDING_INVALID")
    relative = f"curated/daily_facts/trade_date={day}/{FACTS_DAILY_NAME}"
    record = next((item for item in files if item.get("relative_path") == relative), None)
    dates = {Path(item["relative_path"]).parts[2].split("=", 1)[1] for item in files}
    return {
        "published": True,
        "manifest_hash": pointer["manifest_hash"],
        "manifest_file_n": manifest.get("file_n"),
        "scope": receipt.get("scope"),
        "status": receipt.get("status"),
        "quality_pass": bool((receipt.get("quality") or {}).get("PASS") is True),
        "published_as_of": max(dates),
        "day_present": record is not None,
        "day_record": record,
        "files": files,
    }


def verify_r3_publication(root: Path, day: str) -> dict[str, Any]:
    """Independently verify the physical publication of one R3 trade date."""
    state = r3_state(root, day)
    _require(state["day_present"], "R3_PUBLICATION_MISSING", day)
    _require(state["receipt_state"] == "COMMITTED", "R3_RECEIPT_NOT_COMMITTED")
    record = state["day_record"]
    path = root / record["relative_path"]
    _require(path.is_file(), "R3_PARTITION_MISSING", record["relative_path"])
    _require(path.stat().st_size == record["file_size"], "R3_PARTITION_SIZE_DRIFT", day)
    _require(sha256_file(path) == record["sha256"], "R3_PARTITION_HASH_DRIFT", day)
    receipt = _read_json(root / state["pointer"]["receipt"])
    quality = receipt.get("QUALITY") or {}
    blockers = {
        "STRUCTURAL_PASS": quality.get("STRUCTURAL_PASS"),
        "COVERAGE_PASS": quality.get("COVERAGE_PASS"),
        "PROVENANCE_PASS": quality.get("PROVENANCE_PASS"),
        "UNRESOLVED_KEY_N": quality.get("UNRESOLVED_KEY_N"),
        "SOURCE_ERROR_N": quality.get("SOURCE_ERROR_N"),
    }
    _require(blockers["STRUCTURAL_PASS"] is True and blockers["COVERAGE_PASS"] is True
             and blockers["PROVENANCE_PASS"] is True and blockers["UNRESOLVED_KEY_N"] == 0
             and blockers["SOURCE_ERROR_N"] == 0, "R3_PUBLICATION_GATE_FAILED", blockers)
    return {"manifest_hash": state["manifest_hash"], "file_n": state["manifest_file_n"],
            "published_as_of": state["published_as_of"], "partition": record["relative_path"],
            "receipt": state["pointer"]["receipt"], "gate_blockers": blockers}


def verify_facts_publication(root: Path, day: str, *, expect_scope: str) -> dict[str, Any]:
    """Independently verify the physical publication of one Daily Facts date."""
    state = facts_state(root, day)
    _require(state["published"] and state["day_present"], "FACTS_PUBLICATION_MISSING", day)
    _require(state["quality_pass"], "FACTS_RECEIPT_NOT_PASS", day)
    _require(state["scope"] == expect_scope, "FACTS_SCOPE_MISMATCH", state["scope"])
    record = state["day_record"]
    path = root / record["relative_path"]
    _require(path.is_file(), "FACTS_PARTITION_MISSING", record["relative_path"])
    _require(path.stat().st_size == record["file_size"], "FACTS_PARTITION_SIZE_DRIFT", day)
    _require(sha256_file(path) == record["sha256"], "FACTS_PARTITION_HASH_DRIFT", day)
    return {"manifest_hash": state["manifest_hash"], "file_n": state["manifest_file_n"],
            "published_as_of": state["published_as_of"], "scope": state["scope"],
            "partition": record["relative_path"], "partition_sha256": record["sha256"]}


def facts_run_name(day: date) -> str:
    return f"daily_facts_phase1_{day.strftime('%Y%m%d')}_v01"


def unresolved_preclose_keys(root: Path, run_name: str, day: date, evidence_path: Path | None) -> list[dict[str, Any]]:
    """List the exact keys whose preclose reconciliation stayed unresolved.

    The derivation re-runs the frozen reconciliation primitives over persisted
    normalized evidence and the current published R3 bars.  It performs no
    provider request and writes nothing.
    """
    from certify_daily_facts_phase1_v01 import _reconciliation_bars, _rows  # noqa: E402
    from ashare_data.reference_price_evidence import load_reference_price_evidence, ReferencePriceEvidenceError  # noqa: E402

    staging, _raw, database = run_paths(root, run_name)
    if not database.is_file():
        raise MaintenanceError("RUN_LEDGER_MISSING", run_name)
    with sqlite3.connect(database) as con:
        paths = [root / row[0] for row in con.execute("select normalized_path from units order by symbol") if row[0]]
    rows = _rows(paths)
    evidence: dict[Any, Any] = {}
    candidate = evidence_path if evidence_path is not None else staging / "reference_price_evidence.json"
    if candidate.is_file():
        try:
            evidence = load_reference_price_evidence(root, candidate)
        except ReferencePriceEvidenceError:
            evidence = {}
    reconcile(rows, _reconciliation_bars(root, day, day), reference_price_evidence=evidence)
    return [{"symbol": row["symbol"], "trade_date": row["trade_date"],
             "preclose_reconciliation": row.get("preclose_reconciliation"),
             "quality_status": row.get("quality_status"),
             "provider_preclose": row.get("preclose"), "pct_chg": row.get("pct_chg")}
            for row in rows if row.get("preclose_reconciliation") in {"MISMATCH", "UNRESOLVED"}]


@contextlib.contextmanager
def progress_monitor(database: Path, destination: Path | None, *, interval: float) -> Iterator[None]:
    """Emit ledger progress while a long acquisition is running."""
    stop = threading.Event()
    started = time.time()

    def snapshot() -> dict[str, Any]:
        counts: dict[str, int] = {}
        if database.is_file():
            try:
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5) as con:
                    counts = {str(state): int(n) for state, n in con.execute("select state,count(*) from units group by state")}
            except sqlite3.Error:
                counts = {}
        return {"at": _utc(), "elapsed_s": round(time.time() - started, 1), "unit_n": sum(counts.values()), "states": counts}

    def loop() -> None:
        while not stop.wait(interval):
            value = snapshot()
            if destination is not None:
                try:
                    _atomic_json(destination, {"schema": SCHEMA, **value})
                except OSError:
                    pass
            print(json.dumps({"progress": value}, sort_keys=True), file=sys.stderr, flush=True)

    thread = threading.Thread(target=loop, name="daily-maintenance-progress", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def _mcp_call(url: str, name: str, arguments: dict[str, Any], *, timeout: float = 120.0) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}}).encode()
    request = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    if body.lstrip().startswith("event:") or "\ndata:" in body:
        data = next((line[5:].strip() for line in body.splitlines() if line.startswith("data:")), "")
        body = data
    document = json.loads(body)
    if "error" in document:
        raise MaintenanceError("MCP_ERROR", document["error"])
    content = document.get("result", {}).get("content") or []
    text = next((item.get("text") for item in content if item.get("type") == "text"), None)
    if text is None:
        raise MaintenanceError("MCP_RESULT_MISSING", name)
    return json.loads(text)


def query_smoke(root: Path, day: str, preserved_day: str | None, mcp_url: str | None) -> dict[str, Any]:
    """Read the new day plus any preserved authority through LocalQuery and MCP."""
    report: dict[str, Any] = {"symbols": list(SMOKE_SYMBOLS), "local_query": {"symbols": {}}, "mcp": None}
    with LocalQuery(root) as query:
        status = query.status()
        report["local_query"]["status"] = {
            "DAILY_PUBLISHED_AS_OF": status.get("DAILY_PUBLISHED_AS_OF"),
            "DAILY_MANIFEST_HASH": status.get("DAILY_MANIFEST_HASH"),
            "DAILY_FACTS_MANIFEST_HASH": status.get("DAILY_FACTS_MANIFEST_HASH"),
            "DAILY_FACTS_PHASE1_SCOPE": status.get("DAILY_FACTS_PHASE1_SCOPE"),
            "DAILY_FACTS_PHASE1_STATUS": status.get("DAILY_FACTS_PHASE1_STATUS"),
            "FACTS_READY": status.get("FACTS_READY"),
            "PRECLOSE_COMPLETE": status.get("PRECLOSE_COMPLETE"),
        }
        for symbol in SMOKE_SYMBOLS:
            entry: dict[str, Any] = {}
            latest = query.latest(symbol, limit=3, as_of=day, fact_fields=list(FACT_FIELDS))
            row = next((item for item in latest["rows"] if str(item.get("trade_date")) == day), None)
            entry["bar_fields"] = {field: (row or {}).get(field) for field in BAR_FIELDS}
            entry["fact_fields"] = {field: (row or {}).get(field) for field in FACT_FIELDS}
            entry["row_present"] = row is not None
            try:
                facts = query.facts(symbol, day)
                entry["facts_tool"] = {field: facts["facts"].get(field) for field in FACT_FIELDS}
                entry["facts_scope"] = facts.get("DAILY_FACTS_SCOPE")
            except QueryError as exc:
                entry["facts_tool_error"] = exc.code
            if preserved_day:
                try:
                    previous = query.facts(symbol, preserved_day)
                    entry["preserved_facts"] = {field: previous["facts"].get(field) for field in FACT_FIELDS}
                except QueryError as exc:
                    entry["preserved_facts_error"] = exc.code
            report["local_query"]["symbols"][symbol] = entry
    if not mcp_url:
        report["mcp"] = {"status": "SKIPPED"}
        return report
    mcp: dict[str, Any] = {"endpoint": mcp_url, "symbols": {}}
    try:
        status = _mcp_call(mcp_url, "status", {})
        mcp["status"] = {key: status.get(key) for key in ("LATEST_PUBLISHED_TRADE_DATE", "DAILY_PUBLISHED_AS_OF",
                                                          "DAILY_MANIFEST_HASH", "DAILY_FACTS_MANIFEST_HASH",
                                                          "DAILY_FACTS_PHASE1_SCOPE")}
        mcp["status_reports_new_day"] = status.get("LATEST_PUBLISHED_TRADE_DATE") == day
        for symbol in SMOKE_SYMBOLS:
            entry = {}
            latest = _mcp_call(mcp_url, "latest", {"symbol": symbol, "limit": 3, "fields": list(FACT_FIELDS)})
            row = next((item for item in latest.get("rows", []) if str(item.get("trade_date")) == day), None)
            entry["bar_fields"] = {field: (row or {}).get(field) for field in BAR_FIELDS}
            entry["fact_fields"] = {field: (row or {}).get(field) for field in FACT_FIELDS}
            entry["facts"] = _mcp_call(mcp_url, "facts", {"symbol": symbol, "trade_date": day}).get("facts")
            if preserved_day:
                entry["preserved_facts"] = _mcp_call(mcp_url, "facts", {"symbol": symbol,
                                                     "trade_date": preserved_day}).get("facts")
            mcp["symbols"][symbol] = entry
        mcp["ok"] = True
    except (MaintenanceError, urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        mcp["ok"] = False
        mcp["error"] = f"{type(exc).__name__}:{exc}"
    report["mcp"] = mcp
    return report


def inspect_authority(root: Path, day: str) -> dict[str, Any]:
    return {"trade_date": day, "r3": r3_state(root, day), "facts": facts_state(root, day)}


def _blocked(status: str, day: str, steps: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "status": status, "trade_date": day, "steps": steps, "at": _utc(), **extra}


def maintain(day: str, *, execute: bool, root: Path = DATA_ROOT, mcp_url: str | None = "http://127.0.0.1:8766/mcp",
             reference_price_parameters: Path | None = None, progress_path: Path | None = None,
             progress_interval: float = 300.0) -> dict[str, Any]:
    """Run or plan one bounded daily maintenance date."""
    target = date.fromisoformat(day)
    _require(target <= date.today(), "FUTURE_TRADE_DATE_BLOCKED", day)
    steps: list[dict[str, Any]] = []
    calendar = calendar_state(day)
    if calendar == "NON_TRADING_DAY":
        return _blocked("NON_TRADING_DAY", day, steps, calendar=calendar, provider_network_request_n=0)

    state = inspect_authority(root, day)
    plan = {
        "r3": {"published_as_of": state["r3"]["published_as_of"], "day_present": state["r3"]["day_present"],
               "manifest_hash": state["r3"]["manifest_hash"]},
        "facts": {"published_as_of": state["facts"].get("published_as_of"),
                  "scope": state["facts"].get("scope"), "day_present": state["facts"]["day_present"],
                  "manifest_hash": state["facts"].get("manifest_hash")},
        "facts_run_name": facts_run_name(target),
    }
    if not execute:
        planned = {"status": "ALREADY_PUBLISHED"} if (state["r3"]["day_present"] and state["facts"]["day_present"]) \
            else {"status": "READY"}
        dry = run_r3_incremental(day, execute=False) if not state["r3"]["day_present"] else {"status": "ALREADY_PUBLISHED"}
        return _blocked(planned["status"], day, steps, plan=plan, r3_dry_run=dry, provider_network_request_n=0,
                        publication_attempted=False)

    with writer_lock(root):
        state = inspect_authority(root, day)
        preserved_day = state["r3"]["published_as_of"] if state["r3"]["published_as_of"] < day else None
        already_complete = bool(state["r3"]["day_present"] and state["facts"]["day_present"])
        if not already_complete:
            predecessor = previous_trading_day(root, target)
            _require(predecessor is not None and predecessor.isoformat() == state["r3"]["published_as_of"],
                     "AUTHORITY_NOT_CONTIGUOUS", {"expected_predecessor": predecessor.isoformat() if predecessor else None,
                                                  "published_as_of": state["r3"]["published_as_of"]})

        # ---- R3 update ----------------------------------------------------
        r3_result: dict[str, Any] = {"status": "SKIPPED_ALREADY_PUBLISHED"}
        if state["r3"]["day_present"]:
            steps.append({"step": "r3_update", "status": "SKIPPED_ALREADY_PUBLISHED", "manifest_hash": state["r3"]["manifest_hash"]})
        else:
            try:
                r3_result = run_r3_incremental(day, execute=True)
            except IncrementalError as exc:
                detail = str(exc)
                status = "BLOCKED_PROVIDER" if detail.startswith("SOURCE_ERROR") else "BLOCKED_R3"
                steps.append({"step": "r3_update", "status": status, "error": detail})
                return _blocked(status, day, steps, r3_error=detail)
            except Exception as exc:  # defensive: never leave a partial promotion reported as success
                steps.append({"step": "r3_update", "status": "BLOCKED_R3", "error": f"{type(exc).__name__}:{exc}"})
                return _blocked("BLOCKED_R3", day, steps, r3_error=f"{type(exc).__name__}:{exc}")
            status = r3_result.get("status", "UNKNOWN")
            steps.append({"step": "r3_update", "status": status,
                          "eligible_n": r3_result.get("eligible_n"), "observed_n": r3_result.get("observed_n"),
                          "suspended_n": len(r3_result.get("classifications", []) or []),
                          "primary_missing_n": r3_result.get("primary_missing_n"),
                          "network_request_n": r3_result.get("network_request_n"),
                          "secondary_request_n": r3_result.get("secondary_request_n"),
                          "run_id": r3_result.get("run_id"), "quality": r3_result.get("quality"),
                          "manifest_hash": r3_result.get("manifest_hash")})
            if status not in {"PUBLISHED", "ALREADY_PUBLISHED", "ORPHAN_MATCHES_EXPECTED_CANDIDATE"}:
                return _blocked("BLOCKED_R3", day, steps, r3_result=r3_result)

        try:
            r3_evidence = verify_r3_publication(root, day)
        except MaintenanceError as exc:
            steps.append({"step": "r3_verify", "status": "BLOCKED", "error": exc.code})
            return _blocked("BLOCKED_R3", day, steps, r3_error=exc.code)
        steps.append({"step": "r3_verify", "status": "PASS", "manifest_hash": r3_evidence["manifest_hash"],
                      "file_n": r3_evidence["file_n"], "partition": r3_evidence["partition"]})

        # ---- Daily Facts --------------------------------------------------
        run_name = facts_run_name(target)
        scope = f"{day}_FULL_ELIGIBLE"
        state = inspect_authority(root, day)
        facts_result: dict[str, Any] = {"status": "SKIPPED_ALREADY_PUBLISHED"}
        if state["facts"]["day_present"]:
            steps.append({"step": "facts_acquisition", "status": "SKIPPED_ALREADY_PUBLISHED"})
        else:
            eligible_n = None
            try:
                symbols, _required, _as_of, _hash, _relation = published_scope(root, start=target, end=target)
                eligible_n = len(symbols)
            except DailyFactsError as exc:
                steps.append({"step": "facts_scope", "status": "BLOCKED", "error": exc.code})
                return _blocked("BLOCKED_AUTHORITY", day, steps, facts_error=exc.code)
            try:
                with progress_monitor(run_paths(root, run_name)[2], progress_path, interval=progress_interval):
                    facts_result = acquire_daily_facts(root, run_name=run_name, start=target, end=target)
            except DailyFactsError as exc:
                status = "BLOCKED_PROVIDER" if exc.code in {"SOURCE_ERROR", "PROVIDER_VERSION_MISMATCH",
                                                            "INVALID_CNEQUITY_CONFIG", "PROVIDER_IDENTITY_MISMATCH"} else "BLOCKED_CERTIFICATION"
                steps.append({"step": "facts_acquisition", "status": status, "error": exc.code})
                return _blocked(status, day, steps, facts_error=exc.code)
            except Exception as exc:
                steps.append({"step": "facts_acquisition", "status": "BLOCKED_CERTIFICATION", "error": f"{type(exc).__name__}:{exc}"})
                return _blocked("BLOCKED_CERTIFICATION", day, steps, facts_error=f"{type(exc).__name__}:{exc}")
            steps.append({"step": "facts_acquisition", "status": "ACQUIRED", "run": run_name,
                          "eligible_n": eligible_n, "network_fetched_symbol_n": facts_result.get("network_fetched_symbol_n"),
                          "states": facts_result.get("states")})

        certification: dict[str, Any] | None = None
        try:
            certification = certify_daily_facts(root, run_name=run_name, start=target, end=target, execute=True)
        except DailyFactsError as exc:
            steps.append({"step": "facts_certification", "status": "BLOCKED", "error": exc.code})
            return _blocked("BLOCKED_CERTIFICATION", day, steps, facts_error=exc.code)
        if certification.get("PASS") is not True:
            quality = certification.get("quality") or {}
            steps.append({"step": "facts_certification", "status": "BLOCKED", "quality": quality,
                          "error": certification.get("error")})
            if quality.get("PRECLOSE_UNRESOLVED_N", 0) > 0:
                try:
                    keys = unresolved_preclose_keys(root, run_name, target, None)
                except (MaintenanceError, DailyFactsError) as exc:
                    return _blocked("BLOCKED_CERTIFICATION", day, steps, facts_error=getattr(exc, "code", str(exc)), certification=certification)
                symbols = sorted({row["symbol"] for row in keys})
                if reference_price_parameters is None:
                    return _blocked("BLOCKED_REFERENCE_PRICE_EVIDENCE", day, steps, unresolved_keys=keys,
                                    unresolved_symbol_n=len(symbols), certification=certification)
                import fetch_daily_facts_reference_price_evidence_v01 as evidence_fetch
                import build_daily_facts_reference_price_evidence_v01 as evidence_build
                try:
                    fetch_result = evidence_fetch.fetch(root, execute=True, run_name=run_name, symbols=symbols,
                                                        search_start=(target - timedelta(days=30)).isoformat(), search_end=day)
                    build_result = evidence_build.build(root, parameters_path=reference_price_parameters,
                                                        execute=True, run_name=run_name)
                except Exception as exc:
                    return _blocked("BLOCKED_REFERENCE_PRICE_EVIDENCE", day, steps,
                                    facts_error=f"{type(exc).__name__}:{exc}", unresolved_keys=keys)
                steps.append({"step": "reference_price_evidence", "status": "BUILT",
                              "symbol_n": len(symbols), "fetch_receipt": fetch_result.get("receipt_hash"),
                              "evidence_records": len(build_result.get("records", []))})
                try:
                    certification = certify_daily_facts(root, run_name=run_name, start=target, end=target, execute=True)
                except DailyFactsError as exc:
                    steps.append({"step": "facts_certification", "status": "BLOCKED", "error": exc.code})
                    return _blocked("BLOCKED_CERTIFICATION", day, steps, facts_error=exc.code)
            if certification.get("PASS") is not True:
                steps.append({"step": "facts_certification", "status": "BLOCKED_RETAINED",
                              "quality": certification.get("quality")})
                return _blocked("BLOCKED_CERTIFICATION", day, steps, certification=certification)
        steps.append({"step": "facts_certification", "status": "PASS",
                      "eligible_n": certification.get("requested_symbol_n"),
                      "normalized_row_n": certification.get("NORMALIZED_ROW_N"),
                      "normal_continuity_n": certification.get("NORMAL_CONTINUITY_N"),
                      "reference_price_exception_n": certification.get("REFERENCE_PRICE_EXCEPTION_N"),
                      "suspended_n": certification.get("SUSPENDED_N"),
                      "raw_terminal_n": certification.get("ledger_terminal_n"),
                      "quality": certification.get("quality")})

        if state["facts"]["day_present"]:
            steps.append({"step": "facts_publication", "status": "SKIPPED_ALREADY_PUBLISHED"})
        else:
            try:
                publication = publish_daily_facts(root, run_name=run_name, day=target, execute=True)
            except DailyFactsError as exc:
                steps.append({"step": "facts_publication", "status": "BLOCKED", "error": exc.code})
                return _blocked("BLOCKED_CERTIFICATION", day, steps, facts_error=exc.code)
            except Exception as exc:
                steps.append({"step": "facts_publication", "status": "BLOCKED", "error": f"{type(exc).__name__}:{exc}"})
                return _blocked("BLOCKED_CERTIFICATION", day, steps, facts_error=f"{type(exc).__name__}:{exc}")
            steps.append({"step": "facts_publication", "status": publication.get("publication"),
                          "manifest_hash": publication.get("manifest_hash"), "scope": publication.get("scope")})

        try:
            facts_evidence = verify_facts_publication(root, day, expect_scope=scope)
        except MaintenanceError as exc:
            steps.append({"step": "facts_verify", "status": "BLOCKED", "error": exc.code})
            return _blocked("BLOCKED_AUTHORITY", day, steps, facts_error=exc.code)
        steps.append({"step": "facts_verify", "status": "PASS", "manifest_hash": facts_evidence["manifest_hash"],
                      "file_n": facts_evidence["file_n"], "partition": facts_evidence["partition"]})

        try:
            smoke = query_smoke(root, day, preserved_day, mcp_url)
        except (QueryError, MaintenanceError) as exc:
            steps.append({"step": "query_smoke", "status": "BLOCKED", "error": getattr(exc, "code", str(exc))})
            return _blocked("BLOCKED_SMOKE", day, steps, r3=r3_evidence, facts=facts_evidence)
        mcp_ok = bool(smoke["mcp"] and smoke["mcp"].get("ok"))
        steps.append({"step": "query_smoke", "status": "PASS" if mcp_ok else "LOCAL_OK_MCP_DEGRADED"})
        return {"schema": SCHEMA, "status": "PUBLISHED", "trade_date": day, "steps": steps, "at": _utc(),
                "already_published_before_run": already_complete,
                "r3": r3_evidence, "facts": facts_evidence, "smoke": smoke,
                "r3_quality": r3_result.get("quality"),
                "facts_quality": (certification or {}).get("quality"),
                "facts_certification_summary": {key: (certification or {}).get(key) for key in (
                    "requested_symbol_n", "expected_key_n", "NORMALIZED_ROW_N", "NORMAL_CONTINUITY_N",
                    "REFERENCE_PRICE_EXCEPTION_N", "SUSPENDED_N", "ledger_terminal_n", "provider_network_request_n")},
                "preserved_day": preserved_day}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trade-date", required=True, help="target trading date (YYYY-MM-DD)")
    parser.add_argument("--execute", action="store_true", help="perform the maintenance chain; otherwise plan only")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8766/mcp", help="loopback MCP endpoint (empty disables the MCP smoke)")
    parser.add_argument("--reference-price-parameters", type=Path, default=None,
                        help="reviewed official parameters for exact unresolved preclose keys")
    parser.add_argument("--progress-interval", type=float, default=300.0)
    parser.add_argument("--progress-path", type=Path, default=None)
    args = parser.parse_args(argv)
    progress_path = args.progress_path or (DATA_ROOT / "logs" / f"daily-maintenance-{args.trade_date}.progress.json")
    try:
        result = maintain(args.trade_date, execute=args.execute, mcp_url=args.mcp_url or None,
                          reference_price_parameters=args.reference_price_parameters,
                          progress_path=progress_path, progress_interval=args.progress_interval)
    except MaintenanceError as exc:
        print(json.dumps({"schema": SCHEMA, "status": exc.code, "trade_date": args.trade_date,
                          "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"PUBLISHED", "ALREADY_PUBLISHED", "READY", "NON_TRADING_DAY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
