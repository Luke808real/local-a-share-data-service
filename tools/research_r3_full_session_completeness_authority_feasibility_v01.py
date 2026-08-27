#!/usr/bin/env python3
"""Bounded R3 full-session completeness authority feasibility pilot.

This module has one deliberately narrow network boundary: ``fetch_pilot``.
Everything else is local read-only inventory, deterministic plan construction,
normalization, and report generation.  The pilot never writes the data root,
never imports TDX, and never resumes an R4A checkpoint.

The authority contract tested here is:

* ``tradestatus=1`` -> ``EXPECTED_BAR``;
* ``tradestatus=0`` -> ``NOT_EXPECTED_BAR``;
* an absent provider row inside an independently bounded instrument lifetime
  -> ``UNKNOWN``;
* a date outside the independent list/delist lifetime ->
  ``NOT_EXPECTED_BAR`` by lifecycle authority, not by row absence.

The current canonical bars are used only to select information-rich pilot
windows and to cross-check known gaps.  They are never used to manufacture
the expected-key universe.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import sys
import time
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools" / "audits"))

from r3_tdx_volume_rebuild_scope_v01 import build_input_file_manifest  # noqa: E402


BASE_HEAD = "7bb4e4ea1898a138aa400d9ea1778066004c8b63"
BRANCH = "codex/r3-full-session-completeness-authority-feasibility-v01"
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")
WINDOW_START = date(2016, 1, 1)
WINDOW_END = date(2026, 8, 17)
FORMAL_IDENTITY_N = 5456
FORMAL_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"
INPUT_FILE_N = 2580
INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
QUERY_FIELDS = "date,code,open,high,low,close,volume,amount,preclose,tradestatus"
QUERY_FREQUENCY = "d"
QUERY_ADJUSTFLAG = "3"
ALTERNATE_ADJUSTFLAG = "1"
PROVIDER_DISTRIBUTION = "baostock"
PROVIDER_VERSION = "0.9.3"
PROVIDER_RUNTIME = "baostock-0.9.3"
PILOT_MIN_SYMBOL_N = 30
PILOT_MAX_SYMBOL_N = 50
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = (1.0, 2.0)
MIN_INTERVAL_SECONDS = 1.0

REPORT_STEM = "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_FEASIBILITY_V01"
OUTPUT_DIR = REPO_ROOT / "reports" / "research"
REQUEST_MANIFEST_PATH = OUTPUT_DIR / f"{REPORT_STEM}_REQUEST_MANIFEST.json"
RAW_RECEIPT_PATH = OUTPUT_DIR / f"{REPORT_STEM}_RAW_RECEIPT.json"
NORMALIZED_RECEIPT_PATH = OUTPUT_DIR / f"{REPORT_STEM}_NORMALIZED_RECEIPT.json"
REPORT_JSON_PATH = OUTPUT_DIR / f"{REPORT_STEM}.json"
REPORT_MD_PATH = OUTPUT_DIR / f"{REPORT_STEM}.md"


class FeasibilityError(RuntimeError):
    """Fail-closed error for a malformed authority or pilot artifact."""


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for every frozen hash."""

    def default(item: Any) -> str:
        if isinstance(item, date):
            return item.isoformat()
        raise TypeError(f"not JSON serializable: {type(item)!r}")

    return json.dumps(
        value,
        default=default,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def write_json(path: Path, payload: Any) -> None:
    """Write only repository research artifacts, never data-root files."""

    resolved = path.resolve()
    output_root = OUTPUT_DIR.resolve()
    if not resolved.is_relative_to(output_root):
        raise FeasibilityError(f"RESEARCH_OUTPUT_OUTSIDE_REPO_REPORTS:{resolved}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


def write_text(path: Path, text: str) -> None:
    resolved = path.resolve()
    output_root = OUTPUT_DIR.resolve()
    if not resolved.is_relative_to(output_root):
        raise FeasibilityError(f"RESEARCH_OUTPUT_OUTSIDE_REPO_REPORTS:{resolved}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        if not text or any(ch in text for ch in (".", "e", "E")):
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


def identity_hash(symbols: Iterable[str]) -> str:
    return sha256_bytes(json.dumps(sorted(symbols), separators=(",", ":")).encode())


def bs_code(symbol: str) -> str:
    code, exchange = str(symbol).split(".")
    if exchange not in {"SH", "SZ"} or len(code) != 6 or not code.isdigit():
        raise FeasibilityError(f"NON_SHSZ_SYMBOL:{symbol}")
    return ("sh" if exchange == "SH" else "sz") + "." + code


def load_formal_identity_symbols(data_root: Path) -> tuple[list[str], dict[str, str]]:
    """Load the frozen identity from its independent local receipts."""

    identity_path = data_root / "meta" / "asl" / "r3" / "r3-identity-receipt.json"
    progress_path = data_root / "meta" / "asl" / "r3" / "r3-quarterly-roster-audit-progress-v074.json"
    try:
        identity_receipt = json.loads(identity_path.read_text(encoding="utf-8"))
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise FeasibilityError(f"FORMAL_IDENTITY_RECEIPT_UNREADABLE:{exc}") from exc
    if (
        identity_receipt.get("formal_identity_n") != FORMAL_IDENTITY_N
        or identity_receipt.get("formal_identity_hash") != FORMAL_IDENTITY_HASH
        or identity_receipt.get("shsz_identity_complete") is not True
        or progress.get("formal_identity_n", FORMAL_IDENTITY_N) != FORMAL_IDENTITY_N
        or progress.get("formal_identity_hash") != FORMAL_IDENTITY_HASH
    ):
        raise FeasibilityError("FORMAL_IDENTITY_RECEIPT_MISMATCH")
    union = progress.get("roster_union_symbols")
    not_observable = identity_receipt.get("roster_not_observable_identity_sample")
    if not isinstance(union, list) or not isinstance(not_observable, list):
        raise FeasibilityError("FORMAL_IDENTITY_SYMBOL_LIST_UNAVAILABLE")
    symbols = sorted({str(value) for value in [*union, *not_observable]})
    if len(symbols) != FORMAL_IDENTITY_N or identity_hash(symbols) != FORMAL_IDENTITY_HASH:
        raise FeasibilityError("FORMAL_IDENTITY_RECONSTRUCTION_MISMATCH")
    return symbols, {
        "identity_receipt": str(identity_path),
        "roster_progress_receipt": str(progress_path),
        "authority": str(identity_receipt.get("shsz_identity_authority", "UNKNOWN")),
    }


def board_key(symbol: str) -> str:
    code, exchange = symbol.split(".")
    prefix = code[:3]
    return f"{exchange}_{prefix}"


def _status_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in {"tradestatus", "trading_status", "trade_status", "provider_tradestatus"}


def _date_key(key: str) -> bool:
    return key.lower() in {"date", "trade_date", "trading_date"}


def _symbol_key(key: str) -> bool:
    return key.lower() in {"symbol", "code", "provider_code"}


def _walk_status_rows(value: Any) -> tuple[int, set[str], set[str]]:
    """Count receipt row-like status observations without treating a report as full authority."""

    row_like_n = 0
    symbols: set[str] = set()
    dates: set[str] = set()

    def visit(item: Any) -> None:
        nonlocal row_like_n
        if isinstance(item, dict):
            keys = list(item)
            if any(_status_key(key) for key in keys) and any(_date_key(key) for key in keys) and any(
                _symbol_key(key) for key in keys
            ):
                row_like_n += 1
                for key, field in item.items():
                    if _symbol_key(key) and field is not None:
                        symbols.add(str(field))
                    if _date_key(key) and field is not None:
                        dates.add(str(field))
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return row_like_n, symbols, dates


def inventory_local_authority(data_root: Path, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Inventory local status receipts; deliberately does not infer completeness."""

    roots = [data_root / "meta" / "asl" / "r3", repo_root / "reports"]
    candidates: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            # The current feasibility artifacts are outputs, not prior local
            # authority.  Excluding them prevents the report inventory from
            # becoming self-referential on an offline regeneration.
            if REPORT_STEM in path.name:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            row_like_n, symbols, dates = _walk_status_rows(payload)
            if row_like_n:
                candidates.append(
                    {
                        "path": str(path),
                        "row_like_n": row_like_n,
                        "symbol_n": len(symbols),
                        "date_n": len(dates),
                        "scope": "BOUNDED_RECEIPT_OR_DIAGNOSTIC",
                    }
                )
    status_dataset = data_root / "curated" / "trading_status"
    complete_candidates: list[dict[str, Any]] = []
    if status_dataset.exists():
        complete_candidates.append(
            {
                "path": str(status_dataset),
                "reason": "status dataset exists; schema/coverage requires separate validation",
            }
        )
    # A receipt is a full authority only when it explicitly states the full
    # contract and frozen row/symbol scope.  Existing bounded receipts do not.
    explicit_full = [
        row
        for row in candidates
        if row["row_like_n"] >= FORMAL_IDENTITY_N
        and row["symbol_n"] == FORMAL_IDENTITY_N
        and row["scope"] == "FULL_SESSION_AUTHORITY"
    ]
    complete_candidates.extend(explicit_full)
    return {
        "FULL_SESSION_AUTHORITY_ALREADY_EXISTS": bool(complete_candidates),
        "FULL_SESSION_AUTHORITY_CANDIDATES": complete_candidates,
        "STATUS_RECEIPTS_INVENTORY": candidates,
        "INSUFFICIENCY": (
            "INSUFFICIENT_FOR_FULL_SESSION_COMPLETENESS: local evidence contains "
            "identity/listing/delisting/coverage receipts and bounded tradestatus "
            "pilots, but no complete symbol+exchange_trading_date+trading_status "
            "authority with frozen 5456-symbol session coverage."
        ),
    }


def load_local_snapshot(data_root: Path) -> dict[str, Any]:
    """Load canonical metadata/calendar/keys for plan selection only."""

    calendar = (
        pl.scan_parquet(str(data_root / "curated" / "trading_calendar" / "**" / "*.parquet"))
        .filter(pl.col("is_trading") == True)  # noqa: E712
        .select("trade_date")
        .collect()
        .with_columns(pl.col("trade_date").cast(pl.Date))
        .filter((pl.col("trade_date") >= WINDOW_START) & (pl.col("trade_date") <= WINDOW_END))
        .unique()
        .sort("trade_date")
    )
    trading_dates = calendar["trade_date"].to_list()
    if not trading_dates:
        raise FeasibilityError("TRADING_CALENDAR_EMPTY")
    instruments = pl.read_parquet(data_root / "curated" / "instruments" / "part-merged.parquet").select(
        ["symbol", "name", "exchange", "asset_type", "list_date", "delist_date"]
    )
    daily = (
        pl.scan_parquet(str(data_root / "curated" / "daily_bars" / "**" / "*.parquet"))
        .select(["symbol", "trade_date"])
        .filter((pl.col("trade_date") >= WINDOW_START) & (pl.col("trade_date") <= WINDOW_END))
        .group_by("symbol")
        .agg(pl.col("trade_date").cast(pl.Date).sort().alias("dates"))
        .collect()
    )
    formal_symbols, identity_source = load_formal_identity_symbols(data_root)
    canonical_symbols = sorted(str(value) for value in daily["symbol"].to_list())
    if canonical_symbols != formal_symbols:
        raise FeasibilityError("CANONICAL_SYMBOL_SCOPE_DRIFT")
    info_map = {str(row["symbol"]): row for row in instruments.iter_rows(named=True)}
    if set(formal_symbols) - set(info_map):
        raise FeasibilityError("FORMAL_IDENTITY_MISSING_INSTRUMENT_METADATA")
    calendar_index = trading_dates
    symbols: dict[str, dict[str, Any]] = {}
    for row in daily.iter_rows(named=True):
        symbol = str(row["symbol"])
        metadata = info_map[symbol]
        observed = [parse_date(item) for item in row["dates"]]
        observed = [item for item in observed if item is not None]
        listed = parse_date(metadata.get("list_date"))
        delisted = parse_date(metadata.get("delist_date"))
        start_index = bisect_left(calendar_index, listed or WINDOW_START)
        end_index = bisect_right(calendar_index, delisted or WINDOW_END)
        expected_dates = calendar_index[start_index:end_index]
        max_gap_n = 0
        gap_before: date | None = None
        gap_after: date | None = None
        for before, after in zip(observed, observed[1:]):
            gap_n = bisect_left(calendar_index, after) - bisect_right(calendar_index, before)
            if gap_n > max_gap_n:
                max_gap_n = gap_n
                gap_before, gap_after = before, after
        symbols[symbol] = {
            "symbol": symbol,
            "name": metadata.get("name"),
            "exchange": metadata.get("exchange"),
            "asset_type": metadata.get("asset_type"),
            "list_date": listed,
            "delist_date": delisted,
            "observed_dates": observed,
            "observed_date_set": set(observed),
            "row_n": len(observed),
            "expected_lifetime_session_n": len(expected_dates),
            "missing_lifetime_session_n": max(0, len(expected_dates) - len(set(observed))),
            "max_gap_trading_n": max_gap_n,
            "gap_before": gap_before,
            "gap_after": gap_after,
        }
    return {
        "formal_symbols": formal_symbols,
        "identity_n": len(formal_symbols),
        "identity_hash": identity_hash(formal_symbols),
        "identity_source": identity_source,
        "calendar_dates": calendar_index,
        "calendar_n": len(calendar_index),
        "symbols": symbols,
    }


BOARD_ORDER = (
    "SH_600",
    "SH_601",
    "SH_603",
    "SH_688",
    "SZ_000",
    "SZ_001",
    "SZ_002",
    "SZ_300",
    "SZ_301",
)


def select_pilot_symbols(snapshot: dict[str, Any]) -> dict[str, list[str]]:
    """Select 30-50 symbols from local identity without classifying gaps."""

    symbols: dict[str, dict[str, Any]] = snapshot["symbols"]
    selected: dict[str, set[str]] = {}

    def add(symbol: str, category: str) -> None:
        if symbol not in symbols:
            return
        selected.setdefault(symbol, set()).add(category)

    def eligible(meta: dict[str, Any]) -> bool:
        return meta.get("exchange") in {"SH", "SZ"} and meta.get("asset_type") in {"stock", "cdr"}

    def pick(predicate: Callable[[dict[str, Any]], bool], n: int, *, reverse: bool = False) -> list[str]:
        pool = [row for row in symbols.values() if eligible(row) and predicate(row)]
        pool.sort(
            key=lambda row: (
                -int(row.get("max_gap_trading_n", 0)) if reverse else 0,
                -int(row.get("missing_lifetime_session_n", 0)) if reverse else 0,
                str(row["symbol"]),
            )
        )
        return [str(row["symbol"]) for row in pool[:n]]

    # Board anchors guarantee SH/SZ main, growth, and STAR coverage when the
    # formal identity contains the relevant board.
    for board in BOARD_ORDER:
        board_candidates = sorted(
            row["symbol"]
            for row in symbols.values()
            if eligible(row)
            and board_key(str(row["symbol"])) == board
            and row.get("list_date") is not None
            and row["list_date"] <= WINDOW_END
            and row.get("delist_date") is None
        )
        if board_candidates:
            add(str(board_candidates[0]), "BOARD_ANCHOR")

    for symbol in pick(
        lambda row: row.get("list_date") is not None
        and row["list_date"] <= date(2010, 1, 1)
        and row.get("delist_date") is None,
        8,
    ):
        add(symbol, "ORDINARY_LONG_ACTIVE")

    for exchange in ("SH", "SZ"):
        for symbol in pick(
            lambda row, exchange=exchange: row.get("exchange") == exchange
            and row.get("delist_date") is not None,
            3,
            reverse=True,
        ):
            add(symbol, "DELISTED_EDGE")

    for exchange in ("SH", "SZ"):
        new_rows = [
            row
            for row in symbols.values()
            if eligible(row)
            and row.get("exchange") == exchange
            and row.get("list_date") is not None
            and WINDOW_START <= row["list_date"] <= WINDOW_END
        ]
        new_rows.sort(key=lambda row: (row["list_date"], row["symbol"]))
        for row in new_rows[:2]:
            add(str(row["symbol"]), "NEW_LISTING_EDGE")
        recent = [row for row in new_rows if row["list_date"] >= date(2020, 1, 1)]
        recent.sort(key=lambda row: (row["list_date"], row["symbol"]))
        for row in recent[-2:]:
            add(str(row["symbol"]), "NEW_LISTING_EDGE")

    gap_rows = sorted(
        (row for row in symbols.values() if eligible(row) and row.get("max_gap_trading_n", 0) > 0),
        key=lambda row: (-int(row["max_gap_trading_n"]), -int(row["missing_lifetime_session_n"]), row["symbol"]),
    )
    for row in gap_rows[:12]:
        add(str(row["symbol"]), "GAP_HEAVY_LONG_SUSPENSION_CANDIDATE")

    # Explicitly bind the known two-date cross-check to the local identity.
    if "300546.SZ" not in symbols:
        raise FeasibilityError("KNOWN_300546_OUTSIDE_FORMAL_IDENTITY")
    add("300546.SZ", "KNOWN_300546_GAP")

    # Fill only from formal SH/SZ stock identity; never add an ETF or BJ code.
    for symbol in snapshot["formal_symbols"]:
        if len(selected) >= 36:
            break
        if eligible(symbols[symbol]):
            add(symbol, "ACTIVE_FILL")

    if not PILOT_MIN_SYMBOL_N <= len(selected) <= PILOT_MAX_SYMBOL_N:
        raise FeasibilityError(f"PILOT_SYMBOL_COUNT_OUT_OF_BOUNDS:{len(selected)}")
    return {symbol: sorted(categories) for symbol, categories in sorted(selected.items())}


def clamp_window(start: date, end: date) -> tuple[date, date]:
    start = max(start, WINDOW_START)
    end = min(end, WINDOW_END)
    if start > end:
        raise FeasibilityError(f"EMPTY_REQUEST_WINDOW:{start}:{end}")
    return start, end


def request_window(meta: dict[str, Any], categories: list[str]) -> tuple[date, date, str]:
    if "KNOWN_300546_GAP" in categories:
        start, end, purpose = date(2016, 9, 28), date(2016, 10, 11), "KNOWN_300546_GAP"
    elif "NEW_LISTING_EDGE" in categories and meta.get("list_date") is not None:
        listing = meta["list_date"]
        start, end, purpose = listing - timedelta(days=15), listing + timedelta(days=20), "LIFECYCLE_NEW_LISTING"
    elif "DELISTED_EDGE" in categories and meta.get("delist_date") is not None:
        delisted = meta["delist_date"]
        start, end, purpose = delisted - timedelta(days=15), delisted + timedelta(days=20), "LIFECYCLE_DELISTED"
    elif meta.get("max_gap_trading_n", 0) > 0 and meta.get("gap_before") and meta.get("gap_after"):
        # A bounded interior slice tests whether BaoStock emits status=0 rows
        # during a local long-gap candidate.  It does not call the gap a
        # suspension before the provider says so.
        start = meta["gap_before"] + timedelta(days=1)
        end = min(start + timedelta(days=30), meta["gap_after"] - timedelta(days=1))
        if start >= end:
            start, end = meta["gap_before"] - timedelta(days=5), meta["gap_after"] + timedelta(days=5)
        purpose = "LONG_SUSPENSION_INTERIOR"
    else:
        start, end, purpose = date(2020, 6, 1), date(2020, 6, 30), "ACTIVE_NORMAL"
    start, end = clamp_window(start, end)
    return start, end, purpose


def build_request_manifest(
    snapshot: dict[str, Any],
    selected: dict[str, list[str]],
    input_manifest: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    requests: list[dict[str, Any]] = []
    for symbol in sorted(selected):
        categories = sorted(selected[symbol])
        meta = snapshot["symbols"][symbol]
        start, end, purpose = request_window(meta, categories)
        calendar_n = sum(start <= item <= end for item in snapshot["calendar_dates"])
        requests.append(
            {
                "symbol": symbol,
                "bs_code": bs_code(symbol),
                "categories": categories,
                "purpose": purpose,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "exchange_trading_date_n": calendar_n,
                "list_date": meta.get("list_date"),
                "delist_date": meta.get("delist_date"),
                "canonical_observed_row_n": meta.get("row_n"),
                "canonical_gap_context": {
                    "missing_lifetime_session_n": meta.get("missing_lifetime_session_n"),
                    "max_gap_trading_n": meta.get("max_gap_trading_n"),
                    "gap_before": meta.get("gap_before"),
                    "gap_after": meta.get("gap_after"),
                },
                "frequency": QUERY_FREQUENCY,
                "adjustflag": QUERY_ADJUSTFLAG,
            }
        )
    # The adjustflag comparison is deliberately one additional bounded
    # request, not a second pilot symbol.
    comparison = next(row for row in requests if row["symbol"] == "300546.SZ")
    alternate = dict(comparison)
    alternate["purpose"] = "ADJUSTFLAG_COMPARISON"
    alternate["adjustflag"] = ALTERNATE_ADJUSTFLAG
    requests.append(alternate)
    requests.sort(key=lambda row: (row["symbol"], int(row["adjustflag"]), row["purpose"]))
    for index, row in enumerate(requests, 1):
        row["request_order"] = index
    manifest = {
        "TASK": REPORT_STEM,
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
        "FORMAL_IDENTITY_N": snapshot["identity_n"],
        "FORMAL_IDENTITY_HASH": snapshot["identity_hash"],
        "window": {"start": WINDOW_START, "end": WINDOW_END},
        "authority_contract": {
            "provider": PROVIDER_DISTRIBUTION,
            "distribution_version": PROVIDER_VERSION,
            "query_runtime": PROVIDER_RUNTIME,
            "fields": QUERY_FIELDS,
            "frequency": QUERY_FREQUENCY,
            "primary_adjustflag": QUERY_ADJUSTFLAG,
            "alternate_adjustflag_comparison": ALTERNATE_ADJUSTFLAG,
        },
        "PILOT_SYMBOL_N": len(selected),
        "PILOT_SYMBOLS": sorted(selected),
        "requests": requests,
    }
    return manifest, sha256_json(manifest)


def _result_attr(result: Any, name: str, default: str = "UNKNOWN") -> str:
    return str(getattr(result, name, default))


def _read_provider_rows(result: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    while result.next():
        rows.append(list(result.get_row_data()))
    return rows


def fetch_pilot(
    requests: list[dict[str, Any]],
    *,
    provider: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_retries: int = MAX_RETRIES,
    min_interval_seconds: float = MIN_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Perform only the frozen bounded request list and retain raw rows."""

    if provider is None:
        import baostock as provider  # type: ignore[no-redef]
    runtime = {
        "distribution": PROVIDER_DISTRIBUTION,
        "distribution_version": PROVIDER_VERSION,
        "module_version": str(getattr(provider, "__version__", "UNKNOWN")),
        "query_runtime": PROVIDER_RUNTIME,
    }
    login = provider.login()
    login_code = _result_attr(login, "error_code")
    login_message = _result_attr(login, "error_msg", "")
    receipt: dict[str, Any] = {
        "TASK": REPORT_STEM,
        "BASE_HEAD": BASE_HEAD,
        "REQUESTS": [],
        "provider": runtime,
        "login": {"error_code": login_code, "error_msg": login_message},
        "logout": None,
    }
    if login_code != "0":
        receipt["SESSION_FAILED"] = True
        receipt["logout"] = {"error_code": "NOT_RUN", "error_msg": "login failed"}
        return receipt
    last_request_at: float | None = None
    try:
        for request in requests:
            if last_request_at is not None and min_interval_seconds > 0:
                elapsed = time.monotonic() - last_request_at
                if elapsed < min_interval_seconds:
                    sleep_fn(min_interval_seconds - elapsed)
            attempts: list[dict[str, Any]] = []
            final_rows: list[list[str]] = []
            final_code = "UNKNOWN"
            final_message = ""
            for attempt in range(1, max_retries + 2):
                last_request_at = time.monotonic()
                try:
                    result = provider.query_history_k_data_plus(
                        request["bs_code"],
                        QUERY_FIELDS,
                        start_date=request["start_date"],
                        end_date=request["end_date"],
                        frequency=QUERY_FREQUENCY,
                        adjustflag=str(request["adjustflag"]),
                    )
                    final_code = _result_attr(result, "error_code")
                    final_message = _result_attr(result, "error_msg", "")
                    rows = _read_provider_rows(result) if final_code == "0" else []
                    attempts.append(
                        {
                            "attempt": attempt,
                            "error_code": final_code,
                            "error_msg": final_message,
                            "row_n": len(rows),
                        }
                    )
                    if final_code == "0":
                        final_rows = rows
                        break
                except Exception as exc:  # provider failure is retained, never hidden
                    final_code = "EXCEPTION"
                    final_message = f"{type(exc).__name__}: {exc}"
                    attempts.append(
                        {
                            "attempt": attempt,
                            "error_code": final_code,
                            "error_msg": final_message,
                            "row_n": 0,
                        }
                    )
                if attempt <= max_retries:
                    sleep_fn(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
            receipt["REQUESTS"].append(
                {
                    "request": request,
                    "attempts": attempts,
                    "final_error_code": final_code,
                    "final_error_msg": final_message,
                    "rows": final_rows,
                }
            )
    finally:
        logout = provider.logout()
        receipt["logout"] = {
            "error_code": _result_attr(logout, "error_code", "0"),
            "error_msg": _result_attr(logout, "error_msg", ""),
        }
    return receipt


def _normalize_row(raw: list[Any], request: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if len(raw) != 10:
        return None, "INVALID_FIELD_COUNT"
    trade_date = parse_date(raw[0])
    expected_code = request["bs_code"]
    if trade_date is None or str(raw[1]) != expected_code:
        return None, "IDENTITY_OR_DATE_INVALID"
    start = date.fromisoformat(request["start_date"])
    end = date.fromisoformat(request["end_date"])
    if not start <= trade_date <= end:
        return None, "DATE_OUTSIDE_REQUEST"
    status = parse_int(raw[9])
    if status not in {0, 1}:
        return None, "TRADESTATUS_UNKNOWN"
    numeric_names = ("open", "high", "low", "close", "volume", "amount", "preclose")
    numeric: dict[str, float | None] = {}
    numeric_invalid = False
    for index, name in enumerate(numeric_names, 2):
        text = "" if raw[index] is None else str(raw[index]).strip()
        if not text:
            numeric[name] = None
        else:
            numeric[name] = parse_float(text)
            if numeric[name] is None:
                numeric_invalid = True
    if numeric_invalid:
        return None, "INVALID_NUMERIC_FIELD"
    return {
        "symbol": request["symbol"],
        "trade_date": trade_date,
        "provider_code": str(raw[1]),
        "tradestatus": status,
        **numeric,
    }, None


def normalize_receipt(raw_receipt: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Normalize raw rows and classify every exchange date in each base window."""

    normalized_requests: list[dict[str, Any]] = []
    all_cases: list[dict[str, Any]] = []
    for item in raw_receipt.get("REQUESTS", []):
        request = item["request"]
        rows = item.get("rows", []) if item.get("final_error_code") == "0" else []
        normalized_rows: list[dict[str, Any]] = []
        invalid_reasons: list[str] = []
        seen: set[tuple[str, date]] = set()
        duplicate_key_n = 0
        for raw in rows:
            normalized, reason = _normalize_row(raw, request)
            if normalized is None:
                invalid_reasons.append(str(reason))
                continue
            key = (normalized["symbol"], normalized["trade_date"])
            if key in seen:
                duplicate_key_n += 1
            seen.add(key)
            normalized_rows.append(normalized)
        by_key: dict[tuple[str, date], dict[str, Any]] = {}
        for row in normalized_rows:
            by_key.setdefault((row["symbol"], row["trade_date"]), row)
        meta = snapshot["symbols"].get(request["symbol"])
        if meta is None:
            raise FeasibilityError(f"PILOT_SYMBOL_NOT_IN_SNAPSHOT:{request['symbol']}")
        listed = meta.get("list_date")
        delisted = meta.get("delist_date")
        start = date.fromisoformat(request["start_date"])
        end = date.fromisoformat(request["end_date"])
        cases: list[dict[str, Any]] = []
        if request["adjustflag"] == QUERY_ADJUSTFLAG:
            for trading_date in snapshot["calendar_dates"]:
                if not start <= trading_date <= end:
                    continue
                key = (request["symbol"], trading_date)
                provider_row = by_key.get(key)
                if listed is not None and trading_date < listed or delisted is not None and trading_date > delisted:
                    classification, basis = "NOT_EXPECTED_BAR", "LIFECYCLE_OUTSIDE_IDENTITY"
                elif provider_row is None:
                    classification, basis = "UNKNOWN", "PROVIDER_ROW_ABSENT_IN_LIFETIME"
                elif provider_row["tradestatus"] == 1:
                    classification, basis = "EXPECTED_BAR", "PROVIDER_TRADESTATUS_1"
                elif provider_row["tradestatus"] == 0:
                    classification, basis = "NOT_EXPECTED_BAR", "PROVIDER_TRADESTATUS_0"
                else:  # defensive; _normalize_row already gates this
                    classification, basis = "UNKNOWN", "TRADESTATUS_UNKNOWN"
                cases.append(
                    {
                        "symbol": request["symbol"],
                        "trade_date": trading_date,
                        "classification": classification,
                        "basis": basis,
                        "provider_row_present": provider_row is not None,
                        "provider_tradestatus": None if provider_row is None else provider_row["tradestatus"],
                        "canonical_row_present": trading_date in meta["observed_date_set"],
                    }
                )
            all_cases.extend(cases)
        normalized_requests.append(
            {
                "request": request,
                "final_error_code": item.get("final_error_code"),
                "final_error_msg": item.get("final_error_msg", ""),
                "provider_row_n": len(rows),
                "normalized_row_n": len(normalized_rows),
                "duplicate_provider_key_n": duplicate_key_n,
                "invalid_row_n": len(invalid_reasons),
                "invalid_reasons": sorted(invalid_reasons),
                "normalized_rows": normalized_rows,
                "cases": cases,
                "provider_failed": item.get("final_error_code") != "0",
            }
        )
    return {
        "TASK": REPORT_STEM,
        "BASE_HEAD": BASE_HEAD,
        "provider": raw_receipt.get("provider"),
        "login": raw_receipt.get("login"),
        "logout": raw_receipt.get("logout"),
        "REQUESTS": normalized_requests,
        "CASES": all_cases,
    }


def aggregate_semantics(normalized: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    base_requests = [
        row
        for row in normalized["REQUESTS"]
        if row["request"]["adjustflag"] == QUERY_ADJUSTFLAG
    ]
    cases = normalized["CASES"]
    counts = {name: 0 for name in ("EXPECTED_BAR", "NOT_EXPECTED_BAR", "UNKNOWN")}
    basis_counts: dict[str, int] = {}
    for case in cases:
        counts[case["classification"]] += 1
        basis_counts[case["basis"]] = basis_counts.get(case["basis"], 0) + 1
    status1_cases = [case for case in cases if case["provider_tradestatus"] == 1]
    status0_cases = [case for case in cases if case["provider_tradestatus"] == 0]
    status1_missing = [case for case in status1_cases if not case["canonical_row_present"]]
    failed_n = sum(1 for row in base_requests if row["provider_failed"])
    duplicate_n = sum(int(row["duplicate_provider_key_n"]) for row in base_requests)
    invalid_n = sum(int(row["invalid_row_n"]) for row in base_requests)
    unknown_status_n = sum(row["invalid_reasons"].count("TRADESTATUS_UNKNOWN") for row in base_requests)
    absent_lifetime_n = basis_counts.get("PROVIDER_ROW_ABSENT_IN_LIFETIME", 0)
    suspension_requests = [
        row
        for row in base_requests
        if row["request"]["purpose"] == "LONG_SUSPENSION_INTERIOR"
        and int(row["request"]["canonical_gap_context"].get("max_gap_trading_n") or 0) >= 10
    ]
    suspension_cases = [case for row in suspension_requests for case in row["cases"]]
    suspension_status0_n = sum(case["provider_tradestatus"] == 0 for case in suspension_cases)
    suspension_status1_n = sum(case["provider_tradestatus"] == 1 for case in suspension_cases)
    suspension_unknown_n = sum(case["classification"] == "UNKNOWN" for case in suspension_cases)
    suspension_absent_n = sum(case["basis"] == "PROVIDER_ROW_ABSENT_IN_LIFETIME" for case in suspension_cases)
    lifecycle_requests = [
        row
        for row in base_requests
        if row["request"]["purpose"] in {"LIFECYCLE_NEW_LISTING", "LIFECYCLE_DELISTED"}
    ]
    lifecycle_outside_n = sum(
        case["basis"] == "LIFECYCLE_OUTSIDE_IDENTITY"
        for row in lifecycle_requests
        for case in row["cases"]
    )
    return {
        "PILOT_REQUEST_N": len(normalized["REQUESTS"]),
        "BASE_REQUEST_N": len(base_requests),
        "EXPECTED_BAR_CASE_N": counts["EXPECTED_BAR"],
        "NOT_EXPECTED_BAR_CASE_N": counts["NOT_EXPECTED_BAR"],
        "UNKNOWN_CASE_N": counts["UNKNOWN"],
        "TRADESTATUS_1_CASE_N": len(status1_cases),
        "TRADESTATUS_0_CASE_N": len(status0_cases),
        "STATUS1_CANONICAL_PRESENT_N": len(status1_cases) - len(status1_missing),
        "STATUS1_CANONICAL_MISSING_N": len(status1_missing),
        "ROW_ABSENT_IN_LIFETIME_N": absent_lifetime_n,
        "LIFECYCLE_OUTSIDE_CASE_N": lifecycle_outside_n,
        "TRADESTATUS_UNKNOWN_N": unknown_status_n,
        "DUPLICATE_PROVIDER_KEY_N": duplicate_n,
        "INVALID_PROVIDER_ROW_N": invalid_n,
        "PROVIDER_FAILED_REQUEST_N": failed_n,
        "SUSPENSION_REQUEST_N": len(suspension_requests),
        "SUSPENSION_STATUS0_CASE_N": suspension_status0_n,
        "SUSPENSION_STATUS1_CASE_N": suspension_status1_n,
        "SUSPENSION_UNKNOWN_CASE_N": suspension_unknown_n,
        "SUSPENSION_ROW_ABSENT_IN_LIFETIME_N": suspension_absent_n,
        "BASIS_COUNTS": basis_counts,
    }


def adjustflag_comparison(normalized: dict[str, Any]) -> dict[str, Any]:
    comparison = next(
        row for row in normalized["REQUESTS"] if row["request"]["purpose"] == "ADJUSTFLAG_COMPARISON"
    )
    primary = next(
        row
        for row in normalized["REQUESTS"]
        if row["request"]["symbol"] == comparison["request"]["symbol"]
        and row["request"]["adjustflag"] == QUERY_ADJUSTFLAG
    )
    primary_by_date = {row["trade_date"]: row for row in primary["normalized_rows"]}
    alternate_by_date = {row["trade_date"]: row for row in comparison["normalized_rows"]}
    dates_equal = set(primary_by_date) == set(alternate_by_date)
    status_equal = dates_equal and all(
        primary_by_date[d]["tradestatus"] == alternate_by_date[d]["tradestatus"]
        for d in primary_by_date
    )
    ohlc_changed_n = sum(
        any(primary_by_date[d].get(field) != alternate_by_date[d].get(field) for field in ("open", "high", "low", "close"))
        for d in set(primary_by_date) & set(alternate_by_date)
    )
    return {
        "comparison_symbol": comparison["request"]["symbol"],
        "primary_adjustflag": QUERY_ADJUSTFLAG,
        "alternate_adjustflag": ALTERNATE_ADJUSTFLAG,
        "primary_row_n": len(primary["normalized_rows"]),
        "alternate_row_n": len(comparison["normalized_rows"]),
        "date_set_equal": dates_equal,
        "tradestatus_set_equal": status_equal,
        "ohlc_changed_n": ohlc_changed_n,
        "adjustflag_changes_session_coverage": not status_equal,
    }


def known_gap_crosscheck(normalized: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    for request_result in normalized["REQUESTS"]:
        request = request_result["request"]
        if request["adjustflag"] != QUERY_ADJUSTFLAG:
            continue
        if request["purpose"] not in {"KNOWN_300546_GAP", "LONG_SUSPENSION_INTERIOR"}:
            continue
        meta = snapshot["symbols"][request["symbol"]]
        missing_cases = [
            case
            for case in request_result["cases"]
            if not case["canonical_row_present"]
            and case["basis"] != "LIFECYCLE_OUTSIDE_IDENTITY"
        ]
        for case in missing_cases[:3]:
            if case["classification"] == "EXPECTED_BAR":
                diagnosis = "TRUE_MISSING_BAR"
            elif case["classification"] == "NOT_EXPECTED_BAR":
                diagnosis = "LEGIT_NOT_EXPECTED"
            else:
                diagnosis = "UNKNOWN"
            observations.append({**case, "diagnosis": diagnosis, "selection": request["purpose"]})
    # The two previously repaired dates are retained as an explicit semantic
    # check even though they are now present in canonical.
    three_zero = next(
        (
            row
            for row in normalized["REQUESTS"]
            if row["request"]["purpose"] == "KNOWN_300546_GAP"
            and row["request"]["symbol"] == "300546.SZ"
        ),
        None,
    )
    known_repaired: list[dict[str, Any]] = []
    if three_zero is not None:
        for target in (date(2016, 9, 29), date(2016, 10, 10)):
            match = next(
                (case for case in three_zero["cases"] if parse_date(case["trade_date"]) == target),
                None,
            )
            if match is not None:
                known_repaired.append(
                    {
                        **match,
                        "prior_known_gap": True,
                        "diagnosis": "EXPECTED_BAR" if match["classification"] == "EXPECTED_BAR" else match["classification"],
                    }
                )
    counts = {name: sum(row["diagnosis"] == name for row in observations) for name in (
        "TRUE_MISSING_BAR", "LEGIT_NOT_EXPECTED", "UNKNOWN"
    )}
    return {
        "GAP_DISCOVERY_USED_FOR_SELECTION_ONLY": True,
        "GAP_DISCOVERY_IS_EXPECTED_KEY_AUTHORITY": False,
        "SAMPLE_GAP_CASE_N": len(observations),
        "SAMPLE_GAP_DIAGNOSIS_COUNTS": counts,
        "SAMPLE_GAP_OBSERVATIONS": observations,
        "KNOWN_300546_REPAIRED_DATES": known_repaired,
    }


def cost_model(snapshot: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    lifecycle_rows = sum(int(row["expected_lifetime_session_n"]) for row in snapshot["symbols"].values())
    one_query_n = len(snapshot["formal_symbols"])
    year_window_n = 0
    for meta in snapshot["symbols"].values():
        start = max(meta.get("list_date") or WINDOW_START, WINDOW_START)
        end = min(meta.get("delist_date") or WINDOW_END, WINDOW_END)
        if start <= end:
            year_window_n += end.year - start.year + 1
    raw_row_sizes: list[int] = []
    normalized_row_sizes: list[int] = []
    for item in normalized["REQUESTS"]:
        for row in item["normalized_rows"]:
            raw_row_sizes.append(len(canonical_json_bytes(row)))
            normalized_row_sizes.append(len(canonical_json_bytes(row)))
    avg_row_bytes = (sum(normalized_row_sizes) / len(normalized_row_sizes)) if normalized_row_sizes else 0.0
    def bytes_estimate() -> int:
        return int(round(lifecycle_rows * avg_row_bytes)) if avg_row_bytes else 0
    return {
        "SYMBOL_N": len(snapshot["formal_symbols"]),
        "EXPECTED_SESSION_ROW_MODEL": "lifecycle-bounded exchange trading dates; not observed full extraction",
        "EXPECTED_RAW_ROW_N_MODEL": lifecycle_rows,
        "DISK_SIZE_ESTIMATE_BASIS": "pilot normalized-row canonical JSON bytes; not a Parquet or provider wire-size guarantee",
        "one_historical_query_per_symbol": {
            "estimated_request_n": one_query_n,
            "expected_raw_row_n": lifecycle_rows,
            "disk_size_estimate_bytes": bytes_estimate(),
            "runtime_class": "MEDIUM_IF_PROVIDER_FULL_RANGE_RESPONSE_IS_UNCAPPED",
            "checkpoint_resume": True,
            "deterministic_request_manifest": True,
        },
        "windowed_by_calendar_year": {
            "estimated_request_n": year_window_n,
            "expected_raw_row_n": lifecycle_rows,
            "disk_size_estimate_bytes": bytes_estimate(),
            "runtime_class": "HIGH_BUT_CONSERVATIVE_RESPONSE_BOUNDARY",
            "checkpoint_resume": True,
            "deterministic_request_manifest": True,
        },
        "pilot_normalized_row_avg_bytes": avg_row_bytes,
        "FULL_EXTRACTION_ESTIMATED_REQUEST_N": year_window_n,
        "FULL_EXTRACTION_REQUEST_SCENARIOS": {
            "one_query_per_symbol": one_query_n,
            "calendar_year_windowed": year_window_n,
        },
        "FULL_EXTRACTION_NOT_EXECUTED": True,
    }


def decision(aggregate: dict[str, Any], adjust: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    suspension_tested = aggregate["SUSPENSION_REQUEST_N"] > 0
    suspension_closed = suspension_tested and aggregate["SUSPENSION_ROW_ABSENT_IN_LIFETIME_N"] == 0
    lifecycle_closed = aggregate["LIFECYCLE_OUTSIDE_CASE_N"] > 0
    provider_clean = (
        aggregate["PROVIDER_FAILED_REQUEST_N"] == 0
        and aggregate["DUPLICATE_PROVIDER_KEY_N"] == 0
        and aggregate["INVALID_PROVIDER_ROW_N"] == 0
        and aggregate["TRADESTATUS_UNKNOWN_N"] == 0
    )
    adjust_closed = adjust["tradestatus_set_equal"]
    reasons = {
        "provider_status_domain": "PASS" if provider_clean else "BLOCKED",
        "suspension_edge": "PASS" if suspension_closed else "BLOCKED",
        "lifecycle_edge": "PASS" if lifecycle_closed else "BLOCKED",
        "unknown_row_absence": "EXPLICIT_UNKNOWN_IN_LIFETIME",
        "adjustflag_session_coverage": "PASS" if adjust_closed else "BLOCKED",
    }
    feasible = provider_clean and suspension_closed and lifecycle_closed and adjust_closed
    if feasible:
        return "FEASIBLE", "R3_FULL_SESSION_COMPLETENESS_AUTHORITY_EXTRACTION_V01", reasons
    return "BLOCKED", "DO_NOT_START_FULL_EXTRACTION", reasons


def build_report(
    *,
    input_manifest: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    selected: dict[str, list[str]],
    request_manifest_hash: str,
    raw_receipt: dict[str, Any],
    normalized: dict[str, Any],
    raw_hash: str,
    normalized_hash: str,
    test_result: str = "PENDING_TARGETED_TESTS",
    py_compile_result: str = "PENDING",
    diff_check_result: str = "PENDING",
) -> dict[str, Any]:
    aggregate = aggregate_semantics(normalized, snapshot)
    adjust = adjustflag_comparison(normalized)
    gap = known_gap_crosscheck(normalized, snapshot)
    costs = cost_model(snapshot, normalized)
    feasibility, recommendation, reasons = decision(aggregate, adjust)
    safety = {
        "CANONICAL_WRITE_EXECUTED": False,
        "CANONICAL_BYTES_MUTATED": False,
        "R4A9_CHECKPOINT_MUTATED": False,
        "R4A9_RESUME_AUTHORIZED": False,
        "PRECLOSE_COMPLETE": False,
        "PRODUCTION": False,
        "FORWARD": False,
        "TRADEPLAN": False,
        "FULL_MARKET_EXTRACTION_EXECUTED": False,
    }
    report = {
        "REPORT": REPORT_STEM,
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "INPUT_FILE_N": input_manifest["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": input_manifest["INPUT_MANIFEST_HASH"],
        "FORMAL_IDENTITY_N": snapshot["identity_n"],
        "FORMAL_IDENTITY_HASH": snapshot["identity_hash"],
        "FORMAL_IDENTITY_SOURCE": snapshot["identity_source"],
        "FULL_SESSION_AUTHORITY_ALREADY_EXISTS": inventory["FULL_SESSION_AUTHORITY_ALREADY_EXISTS"],
        "LOCAL_AUTHORITY_INVENTORY": inventory,
        "INSUFFICIENT_FOR_FULL_SESSION_COMPLETENESS": not inventory["FULL_SESSION_AUTHORITY_ALREADY_EXISTS"],
        "PILOT_SYMBOL_N": len(selected),
        "PILOT_SYMBOLS": sorted(selected),
        "PILOT_SYMBOL_CATEGORIES": selected,
        "PILOT_REQUEST_N": aggregate["PILOT_REQUEST_N"],
        "REQUEST_MANIFEST_HASH": request_manifest_hash,
        "RAW_RECEIPT_HASH": raw_hash,
        "NORMALIZED_RECEIPT_HASH": normalized_hash,
        **aggregate,
        "SUSPENSION_SEMANTICS": {
            "observed": (
                "BaoStock returned explicit tradestatus=0 rows for all in-lifetime "
                "exchange trading dates in the bounded long-gap slices."
                if aggregate["SUSPENSION_REQUEST_N"] and aggregate["SUSPENSION_ROW_ABSENT_IN_LIFETIME_N"] == 0
                else "Pilot did not prove complete in-lifetime suspension rows; absent in-lifetime dates remain UNKNOWN."
            ),
            "status0_n": aggregate["SUSPENSION_STATUS0_CASE_N"],
            "absent_in_lifetime_n": aggregate["SUSPENSION_ROW_ABSENT_IN_LIFETIME_N"],
            "contract": "tradestatus=0 is NOT_EXPECTED_BAR; absent in lifetime is UNKNOWN.",
        },
        "LIFECYCLE_SEMANTICS": {
            "observed_lifecycle_outside_case_n": aggregate["LIFECYCLE_OUTSIDE_CASE_N"],
            "contract": "pre-listing/post-delisting is NOT_EXPECTED_BAR only from independent identity lifetime; provider absence alone is not evidence.",
        },
        "ROW_ABSENCE_SEMANTICS": {
            "in_lifetime": "UNKNOWN",
            "outside_lifetime": "NOT_EXPECTED_BAR only via independent lifecycle authority",
            "unknown_never_mapped_to_not_expected": True,
        },
        "SEMANTIC_QUESTIONS": {
            "A_SUSPENSION_ROW_RETURNED_ON_EXCHANGE_TRADING_DATE": (
                aggregate["SUSPENSION_STATUS0_CASE_N"] > 0
                and aggregate["SUSPENSION_ROW_ABSENT_IN_LIFETIME_N"] == 0
            ),
            "B_SUSPENSION_ROW_TRADESTATUS_STABLE_ZERO": (
                aggregate["SUSPENSION_STATUS0_CASE_N"] > 0
                and aggregate["SUSPENSION_STATUS1_CASE_N"] == 0
                and aggregate["SUSPENSION_UNKNOWN_CASE_N"] == 0
            ),
            "C_TRADESTATUS_1_MEANS_EXPECTED_BAR": {
                "contract": "EXPECTED_BAR",
                "pilot_status1_n": aggregate["TRADESTATUS_1_CASE_N"],
                "canonical_present_n": aggregate["STATUS1_CANONICAL_PRESENT_N"],
                "canonical_missing_n": aggregate["STATUS1_CANONICAL_MISSING_N"],
            },
            "D_PROVIDER_ROW_ABSENT_MEANS": "UNKNOWN_IN_LIFETIME",
            "E_LIFECYCLE_EDGE_REQUIRES": "INDEPENDENT_LIST_DATE_AND_DELIST_DATE_IDENTITY_CONTRACT",
            "F_LONG_SUSPENSION_CONSISTENCY": {
                "request_n": aggregate["SUSPENSION_REQUEST_N"],
                "status0_n": aggregate["SUSPENSION_STATUS0_CASE_N"],
                "status1_n": aggregate["SUSPENSION_STATUS1_CASE_N"],
                "unknown_n": aggregate["SUSPENSION_UNKNOWN_CASE_N"],
            },
            "G_ADJUSTFLAG_EFFECT": adjust,
        },
        "ADJUSTFLAG_SEMANTICS": adjust,
        "KNOWN_GAP_CROSSCHECK": gap,
        "SEMANTIC_GATE_REASONS": reasons,
        "COST_MODEL": costs,
        "FEASIBILITY": feasibility,
        "NEXT_RECOMMENDATION": recommendation,
        "SAFETY": safety,
        "TEST_RESULT": test_result,
        "PY_COMPILE": py_compile_result,
        "GIT_DIFF_CHECK": diff_check_result,
        "NETWORK_PROVIDER_DATA_FETCH": "BAOSTOCK_BOUNDED_PILOT_ONLY",
        "BAOSTOCK_EXECUTED": True,
        "TDX_EXECUTED": False,
        "R4A9_RESUME_EXECUTED": False,
    }
    return report


def report_markdown(report: dict[str, Any]) -> str:
    safety = report["SAFETY"]
    gap = report["KNOWN_GAP_CROSSCHECK"]
    costs = report["COST_MODEL"]
    lines = [
        f"# {REPORT_STEM}",
        "",
        "Bounded provider-feasibility research only. No canonical write, repair, TDX execution, full extraction, or R4A9 resume was performed.",
        "",
        f"- BASE_HEAD: `{report['BASE_HEAD']}`",
        f"- INPUT_FILE_N / INPUT_MANIFEST_HASH: `{report['INPUT_FILE_N']}` / `{report['INPUT_MANIFEST_HASH']}`",
        f"- FULL_SESSION_AUTHORITY_ALREADY_EXISTS: `{report['FULL_SESSION_AUTHORITY_ALREADY_EXISTS']}`",
        f"- PILOT_SYMBOL_N / PILOT_REQUEST_N: `{report['PILOT_SYMBOL_N']}` / `{report['PILOT_REQUEST_N']}`",
        f"- REQUEST_MANIFEST_HASH: `{report['REQUEST_MANIFEST_HASH']}`",
        f"- RAW_RECEIPT_HASH: `{report['RAW_RECEIPT_HASH']}`",
        f"- NORMALIZED_RECEIPT_HASH: `{report['NORMALIZED_RECEIPT_HASH']}`",
        "",
        "## Contract",
        "",
        "For an exchange trading date, `tradestatus=1` is `EXPECTED_BAR`, `tradestatus=0` is `NOT_EXPECTED_BAR`, and an absent provider row inside the independent list/delist lifetime is `UNKNOWN`. Pre-listing and post-delisting are `NOT_EXPECTED_BAR` only because of the independent lifecycle contract; absence alone is never downgraded.",
        "",
        f"- EXPECTED_BAR_CASE_N: `{report['EXPECTED_BAR_CASE_N']}`",
        f"- NOT_EXPECTED_BAR_CASE_N: `{report['NOT_EXPECTED_BAR_CASE_N']}`",
        f"- UNKNOWN_CASE_N: `{report['UNKNOWN_CASE_N']}`",
        f"- TRADESTATUS_UNKNOWN_N: `{report['TRADESTATUS_UNKNOWN_N']}`",
        f"- PROVIDER_FAILED_REQUEST_N: `{report['PROVIDER_FAILED_REQUEST_N']}`",
        "",
        "## Empirical semantics",
        "",
        f"- A exchange-open/suspension row returned: `{report['SEMANTIC_QUESTIONS']['A_SUSPENSION_ROW_RETURNED_ON_EXCHANGE_TRADING_DATE']}`",
        f"- B suspension `tradestatus=0` stable in tested long-gap slices: `{report['SEMANTIC_QUESTIONS']['B_SUSPENSION_ROW_TRADESTATUS_STABLE_ZERO']}`",
        f"- C `tradestatus=1` contract: `{report['SEMANTIC_QUESTIONS']['C_TRADESTATUS_1_MEANS_EXPECTED_BAR']['contract']}`; canonical present/missing: `{report['STATUS1_CANONICAL_PRESENT_N']}`/`{report['STATUS1_CANONICAL_MISSING_N']}`",
        f"- D provider row absence inside lifetime: `{report['SEMANTIC_QUESTIONS']['D_PROVIDER_ROW_ABSENT_MEANS']}`",
        f"- E lifecycle/IPO edge contract: `{report['SEMANTIC_QUESTIONS']['E_LIFECYCLE_EDGE_REQUIRES']}`",
        f"- F long-suspension status0/status1/unknown: `{report['SUSPENSION_STATUS0_CASE_N']}`/`{report['SUSPENSION_STATUS1_CASE_N']}`/`{report['SUSPENSION_UNKNOWN_CASE_N']}`",
        f"- G adjustflag changes session coverage: `{report['ADJUSTFLAG_SEMANTICS']['adjustflag_changes_session_coverage']}`",
        f"- SUSPENSION_SEMANTICS: {report['SUSPENSION_SEMANTICS']['observed']}",
        f"- LIFECYCLE_OUTSIDE_CASE_N: `{report['LIFECYCLE_SEMANTICS']['observed_lifecycle_outside_case_n']}`",
        f"- ADJUSTFLAG_DATE_STATUS_EQUAL: `{report['ADJUSTFLAG_SEMANTICS']['tradestatus_set_equal']}`; OHLC changed rows: `{report['ADJUSTFLAG_SEMANTICS']['ohlc_changed_n']}`",
        "",
        "## Known-gap cross-check",
        "",
        f"- Gap discovery was selection-only and is not expected-key authority; sampled cases: `{gap['SAMPLE_GAP_CASE_N']}`.",
        f"- 300546 repaired dates classified: `{[(row['trade_date'], row['diagnosis']) for row in gap['KNOWN_300546_REPAIRED_DATES']]}`",
        "",
        "## Cost model",
        "",
        f"- one query per symbol: `{costs['one_historical_query_per_symbol']['estimated_request_n']}` requests",
        f"- calendar-year windowed: `{costs['windowed_by_calendar_year']['estimated_request_n']}` requests",
        f"- modeled raw rows: `{costs['EXPECTED_RAW_ROW_N_MODEL']}`",
        "- No full extraction was executed; request estimates are scenarios, not an execution cap.",
        "",
        "## Decision",
        "",
        f"- FEASIBILITY: `{report['FEASIBILITY']}`",
        f"- NEXT_RECOMMENDATION: `{report['NEXT_RECOMMENDATION']}`",
        "",
        "## Safety",
        "",
    ]
    for key, value in safety.items():
        lines.append(f"- {key}: `{str(value).lower() if isinstance(value, bool) else value}`")
    lines.extend(
        [
            "",
            "## Verification",
            "",
            f"- TEST_RESULT: `{report['TEST_RESULT']}`",
            f"- PY_COMPILE: `{report['PY_COMPILE']}`",
            f"- GIT_DIFF_CHECK: `{report['GIT_DIFF_CHECK']}`",
            "",
        ]
    )
    return "\n".join(lines)


def run_fetch(data_root: Path, *, test_result: str = "PENDING_TARGETED_TESTS") -> dict[str, Any]:
    input_manifest = build_input_file_manifest(data_root)
    if (
        input_manifest["INPUT_FILE_N"] != INPUT_FILE_N
        or input_manifest["INPUT_MANIFEST_HASH"] != INPUT_MANIFEST_HASH
    ):
        raise FeasibilityError("INPUT_MANIFEST_DRIFT")
    snapshot = load_local_snapshot(data_root)
    inventory = inventory_local_authority(data_root)
    selected = select_pilot_symbols(snapshot)
    request_manifest, request_hash = build_request_manifest(snapshot, selected, input_manifest)
    raw = fetch_pilot(request_manifest["requests"])
    normalized = normalize_receipt(raw, snapshot)
    raw_hash = sha256_json(raw)
    normalized_hash = sha256_json(normalized)
    write_json(REQUEST_MANIFEST_PATH, {**request_manifest, "REQUEST_MANIFEST_HASH": request_hash})
    write_json(RAW_RECEIPT_PATH, {**raw, "REQUEST_MANIFEST_HASH": request_hash, "RAW_RECEIPT_HASH": raw_hash})
    write_json(
        NORMALIZED_RECEIPT_PATH,
        {**normalized, "REQUEST_MANIFEST_HASH": request_hash, "NORMALIZED_RECEIPT_HASH": normalized_hash},
    )
    report = build_report(
        input_manifest=input_manifest,
        inventory=inventory,
        snapshot=snapshot,
        selected=selected,
        request_manifest_hash=request_hash,
        raw_receipt=raw,
        normalized=normalized,
        raw_hash=raw_hash,
        normalized_hash=normalized_hash,
        test_result=test_result,
    )
    write_json(REPORT_JSON_PATH, report)
    write_text(REPORT_MD_PATH, report_markdown(report))
    return report


def run_offline_report(
    data_root: Path,
    *,
    test_result: str = "TARGETED_TESTS_PASS",
    py_compile_result: str = "PASS",
    diff_check_result: str = "PASS",
) -> dict[str, Any]:
    """Rebuild the report from existing pilot artifacts without network."""

    input_manifest = build_input_file_manifest(data_root)
    if input_manifest["INPUT_FILE_N"] != INPUT_FILE_N or input_manifest["INPUT_MANIFEST_HASH"] != INPUT_MANIFEST_HASH:
        raise FeasibilityError("INPUT_MANIFEST_DRIFT")
    request_manifest = json.loads(REQUEST_MANIFEST_PATH.read_text(encoding="utf-8"))
    raw = json.loads(RAW_RECEIPT_PATH.read_text(encoding="utf-8"))
    normalized = json.loads(NORMALIZED_RECEIPT_PATH.read_text(encoding="utf-8"))
    request_hash = request_manifest.pop("REQUEST_MANIFEST_HASH", None)
    raw_hash = raw.pop("RAW_RECEIPT_HASH", None)
    normalized_hash = normalized.pop("NORMALIZED_RECEIPT_HASH", None)
    if request_hash != sha256_json(request_manifest):
        raise FeasibilityError("REQUEST_MANIFEST_HASH_MISMATCH")
    if raw_hash != sha256_json({key: value for key, value in raw.items() if key != "REQUEST_MANIFEST_HASH"}):
        raise FeasibilityError("RAW_RECEIPT_HASH_MISMATCH")
    if normalized_hash != sha256_json({key: value for key, value in normalized.items() if key != "REQUEST_MANIFEST_HASH"}):
        raise FeasibilityError("NORMALIZED_RECEIPT_HASH_MISMATCH")
    snapshot = load_local_snapshot(data_root)
    inventory = inventory_local_authority(data_root)
    selected = {row["symbol"]: row["categories"] for row in request_manifest["requests"] if row["adjustflag"] == QUERY_ADJUSTFLAG}
    report = build_report(
        input_manifest=input_manifest,
        inventory=inventory,
        snapshot=snapshot,
        selected=selected,
        request_manifest_hash=request_hash,
        raw_receipt=raw,
        normalized=normalized,
        raw_hash=raw_hash,
        normalized_hash=normalized_hash,
        test_result=test_result,
        py_compile_result=py_compile_result,
        diff_check_result=diff_check_result,
    )
    write_json(REPORT_JSON_PATH, report)
    write_text(REPORT_MD_PATH, report_markdown(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--fetch", action="store_true", help="run the bounded BaoStock pilot")
    parser.add_argument("--offline-report", action="store_true", help="rebuild from existing receipts only")
    parser.add_argument("--test-result", default="PENDING_TARGETED_TESTS")
    parser.add_argument("--py-compile", dest="py_compile_result", default="PENDING")
    parser.add_argument("--diff-check", dest="diff_check_result", default="PENDING")
    args = parser.parse_args(argv)
    if args.fetch == args.offline_report:
        parser.error("choose exactly one of --fetch or --offline-report")
    try:
        report = (
            run_fetch(args.data_root, test_result=args.test_result)
            if args.fetch
            else run_offline_report(
                args.data_root,
                test_result=args.test_result,
                py_compile_result=args.py_compile_result,
                diff_check_result=args.diff_check_result,
            )
        )
    except FeasibilityError as exc:
        print(json.dumps({"AUTHOR_STATUS": "BLOCKED", "ERROR": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps({key: report[key] for key in (
        "FEASIBILITY", "NEXT_RECOMMENDATION", "PILOT_SYMBOL_N", "PILOT_REQUEST_N",
        "EXPECTED_BAR_CASE_N", "NOT_EXPECTED_BAR_CASE_N", "UNKNOWN_CASE_N",
        "REQUEST_MANIFEST_HASH", "RAW_RECEIPT_HASH", "NORMALIZED_RECEIPT_HASH",
    )}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
