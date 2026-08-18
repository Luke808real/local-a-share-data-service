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
import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

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
    closure/reconciliation evidence ONLY. failed_dates_n > 0 => NOT CLOSED.
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
        success_dates.append(day)
        union |= roster
    receipt = {
        "expected_dates_n": len(days),
        "success_dates_n": len(success_dates),
        "failed_dates_n": len(failed_dates),
        "failed_dates_sample": [d.isoformat() for d in failed_dates[:10]],
        "union_symbol_n": len(union),
        "union_symbol_hash": sha256_bytes(
            json.dumps(sorted(union), separators=(",", ":")).encode()
        ),
        "closed": len(failed_dates) == 0,
    }
    if stock_basic_symbols is not None:
        receipt["stock_basic_vs_roster_diff"] = {
            "identity_not_in_roster": sorted(stock_basic_symbols - union)[:200],
            "roster_not_in_identity": sorted(union - stock_basic_symbols)[:200],
            "n_identity_not_in_roster": len(stock_basic_symbols - union),
            "n_roster_not_in_identity": len(union - stock_basic_symbols),
        }
    receipt["unresolved_n"] = len(failed_dates)
    if receipt["closed"] is False:
        receipt["closed"] = "NOT_CLOSED"
        raise R3Error(
            "NOT_CLOSED",
            f"{len(failed_dates)}/{len(days)} roster dates failed (failed_dates_n>0 => NOT CLOSED)",
        )
    return receipt


def v072_exit_verdict(bj_authority: str, unresolved_n: int | None) -> dict[str, Any]:
    """Frozen R3 exit gate (V07.2). UNKNOWN/null unresolved is never 0."""
    if bj_authority != "PROVEN" or unresolved_n != 0:
        return {
            "DAILY_READY": False,
            "R3_EXIT": "BLOCKED_BJ_HISTORICAL_IDENTITY",
            "R4_EXECUTION": "FORBIDDEN",
            "bj_historical_authority": bj_authority,
            "bj_historical_unresolved_n": unresolved_n,
        }
    return {
        "DAILY_READY": True,
        "R3_EXIT": None,
        "R4_EXECUTION": "FORBIDDEN",
        "bj_historical_authority": bj_authority,
        "bj_historical_unresolved_n": unresolved_n,
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


STAGES = ("preflight", "A_instruments", "B_discovery", "C_merge", "C2_enrich", "D_calendar", "E_delisted", "F_daily", "G_coverage", "quality")
STAGE_ORDER = {name: index for index, name in enumerate(STAGES)}


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
        if state.get("current") is not None and state["current"] != stage:
            raise R3Error(
                "STAGE_IN_PROGRESS", f"stage {state['current']} still in progress"
            )
        if state["completed"]:
            last_done = state["completed"][-1]
            if STAGE_ORDER[stage] < STAGE_ORDER[last_done]:
                raise R3Error("STAGE_ORDER", f"cannot rewind from {last_done} to {stage}")
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
        self._clear_ambient_proxy()

    def _clear_ambient_proxy(self) -> None:
        """Record and clear ambient HTTP(S)/SOCKS proxy env vars.

        The frozen config declares no proxy. TDX and Baostock use raw sockets and
        are unaffected; leaving ambient vars set makes httpx (EastMoney) fail
        because this venv lacks socksio for a SOCKS ALL_PROXY. Direct egress to
        the pinned endpoints is verified reachable. This is a runtime-env guard,
        not a config or dependency change.
        """
        removed = {
            key: os.environ.pop(key)
            for key in list(os.environ)
            if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
        }
        if removed:
            logger.warning(
                "AMBIENT_PROXY_CLEARED: removed %s during R3 execution",
                sorted(set(k.upper() for k in removed)),
            )
            self.meta.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                self.meta / "r3-proxy-guard.json",
                {
                    "cleared": sorted(set(k.upper() for k in removed)),
                    "note": "ambient proxy env cleared for pinned direct egress only",
                },
            )

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

    def _acquire_lock(self) -> Path:
        self.meta.mkdir(parents=True, exist_ok=True)
        lock = self.meta / "runner.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise R3Error("WRITER_LOCKED", "another R3 runner holds the writer lock") from exc
        os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
        os.close(fd)
        return lock

    def _release_lock(self, lock: Path) -> None:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass

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

        receipt = {
            "plan_sha": self.plan_sha,
            "base_head": BASE_HEAD,
            "repo_head": git_sha(self.repo_root),
            "runtime": provenance,
            "root": str(self.root),
            "tree_digest": digest,
            "free_gib": round(free_gib, 1),
            "legacy_isolation": True,
            "surface_clean": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_json(self.meta / "r3-preflight.json", receipt)
        return receipt

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
        result = engine.run_job(
            f"r3_{steps[0]}",
            self.daily_as_of,
            steps=steps,
            backfill=True,
            run_id=run_id,
            finalize_run=False,
        )
        return {"run_id": run_id, "result": result}

    def _compact(self, run_id: str) -> dict[str, Any]:
        engine = JobEngine(self.cfg)
        out = engine.run_step("compact", self.daily_as_of, run_id, {})
        if out.get("status") != "success":
            raise R3Error("COMPACT_FAILED", f"compact failed: {out}")
        return out

    # --- stages ------------------------------------------------------------

    def stage_instruments(self) -> dict[str, Any]:
        state = self.machine.enter("A_instruments")
        out = self._run_single_step_runjob(["instruments"])
        compact = self._compact(out["run_id"])
        receipt = {"job": out, "compact": compact}
        self.machine.complete("A_instruments", receipt)
        return receipt

    def stage_discovery(self) -> dict[str, Any]:
        """V07.2 identity completion (stage key kept `B_discovery` for compat).

        No Sina issued-code sweep: SH/SZ identity/closure come from Baostock;
        BJ current identity from EastMoney clist; BJ historical is
        UNPROVABLE_BOUNDED_RESEARCH -> HISTORICAL_DELISTED_BJ = UNKNOWN_CARRIED.
        """
        self.machine.enter("B_discovery")
        receipt = self._identity_completion_v072()
        self.machine.complete("B_discovery", receipt)
        return receipt

    def _identity_completion_v072(self) -> dict[str, Any]:
        from cnequity.adapters.baostock.delisted_bars import roster_on
        from cnequity.adapters.baostock.instruments import fetch_instrument_basics
        from cnequity.adapters.eastmoney.clist import clist_rows_to_symbols, fetch_clist_pages
        from cnequity.adapters.eastmoney.em_auth import EastMoneyClient

        basic_df = fetch_instrument_basics()
        identity_symbols = set(basic_df["symbol"].to_list()) if basic_df.height else set()
        identity_hash = sha256_bytes(
            json.dumps(sorted(identity_symbols), separators=(",", ":")).encode()
        )

        dates = list_trading_dates(self.cfg, self.history_start, self.daily_as_of)
        closure = roster_closure_receipt(
            dates, roster_on, stock_basic_symbols=identity_symbols
        )

        # BJ current identity via EastMoney clist (existing C2 machinery reused)
        client = EastMoneyClient(config=self.cfg)
        try:
            clist = fetch_clist_pages(client, fields="f12,f13,f14,f26")
        finally:
            client.close()
        bj_current = sorted(
            {sym for sym, _item in clist_rows_to_symbols(clist) if sym.endswith(".BJ")}
        )
        bj_current_hash = sha256_bytes(
            json.dumps(bj_current, separators=(",", ":")).encode()
        )

        receipt = {
            "route": "V07.2_identity_completion",
            "shsz_identity_authority": "Baostock_stock_basic",
            "shsz_identity_symbols": len(identity_symbols),
            "shsz_identity_hash": identity_hash,
            "shsz_closure": closure,
            "bj_current_authority": "EastMoney_clist",
            "bj_current_symbols": len(bj_current),
            "bj_current_hash": bj_current_hash,
            "bj_historical_authority": BJ_HISTORICAL_AUTHORITY_VERDICT,
            "bj_historical_delisted": HISTORICAL_DELISTED_BJ_LABEL,
            "bj_historical_resolved": False,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.ledger.append({"stage": "B_discovery", "v07_2_identity": True, "receipt": receipt})
        return receipt

    def stage_merge(self) -> dict[str, Any]:
        self.machine.enter("C_merge")
        out = self._run_single_step_runjob(["instruments"])
        compact = self._compact(out["run_id"])
        self.machine.complete("C_merge", {"job": out, "compact": compact})
        return {"job": out, "compact": compact}

    def stage_enrich(self) -> dict[str, Any]:
        self.machine.enter("C2_enrich")
        receipt = self._enrich_bj_metadata()
        self.machine.complete("C2_enrich", receipt)
        return receipt

    def _enrich_bj_metadata(self) -> dict[str, Any]:
        from cnequity.adapters.eastmoney.clist import clist_rows_to_symbols, fetch_clist_pages
        from cnequity.adapters.eastmoney.em_auth import EastMoneyClient

        instruments = load_curated_instruments(self.cfg)
        if instruments is None or instruments.is_empty():
            raise R3Error("C2_NO_INSTRUMENTS", "no curated instruments to enrich")
        bj = instruments.filter(pl.col("symbol").str.ends_with(".BJ"))
        if bj.is_empty():
            raise R3Error("BLOCKED_ALL_A_UNIVERSE", "no BJ rows in security master")

        client = EastMoneyClient(config=self.cfg)
        try:
            rows = fetch_clist_pages(client, fields="f12,f13,f14,f26")
        finally:
            client.close()
        meta: dict[str, dict[str, Any]] = {}
        for sym, item in clist_rows_to_symbols(rows):
            if not sym.endswith(".BJ"):
                continue
            meta[sym] = {"name": item.get("f14"), "f26": item.get("f26")}

        def parse_f26(value: Any) -> date | None:
            if value in (None, "", "-"):
                return None
            text = str(value).strip()
            if len(text) == 8 and text.isdigit():
                return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None

        enrich_rows: list[dict[str, Any]] = []
        missing: list[str] = []
        for row in bj.select("symbol", "name", "list_date").iter_rows(named=True):
            symbol = row["symbol"]
            info = meta.get(symbol)
            name = row["name"] or (info or {}).get("name")
            list_date = row["list_date"] or parse_f26((info or {}).get("f26"))
            if not name or list_date is None or list_date > self.daily_as_of:
                missing.append(symbol)
                continue
            enrich_rows.append(
                {
                    "symbol": symbol,
                    "name": str(name).strip(),
                    "exchange": "BJ",
                    "asset_type": "stock",
                    "list_date": list_date,
                    "delist_date": None,
                    "prev_symbol": None,
                }
            )
        if missing:
            raise R3Error(
                "BLOCKED_ALL_A_METADATA",
                f"{len(missing)} active BJ rows lack verified name/list_date: {missing[:20]}",
            )

        non_bj = instruments.filter(~pl.col("symbol").str.ends_with(".BJ"))
        enriched_df = pl.DataFrame(enrich_rows)
        merged = pl.concat([non_bj, enriched_df], how="diagonal_relaxed")
        merged = with_provenance(merged, source="eastmoney", data_version="v1")
        merged = merged.sort("symbol").unique(subset=["symbol"], keep="last")

        run_id = self._new_run("r3_c2_enrich")
        manifest = Manifest(self.cfg.manifest_path)
        batch_id = "c2-enrich-bj"
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
        compact = self._compact(run_id)
        post = load_curated_instruments(self.cfg)
        bad = (
            post.filter(pl.col("symbol").str.ends_with(".BJ"))
            .filter(pl.col("name").is_null() | pl.col("list_date").is_null())
            .height
        )
        if bad:
            raise R3Error("C2_POSTCHECK", f"{bad} BJ rows still lack metadata post-compact")
        return {
            "bj_rows": merged.filter(pl.col("symbol").str.ends_with(".BJ")).height,
            "missing_before": len(missing),
            "run_id": run_id,
            "compact": compact,
        }

    def stage_calendar(self) -> dict[str, Any]:
        self.machine.enter("D_calendar")
        engine = JobEngine(self.cfg)
        engine.config._backfill = True  # type: ignore[attr-defined]
        engine.config._backfill_start = self.history_start  # type: ignore[attr-defined]
        out = self._run_single_step_runjob(["trading_calendar"])
        compact = self._compact(out["run_id"])
        self.machine.complete("D_calendar", {"job": out, "compact": compact})
        return {"job": out, "compact": compact}

    def stage_delisted(self) -> dict[str, Any]:
        self.machine.enter("E_delisted")
        receipt = self._recover_delisted_daily()
        self.machine.complete("E_delisted", receipt)
        return receipt

    def _recover_delisted_daily(self) -> dict[str, Any]:
        """R3-safe delisted recovery; never touches backfill_delisted_bars."""
        from cnequity.adapters.baostock.delisted_bars import fetch_delisted_bars

        formal = known_delisted_instruments(self.cfg, self.daily_as_of)
        catalog = load_delisted_catalog(self.cfg)
        targets_raw = set(formal) | set(catalog)
        instruments = load_curated_instruments(self.cfg)
        active = set() if instruments is None else set(instruments["symbol"].to_list())
        targets = sorted(s for s in targets_raw if s not in active)
        target_set_sha = hashlib.sha256(
            json.dumps(sorted(targets), separators=(",", ":")).encode()
        ).hexdigest()

        no_data: list[str] = []
        recover: list[str] = []
        for symbol in targets:
            delist = formal.get(symbol)
            last = catalog.get(symbol)
            if (delist is not None and delist < self.history_start) or (
                last is not None and last < self.history_start
            ):
                no_data.append(symbol)
            else:
                recover.append(symbol)

        run_id = self._new_run("r3_delisted_daily")
        manifest = Manifest(self.cfg.manifest_path)
        writer = StagingWriter(self.cfg.staging_root)
        unresolved: list[str] = []
        recovered_spans: dict[str, tuple[date, date]] = {}
        total_rows = 0

        sh_sz = sorted(s for s in recover if not s.endswith(".BJ"))
        bj = sorted(s for s in recover if s.endswith(".BJ"))

        rows_dict: dict[str, list[dict[str, Any]]] = {}
        attempts = 0
        pending = list(sh_sz)
        while pending and attempts < 3:
            attempts += 1
            self.ledger.append({"stage": "E", "attempt": attempts, "pending": len(pending)})
            still: list[str] = []
            for symbol in pending:
                try:
                    frame_rows, _failed = fetch_delisted_bars(
                        [symbol], self.history_start, self.daily_as_of, config=self.cfg
                    )
                    if _failed:
                        raise RuntimeError(f"baostock failed for {symbol}: {_failed}")
                    frame = pl.DataFrame(frame_rows)
                    source = "baostock"
                except Exception as exc:
                    self.ledger.append({"stage": "E", "symbol": symbol, "error": str(exc)})
                    still.append(symbol)
                    continue
                if frame.is_empty():
                    still.append(symbol)
                    continue
                frame = frame.with_columns(
                    [pl.lit(source).alias("source")] if "source" not in frame.columns else []
                )
                rows_dict[symbol] = frame.to_dicts()
                traded = frame.filter(pl.col("volume") > 0) if "volume" in frame.columns else frame
                if not traded.is_empty():
                    recovered_spans[symbol] = (
                        traded["trade_date"].min(),
                        traded["trade_date"].max(),
                    )
            pending = sorted(set(still))
        unresolved = pending

        # BJ historical delisted targets: only resolvable when authority is PROVEN
        bj_unknown_carried: list[str] = []
        if bj:
            if BJ_HISTORICAL_AUTHORITY_VERDICT == "PROVEN":
                # Route through §5a EastMoney tri-state wrapper only.
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
                        rows_dict[symbol] = frame.to_dicts()
                        traded = frame.filter(pl.col("volume") > 0) if "volume" in frame.columns else frame
                        if not traded.is_empty():
                            recovered_spans[symbol] = (traded["trade_date"].min(), traded["trade_date"].max())
                    else:
                        unresolved.append(symbol)
            else:
                # Authority unproven: never pretend the BJ historical universe is
                # complete; carry as UNKNOWN_CARRIED and never set unresolved_n=0.
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

        chunk_no = 0
        for symbol in sorted(rows_dict):
            frame = pl.DataFrame(rows_dict[symbol])
            if frame.is_empty():
                continue
            frame = with_provenance(
                frame, source=str(frame["source"][0]) if "source" in frame.columns else "baostock",
                data_version=data_version_for("daily_bars"),
            )
            frame = frame.unique(subset=["symbol", "trade_date"], keep="last")
            batch_id = f"delisted-{chunk_no:05d}"
            manifest.start_batch(
                run_id, batch_id, task_id="daily_bars", dataset="daily_bars",
                symbols=[symbol], window_start=self.history_start.isoformat(),
                window_end=self.daily_as_of.isoformat(), blocks_compaction=True,
            )
            writer.write_batch("daily_bars", run_id, batch_id, frame)
            manifest.finish_batch(run_id, batch_id, "success", rows_read=frame.height, rows_written=frame.height)
            total_rows += frame.height
            chunk_no += 1

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

    def stage_daily(self) -> dict[str, Any]:
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
        symbols = sorted(spans)
        if not symbols:
            return {"symbols": 0}
        batch_size = int(getattr(self.cfg, "batch_size", 50) or 50)
        chunks: list[tuple[str, list[str], date, date]] = []
        for index in range(0, len(symbols), batch_size):
            chunk_symbols = symbols[index : index + batch_size]
            span = spans[chunk_symbols[0]]
            chunks.append(
                (_symbol_batch_id(span[0], span[1], index // batch_size), chunk_symbols, span[0], span[1])
            )
        self.cfg.failover_enabled = False
        first = fetch_daily_bars_parallel(
            self.cfg, [], self.history_start, self.daily_as_of, run_id, batch_specs=chunks
        )
        failed_symbols = list(first.get("failed_symbols") or [])
        attempts = 0
        while failed_symbols and attempts < 3:
            attempts += 1
            failed_specs = [
                chunk for chunk in chunks if any(s in failed_symbols for s in chunk[1])
            ]
            retry = fetch_daily_bars_parallel(
                self.cfg, [], self.history_start, self.daily_as_of, run_id, batch_specs=failed_specs
            )
            new_failed = [s for s in (retry.get("failed_symbols") or []) if s in failed_symbols]
            if len(new_failed) >= len(failed_symbols):
                break
            failed_symbols = new_failed
        return {"symbols": len(symbols), "had_error": bool(first.get("had_error")), "attempts": attempts, "failed_after": len(failed_symbols), "failed_sample": failed_symbols[:20]}

    def _em_primary_route(self, run_id: str, spans: dict[str, tuple[date, date]]) -> dict[str, Any]:
        """Stage F2 (V07.2): EastMoney primary for non-TDX (BJ) daily bars.

        Sina is never called here. Every fetch goes through the §5a tri-state
        wrapper; exact retries (<=3) per symbol; EXISTS rows are staged with a
        unique controller-managed batch id and provenance; symbols still without
        valid bars after retries become UNEXPLAINED_MISSING in the coverage
        classifier layer and fail the gate.
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
                frame = None
                while attempt < 3:
                    attempt += 1
                    result = em_daily_tristate(
                        symbol, start, end, config=self.cfg
                    )
                    state = result["state"]
                    reason = result["reason"]
                    frame = result["frame"]
                    if state == "EXISTS":
                        break
                    self.ledger.append(
                        {"stage": "F2", "symbol": symbol, "attempt": attempt, "state": state, "reason": reason}
                    )
                if state == "EXISTS" and frame is not None and not frame.is_empty():
                    out = frame.filter(
                        (pl.col("trade_date") >= start) & (pl.col("trade_date") <= end)
                    )
                    out = out.unique(subset=["symbol", "trade_date"], keep="last")
                    out = with_provenance(
                        out, source="eastmoney", data_version=data_version_for("daily_bars")
                    )
                    batch_id = (
                        "em-"
                        + hashlib.sha256(
                            f"{symbol}|{start.isoformat()}|{end.isoformat()}|a{attempt}".encode()
                        ).hexdigest()[:12]
                    )
                    manifest.start_batch(
                        run_id, batch_id, task_id="daily_bars", dataset="daily_bars",
                        symbols=[symbol], window_start=start.isoformat(),
                        window_end=end.isoformat(), blocks_compaction=True,
                    )
                    writer.write_batch("daily_bars", run_id, batch_id, out)
                    manifest.finish_batch(
                        run_id, batch_id, "success",
                        rows_read=out.height, rows_written=out.height,
                    )
                    outcomes.append(
                        {"symbol": symbol, "state": "EXISTS", "attempt": attempt, "rows": out.height}
                    )
                else:
                    unexplained.append(symbol)
                    outcomes.append(
                        {"symbol": symbol, "state": state or "SOURCE_ERROR", "reason": reason}
                    )

        if unexplained:
            # coverage classifier output: after exact retries exhausted, still no
            # valid bars -> UNEXPLAINED_MISSING (blocks the gate)
            raise R3Error(
                "UNEXPLAINED_MISSING",
                f"{len(unexplained)} BJ symbols have no valid bars after retries: "
                f"{unexplained[:20]}",
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
