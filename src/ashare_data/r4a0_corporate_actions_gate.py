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

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl


R3_DATA_AS_OF = date(2026, 8, 17)
R4A_WINDOW_START = date(2016, 1, 1)
R4A_WINDOW_END = R3_DATA_AS_OF

CNEQUITY_PIN_SHA = "a18ee0484dfb0801650175471724def3228b8a17"
DATASET_NAME = "corporate_actions"

# Frozen R3 formal SH/SZ identity reference (r3-identity-receipt.json).
FORMAL_IDENTITY_N = 5456
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"


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


def _safe_json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


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
            "SELECT run_id, batch_id, task_id, dataset, status, symbols_json, "
            "window_start, window_end, rows_read, rows_written FROM ingestion_batches "
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
            "metadata_parsed": _safe_json(r[8]),
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
            "batch_id": b[1],
            "task_id": b[2],
            "dataset": b[3],
            "status": b[4],
            "symbols_json": b[5],
            "window_start": b[6],
            "window_end": b[7],
            "rows_written": b[9],
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


def collect_coverage_intervals(manifest: dict[str, Any]) -> list[tuple[date, date]]:
    """Trusted successful coverage intervals only, normalized to [start, end].

    Evidence safety: failed / warning / incomplete evidence is never used to
    prove completeness. Only these count:
      * successful corporate_actions ingestion batch window_start/window_end
      * successful corporate_actions run metadata backfill_scope.start/end
      * an authoritative corporate_actions watermark (non-corrupt)
    """
    intervals: list[tuple[date, date]] = []

    for b in manifest.get("batches", []):
        if b.get("status") != "success":
            continue
        s = _parse_date(b.get("window_start"))
        e = _parse_date(b.get("window_end"))
        if s is not None and e is not None and s <= e:
            intervals.append((s, e))

    for r in manifest.get("runs", []):
        if r.get("status") != "success":
            continue
        meta = r.get("metadata_parsed")
        scope = (meta or {}).get("backfill_scope") if isinstance(meta, dict) else {}
        s = _parse_date(scope.get("start"))
        e = _parse_date(scope.get("end") or scope.get("to") or scope.get("cutoff"))
        if s is not None and e is not None and s <= e:
            intervals.append((s, e))

    wm = manifest.get("corporate_watermark")
    if isinstance(wm, dict) and wm.get("corrupt") is not True:
        ws = _parse_date(wm.get("start") or wm.get("min_date") or wm.get("first_date"))
        we = _parse_date(wm.get("end") or wm.get("max_date") or wm.get("last_date"))
        if ws is not None and we is not None and ws <= we:
            intervals.append((ws, we))

    return intervals


def merge_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Merge overlapping / immediately-adjacent intervals.

    Adjacent means the next interval starts no later than the previous end's
    next calendar day, so there is no gap day between them. This is stricter
    than min(start)/max(end) and still admits exact-boundary unions.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: list[tuple[date, date]] = []
    cur_s, cur_e = ordered[0]
    for s, e in ordered[1:]:
        if s <= cur_e or (s - cur_e).days <= 1:
            cur_e = max(cur_e, e)
        else:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    merged.append((cur_s, cur_e))
    return merged


def gaps_in_window(
    merged: list[tuple[date, date]],
    window_start: date,
    window_end: date,
) -> list[dict[str, str]]:
    """Calendar-day gaps inside the requested window, between merged spans."""
    gaps: list[dict[str, str]] = []
    for (a_s, a_e), (b_s, b_e) in zip(merged, merged[1:]):
        gap_s = a_e + timedelta(days=1)
        gap_e = b_s - timedelta(days=1)
        if gap_s > gap_e:
            continue
        gs = max(gap_s, window_start)
        ge = min(gap_e, window_end)
        if gs <= ge:
            gaps.append({"start": str(gs), "end": str(ge)})
    return gaps


def contract_required(cc: dict[str, Any]) -> bool:
    """Mandatory upstream validation: schema AND source AND exact pin."""
    return bool(
        cc.get("SCHEMA_MATCH") is True
        and cc.get("SOURCE_MATCH") is True
        and cc.get("PIN_MATCH") is True
    )


def identity_hash_for(symbols: list[str]) -> str:
    """Reproduce the frozen R3 identity hash: sha256 of the sorted symbol
    list as a compact JSON array (identical to r3_daily identity_hash)."""
    return hashlib.sha256(
        json.dumps(sorted(symbols), separators=(",", ":")).encode()
    ).hexdigest()


def load_expected_identity(
    root: Path,
    *,
    injected: list[str] | None = None,
    expected_hash: str | None = None,
    expected_n: int | None = None,
) -> dict[str, Any]:
    """Load the frozen R3 formal SH/SZ identity as the expected symbol scope.

    Default source: unique symbols of the authoritative R3 curated
    `daily_bars`, whose sorted-compact-json SHA-256 reproduces the frozen
    r3-identity-receipt `formal_identity_hash` (no provider call, no newly
    invented identity semantics; historical delisted SH/SZ are included).
    Any mismatch against the frozen reference is FAIL CLOSED.
    """
    if injected is not None:
        symbols = sorted(set(injected))
        source = "INJECTED"
    else:
        scan = pl.scan_parquet(str(root / "curated/daily_bars/**/*.parquet"))
        syms = scan.select("symbol").unique().collect()["symbol"].cast(str).to_list()
        symbols = sorted(set(syms))
        source = (
            "CURATED_DAILY_BARS_UNIQUE_SYMBOLS "
            "(R3 formal SH/SZ identity, verified against "
            "r3-identity-receipt formal_identity_hash)"
        )
    actual_hash = identity_hash_for(symbols)
    n = len(symbols)
    n_ok = (expected_n is None) or (n == expected_n)
    hash_ok = (expected_hash is None) or (actual_hash == expected_hash)
    ok = bool(n_ok and hash_ok)
    return {
        "IDENTITY_STATUS": "PASS" if ok else "FAIL",
        "EXPECTED_SYMBOL_N": n,
        "EXPECTED_SYMBOL_HASH": actual_hash,
        "IDENTITY_SOURCE": source,
        "identity_ok": ok,
        "symbols": symbols,
    }


def _safe_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else []
    except Exception:
        return []


def symbol_evidence_chunks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Successful corporate_actions chunks carrying an explicit
    queried-symbol scope (symbols_json) and a window.

    Evidence safety: failed / warning / incomplete receipts are not admitted;
    a chunk without an explicit symbol scope proves nothing about which
    symbols were queried and is skipped for symbol coverage.
    """
    chunks: list[dict[str, Any]] = []
    for b in manifest.get("batches", []):
        if b.get("status") != "success":
            continue
        if b.get("dataset") != DATASET_NAME:
            continue
        symbols = _safe_list(b.get("symbols_json"))
        if not symbols:
            continue
        s = _parse_date(b.get("window_start"))
        e = _parse_date(b.get("window_end"))
        if s is None or e is None or s > e:
            continue
        chunks.append(
            {
                "symbols": set(symbols),
                "start": s,
                "end": e,
                "task_id": b.get("task_id"),
            }
        )
    return chunks


def evaluate_pin_contract(
    pin: Any,
    schema_keys: list[str],
    spec_primary: Any,
    spec_backup: Any,
    *,
    pin_expected: str = CNEQUITY_PIN_SHA,
) -> dict[str, Any]:
    """Evaluate the pinned-upstream contract (schema + source + exact pin).

    PNG mismatch alone fails the contract; the pin is not merely reported.
    """
    pin_actual = None
    if isinstance(pin, dict):
        vcs = pin.get("vcs_info") or {}
        pin_actual = vcs.get("commit_id")
    pin_match = bool(pin_actual and pin_actual == pin_expected)
    schema_match = set(CONTRACT.required_fields).issubset(set(schema_keys))
    source_match = (
        spec_primary == CONTRACT.primary_source and spec_backup == CONTRACT.backup_source
    )
    return {
        "PIN_EXPECTED": pin_expected,
        "PIN_ACTUAL": pin_actual,
        "PIN_MATCH": pin_match,
        "SCHEMA_MATCH": schema_match,
        "SOURCE_MATCH": source_match,
        "match": bool(schema_match and source_match and pin_match),
    }


def run_gate(
    root: Path,
    *,
    window_start: date = R4A_WINDOW_START,
    window_end: date = R4A_WINDOW_END,
    contract: CorporateActionsContract = CONTRACT,
    expected_symbols: list[str] | None = None,
    expected_identity_hash: str | None = None,
    expected_identity_n: int | None = None,
) -> dict[str, Any]:
    """Run the R4A0 read-only gate against `root`. Never writes to `root`."""

    dataset = dataset_dir(root)
    parquet_files = list_parquet(dataset)
    manifest = read_manifest_states(root)

    # ---- IDENTITY (frozen R3 formal SH/SZ scope; FAIL CLOSED on mismatch) --
    identity = load_expected_identity(
        root,
        injected=expected_symbols,
        expected_hash=expected_identity_hash,
        expected_n=expected_identity_n,
    )
    identity_ok = bool(identity["identity_ok"])

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
    # A sparse event dataset cannot prove historical completeness from row
    # min/max or from "a corporate run succeeded". COVERAGE_PASS requires the
    # union of trusted successful coverage intervals to be contiguous across
    # the requested window (window_start..window_end). Any internal gap, a
    # left/right shortfall, no-window evidence, or non-success evidence is
    # UNKNOWN_PARTIAL and never PASS.
    intervals = collect_coverage_intervals(manifest)
    merged = merge_intervals(intervals)
    gaps = gaps_in_window(merged, window_start, window_end)
    covered_start = merged[0][0] if merged else None
    covered_end = merged[-1][1] if merged else None
    contiguous = bool(
        dataset_exists
        and row_count > 0
        and covered_start is not None
        and covered_end is not None
        and covered_start <= window_start
        and covered_end >= window_end
        and len(gaps) == 0
    )
    date_coverage_pass = contiguous
    # ---- SYMBOL COVERAGE: every EXPECTED_SYMBOL must itself have a
    # successful-receipt interval union that contiguously covers the window.
    # Event-row presence (or absence) is NOT used for this judgement.
    chunks = symbol_evidence_chunks(manifest)
    successfully_covered_n = 0
    missing_symbols: list[str] = []
    partial_symbols: list[str] = []
    for sym in identity["symbols"]:
        sym_intervals = [
            (c["start"], c["end"]) for c in chunks if sym in c["symbols"]
        ]
        merged_sym = merge_intervals(sym_intervals)
        gaps_sym = gaps_in_window(merged_sym, window_start, window_end)
        cs = merged_sym[0][0] if merged_sym else None
        ce = merged_sym[-1][1] if merged_sym else None
        sym_covered = bool(
            cs is not None
            and ce is not None
            and cs <= window_start
            and ce >= window_end
            and len(gaps_sym) == 0
        )
        if sym_covered:
            successfully_covered_n += 1
        elif not sym_intervals:
            missing_symbols.append(sym)
        else:
            partial_symbols.append(sym)
    symbol_coverage_pass = bool(
        identity_ok
        and len(identity["symbols"]) > 0
        and successfully_covered_n == len(identity["symbols"])
    )
    coverage_status = (
        "PASS" if date_coverage_pass and symbol_coverage_pass else "UNKNOWN_PARTIAL"
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
        "IDENTITY_PASS": identity_ok,
        "EXISTS_PASS": exists_status == "PASS",
        "SCHEMA_PASS": schema_status == "PASS",
        "SCOPE_PASS": scope_status == "PASS",
        "COVERAGE_PASS": coverage_status == "PASS",
        "DATE_COVERAGE_PASS": date_coverage_pass,
        "SYMBOL_COVERAGE_PASS": symbol_coverage_pass,
        "UNIQUENESS_PASS": uniqueness_status == "PASS",
        "PROVENANCE_PASS": provenance_status == "PASS",
    }
    ready = all(passes.values())

    blocker = None
    missing_capability = None
    bounded_next = None
    if not identity_ok:
        blocker = "FORMAL_IDENTITY_MISMATCH"
        missing_capability = (
            "frozen R3 formal SH/SZ identity not reproducible from the local "
            f"authoritative artifact (got n={identity['EXPECTED_SYMBOL_N']}, "
            f"hash={identity['EXPECTED_SYMBOL_HASH']}; frozen reference "
            f"n={FORMAL_IDENTITY_N}, hash={FORMAL_IDENTITY_HASH})"
        )
        bounded_next = "re-verify frozen R3 identity artifact; no bootstrap"
    elif not dataset_exists:
        blocker = "CORPORATE_ACTIONS_DATASET_NOT_BUILT"
        missing_capability = (
            "corporate_actions curated dataset (no parquet under "
            f"{dataset.relative_to(root)}/) and no corporate_actions ingestion run"
        )
        bounded_next = "R4A0_CORPORATE_ACTION_BOUNDED_BOOTSTRAP (Sol decision required)"
    elif not passes["COVERAGE_PASS"]:
        blocker = (
            "DATE_COVERAGE_UNPROVEN"
            if not date_coverage_pass
            else "SYMBOL_COVERAGE_INCOMPLETE"
        )
        missing_capability = (
            "corporate_actions coverage: a contiguous successful-receipt window "
            "AND complete per-EXPECTED_SYMBOL coverage of "
            f"{window_start}..{window_end}"
        )
        bounded_next = (
            "bounded corporate_actions bootstrap/backfill with complete "
            "window and symbol receipts (Sol decision)"
        )
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
        "IDENTITY_STATUS": identity["IDENTITY_STATUS"],
        "EXPECTED_SYMBOL_N": identity["EXPECTED_SYMBOL_N"],
        "EXPECTED_SYMBOL_HASH": identity["EXPECTED_SYMBOL_HASH"],
        "IDENTITY_SOURCE": identity["IDENTITY_SOURCE"],
        "DATASET_EXISTS": dataset_exists,
        "DATASET_PATH": str(dataset),
        "SCHEMA_STATUS": schema_status,
        "SCOPE_STATUS": scope_status,
        "COVERAGE_STATUS": coverage_status,
        "DATE_COVERAGE_PASS": date_coverage_pass,
        "SYMBOL_COVERAGE_PASS": symbol_coverage_pass,
        "SUCCESSFULLY_COVERED_SYMBOL_N": successfully_covered_n,
        "MISSING_SYMBOL_N": len(missing_symbols),
        "MISSING_SYMBOL_SAMPLE": missing_symbols[:20],
        "PARTIAL_SYMBOL_N": len(partial_symbols),
        "PARTIAL_SYMBOL_SAMPLE": partial_symbols[:20],
        "COVERAGE_INTERVALS": [
            {"start": str(s), "end": str(e)} for s, e in intervals
        ],
        "COVERAGE_GAPS": gaps,
        "CONTIGUOUS_COVERAGE": contiguous,
        "REQUESTED_WINDOW": {
            "start": str(window_start),
            "end": str(window_end),
        },
        "COVERED_WINDOW": (
            {
                "start": str(covered_start),
                "end": str(covered_end),
            }
            if covered_start is not None and covered_end is not None
            else None
        ),
        "COVERAGE_PROOF_SEMANTIC": (
            "INGESTION_WINDOW_OR_WATERMARK_MUST_COVER_REQUESTED_WINDOW"
        ),
        "PARTIAL_RUN_REJECTED": bool(
            dataset_exists and coverage_status == "UNKNOWN_PARTIAL"
        ),
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
