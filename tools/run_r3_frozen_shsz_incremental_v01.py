#!/usr/bin/env python3
"""Fail-closed R3 daily incremental runner for the frozen SH/SZ authority.

This is intentionally a narrow operational bridge around CNEquity's TDX
adapter.  It never asks CNEquity to resolve ``all_a``.  The only admissible
provider request scope is the persisted 5,456-symbol R3 authority; a missing
primary TDX singleton remains unresolved until BaoStock positively confirms a
same-date suspension.  Evidence is written before the authority pointer is
atomically switched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config/cnequity.toml"
AUTHORITY = ROOT / "staging/r3_full_session_completeness_authority_v01/full_request_manifest.json"
STAGE = ROOT / "staging/r3_frozen_shsz_daily_incremental_v01"
FORMAL_IDENTITY_N = 5456
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
SERIALIZATION = "json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(',', ':')) sorted by relative_path"


class IncrementalError(RuntimeError):
    """A gate failed; never promote a partial or ambiguous date."""


def require(condition: bool, code: str, detail: Any | None = None) -> None:
    if not condition:
        raise IncrementalError(code if detail is None else f"{code}:{detail}")


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
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "FILE_MUTATED_DURING_HASH", path)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def frozen_authority() -> tuple[tuple[str, ...], dict[str, dict[str, Any]]]:
    raw = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    require(raw.get("FORMAL_SYMBOL_N") == FORMAL_IDENTITY_N, "FROZEN_AUTHORITY_N_MISMATCH")
    require(raw.get("FORMAL_IDENTITY_HASH") == FORMAL_IDENTITY_HASH, "FROZEN_AUTHORITY_HASH_MISMATCH")
    records: dict[str, dict[str, Any]] = {}
    for record in raw.get("requests", []):
        symbol = record.get("symbol")
        require(isinstance(symbol, str) and symbol.endswith((".SH", ".SZ")), "FROZEN_AUTHORITY_SYMBOL_INVALID")
        # The immutable full-session authority is deliberately windowed by
        # calendar year, so a symbol has one request record per year.  Its
        # lifecycle identity must nevertheless be invariant across records.
        previous = records.get(symbol)
        if previous is not None:
            require((previous.get("list_date"), previous.get("delist_date")) ==
                    (record.get("list_date"), record.get("delist_date")),
                    "FROZEN_AUTHORITY_LIFECYCLE_CONFLICT", symbol)
        else:
            records[symbol] = record
    symbols = tuple(sorted(records))
    require(len(symbols) == FORMAL_IDENTITY_N, "FROZEN_AUTHORITY_SYMBOL_N_MISMATCH")
    require(sha256_bytes(canonical(list(symbols))) == FORMAL_IDENTITY_HASH, "FROZEN_AUTHORITY_SYMBOL_HASH_MISMATCH")
    return symbols, records


def eligible_symbols(day: str, records: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    trade_day = date.fromisoformat(day)
    eligible = []
    for symbol, record in sorted(records.items()):
        listed = date.fromisoformat(str(record["list_date"]))
        delisted = record.get("delist_date")
        if listed <= trade_day and (not delisted or trade_day < date.fromisoformat(str(delisted))):
            eligible.append(symbol)
    require(eligible, "EMPTY_FROZEN_ELIGIBLE_SCOPE")
    return tuple(eligible)


def chunks(values: Iterable[str], size: int = 50) -> list[tuple[str, ...]]:
    source = list(values)
    return [tuple(source[index:index + size]) for index in range(0, len(source), size)]


def _validate_frame(frame: Any, day: str, requested: set[str]) -> None:
    import polars as pl
    require(frame.height > 0, "PRIMARY_EMPTY_FRAME")
    require(set(frame.get_column("symbol").to_list()).issubset(requested), "PROVIDER_SCOPE_VIOLATION")
    dates = frame.get_column("trade_date").cast(pl.Date, strict=False)
    require(dates.null_count() == 0 and dates.min() == dates.max() == date.fromisoformat(day), "PROVIDER_DATE_MISMATCH")
    require(frame.get_column("symbol").n_unique() == frame.height, "PROVIDER_DUPLICATE_KEYS")


def _is_missing_primary(exc: Exception) -> bool:
    return "TDX returned no rows for" in str(exc)


def fetch_scope(manifest: Any, run_id: str, batch_id: str, day: str,
                symbols: tuple[str, ...], frozen: set[str], frames: list[Any],
                primary_missing: list[str], attempts: Counter) -> None:
    """Fetch a scope, bisecting only a documented missing-primary response."""
    from cnequity.adapters.tdx_protocol.client import fetch_daily_bars, normalize_with_source
    from cnequity.config.loader import load_config
    from cnequity.storage.parquet import StagingWriter

    require(bool(symbols) and set(symbols).issubset(frozen), "OUTSIDE_FROZEN_PROVIDER_REQUEST")
    manifest.start_batch(run_id, batch_id, "daily_bars", "daily_bars", list(symbols), day, day)
    last_error: Exception | None = None
    for retry in range(3):
        try:
            attempts["network_request_n"] += 1
            raw = fetch_daily_bars(list(symbols), date.fromisoformat(day), date.fromisoformat(day),
                                   config=load_config(CONFIG),
                                   on_heartbeat=lambda: manifest.touch_batch_heartbeat(run_id, batch_id))
            frame = normalize_with_source(raw, dataset="daily_bars")
            _validate_frame(frame, day, set(symbols))
            StagingWriter(ROOT / "staging").write_batch("daily_bars", run_id, batch_id, frame)
            manifest.finish_batch(run_id, batch_id, "success", rows_read=frame.height, rows_written=frame.height,
                                  retry_count=retry)
            frames.append(frame)
            return
        except Exception as exc:
            last_error = exc
            if _is_missing_primary(exc):
                break
            if retry < 2:
                attempts["retry_n"] += 1
                time.sleep(5 * (retry + 1))
    require(last_error is not None, "TDX_ATTEMPT_STATE_INVALID")
    exc = last_error
    manifest.finish_batch(run_id, batch_id, "failed", error_message=f"{type(exc).__name__}:{exc}", retry_count=2)
    if not _is_missing_primary(exc):
        raise IncrementalError(f"SOURCE_ERROR:TDX:{type(exc).__name__}:{exc}") from exc
    if len(symbols) == 1:
        # A successful TDX response with no daily bar is not a suspension.
        primary_missing.append(symbols[0])
        return
    midpoint = len(symbols) // 2
    fetch_scope(manifest, run_id, batch_id + "a", day, symbols[:midpoint], frozen, frames, primary_missing, attempts)
    fetch_scope(manifest, run_id, batch_id + "b", day, symbols[midpoint:], frozen, frames, primary_missing, attempts)


def structural_quality(frame: Any, day: str, requested: set[str]) -> dict[str, Any]:
    gates = Counter({name: 0 for name in ("duplicate", "ohlc_invalid", "negative_volume", "negative_amount", "provenance_failure")})
    observed: set[str] = set()
    for row in frame.to_dicts():
        symbol = row.get("symbol")
        trade_day = row.get("trade_date")
        trade_day = trade_day.isoformat() if hasattr(trade_day, "isoformat") else trade_day
        gates["duplicate"] += int(symbol in observed)
        observed.add(symbol)
        values = [row.get(name) for name in ("open", "high", "low", "close", "volume", "amount")]
        finite = all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)
        gates["ohlc_invalid"] += int(not finite or not (row["low"] <= row["open"] <= row["high"] and row["low"] <= row["close"] <= row["high"]))
        gates["negative_volume"] += int(isinstance(row.get("volume"), (int, float)) and row["volume"] < 0)
        gates["negative_amount"] += int(isinstance(row.get("amount"), (int, float)) and row["amount"] < 0)
        gates["provenance_failure"] += int(row.get("source") != "tdx_protocol" or row.get("data_version") != "v2" or trade_day != day)
    return {"requested_n": len(requested), "observed_n": len(observed),
            "requested_not_observed": sorted(requested - observed), **dict(gates)}


def classify_secondary(symbols: list[str], day: str) -> list[dict[str, Any]]:
    if not symbols:
        return []
    import baostock as bs
    login = bs.login()
    require(login.error_code == "0", "SOURCE_ERROR:BAOSTOCK_LOGIN")
    output = []
    try:
        for symbol in symbols:
            code = ("sh." if symbol.endswith(".SH") else "sz.") + symbol[:6]
            result = bs.query_history_k_data_plus(code, "date,code,tradestatus", start_date=day,
                                                   end_date=day, frequency="d", adjustflag="3")
            rows = []
            while result.next():
                rows.append(dict(zip(result.fields, result.get_row_data())))
            if result.error_code != "0":
                classification = "SOURCE_ERROR"
            elif len(rows) != 1:
                classification = "UNKNOWN"
            elif rows[0].get("code") != code or rows[0].get("date") != day:
                classification = "SOURCE_ERROR"
            elif rows[0].get("tradestatus") == "0":
                classification = "SUSPENDED"
            else:
                classification = "BAR_PRESENT"
            output.append({"symbol": symbol, "trade_date": day, "requested_code": code,
                           "provider": "baostock", "rows": rows,
                           "final_classification": classification,
                           "fetched_at": datetime.now(timezone.utc).isoformat()})
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


def file_rows() -> list[dict[str, Any]]:
    return [{"relative_path": path.relative_to(ROOT).as_posix(), "file_size": path.stat().st_size,
             "sha256": sha256_file(path)}
            for path in sorted((ROOT / "curated/daily_bars").rglob("*.parquet"))]


def promote(day: str, frame: Any, quality: dict[str, Any], classifications: list[dict[str, Any]]) -> dict[str, Any]:
    from cnequity.storage.atomic import write_parquet_atomic

    pointer, old = published_manifest()
    expected_before = old.get("FILES")
    require(isinstance(expected_before, list) and file_rows() == expected_before, "PUBLISHED_INPUT_DRIFT")
    destination = ROOT / "curated/daily_bars" / f"trade_date={day}" / "part-merged.parquet"
    require(not destination.exists(), "TARGET_PARTITION_ALREADY_EXISTS")
    stage_day = STAGE / f"trade_date={day}"
    candidate = stage_day / "part-merged.parquet"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(candidate, frame.sort("symbol"), compression="zstd")
    new_file = {"relative_path": destination.relative_to(ROOT).as_posix(), "file_size": candidate.stat().st_size,
                "sha256": sha256_file(candidate)}
    files = sorted([*expected_before, new_file], key=lambda value: value["relative_path"])
    manifest_hash = sha256_bytes(canonical(files))
    quality_receipt = {
        "STRUCTURAL_PASS": all(quality[name] == 0 for name in ("duplicate", "ohlc_invalid", "negative_volume", "negative_amount")),
        "COVERAGE_PASS": not quality["requested_not_observed"],
        "PROVENANCE_PASS": quality["provenance_failure"] == 0,
        "UNRESOLVED_KEY_N": len(quality["requested_not_observed"]),
        "SOURCE_ERROR_N": sum(value["final_classification"] == "SOURCE_ERROR" for value in classifications),
        "MAX_TRADE_DATE": day,
    }
    require(all((quality_receipt["STRUCTURAL_PASS"], quality_receipt["COVERAGE_PASS"], quality_receipt["PROVENANCE_PASS"],
                 quality_receipt["UNRESOLVED_KEY_N"] == 0, quality_receipt["SOURCE_ERROR_N"] == 0)), "PUBLICATION_QUALITY_GATE_FAILED")
    manifest = {"CANONICAL_SERIALIZATION": SERIALIZATION, "FILES": files, "INPUT_FILE_N": len(files),
                "INPUT_MANIFEST_HASH": manifest_hash}
    plan = {"TASK": "R3_FROZEN_SHSZ_DAILY_INCREMENTAL_V01", "DATE": day,
            "EXPECTED_POST_INPUT_MANIFEST": manifest}
    receipt = {"TASK": plan["TASK"], "DATE": day, "STATE": "COMMITTED",
               "OLD_INPUT_MANIFEST_HASH": pointer["manifest_hash"], "POST_INPUT_FILE_N": len(files),
               "POST_INPUT_MANIFEST_HASH": manifest_hash, "EXPECTED_POST_INPUT_FILE_N": len(files),
               "EXPECTED_POST_INPUT_MANIFEST_HASH": manifest_hash, "QUALITY": quality_receipt,
               "CLASSIFICATION_SHA256": sha256_bytes(canonical(classifications))}
    atomic_json(stage_day / "classifications.json", {"keys": classifications})
    atomic_json(stage_day / "promotion_plan.json", plan)
    atomic_json(stage_day / "promotion_receipt.json", receipt)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(candidate, destination)
    require(file_rows() == files, "POST_PROMOTION_MANIFEST_MISMATCH")
    atomic_json(ROOT / "meta/asl/r3/published-daily-authority.json", {
        "schema": "R3_PUBLISHED_DAILY_AUTHORITY_V01", "manifest_hash": manifest_hash,
        "plan": (stage_day / "promotion_plan.json").relative_to(ROOT).as_posix(),
        "receipt": (stage_day / "promotion_receipt.json").relative_to(ROOT).as_posix(),
    })
    return {"file_n": len(files), "manifest_hash": manifest_hash, "quality": quality_receipt}


def run(day: str, *, execute: bool) -> dict[str, Any]:
    require(date.fromisoformat(day) <= date.today(), "FUTURE_TRADE_DATE_BLOCKED", day)
    frozen, records = frozen_authority()
    eligible = eligible_symbols(day, records)
    pointer, manifest = published_manifest()
    current_max = max(Path(item["relative_path"]).parts[2].split("=", 1)[1] for item in manifest["FILES"])
    require(day > current_max, "DATE_NOT_AFTER_PUBLISHED_AUTHORITY")
    if not execute:
        return {"status": "READY", "trade_date": day, "frozen_n": len(frozen), "eligible_n": len(eligible),
                "current_published_as_of": current_max, "network_request_n": 0}
    from cnequity.orchestrator.manifest import Manifest
    import polars as pl

    store = Manifest(ROOT / "meta/manifest.db")
    run_id = store.start_run("r3_frozen_shsz_daily_incremental", {"scope": "FROZEN_SH_SZ_5456", "trade_date": day,
        "formal_identity_n": FORMAL_IDENTITY_N, "formal_identity_hash": FORMAL_IDENTITY_HASH,
        "published_manifest_before": pointer["manifest_hash"]})
    frames: list[Any] = []
    primary_missing: list[str] = []
    attempts: Counter = Counter()
    try:
        for index, scope in enumerate(chunks(eligible)):
            fetch_scope(store, run_id, f"{day}-batch-{index:03d}", day, scope, set(frozen), frames, primary_missing, attempts)
        require(frames, "NO_PRIMARY_ROWS")
        frame = pl.concat(frames, how="diagonal_relaxed")
        quality = structural_quality(frame, day, set(eligible))
        require(all(quality[name] == 0 for name in ("duplicate", "ohlc_invalid", "negative_volume", "negative_amount", "provenance_failure")),
                "STRUCTURAL_QUALITY_FAILED", quality)
        # Do not assume a singleton primary miss is suspended.  Validate all missing keys,
        # including defensive coverage detection from received frames.
        classifications = classify_secondary(quality["requested_not_observed"], day)
        attempts["secondary_request_n"] = len(classifications)
        bad = [item for item in classifications if item["final_classification"] != "SUSPENDED"]
        require(not bad, "UNRESOLVED_CLASSIFICATION_BLOCK", bad[:3])
        quality["requested_not_observed"] = []
        published = promote(day, frame, quality, classifications)
        store.finish_run(run_id, "success", rows_read=frame.height, rows_written=frame.height)
        return {"status": "PUBLISHED", "run_id": run_id, "trade_date": day, "frozen_n": len(frozen),
                "eligible_n": len(eligible), "observed_n": frame.get_column("symbol").n_unique(),
                "primary_missing_n": len(primary_missing), "classifications": classifications,
                "network_request_n": attempts["network_request_n"], "secondary_request_n": attempts["secondary_request_n"],
                "quality": quality, **published}
    except Exception as exc:
        store.finish_run(run_id, "failed", error_message=f"{type(exc).__name__}:{exc}")
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.trade_date, execute=args.execute), sort_keys=True))


if __name__ == "__main__":
    main()
