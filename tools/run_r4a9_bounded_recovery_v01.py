#!/usr/bin/env python3
"""Recover the two R4A9 units invalidated or blocked by the R3 repair.

The runner is intentionally narrow: it consumes the lineage-safe checkpoint,
executes exactly 002087.SZ and then 300546.SZ through the already-frozen R4A9
adapter, and never considers the remaining 3,315 UNVISITED symbols.  The
provider import is lazy and occurs only after all local authority gates pass.
Canonical daily bars and the old R4A9 staging tree are read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import polars as pl

from rebase_r4a9_checkpoint_lineage_v01 import (
    CURRENT_DAILY_INPUT_MANIFEST_HASH,
    FORMAL_IDENTITY_HASH,
    FORMAL_IDENTITY_N,
    RebaseError,
    build_input_file_manifest,
    canonical_json_bytes,
    load_formal_symbols,
    load_json,
    load_current_symbol_dates,
    parse_date,
    sha256_file,
    validate_resume_input_gate,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
TASK = "R4A9_STATUS_CONFLICT_RECOVERY_WIRING_REEXEC_V01"
BRANCH = "codex/r4a9-status-conflict-recovery-reexec-v01"
BASE_HEAD = "352763992e89e049695aae71153c7edbf65f26de"

NEW_R4A9_STAGE_DIRNAME = "r4a9-preclose-resume-status-conflict-v01"
NEW_CHECKPOINT_SCHEMA_VERSION = "R4A9_STATUS_CONFLICT_RESUME_V01"
CHECKPOINT_REL = Path("staging") / NEW_R4A9_STAGE_DIRNAME / "manifest.json"
RECEIPT_DIRNAME = "recovery_receipts"
UNIT_DIRNAME = "units"

EXPECTED_CHECKPOINT_SHA256 = (
    "6d133a0d140fcc3d8adf2b2b3b5de16982e5741cf539fda710f9ba0abed44282"
)
EXPECTED_DAILY_MANIFEST_HASH = CURRENT_DAILY_INPUT_MANIFEST_HASH
COMPATIBILITY_VERDICT = "BOUNDED_INVALIDATION"

R4A9_CODE_HEAD = "5748318662c0433baf72dec7c368754baf4b27f0"
UPSTREAM_SAFE_ADAPTER_AUTHORITY_SHA = "795b1b8f6b688ecc2e94f85c09d80c365e648920"
OVERLAY_KEY_N = 4
OVERLAY_KEYSET_HASH = "49fd7d316e2a09bbb18f0b840d4a5034f3efb2dbba57e9f60255c7a8910b2663"
OVERLAY_CONTRACT = "R4A_PRECLOSE_PROVEN_STATUS_CONFLICT_OVERLAY_V01"
R4A9_QUERY_PLAN_HASH = (
    "9773875fbae9494bc1d9477cd18633dbccb92112733d9a1077fc3a43bcc38a60"
)
R4A9_QUERY_CONTRACT_VERSION = "R4A_PRECLOSE_V01"
R4A9_SOURCE_VERSION = "baostock-0.9.3"
R4A9_ADAPTER_SOURCE_SHA256 = "52322e254329c2b5fef8fdaf02c16490d9b77498b2dda6e9af98ce8ec128e0fe"
R4A9_OVERLAY_SOURCE_SHA256 = "35546e4e353850a71390213423cbd3301b9093261480be0773e2b940316aed79"
R4A9_ORCHESTRATOR_SOURCE_SHA256 = (
    "8973dfda278a41f820f87b958b1849a501676c1a961190975a6aa46e8ad75fc5"
)
EXTERNAL_R4_REPO = Path("/Users/luke808/ASL-r4a-preclose-status-conflict-runtime-5748318")
EXTERNAL_SRC = EXTERNAL_R4_REPO / "src"
ADAPTER_SOURCE = EXTERNAL_SRC / "ashare_data" / "r4a_preclose_bounded_adapter.py"
OVERLAY_SOURCE = EXTERNAL_SRC / "ashare_data" / "r4a_preclose_status_conflict_overlay.py"
ORCHESTRATOR_SOURCE = EXTERNAL_SRC / "ashare_data" / "r4a7_preclose_full_extraction.py"

RECOVERY_SYMBOLS = ("002087.SZ", "300546.SZ")
REPAIRED_002087_KEYS = (
    "002087.SZ:2024-06-13",
    "002087.SZ:2024-06-14",
)
REPAIRED_300546_KEYS = (
    "300546.SZ:2016-09-29",
    "300546.SZ:2016-10-10",
)
REQUIRED_COUNTERS = (
    "MISSING_REQUIRED_N",
    "UNEXPECTED_TRADED_N",
    "TRADESTATUS_UNKNOWN_N",
    "IDENTITY_FAILURE_N",
    "WINDOW_SCOPE_FAILURE_N",
    "DUPLICATE_N",
    "POST_ASOF_N",
    "INVALID_PRECLOSE_N",
)

REPORT_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"


def _require(condition: bool, code: str, detail: Any | None = None) -> None:
    if not condition:
        raise RebaseError(code if detail is None else f"{code}:{detail}")


def _git_head(path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RebaseError(f"RUNTIME_GIT_HEAD_UNAVAILABLE:{path}") from exc


def _git_clean(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RebaseError(f"RUNTIME_GIT_STATUS_UNAVAILABLE:{path}") from exc
    return result.stdout == ""


def require_base_head(repo_root: Path) -> None:
    actual = _git_head(repo_root)
    _require(actual == BASE_HEAD, "BASE_HEAD_MISMATCH", actual)


def require_recovery_stage_root(data_root: Path, stage_root: Path) -> Path:
    """Guard the exact status-conflict staging root before every write."""
    root = Path(data_root).resolve(strict=True)
    expected = root / "staging" / NEW_R4A9_STAGE_DIRNAME
    requested = Path(stage_root).expanduser()
    _require(requested.is_absolute(), "RECOVERY_STAGE_ROOT_NOT_ABSOLUTE", str(requested))
    _require(requested.resolve(strict=False) == expected, "RECOVERY_STAGE_ROOT_MISMATCH", str(requested))
    _require(expected.is_dir() and not expected.is_symlink(), "RECOVERY_STAGE_ROOT_INVALID", str(expected))
    return expected


def _resolve_external_runtime() -> tuple[Any, Any, Any]:
    _require(EXTERNAL_R4_REPO.is_dir(), "R4_RUNTIME_REPO_MISSING", str(EXTERNAL_R4_REPO))
    _require(_git_head(EXTERNAL_R4_REPO) == R4A9_CODE_HEAD, "R4_RUNTIME_HEAD_MISMATCH")
    _require(_git_clean(EXTERNAL_R4_REPO), "R4_RUNTIME_WORKTREE_DIRTY")
    _require(sha256_file(ADAPTER_SOURCE) == R4A9_ADAPTER_SOURCE_SHA256, "R4_ADAPTER_SOURCE_HASH_MISMATCH")
    _require(sha256_file(OVERLAY_SOURCE) == R4A9_OVERLAY_SOURCE_SHA256, "R4_OVERLAY_SOURCE_HASH_MISMATCH")
    _require(sha256_file(ORCHESTRATOR_SOURCE) == R4A9_ORCHESTRATOR_SOURCE_SHA256, "R4_ORCHESTRATOR_SOURCE_HASH_MISMATCH")
    _require(str(EXTERNAL_SRC) not in sys.path, "R4_RUNTIME_PATH_PRELOADED")
    sys.path.insert(0, str(EXTERNAL_SRC))
    try:
        adapter = importlib.import_module("ashare_data.r4a_preclose_bounded_adapter")
        overlay = importlib.import_module("ashare_data.r4a_preclose_status_conflict_overlay")
        orchestrator = importlib.import_module("ashare_data.r4a7_preclose_full_extraction")
    except Exception as exc:  # pragma: no cover - environment-specific gate
        raise RebaseError("R4_RUNTIME_IMPORT_FAILED") from exc
    _require(sha256_file(Path(adapter.__file__)) == R4A9_ADAPTER_SOURCE_SHA256, "IMPORTED_ADAPTER_HASH_MISMATCH")
    _require(sha256_file(Path(overlay.__file__)) == R4A9_OVERLAY_SOURCE_SHA256, "IMPORTED_OVERLAY_HASH_MISMATCH")
    _require(Path(adapter.__file__).resolve().is_relative_to(EXTERNAL_SRC), "IMPORTED_ADAPTER_PATH_MISMATCH")
    _require(Path(overlay.__file__).resolve().is_relative_to(EXTERNAL_SRC), "IMPORTED_OVERLAY_PATH_MISMATCH")
    _require(sha256_file(Path(orchestrator.__file__)) == R4A9_ORCHESTRATOR_SOURCE_SHA256, "IMPORTED_ORCHESTRATOR_HASH_MISMATCH")
    computed_plan = adapter.build_query_plan(
        ["002087.SZ", "300546.SZ"],
        window_start=adapter.WINDOW_START,
        as_of=adapter.AS_OF,
    )
    _require(computed_plan["QUERY_WINDOW_N"] == 22, "BOUNDED_UNIT_QUERY_WINDOW_COUNT_MISMATCH")
    _require(computed_plan["QUERY_PLAN_HASH"] != R4A9_QUERY_PLAN_HASH, "UNIT_PLAN_UNEXPECTEDLY_EQUALS_FULL_PLAN")
    return adapter, orchestrator, overlay


def _checkpoint_path(data_root: Path) -> Path:
    return data_root.resolve(strict=True) / CHECKPOINT_REL


def _load_lineage_checkpoint(data_root: Path, formal_symbols: set[str]) -> tuple[Path, dict[str, Any], str]:
    root = data_root.resolve(strict=True)
    stage_root = require_recovery_stage_root(root, root / "staging" / NEW_R4A9_STAGE_DIRNAME)
    path = stage_root / "manifest.json"
    _require(path.is_file() and not path.is_symlink(), "LINEAGE_CHECKPOINT_MISSING", str(path))
    checkpoint_sha = sha256_file(path)
    _require(checkpoint_sha == EXPECTED_CHECKPOINT_SHA256, "LINEAGE_CHECKPOINT_HASH_MISMATCH", checkpoint_sha)
    checkpoint = load_json(path)
    _require(checkpoint.get("schema_version") == NEW_CHECKPOINT_SCHEMA_VERSION, "LINEAGE_CHECKPOINT_SCHEMA_MISMATCH")
    _require(checkpoint.get("compatibility_verdict") == COMPATIBILITY_VERDICT, "LINEAGE_COMPATIBILITY_VERDICT_MISMATCH")
    _require(checkpoint.get("daily_input_manifest_hash") == EXPECTED_DAILY_MANIFEST_HASH, "LINEAGE_DAILY_MANIFEST_FIELD_MISMATCH")
    _require(checkpoint.get("formal_identity_n") == FORMAL_IDENTITY_N, "LINEAGE_IDENTITY_COUNT_MISMATCH")
    _require(checkpoint.get("formal_identity_hash") == FORMAL_IDENTITY_HASH, "LINEAGE_IDENTITY_HASH_MISMATCH")
    _require(checkpoint.get("full_query_plan_hash") == R4A9_QUERY_PLAN_HASH, "LINEAGE_QUERY_PLAN_HASH_MISMATCH")
    _require(checkpoint.get("adapter_authority_sha") == R4A9_CODE_HEAD, "LINEAGE_ADAPTER_AUTHORITY_MISMATCH")
    _require(checkpoint.get("resume_adapter_authority_sha") == R4A9_CODE_HEAD, "LINEAGE_RESUME_ADAPTER_AUTHORITY_MISMATCH")
    _require(checkpoint.get("upstream_safe_adapter_authority_sha") == UPSTREAM_SAFE_ADAPTER_AUTHORITY_SHA, "LINEAGE_UPSTREAM_SAFE_ADAPTER_AUTHORITY_MISMATCH")
    _require(checkpoint.get("status_conflict_overlay_contract") == OVERLAY_CONTRACT, "LINEAGE_OVERLAY_CONTRACT_MISMATCH")
    _require(checkpoint.get("status_conflict_overlay_key_n") == OVERLAY_KEY_N, "LINEAGE_OVERLAY_COUNT_MISMATCH")
    _require(checkpoint.get("status_conflict_overlay_keyset_hash") == OVERLAY_KEYSET_HASH, "LINEAGE_OVERLAY_HASH_MISMATCH")
    _require(checkpoint.get("query_contract_version") == R4A9_QUERY_CONTRACT_VERSION, "LINEAGE_QUERY_CONTRACT_MISMATCH")
    units = checkpoint.get("units")
    _require(isinstance(units, dict), "LINEAGE_UNITS_INVALID")
    _require(set(units) == formal_symbols, "LINEAGE_SYMBOL_SCOPE_MISMATCH")
    counts = _state_counts(units)
    _require(
        counts == {
            "SAFE_COMPLETE": 2_139,
            "RECOMPUTE_REQUIRED": 1,
            "RETRY_REQUIRED": 1,
            "UNVISITED": 3_315,
        },
        "LINEAGE_INITIAL_STATE_COUNTS_MISMATCH",
        counts,
    )
    _require(units["002087.SZ"].get("STATE") == "RECOMPUTE_REQUIRED", "002087_INITIAL_STATE_MISMATCH")
    _require(units["002087.SZ"].get("REASON") == "R3_DAILY_REQUIRED_KEYSET_CHANGED", "002087_INITIAL_REASON_MISMATCH")
    _require(units["300546.SZ"].get("STATE") == "RETRY_REQUIRED", "300546_INITIAL_STATE_MISMATCH")
    _require(units["300546.SZ"].get("REASON") == "UPSTREAM_R3_DEFECT_REPAIRED", "300546_INITIAL_REASON_MISMATCH")
    _require(
        all(units[symbol].get("STATE") == "UNVISITED" for symbol in ("600647.SH", "600766.SH", "603133.SH")),
        "REPAIRED_UNVISITED_STATE_CHANGED",
    )
    return path, checkpoint, checkpoint_sha


def _state_counts(units: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in units.values():
        state = str(entry.get("STATE"))
        counts[state] = counts.get(state, 0) + 1
    return counts


def _load_current_authority(data_root: Path) -> tuple[dict[str, Any], set[str], dict[str, set[date]]]:
    manifest = build_input_file_manifest(data_root)
    _require(manifest["INPUT_FILE_N"] == 2_580, "CURRENT_DAILY_INPUT_FILE_N_MISMATCH")
    _require(manifest["INPUT_MANIFEST_HASH"] == EXPECTED_DAILY_MANIFEST_HASH, "CURRENT_DAILY_MANIFEST_DRIFT", manifest["INPUT_MANIFEST_HASH"])
    symbols = load_formal_symbols(data_root)
    target_dates = load_current_symbol_dates(data_root, set(RECOVERY_SYMBOLS))
    _require(len(target_dates["002087.SZ"]) == 2_026, "002087_REQUIRED_ROW_N_MISMATCH", len(target_dates["002087.SZ"]))
    for key in (*REPAIRED_002087_KEYS, *REPAIRED_300546_KEYS):
        symbol, raw_date = key.split(":", 1)
        _require(date.fromisoformat(raw_date) in target_dates[symbol], "REPAIRED_CANONICAL_KEY_MISSING", key)
    return manifest, symbols, target_dates


def _canonical_close(data_root: Path, symbol: str, trade_day: date) -> float:
    root = data_root.resolve(strict=True)
    frame = (
        pl.scan_parquet(str(root / "curated" / "daily_bars" / "**" / "*.parquet"))
        .select(["symbol", "trade_date", "close"])
        .filter((pl.col("symbol") == symbol) & (pl.col("trade_date") == trade_day))
        .collect()
    )
    _require(frame.height == 1, "CANONICAL_CLOSE_NOT_UNIQUE", f"{symbol}:{trade_day}")
    return float(frame["close"][0])


def verify_002087_pre_network_overlay_proof(
    overlay: dict[tuple[str, date], dict[str, Any]], data_root: Path
) -> None:
    key = ("002087.SZ", date(2024, 6, 13))
    evidence = overlay.get(key)
    _require(evidence is not None, "002087_OVERLAY_KEY_MISSING")
    _require(evidence.get("r3_required_key") is True and evidence.get("proven_expected_bar") is True, "002087_OVERLAY_REQUIRED_PROOF_MISSING")
    _require(evidence.get("tushare_trade_status") is True, "002087_OVERLAY_TUSHARE_STATUS_MISMATCH")
    _require(float(evidence.get("fallback_preclose", 0.0)) == 0.17, "002087_OVERLAY_PRECLOSE_MISMATCH")
    _require(evidence.get("predecessor_parity") is True, "002087_OVERLAY_PARITY_MISMATCH")
    _require(_canonical_close(data_root, "002087.SZ", date(2024, 6, 12)) == 0.17, "002087_CANONICAL_20240612_CLOSE_MISMATCH")
    _require(_canonical_close(data_root, "002087.SZ", date(2024, 6, 13)) == 0.16, "002087_CANONICAL_20240613_CLOSE_MISMATCH")


def status_conflict_receipt_metadata(
    symbol: str, overlay: dict[tuple[str, date], dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {
            "symbol": key[0], "trade_date": key[1].isoformat(),
            "primary_source": value["primary_source"], "primary_tradestatus": value["primary_tradestatus"],
            "primary_status_conflict": value["primary_status_conflict"],
            "fallback_source": value["source"], "fallback_preclose": value["fallback_preclose"],
            "predecessor_parity": value["predecessor_parity"],
        }
        for key, value in sorted(overlay.items()) if key[0] == symbol
    ]


def _write_json_atomic(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(canonical_json_bytes(payload))
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RebaseError(f"RECOVERY_WRITE_FAILED:{path}") from exc
    return sha256_file(path)


def _write_parquet_atomic(path: Path, frame: pl.DataFrame) -> str:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(temporary)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RebaseError(f"RECOVERY_PARQUET_WRITE_FAILED:{path}") from exc
    return sha256_file(path)


def enforce_network_scope(symbol: str) -> None:
    _require(symbol in RECOVERY_SYMBOLS, "NETWORK_SYMBOL_SCOPE_VIOLATION", symbol)


def phase2_allowed(phase1_status: str) -> bool:
    """Phase ordering gate: Phase 2 is unreachable after a Phase 1 failure."""

    _require(phase1_status == "PASS", "PHASE_2_BLOCKED_BY_PHASE_1", phase1_status)
    return True


def _build_provider_fetch(
    *,
    provider: Any,
    adapter: Any,
    symbol: str,
    request_records: list[dict[str, Any]],
) -> Callable[[dict[str, Any]], list[dict[str, Any]]]:
    fields = adapter.QUERY_FIELDS

    def fetch(window: dict[str, Any]) -> list[dict[str, Any]]:
        requested_symbol = str(window.get("symbol"))
        enforce_network_scope(requested_symbol)
        _require(requested_symbol == symbol, "PHASE_SYMBOL_SCOPE_VIOLATION", requested_symbol)
        query = window.get("query") or {}
        _require(query.get("fields") == fields, "QUERY_FIELDS_DRIFT")
        _require(query.get("frequency") == adapter.QUERY_FREQUENCY, "QUERY_FREQUENCY_DRIFT")
        _require(query.get("adjustflag") == adapter.QUERY_ADJUSTFLAG, "QUERY_ADJUSTFLAG_DRIFT")
        request_id = f"{symbol}:{window['year']}"
        record: dict[str, Any] = {
            "request_id": request_id,
            "request_order": len(request_records) + 1,
            "symbol": requested_symbol,
            "bs_code": window["bs_code"],
            "start_date": window["start"],
            "end_date": window["end"],
            "fields": fields,
            "frequency": adapter.QUERY_FREQUENCY,
            "adjustflag": adapter.QUERY_ADJUSTFLAG,
        }
        request_records.append(record)
        try:
            raw_rows = provider.query_history_k_data_plus(
                window["bs_code"],
                fields,
                start_date=window["start"],
                end_date=window["end"],
                frequency=adapter.QUERY_FREQUENCY,
                adjustflag=adapter.QUERY_ADJUSTFLAG,
            )
            raw_rows = list(raw_rows)
            record["provider_error_code"] = "0"
            record["raw_row_n"] = len(raw_rows)
            record["raw_sha256"] = hashlib.sha256(canonical_json_bytes(raw_rows)).hexdigest()
            normalized_rows = []
            for row in raw_rows:
                normalized = dict(zip(fields.split(","), row))
                # The frozen adapter uses symbol as its internal identity key;
                # BaoStock's bounded response carries the exchange code.
                normalized["symbol"] = symbol
                normalized_rows.append(normalized)
            return normalized_rows
        except Exception as exc:
            record["provider_error_code"] = "EXCEPTION"
            record["error_type"] = type(exc).__name__
            record["error"] = str(exc)
            raise

    return fetch


def _quality_counters(result: dict[str, Any]) -> dict[str, int]:
    return {field: int(result.get(field, -1)) for field in REQUIRED_COUNTERS}


def _verify_recovery_formal_rows(
    rows: list[dict[str, Any]], *, symbol: str, adapter: Any,
    proven_status_conflicts: dict[tuple[str, date], dict[str, Any]],
) -> None:
    """Keep normal formal provenance strict while admitting one proven fallback."""
    for row in rows:
        trade_day = parse_date(row["trade_date"])
        _require(str(row.get("symbol")) == symbol, "R4_UNIT_FORMAL_SYMBOL_MISMATCH")
        _require(row.get("provider_tradestatus") == 1 and row.get("coverage_status") == "COVERED", "R4_UNIT_FORMAL_COVERAGE_MISMATCH")
        _require(row.get("adapter_version") == R4A9_CODE_HEAD and row.get("query_contract_version") == R4A9_QUERY_CONTRACT_VERSION, "R4_UNIT_FORMAL_RUNTIME_MISMATCH")
        _require(math.isfinite(float(row["preclose"])) and float(row["preclose"]) > 0, "R4_UNIT_FORMAL_PRECLOSE_INVALID")
        key = (symbol, trade_day)
        if key in proven_status_conflicts:
            fallback = proven_status_conflicts[key]
            _require(row.get("source") == fallback["source"] and row.get("source_version") == fallback["source_version"], "R4_UNIT_FALLBACK_PROVENANCE_MISMATCH")
            _require(adapter.display_equal(row["preclose"], fallback["fallback_preclose"]), "R4_UNIT_FALLBACK_PRECLOSE_MISMATCH")
        else:
            _require(row.get("source") == "BAOSTOCK_HISTORY_K_PRECLOSE" and row.get("source_version") == R4A9_SOURCE_VERSION, "R4_UNIT_NORMAL_SOURCE_MISMATCH")


def full_continuation_candidate(phase_1_status: str | None, phase_2_status: str | None) -> bool:
    """Only both successful bounded phases may nominate a full continuation."""
    return phase_1_status == "PASS" and phase_2_status == "PASS"


def _verify_formal_result(
    *,
    adapter: Any,
    orchestrator: Any,
    result: dict[str, Any],
    symbol: str,
    formal_path: Path,
    data_root: Path,
    proven_status_conflicts: dict[tuple[str, date], dict[str, Any]],
) -> dict[str, Any]:
    _require(result.get("STATUS") == "COMPLETE", "R4_UNIT_STATUS_NOT_COMPLETE", result.get("STATUS"))
    _require(result.get("QUALITY_GATE_PASS") is True, "R4_UNIT_QUALITY_GATE_FAILED", symbol)
    counters = _quality_counters(result)
    _require(all(value == 0 for value in counters.values()), "R4_UNIT_QUALITY_COUNTER_NONZERO", counters)
    formal_rows = result.get("formal_rows")
    _require(isinstance(formal_rows, list), "R4_UNIT_FORMAL_ROWS_INVALID", symbol)
    frame = orchestrator.formal_frame(formal_rows)
    _require(frame.columns == orchestrator.FORMAL_COLUMNS, "R4_UNIT_FORMAL_SCHEMA_MISMATCH", symbol)
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    file_sha = _write_parquet_atomic(formal_path, frame)
    readback = pl.read_parquet(formal_path)
    _require(readback.columns == orchestrator.FORMAL_COLUMNS, "R4_UNIT_READBACK_SCHEMA_MISMATCH", symbol)
    readback_rows = readback.to_dicts()
    _require(len(readback_rows) == len(formal_rows), "R4_UNIT_READBACK_ROW_COUNT_MISMATCH", symbol)
    formal_hash = orchestrator.formal_fact_hash(readback_rows)
    staged_hash = orchestrator.staged_formal_content_hash(readback_rows)
    _require(formal_hash == orchestrator.formal_fact_hash(formal_rows), "R4_UNIT_READBACK_FACT_HASH_MISMATCH", symbol)
    _verify_recovery_formal_rows(readback_rows, symbol=symbol, adapter=adapter, proven_status_conflicts=proven_status_conflicts)
    _require(len(readback_rows) == int(result["REQUIRED_ROW_N"]), "R4_UNIT_REQUIRED_FORMAL_COUNT_MISMATCH", symbol)
    _require(len(readback_rows) == int(result["FORMAL_FACT_ROW_N"]), "R4_UNIT_FORMAL_COUNT_MISMATCH", symbol)
    if symbol == "002087.SZ":
        _require(int(result.get("PRIMARY_STATUS_CONFLICT_N", 0)) == 1, "002087_PRIMARY_STATUS_CONFLICT_COUNT_MISMATCH")
        dates = {parse_date(row["trade_date"]): row for row in readback_rows}
        _require(date(2024, 6, 13) in dates, "002087_FORMAL_20240613_MISSING")
        _require(date(2024, 6, 14) in dates, "002087_FORMAL_20240614_MISSING")
        provider_preclose = float(dates[date(2024, 6, 14)]["preclose"])
        canonical_predecessor = _canonical_close(data_root, symbol, date(2024, 6, 13))
        _require(adapter.display_equal(provider_preclose, canonical_predecessor), "002087_PREDECESSOR_PARITY_FAILED")
        fallback = dates[date(2024, 6, 13)]
        _require(fallback["source"] == "TUSHARE_LOCAL_RAW_PRECLOSE_FALLBACK", "002087_FALLBACK_SOURCE_MISMATCH")
        _require(adapter.display_equal(fallback["preclose"], 0.17), "002087_FALLBACK_PRECLOSE_MISMATCH")
        _require(fallback["provider_tradestatus"] == 1 and fallback["coverage_status"] == "COVERED", "002087_FALLBACK_FORMAL_PROVENANCE_MISMATCH")
        _require(proven_status_conflicts[("002087.SZ", date(2024, 6, 13))]["primary_tradestatus"] == 0, "002087_OVERLAY_PRIMARY_STATUS_MISMATCH")
    else:
        _require(int(result.get("PRIMARY_STATUS_CONFLICT_N", 0)) == 0, "NONMATCHING_OVERLAY_FALLBACK_DETECTED")
    return {
        "formal_path": str(formal_path),
        "FORMAL_FILE_SHA256": file_sha,
        "FORMAL_FACT_ROW_N": len(readback_rows),
        "FORMAL_FACT_HASH": formal_hash,
        "STAGED_FORMAL_CONTENT_HASH": staged_hash,
        "QUALITY_COUNTERS": counters,
        "REQUIRED_ROW_N": int(result["REQUIRED_ROW_N"]),
        "QUERY_WINDOW_N": int(result["QUERY_WINDOW_N"]),
        "QUERY_PLAN_HASH": result["QUERY_PLAN_HASH"],
    }


def _new_complete_entry(
    *,
    symbol: str,
    prior_entry: dict[str, Any],
    result: dict[str, Any],
    artifact: dict[str, Any],
    recovery_kind: str,
    raw_receipt_path: Path,
    normalized_receipt_path: Path,
    raw_receipt_sha: str,
    normalized_receipt_sha: str,
) -> dict[str, Any]:
    return {
        "STATE": "COMPLETE",
        "symbol": symbol,
        "UPSTREAM_STATE": prior_entry.get("STATE"),
        "UPSTREAM_REASON": prior_entry.get("REASON"),
        "RECOVERY_KIND": recovery_kind,
        "formal_path": artifact["formal_path"],
        "FORMAL_FILE_SHA256": artifact["FORMAL_FILE_SHA256"],
        "FORMAL_FACT_ROW_N": artifact["FORMAL_FACT_ROW_N"],
        "FORMAL_FACT_HASH": artifact["FORMAL_FACT_HASH"],
        "STAGED_FORMAL_CONTENT_HASH": artifact["STAGED_FORMAL_CONTENT_HASH"],
        "REQUIRED_ROW_N": artifact["REQUIRED_ROW_N"],
        "unit_query_window_n": artifact["QUERY_WINDOW_N"],
        "unit_query_plan_hash": artifact["QUERY_PLAN_HASH"],
        "adapter_version": R4A9_CODE_HEAD,
        "contract": {
            "MANIFEST_SCHEMA_VERSION": "R4A7_PRECLOSE_V01",
            "AS_OF": "2026-08-17",
            "WINDOW_START": "2016-01-01",
            "PRIMARY_SOURCE": "BAOSTOCK_HISTORY_K_PRECLOSE",
            "SOURCE_VERSION": R4A9_SOURCE_VERSION,
            "QUERY_CONTRACT_VERSION": R4A9_QUERY_CONTRACT_VERSION,
            "QUERY_FIELDS": "date,code,preclose,tradestatus",
            "QUERY_FREQUENCY": "d",
            "QUERY_ADJUSTFLAG": "3",
            "CNEQUITY_PIN": "a18ee0484dfb0801650175471724def3228b8a17",
            "FORMAL_IDENTITY_N": FORMAL_IDENTITY_N,
            "FORMAL_IDENTITY_HASH": FORMAL_IDENTITY_HASH,
            "ADAPTER_AUTHORITY_SHA": R4A9_CODE_HEAD,
            "FULL_QUERY_PLAN_HASH": R4A9_QUERY_PLAN_HASH,
        },
        **{field: int(result[field]) for field in REQUIRED_COUNTERS},
        "PROVIDER_SUSPENDED_SUPERSET_N": int(result["PROVIDER_SUSPENDED_SUPERSET_N"]),
        "PRIMARY_STATUS_CONFLICT_N": int(result.get("PRIMARY_STATUS_CONFLICT_N", 0)),
        "provider_raw_receipt_path": str(raw_receipt_path),
        "provider_raw_receipt_sha256": raw_receipt_sha,
        "normalized_receipt_path": str(normalized_receipt_path),
        "normalized_receipt_sha256": normalized_receipt_sha,
        "completion_utc": datetime.now(timezone.utc).isoformat(),
    }


def _execute_symbol(
    *,
    adapter: Any,
    orchestrator: Any,
    data_root: Path,
    stage_root: Path,
    checkpoint: dict[str, Any],
    symbol: str,
    recovery_kind: str,
    reason: str,
    proven_status_conflicts: dict[tuple[str, date], dict[str, Any]],
    progress: Callable[[str], None] | None,
) -> dict[str, Any]:
    enforce_network_scope(symbol)
    prior_entry = checkpoint["units"][symbol]
    request_records: list[dict[str, Any]] = []
    provider = adapter.BaostockSessionProvider()
    result: dict[str, Any] | None = None
    provider_error: Exception | None = None
    try:
        provider.login()
        provider_fetch = _build_provider_fetch(
            provider=provider,
            adapter=adapter,
            symbol=symbol,
            request_records=request_records,
        )
        result = adapter.run_bounded_adapter(
            root=data_root,
            symbols=[symbol],
            provider_fetch=provider_fetch,
            dry_run=False,
            adapter_version=R4A9_CODE_HEAD,
            expected_adapter_sha=R4A9_CODE_HEAD,
            runtime_adapter_sha=R4A9_CODE_HEAD,
            proven_status_conflicts=proven_status_conflicts,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            as_of=adapter.AS_OF,
            window_start=adapter.WINDOW_START,
        )
    except Exception as exc:
        provider_error = exc
    finally:
        try:
            provider.close()
        except Exception as exc:
            if provider_error is None:
                provider_error = exc

    raw_receipt = {
        "schema_version": "R4A9_BOUNDED_RECOVERY_RAW_RECEIPT_V01",
        "symbol": symbol,
        "provider": "BAOSTOCK_HISTORY_K_PRECLOSE",
        "source_version": R4A9_SOURCE_VERSION,
        "request_records": request_records,
        "raw_row_n": sum(int(record.get("raw_row_n", 0)) for record in request_records),
        "network_request_n": len(request_records),
        "provider_failure_n": sum(record.get("provider_error_code") != "0" for record in request_records),
    }
    raw_receipt_path = stage_root / RECEIPT_DIRNAME / f"{symbol}.raw.json"
    raw_receipt_sha = _write_json_atomic(raw_receipt_path, raw_receipt)
    _require(raw_receipt_sha == sha256_file(raw_receipt_path), "RAW_RECEIPT_HASH_RECHECK_FAILED", symbol)

    if provider_error is not None or result is None:
        failure = {
            "schema_version": "R4A9_BOUNDED_RECOVERY_PHASE_RECEIPT_V01",
            "symbol": symbol,
            "status": "FAILED",
            "reason": reason,
            "provider_error_type": type(provider_error).__name__ if provider_error else None,
            "provider_error": str(provider_error) if provider_error else "NO_RESULT",
            "request_records": request_records,
            "raw_receipt_path": str(raw_receipt_path),
            "raw_receipt_sha256": raw_receipt_sha,
            "network_request_n": len(request_records),
        }
        failure_path = stage_root / RECEIPT_DIRNAME / f"{symbol}.phase-failure.json"
        failure_sha = _write_json_atomic(failure_path, failure)
        return {
            "status": "FAILED",
            "symbol": symbol,
            "result": result,
            "error": failure,
            "raw_receipt_path": raw_receipt_path,
            "raw_receipt_sha256": raw_receipt_sha,
            "phase_receipt_path": failure_path,
            "phase_receipt_sha256": failure_sha,
            "request_records": request_records,
        }

    normalized_payload = {
        "schema_version": "R4A9_BOUNDED_RECOVERY_NORMALIZED_RECEIPT_V01",
        "symbol": symbol,
        "status": result.get("STATUS"),
        "quality_gate_pass": result.get("QUALITY_GATE_PASS"),
        "required_row_n": int(result.get("REQUIRED_ROW_N", -1)),
        "formal_fact_row_n": int(result.get("FORMAL_FACT_ROW_N", -1)),
        "quality_counters": _quality_counters(result),
        "query_window_n": int(result.get("QUERY_WINDOW_N", -1)),
        "query_plan_hash": result.get("QUERY_PLAN_HASH"),
        "primary_status_conflict_n": int(result.get("PRIMARY_STATUS_CONFLICT_N", 0)),
        "status_conflict_audit": [row for row in result.get("audit_summary", []) if row.get("issue") == "PRIMARY_STATUS_CONFLICT"],
        "status_conflict_overlay": status_conflict_receipt_metadata(symbol, proven_status_conflicts),
    }
    normalized_receipt_path = stage_root / RECEIPT_DIRNAME / f"{symbol}.normalized.json"
    normalized_receipt_sha = _write_json_atomic(normalized_receipt_path, normalized_payload)
    formal_path = stage_root / UNIT_DIRNAME / f"{symbol}.parquet"
    try:
        artifact = _verify_formal_result(
            adapter=adapter,
            orchestrator=orchestrator,
            result=result,
            symbol=symbol,
            formal_path=formal_path,
            data_root=data_root,
            proven_status_conflicts=proven_status_conflicts,
        )
    except Exception as exc:
        failure = {
            "schema_version": "R4A9_BOUNDED_RECOVERY_PHASE_RECEIPT_V01",
            "symbol": symbol,
            "status": "FAILED",
            "reason": "FORMAL_RESULT_VALIDATION_FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "raw_receipt_path": str(raw_receipt_path),
            "raw_receipt_sha256": raw_receipt_sha,
            "normalized_receipt_path": str(normalized_receipt_path),
            "normalized_receipt_sha256": normalized_receipt_sha,
            "network_request_n": len(request_records),
        }
        failure_path = stage_root / RECEIPT_DIRNAME / f"{symbol}.phase-failure.json"
        failure_sha = _write_json_atomic(failure_path, failure)
        return {
            "status": "FAILED",
            "symbol": symbol,
            "result": result,
            "error": failure,
            "raw_receipt_path": raw_receipt_path,
            "raw_receipt_sha256": raw_receipt_sha,
            "normalized_receipt_path": normalized_receipt_path,
            "normalized_receipt_sha256": normalized_receipt_sha,
            "phase_receipt_path": failure_path,
            "phase_receipt_sha256": failure_sha,
            "request_records": request_records,
        }

    phase = {
        "schema_version": "R4A9_BOUNDED_RECOVERY_PHASE_RECEIPT_V01",
        "symbol": symbol,
        "status": "READY_FOR_CHECKPOINT_TRANSITION",
        "reason": reason,
        "recovery_kind": recovery_kind,
        "raw_receipt_path": str(raw_receipt_path),
        "raw_receipt_sha256": raw_receipt_sha,
        "normalized_receipt_path": str(normalized_receipt_path),
        "normalized_receipt_sha256": normalized_receipt_sha,
        "formal_path": artifact["formal_path"],
        "formal_file_sha256": artifact["FORMAL_FILE_SHA256"],
        "formal_fact_hash": artifact["FORMAL_FACT_HASH"],
        "staged_formal_content_hash": artifact["STAGED_FORMAL_CONTENT_HASH"],
        "required_row_n": artifact["REQUIRED_ROW_N"],
        "formal_fact_row_n": artifact["FORMAL_FACT_ROW_N"],
        "quality_counters": artifact["QUALITY_COUNTERS"],
        "network_request_n": len(request_records),
    }
    if progress:
        progress(f"{symbol} provider/adapter PASS; {len(request_records)} requests; formal rows={artifact['FORMAL_FACT_ROW_N']}")
    return {
        "status": "PASS",
        "symbol": symbol,
        "result": result,
        "artifact": artifact,
        "raw_receipt_path": raw_receipt_path,
        "raw_receipt_sha256": raw_receipt_sha,
        "normalized_receipt_path": normalized_receipt_path,
        "normalized_receipt_sha256": normalized_receipt_sha,
        "phase": phase,
        "request_records": request_records,
    }


def _update_checkpoint(
    *,
    checkpoint_path: Path,
    checkpoint: dict[str, Any],
    expected_before_sha: str,
    data_root: Path,
) -> tuple[str, str]:
    require_recovery_stage_root(data_root, data_root.resolve(strict=True) / "staging" / NEW_R4A9_STAGE_DIRNAME)
    before = sha256_file(checkpoint_path)
    _require(before == expected_before_sha, "CHECKPOINT_BEFORE_SHA_MISMATCH", before)
    after = _write_json_atomic(checkpoint_path, checkpoint)
    _require(after == sha256_file(checkpoint_path), "CHECKPOINT_AFTER_SHA_RECHECK_FAILED")
    return before, after


def _append_checkpoint_phase(
    checkpoint: dict[str, Any],
    *,
    symbol: str,
    phase_n: int,
    outcome: dict[str, Any],
) -> None:
    history = checkpoint.setdefault("recovery_history", [])
    history.append(
        {
            "phase": phase_n,
            "symbol": symbol,
            "status": "COMPLETE",
            "recovery_kind": outcome["phase"]["recovery_kind"],
            "formal_path": outcome["artifact"]["formal_path"],
            "formal_file_sha256": outcome["artifact"]["FORMAL_FILE_SHA256"],
            "formal_fact_hash": outcome["artifact"]["FORMAL_FACT_HASH"],
            "staged_formal_content_hash": outcome["artifact"]["STAGED_FORMAL_CONTENT_HASH"],
        }
    )
    checkpoint["last_recovery_phase"] = phase_n
    checkpoint["last_recovery_status"] = "COMPLETE"
    checkpoint["last_recovery_symbol"] = symbol
    checkpoint["last_recovery_receipt_path"] = str(outcome["raw_receipt_path"].parent / f"{symbol}.phase.json")


def _make_phase_receipt(
    *,
    stage_root: Path,
    phase_n: int,
    outcome: dict[str, Any],
    checkpoint_before_sha: str,
    checkpoint_after_sha: str,
) -> tuple[Path, str]:
    payload = dict(outcome["phase"])
    payload.update(
        {
            "phase": phase_n,
            "status": "COMPLETE",
            "checkpoint_before_sha256": checkpoint_before_sha,
            "checkpoint_after_sha256": checkpoint_after_sha,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    path = stage_root / RECEIPT_DIRNAME / f"{outcome['symbol']}.phase.json"
    return path, _write_json_atomic(path, payload)


def _base_report(
    *,
    start_checkpoint_sha: str,
    end_checkpoint_sha: str,
    daily_manifest_hash: str,
    phase_1: dict[str, Any],
    phase_2: dict[str, Any],
    network_records: list[dict[str, Any]],
    final_checkpoint: dict[str, Any],
    checkpoint_mutated: bool,
    r4_result_write_executed: bool,
) -> dict[str, Any]:
    units = final_checkpoint["units"]
    counts = _state_counts(units)
    phase_1_result = phase_1.get("result") or {}
    phase_2_result = phase_2.get("result") or {}
    if phase_1.get("status") != "PASS":
        author_status = "FAIL_CLOSED_PHASE_1"
    elif phase_2.get("status") != "PASS":
        author_status = "FAIL_CLOSED_PHASE_2"
    else:
        author_status = "PASS_PENDING_SOL_AUDIT"
    return {
        "TASK": TASK,
        "AUTHOR_STATUS": author_status,
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "START_CHECKPOINT_HASH": start_checkpoint_sha,
        "END_CHECKPOINT_HASH": end_checkpoint_sha,
        "DAILY_INPUT_MANIFEST_HASH": daily_manifest_hash,
        "COMPATIBILITY_VERDICT": COMPATIBILITY_VERDICT,
        "RUNTIME_ADAPTER_AUTHORITY_SHA": R4A9_CODE_HEAD,
        "RUNTIME_QUERY_PLAN_HASH": R4A9_QUERY_PLAN_HASH,
        "QUERY_CONTRACT_VERSION": R4A9_QUERY_CONTRACT_VERSION,
        "PRIMARY_SOURCE": "BAOSTOCK_HISTORY_K_PRECLOSE",
        "SOURCE_VERSION": R4A9_SOURCE_VERSION,
        "PHASE_1_STATUS": phase_1.get("status"),
        "PHASE_1_SYMBOL": "002087.SZ",
        "PHASE_1_REQUIRED_ROW_N": phase_1_result.get("REQUIRED_ROW_N"),
        "PHASE_1_FORMAL_ROW_N": phase_1_result.get("FORMAL_FACT_ROW_N"),
        "PHASE_1_FORMAL_FILE_SHA256": (phase_1.get("artifact") or {}).get("FORMAL_FILE_SHA256"),
        "PHASE_1_FORMAL_FACT_HASH": (phase_1.get("artifact") or {}).get("FORMAL_FACT_HASH"),
        "PHASE_1_STAGED_FORMAL_CONTENT_HASH": (phase_1.get("artifact") or {}).get("STAGED_FORMAL_CONTENT_HASH"),
        "PHASE_1_STATUS_DETAIL": phase_1_result.get("STATUS"),
        "PHASE_1_QUALITY_COUNTERS": _quality_counters(phase_1_result) if phase_1_result else None,
        "PHASE_1_PRIMARY_STATUS_CONFLICT_N": int(phase_1_result.get("PRIMARY_STATUS_CONFLICT_N", 0)),
        "PHASE_1_20240613_PRESENT": phase_1.get("status") == "PASS",
        "PHASE_1_20240614_PREDECESSOR_PARITY": phase_1.get("status") == "PASS",
        "PHASE_2_STATUS": phase_2.get("status"),
        "PHASE_2_SYMBOL": "300546.SZ",
        "PHASE_2_REQUIRED_ROW_N": phase_2_result.get("REQUIRED_ROW_N"),
        "PHASE_2_FORMAL_ROW_N": phase_2_result.get("FORMAL_FACT_ROW_N"),
        "PHASE_2_FORMAL_FILE_SHA256": (phase_2.get("artifact") or {}).get("FORMAL_FILE_SHA256"),
        "PHASE_2_FORMAL_FACT_HASH": (phase_2.get("artifact") or {}).get("FORMAL_FACT_HASH"),
        "PHASE_2_STAGED_FORMAL_CONTENT_HASH": (phase_2.get("artifact") or {}).get("STAGED_FORMAL_CONTENT_HASH"),
        "PHASE_2_STATUS_DETAIL": phase_2_result.get("STATUS"),
        "PHASE_2_QUALITY_COUNTERS": _quality_counters(phase_2_result) if phase_2_result else None,
        "002087_20240613_PRESENT": phase_1.get("status") == "PASS",
        "002087_20240613_SOURCE": "TUSHARE_LOCAL_RAW_PRECLOSE_FALLBACK" if phase_1.get("status") == "PASS" else None,
        "002087_20240613_PRECLOSE": 0.17 if phase_1.get("status") == "PASS" else None,
        "002087_20240613_PRIMARY_TRADESTATUS": 0 if phase_1.get("status") == "PASS" else None,
        "002087_20240614_PREDECESSOR_PARITY": phase_1.get("status") == "PASS",
        "300546_20160929_PRESENT": phase_2.get("status") == "PASS",
        "300546_20161010_PRESENT": phase_2.get("status") == "PASS",
        "OLD_R3_REQUIRED_KEY_MISMATCH_RESOLVED": phase_2.get("status") == "PASS" and phase_2_result.get("UNEXPECTED_TRADED_N") == 0,
        "SAFE_COMPLETE_N": counts.get("SAFE_COMPLETE", 0),
        "RECOVERED_COMPLETE_N": counts.get("COMPLETE", 0),
        "FINAL_COMPLETE_N": counts.get("SAFE_COMPLETE", 0) + counts.get("COMPLETE", 0),
        "RECOMPUTE_REQUIRED_N": counts.get("RECOMPUTE_REQUIRED", 0),
        "RETRY_REQUIRED_N": counts.get("RETRY_REQUIRED", 0),
        "UNVISITED_N": counts.get("UNVISITED", 0),
        "STATE_TOTAL_N": sum(counts.values()),
        "NETWORK_SYMBOL_N": len({record["symbol"] for record in network_records}),
        "NETWORK_SYMBOLS": sorted({record["symbol"] for record in network_records}),
        "NETWORK_REQUEST_N": len(network_records),
        "NETWORK_PROVIDER_DATA_FETCH": "YES" if network_records else "NO",
        "CANONICAL_WRITE_EXECUTED": False,
        "CANONICAL_BYTES_MUTATED": False,
        "OLD_R4A9_CHECKPOINT_MUTATED": False,
        "OLD_R4A9_ARTIFACT_DELETED_N": 0,
        "R3_DATA_MUTATED": False,
        "R4_RESULT_WRITE_EXECUTED": r4_result_write_executed,
        "CHECKPOINT_MUTATED": checkpoint_mutated,
        "UNVISITED_EXECUTION_N": 0,
        "FULL_R4A9_CONTINUATION_CANDIDATE": full_continuation_candidate(phase_1.get("status"), phase_2.get("status")),
        "FULL_R4A9_CONTINUATION_AUTHORIZED": False,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "FACTS_READY": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
        "PHASE_2_EXECUTED_ONLY_AFTER_PHASE_1_PASS": True,
        "NO_THIRD_SYMBOL_REQUEST": all(record["symbol"] in RECOVERY_SYMBOLS for record in network_records),
    }


def _write_report(repo_root: Path, report: dict[str, Any]) -> None:
    report_path = repo_root / "reports" / "implementation" / REPORT_NAME
    markdown_path = repo_root / "reports" / "implementation" / REPORT_MD_NAME
    _write_json_atomic(report_path, report)
    phase_lines = [
        f"# {TASK}",
        "",
        "Bounded R4A9 recovery executed only for 002087.SZ then 300546.SZ.",
        "",
        f"- BASE_HEAD: `{report['BASE_HEAD']}`",
        f"- START_CHECKPOINT_HASH: `{report['START_CHECKPOINT_HASH']}`",
        f"- END_CHECKPOINT_HASH: `{report['END_CHECKPOINT_HASH']}`",
        f"- DAILY_INPUT_MANIFEST_HASH: `{report['DAILY_INPUT_MANIFEST_HASH']}`",
        f"- PHASE_1_STATUS: `{report['PHASE_1_STATUS']}`; required/formal `{report['PHASE_1_REQUIRED_ROW_N']}/{report['PHASE_1_FORMAL_ROW_N']}`",
        f"- PHASE_2_STATUS: `{report['PHASE_2_STATUS']}`; required/formal `{report['PHASE_2_REQUIRED_ROW_N']}/{report['PHASE_2_FORMAL_ROW_N']}`",
        f"- FINAL_COMPLETE_N: `{report['FINAL_COMPLETE_N']}`",
        f"- RECOMPUTE_REQUIRED_N: `{report['RECOMPUTE_REQUIRED_N']}`",
        f"- RETRY_REQUIRED_N: `{report['RETRY_REQUIRED_N']}`",
        f"- UNVISITED_N: `{report['UNVISITED_N']}`",
        f"- NETWORK_SYMBOLS: `{', '.join(report['NETWORK_SYMBOLS'])}`",
        f"- NETWORK_REQUEST_N: `{report['NETWORK_REQUEST_N']}`",
        "",
        "## Safety",
        "",
        f"- CANONICAL_WRITE_EXECUTED: `{report['CANONICAL_WRITE_EXECUTED']}`",
        f"- CANONICAL_BYTES_MUTATED: `{report['CANONICAL_BYTES_MUTATED']}`",
        f"- OLD_R4A9_CHECKPOINT_MUTATED: `{report['OLD_R4A9_CHECKPOINT_MUTATED']}`",
        f"- R3_DATA_MUTATED: `{report['R3_DATA_MUTATED']}`",
        f"- UNVISITED_EXECUTION_N: `{report['UNVISITED_EXECUTION_N']}`",
        f"- FULL_R4A9_CONTINUATION_CANDIDATE: `{report['FULL_R4A9_CONTINUATION_CANDIDATE']}`",
        f"- FULL_R4A9_CONTINUATION_AUTHORIZED: `{report['FULL_R4A9_CONTINUATION_AUTHORIZED']}`",
        f"- PRECLOSE_COMPLETE: `{report['PRECLOSE_COMPLETE']}`",
        f"- FACTS_READY: `{report['FACTS_READY']}`",
        "",
        "Phase 2 was not eligible until Phase 1 passed. The remaining 3,315 symbols remain UNVISITED.",
    ]
    markdown_path.write_text("\n".join(phase_lines) + "\n", encoding="utf-8")


def run_recovery(
    *,
    repo_root: Path = REPO_ROOT,
    data_root: Path = DATA_ROOT_DEFAULT,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve(strict=True)
    data_root = Path(data_root).expanduser()
    require_base_head(repo_root)
    stage_root = require_recovery_stage_root(data_root, data_root.resolve(strict=True) / "staging" / NEW_R4A9_STAGE_DIRNAME)
    pre_manifest, formal_symbols, current_dates = _load_current_authority(data_root)
    checkpoint_path, checkpoint, start_checkpoint_sha = _load_lineage_checkpoint(data_root, formal_symbols)
    validate_resume_input_gate(checkpoint, pre_manifest["INPUT_MANIFEST_HASH"])
    prior_checkpoint_path = data_root.resolve(strict=True) / "staging" / "r4a9-preclose-resume-post-r3-repair-v01" / "manifest.json"
    prior_checkpoint_sha = sha256_file(prior_checkpoint_path)
    _require(prior_checkpoint_sha == checkpoint["upstream_checkpoint_sha256"], "UPSTREAM_CHECKPOINT_DRIFT", prior_checkpoint_sha)
    old_checkpoint_path = data_root.resolve(strict=True) / "staging" / "r4a9-preclose-real-full-extraction-v01" / "manifest.json"
    old_checkpoint_sha = sha256_file(old_checkpoint_path)
    prior_checkpoint = load_json(prior_checkpoint_path)
    _require(old_checkpoint_sha == prior_checkpoint.get("upstream_checkpoint_sha256"), "OLD_HISTORICAL_CHECKPOINT_DRIFT", old_checkpoint_sha)
    _require(_git_head(EXTERNAL_R4_REPO) == R4A9_CODE_HEAD, "R4_RUNTIME_HEAD_MISMATCH")

    # Importing the frozen R4 modules is still pre-provider: BaoStock is lazy
    # in the wrapper and login is not called until _execute_symbol below.
    adapter, orchestrator, overlay_module = _resolve_external_runtime()
    _require(adapter.FORMAL_IDENTITY_N == FORMAL_IDENTITY_N, "R4_RUNTIME_IDENTITY_COUNT_MISMATCH")
    _require(adapter.FORMAL_IDENTITY_HASH == FORMAL_IDENTITY_HASH, "R4_RUNTIME_IDENTITY_HASH_MISMATCH")
    _require(adapter.QUERY_FIELDS == "date,code,preclose,tradestatus", "R4_RUNTIME_QUERY_FIELDS_MISMATCH")
    _require(adapter.QUERY_FREQUENCY == "d", "R4_RUNTIME_QUERY_FREQUENCY_MISMATCH")
    _require(adapter.QUERY_ADJUSTFLAG == "3", "R4_RUNTIME_QUERY_ADJUSTFLAG_MISMATCH")
    proven_status_conflicts = overlay_module.load_proven_traded_status_conflicts(EXTERNAL_R4_REPO, data_root)
    _require(len(proven_status_conflicts) == OVERLAY_KEY_N, "RUNTIME_OVERLAY_COUNT_MISMATCH")
    _require(overlay_module.keyset_hash(set(proven_status_conflicts)) == OVERLAY_KEYSET_HASH, "RUNTIME_OVERLAY_HASH_MISMATCH")
    verify_002087_pre_network_overlay_proof(proven_status_conflicts, data_root)

    # These dates are loaded before the first provider login and are retained
    # for explicit report anchors.
    _require(len(current_dates["002087.SZ"]) == 2_026, "002087_REQUIRED_ROW_N_MISMATCH")
    _require(all(date.fromisoformat(key.split(":", 1)[1]) in current_dates[key.split(":", 1)[0]] for key in REPAIRED_002087_KEYS), "002087_CANONICAL_ANCHOR_GATE_FAILED")
    _require(all(date.fromisoformat(key.split(":", 1)[1]) in current_dates[key.split(":", 1)[0]] for key in REPAIRED_300546_KEYS), "300546_CANONICAL_ANCHOR_GATE_FAILED")

    phase_1 = _execute_symbol(
        adapter=adapter,
        orchestrator=orchestrator,
        data_root=data_root,
        stage_root=stage_root,
        checkpoint=checkpoint,
        symbol="002087.SZ",
        recovery_kind="RECOMPUTE_AFTER_R3_KEYSET_CHANGE",
        reason="R3_DAILY_REQUIRED_KEYSET_CHANGED",
        proven_status_conflicts=proven_status_conflicts,
        progress=progress,
    )
    network_records = list(phase_1.get("request_records", []))
    checkpoint_mutated = False
    r4_result_write_executed = bool(phase_1.get("artifact"))
    phase_2: dict[str, Any]
    end_checkpoint_sha = start_checkpoint_sha

    if phase_1["status"] != "PASS":
        phase_2 = {
            "status": "NOT_EXECUTED",
            "symbol": "300546.SZ",
            "result": None,
            "request_records": [],
        }
        failure_manifest = build_input_file_manifest(data_root)
        _require(failure_manifest == pre_manifest, "DAILY_MANIFEST_CHANGED_DURING_FAILED_PHASE_1")
        _require(sha256_file(prior_checkpoint_path) == prior_checkpoint_sha, "UPSTREAM_CHECKPOINT_MUTATED_DURING_FAILED_PHASE_1")
        _require(sha256_file(old_checkpoint_path) == old_checkpoint_sha, "OLD_R4A9_CHECKPOINT_MUTATED_DURING_FAILED_PHASE_1")
        final_checkpoint = load_json(checkpoint_path)
        report = _base_report(
            start_checkpoint_sha=start_checkpoint_sha,
            end_checkpoint_sha=end_checkpoint_sha,
            daily_manifest_hash=pre_manifest["INPUT_MANIFEST_HASH"],
            phase_1=phase_1,
            phase_2=phase_2,
            network_records=network_records,
            final_checkpoint=final_checkpoint,
            checkpoint_mutated=checkpoint_mutated,
            r4_result_write_executed=r4_result_write_executed,
        )
        report["DAILY_MANIFEST_STABLE_DURING_RECOVERY"] = True
        report["OLD_R4A9_CHECKPOINT_HASH"] = old_checkpoint_sha
        report["STOP_REASON"] = "PHASE_1_FAILED_PHASE_2_NOT_EXECUTED"
        _write_report(repo_root, report)
        return report

    # Recheck the lineage hard gate immediately before mutating the new
    # checkpoint and before permitting Phase 2.
    live_manifest_after_phase_1 = build_input_file_manifest(data_root)
    validate_resume_input_gate(checkpoint, live_manifest_after_phase_1["INPUT_MANIFEST_HASH"])
    _require(live_manifest_after_phase_1 == pre_manifest, "DAILY_MANIFEST_CHANGED_DURING_PHASE_1")
    checkpoint = load_json(checkpoint_path)
    _require(sha256_file(checkpoint_path) == start_checkpoint_sha, "CHECKPOINT_CHANGED_BEFORE_PHASE_1_TRANSITION")
    checkpoint["units"]["002087.SZ"] = _new_complete_entry(
        symbol="002087.SZ",
        prior_entry=checkpoint["units"]["002087.SZ"],
        result=phase_1["result"],
        artifact=phase_1["artifact"],
        recovery_kind="RECOMPUTE_AFTER_R3_KEYSET_CHANGE",
        raw_receipt_path=phase_1["raw_receipt_path"],
        normalized_receipt_path=phase_1["normalized_receipt_path"],
        raw_receipt_sha=phase_1["raw_receipt_sha256"],
        normalized_receipt_sha=phase_1["normalized_receipt_sha256"],
    )
    _append_checkpoint_phase(checkpoint, symbol="002087.SZ", phase_n=1, outcome=phase_1)
    checkpoint["state_counts"] = _state_counts(checkpoint["units"])
    checkpoint["recompute_required_n"] = 0
    checkpoint["retry_required_n"] = 1
    checkpoint["unvisited_n"] = 3_315
    checkpoint["safe_reuse_complete_n"] = 2_139
    _, phase_1_checkpoint_sha = _update_checkpoint(
        checkpoint_path=checkpoint_path,
        checkpoint=checkpoint,
        expected_before_sha=start_checkpoint_sha,
        data_root=data_root,
    )
    checkpoint_mutated = True
    phase_1_receipt_path, phase_1_receipt_sha = _make_phase_receipt(
        stage_root=stage_root,
        phase_n=1,
        outcome=phase_1,
        checkpoint_before_sha=start_checkpoint_sha,
        checkpoint_after_sha=phase_1_checkpoint_sha,
    )
    phase_1["phase_receipt_path"] = phase_1_receipt_path
    phase_1["phase_receipt_sha256"] = phase_1_receipt_sha

    # Phase 2 starts from the durable Phase-1 checkpoint and gets its own
    # manifest gate before any second-symbol provider request.
    phase2_allowed(phase_1["status"])
    phase_2_checkpoint = load_json(checkpoint_path)
    phase_2_checkpoint_sha = sha256_file(checkpoint_path)
    _require(phase_2_checkpoint["units"]["300546.SZ"].get("STATE") == "RETRY_REQUIRED", "300546_PHASE_2_STATE_MISMATCH")
    validate_resume_input_gate(phase_2_checkpoint, build_input_file_manifest(data_root)["INPUT_MANIFEST_HASH"])
    phase_2 = _execute_symbol(
        adapter=adapter,
        orchestrator=orchestrator,
        data_root=data_root,
        stage_root=stage_root,
        checkpoint=phase_2_checkpoint,
        symbol="300546.SZ",
        recovery_kind="RETRY_AFTER_UPSTREAM_R3_REPAIR",
        reason="UPSTREAM_R3_DEFECT_REPAIRED",
        proven_status_conflicts=proven_status_conflicts,
        progress=progress,
    )
    network_records.extend(phase_2.get("request_records", []))
    r4_result_write_executed = r4_result_write_executed or bool(phase_2.get("artifact"))

    if phase_2["status"] == "PASS":
        live_manifest_after_phase_2 = build_input_file_manifest(data_root)
        validate_resume_input_gate(phase_2_checkpoint, live_manifest_after_phase_2["INPUT_MANIFEST_HASH"])
        _require(live_manifest_after_phase_2 == pre_manifest, "DAILY_MANIFEST_CHANGED_DURING_PHASE_2")
        phase_2_checkpoint = load_json(checkpoint_path)
        _require(sha256_file(checkpoint_path) == phase_2_checkpoint_sha, "CHECKPOINT_CHANGED_BEFORE_PHASE_2_TRANSITION")
        phase_2_checkpoint["units"]["300546.SZ"] = _new_complete_entry(
            symbol="300546.SZ",
            prior_entry=phase_2_checkpoint["units"]["300546.SZ"],
            result=phase_2["result"],
            artifact=phase_2["artifact"],
            recovery_kind="RETRY_AFTER_UPSTREAM_R3_REPAIR",
            raw_receipt_path=phase_2["raw_receipt_path"],
            normalized_receipt_path=phase_2["normalized_receipt_path"],
            raw_receipt_sha=phase_2["raw_receipt_sha256"],
            normalized_receipt_sha=phase_2["normalized_receipt_sha256"],
        )
        _append_checkpoint_phase(phase_2_checkpoint, symbol="300546.SZ", phase_n=2, outcome=phase_2)
        phase_2_checkpoint["state_counts"] = _state_counts(phase_2_checkpoint["units"])
        phase_2_checkpoint["recompute_required_n"] = 0
        phase_2_checkpoint["retry_required_n"] = 0
        phase_2_checkpoint["unvisited_n"] = 3_315
        phase_2_checkpoint["safe_reuse_complete_n"] = 2_139
        _, end_checkpoint_sha = _update_checkpoint(
            checkpoint_path=checkpoint_path,
            checkpoint=phase_2_checkpoint,
            expected_before_sha=phase_2_checkpoint_sha,
            data_root=data_root,
        )
        phase_2_receipt_path, phase_2_receipt_sha = _make_phase_receipt(
            stage_root=stage_root,
            phase_n=2,
            outcome=phase_2,
            checkpoint_before_sha=phase_2_checkpoint_sha,
            checkpoint_after_sha=end_checkpoint_sha,
        )
        phase_2["phase_receipt_path"] = phase_2_receipt_path
        phase_2["phase_receipt_sha256"] = phase_2_receipt_sha
        final_checkpoint = load_json(checkpoint_path)
    else:
        end_checkpoint_sha = phase_2_checkpoint_sha
        final_checkpoint = load_json(checkpoint_path)

    # Final local-only immutability and state gates.
    _require(sha256_file(prior_checkpoint_path) == prior_checkpoint_sha, "UPSTREAM_CHECKPOINT_MUTATED")
    _require(sha256_file(old_checkpoint_path) == old_checkpoint_sha, "OLD_R4A9_CHECKPOINT_MUTATED")
    final_manifest = build_input_file_manifest(data_root)
    _require(final_manifest == pre_manifest, "DAILY_MANIFEST_CHANGED_AFTER_RECOVERY")
    final_counts = _state_counts(final_checkpoint["units"])
    _require(final_counts.get("UNVISITED", 0) == 3_315, "UNVISITED_COUNT_CHANGED", final_counts)
    _require(all(final_checkpoint["units"][symbol].get("STATE") == "UNVISITED" for symbol in ("600647.SH", "600766.SH", "603133.SH")), "REPAIRED_UNVISITED_NOT_PRESERVED")
    _require(all(record["symbol"] in RECOVERY_SYMBOLS for record in network_records), "THIRD_SYMBOL_NETWORK_REQUEST_DETECTED")
    report = _base_report(
        start_checkpoint_sha=start_checkpoint_sha,
        end_checkpoint_sha=end_checkpoint_sha,
        daily_manifest_hash=final_manifest["INPUT_MANIFEST_HASH"],
        phase_1=phase_1,
        phase_2=phase_2,
        network_records=network_records,
        final_checkpoint=final_checkpoint,
        checkpoint_mutated=checkpoint_mutated,
        r4_result_write_executed=r4_result_write_executed,
    )
    report["PHASE_1_RECEIPT_PATH"] = str(phase_1.get("phase_receipt_path")) if phase_1.get("phase_receipt_path") else None
    report["PHASE_1_RECEIPT_SHA256"] = phase_1.get("phase_receipt_sha256")
    report["PHASE_2_RECEIPT_PATH"] = str(phase_2.get("phase_receipt_path")) if phase_2.get("phase_receipt_path") else None
    report["PHASE_2_RECEIPT_SHA256"] = phase_2.get("phase_receipt_sha256")
    report["OLD_CHECKPOINT_HASH"] = old_checkpoint_sha
    report["DAILY_MANIFEST_STABLE_DURING_RECOVERY"] = True
    report["FULL_R4A9_CONTINUATION_CANDIDATE"] = full_continuation_candidate(phase_1.get("status"), phase_2.get("status"))
    report["FULL_R4A9_CONTINUATION_AUTHORIZED"] = False
    report["R4A9_RESUME_AUTHORIZED"] = False
    _write_report(repo_root, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    args = parser.parse_args(argv)
    try:
        report = run_recovery(
            repo_root=args.repo_root,
            data_root=args.data_root,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
        )
    except RebaseError as exc:
        print(f"FAIL_CLOSED:{exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report.get("PHASE_1_STATUS") == "PASS" and report.get("PHASE_2_STATUS") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
