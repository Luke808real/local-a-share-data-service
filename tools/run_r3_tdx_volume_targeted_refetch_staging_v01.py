#!/usr/bin/env python3
"""R3 targeted TDX daily-volume refetch into isolated repair staging only.

This tool never writes ``curated/daily_bars``.  It binds every provider read to
the audited V01.1 frozen repair-superset, writes only
``<data-root>/staging/r3_tdx_volume_targeted_refetch_v01``, and fails closed if
authority, provider coverage, or canonical-input immutability is not proven.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as importlib_metadata
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent / "audits"))
from r3_tdx_volume_rebuild_scope_v01 import build_input_file_manifest  # noqa: E402


TASK_NAME = "R3_TDX_VOLUME_TARGETED_REFETCH_STAGING_V01"
TARGET_FREEZE_REPORT = "R3_TDX_VOLUME_REBUILD_TARGET_FREEZE_V01_1"
TARGET_FREEZE_COMMIT = "f99918c786c1f0654ce642a460853141b723f61e"
INPUT_MANIFEST_HASH = "f9025a5cbc52d757fdc05d9e6ebb5f3c75c1cd93414a2f6c51bb83314594a6ec"
REPAIR_SUPERSET_MANIFEST_HASH = "1d579988015ef61fe06ae0eede91e2c63c2356a966f58363a6ebbd2f126e7e5b"
REPAIR_SUPERSET_KEY_N = 29_299
TDX_TARGET_SYMBOL_N = 4_577
TDX_PAGE_REQUEST_ESTIMATE = 11_600
CORRECTED_RUNTIME_COMMIT = "ecf57023d57dcf925e9da3aa0e023492abb1221d"
PAGE_SIZE = 800
MAX_PAGES_PER_SYMBOL = 1_000
MAX_SYMBOL_FETCH_ATTEMPTS = 2

DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
STAGING_DIRNAME = "r3_tdx_volume_targeted_refetch_v01"


class TargetedRefetchError(RuntimeError):
    """A fail-closed task-contract or staging-integrity failure."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_canonical_json(path: Path, payload: Any) -> str:
    """Atomically replace a task-owned staging/report artifact."""
    data = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return sha256_bytes(data)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TargetedRefetchError(f"unreadable JSON artifact: {path}: {exc}") from exc


def repo_root_for_tool() -> Path:
    return Path(__file__).resolve().parents[1]


def default_staging_root(data_root: Path) -> Path:
    return data_root / "staging" / STAGING_DIRNAME


def require_isolated_staging_root(data_root: Path, staging_root: Path) -> None:
    data_root = data_root.resolve()
    staging_root = staging_root.resolve()
    allowed_parent = (data_root / "staging").resolve()
    curated = (data_root / "curated").resolve()
    try:
        staging_root.relative_to(curated)
    except ValueError:
        pass
    else:
        raise TargetedRefetchError(f"canonical curated path is forbidden: {staging_root}")
    try:
        staging_root.relative_to(allowed_parent)
    except ValueError as exc:
        raise TargetedRefetchError(
            f"staging root must be under {allowed_parent}, got {staging_root}"
        ) from exc


def _runtime_provenance() -> dict[str, Any]:
    try:
        distribution = importlib_metadata.distribution("cnequity")
        direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
        commit = direct_url.get("vcs_info", {}).get("commit_id")
    except Exception as exc:  # noqa: BLE001 - this is an authority gate
        raise TargetedRefetchError(f"cannot inspect installed cnequity runtime: {exc}") from exc
    if commit != CORRECTED_RUNTIME_COMMIT:
        raise TargetedRefetchError(
            f"CORRECTED_RUNTIME_MISMATCH: {commit!r} != {CORRECTED_RUNTIME_COMMIT}"
        )
    return {
        "PACKAGE": "cnequity",
        "VERSION": distribution.version,
        "DIRECT_URL": direct_url,
        "COMMIT": commit,
        "DAILY_CATEGORY": 9,
        "DAILY_VOLUME_SEMANTIC": "IEEE754_FLOAT32_LOTS_THEN_INT_LOTS_X_100_SHARES",
    }


def _read_frozen_authority(repo_root: Path, data_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    implementation = repo_root / "reports" / "implementation"
    report = load_json(implementation / "R3_TDX_VOLUME_REBUILD_TARGET_FREEZE_V01_1.json")
    if report.get("REPORT") != TARGET_FREEZE_REPORT:
        raise TargetedRefetchError("TARGET_FREEZE_AUTHORITY_MISMATCH")
    if report.get("TARGET_FREEZE_V01_COMMIT") != TARGET_FREEZE_COMMIT:
        raise TargetedRefetchError("TARGET_FREEZE_COMMIT_MISMATCH")
    provenance = report.get("INPUT_PROVENANCE", {})
    if provenance.get("PRE_INPUT_MANIFEST_HASH") != INPUT_MANIFEST_HASH:
        raise TargetedRefetchError("FROZEN_INPUT_MANIFEST_HASH_MISMATCH")
    target_path = implementation / "R3_TDX_VOLUME_REBUILD_TARGET_MANIFEST_V01.json"
    target_rows = load_json(target_path)
    if not isinstance(target_rows, list) or len(target_rows) != REPAIR_SUPERSET_KEY_N:
        raise TargetedRefetchError("REPAIR_SUPERSET_KEY_N_MISMATCH")
    if sha256_file(target_path) != REPAIR_SUPERSET_MANIFEST_HASH:
        raise TargetedRefetchError("REPAIR_SUPERSET_MANIFEST_HASH_MISMATCH")
    keys = [(str(row.get("symbol")), str(row.get("trade_date"))) for row in target_rows]
    if len(set(keys)) != len(keys):
        raise TargetedRefetchError("FROZEN_TARGET_DUPLICATE_KEY")
    if {row.get("classification") for row in target_rows} != {
        "PROVABLY_AFFECTED",
        "AMBIGUOUS",
    }:
        raise TargetedRefetchError("FROZEN_TARGET_CLASSIFICATION_MISMATCH")
    if len({row["symbol"] for row in target_rows}) != TDX_TARGET_SYMBOL_N:
        raise TargetedRefetchError("TDX_TARGET_SYMBOL_N_MISMATCH")

    frozen_input = load_json(implementation / "R3_TDX_VOLUME_REBUILD_INPUT_MANIFEST_V01.json")
    live_input = build_input_file_manifest(data_root)
    if (
        live_input["INPUT_FILE_N"] != frozen_input.get("INPUT_FILE_N")
        or live_input["INPUT_MANIFEST_HASH"] != INPUT_MANIFEST_HASH
        or live_input["FILES"] != frozen_input.get("FILES")
    ):
        raise TargetedRefetchError("INPUT_MANIFEST_AUTHORITY_MISMATCH")
    return target_rows, {
        "TARGET_FREEZE_COMMIT": TARGET_FREEZE_COMMIT,
        "INPUT_MANIFEST_HASH": INPUT_MANIFEST_HASH,
        "REPAIR_SUPERSET_MANIFEST_HASH": REPAIR_SUPERSET_MANIFEST_HASH,
        "LIVE_INPUT_MANIFEST": live_input,
        "RUNTIME": _runtime_provenance(),
    }


def build_request_manifest(
    target_rows: list[dict[str, Any]], runtime: dict[str, Any]
) -> dict[str, Any]:
    """Create the deterministic frozen-key-to-symbol request plan."""
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_keys: set[tuple[str, str]] = set()
    for row in target_rows:
        symbol = str(row["symbol"])
        trade_date = str(row["trade_date"])
        key = (symbol, trade_date)
        if key in seen_keys:
            raise TargetedRefetchError("FROZEN_TARGET_DUPLICATE_KEY")
        seen_keys.add(key)
        if row.get("classification") not in {"PROVABLY_AFFECTED", "AMBIGUOUS"}:
            raise TargetedRefetchError("FROZEN_TARGET_CLASSIFICATION_MISMATCH")
        by_symbol[symbol].append(
            {
                "trade_date": trade_date,
                "classification": row["classification"],
                "old_volume": int(row["current_volume"]),
                "predicted_corrected_volume": row.get(
                    "corrected_volume_if_provably_affected"
                ),
            }
        )
    requests: list[dict[str, Any]] = []
    for order, symbol in enumerate(sorted(by_symbol), start=1):
        expected = sorted(by_symbol[symbol], key=lambda value: value["trade_date"])
        requests.append(
            {
                "request_order": order,
                "symbol": symbol,
                "target_key_n": len(expected),
                "oldest_target_date": expected[0]["trade_date"],
                "newest_target_date": expected[-1]["trade_date"],
                "fetch_start": expected[0]["trade_date"],
                "fetch_end": expected[-1]["trade_date"],
                "expected_target_keys": expected,
                "paging_plan": {
                    "CATEGORY": 9,
                    "PAGE_SIZE": PAGE_SIZE,
                    "INITIAL_OFFSET": 0,
                    "UPPER_BOUND": "TDX provider latest available daily bar; API has no end-date seek",
                    "STOP_CONDITION": "empty page OR short page OR page_min_date < fetch_start",
                    "IN_SPAN_NON_TARGET_ROWS": "recorded as diagnostic only; never staged",
                },
                "corrected_tdx_runtime": {
                    "PACKAGE": runtime["PACKAGE"],
                    "VERSION": runtime["VERSION"],
                    "COMMIT": runtime["COMMIT"],
                    "DAILY_VOLUME_SEMANTIC": runtime["DAILY_VOLUME_SEMANTIC"],
                },
            }
        )
    if len(requests) != TDX_TARGET_SYMBOL_N or sum(r["target_key_n"] for r in requests) != REPAIR_SUPERSET_KEY_N:
        raise TargetedRefetchError("REQUEST_MANIFEST_CARDINALITY_MISMATCH")
    return {
        "TASK": TASK_NAME,
        "TARGET_FREEZE_AUTHORITY": TARGET_FREEZE_REPORT,
        "TARGET_FREEZE_COMMIT": TARGET_FREEZE_COMMIT,
        "INPUT_MANIFEST_HASH": INPUT_MANIFEST_HASH,
        "REPAIR_SUPERSET_MANIFEST_HASH": REPAIR_SUPERSET_MANIFEST_HASH,
        "TARGET_KEY_N": REPAIR_SUPERSET_KEY_N,
        "TARGET_SYMBOL_N": TDX_TARGET_SYMBOL_N,
        "TDX_PAGE_REQUEST_ESTIMATE": TDX_PAGE_REQUEST_ESTIMATE,
        "TDX_PAGE_REQUEST_ESTIMATE_IS_HARD_CAP": False,
        "CORRECTED_TDX_RUNTIME": runtime,
        "REQUESTS": requests,
    }


def ensure_request_manifest(staging_root: Path, manifest: dict[str, Any]) -> tuple[Path, str]:
    path = staging_root / "request_manifest.json"
    expected_bytes = canonical_json_bytes(manifest)
    expected_hash = sha256_bytes(expected_bytes)
    if path.exists():
        if path.read_bytes() != expected_bytes:
            raise TargetedRefetchError("REQUEST_MANIFEST_EXISTING_BYTES_MISMATCH")
    else:
        write_canonical_json(path, manifest)
    return path, expected_hash


def _symbol_artifact_path(staging_root: Path, symbol: str) -> Path:
    return staging_root / "symbol_results" / f"{symbol.replace('.', '_')}.json"


def _parse_date(value: Any) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except Exception:  # noqa: BLE001 - invalid provider value is handled below
            return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            return None
    return None


def _fresh_volume_from_lots(value: Any) -> tuple[int | None, str | None]:
    try:
        lots = float(value)
    except (TypeError, ValueError, OverflowError):
        return None, "NON_NUMERIC_VOLUME"
    if not math.isfinite(lots):
        return None, "NONFINITE_VOLUME"
    if lots < 0:
        return None, "NEGATIVE_VOLUME"
    whole_lots = int(lots)
    if whole_lots > (2**63 - 1) // 100:
        return None, "VOLUME_OUT_OF_RANGE"
    shares = whole_lots * 100
    if shares <= 0:
        return None, "NONPOSITIVE_TARGET_VOLUME"
    return shares, None


def reconcile_symbol(
    request: dict[str, Any],
    provider_rows: list[dict[str, Any]],
    *,
    provider_failed: str | None,
    page_request_n: int,
    tdx_hosts: list[str],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    """Make one durable per-key result for every frozen key of a symbol."""
    symbol = request["symbol"]
    expected = {item["trade_date"]: item for item in request["expected_target_keys"]}
    rows_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unexpected_symbol_n = 0
    in_span_non_target_row_n = 0
    for row in provider_rows:
        if row.get("symbol") != symbol:
            unexpected_symbol_n += 1
            continue
        trade_date = str(row.get("trade_date"))
        if trade_date not in expected:
            in_span_non_target_row_n += 1
            continue
        rows_by_date[trade_date].append(row)

    results: list[dict[str, Any]] = []
    counters = defaultdict(int)
    for trade_date in sorted(expected):
        target = expected[trade_date]
        base = {
            "symbol": symbol,
            "trade_date": trade_date,
            "classification": target["classification"],
            "old_volume": target["old_volume"],
            "predicted_corrected_volume": target["predicted_corrected_volume"],
            "fresh_tdx_volume": None,
            "fetch_status": None,
            "fresh_vs_old": "UNKNOWN",
            "fresh_equals_predicted_corrected": None,
            "provider_runtime": runtime,
            "tdx_hosts": tdx_hosts,
            "provider_page_request_n_for_symbol": page_request_n,
            "unexpected_symbol_n_for_symbol": unexpected_symbol_n,
            "in_span_non_target_row_n_for_symbol": in_span_non_target_row_n,
        }
        if provider_failed is not None:
            base["fetch_status"] = "PROVIDER_FAILED"
            base["provider_error"] = provider_failed
            counters["PROVIDER_FAILED_KEY_N"] += 1
        else:
            matched = rows_by_date.get(trade_date, [])
            if not matched:
                base["fetch_status"] = "MISSING_TARGET_KEY"
                counters["MISSING_KEY_N"] += 1
            elif len(matched) != 1:
                base["fetch_status"] = "DUPLICATE_PROVIDER_ROW"
                base["provider_row_n"] = len(matched)
                counters["DUPLICATE_KEY_N"] += 1
            else:
                provider = matched[0]
                fresh = provider.get("fresh_tdx_volume")
                error = provider.get("volume_error")
                if fresh is None or error is not None:
                    base["fetch_status"] = "INVALID_VOLUME"
                    base["provider_error"] = error or "MISSING_FRESH_VOLUME"
                    counters["INVALID_VOLUME_KEY_N"] += 1
                else:
                    base["fresh_tdx_volume"] = int(fresh)
                    base["fetch_status"] = "RESOLVED"
                    base["fresh_vs_old"] = (
                        "UNCHANGED" if int(fresh) == target["old_volume"] else "CHANGED"
                    )
                    if target["classification"] == "PROVABLY_AFFECTED":
                        base["fresh_equals_predicted_corrected"] = (
                            int(fresh) == int(target["predicted_corrected_volume"])
                        )
                    counters["FETCHED_KEY_N"] += 1
                    counters["RESOLVED_KEY_N"] += 1
                    counters[f"{base['fresh_vs_old']}_KEY_N"] += 1
        counters[f"{target['classification']}_{base['fetch_status']}_N"] += 1
        results.append(base)
    return {
        "TASK": TASK_NAME,
        "REQUEST_MANIFEST_HASH": None,
        "REQUEST": request,
        "SYMBOL": symbol,
        "STATUS": "PROVIDER_FAILED" if provider_failed is not None else "TERMINAL",
        "TDX_HOSTS": tdx_hosts,
        "TDX_PAGE_REQUEST_N": page_request_n,
        "IN_SPAN_NON_TARGET_ROW_N": in_span_non_target_row_n,
        "UNEXPECTED_SYMBOL_N": unexpected_symbol_n,
        "COUNTERS": dict(sorted(counters.items())),
        "RESULTS": results,
    }


class _CountingQuotes:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.page_request_n = 0

    @property
    def server(self) -> Any:
        return getattr(self.inner, "server", None)

    def bars(self, *args: Any, **kwargs: Any) -> Any:
        self.page_request_n += 1
        return self.inner.bars(*args, **kwargs)

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()


def _fetch_one_symbol_pages(client: _CountingQuotes, request: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Fetch only the requested symbol, retaining duplicate target rows."""
    symbol = request["symbol"]
    code, exchange = symbol.split(".")
    market = 1 if exchange == "SH" else (0 if exchange == "SZ" else 2)
    start = request["fetch_start"]
    end = request["fetch_end"]
    offset = 0
    page_n = 0
    seen_pages: set[str] = set()
    provider_rows: list[dict[str, Any]] = []
    in_span_non_target_row_n = 0
    expected_dates = {item["trade_date"] for item in request["expected_target_keys"]}
    while True:
        page_n += 1
        if page_n > MAX_PAGES_PER_SYMBOL:
            raise TargetedRefetchError(f"TDX_PAGINATION_EXCEEDED:{symbol}")
        raw = client.bars(symbol=code, frequency=9, market=market, start=offset, offset=PAGE_SIZE)
        if raw is None or len(raw) == 0:
            break
        signature = sha256_bytes(canonical_json_bytes(raw))
        if signature in seen_pages:
            raise TargetedRefetchError(f"TDX_PAGINATION_DID_NOT_ADVANCE:{symbol}:{offset}")
        seen_pages.add(signature)
        page_dates: list[str] = []
        for raw_row in raw:
            trade_date = _parse_date(raw_row.get("datetime", raw_row.get("date")))
            if trade_date is None:
                raise TargetedRefetchError(f"TDX_INVALID_PROVIDER_DATE:{symbol}")
            page_dates.append(trade_date)
            if trade_date < start or trade_date > end:
                continue
            if trade_date not in expected_dates:
                in_span_non_target_row_n += 1
                continue
            fresh, volume_error = _fresh_volume_from_lots(
                raw_row.get("volume", raw_row.get("vol"))
            )
            provider_rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "fresh_tdx_volume": fresh,
                    "volume_error": volume_error,
                }
            )
        if not page_dates:
            raise TargetedRefetchError(f"TDX_EMPTY_DATE_PAGE:{symbol}")
        if min(page_dates) < start or len(raw) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return provider_rows, in_span_non_target_row_n


class TdxTargetFetcher:
    """Single-session, bounded-retry corrected TDX fetcher for this task."""

    def __init__(self, runtime: dict[str, Any], max_attempts: int = MAX_SYMBOL_FETCH_ATTEMPTS) -> None:
        self.runtime = runtime
        self.max_attempts = max_attempts
        self._lock: Any = None
        self._client: _CountingQuotes | None = None

    def __enter__(self) -> "TdxTargetFetcher":
        from cnequity.adapters.tdx_protocol.session import TDX_SESSION_LOCK

        self._lock = TDX_SESSION_LOCK
        self._lock.acquire()
        return self

    def __exit__(self, *_: Any) -> None:
        self._close_client()
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def _close_client(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _ensure_client(self) -> _CountingQuotes:
        if self._client is None:
            from cnequity.adapters.tdx_protocol.client import _connect_with_retry

            self._client = _CountingQuotes(_connect_with_retry())
        return self._client

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        from cnequity.adapters.tdx_protocol.client import reset_tdx_server_cache

        hosts: list[str] = []
        errors: list[str] = []
        before = self._client.page_request_n if self._client is not None else 0
        total_requests = 0
        for attempt in range(1, self.max_attempts + 1):
            try:
                client = self._ensure_client()
                current_before = client.page_request_n
                if client.server is not None:
                    hosts.append(f"{client.server[0]}:{client.server[1]}")
                rows, non_target_n = _fetch_one_symbol_pages(client, request)
                total_requests += client.page_request_n - current_before
                return {
                    "provider_rows": rows,
                    "provider_failed": None,
                    "page_request_n": total_requests,
                    "tdx_hosts": sorted(set(hosts)),
                    "in_span_non_target_row_n": non_target_n,
                    "attempt_n": attempt,
                }
            except Exception as exc:  # noqa: BLE001 - terminal evidence is required
                if self._client is not None:
                    total_requests += max(0, self._client.page_request_n - before - total_requests)
                errors.append(f"attempt={attempt}:{type(exc).__name__}:{exc}")
                reset_tdx_server_cache()
                self._close_client()
                before = 0
        return {
            "provider_rows": [],
            "provider_failed": " | ".join(errors),
            "page_request_n": total_requests,
            "tdx_hosts": sorted(set(hosts)),
            "in_span_non_target_row_n": 0,
            "attempt_n": self.max_attempts,
        }


def _validate_symbol_artifact(
    payload: dict[str, Any], request: dict[str, Any], request_manifest_hash: str
) -> None:
    if payload.get("TASK") != TASK_NAME or payload.get("REQUEST_MANIFEST_HASH") != request_manifest_hash:
        raise TargetedRefetchError("SYMBOL_RESULT_AUTHORITY_MISMATCH")
    if payload.get("REQUEST") != request or payload.get("SYMBOL") != request["symbol"]:
        raise TargetedRefetchError("SYMBOL_RESULT_REQUEST_MISMATCH")
    expected_dates = [row["trade_date"] for row in request["expected_target_keys"]]
    results = payload.get("RESULTS")
    if not isinstance(results, list) or [row.get("trade_date") for row in results] != expected_dates:
        raise TargetedRefetchError("SYMBOL_RESULT_KEYSET_MISMATCH")


def _write_checkpoint(staging_root: Path, request_manifest_hash: str, requests: list[dict[str, Any]]) -> None:
    completed: list[dict[str, str]] = []
    for request in requests:
        path = _symbol_artifact_path(staging_root, request["symbol"])
        if path.exists():
            completed.append({"symbol": request["symbol"], "sha256": sha256_file(path)})
    payload = {
        "TASK": TASK_NAME,
        "REQUEST_MANIFEST_HASH": request_manifest_hash,
        "COMPLETED_SYMBOL_N": len(completed),
        "COMPLETED_SYMBOLS": completed,
        "COMPLETED_SYMBOLS_HASH": sha256_bytes(canonical_json_bytes(completed)),
    }
    write_canonical_json(staging_root / "checkpoint.json", payload)


def _aggregate_symbol_artifacts(
    staging_root: Path, requests: list[dict[str, Any]], request_manifest_hash: str
) -> list[dict[str, Any]]:
    all_results: list[dict[str, Any]] = []
    for request in requests:
        path = _symbol_artifact_path(staging_root, request["symbol"])
        if not path.exists():
            raise TargetedRefetchError("SYMBOL_RESULT_MISSING_AT_AGGREGATION")
        payload = load_json(path)
        _validate_symbol_artifact(payload, request, request_manifest_hash)
        all_results.extend(payload["RESULTS"])
    all_results.sort(key=lambda row: (row["symbol"], row["trade_date"]))
    return all_results


def reconcile_all(
    results: list[dict[str, Any]],
    *,
    target_key_n: int,
    target_symbol_n: int,
) -> dict[str, Any]:
    keys = [(row["symbol"], row["trade_date"]) for row in results]
    duplicate_staging_key_n = len(keys) - len(set(keys))
    status_counts: dict[str, int] = defaultdict(int)
    bucket_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    bucket_freshness: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    actual_request_n = 0
    seen_symbols: set[str] = set()
    per_symbol_pages: dict[str, int] = {}
    unexpected_by_symbol: dict[str, int] = {}
    in_span_non_target_by_symbol: dict[str, int] = {}
    for row in results:
        status_counts[row["fetch_status"]] += 1
        bucket_counts[row["classification"]][row["fetch_status"]] += 1
        bucket_freshness[row["classification"]][row["fresh_vs_old"]] += 1
        symbol = row["symbol"]
        if symbol not in seen_symbols:
            seen_symbols.add(symbol)
            per_symbol_pages[symbol] = int(row["provider_page_request_n_for_symbol"])
            unexpected_by_symbol[symbol] = int(row["unexpected_symbol_n_for_symbol"])
            in_span_non_target_by_symbol[symbol] = int(
                row["in_span_non_target_row_n_for_symbol"]
            )
    actual_request_n = sum(per_symbol_pages.values())
    unexpected_symbol_n = sum(unexpected_by_symbol.values())
    in_span_non_target_row_n = sum(in_span_non_target_by_symbol.values())
    fetched = status_counts["RESOLVED"]
    missing = status_counts["MISSING_TARGET_KEY"]
    duplicate = status_counts["DUPLICATE_PROVIDER_ROW"] + duplicate_staging_key_n
    provider_failed = status_counts["PROVIDER_FAILED"]
    invalid = status_counts["INVALID_VOLUME"]
    changed = sum(1 for row in results if row["fresh_vs_old"] == "CHANGED")
    unchanged = sum(1 for row in results if row["fresh_vs_old"] == "UNCHANGED")
    affected_prediction_mismatch = sum(
        1
        for row in results
        if row["classification"] == "PROVABLY_AFFECTED"
        and row["fetch_status"] == "RESOLVED"
        and row["fresh_equals_predicted_corrected"] is not True
    )
    staging_complete = (
        len(results) == target_key_n
        and len(set(keys)) == target_key_n
        and len(seen_symbols) == target_symbol_n
        and fetched == target_key_n
        and missing == 0
        and duplicate == 0
        and provider_failed == 0
        and invalid == 0
        and unexpected_symbol_n == 0
        and affected_prediction_mismatch == 0
    )
    return {
        "TASK": TASK_NAME,
        "TARGET_KEY_N": target_key_n,
        "TARGET_SYMBOL_N": target_symbol_n,
        "STAGING_RESULT_KEY_N": len(results),
        "STAGING_RESULT_SYMBOL_N": len(seen_symbols),
        "ACTUAL_TDX_REQUEST_N": actual_request_n,
        "TDX_PAGE_REQUEST_ESTIMATE": TDX_PAGE_REQUEST_ESTIMATE,
        "TDX_PAGE_REQUEST_ESTIMATE_IS_HARD_CAP": False,
        "FETCHED_KEY_N": fetched,
        "RESOLVED_KEY_N": fetched,
        "CHANGED_KEY_N": changed,
        "UNCHANGED_KEY_N": unchanged,
        "MISSING_KEY_N": missing,
        "DUPLICATE_KEY_N": duplicate,
        "PROVIDER_FAILED_KEY_N": provider_failed,
        "INVALID_VOLUME_KEY_N": invalid,
        "UNEXPECTED_SYMBOL_N": unexpected_symbol_n,
        "IN_SPAN_NON_TARGET_ROW_N": in_span_non_target_row_n,
        "PROVABLY_AFFECTED_PREDICTION_MISMATCH_N": affected_prediction_mismatch,
        "BY_CLASSIFICATION_AND_STATUS": {
            key: dict(sorted(value.items())) for key, value in sorted(bucket_counts.items())
        },
        "BY_CLASSIFICATION_AND_FRESH_VS_OLD": {
            key: dict(sorted(value.items())) for key, value in sorted(bucket_freshness.items())
        },
        "STAGING_COMPLETE": staging_complete,
    }


def _quality_gate(
    reconciliation: dict[str, Any],
    *,
    canonical_pre: dict[str, Any],
    canonical_post: dict[str, Any],
) -> dict[str, Any]:
    canonical_unchanged = (
        canonical_pre["INPUT_FILE_N"] == canonical_post["INPUT_FILE_N"]
        and canonical_pre["INPUT_MANIFEST_HASH"] == canonical_post["INPUT_MANIFEST_HASH"]
        and canonical_pre["FILES"] == canonical_post["FILES"]
    )
    return {
        "TASK": TASK_NAME,
        "STAGING_COMPLETE": reconciliation["STAGING_COMPLETE"],
        "CANONICAL_INPUT_UNCHANGED": canonical_unchanged,
        "CANONICAL_WRITE_EXECUTED": False if canonical_unchanged else "UNKNOWN_INPUT_DRIFT",
        "QUALITY_PASS": bool(reconciliation["STAGING_COMPLETE"] and canonical_unchanged),
        "BLOCKERS": [
            name
            for name, failed in (
                ("STAGING_INCOMPLETE", not reconciliation["STAGING_COMPLETE"]),
                ("CANONICAL_INPUT_DRIFT", not canonical_unchanged),
            )
            if failed
        ],
    }


def run_staging(
    *,
    repo_root: Path,
    data_root: Path,
    staging_root: Path,
    fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute or resume the exact frozen target set and produce all receipts."""
    require_isolated_staging_root(data_root, staging_root)
    target_rows, authority = _read_frozen_authority(repo_root, data_root)
    request_manifest = build_request_manifest(target_rows, authority["RUNTIME"])
    request_path, request_manifest_hash = ensure_request_manifest(staging_root, request_manifest)
    requests = request_manifest["REQUESTS"]

    own_fetcher = fetcher is None
    active_fetcher: Any = fetcher
    if own_fetcher:
        active_fetcher = TdxTargetFetcher(authority["RUNTIME"])
        active_fetcher.__enter__()
    try:
        for request in requests:
            symbol_path = _symbol_artifact_path(staging_root, request["symbol"])
            if symbol_path.exists():
                _validate_symbol_artifact(load_json(symbol_path), request, request_manifest_hash)
                continue
            observed = active_fetcher(request)
            symbol_payload = reconcile_symbol(
                request,
                observed["provider_rows"],
                provider_failed=observed["provider_failed"],
                page_request_n=int(observed["page_request_n"]),
                tdx_hosts=list(observed.get("tdx_hosts", [])),
                runtime=authority["RUNTIME"],
            )
            symbol_payload["REQUEST_MANIFEST_HASH"] = request_manifest_hash
            symbol_payload["IN_SPAN_NON_TARGET_ROW_N"] = int(
                observed.get("in_span_non_target_row_n", 0)
            )
            for row in symbol_payload["RESULTS"]:
                row["in_span_non_target_row_n_for_symbol"] = symbol_payload[
                    "IN_SPAN_NON_TARGET_ROW_N"
                ]
            symbol_payload["FETCH_ATTEMPT_N"] = int(observed.get("attempt_n", 1))
            write_canonical_json(symbol_path, symbol_payload)
            _write_checkpoint(staging_root, request_manifest_hash, requests)
    finally:
        if own_fetcher:
            active_fetcher.__exit__(None, None, None)

    results = _aggregate_symbol_artifacts(staging_root, requests, request_manifest_hash)
    staging_output_hash = write_canonical_json(staging_root / "per_key_staging_results.json", results)
    reconciliation = reconcile_all(
        results,
        target_key_n=REPAIR_SUPERSET_KEY_N,
        target_symbol_n=TDX_TARGET_SYMBOL_N,
    )
    canonical_post = build_input_file_manifest(data_root)
    quality = _quality_gate(
        reconciliation,
        canonical_pre=authority["LIVE_INPUT_MANIFEST"],
        canonical_post=canonical_post,
    )
    receipt = {
        "TASK": TASK_NAME,
        "TARGET_FREEZE_AUTHORITY": TARGET_FREEZE_REPORT,
        "TARGET_FREEZE_COMMIT": TARGET_FREEZE_COMMIT,
        "INPUT_MANIFEST_HASH": INPUT_MANIFEST_HASH,
        "REPAIR_SUPERSET_MANIFEST_HASH": REPAIR_SUPERSET_MANIFEST_HASH,
        "REQUEST_MANIFEST_PATH": str(request_path),
        "REQUEST_MANIFEST_HASH": request_manifest_hash,
        "STAGING_OUTPUT_PATH": str(staging_root / "per_key_staging_results.json"),
        "STAGING_OUTPUT_HASH": staging_output_hash,
        "RECONCILIATION": reconciliation,
        "QUALITY_GATE": quality,
        "SAFETY": {
            "CANONICAL_WRITE_EXECUTED": quality["CANONICAL_WRITE_EXECUTED"],
            "300546_MISSING_DAYS_MUTATED": False,
            "R4A9_CHECKPOINT_MUTATED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRECLOSE_COMPLETE": False,
            "PRODUCTION": False,
            "FORWARD": False,
            "TRADEPLAN": False,
            "BAOSTOCK_ROLE": "SECONDARY_AUDIT_ONLY_NOT_EXECUTED",
        },
    }
    write_canonical_json(staging_root / "reconciliation.json", reconciliation)
    write_canonical_json(staging_root / "quality_gate.json", quality)
    receipt_hash = write_canonical_json(staging_root / "execution_receipt.json", receipt)
    receipt["EXECUTION_RECEIPT_HASH"] = receipt_hash
    return receipt


def _markdown_report(receipt: dict[str, Any], *, base_head: str, head: str) -> str:
    reconciliation = receipt["RECONCILIATION"]
    quality = receipt["QUALITY_GATE"]
    safety = receipt["SAFETY"]
    return "\n".join(
        [
            f"# {TASK_NAME}",
            "",
            f"BASE_HEAD: `{base_head}`",
            f"HEAD: `{head}`",
            "AUTHOR_STATUS: `PASS_PENDING_SOL_AUDIT`" if quality["QUALITY_PASS"] else "AUTHOR_STATUS: `FAIL_CLOSED`",
            "",
            "## Authority and artifacts",
            "",
            f"- TARGET_FREEZE_COMMIT: `{receipt['TARGET_FREEZE_COMMIT']}`",
            f"- INPUT_MANIFEST_HASH: `{receipt['INPUT_MANIFEST_HASH']}`",
            f"- REPAIR_SUPERSET_MANIFEST_HASH: `{receipt['REPAIR_SUPERSET_MANIFEST_HASH']}`",
            f"- REQUEST_MANIFEST_HASH: `{receipt['REQUEST_MANIFEST_HASH']}`",
            f"- STAGING_OUTPUT_HASH: `{receipt['STAGING_OUTPUT_HASH']}`",
            "",
            "## Reconciliation",
            "",
            f"- TARGET_KEY_N: {reconciliation['TARGET_KEY_N']}",
            f"- TARGET_SYMBOL_N: {reconciliation['TARGET_SYMBOL_N']}",
            f"- ACTUAL_TDX_REQUEST_N: {reconciliation['ACTUAL_TDX_REQUEST_N']}",
            f"- TDX_PAGE_REQUEST_ESTIMATE: {reconciliation['TDX_PAGE_REQUEST_ESTIMATE']} (not a hard cap)",
            f"- FETCHED_KEY_N: {reconciliation['FETCHED_KEY_N']}",
            f"- CHANGED_KEY_N: {reconciliation['CHANGED_KEY_N']}",
            f"- UNCHANGED_KEY_N: {reconciliation['UNCHANGED_KEY_N']}",
            f"- MISSING_KEY_N: {reconciliation['MISSING_KEY_N']}",
            f"- DUPLICATE_KEY_N: {reconciliation['DUPLICATE_KEY_N']}",
            f"- PROVIDER_FAILED_KEY_N: {reconciliation['PROVIDER_FAILED_KEY_N']}",
            f"- PROVABLY_AFFECTED_PREDICTION_MISMATCH_N: {reconciliation['PROVABLY_AFFECTED_PREDICTION_MISMATCH_N']}",
            f"- STAGING_COMPLETE: {str(reconciliation['STAGING_COMPLETE']).lower()}",
            "",
            "## Safety",
            "",
            *[f"- {key}={str(value).lower() if isinstance(value, bool) else value}" for key, value in safety.items()],
            f"- QUALITY_PASS={str(quality['QUALITY_PASS']).lower()}",
            "",
        ]
    )


def write_repo_reports(receipt: dict[str, Any], *, repo_root: Path, base_head: str, head: str) -> tuple[Path, Path]:
    implementation = repo_root / "reports" / "implementation"
    json_path = implementation / "R3_TDX_VOLUME_TARGETED_REFETCH_STAGING_V01.json"
    markdown_path = implementation / "R3_TDX_VOLUME_TARGETED_REFETCH_STAGING_V01.md"
    report = {
        "REPORT": TASK_NAME,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT" if receipt["QUALITY_GATE"]["QUALITY_PASS"] else "FAIL_CLOSED",
        "BASE_HEAD": base_head,
        "HEAD": head,
        **receipt,
    }
    write_canonical_json(json_path, report)
    markdown_path.write_text(_markdown_report(receipt, base_head=base_head, head=head), encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--staging-root", type=Path, default=None)
    parser.add_argument("--base-head", required=True)
    args = parser.parse_args()
    repo_root = repo_root_for_tool()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=repo_root,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if args.base_head != head:
        print("BASE_HEAD_MISMATCH", file=sys.stderr)
        return 2
    staging_root = args.staging_root or default_staging_root(args.data_root)
    try:
        receipt = run_staging(
            repo_root=repo_root,
            data_root=args.data_root,
            staging_root=staging_root,
        )
        write_repo_reports(receipt, repo_root=repo_root, base_head=args.base_head, head=head)
    except TargetedRefetchError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if receipt["QUALITY_GATE"]["QUALITY_PASS"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
