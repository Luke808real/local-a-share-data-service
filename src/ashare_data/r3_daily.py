"""R3 DAILY FOUNDATION control plane.

This module implements the R3-DAILY-FOUNDATION-V07.2 plan (Sol AUDIT_PASS at
``PLAN_SHA``). It is the only authorized R3 data-execution surface: a thin
service-owned orchestrator on top of the pinned CNEquity raw adapters. It
deliberately does not call the generic ``step_daily_bars``/``cne init``/``cne
run``/retry paths, writes no derived dataset, and never cleans staging.

Fail-closed invariants enforced here:
* exact reviewed plan SHA and Git base;
* pinned runtime provenance (CNEquity Git SHA, lock, config, Python 3.12);
* zero access to any legacy root;
* effective list/delist span bounding for every staged daily row;
* positive-volume required as coverage evidence (zero-volume rows are not);
* PENDING_R4_STATUS_EXPLANATION is never treated as EXPLAINED_MISSING;
* provider tri-state (EXISTS/NOT_EXISTS/SOURCE_ERROR) is separated from the
  coverage classifier enums; the EastMoney wrapper never emits
  UNEXPLAINED_MISSING and never maps a known BJ symbol to NOT_EXISTS;
* `HISTORICAL_DELISTED_BJ = UNKNOWN_CARRIED` keeps DAILY_READY=FALSE until
  BJ_HISTORICAL_AUTHORITY=PROVEN and BJ_HISTORICAL_UNRESOLVED_N=0;
* `LATEST_GOOD_AS_OF` is never written.

See docs/plans/R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN.md and
docs/contracts/R3_DAILY_FOUNDATION_CONTRACT.md for the authoritative wording.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import uuid
import fcntl
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import polars as pl

from cnequity.config import Config, load_config
from cnequity.domain.schemas import data_version_for, with_provenance
from cnequity.orchestrator.engine import JobEngine
from cnequity.orchestrator.manifest import Manifest
from cnequity.orchestrator.worker_pool import _symbol_batch_id, fetch_daily_bars_parallel
from cnequity.steps.common import list_trading_dates, load_curated_instruments
from cnequity.steps.delisted import (
    delisted_coverage_report,
    known_delisted_instruments,
    load_delisted_catalog,
)
from cnequity.storage import StagingWriter

logger = logging.getLogger(__name__)


# Callables actually used by R3 (no Sina):
# (label, module, dotted_attr, expected_module, expected_signature)
APPROVED_CALLABLES: tuple[tuple[str, str, str, str, str], ...] = (
    ("JobEngine.run_job", "cnequity.orchestrator.engine", "JobEngine.run_job",
     "cnequity.orchestrator.engine",
     "(self, job_name: 'str', trade_date: 'date | None' = None, *, steps: 'list[str] | None' = None, waves: 'list[WaveConfig] | None' = None, backfill: 'bool' = False, run_id: 'str | None' = None, retry_failed_only: 'bool' = False, finalize_run: 'bool' = True) -> 'dict[str, Any]'"),
    ("JobEngine.run_step", "cnequity.orchestrator.engine", "JobEngine.run_step",
     "cnequity.orchestrator.engine",
     "(self, name: 'str', trade_date: 'date', run_id: 'str', context: 'dict[str, Any] | None' = None) -> 'dict[str, Any]'"),
    ("fetch_daily_bars_parallel", "cnequity.orchestrator.worker_pool", "fetch_daily_bars_parallel",
     "cnequity.orchestrator.worker_pool",
     "(config: 'Config', symbols: 'list[str]', start: 'date', end: 'date', run_id: 'str', dataset: 'str' = 'daily_bars', *, batch_specs: 'list[BatchSpec] | None' = None) -> 'dict[str, Any]'"),
    ("fetch_instrument_basics", "cnequity.adapters.baostock.instruments", "fetch_instrument_basics",
     "cnequity.adapters.baostock.instruments",
     "(*, bs=None, sleep=<built-in function sleep>) -> 'pl.DataFrame'"),
    ("roster_on", "cnequity.adapters.baostock.delisted_bars", "roster_on",
     "cnequity.adapters.baostock.delisted_bars",
     "(day: 'date', *, bs=None, login: 'bool' = True) -> 'set[str]'"),
    ("import_baostock", "cnequity.adapters.baostock._session", "import_baostock",
     "cnequity.adapters.baostock._session",
     "()"),
    ("_login", "cnequity.adapters.baostock._session", "_login",
     "cnequity.adapters.baostock._session",
     "(bs, *, sleep=<built-in function sleep>) -> 'None'"),
    ("_relogin", "cnequity.adapters.baostock._session", "_relogin",
     "cnequity.adapters.baostock._session",
     "(bs, *, sleep=<built-in function sleep>) -> 'None'"),
    ("_ensure_socket_timeout", "cnequity.adapters.baostock._session", "_ensure_socket_timeout",
     "cnequity.adapters.baostock._session",
     "(timeout: 'float' = 30.0) -> 'None'"),
    ("_force_close_baostock_socket", "cnequity.adapters.baostock._session", "_force_close_baostock_socket",
     "cnequity.adapters.baostock._session",
     "() -> 'None'"),
    ("fetch_delisted_bars", "cnequity.adapters.baostock.delisted_bars", "fetch_delisted_bars",
     "cnequity.adapters.baostock.delisted_bars",
     "(symbols: 'list[str]', start: 'date', end: 'date', *, config=None, bs=None) -> 'tuple[list[dict], list[str]]'"),
    ("fetch_clist_pages", "cnequity.adapters.eastmoney.clist", "fetch_clist_pages",
     "cnequity.adapters.eastmoney.clist",
     "(client: 'EastMoneyClient', *, fields: 'str', fs: 'str' = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048', page_size: 'int' = 100) -> 'list[dict]'"),
    ("clist_rows_to_symbols", "cnequity.adapters.eastmoney.clist", "clist_rows_to_symbols",
     "cnequity.adapters.eastmoney.clist",
     "(rows: 'list[dict]') -> 'list[tuple[str, dict]]'"),
    ("fetch_daily_bars", "cnequity.adapters.eastmoney.bars", "fetch_daily_bars",
     "cnequity.adapters.eastmoney.bars",
     "(symbols: 'list[str]', start: 'date', end: 'date', *, client: 'EastMoneyClient | None' = None, config=None) -> 'pl.DataFrame'"),
    ("delisted_coverage_report", "cnequity.steps.delisted", "delisted_coverage_report",
     "cnequity.steps.delisted",
     "(config: 'Config', start: 'date', end: 'date | None' = None, *, sample: 'int' = 15) -> 'dict'"),
)


def _runtime_pin_proof() -> dict[str, Any]:
    import importlib.metadata as md
    version = md.version("cnequity")
    dist = md.distribution("cnequity")
    direct_path = Path(dist._path) / "direct_url.json"  # type: ignore[attr-defined]
    direct = json.loads(direct_path.read_text(encoding="utf-8")) if direct_path.exists() else {}
    vcs = direct.get("vcs_info") or {}
    ok = (
        version == PINNED_CNEQUITY_VERSION
        and vcs.get("commit_id") == PINNED_CNEQUITY_SHA
    )
    return {
        "verified": bool(ok),
        "version": version,
        "commit": vcs.get("commit_id"),
    }


def approved_callable_contract() -> dict[str, Any]:
    import cnequity

    pkg_dir = Path(cnequity.__file__).resolve().parent
    entries: list[dict[str, Any]] = []
    for entry in APPROVED_CALLABLES:
        _label, module_name, attr_path, expected_module, expected_signature = entry
        module = importlib.import_module(module_name)
        if module.__name__ != expected_module:
            raise R3Error(
                "RUNTIME_CONTRACT_DRIFT",
                f"callable {_label} module mismatch: {module.__name__} != {expected_module}",
            )
        obj = module
        for part in attr_path.split("."):
            obj = getattr(obj, part)
        signature = str(inspect.signature(obj))
        if signature != expected_signature:
            raise R3Error(
                "RUNTIME_CONTRACT_DRIFT",
                f"callable {_label} signature mismatch: {signature} != {expected_signature}",
            )
        source_file = inspect.getsourcefile(obj) or ""
        in_dist = Path(source_file).resolve().is_relative_to(pkg_dir)
        entries.append(
            {
                "name": _label,
                "module": module.__name__,
                "signature": signature,
                "source": Path(source_file).name if source_file else None,
                "in_distribution": bool(in_dist),
            }
        )
        if not in_dist:
            raise R3Error(
                "RUNTIME_CONTRACT_DRIFT",
                f"callable {_label} not inside pinned cnequity distribution: {source_file}",
            )
    pin = _runtime_pin_proof()
    contract = {
        "verified": bool(pin["verified"]),
        "count": len(entries),
        "hash": sha256_bytes(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
        ),
        "runtime_pin": pin,
        "identities": entries,
    }
    if not pin["verified"]:
        raise R3Error("RUNTIME_CONTRACT_DRIFT", f"runtime pin mismatch: {pin}")
    return contract


# --- Frozen R3 constants ----------------------------------------------------

R3_HISTORY_START = date(2016, 1, 1)
R3_DAILY_AS_OF = date(2026, 8, 17)
PLAN_SHA = "3ab1f184edeea1d0e408c45df4a706248b6558d0"
BASE_HEAD = "0254122a99f0a365d2be12f29a2a59b951497fd3"

BJ_HISTORICAL_AUTHORITY_VERDICT = "UNPROVABLE_BOUNDED_RESEARCH"
HISTORICAL_DELISTED_BJ_LABEL = "UNKNOWN_CARRIED"

R2_ZERO_DATA_TREE_SHA = "ddcf9dc509b6bfb0cea8bd27511360ba6d1b4151b4a745f3e0fcb230ecd43dd5"
PINNED_CNEQUITY_SHA = "a18ee0484dfb0801650175471724def3228b8a17"
PINNED_CNEQUITY_VERSION = "0.7.2"
LOCK_SHA = "5f233fa9434624391c06e56a4596edfd52c1ec596d66688753b78f424dd571ac"
CONFIG_SHA = "fac5abd136cb2ae00c07d7ca408eb1d47eed69c26c3547a0547ef9d214063fb5"

LEGACY_ROOTS = (
    Path("/Users/luke808/AI/asl-shared"),
    Path("/Users/luke808/AI/asl-r8-5m-lake"),
    Path("/Users/luke808/AI/V flash/data"),
)

FORBIDDEN_UPSTREAM_SURFACE = (
    "cne init ",
    "cne run ",
    "cne retry ",
    "cne audit ",
    "cne query ",
    "cne status ",
    "cne mcp ",
    "cne demo ",
    "cne verify --repair",
    "cne catchup ",
    "cne clean ",
    "cne backfill minute_bars",
    "cne backfill trade_ticks",
    "cne backfill index_bars",
    "cne backfill corporate_actions",
    "cne backfill trading_status",
    "cne backfill turnover",
    "cne derive ",
    "cne compact ",
    "cne sources ",
    "cne servers test",
)

DERIVED_DENY = (
    "derived/delisting_events",
    "derived/adj_factors",
    "derived/industry_index",
    "derived/sentiment_scores",
)

# V07.3 resumable SH/SZ roster closure: single shared Baostock session,
# bounded per-date retry/relogin, atomic progress checkpoint, and exact
# same-stage resume. These numbers mirror the pinned CNEquity session policy
# (3 attempts, 1s/3s/8s backoff, relogin every 300 dates).
ROSTER_CHECKPOINT_FILENAME = "r3-roster-closure-progress-v073.json"
ROSTER_CHECKPOINT_SCHEMA_VERSION = 1
ROSTER_SWEEP_MAX_ATTEMPTS = 3
ROSTER_SWEEP_BACKOFF = (1.0, 3.0, 8.0)  # seconds; injectable/skipped in tests
ROSTER_PROACTIVE_RELOGIN_EVERY = 300
ROSTER_PROGRESS_LOG_EVERY = 25


class R3Error(RuntimeError):
    """Fail-closed R3 error carrying a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


# --- Git / runtime helpers --------------------------------------------------


def git_sha(path: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def has_ancestor(path: Path, commit: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(path), "merge-base", "--is-ancestor", commit, "HEAD"],
        capture_output=True,
    )
    return out.returncode == 0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, payload: dict) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, default=str, sort_keys=True) + "\n")


def runtime_provenance(cfg: Config, config_path: Path) -> dict[str, Any]:
    """Prove the pinned CNEquity runtime identity, fail-closed."""
    import importlib.metadata as md
    import platform

    version = md.version("cnequity")
    dist = md.distribution("cnequity")
    direct = None
    direct_path = Path(dist._path) / "direct_url.json"  # type: ignore[attr-defined]
    if direct_path.exists():
        direct = json.loads(direct_path.read_text(encoding="utf-8"))
    vcs_info = (direct or {}).get("vcs_info") or {}
    verdict = {
        "python": platform.python_version(),
        "cnequity_version": version,
        "direct_url_commit": vcs_info.get("commit_id"),
        "direct_url_vcs": vcs_info.get("vcs"),
        "config_sha256": sha256_file(config_path),
        "lock_sha256": None,
    }
    lock_path = Path(__file__).resolve().parents[2] / "uv.lock"
    if lock_path.exists():
        verdict["lock_sha256"] = sha256_file(lock_path)
    if (
        version != PINNED_CNEQUITY_VERSION
        or verdict["direct_url_commit"] != PINNED_CNEQUITY_SHA
        or verdict["direct_url_vcs"] != "git"
        or not verdict["python"].startswith("3.12")
    ):
        raise R3Error("RUNTIME_CONTRACT_DRIFT", f"runtime provenance mismatch: {verdict}")
    if verdict["config_sha256"] != CONFIG_SHA or verdict["lock_sha256"] != LOCK_SHA:
        raise R3Error("RUNTIME_CONTRACT_DRIFT", f"config/lock hash mismatch: {verdict}")
    return verdict


# --- Effective spans --------------------------------------------------------


def effective_span(
    list_date: date | None,
    delist_date: date | None,
    history_start: date = R3_HISTORY_START,
    daily_as_of: date = R3_DAILY_AS_OF,
) -> tuple[date, date] | None:
    """Effective in-window span for one symbol, or None if it never overlaps."""
    start = max(list_date or history_start, history_start)
    end = min(delist_date, daily_as_of) if delist_date else daily_as_of
    if delist_date is not None and delist_date < history_start:
        return None
    if list_date is not None and list_date > daily_as_of:
        return None
    return (start, end)


def group_by_span(spans: dict[str, tuple[date, date]]) -> list[tuple[tuple[date, date], list[str]]]:
    """Partition symbols by identical effective span (V03 P1 fix)."""
    buckets: dict[tuple[date, date], list[str]] = {}
    for symbol, span in spans.items():
        buckets.setdefault(span, []).append(symbol)
    return [(span, sorted(symbols)) for span, symbols in sorted(buckets.items())]


# --- V07.2 provider tri-state / closure / exit-gate helpers ----------------

EM_KNOWN_SYMBOL_COLS = ("symbol", "trade_date", "open", "high", "low", "close", "volume")


def parse_clist_date(value: Any) -> date | None:
    """Parse EastMoney clist list-date (f26) YYYYMMDD/date-ISO, mirroring pinned."""
    if value in (None, "", "-"):
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def em_daily_tristate(
    symbol: str,
    start: date,
    end: date,
    *,
    fetcher: Any | None = None,
    config: Any = None,
) -> dict[str, Any]:
    """Thin service-owned EastMoney tri-state wrapper (V07.2).

    Returns EXACTLY one of EXISTS / NOT_EXISTS / SOURCE_ERROR. For a symbol
    already confirmed in the security master it NEVER returns NOT_EXISTS and
    NEVER emits UNEXPLAINED_MISSING.
    """
    if fetcher is None:
        from cnequity.adapters.eastmoney.bars import fetch_daily_bars

        fetcher = fetch_daily_bars
    try:
        frame = fetcher([symbol], start, end, config=config)
    except Exception as exc:
        return {
            "state": "SOURCE_ERROR",
            "reason": f"transport_or_parse: {type(exc).__name__}",
            "symbol": symbol,
            "frame": None,
        }
    if frame is None:
        return {
            "state": "SOURCE_ERROR",
            "reason": "INVALID_KNOWN_SYMBOL_RESPONSE",
            "symbol": symbol,
            "frame": None,
        }
    if frame.is_empty():
        return {
            "state": "SOURCE_ERROR",
            "reason": "EMPTY_KNOWN_SYMBOL",
            "symbol": symbol,
            "frame": frame,
        }
    missing = [c for c in EM_KNOWN_SYMBOL_COLS if c not in frame.columns]
    if missing:
        return {
            "state": "SOURCE_ERROR",
            "reason": "INVALID_KNOWN_SYMBOL_RESPONSE",
            "symbol": symbol,
            "frame": frame,
        }
    rows = frame.filter(pl.col("symbol").eq(symbol)).height
    if rows == 0:
        return {
            "state": "SOURCE_ERROR",
            "reason": "INVALID_KNOWN_SYMBOL_RESPONSE",
            "symbol": symbol,
            "frame": frame,
        }
    return {"state": "EXISTS", "reason": "valid_bars", "symbol": symbol, "frame": frame}


def roster_closure_receipt(
    days: list[date],
    roster_fn: Any,
    *,
    stock_basic_symbols: set[str] | None = None,
) -> dict[str, Any]:
    """Baostock roster closure evidence receipt (V07.2).

    stock_basic is the SH/SZ formal historical identity authority; roster_on is
    closure/reconciliation evidence ONLY. Bidirectional fail-closed:
    A = stock_basic_expected - roster_union, B = roster_union - stock_basic.
    closed iff failed_dates_n==0 and len(A)==0 and len(B)==0.
    This helper ALWAYS returns the receipt (never raises), so the caller can
    atomically persist it and then raise NOT_CLOSED when closed == False.
    """
    success_dates: list[date] = []
    failed_dates: list[date] = []
    union: set[str] = set()
    for day in days:
        try:
            roster = set(roster_fn(day))
        except Exception as exc:
            failed_dates.append(day)
            continue
        if not roster:
            # empty roster on a trading day = SOURCE_ERROR / NOT CLOSED
            failed_dates.append(day)
            continue
        success_dates.append(day)
        union |= roster

    missing_from_roster: set[str] = set()
    extra_in_roster: set[str] = set()
    if stock_basic_symbols is not None:
        missing_from_roster = stock_basic_symbols - union  # A
        extra_in_roster = union - stock_basic_symbols  # B
    unresolved_n = (
        len(failed_dates) + len(missing_from_roster) + len(extra_in_roster)
    )
    closed = unresolved_n == 0
    receipt = {
        "expected_dates_n": len(days),
        "success_dates_n": len(success_dates),
        "failed_dates_n": len(failed_dates),
        "failed_dates_sample": [d.isoformat() for d in failed_dates[:10]],
        "union_symbol_n": len(union),
        "union_symbol_hash": sha256_bytes(
            json.dumps(sorted(union), separators=(",", ":")).encode()
        ),
        "closed": closed,
        "unresolved_n": unresolved_n,
        "identity_not_in_roster_n": len(missing_from_roster),
        "identity_not_in_roster_hash": (
            sha256_bytes(
                json.dumps(sorted(missing_from_roster), separators=(",", ":")).encode()
            )
            if missing_from_roster
            else "0" * 64
        ),
        "identity_not_in_roster_sample": sorted(missing_from_roster)[:200],
        "roster_not_in_identity_n": len(extra_in_roster),
        "roster_not_in_identity_hash": (
            sha256_bytes(
                json.dumps(sorted(extra_in_roster), separators=(",", ":")).encode()
            )
            if extra_in_roster
            else "0" * 64
        ),
        "roster_not_in_identity_sample": sorted(extra_in_roster)[:200],
    }
    return receipt


def v072_exit_verdict(bj_authority: str, unresolved_n: int | None) -> dict[str, Any]:
    """Frozen BJ historical gate (V07.2). NECESSARY, never sufficient.

    Returns ONLY the BJ_HISTORICAL_GATE and its inputs; it never produces
    DAILY_READY or R3_EXIT by itself. DAILY_READY is derived solely by
    tools/verify_r3_daily_foundation.py after every R3 quality gate AND this
    gate == PASS. UNKNOWN/null unresolved is never 0.
    """
    gate = "PASS" if (bj_authority == "PROVEN" and unresolved_n == 0) else "BLOCKED"
    return {
        "BJ_HISTORICAL_GATE": gate,
        "bj_historical_authority": bj_authority,
        "bj_historical_unresolved_n": unresolved_n,
        "blocker": (
            None
            if gate == "PASS"
            else "HISTORICAL_DELISTED_BJ_UNKNOWN_CARRIED"
        ),
        "R4_EXECUTION": "FORBIDDEN",
    }


def stage_e_target_partition(
    formal: dict[str, date],
    catalog: dict[str, date],
    instruments_df: Any,
    as_of: date,
    history_start: date,
) -> dict[str, Any]:
    """Stage-E target partition (V07.2).

    Membership authority = Baostock FORMAL historical identity. Sina catalog is
    CROSSCHECK only and never decides membership. `active` uses real date
    semantics: delist is null or >= as_of AND list is null or <= as_of. A formal
    delisted symbol that is already present in curated instruments is STILL a
    recovery target.
    """
    active: set[str] = set()
    if instruments_df is not None and not instruments_df.is_empty():
        df = instruments_df.filter(
            (pl.col("delist_date").is_null() | (pl.col("delist_date") >= as_of))
            & (pl.col("list_date").is_null() | (pl.col("list_date") <= as_of))
        )
        active = set(df["symbol"].to_list())
    targets = sorted(s for s in formal if s not in active)
    no_data = sorted(
        s
        for s in targets
        if formal.get(s) is not None and formal.get(s) < history_start
    )
    recover = sorted(s for s in targets if s not in no_data)
    return {
        "targets": targets,
        "no_data": no_data,
        "recover": recover,
        "sh_sz": sorted(s for s in recover if not s.endswith(".BJ")),
        "bj": sorted(s for s in recover if s.endswith(".BJ")),
        "active": sorted(active),
        "sina_catalog_role": "CROSSCHECK_ONLY",
        "sina_catalog_symbols_n": len(catalog),
        "target_set_sha": sha256_bytes(
            json.dumps(sorted(targets), separators=(",", ":")).encode()
        ),
    }


# --- Service ledger / state machine ----------------------------------------


@dataclass
class ServiceLedger:
    path: Path

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = dict(record)
        record.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        record.setdefault("record_id", uuid.uuid4().hex)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str, sort_keys=True) + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def symbols_hash(self, symbols: Iterable[str]) -> str:
        return hashlib.sha256(
            json.dumps(sorted(set(symbols)), separators=(",", ":")).encode()
        ).hexdigest()


STAGES = ("preflight", "A_instruments", "B_discovery", "C_merge", "C2_enrich", "D_calendar", "E_delisted", "F_daily", "G_coverage")
STAGE_ORDER = {name: index for index, name in enumerate(STAGES)}
ENTRY_ORDER = STAGES[1:]  # excludes preflight; preflight never writes stage state


class StageMachine:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"status": "pending", "completed": [], "current": None}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        atomic_write_json(self.path, state)

    def enter(self, stage: str) -> dict[str, Any]:
        state = self.load()
        if stage in state["completed"]:
            raise R3Error("STAGE_ALREADY_COMPLETE", f"stage {stage} already complete")
        if state.get("current") is not None:
            raise R3Error(
                "STAGE_IN_PROGRESS", f"stage {state['current']} still in progress"
            )
        if stage not in ENTRY_ORDER:
            raise R3Error("STAGE_UNKNOWN", f"cannot enter non-execution stage {stage}")
        index = ENTRY_ORDER.index(stage)
        expected_prefix = list(ENTRY_ORDER[:index])
        if state["completed"] != expected_prefix:
            raise R3Error(
                "STAGE_PREREQUISITE",
                f"stage {stage} requires completed == "
                f"{expected_prefix or []}; got {state['completed']} "
                "(forward skip or missing immediate predecessor)",
            )
        state["current"] = stage
        state["status"] = "running"
        state.setdefault("started_at", datetime.now(timezone.utc).isoformat())
        self.save(state)
        return state

    def complete(self, stage: str, evidence: dict[str, Any]) -> dict[str, Any]:
        state = self.load()
        if stage not in state["completed"]:
            state["completed"].append(stage)
        state["current"] = None
        state["status"] = "pending"
        state.setdefault("evidence", {})[stage] = evidence
        self.save(state)
        return state

    def resume_current(
        self,
        stage: str,
        *,
        route: str,
        checkpoint_present: bool,
        resume_from_index: int,
        resume_count: int,
    ) -> dict[str, Any]:
        """Append-only same-stage resume of an interrupted running stage (V07.3).

        Required: current == stage, status == running, completed == the exact
        legal prefix of stage (fresh entry also satisfies this because
        ``enter`` persisted current=running before the orchestration runs).
        Adds an append-only ``resumes[]`` lineage record. Never clears current,
        never adds the stage to completed, never deletes evidence.
        """
        state = self.load()
        if state.get("current") != stage or state.get("status") != "running":
            raise R3Error(
                "RECOVERY_STATE_MISMATCH",
                f"expected current={stage} running; got current="
                f"{state.get('current')} status={state.get('status')}",
            )
        if stage not in ENTRY_ORDER:
            raise R3Error("STAGE_UNKNOWN", f"cannot resume unknown stage {stage}")
        index = ENTRY_ORDER.index(stage)
        expected_prefix = list(ENTRY_ORDER[:index])
        if state["completed"] != expected_prefix:
            raise R3Error(
                "RECOVERY_STATE_MISMATCH",
                f"resume {stage} requires completed == {expected_prefix}; "
                f"got {state['completed']}",
            )
        state.setdefault("resumes", []).append(
            {
                "stage": stage,
                "resumed_at": datetime.now(timezone.utc).isoformat(),
                "route": route,
                "checkpoint_present": checkpoint_present,
                "resume_from_index": resume_from_index,
                "resume_count": resume_count,
            }
        )
        self.save(state)
        return state

    def abandon_current(
        self,
        expected_stage: str,
        *,
        reason: str,
        replacement: str | None = None,
    ) -> dict[str, Any]:
        """Append-only abandon of an interrupted running stage (fail-closed).

        Required: current == expected_stage, status == running, completed == the
        exact legal prefix of expected_stage. After: completed unchanged,
        current = None, status = pending, and an append-only `abandoned` record
        is added. Never adds the stage to completed, never deletes evidence.
        """
        state = self.load()
        if state.get("current") != expected_stage or state.get("status") != "running":
            raise R3Error(
                "RECOVERY_STATE_MISMATCH",
                f"expected current={expected_stage} running; got current="
                f"{state.get('current')} status={state.get('status')}",
            )
        if expected_stage not in ENTRY_ORDER:
            raise R3Error("STAGE_UNKNOWN", f"cannot abandon unknown stage {expected_stage}")
        index = ENTRY_ORDER.index(expected_stage)
        expected_prefix = list(ENTRY_ORDER[:index])
        if state["completed"] != expected_prefix:
            raise R3Error(
                "RECOVERY_STATE_MISMATCH",
                f"abandon {expected_stage} requires completed == "
                f"{expected_prefix}; got {state['completed']}",
            )
        recorded = {
            "stage": expected_stage,
            "reason": reason,
            "replacement": replacement,
            "abandoned_at": datetime.now(timezone.utc).isoformat(),
            "prior_started_at": state.get("started_at"),
        }
        state.setdefault("abandoned", []).append(recorded)
        state["current"] = None
        state["status"] = "pending"
        self.save(state)
        return state


# --- R3 runner ------------------------------------------------------------


class R3Runner:
    """Authorized R3 orchestrator around pinned raw adapters."""

    def __init__(
        self,
        config_path: Path,
        *,
        repo_root: Path,
        plan_sha: str = PLAN_SHA,
        history_start: date = R3_HISTORY_START,
        daily_as_of: date = R3_DAILY_AS_OF,
    ) -> None:
        self.config_path = config_path.resolve()
        self.repo_root = repo_root.resolve()
        self.plan_sha = plan_sha
        self.history_start = history_start
        self.daily_as_of = daily_as_of
        if plan_sha != PLAN_SHA:
            raise R3Error("PLAN_SHA_UNKNOWN", f"unknown reviewed plan SHA {plan_sha}")
        if not has_ancestor(self.repo_root, PLAN_SHA):
            raise R3Error(
                "PLAN_NOT_ANCESTOR", f"repo HEAD lacks reviewed plan {PLAN_SHA}"
            )
        self.cfg: Config = load_config(self.config_path)
        if getattr(self.cfg, "data_root", None) is None:
            raise R3Error("CONFIG_ROOT", "data root not set; refusing to execute")
        self.root: Path = Path(self.cfg.data_root)
        self.meta = self.root / "meta" / "asl" / "r3"
        self.state_path = self.meta / "execution-state.json"
        self.ledger = ServiceLedger(self.meta / "service-ledger.jsonl")
        self.machine = StageMachine(self.state_path)
        self._network_env_cleared: list[str] = []

    def _prepare_network_env(self) -> None:
        """Writer-stage-only env prep: clear ambient HTTP(S)/SOCKS proxies.

        Never called in __init__ or preflight. The frozen config declares no
        proxy; httpx (EastMoney) would otherwise fail without socksio for a
        SOCKS ALL_PROXY. This is a runtime-env guard, not a config/dependency
        change. It records the removal and persists a small note ONLY as part
        of a writer stage, never during read-only preflight.
        """
        removed = {
            key: os.environ.pop(key)
            for key in list(os.environ)
            if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
        }
        if removed:
            logger.warning(
                "AMBIENT_PROXY_CLEARED(writer-stage): removed %s",
                sorted(set(k.upper() for k in removed)),
            )
            self._network_env_cleared = sorted(set(k.upper() for k in removed))

    # --- guards ---------------------------------------------------------

    def _check_legacy_isolation(self) -> None:
        for legacy in LEGACY_ROOTS:
            resolved = str(legacy.resolve())
            if str(self.root.resolve()) == resolved or self.root.resolve().is_relative_to(resolved):
                raise R3Error("LEGACY_ISOLATION", f"target root resolves under legacy {legacy}")
        if str(self.root.resolve()) in {str(x.resolve()) for x in LEGACY_ROOTS}:
            raise R3Error("LEGACY_ISOLATION", "target root equals a legacy root")

    def _check_argv_surface(self, argv: Iterable[str]) -> None:
        joined = " ".join(str(a) for a in argv)
        for fragment in FORBIDDEN_UPSTREAM_SURFACE:
            if fragment in joined:
                raise R3Error("FORBIDDEN_SURFACE", f"forbidden upstream surface: {fragment}")

    def _acquire_lock(self) -> int:
        self.meta.mkdir(parents=True, exist_ok=True)
        path = self.meta / "runner.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise R3Error("WRITER_LOCKED", "another R3 runner holds the writer lock")
        os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
        return fd

    def _release_lock(self, fd) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def service_lock_active(self) -> bool:
        """Read-only proof of an active writer lock (no lock mutation)."""
        path = self.meta / "runner.lock"
        if not path.exists():
            return False
        try:
            fd = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    def run_writer_stage(self, name: str) -> dict[str, Any]:
        """Single-writer stage entrypoint: lock -> execute one stage -> release."""
        fd = self._acquire_lock()
        try:
            stage_map = {
                "A_instruments": self.stage_instruments,
                "B_discovery": self.stage_discovery,
                "C_merge": self.stage_merge,
                "C2_enrich": self.stage_enrich,
                "D_calendar": self.stage_calendar,
                "E_delisted": self.stage_delisted,
                "F_daily": self.stage_daily,
                "G_coverage": self.stage_coverage,
            }
            if name not in stage_map:
                raise R3Error("STAGE_UNKNOWN", f"unknown writer stage {name}")
            return stage_map[name]()
        finally:
            self._release_lock(fd)

    # --- preflight --------------------------------------------------------

    def preflight(self) -> dict[str, Any]:
        """Read-only preflight (Task 2). Writes only meta receipts."""
        self._check_legacy_isolation()
        self._check_argv_surface(sys.argv)
        provenance = runtime_provenance(self.cfg, self.config_path)
        if not self.root.exists():
            raise R3Error("TARGET_ROOT_MISSING", f"target root not found: {self.root}")
        if getattr(self.cfg, "workers", 1) != 1:
            raise R3Error("CONFIG_WORKERS", "orchestrator.workers must be 1 (macOS fork safety)")
        if self.cfg.tdx_allow_mock:
            raise R3Error("CONFIG_MOCK", "allow_mock must be false")
        from cnequity.domain.datasets import is_dataset_enabled

        if is_dataset_enabled("minute_bars", self.cfg):
            raise R3Error("CONFIG_MINUTE", "minute bars must be disabled in R3")
        if is_dataset_enabled("trade_ticks", self.cfg):
            raise R3Error("CONFIG_TICKS", "trade ticks must be disabled in R3")

        statvfs = os.statvfs(self.root)
        free_gib = statvfs.f_bavail * statvfs.f_frsize / (1024 ** 3)
        if free_gib < 100:
            raise R3Error("DISK_LOW", f"only {free_gib:.1f} GiB free; need >= 100 GiB")

        state = self.machine.load()
        first_run = not state.get("completed")
        snapshot = target_tree_snapshot(self.root, exclude="meta/asl/r3")
        digest = snapshot["digest"]
        if first_run:
            layout_errors = zero_data_layout_errors(self.root)
            if layout_errors:
                raise R3Error(
                    "R3_PREFLIGHT_STATE_DRIFT",
                    "target root not at R2 zero-data baseline: " + "; ".join(layout_errors),
                )
            if digest != R2_ZERO_DATA_REFERENCE_SHA:
                logger.warning(
                    "canonical digest %s differs from recorded R2 reference %s; "
                    "structural zero-data check governs",
                    digest,
                    R2_ZERO_DATA_REFERENCE_SHA,
                )
        else:
            curated_ok = set()
            curated_dir = self.root / "curated"
            if curated_dir.is_dir():
                curated_ok = {p.name for p in curated_dir.iterdir() if p.is_dir()}
            unexpected_datasets = sorted(
                curated_ok - {"instruments", "trading_calendar", "daily_bars"}
            )
            if unexpected_datasets:
                raise R3Error(
                    "NON_R3_DATASET",
                    f"unexpected curated dataset(s): {unexpected_datasets}",
                )
            for sub in ("derived", "raw"):
                sub_path = self.root / sub
                if sub_path.is_dir() and any(sub_path.rglob("*.parquet")):
                    raise R3Error("NON_R3_PAYLOAD", f"parquet present under {sub}/")

        manifest_path = Path(self.cfg.manifest_path)
        wal = Path(f"{manifest_path}-wal")
        if manifest_path.exists():
            if wal.exists() and wal.stat().st_size not in (0, 32):
                raise R3Error("MANIFEST_WAL", "non-empty manifest WAL present; stop")

        return {
            "plan_sha": self.plan_sha,
            "base_head": BASE_HEAD,
            "repo_head": git_sha(self.repo_root),
            "runtime": provenance,
            "root": str(self.root),
            "tree_digest": digest,
            "free_gib": round(free_gib, 1),
            "legacy_isolation": True,
            "surface_clean": True,
            "service_writer_lock_active": self.service_lock_active(),
            "callable_contract": approved_callable_contract(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    # --- stage helpers -----------------------------------------------------

    def _new_run(self, job_name: str) -> str:
        manifest = Manifest(self.cfg.manifest_path)
        return manifest.start_run(job_name, {"trade_date": self.daily_as_of.isoformat(), "backfill": True})

    def _run_single_step_runjob(self, steps: list[str]) -> dict[str, Any]:
        """One JobEngine.run_job over an allowlisted step set (no generic daily)."""
        allowed = {"instruments", "trading_calendar"}
        if not set(steps) <= allowed:
            raise R3Error("FORBIDDEN_STEP", f"step not allowed: {set(steps) - allowed}")
        engine = JobEngine(self.cfg)
        run_id = self._new_run(f"r3_{steps[0]}")
        try:
            result = engine.run_job(
                f"r3_{steps[0]}",
                self.daily_as_of,
                steps=steps,
                backfill=True,
                run_id=run_id,
                finalize_run=False,
            )
        except Exception as exc:
            # the created manifest run must reach a terminal failed status even
            # when run_job itself raises (no compact, no machine.complete)
            Manifest(self.cfg.manifest_path).finish_run(
                run_id, "failed", error_message=f"{type(exc).__name__}: {exc}"
            )
            raise
        return {"run_id": run_id, "result": result}

    def _compact(self, run_id: str) -> dict[str, Any]:
        engine = JobEngine(self.cfg)
        out = engine.run_step("compact", self.daily_as_of, run_id, {})
        if out.get("status") != "success":
            raise R3Error("COMPACT_FAILED", f"compact failed: {out}")
        return out

    def _run_single_step_terminal(self, steps: list[str]) -> dict[str, Any]:
        """Single-step run + compact + manifest terminalize (success/failed).

        Fixes the future-Stage-A gap: a successful run_job (finalize_run=False)
        followed by compact is now finalized with Manifest.finish_run success.
        Any failure is auditable with finish_run failed; never a fake success.
        """
        out = self._run_single_step_runjob(steps)
        run_id = out["run_id"]
        try:
            compact = self._compact(run_id)
            rows_read = int(out["result"].get("rows_read") or 0)
            rows_written = int(out["result"].get("rows_written") or 0)
            Manifest(self.cfg.manifest_path).finish_run(
                run_id, "success", rows_read=rows_read, rows_written=rows_written
            )
        except Exception as exc:
            try:
                Manifest(self.cfg.manifest_path).finish_run(
                    run_id, "failed", error_message=f"{type(exc).__name__}: {exc}"
                )
            except Exception:
                pass
            raise
        return {"job": out, "run_id": run_id, "compact": compact}

    def finalize_completed_a_manifest(self) -> dict[str, Any]:
        """Terminalize the exact Stage-A instruments manifest run (idempotent)."""
        state = self.machine.load()
        completed = list(state.get("completed") or [])
        if completed != ["A_instruments"]:
            raise R3Error(
                "A_MANIFEST_RECOVERY_MISMATCH",
                f"completed must be exactly ['A_instruments']; got {completed}",
            )
        evidence = (state.get("evidence") or {}).get("A_instruments") or {}
        run_id = (evidence.get("job") or {}).get("run_id")
        if not run_id:
            raise R3Error("A_MANIFEST_RECOVERY_MISMATCH", "no A run_id in state evidence")
        if (evidence.get("compact") or {}).get("status") != "success":
            raise R3Error(
                "A_MANIFEST_RECOVERY_MISMATCH",
                "A evidence compact.status != success",
            )
        manifest = Manifest(self.cfg.manifest_path)
        run = manifest.get_run(run_id)
        if run is None:
            raise R3Error(
                "A_MANIFEST_RECOVERY_MISMATCH", f"manifest run {run_id} not found"
            )
        job_name = str(run["job_name"])
        run_status = str(run["status"])
        if job_name != "r3_instruments":
            raise R3Error(
                "A_MANIFEST_RECOVERY_MISMATCH",
                f"manifest job_name {job_name} != r3_instruments",
            )
        batches = manifest.get_batches_for_run(run_id)
        statuses = sorted({(str(b["dataset"]), str(b["status"])) for b in batches})
        datasets = [ds for ds, _st in statuses]
        if "instruments" not in datasets or "compact" not in datasets:
            raise R3Error(
                "A_MANIFEST_RECOVERY_MISMATCH",
                f"missing instruments/compact batch; got {datasets}",
            )
        if any(status != "success" for _ds, status in statuses):
            raise R3Error(
                "A_MANIFEST_RECOVERY_MISMATCH",
                f"non-success batch statuses: {statuses}",
            )
        incomplete = manifest.incomplete_batch_counts_by_dataset(run_id) or {}
        if any(n > 0 for n in incomplete.values()):
            raise R3Error(
                "A_MANIFEST_RECOVERY_MISMATCH",
                f"incomplete batches present: {incomplete}",
            )
        rows_read = int(
            evidence.get("job", {}).get("result", {}).get("rows_read", 0) or 0
        )
        rows_written = int(
            evidence.get("job", {}).get("result", {}).get("rows_written", 0) or 0
        )
        before = {"run_status": run_status, "batch_statuses": statuses}
        idempotent = run_status == "success"
        if idempotent:
            # already-success: verify manifest rows == state A evidence rows
            run_row = manifest.get_run(run_id)
            _rr = dict(run_row) if run_row is not None else {}
            manifest_rows_read = int(_rr.get("rows_read") or 0)
            manifest_rows_written = int(_rr.get("rows_written") or 0)
            if manifest_rows_read != rows_read or manifest_rows_written != rows_written:
                raise R3Error(
                    "A_MANIFEST_RECOVERY_MISMATCH",
                    f"success run rows ({manifest_rows_read}/{manifest_rows_written}) "
                    f"!= A evidence rows ({rows_read}/{rows_written})",
                )
        else:
            if run_status != "running":
                raise R3Error(
                    "A_MANIFEST_RECOVERY_MISMATCH",
                    f"manifest run status {run_status} not running",
                )
            manifest.finish_run(
                run_id, "success", rows_read=rows_read, rows_written=rows_written
            )
        after_run = manifest.get_run(run_id)
        after_status = str(after_run["status"]) if after_run else "UNKNOWN"
        return {
            "run_id": run_id,
            "status": after_status,
            "idempotent": bool(idempotent),
            "rows_read": rows_read,
            "rows_written": rows_written,
            "a_batch_statuses": statuses,
            "manifest_before": before,
            "manifest_after": {"run_status": after_status},
        }

    def recover_interrupted_control_plane(self) -> dict[str, Any]:
        """Fail-closed control-plane recovery for the B_discovery incident.

        Exact incident: completed==[A_instruments], current==B_discovery,
        status==running, and no V07.2 identity receipt. Terminalizes the exact
        A manifest run, appends a recovery ledger event, abandons the legacy B
        marker (append-only), writes control-plane-recovery-v01.json, then
        releases the writer lock. No market stage, no provider, no delete.
        """
        fd = self._acquire_lock()
        try:
            state = self.machine.load()
            current = state.get("current")
            completed = list(state.get("completed") or [])
            if current != "B_discovery" or completed != ["A_instruments"]:
                raise R3Error(
                    "B_RECOVERY_NOT_SAFE",
                    f"incident must be current=B_discovery completed=[A]; "
                    f"got current={current} completed={completed}",
                )
            prior_hash = (
                sha256_file(self.state_path)
                if self.state_path.exists()
                else "0" * 64
            )
            identity = self.meta / "r3-identity-receipt.json"
            identity_before = identity.exists()
            if identity_before:
                raise R3Error(
                    "B_RECOVERY_NOT_SAFE",
                    "V07.2 identity receipt present; abort recovery",
                )
            finalized = self.finalize_completed_a_manifest()
            self.ledger.append(
                {
                    "stage": "CTRL_RECOVERY",
                    "event": "INTERRUPTED_B_ABANDONED",
                    "reason": "LEGACY_SINA_PARTIAL_SUPERSEDED_BY_V07_2",
                    "replacement": "V07.2_identity_completion",
                    "state_before_hash": prior_hash,
                    "a_finalize_run_id": finalized["run_id"],
                }
            )
            # deterministic target state; abandon must come ONLY after the
            # recovery receipt is durably written (fail-closed order)
            projected_state_after = {
                "status": "pending",
                "current": None,
                "completed": ["A_instruments"],
            }
            receipt = {
                "recovery_type": "R3_INTERRUPTED_CONTROL_PLANE_RECOVERY",
                "prior_state_hash": prior_hash,
                "prior_current": current,
                "prior_completed": completed,
                "a_run_id": finalized["run_id"],
                "a_manifest_before": finalized["manifest_before"],
                "a_manifest_after": finalized["manifest_after"],
                "a_batch_statuses": finalized["a_batch_statuses"],
                "legacy_b_classification": "B_PARTIAL_NO_COMPLETE_RECEIPT_LEGACY_SINA",
                "legacy_b_evidence_preserved": True,
                "v072_identity_receipt_present_before": bool(identity_before),
                "state_after": projected_state_after,
                "recovery_timestamp": datetime.now(timezone.utc).isoformat(),
                "plan_sha": self.plan_sha,
            }
            atomic_write_json(
                self.meta / "control-plane-recovery-v01.json", receipt
            )
            # abandon the legacy B marker LAST; if receipt write failed above,
            # current stays B_discovery/running and re-entry stays blocked.
            self.machine.abandon_current(
                "B_discovery",
                reason="LEGACY_SINA_PARTIAL_SUPERSEDED_BY_V07_2",
                replacement="V07.2_identity_completion",
            )
            state_after = self.machine.load()
            receipt["state_after"] = {
                "status": state_after["status"],
                "current": state_after.get("current"),
                "completed": state_after["completed"],
            }
            return receipt
        finally:
            self._release_lock(fd)

    # --- stages ------------------------------------------------------------

    def stage_instruments(self) -> dict[str, Any]:
        self._prepare_network_env()
        self.machine.enter("A_instruments")
        run = self._run_single_step_terminal(["instruments"])
        receipt = {"job": run["job"], "compact": run["compact"]}
        self.machine.complete("A_instruments", receipt)
        return receipt

    def stage_discovery(self) -> dict[str, Any]:
        """V07.2 identity completion + V07.3 resumable roster closure.

        No Sina issued-code sweep: SH/SZ identity/closure come from Baostock via
        a single shared session with an atomic progress checkpoint; BJ current
        identity from EastMoney clist; BJ historical is UNPROVABLE_BOUNDED_RESEARCH
        -> HISTORICAL_DELISTED_BJ = UNKNOWN_CARRIED.

        Re-entry is same-stage resume: when the interrupted V07.2/V07.3 run
        already left current=B_discovery running on the exact A prefix, we do not
        call StageMachine.enter again (it would raise STAGE_IN_PROGRESS); the
        orchestration records the resume lineage and continues from the
        checkpoint (or boots from zero for the legacy no-checkpoint incident).
        """
        state = self.machine.load()
        if state.get("current") != "B_discovery":
            self.machine.enter("B_discovery")
        receipt = self._identity_completion_v072()
        self.machine.complete("B_discovery", receipt)
        return receipt

    def _identity_completion_v072(self) -> dict[str, Any]:
        self._prepare_network_env()
        from cnequity.adapters.baostock.instruments import fetch_instrument_basics
        from cnequity.adapters.eastmoney.clist import clist_rows_to_symbols, fetch_clist_pages
        from cnequity.adapters.eastmoney.em_auth import EastMoneyClient

        basic_df = fetch_instrument_basics()
        if basic_df.height:
            formal_identity_df = basic_df.filter(
                (pl.col("exchange").is_in(["SH", "SZ"]))
                & (pl.col("asset_type").is_in(["stock", "cdr"]))
                & (pl.col("list_date").is_null() | (pl.col("list_date") <= self.daily_as_of))
                & (
                    pl.col("delist_date").is_null()
                    | (pl.col("delist_date") >= self.history_start)
                )
            )
            formal_identity_symbols = set(formal_identity_df["symbol"].to_list())
            # ROSTER_CLOSURE_SCOPE: only common-stock names the pinned roster can
            # actually observe. CDRs (e.g. 689xxx SH) stay in FORMAL_IDENTITY_SCOPE
            # and are recorded as roster_not_observable, never a false NOT_CLOSED.
            roster_closure_df = formal_identity_df.filter(
                pl.col("asset_type").eq("stock")
            )
            roster_closure_symbols = set(roster_closure_df["symbol"].to_list())
        else:
            formal_identity_symbols = set()
            roster_closure_symbols = set()
        identity_hash = sha256_bytes(
            json.dumps(sorted(formal_identity_symbols), separators=(",", ":")).encode()
        )
        roster_expected_hash = sha256_bytes(
            json.dumps(sorted(roster_closure_symbols), separators=(",", ":")).encode()
        )
        roster_not_observable = formal_identity_symbols - roster_closure_symbols
        roster_not_observable_hash = (
            sha256_bytes(
                json.dumps(sorted(roster_not_observable), separators=(",", ":")).encode()
            )
            if roster_not_observable
            else "0" * 64
        )

        dates = list_trading_dates(self.cfg, self.history_start, self.daily_as_of)
        if not dates:
            raise R3Error("NO_TRADING_DATES", "no trading dates in R3 window")
        sweep = self._resumable_roster_sweep(
            dates,
            roster_closure_symbols=roster_closure_symbols,
            formal_identity_hash=identity_hash,
            roster_expected_hash=roster_expected_hash,
        )
        closure = sweep["closure"]

        # SH/SZ formal delisted map (Baostock stock_basic, in-window scope)
        shsz_formal_delisted: dict[str, str] = {}
        if basic_df.height:
            delisted_df = basic_df.filter(
                (pl.col("exchange").is_in(["SH", "SZ"]))
                & (pl.col("asset_type").is_in(["stock", "cdr"]))
                & pl.col("delist_date").is_not_null()
                & (pl.col("delist_date") >= self.history_start)
            )
            for row in delisted_df.select("symbol", "delist_date").iter_rows(named=True):
                shsz_formal_delisted[row["symbol"]] = row["delist_date"].isoformat()
        shsz_formal_delisted_hash = sha256_bytes(
            json.dumps(
                sorted(shsz_formal_delisted.items()),
                separators=(",", ":"),
            ).encode()
        )

        # Excerpt of the Stage-A formal evidence for drift comparison (same source)
        stage_a_formal = known_delisted_instruments(self.cfg, self.daily_as_of)
        fresh_scope = set(shsz_formal_delisted)
        existing_windows = {
            s: stage_a_formal[s]
            for s in stage_a_formal
            if s in fresh_scope or (s.endswith((".SH", ".SZ")) and stage_a_formal[s] >= self.history_start)
        }
        fresh_iso = {s: shsz_formal_delisted[s] for s in fresh_scope}
        existing_iso_map = {s: d.isoformat() for s, d in existing_windows.items()}
        existing_iso = {s: v for s, v in existing_iso_map.items() if s in fresh_scope}
        drift_missing = sorted(fresh_scope - set(existing_iso))
        drift_extra = sorted(set(existing_iso_map) - fresh_scope)
        drift_date_mismatch = sorted(
            s for s in fresh_scope if s in existing_iso and existing_iso[s] != fresh_iso[s]
        )

        # BJ current identity: EM clist -> complete active BJ membership
        client = EastMoneyClient(config=self.cfg)
        try:
            clist = fetch_clist_pages(client, fields="f12,f13,f14,f26")
        finally:
            client.close()
        bj_membership: list[dict[str, Any]] = []
        for sym, item in clist_rows_to_symbols(clist):
            if not sym.endswith(".BJ"):
                continue
            list_date = parse_clist_date(item.get("f26"))
            name = (item.get("f14") or "").strip() or None
            if not name or list_date is None or list_date > self.daily_as_of:
                raise R3Error(
                    "BLOCKED_ALL_A_METADATA",
                    f"active BJ {sym} lacks verified name/list_date in EM clist",
                )
            bj_membership.append(
                {
                    "symbol": sym,
                    "name": name,
                    "exchange": "BJ",
                    "asset_type": "stock",
                    "list_date": list_date,
                    "delist_date": None,
                    "prev_symbol": None,
                }
            )
        bj_current = sorted(m["symbol"] for m in bj_membership)
        bj_current_hash = sha256_bytes(
            json.dumps(bj_current, separators=(",", ":")).encode()
        )

        receipt = {
            "route": "V07.2_identity_completion",
            "shsz_identity_authority": "Baostock_stock_basic",
            "shsz_identity_symbols": len(formal_identity_symbols),
            "shsz_identity_hash": identity_hash,
            "formal_identity_scope": ["SH", "SZ", "stock", "cdr"],
            "formal_identity_n": len(formal_identity_symbols),
            "formal_identity_hash": identity_hash,
            "roster_closure_scope": ["SH", "SZ", "stock"],
            "roster_expected_n": len(roster_closure_symbols),
            "roster_expected_hash": roster_expected_hash,
            "roster_not_observable_identity_n": len(roster_not_observable),
            "roster_not_observable_identity_hash": roster_not_observable_hash,
            "roster_not_observable_identity_sample": sorted(roster_not_observable)[:200],
            "shsz_formal_delisted": shsz_formal_delisted,
            "shsz_formal_delisted_n": len(shsz_formal_delisted),
            "shsz_formal_delisted_hash": shsz_formal_delisted_hash,
            "stage_a_formal_hash": sha256_bytes(
                json.dumps(
                    sorted(existing_iso_map.items()),
                    separators=(",", ":"),
                ).encode()
            ),
            "formal_drift": {
                "missing": drift_missing[:200],
                "extra": drift_extra[:200],
                "date_mismatch": drift_date_mismatch[:200],
                "missing_n": len(drift_missing),
                "extra_n": len(drift_extra),
                "date_mismatch_n": len(drift_date_mismatch),
            },
            "shsz_closure": closure,
            "bj_current_authority": "EastMoney_clist",
            "bj_current_symbols": len(bj_current),
            "bj_current_hash": bj_current_hash,
            "bj_current_membership": bj_membership,
            "bj_historical_authority": BJ_HISTORICAL_AUTHORITY_VERDICT,
            "bj_historical_delisted": HISTORICAL_DELISTED_BJ_LABEL,
            "bj_historical_resolved": False,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_json(self.meta / "r3-identity-receipt.json", receipt)
        # persist BEFORE raising so failed runs leave an auditable receipt
        if not closure["closed"]:
            raise R3Error(
                "NOT_CLOSED",
                f"roster not closed: {closure['unresolved_n']} unresolved "
                f"(failed_dates={closure['failed_dates_n']}, "
                f"identity_not_in_roster={closure['identity_not_in_roster_n']}, "
                f"roster_not_in_identity={closure['roster_not_in_identity_n']})",
            )
        if drift_missing or drift_extra or drift_date_mismatch:
            raise R3Error(
                "FORMAL_IDENTITY_DRIFT",
                f"Stage-B fresh formal map != Stage-A formal evidence: "
                f"missing={len(drift_missing)}, extra={len(drift_extra)}, "
                f"date_mismatch={len(drift_date_mismatch)}",
            )
        # V07.3: the closure is closed and identity drift is empty, so the
        # checkpoint may record a terminal success (never deleted; it stays as
        # lineage evidence that B completed).
        ckpt_path = self.meta / ROSTER_CHECKPOINT_FILENAME
        if ckpt_path.exists():
            ck = json.loads(ckpt_path.read_text(encoding="utf-8"))
            ck["terminal_status"] = "success"
            ck["final_identity_receipt_sha"] = sha256_bytes(
                json.dumps(
                    receipt, sort_keys=True, separators=(",", ":"), default=str
                ).encode()
            )
            self._write_roster_checkpoint(ck)
        self.ledger.append({"stage": "B_discovery", "v07_2_identity": True, "receipt": receipt})
        return receipt

    # --- V07.3 resumable roster closure ------------------------------------

    def _roster_union_hash(self, union: set[str]) -> str:
        return sha256_bytes(
            json.dumps(sorted(union), separators=(",", ":")).encode()
        )

    def _write_roster_checkpoint(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.meta / ROSTER_CHECKPOINT_FILENAME, payload)

    def _resumable_roster_sweep(
        self,
        days: list[date],
        *,
        roster_closure_symbols: set[str],
        formal_identity_hash: str,
        roster_expected_hash: str,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict[str, Any]:
        """V07.3 resumable SH/SZ roster closure over a single shared session.

        Reuses the pinned CNEquity Baostock session lifecycle (``import_baostock``,
        ``_login``, ``_relogin``, ``_ensure_socket_timeout``,
        ``_force_close_baostock_socket``). One login per episode with bounded
        proactive relogin every ``ROSTER_PROACTIVE_RELOGIN_EVERY`` successful
        dates (never per date). Each date is attempted up to
        ``ROSTER_SWEEP_MAX_ATTEMPTS`` times with ``ROSTER_SWEEP_BACKOFF``
        between attempts (injectable, so tests never sleep); a date that is
        still failing after the bound is persisted to the checkpoint as
        ``blocked_date`` and raises ``ROSTER_DATE_RETRY_EXHAUSTED`` — the sweep
        fail-fasts and never scans past it.

        Progress checkpoint (``r3-roster-closure-progress-v073.json``) is written
        atomically after EVERY successful date so a crash/SIGINT costs at most
        one re-fetch. Resume requires the checkpoint provenance hashes to match
        the freshly computed authority (``ROSTER_CHECKPOINT_PROVENANCE_MISMATCH``)
        and a self-consistent union hash (``ROSTER_CHECKPOINT_CORRUPT``). The
        legacy V07.2 interruption (current=B_discovery, no checkpoint, no final
        identity receipt) boots from zero under
        ``V07.3_BOOTSTRAP_RESUME_FROM_ZERO``.

        Returns ``{"closure": <standard closure receipt>, "checkpoint": ...}``.
        """
        from cnequity.adapters.baostock._session import (
            _ensure_socket_timeout,
            _force_close_baostock_socket,
            _login,
            _relogin,
            import_baostock,
        )
        from cnequity.adapters.baostock.delisted_bars import roster_on

        checkpoint_path = self.meta / ROSTER_CHECKPOINT_FILENAME
        expected_iso = sorted(d.isoformat() for d in days)
        expected_dates_hash = sha256_bytes(
            json.dumps(expected_iso, separators=(",", ":")).encode()
        )
        expected_dates_n = len(days)

        # A final r3-identity-receipt.json forbids any same-stage resume.
        if (self.meta / "r3-identity-receipt.json").exists():
            raise R3Error(
                "B_RESUME_FINAL_RECEIPT_PRESENT",
                "r3-identity-receipt.json already exists; same-stage resume is "
                "forbidden and a fresh sweep must not run.",
            )

        ckpt_present = checkpoint_path.exists()
        if ckpt_present:
            ckpt = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            expect = {
                "plan_sha": self.plan_sha,
                "history_start": self.history_start.isoformat(),
                "daily_as_of": self.daily_as_of.isoformat(),
                "formal_identity_hash": formal_identity_hash,
                "roster_expected_hash": roster_expected_hash,
                "expected_dates_n": expected_dates_n,
                "expected_dates_hash": expected_dates_hash,
            }
            for key, val in expect.items():
                if ckpt.get(key) != val:
                    raise R3Error(
                        "ROSTER_CHECKPOINT_PROVENANCE_MISMATCH",
                        f"checkpoint {key} changed: {ckpt.get(key)!r} != {val!r}",
                    )
            next_index = int(ckpt.get("next_index", -1))
            if not (0 <= next_index <= expected_dates_n):
                raise R3Error(
                    "ROSTER_CHECKPOINT_CORRUPT",
                    f"next_index {next_index} out of range [0, {expected_dates_n}]",
                )
            union = set(ckpt.get("union_symbols") or [])
            if self._roster_union_hash(union) != ckpt.get("union_symbol_hash"):
                raise R3Error(
                    "ROSTER_CHECKPOINT_CORRUPT",
                    "checkpoint union_symbol_hash inconsistent with union_symbols",
                )
            success_dates_n = int(ckpt.get("success_dates_n", -1))
            last_completed_date = ckpt.get("last_completed_date")
            resume_count = int(ckpt.get("resume_count", 0)) + 1
            episode = int(ckpt.get("execution_episode", 0)) + 1
        else:
            # Legacy V07.2 interruption / fresh first run: no checkpoint.
            next_index = 0
            success_dates_n = 0
            union = set()
            last_completed_date = None
            resume_count = 0
            episode = 1

        # Append-only same-stage resume lineage (fresh entry also persisted
        # current=B_discovery via StageMachine.enter, so this is satisfied).
        self.machine.resume_current(
            "B_discovery",
            route="V07.3_resumable_roster_closure",
            checkpoint_present=ckpt_present,
            resume_from_index=next_index,
            resume_count=resume_count,
        )

        def _write_ckpt() -> dict[str, Any]:
            payload: dict[str, Any] = {
                "schema_version": ROSTER_CHECKPOINT_SCHEMA_VERSION,
                "route": "V07.3_resumable_roster_closure",
                "plan_sha": self.plan_sha,
                "history_start": self.history_start.isoformat(),
                "daily_as_of": self.daily_as_of.isoformat(),
                "formal_identity_hash": formal_identity_hash,
                "roster_expected_hash": roster_expected_hash,
                "expected_dates_n": expected_dates_n,
                "expected_dates_hash": expected_dates_hash,
                "next_index": next_index,
                "last_completed_date": last_completed_date,
                "success_dates_n": success_dates_n,
                "union_symbol_n": len(union),
                "union_symbol_hash": self._roster_union_hash(union),
                "union_symbols": sorted(union),
                "blocked_date": blocked_date,
                "last_error_summary": last_error_summary,
                "execution_episode": episode,
                "resume_count": resume_count,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            self._write_roster_checkpoint(payload)
            return payload

        blocked_date: str | None = None
        last_error_summary: str | None = None
        bs = None
        rate_limit = getattr(self.cfg, "rate_limit", None)
        episode_retry_n = 0
        logger.info(
            "roster closure %s: %d/%d dates done, union=%d, resume_count=%d",
            "resume-from-checkpoint" if ckpt_present else "bootstrap-from-zero",
            next_index,
            expected_dates_n,
            len(union),
            resume_count,
        )
        try:
            i = next_index
            while i < expected_dates_n:
                day = days[i]
                last_err: str | None = None
                ok = False
                for attempt in range(1, ROSTER_SWEEP_MAX_ATTEMPTS + 1):
                    if bs is None:
                        bs = import_baostock()
                        _login(bs)
                        _ensure_socket_timeout()
                    if rate_limit is not None:
                        rate_limit("baostock")
                    try:
                        roster = roster_on(day, bs=bs, login=False)
                        ok = True
                        break
                    except KeyboardInterrupt:
                        # Never retry on SIGINT; checkpoint already holds the
                        # last completed prefix and the outer finally releases
                        # the service lock. The next run resumes from next_index.
                        raise
                    except Exception as exc:  # noqa: BLE001 - network/parse errors
                        last_err = f"{type(exc).__name__}: {exc}"
                        episode_retry_n += 1
                        _force_close_baostock_socket()
                        if attempt < ROSTER_SWEEP_MAX_ATTEMPTS:
                            _relogin(bs)
                            sleep(ROSTER_SWEEP_BACKOFF[min(attempt - 1, len(ROSTER_SWEEP_BACKOFF) - 1)])
                if not ok:
                    # fail-fast: persist progress + blocked_date, then STOP.
                    blocked_date = day.isoformat()
                    last_error_summary = last_err
                    _write_ckpt()
                    raise R3Error(
                        "ROSTER_DATE_RETRY_EXHAUSTED",
                        f"blocked_date={day.isoformat()} "
                        f"attempts={ROSTER_SWEEP_MAX_ATTEMPTS} "
                        f"last_error={last_err}",
                    )
                # success: atomically persist progress before the next date.
                union |= set(roster)
                last_completed_date = day.isoformat()
                success_dates_n += 1
                next_index = i + 1
                _write_ckpt()
                if success_dates_n % ROSTER_PROGRESS_LOG_EVERY == 0:
                    logger.info(
                        "roster progress %d/%d last_date=%s union_symbol_n=%d "
                        "resume_count=%d episode_retry_n=%d",
                        next_index,
                        expected_dates_n,
                        day,
                        len(union),
                        resume_count,
                        episode_retry_n,
                    )
                if success_dates_n % ROSTER_PROACTIVE_RELOGIN_EVERY == 0 and bs is not None:
                    _relogin(bs)
                i += 1
        finally:
            if bs is not None:
                try:
                    bs.logout()
                except Exception:  # noqa: BLE001 - best-effort logout
                    pass

        closure = {
            "expected_dates_n": expected_dates_n,
            "success_dates_n": success_dates_n,
            "failed_dates_n": 0,
            "failed_dates_sample": [],
            "union_symbol_n": len(union),
            "union_symbol_hash": self._roster_union_hash(union),
            "closed": True,
            "unresolved_n": 0,
            "identity_not_in_roster_n": 0,
            "identity_not_in_roster_hash": "0" * 64,
            "identity_not_in_roster_sample": [],
            "roster_not_in_identity_n": 0,
            "roster_not_in_identity_hash": "0" * 64,
            "roster_not_in_identity_sample": [],
        }
        if roster_closure_symbols is not None:
            missing = sorted(roster_closure_symbols - union)  # A
            extra = sorted(union - roster_closure_symbols)  # B
            closure.update(
                {
                    "identity_not_in_roster_n": len(missing),
                    "identity_not_in_roster_hash": (
                        sha256_bytes(
                            json.dumps(missing, separators=(",", ":")).encode()
                        )
                        if missing
                        else "0" * 64
                    ),
                    "identity_not_in_roster_sample": missing[:200],
                    "roster_not_in_identity_n": len(extra),
                    "roster_not_in_identity_hash": (
                        sha256_bytes(
                            json.dumps(extra, separators=(",", ":")).encode()
                        )
                        if extra
                        else "0" * 64
                    ),
                    "roster_not_in_identity_sample": extra[:200],
                    "closed": not missing and not extra,
                    "unresolved_n": len(missing) + len(extra),
                }
            )
        ckpt = _write_ckpt()
        return {"closure": closure, "checkpoint": ckpt, "union": union}

    def stage_merge(self) -> dict[str, Any]:
        self._prepare_network_env()
        self.machine.enter("C_merge")
        run = self._run_single_step_terminal(["instruments"])
        self.machine.complete("C_merge", {"job": run["job"], "compact": run["compact"]})
        return {"job": run["job"], "compact": run["compact"]}

    def stage_enrich(self) -> dict[str, Any]:
        self.machine.enter("C2_enrich")
        receipt = self._enrich_bj_metadata()
        self.machine.complete("C2_enrich", receipt)
        return receipt

    def _enrich_bj_metadata(self) -> dict[str, Any]:
        """C2: build the complete instruments snapshot from the Stage-B receipt.

        Existing non-BJ instruments + the COMPLETE Stage-B EM BJ membership
        (from r3-identity-receipt.json) => full instruments snapshot. Does not
        depend on Sina or on any BJ rows already present in curated instruments.
        """
        membership = self._load_identity_bj_membership()
        if not membership:
            raise R3Error("BLOCKED_ALL_A_UNIVERSE", "no active BJ membership from Stage-B receipt")
        bad_members = [
            m["symbol"]
            for m in membership
            if not m.get("name") or m.get("list_date") is None or m["list_date"] > self.daily_as_of
        ]
        if bad_members:
            raise R3Error(
                "BLOCKED_ALL_A_METADATA",
                f"{len(bad_members)} active BJ rows lack verified name/list_date: {bad_members[:20]}",
            )

        instruments = load_curated_instruments(self.cfg)
        if instruments is None or instruments.is_empty():
            raise R3Error(
                "BLOCKED_ALL_A_UNIVERSE",
                "no existing curated instruments; cannot build a complete snapshot "
                "(rejecting a BJ-only snapshot)",
            )
        non_bj = instruments.filter(~pl.col("symbol").str.ends_with(".BJ"))
        sh_sz_foundation = non_bj.filter(
            (pl.col("exchange").is_in(["SH", "SZ"]))
            & (pl.col("asset_type").is_in(["stock", "cdr"]))
        )
        if sh_sz_foundation.is_empty():
            raise R3Error(
                "BLOCKED_ALL_A_UNIVERSE",
                "no valid SH/SZ non-BJ foundation; rejecting a BJ-only snapshot",
            )

        # provenance applied ONLY to the Stage-B BJ membership dataframe;
        # non-BJ rows stay field-for-field unchanged; no with_provenance on merged
        bj_df = pl.DataFrame(
            sorted(
                membership,
                key=lambda m: m["symbol"],
            )
        )
        bj_df = with_provenance(
            pl.DataFrame(
                [
                    {k: m[k] for k in ("symbol", "name", "exchange", "asset_type", "list_date", "delist_date", "prev_symbol")}
                    for m in sorted(membership, key=lambda mm: mm["symbol"])
                ]
            ),
            source="eastmoney",
            data_version="v1",
        )
        merged = pl.concat([non_bj, bj_df], how="diagonal_relaxed")
        merged = merged.sort("symbol").unique(subset=["symbol"], keep="last")

        run_id = self._new_run("r3_c2_enrich")
        manifest = Manifest(self.cfg.manifest_path)
        batch_id = "c2-enrich-bj"
        self.ledger.append(
            {
                "stage": "C2",
                "event": "ATTEMPT_START",
                "symbols": sorted(merged["symbol"].to_list()),
                "batch_id": batch_id,
                "status": "running",
            }
        )
        manifest.start_batch(
            run_id,
            batch_id,
            task_id="instruments",
            dataset="instruments",
            symbols=sorted(merged["symbol"].to_list()),
            window_start=self.history_start.isoformat(),
            window_end=self.daily_as_of.isoformat(),
            blocks_compaction=True,
        )
        StagingWriter(self.cfg.staging_root).write_batch(
            "instruments", run_id, batch_id, merged
        )
        manifest.finish_batch(
            run_id, batch_id, "success", rows_read=merged.height, rows_written=merged.height
        )
        self.ledger.append(
            {
                "stage": "C2",
                "event": "ATTEMPT_END",
                "symbols": sorted(merged["symbol"].to_list()),
                "batch_id": batch_id,
                "status": "success",
                "rows": merged.height,
            }
        )
        try:
            compact = self._compact(run_id)
            post = load_curated_instruments(self.cfg)
            bad = (
                post.filter(pl.col("symbol").str.ends_with(".BJ"))
                .filter(pl.col("name").is_null() | pl.col("list_date").is_null())
                .height
            )
            if bad:
                raise R3Error("C2_POSTCHECK", f"{bad} BJ rows still lack metadata post-compact")
            bj_count = int(merged.filter(pl.col("symbol").str.ends_with(".BJ")).height)
            if bj_count != len(membership):
                raise R3Error(
                    "C2_POSTCHECK",
                    f"BJ membership mismatch: staged {bj_count}, receipt {len(membership)}",
                )
            expected_bj = {m["symbol"] for m in membership}
            actual_bj = set(
                post.filter(pl.col("symbol").str.ends_with(".BJ"))["symbol"].to_list()
            )
            extra_bj = sorted(actual_bj - expected_bj)
            missing_bj = sorted(expected_bj - actual_bj)
            if extra_bj or missing_bj:
                raise R3Error(
                    "C2_POSTCHECK",
                    f"post-compact BJ set mismatch: extra={len(extra_bj)} "
                    f"missing={len(missing_bj)}; fail-closed, "
                    "not auto-deleting preserved rows",
                )
            # controller terminal success ONLY after compact + post proof
            self.ledger.append(
                {
                    "stage": "C2",
                    "event": "CONTROLLER_COMPLETE",
                    "batch_id": batch_id,
                    "status": "success",
                    "bj_symbols": bj_count,
                    "run_id": run_id,
                }
            )
            return {
                "bj_rows": bj_count,
                "membership_source": "r3-identity-receipt.json",
                "run_id": run_id,
                "compact": compact,
            }
        except R3Error as exc:
            self.ledger.append(
                {
                    "stage": "C2",
                    "event": "CONTROLLER_COMPLETE",
                    "batch_id": batch_id,
                    "status": "failed",
                    "reason": str(exc),
                    "error_code": exc.code,
                }
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self.ledger.append(
                {
                    "stage": "C2",
                    "event": "CONTROLLER_COMPLETE",
                    "batch_id": batch_id,
                    "status": "failed",
                    "reason": str(exc),
                    "error_code": "UNEXPECTED",
                }
            )
            raise

        return {
            "bj_rows": bj_count,
            "membership_source": "r3-identity-receipt.json",
            "run_id": run_id,
            "compact": compact,
        }

    def _load_identity_bj_membership(self) -> list[dict[str, Any]]:
        """Complete active BJ membership from the Stage-B identity receipt."""
        receipt_path = self.meta / "r3-identity-receipt.json"
        if not receipt_path.exists():
            raise R3Error(
                "IDENTITY_RECEIPT_MISSING",
                f"Stage-B identity receipt missing: {receipt_path}",
            )
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        members = payload.get("bj_current_membership") or []
        return [
            {
                "symbol": m["symbol"],
                "name": m.get("name"),
                "exchange": m.get("exchange", "BJ"),
                "asset_type": m.get("asset_type", "stock"),
                "list_date": date.fromisoformat(str(m["list_date"]))
                if isinstance(m.get("list_date"), str)
                else m.get("list_date"),
                "delist_date": m.get("delist_date"),
                "prev_symbol": m.get("prev_symbol"),
            }
            for m in members
        ]

    def stage_calendar(self) -> dict[str, Any]:
        self.machine.enter("D_calendar")
        engine = JobEngine(self.cfg)
        engine.config._backfill = True  # type: ignore[attr-defined]
        engine.config._backfill_start = self.history_start  # type: ignore[attr-defined]
        run = self._run_single_step_terminal(["trading_calendar"])
        self.machine.complete("D_calendar", {"job": run["job"], "compact": run["compact"]})
        return {"job": run["job"], "compact": run["compact"]}

    def stage_delisted(self) -> dict[str, Any]:
        self.machine.enter("E_delisted")
        receipt = self._recover_delisted_daily()
        self.machine.complete("E_delisted", receipt)
        return receipt

    def _recover_delisted_daily(self) -> dict[str, Any]:
        """R3-safe delisted recovery; never touches backfill_delisted_bars.

        SH/SZ: prefetch controller+manifest+ledger lineage; exact retries with
        strict scope; success retries supersede prior failed batches. EXPECTED
        NO-DATA symbols get explicit terminal ledger evidence (no provider
        request). Compact is guarded by zero incomplete manifest blocking
        batches and an asserted complete service scope.
        """
        self._prepare_network_env()
        from cnequity.adapters.baostock.delisted_bars import fetch_delisted_bars

        formal = self._load_shsz_formal_map()
        catalog = load_delisted_catalog(self.cfg)
        instruments = load_curated_instruments(self.cfg)
        partition = stage_e_target_partition(
            formal,
            catalog,
            instruments,
            self.daily_as_of,
            self.history_start,
        )
        targets = partition["targets"]
        no_data = partition["no_data"]
        recover = partition["recover"]
        target_set_sha = partition["target_set_sha"]

        run_id = self._new_run("r3_delisted_daily")
        manifest = Manifest(self.cfg.manifest_path)
        writer = StagingWriter(self.cfg.staging_root)
        unresolved: list[str] = []
        recovered_spans: dict[str, tuple[date, date]] = {}
        total_rows = 0

        # EXPECTED_NO_DATA_BEFORE_WINDOW: explicit terminal evidence, no fetch
        for symbol in no_data:
            self.ledger.append(
                {
                    "stage": "E",
                    "event": "EXPECTED_NO_DATA_TERMINAL",
                    "symbol": symbol,
                    "ownership": "EXPECTED_NO_DATA_BEFORE_WINDOW",
                    "adapter": "none",
                    "status": "terminal_no_provider_request",
                    "window_start": self.history_start.isoformat(),
                    "window_end": self.daily_as_of.isoformat(),
                }
            )

        sh_sz = partition["sh_sz"]
        bj = partition["bj"]

        def _batch_id(symbol: str, attempt: int) -> str:
            return "e-baostock-" + hashlib.sha256(
                f"{symbol}|{self.history_start}|{self.daily_as_of}|a{attempt}".encode()
            ).hexdigest()[:12]

        # SH/SZ RECOVERY_REQUIRED with prefetch manifest + service ledger
        for symbol in sh_sz:
            attempt = 0
            prior_failed: list[str] = []
            done = False
            while attempt < 3 and not done:
                attempt += 1
                batch_id = _batch_id(symbol, attempt)
                manifest.start_batch(
                    run_id, batch_id, task_id="daily_bars", dataset="daily_bars",
                    symbols=[symbol], window_start=self.history_start.isoformat(),
                    window_end=self.daily_as_of.isoformat(), blocks_compaction=True,
                )
                self.ledger.append(
                    {
                        "stage": "E",
                        "event": "ATTEMPT_START",
                        "symbol": symbol,
                        "symbol_hash": sha256_bytes(symbol.encode()),
                        "attempt": attempt,
                        "window_start": self.history_start.isoformat(),
                        "window_end": self.daily_as_of.isoformat(),
                        "adapter": "baostock",
                        "batch_id": batch_id,
                        "ownership": "RECOVERY_REQUIRED",
                        "status": "running",
                    }
                )
                try:
                    frame_rows, _failed = fetch_delisted_bars(
                        [symbol], self.history_start, self.daily_as_of, config=self.cfg
                    )
                    if _failed:
                        raise RuntimeError(f"baostock failed for {symbol}: {_failed}")
                    frame = pl.DataFrame(frame_rows)
                except Exception as exc:
                    manifest.finish_batch(run_id, batch_id, "failed", error_message=str(exc))
                    prior_failed.append(batch_id)
                    self.ledger.append(
                        {
                            "stage": "E", "event": "ATTEMPT_END", "symbol": symbol,
                            "attempt": attempt, "adapter": "baostock", "batch_id": batch_id,
                            "status": "failed", "reason": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue
                if frame.is_empty():
                    manifest.finish_batch(run_id, batch_id, "failed",
                                          error_message="empty baostock response")
                    prior_failed.append(batch_id)
                    self.ledger.append(
                        {
                            "stage": "E", "event": "ATTEMPT_END", "symbol": symbol,
                            "attempt": attempt, "adapter": "baostock", "batch_id": batch_id,
                            "status": "failed", "reason": "EMPTY_RESPONSE",
                        }
                    )
                    continue
                frame = frame.with_columns(
                    pl.lit("baostock").alias("source")
                ) if "source" not in frame.columns else frame
                traded = frame.filter(pl.col("volume") > 0) if "volume" in frame.columns else frame
                if traded.is_empty():
                    # non-empty frame but ZERO positive-volume rows => not success
                    manifest.finish_batch(run_id, batch_id, "failed",
                                          error_message="ZERO_VOLUME_ONLY")
                    prior_failed.append(batch_id)
                    self.ledger.append(
                        {
                            "stage": "E", "event": "ATTEMPT_END", "symbol": symbol,
                            "attempt": attempt, "adapter": "baostock", "batch_id": batch_id,
                            "status": "failed", "reason": "ZERO_VOLUME_ONLY",
                        }
                    )
                    continue
                frame = with_provenance(
                    frame, source="baostock", data_version=data_version_for("daily_bars")
                )
                frame = frame.unique(subset=["symbol", "trade_date"], keep="last")
                writer.write_batch("daily_bars", run_id, batch_id, frame)
                manifest.finish_batch(
                    run_id, batch_id, "success",
                    rows_read=frame.height, rows_written=frame.height,
                )
                if prior_failed:
                    manifest.supersede_batches(run_id, prior_failed, superseded_by=batch_id)
                    self.ledger.append(
                        {
                            "stage": "E", "event": "ATTEMPT_SUPERSEDE", "symbol": symbol,
                            "successful_batch_id": batch_id,
                            "superseded_batch_ids": list(prior_failed),
                            "superseded_n": len(prior_failed),
                        }
                    )
                prior_failed.clear()
                recovered_spans[symbol] = (
                    traded["trade_date"].min(),
                    traded["trade_date"].max(),
                )
                total_rows += frame.height
                self.ledger.append(
                    {
                        "stage": "E", "event": "ATTEMPT_END", "symbol": symbol,
                        "attempt": attempt, "adapter": "baostock", "batch_id": batch_id,
                        "status": "success", "rows": frame.height,
                        "span": (
                            f"{traded['trade_date'].min()}|{traded['trade_date'].max()}"
                            if not traded.is_empty()
                            else None
                        ),
                        "symbol_hash": sha256_bytes(symbol.encode()),
                    }
                )
                done = True
            if not done:
                unresolved.append(symbol)

        # BJ historical delisted targets: only resolvable when authority is PROVEN
        bj_unknown_carried: list[str] = []
        if bj:
            if BJ_HISTORICAL_AUTHORITY_VERDICT == "PROVEN":
                for symbol in bj:
                    attempt = 0
                    state = None
                    reason = None
                    frame = None
                    while attempt < 3:
                        attempt += 1
                        result = em_daily_tristate(
                            symbol, self.history_start, self.daily_as_of, config=self.cfg
                        )
                        state, reason, frame = result["state"], result["reason"], result["frame"]
                        if state == "EXISTS":
                            break
                    if state == "EXISTS" and frame is not None and not frame.is_empty():
                        frame = with_provenance(
                            frame, source="eastmoney",
                            data_version=data_version_for("daily_bars"),
                        )
                        batch_id = "e-em-" + hashlib.sha256(
                            f"{symbol}|{self.history_start}|{self.daily_as_of}".encode()
                        ).hexdigest()[:12]
                        manifest.start_batch(
                            run_id, batch_id, task_id="daily_bars", dataset="daily_bars",
                            symbols=[symbol], window_start=self.history_start.isoformat(),
                            window_end=self.daily_as_of.isoformat(), blocks_compaction=True,
                        )
                        writer.write_batch("daily_bars", run_id, batch_id, frame)
                        manifest.finish_batch(
                            run_id, batch_id, "success",
                            rows_read=frame.height, rows_written=frame.height,
                        )
                        total_rows += frame.height
                        traded = frame.filter(pl.col("volume") > 0) if "volume" in frame.columns else frame
                        if not traded.is_empty():
                            recovered_spans[symbol] = (
                                traded["trade_date"].min(), traded["trade_date"].max()
                            )
                    else:
                        unresolved.append(symbol)
            else:
                bj_unknown_carried = list(bj)
                self.ledger.append(
                    {
                        "stage": "E",
                        "bj_historical_authority": BJ_HISTORICAL_AUTHORITY_VERDICT,
                        "bj_historical_delisted": HISTORICAL_DELISTED_BJ_LABEL,
                        "targets": bj,
                    }
                )

        if unresolved:
            raise R3Error(
                "E_UNRESOLVED",
                f"{len(unresolved)} delisted targets unresolved after retries: {unresolved[:20]}",
            )

        # compact guard: zero incomplete manifest blocking batches for this run
        incomplete = manifest.incomplete_batch_counts_by_dataset(run_id) or {}
        blocking = {ds: n for ds, n in incomplete.items() if n > 0}
        if blocking:
            raise R3Error(
                "E_INCOMPLETE_BEFORE_COMPACT",
                f"incomplete manifest batches before compact: {blocking}",
            )

        compact = self._compact(run_id)
        bj_authority = BJ_HISTORICAL_AUTHORITY_VERDICT
        exit_verdict = v072_exit_verdict(
            bj_authority,
            len(bj_unknown_carried) if bj_unknown_carried else None,
        )
        receipt = {
            "run_id": run_id,
            "target_set_sha": target_set_sha,
            "targets": len(targets),
            "recover": len(recover),
            "expected_no_data_before_window": len(no_data),
            "recovered": len(recovered_spans),
            "rows_written": total_rows,
            "unresolved": len(unresolved),
            "sina_catalog_role": "CROSSCHECK_ONLY",
            "sina_catalog_symbols_n": len(catalog),
            "bj_historical_targets": len(bj),
            "bj_historical_authority": bj_authority,
            "bj_historical_delisted": HISTORICAL_DELISTED_BJ_LABEL,
            "bj_historical_unresolved_n": (
                len(bj_unknown_carried) if bj_unknown_carried else None
            ),
            "exit": exit_verdict,
            "compact": compact,
        }
        atomic_write_json(self.meta / "r3-delisted-recovery.json", receipt)
        return receipt

    def _load_shsz_formal_map(self) -> dict[str, date]:
        """Stage-E formal membership authority = Stage-B persisted formal map."""
        receipt_path = self.meta / "r3-identity-receipt.json"
        if not receipt_path.exists():
            raise R3Error(
                "FORMAL_IDENTITY_RECEIPT_MISSING",
                f"Stage-B identity receipt missing: {receipt_path}",
            )
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        raw = payload.get("shsz_formal_delisted") or {}
        return {
            symbol: date.fromisoformat(value) for symbol, value in raw.items()
        }

    def stage_daily(self) -> dict[str, Any]:
        self._prepare_network_env()
        self.machine.enter("F_daily")
        receipt = self._fetch_daily_bars_per_route()
        self.machine.complete("F_daily", receipt)
        return receipt

    def _effective_active(self) -> pl.DataFrame:
        instruments = load_curated_instruments(self.cfg)
        if instruments is None or instruments.is_empty():
            raise R3Error("NO_INSTRUMENTS", "no instruments to plan daily coverage")
        df = instruments.filter(pl.col("asset_type").is_in(["stock", "cdr"]))
        df = df.with_columns(
            pl.when(pl.col("delist_date").is_null() | (pl.col("delist_date") >= self.daily_as_of))
            .then(pl.lit(True))
            .otherwise(pl.lit(False))
            .alias("_effective_active")
        )
        df = df.filter(pl.col("_effective_active")).drop("_effective_active")
        df = df.with_columns(
            pl.col("symbol").str.split(".").list.get(-1).alias("_exchange")
        )
        return df

    def _fetch_daily_bars_per_route(self) -> dict[str, Any]:
        spans_shsz: dict[str, tuple[date, date]] = {}
        spans_bj: dict[str, tuple[date, date]] = {}
        expect_no_data: list[str] = []
        for row in self._effective_active().iter_rows(named=True):
            span = effective_span(row["list_date"], row["delist_date"])
            if span is None:
                expect_no_data.append(row["symbol"])
                continue
            if row["_exchange"] in ("SH", "SZ"):
                spans_shsz[row["symbol"]] = span
            else:
                spans_bj[row["symbol"]] = span

        run_id = self._new_run("r3_daily_bars")
        f1 = self._tdx_route(run_id, spans_shsz)
        f2 = self._em_primary_route(run_id, spans_bj)
        compact = self._compact(run_id)
        return {
            "run_id": run_id,
            "sh_sz_symbols": len(spans_shsz),
            "bj_symbols": len(spans_bj),
            "expected_no_data": len(expect_no_data),
            "f1_tdx": f1,
            "f2_em_primary": f2,
            "compact": compact,
        }

    def _tdx_route(self, run_id: str, spans: dict[str, tuple[date, date]]) -> dict[str, Any]:
        """Stage F1 (V07.3): SH/SZ via pinned fetch_daily_bars_parallel.

        Pinned worker-pool behavior is unchanged. The controller records
        service-ledger ATTEMPT_START/END per attempt (batch specs, symbols hash,
        window, adapter=tdx, status), retries ONLY the exact failed scope with
        increasing attempt, requires strict decrease of the failed-symbol set,
        and raises F1_FAILED_AFTER (so compact is never reached) when any
        symbol remains failed.
        """
        symbols = sorted(spans)
        if not symbols:
            return {"symbols": 0}
        batch_size = int(getattr(self.cfg, "batch_size", 50) or 50)
        chunks: list[tuple[str, list[str], date, date]] = []
        global_index = 0
        for identical_span, span_symbols in group_by_span(spans):
            # batch built from an identical effective span only; never the
            # first symbol's span generalized across neighbours
            for index in range(0, len(span_symbols), batch_size):
                chunk_symbols = span_symbols[index : index + batch_size]
                start, end = identical_span
                chunks.append(
                    (
                        _symbol_batch_id(start, end, global_index),
                        chunk_symbols,
                        start,
                        end,
                    )
                )
                global_index += 1
        self.cfg.failover_enabled = False

        def _specs_symbols(specs):
            out = [s for _bid, syms, _s, _e in specs for s in syms]
            return out

        attempt = 0
        prev_failed: set[str] = set()
        failed: set[str] = set()
        rows_written_last = 0
        while True:
            attempt += 1
            specs = chunks if attempt == 1 else [
                ch for ch in chunks if any(s in failed for s in ch[1])
            ]
            if not specs:
                break
            scoped = _specs_symbols(specs)
            specs_windows = [
                {
                    "batch_id": b,
                    "window_start": w0.isoformat(),
                    "window_end": w1.isoformat(),
                    "symbols": syms,
                }
                for b, syms, w0, w1 in specs
            ]
            self.ledger.append(
                {
                    "stage": "F1",
                    "event": "ATTEMPT_START",
                    "attempt": attempt,
                    "batch_ids": [b for b, _s, _w0, _w1 in specs],
                    "specs_windows": specs_windows,
                    "symbols": scoped,
                    "symbol_hash": sha256_bytes(
                        json.dumps(sorted(scoped), separators=(",", ":")).encode()
                    ),
                    "window_start": self.history_start.isoformat(),
                    "window_end": self.daily_as_of.isoformat(),
                    "adapter": "tdx",
                    "status": "running",
                }
            )
            result = fetch_daily_bars_parallel(
                self.cfg, [], self.history_start, self.daily_as_of, run_id,
                batch_specs=specs,
            )
            failed = set(result.get("failed_symbols") or [])
            rows_written_last = int(result.get("rows_written") or 0)
            self.ledger.append(
                {
                    "stage": "F1",
                    "event": "ATTEMPT_END",
                    "attempt": attempt,
                    "adapter": "tdx",
                    "status": "success" if not failed else "failed",
                    "failed_symbols": sorted(failed)[:200],
                    "failed_after": len(failed),
                    "rows_written": rows_written_last,
                }
            )
            if not failed:
                break
            if attempt > 1 and len(failed) >= len(prev_failed):
                raise R3Error(
                    "F1_STRICT_DECREASE",
                    f"TDX failed-symbol set did not strictly decrease "
                    f"(attempt {attempt}: {len(prev_failed)} -> {len(failed)}); fail-closed",
                )
            prev_failed = failed
            if attempt >= 3:
                break

        if failed:
            raise R3Error(
                "F1_FAILED_AFTER",
                f"TDX symbols still failed after retries: {sorted(failed)[:20]} "
                "(compact is not reached)",
            )
        return {
            "symbols": len(symbols),
            "route": "tdx",
            "attempts": attempt,
            "failed_after": 0,
            "rows_written_last": rows_written_last,
        }

    def _em_primary_route(self, run_id: str, spans: dict[str, tuple[date, date]]) -> dict[str, Any]:
        """Stage F2 (V07.2): EastMoney primary for non-TDX (BJ) daily bars.

        Sina is never called. Each attempt: a deterministic unique batch id + a
        blocking manifest controller batch are created BEFORE the network fetch,
        and a service-ledger attempt record is written; AFTER the attempt,
        success/failure state, attempt, symbol, exact window, adapter, rows,
        status, and lineage are recorded.

        EXISTS requires at least one valid row inside the requested effective
        span; an out-of-span-only frame is NOT success. After exact retries
        (<=3), a symbol with no valid in-span bars becomes UNEXPLAINED_MISSING
        (coverage classifier) and fails the gate.
        """
        if not spans:
            return {"symbols": 0}
        manifest = Manifest(self.cfg.manifest_path)
        writer = StagingWriter(self.cfg.staging_root)
        groups = group_by_span(spans)
        outcomes: list[dict[str, Any]] = []
        unexplained: list[str] = []

        for _group_index, (span, symbols) in enumerate(groups):
            start, end = span
            for symbol in symbols:
                attempt = 0
                state: str | None = None
                reason: str | None = None
                out_rows = 0
                batch_id: str | None = None
                prior_failed: list[str] = []
                while attempt < 3:
                    attempt += 1
                    batch_id = (
                        "em-"
                        + hashlib.sha256(
                            f"{symbol}|{start.isoformat()}|{end.isoformat()}|a{attempt}".encode()
                        ).hexdigest()[:12]
                    )
                    # BEFORE network fetch: blocking manifest batch + ledger
                    manifest.start_batch(
                        run_id, batch_id, task_id="daily_bars", dataset="daily_bars",
                        symbols=[symbol], window_start=start.isoformat(),
                        window_end=end.isoformat(), blocks_compaction=True,
                    )
                    self.ledger.append(
                        {
                            "stage": "F2",
                            "event": "ATTEMPT_START",
                            "attempt": attempt,
                            "symbol": symbol,
                            "window_start": start.isoformat(),
                            "window_end": end.isoformat(),
                            "adapter": "eastmoney",
                            "batch_id": batch_id,
                            "status": "running",
                        }
                    )
                    try:
                        result = em_daily_tristate(symbol, start, end, config=self.cfg)
                        state = result["state"]
                        reason = result["reason"]
                        frame = result["frame"]
                    except Exception as exc:
                        state = "SOURCE_ERROR"
                        reason = f"wrapper_exc:{type(exc).__name__}"
                        frame = None

                    if state == "EXISTS" and frame is not None and not frame.is_empty():
                        out = frame.filter(
                            (pl.col("trade_date") >= start) & (pl.col("trade_date") <= end)
                        )
                        if out.is_empty():
                            # out-of-span-only frame => NOT success
                            state = "SOURCE_ERROR"
                            reason = "OUT_OF_SPAN_EMPTY"
                            out_rows = 0
                        else:
                            pos = out.filter(pl.col("volume") > 0)
                            if pos.is_empty():
                                # zero-volume-only => NOT a success completion
                                state = "SOURCE_ERROR"
                                reason = "ZERO_VOLUME_ONLY"
                                out_rows = 0
                            else:
                                out = out.unique(subset=["symbol", "trade_date"], keep="last")
                                out = with_provenance(
                                    out,
                                    source="eastmoney",
                                    data_version=data_version_for("daily_bars"),
                                )
                                writer.write_batch("daily_bars", run_id, batch_id, out)
                                manifest.finish_batch(
                                    run_id, batch_id, "success",
                                    rows_read=out.height, rows_written=out.height,
                                )
                                out_rows = out.height
                                if prior_failed:
                                    manifest.supersede_batches(
                                        run_id,
                                        prior_failed,
                                        superseded_by=batch_id,
                                    )
                                    self.ledger.append(
                                        {
                                            "stage": "F2",
                                            "event": "ATTEMPT_SUPERSEDE",
                                            "symbol": symbol,
                                            "successful_batch_id": batch_id,
                                            "superseded_batch_ids": list(prior_failed),
                                            "superseded_n": len(prior_failed),
                                        }
                                    )
                                    prior_failed.clear()
                                self.ledger.append(
                                    {
                                        "stage": "F2",
                                        "event": "ATTEMPT_END",
                                        "attempt": attempt,
                                        "symbol": symbol,
                                        "window_start": start.isoformat(),
                                        "window_end": end.isoformat(),
                                        "adapter": "eastmoney",
                                        "batch_id": batch_id,
                                        "state": "EXISTS",
                                        "reason": reason,
                                        "rows": out_rows,
                                        "status": "success",
                                    }
                                )
                                break

                    manifest.finish_batch(
                        run_id, batch_id, "failed",
                        error_message=f"{state}:{reason}",
                    )
                    if batch_id is not None:
                        prior_failed.append(batch_id)
                    self.ledger.append(
                        {
                            "stage": "F2",
                            "event": "ATTEMPT_END",
                            "attempt": attempt,
                            "symbol": symbol,
                            "window_start": start.isoformat(),
                            "window_end": end.isoformat(),
                            "adapter": "eastmoney",
                            "batch_id": batch_id,
                            "state": state,
                            "reason": reason,
                            "rows": 0,
                            "status": "failed",
                        }
                    )

                if out_rows > 0:
                    outcomes.append(
                        {"symbol": symbol, "state": "EXISTS", "attempt": attempt, "rows": out_rows}
                    )
                else:
                    unexplained.append(symbol)
                    outcomes.append(
                        {"symbol": symbol, "state": state or "SOURCE_ERROR", "reason": reason}
                    )

        if unexplained:
            raise R3Error(
                "UNEXPLAINED_MISSING",
                f"{len(unexplained)} BJ symbols have no valid in-span bars after "
                f"retries: {unexplained[:20]}",
            )
        return {
            "symbols": len(spans),
            "route": "eastmoney_primary",
            "groups": len(groups),
            "outcomes": outcomes,
            "unexplained_after": 0,
        }

    def stage_coverage(self) -> dict[str, Any]:
        self.machine.enter("G_coverage")
        report = delisted_coverage_report(
            self.cfg, self.history_start, self.daily_as_of, sample=20
        )
        if not report.get("verified"):
            raise R3Error("DELISTED_COVERAGE_UNVERIFIED", str(report))
        atomic_write_json(self.meta / "r3-delisted-coverage.json", report)
        self.machine.complete("G_coverage", {"verified": True, "report": report})
        return report


# --- target root snapshot --------------------------------------------------


def _stable_treeline(root: Path, exclude: str | None) -> list[str]:
    """Metadata-stable path/file-hash lines, excluding the controller subtree."""
    excluded_prefix = Path(exclude) if exclude else None
    lines: list[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        rel = Path(dirpath).relative_to(root)
        if rel == Path("."):
            rel_key = "."
        else:
            rel_key = rel.as_posix()
        if excluded_prefix is not None and rel_key == excluded_prefix.as_posix():
            dirnames[:] = []
            continue
        if excluded_prefix is not None and rel_key.startswith(
            excluded_prefix.as_posix() + "/"
        ):
            dirnames[:] = []
            continue
        seen += 1
        lines.append(f"D {rel_key} " + " ".join(sorted(dirnames)))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink():
                raise R3Error("SNAPSHOT_SYMLINK", f"symlink inside target root: {path}")
            lines.append(f"F {Path(rel_key, name).as_posix()} {sha256_file(path)}")
    return lines


def target_tree_snapshot(root: Path, exclude: str | None = None) -> dict[str, Any]:
    """Stable content snapshot (paths + file hashes + directory entries)."""
    root = root.resolve(strict=True)
    lines = _stable_treeline(root, exclude)
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {"entries": len(lines), "digest": digest, "excluded": exclude, "lines": lines}


R2_LAYOUT_DIRECTORIES = frozenset(
    {
        ".",
        "backups",
        "curated",
        "derived",
        "duckdb",
        "meta",
        "meta/adj_factors_cache",
        "meta/on_demand",
        "meta/quality",
        "meta/quality/findings",
        "meta/quality/source_diffs",
        "meta/seeds",
        "meta/source_snapshots",
        "meta/state",
        "raw",
        "staging",
    }
)
R2_REQUIRED_FILES = frozenset({"meta/manifest.db", "duckdb/cnequity.duckdb"})
R2_EMPTY_DATA_DIRS = frozenset(
    {
        "backups",
        "curated",
        "derived",
        "meta/adj_factors_cache",
        "meta/on_demand",
        "meta/quality/findings",
        "meta/quality/source_diffs",
        "meta/seeds",
        "meta/source_snapshots",
        "meta/state",
        "raw",
        "staging",
    }
)
# R2 audited digest (metadata-inclusive) retained as a diagnostic reference;
# the structural zero-data layout check governs the preflight gate.
R2_ZERO_DATA_REFERENCE_SHA = R2_ZERO_DATA_TREE_SHA


def zero_data_layout_errors(root: Path) -> list[str]:
    """Return violations of the R2 zero-data layout, allowing meta/asl/r3."""
    errors: list[str] = []
    actual_dirs = {
        Path(path).relative_to(root).as_posix() if Path(path) != root else "."
        for path, _dn, _fn in os.walk(root)
    }
    missing = sorted(R2_LAYOUT_DIRECTORIES - actual_dirs)
    if missing:
        errors.append(f"missing directories: {missing}")
    for path, dirnames, filenames in os.walk(root):
        rel = Path(path).relative_to(root)
        rel_key = rel.as_posix() if rel != Path(".") else "."
        if rel_key.startswith("meta/asl/"):
            dirnames[:] = []
            continue
        for name in filenames:
            rp = (rel / name).as_posix()
            if rp in R2_REQUIRED_FILES:
                continue
            errors.append(f"unexpected file: {rp}")
    for empty_dir in R2_EMPTY_DATA_DIRS:
        target = root / empty_dir
        if target.is_dir() and any(target.rglob("*")):
            errors.append(f"zero-data directory not empty: {empty_dir}")
    return errors
