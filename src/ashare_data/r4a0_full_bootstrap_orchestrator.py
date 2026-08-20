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
    load_expected_identity,
)


CHUNK_SIZE = 24
WINDOW_START_STR = WINDOW_START.isoformat()
WINDOW_END_STR = WINDOW_END.isoformat()


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def compute_covered_symbols(rows: list[dict[str, Any]]) -> list[str]:
    """FULLY_COVERED_SYMBOLS from manifest chunk receipts (resume authority).

    A symbol is covered iff it appears in the symbols_json of a SUCCESSFUL
    ``corporate_actions_chunk`` receipt whose window is exactly the requested
    window (2016-01-01 .. 2026-08-17). failed / warning / wrong-window receipts
    never count. Zero event rows are irrelevant here (query coverage, not row
    presence).
    """
    covered: set[str] = set()
    for b in rows:
        if b.get("task_id") != "corporate_actions_chunk":
            continue
        if b.get("status") != "success":
            continue
        if b.get("window_start") != WINDOW_START_STR or b.get(
            "window_end"
        ) != WINDOW_END_STR:
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
        covered.update(str(s) for s in syms)
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
    """Resume from manifest -> remaining -> deterministic plan -> (dry)run."""
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
    receipts = load_chunk_receipts(manifest_path)
    covered = compute_covered_symbols(receipts)
    unknown = _check_unknown_receipt_symbols(covered, expected_set)
    if unknown:
        return {
            "STATUS": "UNKNOWN_RECEIPT_SYMBOL",
            "unknown_symbols": unknown[:20],
            "unknown_symbol_n": len(unknown),
        }
    remaining = sorted(expected_set - set(covered))
    plan = build_chunk_plan(remaining)
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
        "NETWORK_PROVIDER_DATA_FETCH": "NO",
        "MANIFEST_WRITE": "NO",
        "REAL_ROOT_WRITE": "NO",
        "DRY_RUN_STATUS": "OK" if dry_run else "N/A",
        "progress": [],
        "final_formal_gate": None,
        "FULL_BOOTSTRAP_COMPLETE": False,
    }
    if dry_run or not plan["chunks"]:
        return report

    # ---- future / testable real mode: deterministic chunk sequence ---------
    progress: list[dict[str, Any]] = []
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
        receipts = load_chunk_receipts(manifest_path)
        covered_now = compute_covered_symbols(receipts)
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
                }
            )
            return report
    report["progress"] = progress
    report["COVERED_SYMBOL_N"] = len(covered_now)
    # final formal gate (read-only) decides completeness
    gate = run_gate_fn() if run_gate_fn is not None else None
    report["final_formal_gate"] = gate
    if gate is not None and gate.get("R4A0_READY") is True:
        report["STATUS"] = "FULL_BOOTSTRAP_COMPLETE"
        report["FULL_BOOTSTRAP_COMPLETE"] = True
    else:
        report["STATUS"] = "FULL_BOOTSTRAP_INCOMPLETE"
    return report


def first_last_chunks(plan: dict, *, count: int = 3) -> dict[str, Any]:
    ch = plan["chunks"]
    return {
        "FIRST_3_CHUNKS": [c["index"] for c in ch[:count]],
        "LAST_3_CHUNKS": [c["index"] for c in ch[-count:]] if ch else [],
    }
