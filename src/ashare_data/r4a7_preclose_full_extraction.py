"""R4A7 resumable full-universe preclose extraction orchestrator (V01).

Dry-run / implementation-only task. Builds the deterministic 5456-symbol
SH/SZ query plan, implements a resumable PENDING/COMPLETE/FAILED checkpoint
manifest, a staging write boundary, atomic unit completion, and a final
aggregation validator that never promotes state.

THIS MODULE MUST NOT RUN THE REAL FULL-MARKET BAOSTOCK EXTRACTION on its
own: the orchestrator only executes units when the caller injects a
provider_fetch and passes dry_run=False, which this task never does.

Every per-unit execution reuses the audited bounded adapter
(``run_bounded_adapter``), preserving its exact REAL SHA authority, field
identity, identity/window gates, global duplicate gate, quality gate, and
coverage accounting. No looser reimplementation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterable

import polars as pl

from ashare_data.r4a0_corporate_actions_gate import load_expected_identity
from ashare_data.r4a_preclose_bounded_adapter import (
    AS_OF,
    CNEQUITY_PIN,
    FORMAL_IDENTITY_HASH,
    FORMAL_IDENTITY_N,
    WINDOW_START,
    QUERY_ADJUSTFLAG,
    QUERY_CONTRACT_VERSION,
    QUERY_FIELDS,
    QUERY_FREQUENCY,
    SOURCE,
    SOURCE_VERSION,
    adapter_authority_status,
    build_query_plan,
    compute_window_boundary_edges,
    load_frozen_sentinel_evidence,
    load_instrument_list_dates,
    load_required_keys,
    run_bounded_adapter,
    symbol_hash,
    verify_clean_normal_parity,
    verify_frozen_sentinels,
    verify_window_boundary_rows,
)


MANIFEST_SCHEMA_VERSION = "R4A7_PRECLOSE_V01"
MANIFEST_FILENAME = "manifest.json"
UNITS_DIRNAME = "units"
EXECUTION_CONTEXT_REAL = "REAL"
EXECUTION_CONTEXT_OFFLINE_TEST = "OFFLINE_TEST"
FORMAL_COLUMNS = [
    "symbol",
    "trade_date",
    "preclose",
    "source",
    "source_version",
    "adapter_version",
    "query_contract_version",
    "fetched_at",
    "provider_tradestatus",
    "coverage_status",
]


def _sha_256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_atomic(path: Path, payload: str) -> None:
    """Atomic file write: tmp in the same directory + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def formal_fact_hash(rows: Iterable[dict[str, Any]]) -> str:
    """Deterministic hash over sorted (symbol, trade_date, display preclose).

    Identical semantics to the R4A6 real pilot FORMAL_FACT_HASH contract.
    """
    tuples = sorted(
        (r["symbol"], r["trade_date"].isoformat(), round(float(r["preclose"]), 4))
        for r in rows
    )
    return _sha_256(json.dumps(tuples, separators=(",", ":")))


def staged_formal_content_hash(rows: Iterable[dict[str, Any]]) -> str:
    """Deterministic hash over ALL canonical formal columns (per row).

    Separate from the frozen R4A6-compatible FORMAL_FACT_HASH (which stays
    keyed on (symbol, trade_date, display preclose)). This hash covers the
    full staged row so that any per-field tamper invalidates the unit.
    """

    def _fmt(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        return value

    payload = [[_fmt(row.get(c)) for c in FORMAL_COLUMNS] for row in rows]
    return _sha_256(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def verify_staged_formal_rows(
    rows: Iterable[dict[str, Any]],
    *,
    expected_symbol: str,
    contract: dict[str, Any],
    as_of: date,
) -> tuple[bool, list[str]]:
    """Explicit staged-formal row verifier (R4A7.2 audit fix).

    Every formal row must satisfy the full frozen contract identity:
      symbol == expected symbol
      trade_date <= AS_OF
      preclose finite positive
      provider_tradestatus == 1
      coverage_status == "COVERED"
      source == BAOSTOCK_HISTORY_K_PRECLOSE
      source_version == baostock-0.9.3
      query_contract_version == frozen contract
      adapter_version == current exact adapter authority SHA

    Used in BOTH unit_complete_and_valid() and aggregate_full_run(); the
    (symbol, trade_date, preclose) hash alone is never sufficient.
    """
    issues: list[str] = []
    expected_adapter = contract.get("ADAPTER_AUTHORITY_SHA")
    for index, row in enumerate(rows):
        prefix = f"row{index}"
        if str(row.get("symbol")) != expected_symbol:
            issues.append(f"{prefix}:symbol={row.get('symbol')!r}")
        trade_date = row.get("trade_date")
        if trade_date is None or trade_date > as_of:
            issues.append(f"{prefix}:trade_date={trade_date!r}")
        preclose = row.get("preclose")
        try:
            finite_positive = bool(
                preclose is not None
                and math.isfinite(float(preclose))
                and float(preclose) > 0
            )
        except (TypeError, ValueError):
            finite_positive = False
        if not finite_positive:
            issues.append(f"{prefix}:preclose={preclose!r}")
        if row.get("provider_tradestatus") != 1:
            issues.append(f"{prefix}:provider_tradestatus={row.get('provider_tradestatus')!r}")
        if row.get("coverage_status") != "COVERED":
            issues.append(f"{prefix}:coverage_status={row.get('coverage_status')!r}")
        if row.get("source") != SOURCE:
            issues.append(f"{prefix}:source={row.get('source')!r}")
        if row.get("source_version") != SOURCE_VERSION:
            issues.append(f"{prefix}:source_version={row.get('source_version')!r}")
        if row.get("query_contract_version") != contract.get("QUERY_CONTRACT_VERSION"):
            issues.append(
                f"{prefix}:query_contract_version={row.get('query_contract_version')!r}"
            )
        if row.get("adapter_version") != expected_adapter:
            issues.append(f"{prefix}:adapter_version={row.get('adapter_version')!r}")
    return (len(issues) == 0, issues)


def formal_frame(rows: Iterable[dict[str, Any]]) -> pl.DataFrame:
    """Canonical staged formal frame (formal rows only, fixed columns)."""
    records = [
        {
            "symbol": r["symbol"],
            "trade_date": r["trade_date"],
            "preclose": round(float(r["preclose"]), 6),
            "source": r.get("source", SOURCE),
            "source_version": r.get("source_version", SOURCE_VERSION),
            "adapter_version": r.get("adapter_version"),
            "query_contract_version": r.get("query_contract_version", QUERY_CONTRACT_VERSION),
            "fetched_at": r.get("fetched_at"),
            "provider_tradestatus": r.get("provider_tradestatus", 1),
            "coverage_status": r.get("coverage_status", "COVERED"),
        }
        for r in rows
    ]
    return pl.DataFrame(records, schema={c: pl.Utf8 if c == "symbol" else (
        pl.Date if c == "trade_date" else pl.Float64 if c == "preclose" else (
            pl.Int32 if c == "provider_tradestatus" else pl.Utf8
        )
    ) for c in FORMAL_COLUMNS})


def build_full_query_plan(
    root: Path,
    *,
    as_of: date = AS_OF,
    window_start: date = WINDOW_START,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic full-universe plan from the frozen 5456 identity.

    identity may be injected for tests; production default loads the audited
    R3 identity via load_expected_identity with the frozen hash/n, which is
    FAIL CLOSED on any drift.
    """
    if identity is None:
        identity = load_expected_identity(
            root,
            expected_hash=FORMAL_IDENTITY_HASH,
            expected_n=FORMAL_IDENTITY_N,
        )
        if not identity.get("identity_ok"):
            raise RuntimeError(
                f"frozen identity drift: {identity.get('IDENTITY_STATUS')} "
                f"n={identity.get('EXPECTED_SYMBOL_N')} "
                f"hash={identity.get('EXPECTED_SYMBOL_HASH')}"
            )
    symbols = sorted(set(identity["symbols"]))
    plan = build_query_plan(symbols, window_start=window_start, as_of=as_of)
    return {
        "FULL_SYMBOL_N": len(symbols),
        "FULL_SYMBOL_HASH": symbol_hash(symbols),
        "FULL_QUERY_WINDOW_N": plan["QUERY_WINDOW_N"],
        "FULL_QUERY_PLAN_HASH": plan["QUERY_PLAN_HASH"],
        "query_plan": plan["query_plan"],
        "symbols": symbols,
    }


def contract_identity(
    *,
    as_of: date,
    window_start: date,
    adapter_authority_sha: str | None,
    full_query_plan_hash: str | None,
) -> dict[str, Any]:
    """Executable-contract identity recorded in every receipt/manifest."""
    return {
        "MANIFEST_SCHEMA_VERSION": MANIFEST_SCHEMA_VERSION,
        "AS_OF": as_of.isoformat(),
        "WINDOW_START": window_start.isoformat(),
        "PRIMARY_SOURCE": SOURCE,
        "SOURCE_VERSION": SOURCE_VERSION,
        "QUERY_CONTRACT_VERSION": QUERY_CONTRACT_VERSION,
        "QUERY_FIELDS": QUERY_FIELDS,
        "QUERY_FREQUENCY": QUERY_FREQUENCY,
        "QUERY_ADJUSTFLAG": QUERY_ADJUSTFLAG,
        "CNEQUITY_PIN": CNEQUITY_PIN,
        "FORMAL_IDENTITY_N": FORMAL_IDENTITY_N,
        "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
        "ADAPTER_AUTHORITY_SHA": adapter_authority_sha,
        "FULL_QUERY_PLAN_HASH": full_query_plan_hash,
    }


def new_manifest(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "contract": contract,
        "units": {},
    }


def load_manifest(path: Path) -> dict[str, Any]:
    """Read manifest; corrupted or unreadable manifest FAILS CLOSED."""
    try:
        raw = path.read_text(encoding="utf-8")
        manifest = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"CHECKPOINT_CORRUPT: cannot read {path}: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise RuntimeError(f"CHECKPOINT_CORRUPT: bad schema in {path}")
    if not isinstance(manifest.get("contract"), dict) or not isinstance(manifest.get("units"), dict):
        raise RuntimeError(f"CHECKPOINT_CORRUPT: bad shape in {path}")
    return manifest


def manifest_contract_matches(manifest: dict[str, Any], contract: dict[str, Any]) -> bool:
    return manifest.get("contract") == contract


def build_unit_receipt(
    *,
    symbol: str,
    unit_window_hash: str,
    plan_window_n: int,
    adapter_version: str,
    required_row_n: int,
    formal_fact_row_n: int,
    missing_required_n: int,
    provider_suspended_superset_n: int,
    unexpected_traded_n: int,
    tradestatus_unknown_n: int,
    identity_failure_n: int,
    window_scope_failure_n: int,
    duplicate_n: int,
    post_asof_n: int,
    invalid_preclose_n: int,
    formal_hash: str,
    staged_formal_hash: str,
    formal_path: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Unit completion receipt: everything needed to revalidate on resume."""
    return {
        "STATE": "COMPLETE",
        "symbol": symbol,
        "unit_query_window_n": plan_window_n,
        "unit_query_plan_hash": unit_window_hash,
        "adapter_version": adapter_version,
        "REQUIRED_ROW_N": required_row_n,
        "FORMAL_FACT_ROW_N": formal_fact_row_n,
        "MISSING_REQUIRED_N": missing_required_n,
        "PROVIDER_SUSPENDED_SUPERSET_N": provider_suspended_superset_n,
        "UNEXPECTED_TRADED_N": unexpected_traded_n,
        "TRADESTATUS_UNKNOWN_N": tradestatus_unknown_n,
        "IDENTITY_FAILURE_N": identity_failure_n,
        "WINDOW_SCOPE_FAILURE_N": window_scope_failure_n,
        "DUPLICATE_N": duplicate_n,
        "POST_ASOF_N": post_asof_n,
        "INVALID_PRECLOSE_N": invalid_preclose_n,
        "FORMAL_FACT_HASH": formal_hash,
        "STAGED_FORMAL_CONTENT_HASH": staged_formal_hash,
        "formal_path": formal_path,
        "contract": contract,
        "completion_utc": _now_utc(),
    }


def receipt_state(receipt: dict[str, Any]) -> str:
    return str(receipt.get("STATE", "UNKNOWN"))


def unit_complete_and_valid(
    receipt: dict[str, Any] | None,
    *,
    contract: dict[str, Any],
    formal_path: Path,
    expected_symbol: str,
    as_of: date = AS_OF,
    window_start: date = WINDOW_START,
) -> bool:
    """Resume skip rule: skip ONLY when the receipt survives exact checks.

    A completed unit must have exact contract identity, matching symbol,
    readable formal output whose content hash re-verifies, and COMPLETE
    state. Anything else (missing file, corrupted receipt, hash mismatch,
    contract drift, FAILED/UNKNOWN state) is not proof of completion.
    """
    if receipt is None:
        return False
    if receipt_state(receipt) != "COMPLETE":
        return False
    if receipt.get("symbol") != expected_symbol:
        return False
    if receipt.get("contract") != contract:
        return False
    if receipt.get("adapter_version") != contract.get("ADAPTER_AUTHORITY_SHA"):
        return False
    # Unit executable identity: rederive and verify the unit query plan.
    unit_plan = build_query_plan(
        [expected_symbol], window_start=window_start, as_of=as_of
    )
    if int(receipt.get("unit_query_window_n", -1)) != unit_plan["QUERY_WINDOW_N"]:
        return False
    if receipt.get("unit_query_plan_hash") != unit_plan["QUERY_PLAN_HASH"]:
        return False
    if not formal_path.is_file():
        return False
    try:
        frame = pl.read_parquet(formal_path)
        rows = frame.to_dicts()
    except Exception:
        return False
    actual = formal_fact_hash(rows)
    if actual != receipt.get("FORMAL_FACT_HASH"):
        return False
    staged_ok, _ = verify_staged_formal_rows(
        rows,
        expected_symbol=expected_symbol,
        contract=contract,
        as_of=as_of,
    )
    if not staged_ok:
        return False
    staged_actual = staged_formal_content_hash(rows)
    if staged_actual != receipt.get("STAGED_FORMAL_CONTENT_HASH"):
        return False
    if len(rows) != int(receipt.get("FORMAL_FACT_ROW_N", -1)):
        return False
    return True


def execute_unit(
    *,
    root: Path,
    symbol: str,
    provider_fetch: Callable[[dict[str, Any]], list[dict[str, Any]]],
    adapter_version: str,
    expected_adapter_sha: str,
    runtime_adapter_sha: str,
    fetched_at: str,
    as_of: date,
    window_start: date,
) -> dict[str, Any]:
    """Execute one symbol unit through the audited bounded adapter."""
    result = run_bounded_adapter(
        root=root,
        symbols=[symbol],
        provider_fetch=provider_fetch,
        dry_run=False,
        adapter_version=adapter_version,
        expected_adapter_sha=expected_adapter_sha,
        runtime_adapter_sha=runtime_adapter_sha,
        fetched_at=fetched_at,
        as_of=as_of,
        window_start=window_start,
    )
    if result.get("STATUS") != "COMPLETE":
        raise RuntimeError(f"unit failed for {symbol}: {result.get('STATUS')}")
    if not result.get("QUALITY_GATE_PASS"):
        raise RuntimeError(f"quality gate failed for {symbol}")
    return result


def run_full_extraction(
    root: Path,
    *,
    provider_fetch: Callable[[dict[str, Any]], list[dict[str, Any]]] | None,
    adapter_version: str,
    expected_adapter_sha: str,
    runtime_adapter_sha: str,
    staging_root: Path,
    manifest_path: Path | None = None,
    dry_run: bool = True,
    limit: int | None = None,
    as_of: date = AS_OF,
    window_start: date = WINDOW_START,
    identity: dict[str, Any] | None = None,
    execution_context: str | None = None,
) -> dict[str, Any]:
    """Resumable full-universe extraction; production default is dry-run.

    REAL (non-dry-run) execution is hardened (R4A7.1 audit fixes):
      - the frozen adapter authority gate runs BEFORE any checkpoint read,
        checkpoint reuse, or provider fetch; adapter_version ==
        expected_adapter_sha == runtime_adapter_sha as canonical 40-char
        lowercase hex SHA is required. Runtime-only drift fails closed even
        against a fully COMPLETE checkpoint;
      - identity injection is FORBIDDEN; the authoritative frozen identity
        is loaded via load_expected_identity (5456 / frozen hash) and any
        drift fails closed. A 1-symbol REAL run therefore cannot produce
        COMPLETE_ALL_UNITS;
      - OFFLINE_TEST keeps the injectable small-identity path for tests and
        must be EXPLICIT: execution_context has no implicit default, so a
        real/non-dry-run call cannot silently degrade into OFFLINE_TEST.

    R4A7.2 hardening:
      - execution_context must be exactly "REAL" or "OFFLINE_TEST". None,
        unknown, or arbitrary values -> UNKNOWN_EXECUTION_CONTEXT with no
        checkpoint trust and no provider fetch.
      - COMPLETE_ALL_UNITS is returned only when COMPLETE_N ==
        FULL_SYMBOL_N and FAILED_N == 0 and PENDING_N == 0. When a limit
        stops execution while units remain the status is
        PARTIAL_LIMIT_REACHED (partial != complete); a later resume may
        continue normally.
    """
    if execution_context not in (EXECUTION_CONTEXT_REAL, EXECUTION_CONTEXT_OFFLINE_TEST):
        return {
            "STATUS": "UNKNOWN_EXECUTION_CONTEXT",
            "execution_context": execution_context,
            "reason": "execution_context must be explicitly REAL or "
            "OFFLINE_TEST; no implicit default, no checkpoint trust, "
            "no provider fetch",
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "MARKET_DATA_WRITE": "NO",
        }
    if execution_context == EXECUTION_CONTEXT_REAL:
        authority = adapter_authority_status(
            adapter_version,
            expected_sha=expected_adapter_sha,
            runtime_sha=runtime_adapter_sha,
            execution_mode="REAL",
        )
        if not authority["ADAPTER_AUTHORITY_PASS"]:
            return {
                "STATUS": "ADAPTER_AUTHORITY_FAILED_BEFORE_RESUME",
                "ADAPTER_AUTHORITY_MODE": authority["ADAPTER_AUTHORITY_MODE"],
                "reason": "real execution requires adapter_version == "
                "expected_adapter_sha == runtime_adapter_sha (canonical "
                "40-char lowercase hex); checkpoint is NOT trusted",
                "NETWORK_PROVIDER_DATA_FETCH": "NO",
                "MARKET_DATA_WRITE": "NO",
            }
        if identity is not None:
            return {
                "STATUS": "REAL_IDENTITY_INJECTION_FORBIDDEN",
                "reason": "real execution must load the frozen authoritative "
                "identity; injected identity is test-only",
                "NETWORK_PROVIDER_DATA_FETCH": "NO",
                "MARKET_DATA_WRITE": "NO",
            }
        identity = load_expected_identity(
            root,
            expected_hash=FORMAL_IDENTITY_HASH,
            expected_n=FORMAL_IDENTITY_N,
        )
        if not identity.get("identity_ok"):
            return {
                "STATUS": "REAL_IDENTITY_DRIFT",
                "reason": "authoritative frozen identity not reproducible "
                "(5456 / frozen hash)",
                "EXPECTED_SYMBOL_N": identity.get("EXPECTED_SYMBOL_N"),
                "EXPECTED_SYMBOL_HASH": identity.get("EXPECTED_SYMBOL_HASH"),
                "NETWORK_PROVIDER_DATA_FETCH": "NO",
                "MARKET_DATA_WRITE": "NO",
            }
    full = build_full_query_plan(root, as_of=as_of, window_start=window_start, identity=identity)
    contract = contract_identity(
        as_of=as_of,
        window_start=window_start,
        adapter_authority_sha=adapter_version,
        full_query_plan_hash=full["FULL_QUERY_PLAN_HASH"],
    )
    manifest_path = manifest_path or (staging_root / MANIFEST_FILENAME)
    units_dir = staging_root / UNITS_DIRNAME
    manifest = None
    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        if not manifest_contract_matches(manifest, contract):
            return {
                "STATUS": "CHECKPOINT_CONTRACT_DRIFT",
                "reason": "existing checkpoint contract identity does not match "
                "this run; checkpoint cannot be reused (fail closed)",
                "FULL_SYMBOL_N": full["FULL_SYMBOL_N"],
                "FULL_SYMBOL_HASH": full["FULL_SYMBOL_HASH"],
                "FULL_QUERY_WINDOW_N": full["FULL_QUERY_WINDOW_N"],
                "FULL_QUERY_PLAN_HASH": full["FULL_QUERY_PLAN_HASH"],
            }
    if manifest is None:
        manifest = new_manifest(contract)

    summary = {
        "STATUS": "DRY_RUN_OK" if dry_run else "RUNNING",
        "FULL_SYMBOL_N": full["FULL_SYMBOL_N"],
        "FULL_SYMBOL_HASH": full["FULL_SYMBOL_HASH"],
        "FULL_QUERY_WINDOW_N": full["FULL_QUERY_WINDOW_N"],
        "FULL_QUERY_PLAN_HASH": full["FULL_QUERY_PLAN_HASH"],
        "COMPLETE_N": 0,
        "FAILED_N": 0,
        "PENDING_N": 0,
        "SKIPPED_N": 0,
        "EXECUTED_N": 0,
        "units": {},
    }
    for symbol in full["symbols"]:
        entry = manifest["units"].get(symbol)
        receipt = entry if isinstance(entry, dict) else None
        formal_path = units_dir / f"{symbol}.parquet"
        if unit_complete_and_valid(
            receipt,
            contract=contract,
            formal_path=formal_path,
            expected_symbol=symbol,
            as_of=as_of,
            window_start=window_start,
        ):
            summary["COMPLETE_N"] += 1
            summary["SKIPPED_N"] += 1
            summary["units"][symbol] = {"STATE": "COMPLETE", "skipped": True}
            continue
        if dry_run:
            state = receipt_state(receipt) if receipt else "PENDING"
            summary["units"][symbol] = {"STATE": state, "note": "dry-run, not executed"}
            if state == "FAILED":
                summary["FAILED_N"] += 1
            else:
                summary["PENDING_N"] += 1
            continue
        if provider_fetch is None:
            raise RuntimeError("provider_fetch is required for non-dry-run execution")
        if limit is not None and summary["EXECUTED_N"] >= limit:
            summary["units"][symbol] = {"STATE": "PENDING", "note": "limit reached"}
            summary["PENDING_N"] += 1
            summary["LIMIT_REACHED"] = True
            continue
        fetched_at = _now_utc()
        try:
            result = execute_unit(
                root=root,
                symbol=symbol,
                provider_fetch=provider_fetch,
                adapter_version=adapter_version,
                expected_adapter_sha=expected_adapter_sha,
                runtime_adapter_sha=runtime_adapter_sha,
                fetched_at=fetched_at,
                as_of=as_of,
                window_start=window_start,
            )
            formal_rows = result["formal_rows"]
            frame = formal_frame(formal_rows)
            tmp = units_dir / f".{symbol}.parquet.tmp-{os.getpid()}"
            units_dir.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(tmp)
            # Read-back integrity verification before any receipt is written.
            readback = pl.read_parquet(tmp)
            readback_rows = readback.to_dicts()
            formal_hash = formal_fact_hash(readback_rows)
            staged_formal_hash = staged_formal_content_hash(readback_rows)
            if len(readback_rows) != len(formal_rows):
                raise RuntimeError(f"read-back row count mismatch for {symbol}")
            if formal_hash != formal_fact_hash(formal_rows):
                raise RuntimeError(f"read-back content hash mismatch for {symbol}")
            ok, staged_issues = verify_staged_formal_rows(
                readback_rows,
                expected_symbol=symbol,
                contract=contract,
                as_of=as_of,
            )
            if not ok:
                raise RuntimeError(
                    f"staged formal row integrity failed for {symbol}: "
                    f"{staged_issues[:5]}"
                )
            os.replace(tmp, formal_path)
            unit_window_hash = build_query_plan(
                [symbol], window_start=window_start, as_of=as_of
            )["QUERY_PLAN_HASH"]
            counts = result
            receipt = build_unit_receipt(
                symbol=symbol,
                unit_window_hash=unit_window_hash,
                plan_window_n=int(counts["QUERY_WINDOW_N"]),
                adapter_version=adapter_version,
                required_row_n=int(counts["REQUIRED_ROW_N"]),
                formal_fact_row_n=int(len(formal_rows)),
                missing_required_n=int(counts["MISSING_REQUIRED_N"]),
                provider_suspended_superset_n=int(counts["PROVIDER_SUSPENDED_SUPERSET_N"]),
                unexpected_traded_n=int(counts["UNEXPECTED_TRADED_N"]),
                tradestatus_unknown_n=int(counts["TRADESTATUS_UNKNOWN_N"]),
                identity_failure_n=int(counts["IDENTITY_FAILURE_N"]),
                window_scope_failure_n=int(counts["WINDOW_SCOPE_FAILURE_N"]),
                duplicate_n=int(counts["DUPLICATE_N"]),
                post_asof_n=int(counts["POST_ASOF_N"]),
                invalid_preclose_n=int(counts["INVALID_PRECLOSE_N"]),
                formal_hash=formal_hash,
                staged_formal_hash=staged_formal_hash,
                formal_path=str(formal_path),
                contract=contract,
            )
            manifest["units"][symbol] = receipt
            write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
            summary["EXECUTED_N"] += 1
            summary["COMPLETE_N"] += 1
            summary["units"][symbol] = {"STATE": "COMPLETE", "executed": True}
        except Exception as exc:  # noqa: BLE001 - fail closed with a FAILED unit
            manifest["units"][symbol] = {
                "STATE": "FAILED",
                "symbol": symbol,
                "error": f"{type(exc).__name__}: {exc}",
                "failed_utc": _now_utc(),
            }
            write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
            summary["FAILED_N"] += 1
            summary["units"][symbol] = manifest["units"][symbol]
            # No silent retry: stop on first failed unit.
            summary["STATUS"] = "STOPPED_UNIT_FAILED"
            return summary
    if not dry_run:
        all_complete = bool(
            summary["COMPLETE_N"] == summary["FULL_SYMBOL_N"]
            and summary["FAILED_N"] == 0
            and summary["PENDING_N"] == 0
        )
        if all_complete:
            summary["STATUS"] = "COMPLETE_ALL_UNITS"
        elif summary.get("LIMIT_REACHED"):
            summary["STATUS"] = "PARTIAL_LIMIT_REACHED"
        else:
            summary["STATUS"] = "PARTIAL_INCOMPLETE"
    return summary


def aggregate_full_run(
    *,
    staging_root: Path,
    manifest_path: Path | None = None,
    root: Path | None = None,
    symbols: list[str] | None = None,
    execution_context: str | None = None,
    expected_adapter_sha: str | None = None,
    runtime_adapter_sha: str | None = None,
    as_of: date = AS_OF,
    window_start: date = WINDOW_START,
    event_dates: set[tuple[str, date]] | None = None,
    first_listing_dates: set[tuple[str, date]] | None = None,
    resumption_keys: set[tuple[str, date]] | None = None,
    known_special_keys: set[tuple[str, date]] | None = None,
    bars: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """Final full-run validator: aggregates, NEVER promotes.

    R4A7.1 hardening:
      - REAL execution derives the expected universe from the frozen
        authority (load_expected_identity 5456 / frozen hash via the exact
        frozen contract), never from an arbitrary caller subset. Identity
        injection is test-only (OFFLINE_TEST).
      - authority gate (adapter==expected==runtime, canonical 40-char hex)
        must pass BEFORE any aggregation trust.
      - every expected symbol must have exactly one COMPLETE receipt that
        passes the same resume integrity check (state, symbol, contract,
        parquet exists/readable, row count, content hash).
      - FAILED_N=0 and PENDING_N=0 required; any invalid/missing unit ->
        COVERAGE_COMPLETE=false and PRECLOSE_COMPLETE_CANDIDATE=false;
        missing formal files are never ignored.
      - the frozen WINDOW_BOUNDARY_EDGE key set is computed once from
        authoritative R3 data and passed to BOTH
        verify_window_boundary_rows(...) and verify_clean_normal_parity(...);
        CLEAN_NORMAL excludes WINDOW_BOUNDARY_EDGE.

    Calculates PRECLOSE_COMPLETE_CANDIDATE only; PRECLOSE_COMPLETE stays
    false. Gates that need context the caller did not supply report NOT_RUN
    and the candidate stays false (UNKNOWN != PASS).

    R4A7.2.1 closure:
      - execution_context has NO implicit default (OFFLINE_TEST must be
        explicit). None / omitted / unknown / arbitrary string ->
        UNKNOWN_EXECUTION_CONTEXT with COVERAGE_COMPLETE=false,
        PRECLOSE_COMPLETE_CANDIDATE=false, PRECLOSE_COMPLETE=false and
        MARKET_DATA_WRITE=NO.
      - the context is validated BEFORE load_manifest() and before any unit
        receipt or staged parquet is read, so a corrupt or missing manifest
        is never read/trusted for an unknown context.
    """
    if execution_context not in (EXECUTION_CONTEXT_REAL, EXECUTION_CONTEXT_OFFLINE_TEST):
        return {
            "STATUS": "UNKNOWN_EXECUTION_CONTEXT",
            "execution_context": execution_context,
            "COVERAGE_COMPLETE": False,
            "PRECLOSE_COMPLETE_CANDIDATE": False,
            "PRECLOSE_COMPLETE": False,
            "FULL_MARKET_AUTHORIZED": False,
            "MARKET_DATA_WRITE": "NO",
        }
    manifest_path = manifest_path or (staging_root / MANIFEST_FILENAME)
    manifest = load_manifest(manifest_path)
    units = manifest["units"]
    receipts = [u for u in units.values() if isinstance(u, dict)]

    if execution_context == EXECUTION_CONTEXT_REAL:
        if root is None:
            raise RuntimeError("REAL aggregation requires the authoritative root")
        if expected_adapter_sha is None or runtime_adapter_sha is None:
            raise RuntimeError(
                "REAL aggregation requires expected_adapter_sha and "
                "runtime_adapter_sha for the authority gate"
            )
        authority = adapter_authority_status(
            manifest["contract"].get("ADAPTER_AUTHORITY_SHA"),
            expected_sha=expected_adapter_sha,
            runtime_sha=runtime_adapter_sha,
            execution_mode="REAL",
        )
        if not authority["ADAPTER_AUTHORITY_PASS"]:
            return {
                "STATUS": "ADAPTER_AUTHORITY_FAILED_BEFORE_AGGREGATION",
                "COVERAGE_COMPLETE": False,
                "PRECLOSE_COMPLETE_CANDIDATE": False,
                "PRECLOSE_COMPLETE": False,
                "MARKET_DATA_WRITE": "NO",
            }
        identity = load_expected_identity(
            root,
            expected_hash=FORMAL_IDENTITY_HASH,
            expected_n=FORMAL_IDENTITY_N,
        )
        if not identity.get("identity_ok"):
            return {
                "STATUS": "REAL_IDENTITY_DRIFT",
                "COVERAGE_COMPLETE": False,
                "PRECLOSE_COMPLETE_CANDIDATE": False,
                "PRECLOSE_COMPLETE": False,
                "MARKET_DATA_WRITE": "NO",
            }
        expected_set = set(identity["symbols"])
        if len(expected_set) != FORMAL_IDENTITY_N:
            return {
                "STATUS": "REAL_IDENTITY_N_MISMATCH",
                "expected_n": len(expected_set),
                "COVERAGE_COMPLETE": False,
                "PRECLOSE_COMPLETE_CANDIDATE": False,
                "PRECLOSE_COMPLETE": False,
                "MARKET_DATA_WRITE": "NO",
            }
        full = build_full_query_plan(root, as_of=as_of, window_start=window_start)
        frozen_contract = contract_identity(
            as_of=as_of,
            window_start=window_start,
            adapter_authority_sha=expected_adapter_sha,
            full_query_plan_hash=full["FULL_QUERY_PLAN_HASH"],
        )
    else:
        expected_set = set(symbols) if symbols is not None else set()
        frozen_contract = None

    contract = manifest["contract"]
    contract_exact = bool(
        frozen_contract is None or manifest.get("contract") == frozen_contract
    )

    required_n = 0
    formal_n = 0
    missing_n = 0
    suspended_n = 0
    unexpected_traded_n = 0
    tradestatus_unknown_n = 0
    identity_failure_n = 0
    window_scope_failure_n = 0
    duplicate_n = 0
    post_asof_n = 0
    invalid_preclose_n = 0
    all_formal_rows: list[dict[str, Any]] = []
    units_dir = staging_root / UNITS_DIRNAME

    invalid_missing: list[str] = []
    complete_symbols: set[str] = set()
    failed_symbols: set[str] = set()
    pending_symbols: set[str] = set()
    failed_n = 0
    pending_n = 0
    for expected_symbol in sorted(expected_set):
        candidate_receipts = [
            u
            for u in units.values()
            if isinstance(u, dict) and u.get("symbol") == expected_symbol
        ]
        if len(candidate_receipts) == 0:
            invalid_missing.append(expected_symbol)
            continue
        if len(candidate_receipts) > 1:
            invalid_missing.append(expected_symbol)  # duplicate receipt
            continue
        receipt = candidate_receipts[0]
        if receipt.get("STATE") == "FAILED":
            failed_symbols.add(expected_symbol)
            failed_n += 1
            continue
        if receipt.get("STATE") != "COMPLETE":
            pending_symbols.add(expected_symbol)
            pending_n += 1
            continue
        formal_path = Path(str(receipt.get("formal_path", "")))
        if not formal_path.is_absolute():
            formal_path = units_dir / formal_path.name
        if not unit_complete_and_valid(
            receipt,
            contract=contract,
            formal_path=formal_path,
            expected_symbol=expected_symbol,
            as_of=as_of,
            window_start=window_start,
        ):
            invalid_missing.append(expected_symbol)
            continue
        complete_symbols.add(expected_symbol)
        required_n += int(receipt.get("REQUIRED_ROW_N", 0))
        formal_n += int(receipt.get("FORMAL_FACT_ROW_N", 0))
        missing_n += int(receipt.get("MISSING_REQUIRED_N", 0))
        suspended_n += int(receipt.get("PROVIDER_SUSPENDED_SUPERSET_N", 0))
        unexpected_traded_n += int(receipt.get("UNEXPECTED_TRADED_N", 0))
        tradestatus_unknown_n += int(receipt.get("TRADESTATUS_UNKNOWN_N", 0))
        identity_failure_n += int(receipt.get("IDENTITY_FAILURE_N", 0))
        window_scope_failure_n += int(receipt.get("WINDOW_SCOPE_FAILURE_N", 0))
        duplicate_n += int(receipt.get("DUPLICATE_N", 0))
        post_asof_n += int(receipt.get("POST_ASOF_N", 0))
        invalid_preclose_n += int(receipt.get("INVALID_PRECLOSE_N", 0))
        all_formal_rows.extend(pl.read_parquet(formal_path).to_dicts())
    full_formal_hash = formal_fact_hash(all_formal_rows)

    failed_n = max(failed_n, len(failed_symbols))
    pending_n = max(pending_n, len(pending_symbols))
    coverage_complete = bool(
        expected_set
        and len(complete_symbols) == len(expected_set)
        and not invalid_missing
        and not failed_symbols
        and not pending_symbols
        and missing_n == 0
        and contract_exact
        and formal_n == required_n
    )

    boundary: dict[str, Any] = {"WINDOW_BOUNDARY_PASS": False, "status": "NOT_RUN"}
    sentinel: dict[str, Any] = {"FROZEN_OFFICIAL_SENTINEL_PASS": False, "status": "NOT_RUN"}
    clean_normal: dict[str, Any] = {"NORMAL_FULL_PARITY_PASS": False, "status": "NOT_RUN"}

    window_boundary_keys: set[tuple[str, date]] = set()
    if root is not None and coverage_complete and all_formal_rows:
        required = load_required_keys(
            root, sorted(expected_set), as_of=as_of, window_start=window_start
        )
        list_dates = load_instrument_list_dates(root)
        edges = compute_window_boundary_edges(
            required_keys=required["required_keys"],
            instrument_list_dates=list_dates,
            pre_window_predecessor_symbols=set(required["PRE_WINDOW_PREDECESSOR_SYMBOLS"]),
            window_start=window_start,
        )
        window_boundary_keys = edges["window_boundary_keys"]
        gate = verify_window_boundary_rows(window_boundary_keys, all_formal_rows)
        boundary = {
            k: gate[k]
            for k in (
                "WINDOW_BOUNDARY_REQUIRED_N",
                "WINDOW_BOUNDARY_PRESENT_N",
                "WINDOW_BOUNDARY_VALID_N",
                "WINDOW_BOUNDARY_MISSING_N",
                "WINDOW_BOUNDARY_INVALID_N",
                "WINDOW_BOUNDARY_PASS",
            )
        }
        expected = load_frozen_sentinel_evidence()
        sgate = verify_frozen_sentinels(expected, all_formal_rows)
        sentinel = {
            k: sgate[k]
            for k in (
                "SENTINEL_REQUIRED_N",
                "SENTINEL_PRESENT_N",
                "SENTINEL_EXACT_N",
                "SENTINEL_MISSING_N",
                "SENTINEL_MISMATCH_N",
                "FROZEN_OFFICIAL_SENTINEL_PASS",
            )
        }
        if (
            bars is not None
            and event_dates is not None
            and first_listing_dates is not None
            and resumption_keys is not None
            and known_special_keys is not None
        ):
            cgate = verify_clean_normal_parity(
                formal_rows=all_formal_rows,
                bars=bars,
                event_dates=event_dates,
                first_listing_dates=first_listing_dates,
                resumption_keys=resumption_keys,
                known_special_keys=known_special_keys,
                window_boundary_keys=window_boundary_keys,
            )
            clean_normal = {
                k: cgate[k]
                for k in (
                    "CLEAN_NORMAL_REQUIRED_N",
                    "CLEAN_NORMAL_COMPARABLE_N",
                    "CLEAN_NORMAL_UNCOMPARED_N",
                    "CLEAN_NORMAL_EXACT_N",
                    "CLEAN_NORMAL_MISMATCH_N",
                    "CLEAN_NORMAL_MAX_DIFF",
                    "NORMAL_FULL_PARITY_PASS",
                )
            }
        elif root is None:
            clean_normal = {**clean_normal, "status": "NOT_RUN_NO_ROOT"}
        else:
            clean_normal = {**clean_normal, "status": "SKIPPED_CONTEXT_UNAVAILABLE"}
    elif root is None:
        boundary = {**boundary, "status": "NOT_RUN_NO_ROOT"}
        sentinel = {**sentinel, "status": "NOT_RUN_NO_ROOT"}
        clean_normal = {**clean_normal, "status": "NOT_RUN_NO_ROOT"}
    else:
        boundary = {**boundary, "status": "SKIPPED_INCOMPLETE"}
        sentinel = {**sentinel, "status": "SKIPPED_INCOMPLETE"}
        clean_normal = {**clean_normal, "status": "SKIPPED_INCOMPLETE"}

    candidate = bool(
        coverage_complete
        and formal_n == required_n
        and missing_n == 0
        and unexpected_traded_n == 0
        and tradestatus_unknown_n == 0
        and identity_failure_n == 0
        and window_scope_failure_n == 0
        and duplicate_n == 0
        and post_asof_n == 0
        and invalid_preclose_n == 0
        and boundary.get("WINDOW_BOUNDARY_PASS") is True
        and sentinel.get("FROZEN_OFFICIAL_SENTINEL_PASS") is True
        and clean_normal.get("NORMAL_FULL_PARITY_PASS") is True
        and not invalid_missing
        and failed_n == 0
        and pending_n == 0
        and contract_exact
    )
    return {
        "STATUS": "VALIDATOR_RUN",
        "units_complete_n": len(complete_symbols),
        "units_failed_n": failed_n,
        "units_pending_n": pending_n,
        "invalid_missing_n": len(invalid_missing),
        "REQUIRED_ROW_N": required_n,
        "FORMAL_FACT_ROW_N": formal_n,
        "MISSING_REQUIRED_N": missing_n,
        "PROVIDER_SUSPENDED_SUPERSET_N": suspended_n,
        "UNEXPECTED_TRADED_N": unexpected_traded_n,
        "TRADESTATUS_UNKNOWN_N": tradestatus_unknown_n,
        "IDENTITY_FAILURE_N": identity_failure_n,
        "WINDOW_SCOPE_FAILURE_N": window_scope_failure_n,
        "DUPLICATE_N": duplicate_n,
        "POST_ASOF_N": post_asof_n,
        "INVALID_PRECLOSE_N": invalid_preclose_n,
        "FULL_FORMAL_FACT_HASH": full_formal_hash,
        "WINDOW_BOUNDARY": boundary,
        "SENTINELS": sentinel,
        "CLEAN_NORMAL": clean_normal,
        "COVERAGE_COMPLETE": coverage_complete,
        "CONTRACT_EXACT": contract_exact,
        "PRECLOSE_COMPLETE_CANDIDATE": candidate,
        "PRECLOSE_COMPLETE": False,
        "FULL_MARKET_AUTHORIZED": False,
        "MARKET_DATA_WRITE": "NO",
    }
