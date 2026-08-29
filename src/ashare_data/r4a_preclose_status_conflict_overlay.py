"""Frozen, fail-closed R4A preclose fallback authority for four proven keys.

The primary BaoStock contract remains unchanged for every ordinary row.  This
module exposes the only permitted exception: four independently adjudicated
``tradestatus=0`` rows whose local, hash-verified Tushare raw-daily evidence
proves an actual traded session and provides a valid display preclose.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping

import polars as pl


OVERLAY_CONTRACT = "R4A_PRECLOSE_PROVEN_STATUS_CONFLICT_OVERLAY_V01"
OVERLAY_KEYS = (
    ("002087.SZ", date(2024, 6, 13)),
    ("600647.SH", date(2024, 6, 13)),
    ("600766.SH", date(2024, 6, 13)),
    ("603133.SH", date(2024, 6, 13)),
)
OVERLAY_KEY_N = 4
OVERLAY_KEYSET_HASH = "49fd7d316e2a09bbb18f0b840d4a5034f3efb2dbba57e9f60255c7a8910b2663"
ADJUDICATION_MANIFEST_HASH = "fbfbd2dd29c35ca686bf3373c2b70aa24ff615f1d2c853542fa7ba3d334a3a30"
EVIDENCE_INDEX_HASH = "2158c237683462b37e5f12357250d69473cd33d2c368d81aa07348453c4b77d3"
EVIDENCE_INDEX_FILE_SHA256 = "86eb2a47aef68a78a886882c9b1526fd3f5efd1edbca3c61a3bbc4b6d8d25a3c"
TUSHARE_FILE = Path("/Users/luke808/AI/V flash/data/raw/tushare/daily_bars/359774eea1675b329e747cbb-0006.parquet")
TUSHARE_FILE_SHA256 = "1413ca2a9f14e5fef2f548643eb7e9353d185ffaaba93ee0a2ba40d9a88f87d1"
R3_REPAIR_REPORT = Path("reports/implementation/R3_PROVEN_MISSING_4KEY_REPAIR_V01.json")
PILOT_REPORT = Path("reports/implementation/R3_STATUS0_SECONDARY_AUTHORITY_PILOT_V01.json")
ADJUDICATION = Path("reports/implementation/R3_STATUS0_SECONDARY_AUTHORITY_PILOT_V01_ADJUDICATION_MANIFEST.json")
EVIDENCE_INDEX = Path("reports/implementation/R3_STATUS0_SECONDARY_AUTHORITY_PILOT_V01_EVIDENCE_INDEX.json")


class OverlayAuthorityError(RuntimeError):
    """The frozen provenance for the overlay cannot be proven."""


def _require(condition: bool, code: str, detail: Any | None = None) -> None:
    if not condition:
        raise OverlayAuthorityError(code if detail is None else f"{code}:{detail}")


def _json_default(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"NOT_JSON_SERIALIZABLE:{type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")


def _sha256_file(path: Path) -> str:
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise OverlayAuthorityError(f"OVERLAY_LOCAL_FILE_READ_FAILED:{path}") from exc
    _require(
        (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev)
        == (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev),
        "OVERLAY_LOCAL_FILE_MUTATED_DURING_READ",
        str(path),
    )
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OverlayAuthorityError(f"OVERLAY_JSON_UNREADABLE:{path}") from exc
    _require(isinstance(value, dict), "OVERLAY_JSON_NOT_OBJECT", str(path))
    return value


def _parse_date(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise OverlayAuthorityError(f"OVERLAY_INVALID_DATE:{value!r}") from exc


def _positive_float(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise OverlayAuthorityError(f"OVERLAY_INVALID_PRECLOSE:{label}") from exc
    _require(math.isfinite(parsed) and parsed > 0, "OVERLAY_INVALID_PRECLOSE", label)
    return parsed


def _display_equal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(str(right)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return False


def keyset_hash(keys: set[tuple[str, date]]) -> str:
    ordered = sorted(keys)
    _require(len(ordered) == len(set(ordered)), "OVERLAY_DUPLICATE_KEY_FOR_HASH")
    digest = hashlib.sha256()
    for symbol, trade_day in ordered:
        digest.update(f"{symbol}\t{trade_day.isoformat()}\n".encode("utf-8"))
    return digest.hexdigest()


def validate_overlay_registry(overlay: Mapping[tuple[str, date], Mapping[str, Any]]) -> None:
    """Require an exact, fully qualified four-key registry before use."""
    keys = set(overlay)
    _require(keys == set(OVERLAY_KEYS), "OVERLAY_SCOPE_MISMATCH")
    _require(keyset_hash(keys) == OVERLAY_KEYSET_HASH, "OVERLAY_KEYSET_HASH_DRIFT")
    for key in OVERLAY_KEYS:
        row = overlay[key]
        _require(row.get("contract") == OVERLAY_CONTRACT, "OVERLAY_CONTRACT_MISMATCH", key)
        _require(row.get("proven_expected_bar") is True, "OVERLAY_EXPECTED_BAR_MISSING", key)
        _require(row.get("r3_required_key") is True, "OVERLAY_REQUIRED_KEY_MISSING", key)
        _require(row.get("tushare_trade_status") is True, "OVERLAY_TUSHARE_NOT_TRADING", key)
        _positive_float(row.get("fallback_preclose"), f"{key[0]}:{key[1]}")
        _require(row.get("source") == "TUSHARE_LOCAL_RAW_PRECLOSE_FALLBACK", "OVERLAY_SOURCE_MISMATCH", key)
        _require(row.get("source_version") == "tushare-1.4.29", "OVERLAY_SOURCE_VERSION_MISMATCH", key)
        _require(row.get("primary_source") == "BAOSTOCK_HISTORY_K_PRECLOSE", "OVERLAY_PRIMARY_SOURCE_MISMATCH", key)
        _require(row.get("primary_tradestatus") == 0, "OVERLAY_PRIMARY_STATUS_MISMATCH", key)
        _require(row.get("primary_status_conflict") is True, "OVERLAY_PRIMARY_CONFLICT_MISSING", key)
        _require(row.get("predecessor_parity") is True, "FALLBACK_PRECLOSE_PARITY_FAILURE", key)


def load_proven_traded_status_conflicts(repo_root: Path, data_root: Path) -> dict[tuple[str, date], dict[str, Any]]:
    """Load and prove the exact four-key local Tushare fallback registry.

    This is intentionally an offline loader.  Any missing, altered, stale, or
    non-exact authority causes a terminal error; no other status-0 key can be
    admitted through this function.
    """
    root = Path(repo_root).resolve(strict=True)
    daily_root = Path(data_root).resolve(strict=True) / "curated" / "daily_bars"
    _require(daily_root.is_dir(), "OVERLAY_CANONICAL_ROOT_MISSING", str(daily_root))
    pilot = _load_json(root / PILOT_REPORT)
    adjudication = _load_json(root / ADJUDICATION)
    evidence_index = _load_json(root / EVIDENCE_INDEX)
    repair_report = _load_json(root / R3_REPAIR_REPORT)
    _require(pilot.get("ADJUDICATION_MANIFEST_HASH") == ADJUDICATION_MANIFEST_HASH, "OVERLAY_PILOT_ADJUDICATION_HASH_MISMATCH")
    _require(pilot.get("EVIDENCE_INDEX_HASH") == EVIDENCE_INDEX_HASH, "OVERLAY_PILOT_EVIDENCE_HASH_MISMATCH")
    body = dict(adjudication)
    _require(body.pop("MANIFEST_HASH", None) == ADJUDICATION_MANIFEST_HASH, "OVERLAY_ADJUDICATION_DECLARATION_MISMATCH")
    _require(hashlib.sha256(canonical_json_bytes(body)).hexdigest() == ADJUDICATION_MANIFEST_HASH, "OVERLAY_ADJUDICATION_HASH_DRIFT")
    _require(hashlib.sha256(canonical_json_bytes(evidence_index)).hexdigest() == EVIDENCE_INDEX_HASH, "OVERLAY_EVIDENCE_HASH_DRIFT")
    _require(hashlib.sha256(canonical_json_bytes(evidence_index) + b"\n").hexdigest() == EVIDENCE_INDEX_FILE_SHA256, "OVERLAY_EVIDENCE_FILE_HASH_DRIFT")
    _require(repair_report.get("TARGET_KEY_N") == OVERLAY_KEY_N, "OVERLAY_REPAIR_TARGET_N_MISMATCH")
    _require(repair_report.get("TARGET_KEYSET_HASH") == OVERLAY_KEYSET_HASH, "OVERLAY_REPAIR_TARGET_HASH_MISMATCH")
    _require(repair_report.get("ADJUDICATION_MANIFEST_HASH") == ADJUDICATION_MANIFEST_HASH, "OVERLAY_REPAIR_ADJUDICATION_HASH_MISMATCH")
    rows = adjudication.get("ROWS")
    evidence_rows = evidence_index.get("ROWS")
    _require(isinstance(rows, list) and isinstance(evidence_rows, list), "OVERLAY_AUTHORITY_ROWS_MISSING")
    adjudicated = {(str(row.get("symbol")), _parse_date(row.get("trade_date"))): row for row in rows}
    evidence = {(str(row.get("symbol")), _parse_date(row.get("trade_date"))): row for row in evidence_rows}
    _require(len(adjudicated) == len(rows) and len(evidence) == len(evidence_rows), "OVERLAY_AUTHORITY_DUPLICATE_KEY")
    _require(set(OVERLAY_KEYS) <= set(adjudicated) and set(OVERLAY_KEYS) <= set(evidence), "OVERLAY_AUTHORITY_KEY_MISSING")
    _require(TUSHARE_FILE.is_file() and not TUSHARE_FILE.is_symlink(), "OVERLAY_TUSHARE_FILE_MISSING")
    _require(_sha256_file(TUSHARE_FILE) == TUSHARE_FILE_SHA256, "OVERLAY_TUSHARE_FILE_HASH_DRIFT")
    raw = pl.read_parquet(TUSHARE_FILE)
    symbols = [symbol.split(".", 1)[0] for symbol, _ in OVERLAY_KEYS]
    target_dates = [trade_day for _, trade_day in OVERLAY_KEYS]
    source_rows = raw.filter(pl.col("code").is_in(symbols) & pl.col("trade_date").is_in(target_dates))
    bars = (
        pl.scan_parquet(str(daily_root / "**" / "*.parquet"))
        .filter(pl.col("symbol").is_in([key[0] for key in OVERLAY_KEYS]) & (pl.col("trade_date") <= max(target_dates)))
        .select(["symbol", "trade_date", "close"])
        .collect()
        .with_columns(pl.col("symbol").cast(pl.String), pl.col("trade_date").cast(pl.Date))
    )
    registry: dict[tuple[str, date], dict[str, Any]] = {}
    repair_sources = repair_report.get("REPAIR_SOURCE")
    _require(isinstance(repair_sources, dict), "OVERLAY_REPAIR_SOURCE_MISSING")
    for key in OVERLAY_KEYS:
        symbol, trade_day = key
        row = adjudicated[key]
        evidence_row = evidence[key]
        _require(row.get("decision") == "EXPECTED_BAR" and row.get("repair_required") is True and row.get("secondary_source") == "TUSHARE_LOCAL_RAW_DAILY", "OVERLAY_ADJUDICATION_NOT_PROVEN", key)
        _require(evidence_row.get("decision") == "EXPECTED_BAR" and evidence_row.get("repair_required") is True, "OVERLAY_EVIDENCE_NOT_PROVEN", key)
        _require(evidence_row.get("primary_tradestatus") == 0, "OVERLAY_PRIMARY_STATUS_NOT_ZERO", key)
        daily = evidence_row.get("secondary_observation", {}).get("daily", {})
        _require(isinstance(daily, dict) and daily.get("provider") == "TUSHARE" and daily.get("provider_version") == "1.4.29" and daily.get("trade_status") is True, "OVERLAY_TUSHARE_DECLARATION_MISMATCH", key)
        _require(daily.get("file") == str(TUSHARE_FILE) and daily.get("file_sha256") == TUSHARE_FILE_SHA256, "OVERLAY_TUSHARE_DECLARATION_HASH_MISMATCH", key)
        matches = source_rows.filter((pl.col("code") == symbol.split(".", 1)[0]) & (pl.col("trade_date") == trade_day))
        _require(matches.height == 1, "OVERLAY_TUSHARE_ROW_CARDINALITY", key)
        source = matches.row(0, named=True)
        _require(source.get("provider") == "TUSHARE" and source.get("provider_version") == "1.4.29" and source.get("trade_status") is True, "OVERLAY_TUSHARE_ROW_MISMATCH", key)
        preclose = _positive_float(source.get("preclose"), f"{symbol}:{trade_day}")
        report_source = repair_sources.get(f"{symbol}:{trade_day.isoformat()}")
        _require(isinstance(report_source, dict) and report_source.get("evidence_file_sha256") == TUSHARE_FILE_SHA256 and str(report_source.get("preclose")) == f"{preclose:.4f}", "OVERLAY_REPAIR_EVIDENCE_MISMATCH", key)
        canonical = bars.filter((pl.col("symbol") == symbol) & (pl.col("trade_date") == trade_day))
        _require(canonical.height == 1, "OVERLAY_CANONICAL_KEY_MISSING_OR_DUPLICATE", key)
        previous = bars.filter((pl.col("symbol") == symbol) & (pl.col("trade_date") < trade_day)).sort("trade_date")
        _require(previous.height > 0, "OVERLAY_PREDECESSOR_MISSING", key)
        prior = previous.row(-1, named=True)
        _require(_display_equal(preclose, prior.get("close")), "FALLBACK_PRECLOSE_PARITY_FAILURE", key)
        primary = evidence_row.get("primary_provider_row")
        _require(isinstance(primary, dict), "OVERLAY_PRIMARY_ROW_MISSING", key)
        registry[key] = {
            "contract": OVERLAY_CONTRACT,
            "symbol": symbol,
            "trade_date": trade_day,
            "r3_required_key": True,
            "proven_expected_bar": True,
            "tushare_trade_status": True,
            "fallback_preclose": preclose,
            "source": "TUSHARE_LOCAL_RAW_PRECLOSE_FALLBACK",
            "source_version": "tushare-1.4.29",
            "evidence_file": str(TUSHARE_FILE),
            "evidence_file_sha256": TUSHARE_FILE_SHA256,
            "tushare_row_hash": source.get("row_hash"),
            "primary_source": "BAOSTOCK_HISTORY_K_PRECLOSE",
            "primary_tradestatus": 0,
            "primary_preclose": _positive_float(primary.get("preclose"), f"primary:{symbol}:{trade_day}"),
            "primary_status_conflict": True,
            "previous_canonical_trade_date": prior["trade_date"].isoformat(),
            "previous_canonical_close": float(prior["close"]),
            "predecessor_parity": True,
        }
    validate_overlay_registry(registry)
    return registry
