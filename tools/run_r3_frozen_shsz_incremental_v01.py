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
import contextlib
import fcntl
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
ACTIVE_AUTHORITY_POINTER = ROOT / "meta/asl/r3/active-shsz-authority.json"
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


@contextlib.contextmanager
def writer_lock() -> Iterable[None]:
    """One local writer is enough: publication state is a single-host asset."""
    path = ROOT / "meta/asl/r3/incremental-publication.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IncrementalError("R3_INCREMENTAL_WRITER_LOCKED") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def calendar_state(day: str) -> str:
    """Use the persisted CNEquity calendar; never query a second calendar source."""
    import polars as pl

    target = date.fromisoformat(day)
    path = ROOT / "curated/trading_calendar" / f"trade_date={target.year}" / "part-merged.parquet"
    require(path.is_file(), "TRADING_CALENDAR_AUTHORITY_MISSING", target.year)
    rows = pl.read_parquet(path).filter(pl.col("trade_date") == target)
    require(rows.height == 1, "TRADING_CALENDAR_DATE_MISSING", day)
    return "TRADING_DAY" if rows.item(0, "is_trading") is True else "NON_TRADING_DAY"


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
    # V01 is bootstrapped from this immutable source.  Once an active pointer
    # exists, a changed current source is an explicit rollover decision, never
    # an implicit daily-universe expansion.
    if ACTIVE_AUTHORITY_POINTER.exists():
        try:
            pointer = json.loads(ACTIVE_AUTHORITY_POINTER.read_text())
            artifact = ROOT / pointer["authority"]
            active = json.loads(artifact.read_text())
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise IncrementalError("ACTIVE_AUTHORITY_INVALID") from exc
        require(pointer.get("schema") == "ASL_ACTIVE_R3_SHSZ_AUTHORITY_V01", "ACTIVE_AUTHORITY_INVALID")
        require(active.get("authority_version") == pointer.get("authority_version"), "ACTIVE_AUTHORITY_INVALID")
        require(sha256_bytes(canonical(active)) == pointer.get("authority_hash"), "ACTIVE_AUTHORITY_DRIFT")
        lifecycle = active.get("lifecycle")
        require(active.get("quality", {}).get("PASS") is True and isinstance(lifecycle, list), "ACTIVE_AUTHORITY_INVALID")
        active_records = {row.get("symbol"): row for row in lifecycle if isinstance(row, dict)}
        require(len(active_records) == active.get("symbol_n") == len(records), "AUTHORITY_REFRESH_REQUIRED")
        require(tuple(sorted(active_records)) == symbols and active.get("identity_hash") == FORMAL_IDENTITY_HASH,
                "AUTHORITY_REFRESH_REQUIRED")
        require(all((active_records[s].get("list_date"), active_records[s].get("delist_date")) ==
                    (records[s].get("list_date"), records[s].get("delist_date")) for s in symbols),
                "AUTHORITY_REFRESH_REQUIRED")
        records = {symbol: active_records[symbol] for symbol in symbols}
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


def _switch_pointer(stage_day: Path, manifest_hash: str) -> None:
    """The sole authority mutation; callers verify physical state first."""
    atomic_json(ROOT / "meta/asl/r3/published-daily-authority.json", {
        "schema": "R3_PUBLISHED_DAILY_AUTHORITY_V01", "manifest_hash": manifest_hash,
        "plan": (stage_day / "promotion_plan.json").relative_to(ROOT).as_posix(),
        "receipt": (stage_day / "promotion_receipt.json").relative_to(ROOT).as_posix(),
    })


def promotion_checkpoint(_name: str) -> None:
    """Test-only interruption seam; production deliberately performs no action."""


def recover_promotion(day: str) -> dict[str, Any]:
    """Complete only an exact, previously evidenced interrupted promotion.

    A curated orphan is never overwritten.  A candidate under staging may be
    retried only when it is bound to the preceding pointer and the committed
    plan/receipt hash it exactly; otherwise the state is an explicit conflict.
    """
    stage_day = STAGE / f"trade_date={day}"
    candidate = stage_day / "part-merged.parquet"
    destination = ROOT / "curated/daily_bars" / f"trade_date={day}" / "part-merged.parquet"
    plan_path, receipt_path = stage_day / "promotion_plan.json", stage_day / "promotion_receipt.json"
    if not destination.exists() and not candidate.exists():
        return {"state": "READY_TO_RETRY"}
    if not (plan_path.exists() and receipt_path.exists()):
        if destination.exists():
            raise IncrementalError("ORPHAN_CONFLICT:CURATED_WITHOUT_COMMITTED_EVIDENCE")
        if candidate.exists():
            raise IncrementalError("ORPHAN_CONFLICT:CANDIDATE_WITHOUT_COMMITTED_EVIDENCE")
        return {"state": "READY_TO_RETRY"}
    try:
        plan, receipt = json.loads(plan_path.read_text()), json.loads(receipt_path.read_text())
        manifest = plan["EXPECTED_POST_INPUT_MANIFEST"]
        files = manifest["FILES"]
        expected_hash = manifest["INPUT_MANIFEST_HASH"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IncrementalError("ORPHAN_CONFLICT:INVALID_PROMOTION_EVIDENCE") from exc
    pointer, _old = published_manifest()
    expected_record = next((row for row in files if row["relative_path"] == destination.relative_to(ROOT).as_posix()), None)
    if not isinstance(expected_record, dict) or receipt.get("POST_INPUT_MANIFEST_HASH") != expected_hash:
        raise IncrementalError("ORPHAN_CONFLICT:MANIFEST_RECEIPT_DISAGREE")
    if pointer.get("manifest_hash") == expected_hash:
        require(destination.exists() and sha256_file(destination) == expected_record.get("sha256"), "ORPHAN_CONFLICT:PUBLISHED_FILE_DRIFT")
        return {"state": "ALREADY_PUBLISHED", "manifest_hash": expected_hash}
    require(receipt.get("OLD_INPUT_MANIFEST_HASH") == pointer.get("manifest_hash"), "ORPHAN_CONFLICT:PREDECESSOR_DRIFT")
    source = destination if destination.exists() else candidate
    if not source.exists():
        return {"state": "READY_TO_RETRY"}
    if (source.stat().st_size != expected_record.get("file_size") or sha256_file(source) != expected_record.get("sha256")):
        raise IncrementalError("ORPHAN_CONFLICT:CANDIDATE_HASH_MISMATCH")
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate, destination)
    require(file_rows() == files, "ORPHAN_CONFLICT:POST_INSTALL_MANIFEST_MISMATCH")
    _switch_pointer(stage_day, expected_hash)
    return {"state": "ORPHAN_MATCHES_EXPECTED_CANDIDATE", "manifest_hash": expected_hash}


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
    atomic_json(stage_day / "promotion_intent.json", {"schema": "R3_INCREMENTAL_PROMOTION_INTENT_V01", "date": day,
                "candidate": candidate.relative_to(ROOT).as_posix(), "destination": destination.relative_to(ROOT).as_posix(),
                "old_manifest_hash": pointer["manifest_hash"]})
    promotion_checkpoint("INTENT_WRITTEN")
    write_parquet_atomic(candidate, frame.sort("symbol"), compression="zstd")
    promotion_checkpoint("CANDIDATE_WRITTEN")
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
    promotion_checkpoint("EVIDENCE_COMMITTED")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(candidate, destination)
    promotion_checkpoint("PHYSICAL_INSTALLED")
    require(file_rows() == files, "POST_PROMOTION_MANIFEST_MISMATCH")
    promotion_checkpoint("POST_INSTALL_VERIFIED")
    _switch_pointer(stage_day, manifest_hash)
    promotion_checkpoint("POINTER_SWITCHED")
    return {"file_n": len(files), "manifest_hash": manifest_hash, "quality": quality_receipt}


def run(day: str, *, execute: bool) -> dict[str, Any]:
    require(date.fromisoformat(day) <= date.today(), "FUTURE_TRADE_DATE_BLOCKED", day)
    calendar = calendar_state(day)
    if calendar == "NON_TRADING_DAY":
        return {"status": "NON_TRADING_DAY", "trade_date": day, "network_request_n": 0,
                "publication_attempted": False}
    if execute:
        with writer_lock():
            recovered = recover_promotion(day)
            if recovered["state"] in {"ALREADY_PUBLISHED", "ORPHAN_MATCHES_EXPECTED_CANDIDATE"}:
                return {"status": recovered["state"], "trade_date": day, "network_request_n": 0, **recovered}
            return _run_trading_day(day, execute=True)
    return _run_trading_day(day, execute=False)


def _run_trading_day(day: str, *, execute: bool) -> dict[str, Any]:
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
