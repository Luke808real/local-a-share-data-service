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
) -> dict[str, Any]:
    """Resumable full-universe extraction; production default is dry-run."""
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
        if unit_complete_and_valid(receipt, contract=contract, formal_path=formal_path, expected_symbol=symbol):
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
            if len(readback_rows) != len(formal_rows):
                raise RuntimeError(f"read-back row count mismatch for {symbol}")
            if formal_hash != formal_fact_hash(formal_rows):
                raise RuntimeError(f"read-back content hash mismatch for {symbol}")
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
    if not dry_run and summary["FAILED_N"] == 0:
        summary["STATUS"] = "COMPLETE_ALL_UNITS"
    return summary


def aggregate_full_run(
    *,
    staging_root: Path,
    manifest_path: Path | None = None,
    root: Path | None = None,
    symbols: list[str] | None = None,
    as_of: date = AS_OF,
    window_start: date = WINDOW_START,
    event_dates: set[tuple[str, date]] | None = None,
    first_listing_dates: set[tuple[str, date]] | None = None,
    resumption_keys: set[tuple[str, date]] | None = None,
    known_special_keys: set[tuple[str, date]] | None = None,
    bars: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """Final full-run validator: aggregates, NEVER promotes.

    Calculates PRECLOSE_COMPLETE_CANDIDATE only; PRECLOSE_COMPLETE stays
    false. Full parity/sentinel/boundary gates are computed only when the
    calling context supplies the frozen classification context (production
    full-run context builder is a future audited stage); otherwise those
    gates report NOT_RUN and the candidate stays false (UNKNOWN != PASS).
    """
    manifest_path = manifest_path or (staging_root / MANIFEST_FILENAME)
    manifest = load_manifest(manifest_path)
    units = manifest["units"]
    receipts = [u for u in units.values() if isinstance(u, dict)]
    complete = [u for u in receipts if u.get("STATE") == "COMPLETE"]
    failed = [u for u in receipts if u.get("STATE") == "FAILED"]
    required_n = 0
    formal_n = 0
    missing_n = 0
    suspended_n = 0
    unexpected_traded_n = sum(int(u.get("UNEXPECTED_TRADED_N", 0)) for u in complete)
    tradestatus_unknown_n = sum(int(u.get("TRADESTATUS_UNKNOWN_N", 0)) for u in complete)
    identity_failure_n = sum(int(u.get("IDENTITY_FAILURE_N", 0)) for u in complete)
    window_scope_failure_n = sum(int(u.get("WINDOW_SCOPE_FAILURE_N", 0)) for u in complete)
    duplicate_n = sum(int(u.get("DUPLICATE_N", 0)) for u in complete)
    post_asof_n = sum(int(u.get("POST_ASOF_N", 0)) for u in complete)
    invalid_preclose_n = sum(int(u.get("INVALID_PRECLOSE_N", 0)) for u in complete)
    all_formal_rows: list[dict[str, Any]] = []
    units_dir = staging_root / UNITS_DIRNAME
    for u in complete:
        required_n += int(u.get("REQUIRED_ROW_N", 0))
        formal_n += int(u.get("FORMAL_FACT_ROW_N", 0))
        missing_n += int(u.get("MISSING_REQUIRED_N", 0))
        suspended_n += int(u.get("PROVIDER_SUSPENDED_SUPERSET_N", 0))
        path = Path(str(u.get("formal_path", "")))
        if not path.is_absolute():
            path = units_dir / path.name
        if path.is_file():
            all_formal_rows.extend(pl.read_parquet(path).to_dicts())
    full_formal_hash = formal_fact_hash(all_formal_rows)

    # The expected symbol universe is not stored inside the manifest (the
    # orchestrator keeps identity external); the validator caller must
    # supply it. Without it, coverage can never be proven COMPLETE, so the
    # candidate stays false (UNKNOWN != PASS).
    expected_symbols = set(symbols) if symbols is not None else set()
    complete_symbols = {u.get("symbol") for u in complete}
    coverage_complete = bool(
        expected_symbols
        and complete_symbols == expected_symbols
        and not failed
        and missing_n == 0
    )

    boundary: dict[str, Any] = {"WINDOW_BOUNDARY_PASS": False, "status": "NOT_RUN"}
    sentinel: dict[str, Any] = {"FROZEN_OFFICIAL_SENTINEL_PASS": False, "status": "NOT_RUN"}
    clean_normal: dict[str, Any] = {"NORMAL_FULL_PARITY_PASS": False, "status": "NOT_RUN"}

    def run_boundary() -> dict[str, Any]:
        if root is None:
            return {**boundary, "status": "NOT_RUN_NO_ROOT"}
        if not coverage_complete or not all_formal_rows:
            return {**boundary, "status": "SKIPPED_INCOMPLETE"}
        required = load_required_keys(
            root, expected_symbols, as_of=as_of, window_start=window_start
        )
        list_dates = load_instrument_list_dates(root)
        edges = compute_window_boundary_edges(
            required_keys=required["required_keys"],
            instrument_list_dates=list_dates,
            pre_window_predecessor_symbols=set(required["PRE_WINDOW_PREDECESSOR_SYMBOLS"]),
            window_start=window_start,
        )
        gate = verify_window_boundary_rows(edges["window_boundary_keys"], all_formal_rows)
        return {
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

    def run_sentinel() -> dict[str, Any]:
        if not coverage_complete or not all_formal_rows:
            return {**sentinel, "status": "SKIPPED_INCOMPLETE"}
        expected = load_frozen_sentinel_evidence()
        gate = verify_frozen_sentinels(expected, all_formal_rows)
        return {
            k: gate[k]
            for k in (
                "SENTINEL_REQUIRED_N",
                "SENTINEL_PRESENT_N",
                "SENTINEL_EXACT_N",
                "SENTINEL_MISSING_N",
                "SENTINEL_MISMATCH_N",
                "FROZEN_OFFICIAL_SENTINEL_PASS",
            )
        }

    def run_clean_normal() -> dict[str, Any]:
        if not coverage_complete or not all_formal_rows or bars is None:
            return {**clean_normal, "status": "SKIPPED_INCOMPLETE"}
        if (
            event_dates is None
            or first_listing_dates is None
            or resumption_keys is None
            or known_special_keys is None
        ):
            return {**clean_normal, "status": "SKIPPED_CONTEXT_UNAVAILABLE"}
        gate = verify_clean_normal_parity(
            formal_rows=all_formal_rows,
            bars=bars,
            event_dates=event_dates,
            first_listing_dates=first_listing_dates,
            resumption_keys=resumption_keys,
            known_special_keys=known_special_keys,
            window_boundary_keys=set(),
        )
        return {
            k: gate[k]
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

    boundary = run_boundary()
    sentinel = run_sentinel()
    clean_normal = run_clean_normal()
    candidate = bool(
        coverage_complete
        and boundary.get("WINDOW_BOUNDARY_PASS") is True
        and sentinel.get("FROZEN_OFFICIAL_SENTINEL_PASS") is True
        and clean_normal.get("NORMAL_FULL_PARITY_PASS") is True
        and unexpected_traded_n == 0
        and tradestatus_unknown_n == 0
        and identity_failure_n == 0
        and window_scope_failure_n == 0
        and duplicate_n == 0
        and post_asof_n == 0
        and invalid_preclose_n == 0
    )
    return {
        "STATUS": "VALIDATOR_RUN" if manifest else "MANIFEST_MISSING",
        "units_complete_n": len(complete),
        "units_failed_n": len(failed),
        "units_pending_n": len(receipts) - len(complete) - len(failed),
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
        "PRECLOSE_COMPLETE_CANDIDATE": candidate,
        "PRECLOSE_COMPLETE": False,
        "FULL_MARKET_AUTHORIZED": False,
        "MARKET_DATA_WRITE": "NO",
    }
