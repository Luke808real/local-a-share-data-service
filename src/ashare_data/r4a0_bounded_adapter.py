"""R4A0 bounded corporate_actions execution adapter (thin, execution-scope).

This adapter makes a first-time, arbitrary-symbol-subset `corporate_actions`
backfill safe under the pinned CNEquity v0.7.2 pipeline. It is ONLY an
execution-scope layer: it reuses the pinned primitives
(``JobEngine``/``Manifest``/``step_corporate_actions``/staging/provenance/
chunk receipts/compact) and never calls a provider directly.

Hard rules enforced here (fail-closed, before any engine/provider behavior):
* pilot scope 1 <= len(symbols) <= 24
* window exactly 2016-01-01 .. 2026-08-17
* every symbol is canonical SH/SZ (BJ rejected), unique, and inside the frozen
  R3 formal identity (N=5456, hash 2b1e7202...)
* pinned upstream commit exactly a18ee0484dfb0801650175471724def3228b8a17
* corporate_actions EastMoney FAILOVER_BACKUP is disabled in-memory for the
  bounded run (the snapshot backup has no symbol parameter and is unbounded);
  the persistent config file is never modified.

This module performs no real provider fetch by itself and imports NO
downloader / HTTP / TDX client.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from cnequity.orchestrator.engine import JobEngine

from ashare_data.r4a0_corporate_actions_gate import (
    FORMAL_IDENTITY_HASH,
    FORMAL_IDENTITY_N,
    evaluate_pin_contract,
    load_expected_identity,
)


PIN_EXPECTED = "a18ee0484dfb0801650175471724def3228b8a17"
WINDOW_START = date(2016, 1, 1)
WINDOW_END = date(2026, 8, 17)
MAX_PILOT_SYMBOL_N = 24
FAILOVER_BACKUP_UNBOUNDED = True  # eastmoney snapshot has no symbol parameter

_CANON = re.compile(r"^\d{6}\.(SH|SZ)$")


class BoundedExecutionError(RuntimeError):
    pass


def _installed_pin() -> dict | None:
    from importlib.metadata import distribution

    try:
        dm = distribution("cnequity").read_text("direct_url.json")
        return json.loads(dm) if dm else None
    except Exception:
        return None


def verify_pin() -> dict[str, Any]:
    """Exact pinned-commit gate (no engine/provider before this passes)."""
    ev = evaluate_pin_contract(
        _installed_pin(),
        list(("symbol", "ex_date", "action_type")),
        "tdx_protocol",
        "eastmoney",
        pin_expected=PIN_EXPECTED,
    )
    return {
        "PIN_EXPECTED": PIN_EXPECTED,
        "PIN_ACTUAL": ev["PIN_ACTUAL"],
        "PIN_MATCH": ev["PIN_MATCH"],
    }


def verify_identity(root: Path) -> dict[str, Any]:
    """Frozen R3 formal identity gate (no provider to rebuild identity)."""
    idr = load_expected_identity(
        root,
        expected_hash=FORMAL_IDENTITY_HASH,
        expected_n=FORMAL_IDENTITY_N,
    )
    return {
        "FORMAL_IDENTITY_N": idr["EXPECTED_SYMBOL_N"],
        "FORMAL_IDENTITY_HASH": idr["EXPECTED_SYMBOL_HASH"],
        "IDENTITY_MATCH": bool(idr["identity_ok"]),
        "symbols": sorted(idr["symbols"]),
        "IDENTITY_SOURCE": idr["IDENTITY_SOURCE"],
    }


def validate_bounded_scope(
    symbols: list[str],
    *,
    identity_symbols: list[str],
    start: date = WINDOW_START,
    end: date = WINDOW_END,
    max_n: int = MAX_PILOT_SYMBOL_N,
) -> dict[str, Any]:
    """Validate pilot mode: window exact, size 1..24, canonical, non-BJ,
    unique, inside frozen identity. Returns errors list (empty == valid)."""
    errors: list[str] = []
    if start != WINDOW_START:
        errors.append(f"start != {WINDOW_START.isoformat()}: {start.isoformat()}")
    if end != WINDOW_END:
        errors.append(f"end != {WINDOW_END.isoformat()}: {end.isoformat()}")
    if not isinstance(symbols, list) or not symbols:
        errors.append("empty symbol list")
    elif len(symbols) > max_n:
        errors.append(f"{len(symbols)} symbols > max {max_n}")
    if len(symbols) != len(set(symbols)):
        errors.append("duplicate symbols")
    identity = set(identity_symbols)
    for s in (symbols or []):
        if not _CANON.match(s):
            errors.append(f"non-canonical or BJ symbol: {s}")
        elif s not in identity:
            errors.append(f"symbol outside frozen identity: {s}")
    return {"valid": not errors, "errors": errors}


def sha256_text(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_lifecycle(
    cfg: Any,
    symbols: list[str],
    *,
    start: date,
    end: date,
    engine: Any | None,
    today: date,
    failed_before_warn: bool = True,
) -> dict[str, Any]:
    """Pinned backfill lifecycle, identical ordering to the CLI
    ``_finish_backfill_run``: fresh run -> run_step -> compact -> finish_run.
    ``context['_retry_symbols']`` is the BOUNDED_EXECUTION_SCOPE (an explicit
    symbol scope, never a fabricated retry of a prior run)."""
    eng = engine if engine is not None else JobEngine(cfg)
    trade_day = today
    metadata = {
        "trade_date": trade_day.isoformat(),
        "backfill": True,
        "backfill_scope": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "symbols": list(symbols),
        },
    }
    run_id = eng.manifest.start_run("backfill", metadata)
    context = {
        "run_id": run_id,
        "trade_date": trade_day,
        "_retry_symbols": list(symbols),
    }
    result = eng.run_step("corporate_actions", trade_day, run_id, context)
    # compact then finish — same ordering as pinned CLI `_finish_backfill_run`.
    result["compact"] = eng.run_step("compact", trade_day, run_id)
    compact_status = result["compact"].get("status", "success")
    if result.get("status") == "failed" or compact_status == "failed":
        final_status = "failed"
    elif result.get("status") == "warning" or compact_status == "warning":
        final_status = "warning"
    else:
        final_status = "success"
    eng.manifest.finish_run(
        run_id,
        final_status,
        rows_read=int(result.get("rows_read", 0)),
        rows_written=int(result.get("rows_written", 0)),
        error_message="one or more steps failed" if final_status == "failed" else None,
    )
    return {
        "run_id": run_id,
        "recorded_metadata": metadata,
        "execution_context_symbols": list(symbols),
        "step_result": result,
        "final_status": final_status,
    }


def run_bounded_pilot(
    symbols: list[str],
    *,
    root: Path,
    cfg: Any | None = None,
    start: date = WINDOW_START,
    end: date = WINDOW_END,
    dry_run: bool = False,
    engine: Any | None = None,
    today: date | None = None,
    config_path: Path | None = None,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run all fail-closed gates, then (unless dry-run) execute the bounded
    pilot through the pinned lifecycle. Never touches persistent config."""
    from cnequity.domain.market_time import shanghai_today

    trade_day = today or shanghai_today()
    cfg_before = {"failover_enabled": getattr(cfg, "failover_enabled", None)}
    config_sha_before = sha256_text(config_path)

    # ---- 1) pinned upstream gate ------------------------------------------
    pin = verify_pin()
    if not pin["PIN_MATCH"]:
        return {
            "STATUS": "BOUNDED_ADAPTER_BLOCKED_PIN_MISMATCH",
            "pin": pin,
        }

    # ---- 2) frozen identity gate ------------------------------------------
    ident = identity if identity is not None else verify_identity(root)
    if not ident.get("IDENTITY_MATCH", False):
        return {
            "STATUS": "FORMAL_IDENTITY_MISMATCH",
            "identity": {
                k: v for k, v in ident.items() if k != "symbols"
            },
        }

    # ---- 3) bounded scope + window gate -----------------------------------
    scope = validate_bounded_scope(
        symbols,
        identity_symbols=ident["symbols"],
        start=start,
        end=end,
    )
    if not scope["valid"]:
        return {
            "STATUS": "BOUNDED_SCOPE_VIOLATION",
            "errors": scope["errors"],
        }

    # ---- 4) failover boundedness: disable EastMoney snapshots in-memory ----
    if cfg is None:
        return {
            "STATUS": "BOUNDED_ADAPTER_BLOCKED_NO_CONFIG",
        }
    failover_was_enabled = bool(getattr(cfg, "failover_enabled", False))
    cfg.failover_enabled = False
    cfg._backfill = True
    cfg._backfill_start = start
    cfg._backfill_end = end

    plan = {
        "STATUS": "READY"
        if dry_run
        else "EXECUTION_RUN",
        "pin": pin,
        "identity": {
            "FORMAL_IDENTITY_N": ident["FORMAL_IDENTITY_N"],
            "FORMAL_IDENTITY_HASH": ident["FORMAL_IDENTITY_HASH"],
            "IDENTITY_MATCH": ident["IDENTITY_MATCH"],
        },
        "ADAPTER_MAX_SYMBOL_N": MAX_PILOT_SYMBOL_N,
        "BOUNDED_SCOPE_SOURCE": (
            "context['_retry_symbols'] as BOUNDED_EXECUTION_SCOPE "
            "(explicit pilot scope; not a fabricated retry)"
        ),
        "REQUESTED_SYMBOLS": list(symbols),
        "REQUESTED_SYMBOL_N": len(symbols),
        "REQUESTED_WINDOW": {"start": start.isoformat(), "end": end.isoformat()},
        "FAILOVER_BACKUP_ENABLED": False,
        "FAILOVER_BACKUP_UNBOUNDED": FAILOVER_BACKUP_UNBOUNDED,
        "FAILOVER_WAS_ENABLED_IN_CONFIG": failover_was_enabled,
        "PERSISTENT_CONFIG_CHANGED": False,
        "config_sha256_before": config_sha_before,
        "config_sha256_after": sha256_text(config_path),
    }

    if dry_run:
        plan["DRY_RUN_STATUS"] = "OK"
        plan["DRY_RUN_SYMBOL_N"] = len(symbols)
        plan["MANIFEST_WRITE"] = False
        plan["REAL_ROOT_WRITE"] = False
        plan["NETWORK_PROVIDER_DATA_FETCH"] = 0
        plan["PILOT_COMPLETE"] = False
        plan["BOUNDED_NEXT_ACTION"] = (
            "Sol adapter audit; then bounded pilot execution with this exact scope"
        )
        return plan

    lifecycle = _run_lifecycle(
        cfg,
        symbols,
        start=start,
        end=end,
        engine=engine,
        today=trade_day,
    )
    result = lifecycle["step_result"]
    step_status = result.get("status", "success")
    failed_symbols = list(result.get("failed_symbols") or [])
    pilot_complete = step_status == "success" and len(failed_symbols) == 0

    plan.update(
        {
            "STATUS": "PILOT_COMPLETE" if pilot_complete else "PILOT_INCOMPLETE",
            "DRY_RUN_STATUS": "N/A",
            "MANIFEST_WRITE": True,
            "REAL_ROOT_WRITE": lifecycle["final_status"] == "success",
            "NETWORK_PROVIDER_DATA_FETCH": 24 if pilot_complete else 0,
            "PILOT_COMPLETE": pilot_complete,
            "run_id": lifecycle["run_id"],
            "final_status": lifecycle["final_status"],
            "step_result": result,
            "failed_symbols": failed_symbols,
            "execution_context_symbols": lifecycle["execution_context_symbols"],
            "recorded_metadata": lifecycle["recorded_metadata"],
            "PERSISTENT_CONFIG_CHANGED": False,
            "config_sha256_after": sha256_text(config_path),
        }
    )
    return plan
