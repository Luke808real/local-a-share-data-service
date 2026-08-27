#!/usr/bin/env python3
"""Read-only R3 SH/SZ daily-foundation correctness revalidation.

This module deliberately has no provider client and no data-root writer.  It
binds the current canonical daily-bars bytes to a PRE/POST input manifest,
revalidates the frozen SH/SZ identity and the two prior repair authorities,
and reports the distinction between a locally clean dataset and completeness
that cannot be proven without an independent per-symbol trading-status map.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOT = REPO_ROOT / "tools" / "audits"
if str(AUDIT_ROOT) not in sys.path:
    sys.path.insert(0, str(AUDIT_ROOT))

from r3_tdx_volume_anomaly_audit_v01 import run_audit as run_tdx_hard_anomaly_audit  # noqa: E402


TASK = "R3_SHSZ_DAILY_FOUNDATION_CORRECTNESS_REVALIDATION_V01"
BASE_HEAD = "3859d195855d50386595be5efbb6d5541ec23977"
HISTORY_START = date(2016, 1, 1)
AS_OF = date(2026, 8, 17)

INPUT_FILE_N = 2_580
INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
SHSZ_IDENTITY_N = 5_456
SHSZ_IDENTITY_HASH = "2b1e720232936dcdbbea978e7d4ec26a6b0b22d96ee960af7460c5642717be2f"

REPAIRED_VOLUME_KEY_N = 1_169
CHANGED_MANIFEST_HASH = "f1cdb9d5416535e76631673479ef1fc36539a8626ee18026a581f6e1191b62c7"
PRE_REPAIR_ROW_N = 10_709_989
EXPECTED_POST_REPAIR_ROW_N = PRE_REPAIR_ROW_N + 2

TARGET_SYMBOL = "300546.SZ"
TARGET_KEYS = (
    (TARGET_SYMBOL, date(2016, 9, 29)),
    (TARGET_SYMBOL, date(2016, 10, 10)),
)

REQUIRED_DAILY_COLUMNS = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "source",
    "data_version",
    "fetched_at",
)
EXPECTED_DAILY_SCHEMA = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
    "source": pl.String,
    "data_version": pl.String,
    "fetched_at": pl.Datetime("us", "UTC"),
}
SYMBOL_RE = re.compile(r"^\d{6}\.(SH|SZ)$")
PARTITION_RE = re.compile(r"^trade_date=(\d{4}-\d{2}-\d{2})$")

INPUT_MANIFEST_REFERENCE = "reports/implementation/R3_300546_MISSING_DAYS_POST_INPUT_MANIFEST_V01.json"
CHANGED_MANIFEST_REFERENCE = "reports/implementation/R3_TDX_VOLUME_TARGETED_REFETCH_CHANGED_MANIFEST_V01_1.json"
AUTHORITY_REFERENCE = "reports/implementation/R3_300546_MISSING_DAYS_AUTHORITY_V01.json"
IDENTITY_RECEIPT_NAME = "r3-identity-receipt.json"
ROSTER_PROGRESS_NAME = "r3-quarterly-roster-audit-progress-v074.json"
DELISTED_COVERAGE_NAME = "r3-delisted-coverage.json"


class RevalidationError(RuntimeError):
    """A terminal mismatch; callers must not accept a revalidation result."""


class InputDriftDuringRevalidation(RevalidationError):
    """Raised when the canonical input changes between PRE and POST."""

    def __init__(self, pre: dict[str, Any], post: dict[str, Any]) -> None:
        super().__init__("INPUT_DRIFT_DURING_REVALIDATION")
        self.pre = pre
        self.post = post


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RevalidationError(f"UNREADABLE_LOCAL_ARTIFACT:{path}:{exc}") from exc


def _require(condition: bool, code: str, detail: str | None = None) -> None:
    if not condition:
        raise RevalidationError(code if detail is None else f"{code}:{detail}")


def git_head(repo_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RevalidationError(f"GIT_HEAD_UNAVAILABLE:{exc}") from exc


def build_input_file_manifest(data_root: Path) -> dict[str, Any]:
    """Build the exact frozen daily_bars byte manifest without writing."""
    base = data_root / "curated" / "daily_bars"
    files = sorted(base.rglob("*.parquet"))
    rows: list[dict[str, Any]] = []
    for path in files:
        rows.append(
            {
                "relative_path": str(path.relative_to(data_root)),
                "file_size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "INPUT_FILE_N": len(rows),
        "INPUT_MANIFEST_HASH": sha256_bytes(canonical_json_bytes(rows)),
        "CANONICAL_SERIALIZATION": (
            "json.dumps(rows, ensure_ascii=True, sort_keys=True, "
            "separators=(',', ':')) sorted by relative_path"
        ),
        "FILES": rows,
    }


def input_manifests_equal(pre: dict[str, Any], post: dict[str, Any]) -> bool:
    return (
        pre.get("INPUT_FILE_N") == post.get("INPUT_FILE_N")
        and pre.get("INPUT_MANIFEST_HASH") == post.get("INPUT_MANIFEST_HASH")
        and pre.get("FILES") == post.get("FILES")
    )


def validate_frozen_input_manifest(
    manifest: dict[str, Any], expected: dict[str, Any] | None = None
) -> dict[str, Any]:
    _require(manifest.get("INPUT_FILE_N") == INPUT_FILE_N, "INPUT_FILE_N_MISMATCH")
    _require(
        manifest.get("INPUT_MANIFEST_HASH") == INPUT_MANIFEST_HASH,
        "INPUT_MANIFEST_HASH_MISMATCH",
    )
    files = manifest.get("FILES")
    _require(isinstance(files, list) and len(files) == INPUT_FILE_N, "INPUT_FILES_MISMATCH")
    _require(
        files == sorted(files, key=lambda row: row.get("relative_path", "")),
        "INPUT_FILES_NOT_SORTED",
    )
    _require(
        len({row.get("relative_path") for row in files}) == INPUT_FILE_N,
        "INPUT_FILES_DUPLICATE",
    )
    if expected is not None:
        _require(
            files == expected.get("FILES"),
            "INPUT_FILES_FROZEN_REFERENCE_MISMATCH",
        )
    return manifest


def read_frozen_input_reference(repo_root: Path) -> dict[str, Any]:
    reference = load_json(repo_root / INPUT_MANIFEST_REFERENCE)
    _require(isinstance(reference, dict), "INPUT_REFERENCE_INVALID")
    _require(
        reference.get("INPUT_FILE_N") == INPUT_FILE_N
        and reference.get("INPUT_MANIFEST_HASH") == INPUT_MANIFEST_HASH,
        "INPUT_REFERENCE_AUTHORITY_MISMATCH",
    )
    _require(
        isinstance(reference.get("FILES"), list)
        and len(reference["FILES"]) == INPUT_FILE_N,
        "INPUT_REFERENCE_FILES_MISMATCH",
    )
    return reference


def run_prescan_postscan(
    data_root: Path,
    *,
    manifest_builder: Callable[[Path], dict[str, Any]] = build_input_file_manifest,
    scanner: Callable[[Path], dict[str, Any]],
    pre_validator: Callable[[dict[str, Any]], Any] | None = None,
    post_validator: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run a scanner only while its complete input is bound before and after."""
    pre = manifest_builder(data_root)
    if pre_validator is not None:
        pre_validator(pre)
    result = scanner(data_root)
    post = manifest_builder(data_root)
    if not input_manifests_equal(pre, post):
        raise InputDriftDuringRevalidation(pre, post)
    if post_validator is not None:
        post_validator(post)
    return result, pre, post


def _schema_description(schema: dict[str, Any]) -> dict[str, str]:
    return {str(name): str(dtype) for name, dtype in schema.items()}


def _finite_bad(column: str) -> pl.Expr:
    return pl.col(column).is_null() | ~pl.col(column).is_finite()


def validate_daily_frame(
    daily: pl.DataFrame,
    *,
    partition_date_mismatch_row_n: int = 0,
    partition_parse_error_n: int = 0,
) -> dict[str, Any]:
    """Validate the full daily frame; pure with respect to the filesystem."""
    names = tuple(daily.columns)
    missing_columns = [column for column in REQUIRED_DAILY_COLUMNS if column not in names]
    _require(not missing_columns, "DAILY_REQUIRED_COLUMNS_MISSING", str(missing_columns))

    duplicate_groups = (
        daily.select(["symbol", "trade_date"])
        .group_by(["symbol", "trade_date"])
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") > 1)
    )
    duplicate_key_n = duplicate_groups.height
    duplicate_row_n = (
        int(duplicate_groups.select((pl.col("n") - 1).sum())[0, 0])
        if duplicate_groups.height
        else 0
    )

    null_key_row_n = daily.filter(
        pl.col("symbol").is_null() | pl.col("trade_date").is_null()
    ).height
    malformed_symbol_row_n = daily.filter(
        pl.col("symbol").is_not_null()
        & ~pl.col("symbol").str.contains(SYMBOL_RE.pattern)
    ).height
    finite_ohlc_bad_row_n = daily.filter(
        _finite_bad("open")
        | _finite_bad("high")
        | _finite_bad("low")
        | _finite_bad("close")
    ).height
    invalid_volume_row_n = daily.filter(
        pl.col("volume").is_null() | (pl.col("volume") < 0)
    ).height
    invalid_amount_row_n = daily.filter(
        pl.col("amount").is_null()
        | ~pl.col("amount").is_finite()
        | (pl.col("amount") < 0)
    ).height

    finite_rows = daily.filter(
        ~(_finite_bad("open") | _finite_bad("high") | _finite_bad("low") | _finite_bad("close"))
    )
    ohlc_order_bad_row_n = finite_rows.filter(
        (pl.col("high") < pl.max_horizontal("open", "close", "low"))
        | (pl.col("low") > pl.min_horizontal("open", "close", "high"))
    ).height
    bound_bad_row_n = daily.filter(
        pl.col("trade_date").is_null()
        | (pl.col("trade_date") < HISTORY_START)
        | (pl.col("trade_date") > AS_OF)
    ).height
    required_null_row_n = daily.filter(
        pl.any_horizontal([pl.col(column).is_null() for column in REQUIRED_DAILY_COLUMNS])
    ).height
    data_versions = sorted(
        str(value) for value in daily.select("data_version").unique().to_series().to_list()
    )
    sources = sorted(str(value) for value in daily.select("source").unique().to_series().to_list())
    return {
        "TOTAL_ROW_N": daily.height,
        "UNIQUE_SYMBOL_N": daily["symbol"].n_unique(),
        "ACTUAL_KEY_N": daily.select(["symbol", "trade_date"]).unique().height,
        "MIN_TRADE_DATE": daily["trade_date"].min(),
        "MAX_TRADE_DATE": daily["trade_date"].max(),
        "DUPLICATE_KEY_N": duplicate_key_n,
        "DUPLICATE_ROW_N": duplicate_row_n,
        "NULL_KEY_ROW_N": null_key_row_n,
        "MALFORMED_SYMBOL_ROW_N": malformed_symbol_row_n,
        "FINITE_OHLC_BAD_ROW_N": finite_ohlc_bad_row_n,
        "INVALID_VOLUME_ROW_N": invalid_volume_row_n,
        "INVALID_AMOUNT_ROW_N": invalid_amount_row_n,
        "OHLC_ORDER_BAD_ROW_N": ohlc_order_bad_row_n,
        "DATE_BOUND_BAD_ROW_N": bound_bad_row_n,
        "REQUIRED_NULL_ROW_N": required_null_row_n,
        "PARTITION_DATE_MISMATCH_ROW_N": partition_date_mismatch_row_n,
        "PARTITION_PARSE_ERROR_N": partition_parse_error_n,
        "DATA_VERSION_SET": data_versions,
        "SOURCE_SET": sources,
        "SCHEMA": _schema_description(daily.schema),
    }


def scan_daily_dataset(data_root: Path, expected_input: dict[str, Any]) -> dict[str, Any]:
    daily_root = data_root / "curated" / "daily_bars"
    paths = sorted(daily_root.rglob("*.parquet"))
    _require(paths, "DAILY_DATASET_EMPTY")
    expected_paths = {row["relative_path"] for row in expected_input["FILES"]}
    actual_paths = {str(path.relative_to(data_root)) for path in paths}
    _require(actual_paths == expected_paths, "DAILY_FILE_SET_MISMATCH")

    first_schema = pl.read_parquet(paths[0], n_rows=0).schema
    partition_mismatch_row_n = 0
    partition_parse_error_n = 0
    schema_mismatch_files: list[str] = []
    for path in paths:
        schema = pl.read_parquet(path, n_rows=0).schema
        if schema != first_schema:
            schema_mismatch_files.append(str(path.relative_to(data_root)))
        match = PARTITION_RE.match(path.parent.name)
        if match is None:
            partition_parse_error_n += 1
            continue
        expected_date = date.fromisoformat(match.group(1))
        partition_dates = pl.read_parquet(path, columns=["trade_date"])["trade_date"].unique()
        if partition_dates.len() != 1 or partition_dates[0] != expected_date:
            partition_mismatch_row_n += pl.read_parquet(path, columns=["trade_date"]).height
    _require(not schema_mismatch_files, "DAILY_SCHEMA_MISMATCH", str(schema_mismatch_files[:5]))
    _require(
        first_schema == EXPECTED_DAILY_SCHEMA,
        "DAILY_SCHEMA_TYPE_MISMATCH",
        _schema_description(first_schema),
    )

    daily = pl.read_parquet([str(path) for path in paths])
    quality = validate_daily_frame(
        daily,
        partition_date_mismatch_row_n=partition_mismatch_row_n,
        partition_parse_error_n=partition_parse_error_n,
    )
    quality["SCHEMA_MISMATCH_FILE_N"] = len(schema_mismatch_files)
    quality["EXPECTED_SCHEMA"] = _schema_description(first_schema)
    return {"daily": daily, "quality": quality}


def identity_hash(symbols: Iterable[str]) -> str:
    return sha256_bytes(canonical_json_bytes(sorted(set(symbols))))


def reconstruct_frozen_identity(data_root: Path, actual_symbols: set[str]) -> dict[str, Any]:
    r3_root = data_root / "meta" / "asl" / "r3"
    identity = load_json(r3_root / IDENTITY_RECEIPT_NAME)
    progress = load_json(r3_root / ROSTER_PROGRESS_NAME)
    coverage = load_json(r3_root / DELISTED_COVERAGE_NAME)
    _require(isinstance(identity, dict), "IDENTITY_RECEIPT_INVALID")
    _require(isinstance(progress, dict), "ROSTER_PROGRESS_INVALID")
    _require(isinstance(coverage, dict), "DELISTED_COVERAGE_INVALID")

    roster_symbols = progress.get("roster_union_symbols")
    not_observable = identity.get("roster_not_observable_identity_sample")
    _require(isinstance(roster_symbols, list), "ROSTER_SYMBOL_LIST_MISSING")
    _require(isinstance(not_observable, list), "ROSTER_NOT_OBSERVABLE_LIST_MISSING")
    formal_symbols = sorted(set(str(value) for value in roster_symbols + not_observable))
    _require(len(formal_symbols) == SHSZ_IDENTITY_N, "FROZEN_IDENTITY_N_MISMATCH")
    _require(identity_hash(formal_symbols) == SHSZ_IDENTITY_HASH, "FROZEN_IDENTITY_HASH_MISMATCH")
    _require(identity.get("formal_identity_n") == SHSZ_IDENTITY_N, "IDENTITY_RECEIPT_N_MISMATCH")
    _require(identity.get("formal_identity_hash") == SHSZ_IDENTITY_HASH, "IDENTITY_RECEIPT_HASH_MISMATCH")
    _require(identity.get("shsz_identity_complete") is True, "SHSZ_IDENTITY_NOT_COMPLETE")
    _require(identity.get("shsz_identity_symbols") == SHSZ_IDENTITY_N, "SHSZ_IDENTITY_N_MISMATCH")
    _require(identity.get("shsz_identity_hash") == SHSZ_IDENTITY_HASH, "SHSZ_IDENTITY_HASH_MISMATCH")
    _require(identity.get("roster_union_symbol_hash") == identity_hash(roster_symbols), "ROSTER_HASH_MISMATCH")
    _require(identity.get("roster_union_symbol_n") == len(set(roster_symbols)), "ROSTER_N_MISMATCH")
    _require(coverage.get("formal_identity_authority_complete") is True, "COVERAGE_IDENTITY_NOT_COMPLETE")
    _require(coverage.get("formal_identity_n") == SHSZ_IDENTITY_N, "COVERAGE_IDENTITY_N_MISMATCH")
    _require(coverage.get("formal_identity_hash") == SHSZ_IDENTITY_HASH, "COVERAGE_IDENTITY_HASH_MISMATCH")
    hard_blockers = coverage.get("hard_blockers")
    _require(isinstance(hard_blockers, dict), "COVERAGE_HARD_BLOCKERS_MISSING")

    formal_set = set(formal_symbols)
    missing = sorted(formal_set - actual_symbols)
    unexpected = sorted(actual_symbols - formal_set)
    return {
        "SHSZ_IDENTITY_N": SHSZ_IDENTITY_N,
        "SHSZ_IDENTITY_HASH": SHSZ_IDENTITY_HASH,
        "ACTUAL_SYMBOL_N": len(actual_symbols),
        "MISSING_IDENTITY_SYMBOL_N": len(missing),
        "UNEXPECTED_SYMBOL_N": len(unexpected),
        "MISSING_IDENTITY_SYMBOL_SAMPLE": missing[:20],
        "UNEXPECTED_SYMBOL_SAMPLE": unexpected[:20],
        "FORMAL_IDENTITY_AUTHORITY": identity.get("shsz_identity_authority"),
        "IDENTITY_RECEIPT_PATH": str(r3_root / IDENTITY_RECEIPT_NAME),
        "ROSTER_PROGRESS_PATH": str(r3_root / ROSTER_PROGRESS_NAME),
        "DELISTED_COVERAGE_PATH": str(r3_root / DELISTED_COVERAGE_NAME),
        "ROSTER_SUCCESSFUL_SAMPLE_N": identity.get("successful_sample_n"),
        "ROSTER_FAILED_SAMPLE_N": identity.get("failed_sample_n"),
        "ROSTER_EXTRA_VS_FORMAL_N": identity.get("roster_extra_vs_formal_n"),
        "ROSTER_SPAN_CONFLICT_N": identity.get("roster_span_conflict_n"),
        "FORMAL_DELISTED_N": coverage.get("formal_delisted_n"),
        "DELISTED_RECOVERY_COMPLETE": coverage.get("formal_recovery_complete"),
        "KNOWN_SURVIVORSHIP_COVERAGE_COMPLETE": coverage.get("known_coverage_complete"),
        "DELISTED_HARD_BLOCKERS": hard_blockers,
        "BJ_SCOPE": identity.get("bj_scope", "DEFERRED_EXTENSION"),
        "BJ_HISTORICAL_STATUS": identity.get("bj_historical_status", "UNKNOWN_CARRIED"),
    }


def load_calendar(data_root: Path) -> dict[str, Any]:
    paths = sorted((data_root / "curated" / "trading_calendar").rglob("*.parquet"))
    _require(paths, "TRADING_CALENDAR_EMPTY")
    calendar = pl.read_parquet([str(path) for path in paths])
    _require(
        {"trade_date", "is_trading"}.issubset(calendar.columns),
        "TRADING_CALENDAR_SCHEMA_MISMATCH",
    )
    within = calendar.filter(
        (pl.col("trade_date") >= HISTORY_START)
        & (pl.col("trade_date") <= AS_OF)
        & pl.col("is_trading")
    )
    duplicate_n = (
        calendar.group_by("trade_date").agg(pl.len().alias("n")).filter(pl.col("n") > 1).height
    )
    return {
        "CALENDAR_FILE_N": len(paths),
        "CALENDAR_ROW_N": calendar.height,
        "CALENDAR_DUPLICATE_DATE_N": duplicate_n,
        "TRADING_DATE_N_IN_WINDOW": within["trade_date"].n_unique(),
        "TRADING_DATE_MIN_IN_WINDOW": within["trade_date"].min(),
        "TRADING_DATE_MAX_IN_WINDOW": within["trade_date"].max(),
        "TRADING_DATES_IN_WINDOW": sorted(set(within["trade_date"].to_list())),
    }


def completeness_result(
    *,
    actual_key_n: int,
    actual_symbol_n: int,
    calendar: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    """Return UNKNOWN for full trading-session counts unless status authority exists."""
    return {
        "EXPECTED_KEY_N": "UNKNOWN",
        "ACTUAL_KEY_N": actual_key_n,
        "MISSING_TRADING_KEY_N": "UNKNOWN",
        "UNEXPECTED_TRADING_KEY_N": "UNKNOWN",
        "AUTHORITY_STATUS": "UNVERIFIED_FULL_SESSION_TRADING_STATUS",
        "AUTHORITY_REASON": (
            "The frozen local coverage receipt explicitly limits its proof to "
            "catalogue/discovery, lifetime overlap, last traded bars, and "
            "identity; it does not verify every expected trading session inside "
            "each observed price series. The durable completeness closure is a "
            "25-symbol bounded pilot, not a 5456-symbol independent status map."
        ),
        "BOUNDED_PILOT_SCOPE": "25 symbols / 275 BaoStock windows (historical receipt)",
        "CALENDAR_TRADING_DATE_N": calendar["TRADING_DATE_N_IN_WINDOW"],
        "FORMAL_IDENTITY_N": identity["SHSZ_IDENTITY_N"],
        "ACTUAL_SYMBOL_N": actual_symbol_n,
        "MISSING_KEY_MANIFEST": "NOT_GENERATED_FULL_AUTHORITY_UNKNOWN",
    }


def validate_changed_rows(rows: Any, *, raw_hash: str) -> list[dict[str, Any]]:
    _require(isinstance(rows, list), "CHANGED_MANIFEST_INVALID")
    _require(raw_hash == CHANGED_MANIFEST_HASH, "CHANGED_MANIFEST_HASH_MISMATCH")
    _require(len(rows) == REPAIRED_VOLUME_KEY_N, "CHANGED_MANIFEST_N_MISMATCH")
    keys = [(str(row.get("symbol")), str(row.get("trade_date"))) for row in rows]
    _require(len(set(keys)) == len(keys), "CHANGED_MANIFEST_DUPLICATE_KEY")
    _require(
        rows == sorted(rows, key=lambda row: (row.get("symbol", ""), row.get("trade_date", ""))),
        "CHANGED_MANIFEST_NOT_SORTED",
    )
    _require(
        all(row.get("fetch_status") == "RESOLVED" for row in rows),
        "CHANGED_MANIFEST_FETCH_STATUS_MISMATCH",
    )
    _require(
        all(row.get("fresh_tdx_volume") != row.get("old_volume") for row in rows),
        "CHANGED_MANIFEST_EQUAL_VOLUME",
    )
    classification_counts = Counter(row.get("classification") for row in rows)
    _require(
        classification_counts["PROVABLY_AFFECTED"] == 32,
        "CHANGED_MANIFEST_AFFECTED_N_MISMATCH",
    )
    _require(
        classification_counts["AMBIGUOUS"] == 1_137,
        "CHANGED_MANIFEST_AMBIGUOUS_N_MISMATCH",
    )
    return rows


def load_changed_rows(repo_root: Path) -> list[dict[str, Any]]:
    path = repo_root / CHANGED_MANIFEST_REFERENCE
    _require(path.is_file(), "CHANGED_MANIFEST_MISSING")
    rows = load_json(path)
    return validate_changed_rows(rows, raw_hash=sha256_file(path))


def revalidate_changed_volumes(daily: pl.DataFrame, rows: list[dict[str, Any]]) -> dict[str, Any]:
    changed = pl.DataFrame(rows).with_columns(pl.col("trade_date").str.to_date())
    actual = daily.select(["symbol", "trade_date", "volume"])
    joined = changed.join(actual, on=["symbol", "trade_date"], how="left", suffix="_canonical")
    missing_n = joined.filter(pl.col("volume").is_null()).height
    mismatch_n = joined.filter(
        pl.col("volume").is_not_null()
        & (pl.col("volume") != pl.col("fresh_tdx_volume"))
    ).height
    old_value_n = joined.filter(
        pl.col("volume").is_not_null() & (pl.col("volume") == pl.col("old_volume"))
    ).height
    return {
        "REPAIRED_VOLUME_KEY_N": len(rows),
        "REPAIRED_VOLUME_MISMATCH_N": mismatch_n,
        "REPAIRED_VOLUME_MISSING_N": missing_n,
        "CURRENT_EQUALS_RETAINED_OLD_N": old_value_n,
        "REPAIRED_VOLUME_EXACT": missing_n == 0 and mismatch_n == 0,
    }


def load_authority_facts(repo_root: Path) -> dict[tuple[str, date], dict[str, Any]]:
    payload = load_json(repo_root / AUTHORITY_REFERENCE)
    _require(isinstance(payload, dict), "300546_AUTHORITY_INVALID")
    facts = payload.get("TARGET_FACTS")
    _require(isinstance(facts, list) and len(facts) == 2, "300546_AUTHORITY_FACT_N_MISMATCH")
    out: dict[tuple[str, date], dict[str, Any]] = {}
    for fact in facts:
        key = (str(fact["symbol"]), date.fromisoformat(str(fact["trade_date"])))
        _require(key in TARGET_KEYS, "300546_AUTHORITY_UNEXPECTED_KEY", str(key))
        _require(key not in out, "300546_AUTHORITY_DUPLICATE_KEY", str(key))
        _require(fact.get("tradestatus") == 1, "300546_AUTHORITY_TRADESTATUS_MISMATCH")
        out[key] = fact
    _require(set(out) == set(TARGET_KEYS), "300546_AUTHORITY_TARGET_SET_MISMATCH")
    return out


def compare_300546_authority(
    daily: pl.DataFrame, facts: dict[tuple[str, date], dict[str, Any]]
) -> dict[str, Any]:
    target = daily.filter(pl.col("symbol") == TARGET_SYMBOL)
    target_duplicate_n = (
        target.group_by("trade_date").agg(pl.len().alias("n")).filter(pl.col("n") > 1).height
    )
    mismatches: list[str] = []
    present_n = 0
    for key, fact in sorted(facts.items()):
        symbol, trade_day = key
        rows = target.filter(pl.col("trade_date") == trade_day)
        if rows.height != 1:
            mismatches.append(f"{symbol}:{trade_day}:row_count={rows.height}")
            continue
        present_n += 1
        row = rows.row(0, named=True)
        for field in ("open", "high", "low", "close", "volume", "amount", "source", "data_version"):
            if row.get(field) != fact.get(field):
                mismatches.append(f"{symbol}:{trade_day}:{field}")
    return {
        "300546_TARGET_KEY_N": present_n,
        "300546_TARGET_KEY_EXPECTED_N": len(TARGET_KEYS),
        "300546_TARGET_KEY_MISMATCH_N": len(mismatches),
        "300546_TARGET_DUPLICATE_KEY_N": target_duplicate_n,
        "300546_MISMATCH_SAMPLE": mismatches[:20],
        "300546_AUTHORITY_SOURCE": "BaoStock",
        "300546_AUTHORITY_RUNTIME": "baostock-0.9.3 (frozen receipt; no execution in this task)",
        "300546_AUTHORITY_EXACT": present_n == len(TARGET_KEYS) and not mismatches and target_duplicate_n == 0,
    }


def official_legacy_gate_observations(data_root: Path, daily: pl.DataFrame) -> dict[str, Any]:
    curated = data_root / "curated"
    datasets = sorted(
        path.name
        for path in curated.iterdir()
        if path.is_dir() and list(path.rglob("*.parquet"))
    )
    unexpected = sorted(set(datasets) - {"instruments", "trading_calendar", "daily_bars"})
    unit_frame = daily.filter(
        (pl.col("volume") > 0)
        & pl.col("amount").is_not_null()
        & (pl.col("close") > 0)
        & (pl.col("amount") > 0)
    ).with_columns((pl.col("amount") / pl.col("close") / pl.col("volume")).alias("ratio"))
    unit_rows = unit_frame.group_by("source").agg(
        pl.len().alias("rows"), pl.col("ratio").median().alias("median_ratio")
    ).sort("source")
    units = [
        {"source": row["source"], "rows": row["rows"], "median_ratio": row["median_ratio"]}
        for row in unit_rows.iter_rows(named=True)
    ]
    unit_pass = all(
        item["rows"] < 200
        or (item["median_ratio"] is not None and 0.8 <= item["median_ratio"] <= 1.25)
        for item in units
    )
    return {
        "OFFICIAL_VERIFIER_PATH": "tools/verify_r3_daily_foundation.py",
        "STRUCTURAL_GATE": {
            "status": "FAILED" if unexpected else "PASS",
            "code": "NON_R3_DATASET" if unexpected else None,
            "unexpected_curated_datasets": unexpected,
            "detail": (
                "The frozen official verifier rejects later curated datasets "
                "before daily checks; current root contains corporate_actions."
                if unexpected
                else None
            ),
        },
        "UNIT_GATE": {
            "status": "PASS" if unit_pass else "FAILED",
            "per_source": units,
            "band": [0.8, 1.25],
        },
        "ALL_A_UNIVERSE_GATE": {
            "status": "FAILED",
            "code": "UNIVERSE_MISSING",
            "detail": "Legacy verifier requires active BJ > 0; BJ is deferred for the SH/SZ MVP.",
        },
    }


def read_only_gate_summary(
    *,
    quality: dict[str, Any],
    identity: dict[str, Any],
    completeness: dict[str, Any],
    volume: dict[str, Any],
    target: dict[str, Any],
    legacy: dict[str, Any],
) -> dict[str, list[str]]:
    core_ok = (
        quality["DUPLICATE_KEY_N"] == 0
        and quality["NULL_KEY_ROW_N"] == 0
        and quality["MALFORMED_SYMBOL_ROW_N"] == 0
        and quality["FINITE_OHLC_BAD_ROW_N"] == 0
        and quality["INVALID_VOLUME_ROW_N"] == 0
        and quality["INVALID_AMOUNT_ROW_N"] == 0
        and quality["OHLC_ORDER_BAD_ROW_N"] == 0
        and quality["DATE_BOUND_BAD_ROW_N"] == 0
        and quality["PARTITION_DATE_MISMATCH_ROW_N"] == 0
        and quality["PARTITION_PARSE_ERROR_N"] == 0
        and quality["SCHEMA"] == quality.get("EXPECTED_SCHEMA", quality["SCHEMA"])
    )

    identity_ok = identity["MISSING_IDENTITY_SYMBOL_N"] == 0 and identity["UNEXPECTED_SYMBOL_N"] == 0
    blockers = identity.get("DELISTED_HARD_BLOCKERS", {})
    delisted_ok = (
        identity.get("DELISTED_RECOVERY_COMPLETE") is True
        and identity.get("KNOWN_SURVIVORSHIP_COVERAGE_COMPLETE") is True
        and isinstance(blockers, dict)
        and all(value == 0 for value in blockers.values())
    )
    volume_ok = (
        volume["REPAIRED_VOLUME_EXACT"]
        and volume["TDX_HARD_ANOMALY_N"] == 0
        and volume["BAOSTOCK_HARD_ANOMALY_N"] == 0
    )
    target_ok = target["300546_AUTHORITY_EXACT"]

    passed: list[str] = ["INPUT_IMMUTABILITY_PRE_POST"]
    if core_ok:
        passed.append("R3_CORE_SHSZ_SCHEMA_AND_VALUE_QUALITY")
    if identity_ok:
        passed.append("R3_SHSZ_IDENTITY_AUTHORITY")
    if delisted_ok:
        passed.append("R3_SHSZ_DELISTED_SURVIVORSHIP_RECEIPT")
    if volume_ok:
        passed.extend(
            [
                "R3_TDX_VOLUME_CHANGED_MANIFEST_BINDING",
                "R3_TDX_HARD_ANOMALY_READ_ONLY_AUDIT",
            ]
        )
    if target_ok:
        passed.append("300546_TWO_KEY_AUTHORITY_RECONCILIATION")
    if legacy["UNIT_GATE"]["status"] == "PASS":
        passed.append("OFFICIAL_R3_UNIT_RATIO_GATE")

    failed: list[str] = []
    if legacy["STRUCTURAL_GATE"]["status"] == "FAILED":
        failed.append("OFFICIAL_R3_STRUCTURAL_DATASET_GATE_NON_R3_DATASET")
    if legacy["ALL_A_UNIVERSE_GATE"]["status"] == "FAILED":
        failed.append("OFFICIAL_R3_ALL_A_UNIVERSE_GATE_BJ_DEFERRED")
    if not core_ok:
        failed.append("R3_CORE_SHSZ_SCHEMA_AND_VALUE_QUALITY")
    if not identity_ok:
        failed.append("R3_SHSZ_IDENTITY_AUTHORITY")
    if not delisted_ok:
        failed.append("R3_SHSZ_DELISTED_SURVIVORSHIP_RECEIPT")
    if not volume_ok:
        failed.append("R3_TDX_VOLUME_REVALIDATION")
    if not target_ok:
        failed.append("300546_TWO_KEY_AUTHORITY_RECONCILIATION")
    if legacy["UNIT_GATE"]["status"] != "PASS":
        failed.append("OFFICIAL_R3_UNIT_RATIO_GATE")

    unverified = [
        "R3_FULL_INDEPENDENT_TRADING_SESSION_COMPLETENESS",
        "R3_DAILY_GAP_GATE_NON_CIRCULAR_FULL_RANGE_PROOF",
        "BJ_HISTORICAL_IDENTITY_GATE_SHSZ_SCOPE_DEFERRED",
        "TDX_AMOUNT_GLOBAL_DECODER_CORRECTNESS",
    ]
    _require(completeness["EXPECTED_KEY_N"] == "UNKNOWN", "COMPLETENESS_UNKNOWN_EXPECTATION_CHANGED")
    return {"PASSED_GATES": passed, "FAILED_GATES": failed, "UNVERIFIED_GATES": unverified}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def make_report(
    *,
    pre: dict[str, Any],
    post: dict[str, Any],
    scan: dict[str, Any],
    calendar: dict[str, Any],
    identity: dict[str, Any],
    completeness: dict[str, Any],
    volume: dict[str, Any],
    target: dict[str, Any],
    legacy: dict[str, Any],
    hard_audit: dict[str, Any],
    gates: dict[str, list[str]],
) -> dict[str, Any]:
    quality = scan["quality"]
    decision = "BLOCKED"
    return _json_safe(
        {
            "REPORT": TASK,
            "AUTHOR_STATUS": "BLOCKED_PENDING_SOL_AUDIT",
            "BASE_HEAD": BASE_HEAD,
            "EXECUTION_BASE_HEAD": BASE_HEAD,
            "REPORT_FINAL_COMMIT": "NOT_EMITTED_IN_REPORT",
            "PRE_INPUT_FILE_N": pre["INPUT_FILE_N"],
            "PRE_INPUT_MANIFEST_HASH": pre["INPUT_MANIFEST_HASH"],
            "POST_VALIDATION_INPUT_FILE_N": post["INPUT_FILE_N"],
            "POST_VALIDATION_INPUT_MANIFEST_HASH": post["INPUT_MANIFEST_HASH"],
            "INPUT_STABLE_DURING_REVALIDATION": input_manifests_equal(pre, post),
            "INPUT_MANIFEST_FILES_EQUAL": pre["FILES"] == post["FILES"],
            "INPUT_MANIFEST_REFERENCE": INPUT_MANIFEST_REFERENCE,
            "TOTAL_ROW_N": quality["TOTAL_ROW_N"],
            "UNIQUE_SYMBOL_N": quality["UNIQUE_SYMBOL_N"],
            "ACTUAL_KEY_N": quality["ACTUAL_KEY_N"],
            "MIN_TRADE_DATE": quality["MIN_TRADE_DATE"],
            "MAX_TRADE_DATE": quality["MAX_TRADE_DATE"],
            "ROW_COUNT_RECONCILIATION": {
                "PRE_REPAIR_ROW_N": PRE_REPAIR_ROW_N,
                "EXPECTED_POST_REPAIR_ROW_N": EXPECTED_POST_REPAIR_ROW_N,
                "ACTUAL_POST_REPAIR_ROW_N": quality["TOTAL_ROW_N"],
                "DELTA_FROM_PRE_REPAIR": quality["TOTAL_ROW_N"] - PRE_REPAIR_ROW_N,
                "STATUS": "PASS" if quality["TOTAL_ROW_N"] == EXPECTED_POST_REPAIR_ROW_N else "FAILED",
            },
            "INPUT_FILE_N": INPUT_FILE_N,
            "DUPLICATE_KEY_N": quality["DUPLICATE_KEY_N"],
            "DUPLICATE_ROW_N": quality["DUPLICATE_ROW_N"],
            "NULL_KEY_ROW_N": quality["NULL_KEY_ROW_N"],
            "MALFORMED_SYMBOL_ROW_N": quality["MALFORMED_SYMBOL_ROW_N"],
            "PARTITION_DATE_MISMATCH_ROW_N": quality["PARTITION_DATE_MISMATCH_ROW_N"],
            "PARTITION_PARSE_ERROR_N": quality["PARTITION_PARSE_ERROR_N"],
            "FINITE_OHLC_BAD_ROW_N": quality["FINITE_OHLC_BAD_ROW_N"],
            "INVALID_VOLUME_ROW_N": quality["INVALID_VOLUME_ROW_N"],
            "INVALID_AMOUNT_ROW_N": quality["INVALID_AMOUNT_ROW_N"],
            "OHLC_ORDER_BAD_ROW_N": quality["OHLC_ORDER_BAD_ROW_N"],
            "DATE_BOUND_BAD_ROW_N": quality["DATE_BOUND_BAD_ROW_N"],
            "REQUIRED_NULL_ROW_N": quality["REQUIRED_NULL_ROW_N"],
            "DATA_VERSION_SET": quality["DATA_VERSION_SET"],
            "SOURCE_SET": quality["SOURCE_SET"],
            "SHSZ_IDENTITY_N": identity["SHSZ_IDENTITY_N"],
            "SHSZ_IDENTITY_HASH": identity["SHSZ_IDENTITY_HASH"],
            "MISSING_IDENTITY_SYMBOL_N": identity["MISSING_IDENTITY_SYMBOL_N"],
            "UNEXPECTED_SYMBOL_N": identity["UNEXPECTED_SYMBOL_N"],
            "IDENTITY_RECONCILIATION": identity,
            "CALENDAR": {
                key: value
                for key, value in calendar.items()
                if key != "TRADING_DATES_IN_WINDOW"
            },
            "COMPLETENESS": completeness,
            "EXPECTED_KEY_N": completeness["EXPECTED_KEY_N"],
            "MISSING_TRADING_KEY_N": completeness["MISSING_TRADING_KEY_N"],
            "UNEXPECTED_TRADING_KEY_N": completeness["UNEXPECTED_TRADING_KEY_N"],
            "REPAIRED_VOLUME_KEY_N": volume["REPAIRED_VOLUME_KEY_N"],
            "REPAIRED_VOLUME_MISMATCH_N": volume["REPAIRED_VOLUME_MISMATCH_N"],
            "REPAIRED_VOLUME_MISSING_N": volume["REPAIRED_VOLUME_MISSING_N"],
            "TDX_HARD_ANOMALY_N": hard_audit["TDX_HARD_ANOMALY_N"],
            "BAOSTOCK_HARD_ANOMALY_N": hard_audit["BAOSTOCK_HARD_ANOMALY_N"],
            "300546": target,
            "VOLUME_REVALIDATION": volume,
            "LEGACY_VERIFIER_OBSERVATIONS": legacy,
            "PASSED_GATES": gates["PASSED_GATES"],
            "FAILED_GATES": gates["FAILED_GATES"],
            "UNVERIFIED_GATES": gates["UNVERIFIED_GATES"],
            "R3_SHSZ_DAILY_FOUNDATION_CORRECTNESS_REVALIDATION": decision,
            "R3_CORRECTNESS_REOPENED": True,
            "R3_DAILY_FOUNDATION_REFREEZE_RECOMMENDATION": "NO",
            "DECISION_REASON": (
                "Core local SH/SZ shape, identity, value quality, repaired volume "
                "binding, and the two inserted authority rows revalidate cleanly. "
                "Full 5456-symbol expected trading-session completeness remains "
                "UNKNOWN, and the legacy all-A verifier has explicit non-applicable "
                "BJ/structural blockers; UNKNOWN is not PASS."
            ),
            "SAFETY": {
                "NETWORK_PROVIDER_DATA_FETCH": "NO",
                "TDX_REFETCH_EXECUTED": False,
                "BAOSTOCK_EXECUTED": False,
                "R3_DATA_REBUILD_EXECUTED": False,
                "R3_MARKET_DATA_WRITE": "NO",
                "CANONICAL_WRITE_EXECUTED": False,
                "CANONICAL_BYTES_MUTATED": False,
                "R4A9_CHECKPOINT_MUTATED": False,
                "R4A9_RESUME_AUTHORIZED": False,
                "PRECLOSE_COMPLETE": False,
                "PRODUCTION": False,
                "FORWARD": False,
                "TRADEPLAN": False,
            },
        }
    )


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# R3 SH/SZ DAILY FOUNDATION CORRECTNESS REVALIDATION V01",
        "",
        f"AUTHOR_STATUS: `{report['AUTHOR_STATUS']}`",
        f"BASE_HEAD: `{report['BASE_HEAD']}`",
        "",
        "## Decision",
        "",
        f"- R3_SHSZ_DAILY_FOUNDATION_CORRECTNESS_REVALIDATION: `{report['R3_SHSZ_DAILY_FOUNDATION_CORRECTNESS_REVALIDATION']}`",
        f"- R3_CORRECTNESS_REOPENED: `{str(report['R3_CORRECTNESS_REOPENED']).lower()}`",
        f"- R3_DAILY_FOUNDATION_REFREEZE_RECOMMENDATION: `{report['R3_DAILY_FOUNDATION_REFREEZE_RECOMMENDATION']}`",
        "- Core local checks are clean, but full expected traded-session coverage is `UNKNOWN`; UNKNOWN is not PASS.",
        "",
        "## Input binding and shape",
        "",
        f"- PRE_INPUT_FILE_N / HASH: `{report['PRE_INPUT_FILE_N']}` / `{report['PRE_INPUT_MANIFEST_HASH']}`",
        f"- POST_VALIDATION_INPUT_FILE_N / HASH: `{report['POST_VALIDATION_INPUT_FILE_N']}` / `{report['POST_VALIDATION_INPUT_MANIFEST_HASH']}`",
        f"- INPUT_STABLE_DURING_REVALIDATION: `{str(report['INPUT_STABLE_DURING_REVALIDATION']).lower()}`",
        f"- TOTAL_ROW_N: {report['TOTAL_ROW_N']}; UNIQUE_SYMBOL_N: {report['UNIQUE_SYMBOL_N']}; ACTUAL_KEY_N: {report['ACTUAL_KEY_N']}",
        f"- ROW_COUNT_RECONCILIATION: pre-repair `{report['ROW_COUNT_RECONCILIATION']['PRE_REPAIR_ROW_N']}` + 2 expected `{report['ROW_COUNT_RECONCILIATION']['EXPECTED_POST_REPAIR_ROW_N']}`; actual `{report['ROW_COUNT_RECONCILIATION']['ACTUAL_POST_REPAIR_ROW_N']}` (`{report['ROW_COUNT_RECONCILIATION']['STATUS']}`)",
        f"- MIN_TRADE_DATE / MAX_TRADE_DATE: `{report['MIN_TRADE_DATE']}` / `{report['MAX_TRADE_DATE']}`",
        f"- DUPLICATE_KEY_N: {report['DUPLICATE_KEY_N']}; PARTITION_DATE_MISMATCH_ROW_N: {report['PARTITION_DATE_MISMATCH_ROW_N']}",
        "",
        "## Frozen authorities",
        "",
        f"- SHSZ_IDENTITY_N / HASH: `{report['SHSZ_IDENTITY_N']}` / `{report['SHSZ_IDENTITY_HASH']}`",
        f"- MISSING_IDENTITY_SYMBOL_N: {report['MISSING_IDENTITY_SYMBOL_N']}; UNEXPECTED_SYMBOL_N: {report['UNEXPECTED_SYMBOL_N']}",
        f"- REPAIRED_VOLUME_KEY_N / MISMATCH_N: {report['REPAIRED_VOLUME_KEY_N']} / {report['REPAIRED_VOLUME_MISMATCH_N']}",
        f"- 300546_TARGET_KEY_N / MISMATCH_N: {report['300546']['300546_TARGET_KEY_N']} / {report['300546']['300546_TARGET_KEY_MISMATCH_N']}",
        "",
        "## Completeness boundary",
        "",
        f"- EXPECTED_KEY_N: `{report['EXPECTED_KEY_N']}`",
        f"- MISSING_TRADING_KEY_N: `{report['MISSING_TRADING_KEY_N']}`",
        f"- UNEXPECTED_TRADING_KEY_N: `{report['UNEXPECTED_TRADING_KEY_N']}`",
        f"- AUTHORITY_STATUS: `{report['COMPLETENESS']['AUTHORITY_STATUS']}`",
        "- Frozen coverage evidence is a bounded pilot and explicitly does not prove every expected session for every symbol.",
        "",
        "## Gates",
        "",
        f"- PASSED_GATES: {', '.join(report['PASSED_GATES'])}",
        f"- FAILED_GATES: {', '.join(report['FAILED_GATES'])}",
        f"- UNVERIFIED_GATES: {', '.join(report['UNVERIFIED_GATES'])}",
        "",
        "## Safety",
        "",
    ]
    for key, value in report["SAFETY"].items():
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        lines.append(f"- {key}={rendered}")
    return "\n".join(lines) + "\n"


def run_revalidation(
    *,
    repo_root: Path,
    data_root: Path,
    expected_input: dict[str, Any],
    manifest_builder: Callable[[Path], dict[str, Any]] = build_input_file_manifest,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    def scanner(root: Path) -> dict[str, Any]:
        scan = scan_daily_dataset(root, expected_input)
        daily = scan["daily"]
        calendar = load_calendar(root)
        identity = reconstruct_frozen_identity(root, set(daily["symbol"].unique().to_list()))
        completeness = completeness_result(
            actual_key_n=scan["quality"]["ACTUAL_KEY_N"],
            actual_symbol_n=scan["quality"]["UNIQUE_SYMBOL_N"],
            calendar=calendar,
            identity=identity,
        )
        changed_rows = load_changed_rows(repo_root)
        volume = revalidate_changed_volumes(daily, changed_rows)
        hard_audit = run_tdx_hard_anomaly_audit(root)
        volume.update(
            {
                "TDX_HARD_ANOMALY_N": hard_audit["TDX_HARD_ANOMALY_N"],
                "BAOSTOCK_HARD_ANOMALY_N": hard_audit["BAOSTOCK_HARD_ANOMALY_N"],
                "TDX_HARD_ANOMALY_SYMBOL_N": hard_audit["TDX_HARD_ANOMALY_SYMBOL_N"],
            }
        )
        facts = load_authority_facts(repo_root)
        target = compare_300546_authority(daily, facts)
        legacy = official_legacy_gate_observations(root, daily)
        return {
            "scan": scan,
            "calendar": calendar,
            "identity": identity,
            "completeness": completeness,
            "volume": volume,
            "target": target,
            "legacy": legacy,
            "hard_audit": hard_audit,
        }

    result, pre, post = run_prescan_postscan(
        data_root,
        manifest_builder=manifest_builder,
        scanner=scanner,
        pre_validator=lambda manifest: validate_frozen_input_manifest(manifest, expected_input),
        post_validator=lambda manifest: validate_frozen_input_manifest(manifest, expected_input),
    )
    return result, pre, post


def write_report_files(
    *,
    report_dir: Path,
    report: dict[str, Any],
    pre: dict[str, Any],
    post: dict[str, Any],
) -> None:
    """Write only repository report artifacts after PRE == POST has passed."""
    _require(report_dir.is_relative_to(REPO_ROOT), "REPORT_DIR_OUTSIDE_REPO")
    report_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "R3_SHSZ_DAILY_FOUNDATION_CORRECTNESS_REVALIDATION_PRE_INPUT_MANIFEST_V01.json": pre,
        "R3_SHSZ_DAILY_FOUNDATION_CORRECTNESS_REVALIDATION_POST_VALIDATION_INPUT_MANIFEST_V01.json": post,
        "R3_SHSZ_DAILY_FOUNDATION_CORRECTNESS_REVALIDATION_V01.json": report,
    }
    for name, payload in outputs.items():
        (report_dir / name).write_bytes(canonical_json_bytes(payload))
    (report_dir / "R3_SHSZ_DAILY_FOUNDATION_CORRECTNESS_REVALIDATION_V01.md").write_text(
        markdown_report(report), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/cnequity.toml"))
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--report-dir", type=Path, default=REPO_ROOT / "reports" / "implementation")
    args = parser.parse_args()

    try:
        _require(git_head(REPO_ROOT) == BASE_HEAD, "BASE_HEAD_MISMATCH")
        config_path = (REPO_ROOT / args.config).resolve()
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        data_root = (args.data_root or Path(config["data"]["root"])).resolve()
        expected = read_frozen_input_reference(REPO_ROOT)
        result, pre, post = run_revalidation(
            repo_root=REPO_ROOT,
            data_root=data_root,
            expected_input=expected,
        )
        gates = read_only_gate_summary(
            quality=result["scan"]["quality"],
            identity=result["identity"],
            completeness=result["completeness"],
            volume=result["volume"],
            target=result["target"],
            legacy=result["legacy"],
        )
        report = make_report(
            pre=pre,
            post=post,
            scan=result["scan"],
            calendar=result["calendar"],
            identity=result["identity"],
            completeness=result["completeness"],
            volume=result["volume"],
            target=result["target"],
            legacy=result["legacy"],
            hard_audit=result["hard_audit"],
            gates=gates,
        )
        write_report_files(report_dir=(REPO_ROOT / args.report_dir).resolve(), report=report, pre=pre, post=post)
    except RevalidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
