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

def config_changed(before: str | None, after: str | None) -> bool:
    return before is not None and after is not None and before != after


def config_integrity_status(before: str | None, after: str | None) -> str:
    if before is None or after is None:
        return "UNKNOWN"
    return "CHANGED" if before != after else "OK"


def ca_artifact_digest(root: Path) -> dict[str, list[str]]:
    """Read-only inventory of corporate_actions artifact paths (evidence for
    MARKET_DATA_WRITE_STATUS; never guesses from final_status)."""
    out: dict[str, list[str]] = {}
    for sub in ("curated", "staging"):
        d = root / sub / "corporate_actions"
        if d.is_dir():
            out[f"{sub}/corporate_actions"] = sorted(
                str(p.relative_to(root)) for p in d.rglob("*.parquet")
            )
    return out


def market_data_write_status(
    before: dict[str, list[str]] | None,
    after: dict[str, list[str]] | None,
) -> str:
    if before is None or after is None:
        return "UNKNOWN"
    return "YES" if before != after else "NO"


def receipt_post_check(
    eng: Any,
    run_id: str,
    symbols: list[str],
    start: date,
    end: date,
) -> dict[str, Any]:
    """Requested vs successful corporate_actions_chunk receipts (best-effort
    evidence; never fabricates a COMPLETE claim)."""
    try:
        rows = eng.manifest.get_batches_for_run(run_id)
    except Exception:
        return {"STATUS": "UNKNOWN"}
    chunks = [
        b
        for b in rows
        if b.get("task_id") == "corporate_actions_chunk"
        and b.get("status") == "success"
    ]
    covered: set[str] = set()
    windows_ok = True
    for b in chunks:
        raw = b.get("symbols_json")
        syms: list[str] = []
        if isinstance(raw, str):
            try:
                syms = json.loads(raw) or []
            except Exception:
                syms = []
        elif isinstance(raw, list):
            syms = [str(s) for s in raw]
        covered.update(syms)
        if b.get("window_start") != start.isoformat() or b.get(
            "window_end"
        ) != end.isoformat():
            windows_ok = False
    requested = set(symbols)
    ok = bool(covered == requested and windows_ok and len(chunks) > 0)
    return {
        "STATUS": "OK" if ok else "MISMATCH",
        "no_unexpected_symbols": (covered - requested) == set(),
        "each_requested_symbol_receipted": requested <= covered,
        "window_exact": windows_ok,
        "failed_chunk_contributes": False,
    }


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
    state: dict[str, Any],
) -> dict[str, Any]:
    """Pinned backfill lifecycle, identical ordering to the CLI
    ``_finish_backfill_run``: fresh run -> run_step -> compact -> finish_run.
    ``context['_retry_symbols']`` is the BOUNDED_EXECUTION_SCOPE (an explicit
    symbol scope, never a fabricated retry of a prior run). ``state`` records
    whether the corporate_actions (provider) execution path was entered."""
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
    state["provider_entered"] = True  # about to enter the pinned provider step
    result = eng.run_step("corporate_actions", trade_day, run_id, context)
    compact = eng.run_step("compact", trade_day, run_id)
    result["compact"] = compact
    compact_status = compact.get("status", "success")
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
        "compact_status": compact_status,
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
    pilot through the pinned lifecycle, restoring in-memory config afterwards.
    Never touches the persistent config file."""
    from cnequity.domain.market_time import shanghai_today

    trade_day = today or shanghai_today()
    config_sha_before = sha256_text(config_path)

    # ---- 1) pinned upstream gate ------------------------------------------
    pin = verify_pin()
    if not pin["PIN_MATCH"]:
        return {"STATUS": "BOUNDED_ADAPTER_BLOCKED_PIN_MISMATCH", "pin": pin}

    # ---- 2) frozen identity gate ------------------------------------------
    ident = identity if identity is not None else verify_identity(root)
    if not ident.get("IDENTITY_MATCH", False):
        return {
            "STATUS": "FORMAL_IDENTITY_MISMATCH",
            "identity": {k: v for k, v in ident.items() if k != "symbols"},
        }

    # ---- 3) bounded scope + window gate -----------------------------------
    scope = validate_bounded_scope(
        symbols,
        identity_symbols=ident["symbols"],
        start=start,
        end=end,
    )
    if not scope["valid"]:
        return {"STATUS": "BOUNDED_SCOPE_VIOLATION", "errors": scope["errors"]}

    # ---- 4) failover boundedness: disable EastMoney snapshots in-memory ----
    if cfg is None:
        return {"STATUS": "BOUNDED_ADAPTER_BLOCKED_NO_CONFIG"}
    failover_was_enabled = bool(getattr(cfg, "failover_enabled", False))

    cfg_snapshot = {
        "failover_enabled": getattr(cfg, "failover_enabled", None),
        "_backfill": getattr(cfg, "_backfill", None),
        "_backfill_start": getattr(cfg, "_backfill_start", None),
        "_backfill_end": getattr(cfg, "_backfill_end", None),
    }
    base = {
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
        "BOUNDED_NEXT_ACTION": (
            "Sol adapter fix re-audit; then bounded pilot execution (requires --exec, "
            "still FORBIDDEN until then)"
        ),
    }
    try:
        # execution-local override; the persistent config file is never modified
        cfg.failover_enabled = False
        cfg._backfill = True
        cfg._backfill_start = start
        cfg._backfill_end = end

        if dry_run:
            config_sha_after = sha256_text(config_path)
            return {
                **base,
                "STATUS": "READY",
                "DRY_RUN_STATUS": "OK",
                "DRY_RUN_SYMBOL_N": len(symbols),
                "MANIFEST_WRITE": "NO",
                "REAL_ROOT_WRITE": "NO",
                "NETWORK_PROVIDER_DATA_FETCH": "NO",
                "NETWORK_PROVIDER_REQUEST_COUNT": "UNVERIFIED",
                "MARKET_DATA_WRITE_STATUS": "NO",
                "PILOT_COMPLETE": False,
                "PERSISTENT_CONFIG_CHANGED": config_changed(
                    config_sha_before, config_sha_after
                ),
                "CONFIG_INTEGRITY_STATUS": config_integrity_status(
                    config_sha_before, config_sha_after
                ),
                "config_sha256_before": config_sha_before,
                "config_sha256_after": config_sha_after,
                "CONFIG_STATE_RESTORED": True,
            }

        art_before = ca_artifact_digest(root)
        state: dict[str, Any] = {"provider_entered": False}
        execution_error = None
        try:
            lifecycle = _run_lifecycle(
                cfg,
                symbols,
                start=start,
                end=end,
                engine=engine,
                today=trade_day,
                state=state,
            )
        except Exception as exc:
            execution_error = exc
        config_sha_after = sha256_text(config_path)
        config_dirty = config_changed(config_sha_before, config_sha_after)
        network = "YES" if state["provider_entered"] else "UNKNOWN"
        art_after = ca_artifact_digest(root)
        market_data = market_data_write_status(art_before, art_after)

        if execution_error is not None:
            return {
                **base,
                "STATUS": "EXECUTION_ERROR",
                "MANIFEST_WRITE": "YES",
                "REAL_ROOT_WRITE": "YES",
                "NETWORK_PROVIDER_DATA_FETCH": network,
                "NETWORK_PROVIDER_REQUEST_COUNT": "UNVERIFIED",
                "MARKET_DATA_WRITE_STATUS": market_data,
                "PILOT_COMPLETE": False,
                "PERSISTENT_CONFIG_CHANGED": config_dirty,
                "CONFIG_INTEGRITY_STATUS": config_integrity_status(
                    config_sha_before, config_sha_after
                ),
                "config_sha256_before": config_sha_before,
                "config_sha256_after": config_sha_after,
                "error": str(execution_error),
                "CONFIG_STATE_RESTORED": True,
            }

        result = lifecycle["step_result"]
        step_status = result.get("status", "success")
        failed_symbols = list(result.get("failed_symbols") or [])
        compact_status = lifecycle["compact_status"]
        final_status = lifecycle["final_status"]
        effective_eng = engine if engine is not None else JobEngine(cfg)
        post = receipt_post_check(
            effective_eng, lifecycle["run_id"], symbols, start, end
        )
        # PILOT_COMPLETE invariant: corporate success AND no failed symbol AND
        # compact success AND final success AND no persistent-config mutation.
        pilot_complete = bool(
            step_status == "success"
            and len(failed_symbols) == 0
            and compact_status == "success"
            and final_status == "success"
            and not config_dirty
        )
        status = (
            "WRITE_BOUNDARY_BREACH"
            if config_dirty
            else ("PILOT_COMPLETE" if pilot_complete else "PILOT_INCOMPLETE")
        )
        return {
            **base,
            "STATUS": status,
            "DRY_RUN_STATUS": "N/A",
            "MANIFEST_WRITE": "YES",
            "REAL_ROOT_WRITE": "YES",
            "NETWORK_PROVIDER_DATA_FETCH": network,
            "NETWORK_PROVIDER_REQUEST_COUNT": "UNVERIFIED",
            "MARKET_DATA_WRITE_STATUS": market_data,
            "PILOT_COMPLETE": pilot_complete,
            "final_status": final_status,
            "compact_status": compact_status,
            "failed_symbols": failed_symbols,
            "step_result": result,
            "run_id": lifecycle["run_id"],
            "receipt_post_check": post,
            "PERSISTENT_CONFIG_CHANGED": config_dirty,
            "CONFIG_INTEGRITY_STATUS": config_integrity_status(
                config_sha_before, config_sha_after
            ),
            "config_sha256_before": config_sha_before,
            "config_sha256_after": config_sha_after,
            "CONFIG_STATE_RESTORED": True,
        }
    finally:
        for key, val in cfg_snapshot.items():
            setattr(cfg, key, val)
