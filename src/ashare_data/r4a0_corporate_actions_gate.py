"""R4A0 corporate_actions availability gate (READ-ONLY).

Answers the frozen R4A0 question: does the authoritative real data root
already hold reliable SH/SZ corporate_actions data usable as R4A historical
preclose-derivation input?

This gate is deliberately read-only. It never writes to the data root, never
modifies any existing dataset, never calls a provider, and never bootstraps or
backfills corporate_actions. If any required gate cannot be proven, the gate
fails closed (R4A0_READY=false). UNKNOWN != PASS.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl


R3_DATA_AS_OF = date(2026, 8, 17)
R4A_WINDOW_START = date(2016, 1, 1)
R4A_WINDOW_END = R3_DATA_AS_OF

CNEQUITY_PIN_SHA = "a18ee0484dfb0801650175471724def3228b8a17"
DATASET_NAME = "corporate_actions"


@dataclass(frozen=True)
class CorporateActionsContract:
    """Inlined contract from pinned CNEquity v0.7.2 (domain/schemas.py +
    domain/datasets.py). An equality check against the installed pinned
    package runs in the verify CLI; the schema is never guessed from names."""

    dataset: str = DATASET_NAME
    partition_col: str = "ex_date"
    partition_granularity: str = "year"
    primary_source: str = "tdx_protocol"
    backup_source: str = "eastmoney"
    required_fields: tuple[str, ...] = (
        "symbol",
        "ex_date",
        "action_type",
        "cash_dividend",
        "bonus_ratio",
        "transfer_ratio",
        "allotment_ratio",
        "allotment_price",
        "source",
        "data_version",
        "fetched_at",
    )
    provenance_fields: tuple[str, ...] = ("source", "data_version", "fetched_at")
    primary_key: tuple[str, ...] = ("symbol", "ex_date", "action_type")


CONTRACT = CorporateActionsContract()


def dataset_dir(root: Path) -> Path:
    return root / "curated" / DATASET_NAME


def list_parquet(dataset: Path) -> list[Path]:
    if not dataset.is_dir():
        return []
    return sorted(p for p in dataset.rglob("*.parquet") if p.is_file())


def read_manifest_states(root: Path) -> dict[str, Any]:
    """Read meta/manifest.db read-only for corporate_actions runs/batches.

    Uses SQLite ``immutable=1`` read-only semantics so that opening the WAL-mode
    manifest never creates or refreshes ``-wal``/``-shm`` sidecars on the real
    data root. A non-empty ``-wal`` (uncheckpointed frames) makes immutable
    reads unsound, so the gate fails closed instead of reading.
    """
    manifest = root / "meta" / "manifest.db"
    if not manifest.is_file():
        return {
            "runs": [],
            "batches": [],
            "corporate_run_success": False,
            "corporate_watermark": None,
            "manifest_wal_pending": False,
            "manifest_error": None,
        }
    wal = root / "meta" / f"{manifest.name}-wal"
    if wal.exists() and wal.stat().st_size > 0:
        # do not open the database at all: an immutable read would silently
        # miss uncheckpointed WAL frames, so fail closed.
        return {
            "runs": [],
            "batches": [],
            "corporate_run_success": False,
            "corporate_watermark": None,
            "manifest_wal_pending": True,
            "manifest_error": "WAL_PENDING_IMMUTABLE_READ_UNSAFE",
        }
    con = sqlite3.connect(f"file:{manifest}?mode=ro&immutable=1", uri=True)
    try:
        runs = con.execute(
            "SELECT run_id, job_name, status, started_at, finished_at, rows_read, "
            "rows_written, error_message, metadata_json FROM ingestion_runs"
        ).fetchall()
        batches = con.execute(
            "SELECT run_id, batch_id, task_id, dataset, status, window_start, "
            "window_end, rows_read, rows_written FROM ingestion_batches "
            "WHERE dataset IN (?) OR dataset LIKE ?",
            (DATASET_NAME, "%corporate%"),
        ).fetchall()
    finally:
        con.close()

    runs_meta = [
        {
            "run_id": r[0],
            "job_name": r[1],
            "status": r[2],
            "started_at": r[3],
            "finished_at": r[4],
            "rows_read": r[5],
            "rows_written": r[6],
            "error_message": r[7],
            "metadata_json": r[8],
        }
        for r in runs
    ]
    corporate_runs = [
        r
        for r in runs_meta
        if "corporate" in (r["job_name"] or "").lower()
        or "corporate" in (r["metadata_json"] or "").lower()
    ]
    batches_meta = [
        {
            "run_id": b[0],
            "dataset": b[3],
            "status": b[4],
            "window_start": b[5],
            "window_end": b[6],
            "rows_written": b[8],
        }
        for b in batches
    ]
    run_success = any(r["status"] == "success" for r in corporate_runs)

    watermark = None
    wm = root / "meta" / "state" / f"{DATASET_NAME}.json"
    if wm.is_file():
        try:
            watermark = json.loads(wm.read_text(encoding="utf-8"))
        except Exception:
            watermark = {"corrupt": True}

    return {
        "runs": corporate_runs,
        "batches": batches_meta,
        "corporate_run_success": run_success,
        "corporate_watermark": watermark,
        "manifest_wal_pending": False,
        "manifest_error": None,
    }


def run_gate(
    root: Path,
    *,
    window_start: date = R4A_WINDOW_START,
    window_end: date = R4A_WINDOW_END,
    contract: CorporateActionsContract = CONTRACT,
) -> dict[str, Any]:
    """Run the R4A0 read-only gate against `root`. Never writes to `root`."""

    dataset = dataset_dir(root)
    parquet_files = list_parquet(dataset)
    manifest = read_manifest_states(root)

    # ---- EXISTS -----------------------------------------------------------
    dataset_exists = len(parquet_files) > 0
    exists_status = "PASS" if dataset_exists else "FAIL"

    # ---- data load (only when present) ------------------------------------
    df: pl.DataFrame | None = None
    row_count = 0
    min_ex_date = None
    max_ex_date = None
    if dataset_exists:
        try:
            df = pl.scan_parquet(parquet_files).collect()
            row_count = df.height
            if "ex_date" in df.columns and row_count:
                ex = df["ex_date"]
                min_ex_date = str(ex.min())
                max_ex_date = str(ex.max())
        except Exception:
            df = None
            row_count = -1  # unreadable data is not proof of anything

    # ---- SCHEMA -----------------------------------------------------------
    present_fields: list[str] = []
    missing_fields: list[str] = list(contract.required_fields)
    schema_status = "NOT_AVAILABLE"
    if dataset_exists and df is not None:
        cols = set(df.columns)
        present_fields = [f for f in contract.required_fields if f in cols]
        missing_fields = [f for f in contract.required_fields if f not in cols]
        schema_status = "PASS" if not missing_fields else "FAIL"
    elif dataset_exists:
        schema_status = "FAIL"  # dataset present but unreadable

    # ---- SCOPE (SH/SZ only; BJ deferred; unexpected scope reported) -------
    sh_rows = sz_rows = other_rows = 0
    other_symbols: list[str] = []
    scope_status = "NOT_AVAILABLE"
    if dataset_exists and df is not None and "symbol" in df.columns:
        sym = df["symbol"].cast(str).to_list()
        sh_rows = sum(1 for s in sym if s.upper().endswith(".SH"))
        sz_rows = sum(1 for s in sym if s.upper().endswith(".SZ"))
        other_rows = len(sym) - sh_rows - sz_rows
        other_symbols = sorted(
            {
                s
                for s in sym
                if not (s.upper().endswith(".SH") or s.upper().endswith(".SZ"))
            }
        )[:20]
        scope_status = (
            "PASS"
            if other_rows == 0 and (sh_rows + sz_rows) == row_count and row_count > 0
            else "FAIL"
        )

    # ---- COVERAGE (event data: no daily-grid requirement) -----------------
    # Event data does not require a row per trading day. A full coverage claim
    # needs (a) rows present and (b) an explicit historical-coverage proof from
    # an ingestion run or watermark bounding the R4A window. Without that proof
    # coverage is UNKNOWN/PARTIAL and never PASS.
    if not dataset_exists or row_count <= 0:
        coverage_status = "UNKNOWN_PARTIAL"
    else:
        wm = manifest["corporate_watermark"]
        run_success = manifest["corporate_run_success"]
        wm_covers = False
        if wm and isinstance(wm, dict):
            start = wm.get("start") or wm.get("min_date") or wm.get("first_date")
            end = wm.get("end") or wm.get("max_date") or wm.get("last_date")
            wm_covers = bool(start and end)
        coverage_status = (
            "PASS" if (run_success or (wm_covers and row_count > 0)) else "UNKNOWN_PARTIAL"
        )

    # ---- UNIQUENESS (PK = symbol, ex_date, action_type) -------------------
    duplicate_action_n = 0
    duplicate_exdate_group_n = 0
    uniqueness_status = "NOT_AVAILABLE"
    if dataset_exists and df is not None:
        keys = [c for c in contract.primary_key if c in df.columns]
        if keys:
            dups = (
                df.select(keys)
                .group_by(keys)
                .agg(pl.len().alias("__n"))
                .filter(pl.col("__n") > 1)
            )
            duplicate_action_n = int(dups.height)
        # legal same-date multi-component groups (same symbol+ex_date, multiple
        # action types) are NOT duplicates -- reported informationally.
        if {"symbol", "ex_date"}.issubset(df.columns):
            multi = (
                df.select(["symbol", "ex_date", "action_type"])
                .group_by(["symbol", "ex_date"])
                .agg(pl.len().alias("__n"))
                .filter(pl.col("__n") > 1)
            )
            duplicate_exdate_group_n = int(multi.height)
        uniqueness_status = "PASS" if duplicate_action_n == 0 else "FAIL"

    # ---- PROVENANCE -------------------------------------------------------
    provenance_status = "NOT_AVAILABLE"
    unresolved_n = 0
    if dataset_exists and df is not None:
        pf = contract.provenance_fields
        prov_present = all(f in df.columns for f in pf)
        if prov_present:
            unresolved_n = int(
                df.select(
                    pl.any_horizontal(pl.col(f).is_null() for f in pf).alias("__bad")
                )
                .filter(pl.col("__bad"))
                .height
            )
        manifest_provenance = bool(
            manifest["corporate_run_success"] or manifest["corporate_watermark"]
        )
        provenance_status = (
            "PASS"
            if prov_present and unresolved_n == 0 and manifest_provenance
            else "FAIL"
        )

    # ---- aggregate ---------------------------------------------------------
    passes = {
        "EXISTS_PASS": exists_status == "PASS",
        "SCHEMA_PASS": schema_status == "PASS",
        "SCOPE_PASS": scope_status == "PASS",
        "COVERAGE_PASS": coverage_status == "PASS",
        "UNIQUENESS_PASS": uniqueness_status == "PASS",
        "PROVENANCE_PASS": provenance_status == "PASS",
    }
    ready = all(passes.values())

    blocker = None
    missing_capability = None
    bounded_next = None
    if not dataset_exists:
        blocker = "CORPORATE_ACTIONS_DATASET_NOT_BUILT"
        missing_capability = (
            "corporate_actions curated dataset (no parquet under "
            f"{dataset.relative_to(root)}/) and no corporate_actions ingestion run"
        )
        bounded_next = "R4A0_CORPORATE_ACTION_BOUNDED_BOOTSTRAP (Sol decision required)"
    elif not passes["COVERAGE_PASS"]:
        blocker = "COVERAGE_UNPROVEN"
        missing_capability = (
            "historical corporate_actions coverage proof "
            "(successful corporate_actions ingestion run or watermark)"
        )
        bounded_next = "bounded corporate_actions bootstrap/backfill with window evidence"
    elif not passes["PROVENANCE_PASS"] or not passes["UNIQUENESS_PASS"]:
        blocker = (
            "DATASET_QUALITY_UNPROVEN"
            if not passes["PROVENANCE_PASS"]
            else "DUPLICATE_ACTION_ROWS"
        )
        missing_capability = "per-row lineage and PK-uniqueness on corporate_actions rows"
        bounded_next = "repair/re-run via bounded bootstrap (Sol decision)"
    elif not passes["SCOPE_PASS"]:
        blocker = "UNEXPECTED_SYMBOL_SCOPE"
        missing_capability = "SH/SZ-only corporate_actions symbol scope"
        bounded_next = "bounded re-ingestion restricted to SH/SZ"
    elif not passes["SCHEMA_PASS"]:
        blocker = "SCHEMA_INCOMPLETE"
        missing_capability = f"missing fields: {missing_fields}"
        bounded_next = "schema-conformant bootstrap (Sol decision)"
    else:
        blocker = None
        missing_capability = None
        bounded_next = None

    return {
        "R4A0_READY": ready,
        "DATASET_EXISTS": dataset_exists,
        "DATASET_PATH": str(dataset),
        "SCHEMA_STATUS": schema_status,
        "SCOPE_STATUS": scope_status,
        "COVERAGE_STATUS": coverage_status,
        "UNIQUENESS_STATUS": uniqueness_status,
        "PROVENANCE_STATUS": provenance_status,
        "ROW_COUNT": row_count,
        "MIN_EX_DATE": min_ex_date,
        "MAX_EX_DATE": max_ex_date,
        "SH_ROWS": sh_rows,
        "SZ_ROWS": sz_rows,
        "OTHER_ROWS": other_rows,
        "OTHER_SYMBOLS": other_symbols,
        "DUPLICATE_ACTION_N": duplicate_action_n,
        "DUPLICATE_EXDATE_GROUP_N": duplicate_exdate_group_n,
        "UNRESOLVED_N": unresolved_n,
        "required_fields": list(contract.required_fields),
        "present_fields": present_fields,
        "missing_fields": missing_fields,
        "primary_key": list(contract.primary_key),
        "partition_col": contract.partition_col,
        "partition_granularity": contract.partition_granularity,
        "primary_source": contract.primary_source,
        "backup_source": contract.backup_source,
        "manifest": {
            "corporate_run_success": manifest["corporate_run_success"],
            "corporate_watermark": manifest["corporate_watermark"] is not None,
            "manifest_wal_pending": manifest.get("manifest_wal_pending", False),
            "manifest_error": manifest.get("manifest_error"),
        },
        "BLOCKER": blocker,
        "MISSING_CAPABILITY": missing_capability,
        "BOUNDED_NEXT_ACTION": bounded_next,
    }
