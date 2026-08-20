"""R4A0 resumable corporate_actions full-bootstrap orchestrator (thin).

Reuses the audited bounded adapter (``run_bounded_pilot``) as the ONLY
execution primitive; the manifest is the resume authority. No new downloader,
no direct TDX/EastMoney call, no pinned-upstream change.

The orchestrator only builds and dry-runs here. Real execution is a future
mode gated by Sol (``FULL_BOOTSTRAP_EXECUTION=FORBIDDEN_PENDING_SOL_ORCHESTRATOR_AUDIT``).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ashare_data.r4a0_bounded_adapter import (
    MAX_PILOT_SYMBOL_N,
    WINDOW_END,
    WINDOW_START,
    run_bounded_pilot,
)
from ashare_data.r4a0_corporate_actions_gate import (
    FORMAL_IDENTITY_HASH,
    FORMAL_IDENTITY_N,
    gaps_in_window,
    load_expected_identity,
    merge_intervals,
    run_gate,
)


CHUNK_SIZE = 24
WINDOW_START_STR = WINDOW_START.isoformat()
WINDOW_END_STR = WINDOW_END.isoformat()


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def compute_covered_symbols(rows: list[dict[str, Any]]) -> list[str]:
    """FULLY_COVERED_SYMBOLS from manifest chunk receipts (resume authority).

    Aligned with the formal gate's per-symbol coverage semantics: collect every
    SUCCESSFUL corporate_actions chunk receipt (dataset=corporate_actions)
    window for a symbol, merge overlapping/adjacent intervals, and a symbol is
    covered when its union contiguously covers the full window. failed /
    warning receipts never count; multiple partial successful receipts whose
    union covers the window DO count (same as the gate, so it is neither
    looser nor stricter: it cannot false-skip). Zero event rows are irrelevant.
    """
    by_symbol: dict[str, list[tuple[date, date]]] = {}
    for b in rows:
        if b.get("task_id") != "corporate_actions_chunk":
            continue
        if b.get("dataset") != "corporate_actions":
            continue
        if b.get("status") != "success":
            continue
        w_s = _parse_date(b.get("window_start"))
        w_e = _parse_date(b.get("window_end"))
        if w_s is None or w_e is None or w_s > w_e:
            continue
        raw = b.get("symbols_json")
        syms: list[str] = []
        if isinstance(raw, str):
            try:
                syms = json.loads(raw) or []
            except Exception:
                syms = []
        elif isinstance(raw, list):
            syms = [str(s) for s in raw]
        for s in syms:
            by_symbol.setdefault(s, []).append((w_s, w_e))
    covered = []
    for s, intervals in by_symbol.items():
        merged = merge_intervals(intervals)
        gaps = gaps_in_window(merged, WINDOW_START, WINDOW_END)
        cs = merged[0][0] if merged else None
        ce = merged[-1][1] if merged else None
        if (
            cs is not None
            and ce is not None
            and cs <= WINDOW_START
            and ce >= WINDOW_END
            and len(gaps) == 0
        ):
            covered.append(s)
    return sorted(covered)


def load_chunk_receipts(manifest_path: Path | None) -> list[dict[str, Any]]:
    """Read every corporate_actions_chunk batch read-only (immutable SQLite).

    A non-empty WAL would make an immutable read incomplete, so we fail closed.
    """
    if manifest_path is None or not manifest_path.is_file():
        return []
    wal = manifest_path.with_name(manifest_path.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise RuntimeError("WAL_PENDING_IMMUTABLE_READ_UNSAFE")
    con = sqlite3.connect(f"file:{manifest_path}?mode=ro&immutable=1", uri=True)
    try:
        rows = con.execute(
            "SELECT * FROM ingestion_batches WHERE task_id = ?",
            ("corporate_actions_chunk",),
        ).fetchall()
        cols = [d[1] for d in con.execute("PRAGMA table_info(ingestion_batches)")]
    finally:
        con.close()
    return [dict(zip(cols, r)) for r in rows]


def build_chunk_plan(remaining_symbols: list[str], *, chunk_size: int = CHUNK_SIZE) -> dict:
    """Deterministic, sorted, fixed-size chunk plan (last chunk may be < size)."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    ordered = sorted(set(remaining_symbols))
    chunks = [
        ordered[i : i + chunk_size] for i in range(0, len(ordered), chunk_size)
    ]
    chunk_meta: list[dict[str, Any]] = []
    for idx, c in enumerate(chunks):
        chunk_meta.append(
            {
                "index": idx + 1,
                "symbol_n": len(c),
                "first_symbol": c[0],
                "last_symbol": c[-1],
                "symbol_hash": _sha(json.dumps(c, separators=(",", ":"))),
                "symbols": list(c),
            }
        )
    plan_hash = _sha(
        json.dumps([c for c in chunks], separators=(",", ":"))
    )
    return {
        "CHUNK_SIZE": chunk_size,
        "CHUNK_COUNT": len(chunks),
        "CHUNK_PLAN_HASH": plan_hash,
        "chunks": chunk_meta,
        "total_symbols": len(ordered),
    }


def _check_unknown_receipt_symbols(covered: list[str], expected: set[str]) -> list[str]:
    return [s for s in covered if s not in expected]


def compute_queried_symbols(rows: list[dict[str, Any]]) -> list[str]:
    """Union of symbols_json across ALL successful corporate_actions_chunk
    receipts regardless of window. Any trusted successful queried scope must
    stay inside the frozen identity (partial-window successful receipts
    included)."""
    queried: set[str] = set()
    for b in rows:
        if b.get("task_id") != "corporate_actions_chunk":
            continue
        if b.get("dataset") != "corporate_actions":
            continue
        if b.get("status") != "success":
            continue
        raw = b.get("symbols_json")
        syms: list[str] = []
        if isinstance(raw, str):
            try:
                syms = json.loads(raw) or []
            except Exception:
                syms = []
        elif isinstance(raw, list):
            syms = [str(s) for s in raw]
        queried.update(syms)
    return sorted(queried)


def unknown_queried_symbols(queried: list[str], expected: set[str]) -> list[str]:
    return [s for s in queried if s not in expected]


def config_boundary_status(before: str | None, after: str | None) -> str:
    """OK only when both hashes exist and are equal; missing either side is
    UNKNOWN (fail closed, NOT PASS); a mismatch is CHANGED."""
    if before is None or after is None:
        return "UNKNOWN"
    return "CHANGED" if before != after else "OK"


def default_formal_gate(root: Path) -> dict[str, Any]:
    """Production formal gate: the audited r4a0 run_gate with frozen identity
    and window wired in. Never a caller-injected fake in production."""
    return run_gate(
        root,
        expected_identity_n=FORMAL_IDENTITY_N,
        expected_identity_hash=FORMAL_IDENTITY_HASH,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )


def run_gate_safely(gate_fn: Callable[..., dict[str, Any]], root: Path) -> dict[str, Any]:
    try:
        return gate_fn(root)
    except Exception as exc:  # fail closed; never traceback out
        return {"__error__": str(exc)}


def gate_correctness_blockers(gate: dict[str, Any]) -> list[str]:
    """Non-coverage correctness blockers from a formal gate result (identity /
    schema / scope / uniqueness / provenance). Coverage-incomplete is allowed;
    these are not."""
    if not isinstance(gate, dict) or gate.get("__error__"):
        return ["GATE_UNEXECUTABLE"]
    blockers: list[str] = []
    if gate.get("IDENTITY_STATUS") is not None and gate["IDENTITY_STATUS"] != "PASS":
        blockers.append("IDENTITY")
    for key in ("SCHEMA_STATUS", "SCOPE_STATUS", "UNIQUENESS_STATUS", "PROVENANCE_STATUS"):
        if gate.get(key) is not None and gate[key] != "PASS":
            blockers.append(key[: -len("_STATUS")])
    return blockers


def protected_inventory_hash(root: Path) -> str:
    """path+size+mtime_ns inventory hash of protected R3 datasets (no content
    SHA-256)."""
    rows: list[dict[str, Any]] = []
    for sub in ("curated", "staging"):
        for ds in ("daily_bars", "instruments", "trading_calendar"):
            d = root / sub / ds
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    st = p.stat()
                    rows.append(
                        {
                            "path": str(p.relative_to(root)),
                            "size": st.st_size,
                            "mtime_ns": st.st_mtime_ns,
                        }
                    )
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def config_sha(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_full_bootstrap(
    root: Path,
    *,
    cfg: Any,
    dry_run: bool = True,
    identity: dict[str, Any] | None = None,
    manifest_path: Path | None = None,
    engine: Any | None = None,
    config_path: Path | None = None,
    today: date | None = None,
    gate_every: int = 10,
    run_gate_fn: Callable[..., dict[str, Any]] | None = None,
    adapter_callable: Callable[..., dict[str, Any]] = run_bounded_pilot,
) -> dict[str, Any]:
    """Resume from manifest -> remaining -> deterministic plan -> (dry)run.

    Production default gate is the audited real run_gate (frozen identity +
    window); dependency injection (run_gate_fn / adapter_callable / engine) is
    for tests only. Real mode: START gate -> chunks (PERIODIC every gate_every)
    -> FINAL gate, with before/after write-boundary checks.
    """
    ident = identity if identity is not None else load_expected_identity(
        root, expected_hash=FORMAL_IDENTITY_HASH, expected_n=FORMAL_IDENTITY_N
    )
    if not ident.get("identity_ok", False):
        return {
            "STATUS": "FORMAL_IDENTITY_MISMATCH",
            "EXPECTED_SYMBOL_N": ident.get("EXPECTED_SYMBOL_N"),
            "EXPECTED_SYMBOL_HASH": ident.get("EXPECTED_SYMBOL_HASH"),
        }
    expected = sorted(ident["symbols"])
    expected_set = set(expected)
    try:
        receipts = load_chunk_receipts(manifest_path)
    except Exception as exc:
        return {"STATUS": "MANIFEST_READ_FAILURE", "error": str(exc)}
    covered = compute_covered_symbols(receipts)
    queried = compute_queried_symbols(receipts)
    unknown = unknown_queried_symbols(queried, expected_set)
    if unknown:
        return {
            "STATUS": "UNKNOWN_RECEIPT_SYMBOL",
            "unknown_symbols": unknown[:20],
            "unknown_symbol_n": len(unknown),
        }
    remaining = sorted(expected_set - set(covered))
    plan = build_chunk_plan(remaining)
    config_sha_before = config_sha(config_path)
    protected_before = protected_inventory_hash(root)
    gate_fn = run_gate_fn if run_gate_fn is not None else default_formal_gate
    report: dict[str, Any] = {
        "STATUS": "READY",
        "EXPECTED_SYMBOL_N": len(expected),
        "EXPECTED_SYMBOL_HASH": ident["EXPECTED_SYMBOL_HASH"],
        "COVERED_SYMBOL_N": len(covered),
        "REMAINING_SYMBOL_N": len(remaining),
        "CHUNK_SIZE": plan["CHUNK_SIZE"],
        "CHUNK_COUNT": plan["CHUNK_COUNT"],
        "CHUNK_PLAN_HASH": plan["CHUNK_PLAN_HASH"],
        "chunks": plan["chunks"],
        "MANIFEST_IS_RESUME_AUTHORITY": True,
        "progress": [],
        "periodic_gates": [],
        "final_formal_gate": None,
        "FULL_BOOTSTRAP_COMPLETE": False,
        "PROTECTED_HASH_BEFORE": protected_before,
        "PROTECTED_HASH_AFTER": None,
        "config_sha256_before": config_sha_before,
        "config_sha256_after": None,
        "CONFIG_BOUNDARY_STATUS": None,
        "ORIGINAL_STOP_REASON": None,
        "EXECUTION_STARTED": False,
    }
    if dry_run or not plan["chunks"]:
        report["DRY_RUN_STATUS"] = "OK" if dry_run else "N/A"
        report["NETWORK_PROVIDER_DATA_FETCH"] = "NO"
        report["MANIFEST_WRITE"] = "NO"
        report["REAL_ROOT_WRITE"] = "NO"
        report["PROVIDER_STEP_ENTERED"] = "NO"
        report["NETWORK_PROVIDER_REQUEST_COUNT"] = "UNVERIFIED"
        if not dry_run and not plan["chunks"]:
            # zero-remaining real resume: only a read-only FINAL formal gate.
            # No adapter / provider / manifest write happened, so telemetry
            # stays NO and EXECUTION_STARTED stays false.
            return _finalize_report(
                report, gate_fn, root, config_sha_before, protected_before, config_path
            )
        return report

    # ---- REAL MODE: gates + deterministic chunk sequence ------------------
    report["MANIFEST_WRITE"] = "YES"
    report["REAL_ROOT_WRITE"] = "YES"
    report["NETWORK_PROVIDER_DATA_FETCH"] = "UNKNOWN"
    report["NETWORK_PROVIDER_REQUEST_COUNT"] = "UNVERIFIED"
    report["DRY_RUN_STATUS"] = "N/A"

    # START gate: coverage-incomplete is expected; new correctness blockers stop.
    start_gate = run_gate_safely(gate_fn, root)
    start_blockers = gate_correctness_blockers(start_gate)
    if start_blockers:
        report.update(
            {
                "STATUS": "START_GATE_FAILURE",
                "start_blockers": start_blockers,
                "start_gate": start_gate,
            }
        )
        # no adapter ran yet and no real data was written
        report["EXECUTION_STARTED"] = False
        return report
    report["start_gate"] = start_gate
    report["EXECUTION_STARTED"] = True

    progress: list[dict[str, Any]] = []
    success_count = 0
    periodic_gates: list[dict[str, Any]] = []
    covered_now: list[str] = covered
    for chunk_meta in plan["chunks"]:
        symbols = chunk_meta["symbols"]
        out = adapter_callable(
            symbols,
            root=root,
            cfg=cfg,
            dry_run=False,
            engine=engine,
            today=today,
            config_path=config_path,
            identity=ident,
        )
        ok = bool(
            out.get("STATUS") == "PILOT_COMPLETE"
            and out.get("receipt_post_check", {}).get("STATUS") == "OK"
            and out.get("CONFIG_INTEGRITY_STATUS") == "OK"
        )
        try:
            covered_now = compute_covered_symbols(load_chunk_receipts(manifest_path))
        except Exception as exc:
            report.update(
                {
                    "STATUS": "MANIFEST_READ_FAILURE",
                    "stop_reason": "MANIFEST_READ_FAILURE",
                    "stop_chunk_index": chunk_meta["index"],
                    "error": str(exc),
                    "progress": progress,
                    "periodic_gates": periodic_gates,
                }
            )
            return apply_write_boundary(
                report, root, config_sha_before, protected_before, config_path
            )
        progress.append(
            {
                "CHUNK_INDEX": chunk_meta["index"],
                "RUN_ID": out.get("run_id"),
                "REQUESTED_SYMBOL_N": len(symbols),
                "PILOT_COMPLETE": bool(out.get("PILOT_COMPLETE")),
                "RECEIPT_STATUS": out.get("receipt_post_check", {}).get("STATUS"),
                "FAILED_SYMBOLS": out.get("failed_symbols") or [],
                "COVERED_N_AFTER": len(covered_now),
                "REMAINING_N_AFTER": len(expected_set - set(covered_now)),
            }
        )
        if not ok:
            if out.get("CONFIG_INTEGRITY_STATUS") != "OK":
                stop_reason = "CONFIG_UNKNOWN_OR_CHANGED"
            elif out.get("receipt_post_check", {}).get("STATUS") != "OK":
                stop_reason = "RECEIPT_MISMATCH"
            else:
                stop_reason = out.get("STATUS") or "UNKNOWN"
            report.update(
                {
                    "STATUS": "FULL_BOOTSTRAP_STOPPED",
                    "stop_reason": stop_reason,
                    "stop_chunk_index": chunk_meta["index"],
                    "chunk_result": out,
                    "progress": progress,
                    "periodic_gates": periodic_gates,
                }
            )
            return apply_write_boundary(
                report, root, config_sha_before, protected_before, config_path
            )
        success_count += 1
        if success_count % gate_every == 0:
            periodic = run_gate_safely(gate_fn, root)
            periodic_blockers = gate_correctness_blockers(periodic)
            periodic_gates.append(periodic)
            if periodic_blockers:
                report.update(
                    {
                        "STATUS": "PERIODIC_GATE_FAILURE",
                        "periodic_blockers": periodic_blockers,
                        "periodic_gate_after_chunk": chunk_meta["index"],
                        "progress": progress,
                        "periodic_gates": periodic_gates,
                    }
                )
                return apply_write_boundary(
                    report, root, config_sha_before, protected_before, config_path
                )
    report["progress"] = progress
    report["periodic_gates"] = periodic_gates
    report["COVERED_SYMBOL_N"] = len(covered_now)
    return _finalize_report(
        report, gate_fn, root, config_sha_before, protected_before, config_path
    )


def apply_write_boundary(
    report: dict[str, Any],
    root: Path,
    config_sha_before: str | None,
    protected_before: str,
    config_path: Path | None,
) -> dict[str, Any]:
    """Central termination boundary for every real-execution exit path:
    record PROTECTED_HASH_AFTER + CONFIG_SHA_AFTER and compare. A breach makes
    WRITE_BOUNDARY_BREACH win over the original stop reason (preserved in
    ORIGINAL_STOP_REASON)."""
    config_sha_after = config_sha(config_path)
    protected_after = protected_inventory_hash(root)
    report["PROTECTED_HASH_AFTER"] = protected_after
    report["config_sha256_after"] = config_sha_after
    report["CONFIG_BOUNDARY_STATUS"] = config_boundary_status(
        config_sha_before, config_sha_after
    )
    if protected_after != protected_before or report["CONFIG_BOUNDARY_STATUS"] == "CHANGED":
        if report.get("STATUS") != "WRITE_BOUNDARY_BREACH":
            report["ORIGINAL_STOP_REASON"] = report.get("STATUS")
        report["STATUS"] = "WRITE_BOUNDARY_BREACH"
        report["FULL_BOOTSTRAP_COMPLETE"] = False
    return report


def _finalize_report(
    report: dict[str, Any],
    gate_fn: Callable[..., dict[str, Any]],
    root: Path,
    config_sha_before: str | None,
    protected_before: str,
    config_path: Path | None,
) -> dict[str, Any]:
    """FINAL formal gate + write boundary for real execution.

    boundary is checked on every exit; CONFIG_BOUNDARY_STATUS!=OK fails closed
    (UNKNOWN/CHANGED can never complete). Only OK + R4A0_READY=true completes.
    """
    report = apply_write_boundary(
        report, root, config_sha_before, protected_before, config_path
    )
    if report["STATUS"] == "WRITE_BOUNDARY_BREACH":
        return report
    if report.get("CONFIG_BOUNDARY_STATUS") != "OK":
        # config unknown/changed is fail-closed even if everything else passed
        if report["CONFIG_BOUNDARY_STATUS"] == "UNKNOWN":
            report["STATUS"] = "CONFIG_BOUNDARY_UNKNOWN"
        report["FULL_BOOTSTRAP_COMPLETE"] = False
        return report
    final_gate = run_gate_safely(gate_fn, root)
    report["final_formal_gate"] = final_gate
    if final_gate.get("__error__"):
        report["STATUS"] = "GATE_EXECUTION_FAILURE"
        report["FULL_BOOTSTRAP_COMPLETE"] = False
        return report
    complete = final_gate.get("R4A0_READY") is True
    report["FULL_BOOTSTRAP_COMPLETE"] = bool(complete)
    if complete:
        report["STATUS"] = "FULL_BOOTSTRAP_COMPLETE"
    else:
        report["STATUS"] = "FULL_BOOTSTRAP_INCOMPLETE"
        report["FULL_BOOTSTRAP_BLOCKER"] = final_gate.get("BLOCKER")
    return report


def first_last_chunks(plan: dict, *, count: int = 3) -> dict[str, Any]:
    ch = plan["chunks"]
    return {
        "FIRST_3_CHUNKS": [c["index"] for c in ch[:count]],
        "LAST_3_CHUNKS": [c["index"] for c in ch[-count:]] if ch else [],
    }
