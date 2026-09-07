#!/usr/bin/env python3
"""One-shot, fail-closed recovery for the frozen R3 SH/SZ daily authority.

This runner deliberately does not use CNEquity's current universe resolver.
It derives the per-date eligible set only from the persisted 5,456-symbol
authority manifest and its frozen lifecycle fields.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
CONFIG = Path("/Users/luke808/ASL-r3-daily-foundation-v01/config/cnequity.toml")
AUTHORITY = ROOT / "staging/r3_full_session_completeness_authority_v01/full_request_manifest.json"
ORPHAN_RUN_ID = "3b37b4c5-9113-40b8-907f-ee629551328d"
ORPHAN_DATE = "2026-08-31"
FORMAL_IDENTITY_N = 5456
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
TARGET_DATES = ("2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07")
STAGE = ROOT / "staging/r3_frozen_shsz_daily_orphan_recovery_v01"
CANONICAL_SERIALIZATION = "json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(',', ':')) sorted by relative_path"


class RecoveryError(RuntimeError):
    pass


def require(condition: bool, code: str, detail: Any | None = None) -> None:
    if not condition:
        raise RecoveryError(code if detail is None else f"{code}:{detail}")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "FILE_MUTATED_DURING_HASH", path)
    return digest.hexdigest()


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_bytes(path, canonical(value) + b"\n")


def frozen_authority(path: Path = AUTHORITY) -> tuple[tuple[str, ...], dict[str, dict[str, Any]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    require(raw.get("FORMAL_SYMBOL_N") == FORMAL_IDENTITY_N, "FROZEN_AUTHORITY_N_MISMATCH")
    require(raw.get("FORMAL_IDENTITY_HASH") == FORMAL_IDENTITY_HASH, "FROZEN_AUTHORITY_HASH_MISMATCH")
    records: dict[str, dict[str, Any]] = {}
    for request in raw.get("requests", []):
        symbol = request.get("symbol")
        require(isinstance(symbol, str) and symbol.endswith((".SH", ".SZ")), "FROZEN_AUTHORITY_SYMBOL_INVALID")
        records.setdefault(symbol, request)
    symbols = tuple(sorted(records))
    require(len(symbols) == FORMAL_IDENTITY_N, "FROZEN_AUTHORITY_SYMBOL_N_MISMATCH", len(symbols))
    require(sha256_bytes(canonical(list(symbols))) == FORMAL_IDENTITY_HASH, "FROZEN_AUTHORITY_SYMBOL_HASH_MISMATCH")
    return symbols, records


def eligible_symbols(day: str, records: dict[str, dict[str, Any]], *, expected_n: int | None = 5208) -> tuple[str, ...]:
    trade_day = date.fromisoformat(day)
    result = []
    for symbol in sorted(records):
        record = records[symbol]
        listed = date.fromisoformat(str(record["list_date"]))
        delisted = record.get("delist_date")
        if listed <= trade_day and (delisted is None or trade_day < date.fromisoformat(str(delisted))):
            result.append(symbol)
    if expected_n is not None:
        require(len(result) == expected_n, "FROZEN_ELIGIBLE_SYMBOL_N_MISMATCH", len(result))
    return tuple(result)


def run_batches(run_id: str) -> list[dict[str, Any]]:
    db = ROOT / "meta/manifest.db"
    conn = sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT batch_id,status,symbols_json,rows_written,window_start,window_end FROM ingestion_batches WHERE run_id=?",
            (run_id,),
        )]
    finally:
        conn.close()
    for row in rows:
        row["symbols"] = tuple(json.loads(row.pop("symbols_json") or "[]"))
    rows.sort(key=lambda row: int(str(row["batch_id"]).rsplit("batch-", 1)[1].split("-", 1)[0]))
    return rows


def orphan_preflight(run_id: str, frozen: set[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    matches = subprocess.run(["ps", "-ax", "-o", "command="], check=True, capture_output=True, text=True).stdout
    require(run_id not in matches, "ORPHAN_RUN_PROCESS_STILL_OWNED")
    batches = run_batches(run_id)
    successful = [row for row in batches if row["status"] == "success"]
    running = [row for row in batches if row["status"] == "running"]
    require(len(successful) == 95 and len(running) == 1, "ORPHAN_BATCH_STATE_MISMATCH")
    require(all(row["window_start"] == ORPHAN_DATE == row["window_end"] for row in batches), "ORPHAN_BATCH_DATE_MISMATCH")
    successful_symbols = tuple(symbol for row in successful for symbol in row["symbols"])
    orphan_symbols = tuple(running[0]["symbols"])
    require(len(successful_symbols) == len(set(successful_symbols)) == 4750, "ORPHAN_SUCCESS_SCOPE_INVALID")
    require(len(orphan_symbols) == len(set(orphan_symbols)) == 50, "ORPHAN_RUNNING_SCOPE_INVALID")
    require(set(successful_symbols).isdisjoint(orphan_symbols), "ORPHAN_BATCH_SCOPE_OVERLAP")
    require(set(successful_symbols).union(orphan_symbols).issubset(frozen), "ORPHAN_SCOPE_OUTSIDE_FROZEN")
    orphan_dir = ROOT / "staging/daily_bars" / f"run_id={run_id}"
    orphan_file = orphan_dir / f"part-{running[0]['batch_id']}.parquet"
    require(not orphan_file.exists(), "ORPHAN_BATCH_ALREADY_STAGED", orphan_file)
    return successful_symbols, orphan_symbols


def chunked(values: Iterable[str], size: int = 50) -> list[tuple[str, ...]]:
    source = list(values)
    return [tuple(source[index:index + size]) for index in range(0, len(source), size)]


def _validate_frame(frame: Any, day: str, requested: set[str]) -> None:
    import polars as pl
    require(set(frame.get_column("symbol").to_list()).issubset(requested), "PROVIDER_SCOPE_VIOLATION")
    dates = frame.get_column("trade_date").cast(pl.Date, strict=False)
    require(dates.null_count() == 0 and dates.min() == dates.max() == date.fromisoformat(day), "PROVIDER_DATE_MISMATCH")
    require(frame.get_column("symbol").n_unique() == frame.height, "PROVIDER_DUPLICATE_KEYS")


def quality(rows: list[dict[str, Any]], day: str, requested: set[str]) -> dict[str, Any]:
    gates = Counter({key: 0 for key in ("duplicate", "ohlc_invalid", "negative_volume", "negative_amount", "provenance_failure")})
    observed: set[str] = set()
    for row in rows:
        symbol = row.get("symbol")
        trade_day = row.get("trade_date")
        if hasattr(trade_day, "isoformat"):
            trade_day = trade_day.isoformat()
        key = (symbol, trade_day)
        gates["duplicate"] += symbol in observed
        observed.add(symbol)
        values = [row.get(name) for name in ("open", "high", "low", "close", "volume", "amount")]
        finite = all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)
        if finite:
            gates["ohlc_invalid"] += not (row["low"] <= row["open"] <= row["high"] and row["low"] <= row["close"] <= row["high"])
        else:
            gates["ohlc_invalid"] += 1
        gates["negative_volume"] += int(isinstance(row.get("volume"), (int, float)) and row["volume"] < 0)
        gates["negative_amount"] += int(isinstance(row.get("amount"), (int, float)) and row["amount"] < 0)
        gates["provenance_failure"] += int(row.get("source") != "tdx_protocol" or row.get("data_version") != "v2" or str(trade_day) != day)
    unresolved = sorted(requested - observed)
    return {"requested_n": len(requested), "observed_n": len(observed), "requested_not_observed": unresolved, **dict(gates)}


def classify_secondary(keys: list[str], day: str) -> list[dict[str, Any]]:
    if not keys:
        return []
    import baostock as bs
    login = bs.login()
    if login.error_code != "0":
        raise RecoveryError("SOURCE_ERROR:BAOSTOCK_LOGIN")
    output = []
    try:
        for symbol in keys:
            code = ("sh." if symbol.endswith(".SH") else "sz.") + symbol[:6]
            result = bs.query_history_k_data_plus(code, "date,code,tradestatus", start_date=day, end_date=day, frequency="d", adjustflag="3")
            rows = []
            while result.next():
                rows.append(dict(zip(result.fields, result.get_row_data())))
            if result.error_code != "0":
                status = "SOURCE_ERROR"
            elif len(rows) != 1:
                status = "UNKNOWN"
            elif rows[0].get("code") != code or rows[0].get("date") != day:
                status = "SOURCE_ERROR"
            elif rows[0].get("tradestatus") == "0":
                status = "SUSPENDED"
            else:
                status = "BAR_PRESENT"
            output.append({"symbol": symbol, "trade_date": day, "requested_code": code, "rows": rows, "final_classification": status})
    finally:
        bs.logout()
    return output


def published_manifest() -> tuple[dict[str, Any], dict[str, Any]]:
    pointer = json.loads((ROOT / "meta/asl/r3/published-daily-authority.json").read_text())
    require(pointer.get("schema") == "R3_PUBLISHED_DAILY_AUTHORITY_V01", "PUBLISHED_POINTER_INVALID")
    plan = json.loads((ROOT / pointer["plan"]).read_text())
    receipt = json.loads((ROOT / pointer["receipt"]).read_text())
    manifest = plan.get("EXPECTED_POST_INPUT_MANIFEST")
    require(receipt.get("STATE") == "COMMITTED" and isinstance(manifest, dict), "PUBLISHED_AUTHORITY_INVALID")
    require(pointer.get("manifest_hash") == manifest.get("INPUT_MANIFEST_HASH") == receipt.get("POST_INPUT_MANIFEST_HASH"), "PUBLISHED_MANIFEST_BINDING_INVALID")
    return pointer, manifest


def _read_staged(run_id: str) -> Any:
    import polars as pl

    directory = ROOT / "staging/daily_bars" / f"run_id={run_id}"
    files = sorted(directory.glob("*.parquet"))
    require(bool(files), "STAGING_ROWS_MISSING", run_id)
    return pl.concat([pl.read_parquet(path) for path in files], how="diagonal_relaxed")


def _fetch_batch(
    manifest: Any,
    run_id: str,
    batch_id: str,
    day: str,
    symbols: tuple[str, ...],
    frozen: set[str],
) -> None:
    """Fetch one recovery batch after persisting its exact frozen request scope."""
    import polars as pl
    from cnequity.adapters.tdx_protocol.client import fetch_daily_bars, normalize_with_source
    from cnequity.config.loader import load_config
    from cnequity.storage.parquet import StagingWriter

    require(bool(symbols) and set(symbols).issubset(frozen), "OUTSIDE_FROZEN_PROVIDER_REQUEST")
    manifest.start_batch(run_id, batch_id, "daily_bars", "daily_bars", list(symbols), day, day)
    try:
        raw = fetch_daily_bars(list(symbols), date.fromisoformat(day), date.fromisoformat(day), config=load_config(CONFIG), on_heartbeat=lambda: manifest.touch_batch_heartbeat(run_id, batch_id))
        frame = normalize_with_source(raw, dataset="daily_bars")
        require(frame.height > 0, "SOURCE_ERROR:EMPTY_PROVIDER_RESPONSE")
        _validate_frame(frame, day, set(symbols))
        # Provider rows are written only after scope/date/key validation.  A short
        # response is retained for later bounded BaoStock classification, never
        # translated into a suspended bar here.
        StagingWriter(ROOT / "staging").write_batch("daily_bars", run_id, batch_id, frame)
        manifest.finish_batch(run_id, batch_id, "success", rows_read=frame.height, rows_written=frame.height)
    except Exception as exc:
        manifest.finish_batch(run_id, batch_id, "failed", error_message=f"{type(exc).__name__}:{exc}")
        raise


def _batch_plan(day: str, eligible: tuple[str, ...], successful: set[str], orphan: tuple[str, ...] | None) -> list[tuple[str, ...]]:
    require(successful.issubset(set(eligible)), "SUCCESS_SCOPE_OUTSIDE_DATE_ELIGIBILITY")
    pending = tuple(symbol for symbol in eligible if symbol not in successful)
    if orphan is None:
        return chunked(pending)
    require(set(orphan).issubset(set(pending)), "ORPHAN_SCOPE_OUTSIDE_PENDING")
    after_orphan = tuple(symbol for symbol in pending if symbol not in set(orphan))
    return [orphan, *chunked(after_orphan)]


def _file_rows(root: Path) -> list[dict[str, Any]]:
    return [
        {"relative_path": path.relative_to(root).as_posix(), "file_size": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted((root / "curated/daily_bars").rglob("*.parquet"))
    ]


def _publish(day: str, frame: Any, quality_result: dict[str, Any], classifications: list[dict[str, Any]]) -> tuple[int, str]:
    """Write evidence first and atomically move the authority pointer last."""
    import polars as pl
    from cnequity.storage.atomic import write_parquet_atomic

    pointer, previous = published_manifest()
    expected_before = previous.get("FILES")
    require(isinstance(expected_before, list), "PUBLISHED_MANIFEST_FILES_INVALID")
    require(_file_rows(ROOT) == expected_before, "PUBLISHED_INPUT_DRIFT")
    destination = ROOT / "curated/daily_bars" / f"trade_date={day}" / "part-merged.parquet"
    require(not destination.exists(), "TARGET_PARTITION_ALREADY_EXISTS")
    candidate = STAGE / f"trade_date={day}" / "part-merged.parquet"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(candidate, frame.sort("symbol"), compression="zstd")
    new_file = {"relative_path": destination.relative_to(ROOT).as_posix(), "file_size": candidate.stat().st_size, "sha256": sha256_file(candidate)}
    files = sorted([*expected_before, new_file], key=lambda row: row["relative_path"])
    manifest_hash = sha256_bytes(canonical(files))
    stage_day = STAGE / f"trade_date={day}"
    plan_path = stage_day / "promotion_plan.json"
    receipt_path = stage_day / "promotion_receipt.json"
    plan = {"TASK": "R3_FROZEN_SHSZ_DAILY_ORPHAN_RECOVERY_V01", "DATE": day, "EXPECTED_POST_INPUT_MANIFEST": {"CANONICAL_SERIALIZATION": CANONICAL_SERIALIZATION, "FILES": files, "INPUT_FILE_N": len(files), "INPUT_MANIFEST_HASH": manifest_hash}}
    unresolved = quality_result["requested_not_observed"]
    receipt = {"TASK": plan["TASK"], "DATE": day, "STATE": "PREPARED", "OLD_INPUT_MANIFEST_HASH": pointer["manifest_hash"], "POST_INPUT_FILE_N": len(files), "POST_INPUT_MANIFEST_HASH": manifest_hash, "CLASSIFICATION_SHA256": sha256_bytes(canonical(classifications)), "QUALITY": {"STRUCTURAL_PASS": all(quality_result[name] == 0 for name in ("duplicate", "ohlc_invalid", "negative_volume", "negative_amount")), "COVERAGE_PASS": not unresolved, "PROVENANCE_PASS": quality_result["provenance_failure"] == 0, "UNRESOLVED_KEY_N": len(unresolved), "SOURCE_ERROR_N": sum(item["final_classification"] == "SOURCE_ERROR" for item in classifications), "MAX_TRADE_DATE": day}}
    require(receipt["QUALITY"]["STRUCTURAL_PASS"] and receipt["QUALITY"]["COVERAGE_PASS"] and receipt["QUALITY"]["PROVENANCE_PASS"] and receipt["QUALITY"]["UNRESOLVED_KEY_N"] == 0 and receipt["QUALITY"]["SOURCE_ERROR_N"] == 0, "PUBLICATION_QUALITY_GATE_FAILED")
    atomic_json(plan_path, plan)
    atomic_json(receipt_path, {**receipt, "STATE": "COMMITTED"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(candidate, destination)
    require(_file_rows(ROOT) == files, "POST_PROMOTION_MANIFEST_MISMATCH")
    atomic_json(ROOT / "meta/asl/r3/published-daily-authority.json", {"schema": "R3_PUBLISHED_DAILY_AUTHORITY_V01", "manifest_hash": manifest_hash, "plan": plan_path.relative_to(ROOT).as_posix(), "receipt": receipt_path.relative_to(ROOT).as_posix()})
    return len(files), manifest_hash


def run_incremental_for_frozen_shsz_date(
    day: str,
    frozen_symbols: tuple[str, ...],
    records: dict[str, dict[str, Any]],
    *,
    execute: bool,
    run_id: str | None = None,
    orphan: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Run one date strictly against the persisted frozen SH/SZ authority."""
    require(day in TARGET_DATES, "DATE_OUTSIDE_AUTHORIZED_SCOPE", day)
    eligible = eligible_symbols(day, records)
    require(set(eligible).issubset(set(frozen_symbols)), "ELIGIBLE_SCOPE_OUTSIDE_FROZEN")
    if not execute:
        return {"date": day, "eligible_n": len(eligible), "network_request_n": 0}
    from cnequity.orchestrator.manifest import Manifest

    manifest = Manifest(ROOT / "meta/manifest.db")
    if run_id is None:
        run_id = manifest.start_run("r3_frozen_shsz_daily_orphan_recovery", {"scope": "SH_SZ_FROZEN_5456", "date": day, "formal_identity_n": FORMAL_IDENTITY_N, "formal_identity_hash": FORMAL_IDENTITY_HASH})
        successful: set[str] = set()
        batch_prefix = f"{day}_frozen"
    else:
        successful, persisted_orphan = orphan_preflight(run_id, set(frozen_symbols))
        require(orphan == persisted_orphan, "ORPHAN_SCOPE_CHANGED")
        successful = set(successful)
        # The abandoned record is preserved for audit, then superseded only by
        # the successful recovery batch; no successful original batch is replayed.
        manifest.advance_batch_timeouts(run_id, stale_after_seconds=1)
        batch_prefix = f"{day}_recovery"
    batches = _batch_plan(day, eligible, successful, orphan)
    for index, symbols in enumerate(batches):
        try:
            _fetch_batch(manifest, run_id, f"{batch_prefix}-batch-{index}", day, symbols, set(frozen_symbols))
        except Exception as exc:
            manifest.finish_run(run_id, "failed", error_message=f"{type(exc).__name__}:{exc}")
            raise
    if orphan is not None:
        manifest.supersede_batches(run_id, [f"{day}_{day}-batch-95"], superseded_by=f"{batch_prefix}-batch-0")
    frame = _read_staged(run_id)
    rows = frame.to_dicts()
    result = quality(rows, day, set(eligible))
    require(all(result[name] == 0 for name in ("duplicate", "ohlc_invalid", "negative_volume", "negative_amount", "provenance_failure")), "STRUCTURAL_QUALITY_FAILED", result)
    classifications = classify_secondary(result["requested_not_observed"], day)
    bad = [item for item in classifications if item["final_classification"] != "SUSPENDED"]
    require(not bad, "UNRESOLVED_CLASSIFICATION_BLOCK", bad)
    # Suspended keys are reconciled by their secondary receipt; the published
    # parquet contains only genuine provider bars, never synthetic suspension rows.
    result["requested_not_observed"] = []
    file_n, manifest_hash = _publish(day, frame, result, classifications)
    manifest.finish_run(run_id, "success", rows_read=frame.height, rows_written=frame.height)
    return {"date": day, "run_id": run_id, "eligible_n": len(eligible), "network_request_n": len(batches), "requested_n": len(eligible), "observed_n": frame.get_column("symbol").n_unique(), "classifications": classifications, "quality": result, "manifest_file_n": file_n, "manifest_hash": manifest_hash}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    frozen, records = frozen_authority()
    successful, orphan = orphan_preflight(ORPHAN_RUN_ID, set(frozen))
    summary = {"orphan_run_id": ORPHAN_RUN_ID, "frozen_membership_n": len(frozen), "successful_requested_n": len(successful), "orphan_requested_n": len(orphan), "dates": []}
    for index, day in enumerate(TARGET_DATES):
        summary["dates"].append(run_incremental_for_frozen_shsz_date(
            day,
            frozen,
            records,
            execute=args.execute,
            run_id=ORPHAN_RUN_ID if index == 0 else None,
            orphan=orphan if index == 0 else None,
        ))
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
