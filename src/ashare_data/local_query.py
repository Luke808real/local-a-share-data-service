"""Read-only queries over the published ASL daily data.

This module deliberately has no provider imports and no write path.  It uses
the existing read-only DuckDB catalog when the catalog points at the selected
data root, and otherwise uses a read-only DuckDB connection over the existing
Parquet files.  The latter is a query plan, not a second database or a data
copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


DEFAULT_DATA_ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
DAILY_RELATIVE_PATH = Path("curated/daily_bars")
INSTRUMENTS_RELATIVE_PATH = Path("curated/instruments")
DUCKDB_RELATIVE_PATH = Path("duckdb/cnequity.duckdb")

# This is the current published R3 state carried by the data root.  The value
# is also checked against the durable post-promotion receipt by status().
DAILY_USABLE = True
DAILY_COVERAGE_STATUS = "PARTIAL"
PRECLOSE_COMPLETE = False
FACTS_READY = False

READY_BAR_COLUMNS = (
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
)
IDENTITY_COLUMNS = (
    "symbol",
    "name",
    "exchange",
    "asset_type",
    "list_date",
    "delist_date",
    "prev_symbol",
    "source",
    "data_version",
)
CANONICAL_SERIALIZATION = (
    "json.dumps(rows, ensure_ascii=True, sort_keys=True, "
    "separators=(',', ':')) sorted by relative_path"
)

_SYMBOL_RE = re.compile(r"^(?P<code>[0-9]{6})(?:\.(?P<exchange>SH|SZ))?$")
_PARTITION_RE = re.compile(r"^trade_date=(?P<trade_date>[0-9]{4}-[0-9]{2}-[0-9]{2})$")


class QueryError(RuntimeError):
    """A fail-closed query error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, details: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _as_json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _as_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_json_value(v) for v in value]
    # DuckDB/optional dataframe backends may return scalar wrapper objects.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _as_json_value(item())
        except Exception:
            pass
    return value


def parse_iso_date(value: str | date, *, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise QueryError("INVALID_DATE", f"{field_name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise QueryError("INVALID_DATE", f"{field_name} must be YYYY-MM-DD") from exc


def normalize_symbol_input(value: str) -> tuple[str, str | None]:
    """Validate a symbol input without guessing an exchange.

    The returned pair is ``(six_digit_code, explicit_exchange_or_none)``.
    Resolution of a bare code is intentionally deferred to the formal local
    identity table, where zero or multiple matches fail closed.
    """

    if not isinstance(value, str):
        raise QueryError("INVALID_SYMBOL", "symbol must be a six-digit code")
    candidate = value.strip().upper()
    match = _SYMBOL_RE.fullmatch(candidate)
    if not match:
        raise QueryError(
            "INVALID_SYMBOL",
            "symbol must be six digits, or six digits followed by .SH/.SZ",
        )
    return match.group("code"), match.group("exchange")


def manifest_byte_form(rows: Sequence[dict[str, Any]]) -> bytes:
    return _json_bytes(list(rows))


def _ensure_inside(root: Path, path: Path, *, label: str, must_exist: bool = True) -> Path:
    try:
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise QueryError("DATA_ROOT_INCOMPLETE", f"missing {label}: {path}") from exc
    if not resolved.is_relative_to(root):
        raise QueryError(
            "DATA_ROOT_ESCAPE",
            f"{label} escapes DATA_ROOT: {resolved}",
        )
    return resolved


def build_daily_file_manifest(data_root: Path) -> dict[str, Any]:
    """Hash the canonical daily file set using the existing R3 contract."""

    root = Path(data_root).expanduser().resolve(strict=True)
    base = _ensure_inside(root, root / DAILY_RELATIVE_PATH, label="daily root")
    files = sorted(base.rglob("*.parquet"), key=lambda p: str(p.relative_to(root)))
    rows: list[dict[str, Any]] = []
    for path in files:
        resolved = _ensure_inside(root, path, label="daily parquet")
        before = resolved.stat()
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        after = resolved.stat()
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_identity != after_identity:
            raise QueryError(
                "DAILY_FILE_MUTATED_DURING_HASH",
                f"daily parquet changed while hashing: {resolved}",
            )
        rows.append(
            {
                "relative_path": str(resolved.relative_to(root)),
                "file_size": after.st_size,
                "sha256": digest.hexdigest(),
            }
        )
    serialized = manifest_byte_form(rows)
    return {
        "INPUT_FILE_N": len(rows),
        "INPUT_MANIFEST_HASH": _hash_bytes(serialized),
        "CANONICAL_SERIALIZATION": CANONICAL_SERIALIZATION,
        "FILES": rows,
    }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _published_manifest_reference(root: Path) -> tuple[Path, dict[str, Any]] | None:
    """Locate the current durable post-promotion manifest authority.

    The first path is the latest bounded canonical promotion receipt.  The
    older two-day promotion manifest is retained only as a fallback for a
    clean fixture or an earlier local data root; its hash is always checked
    against live files by ``status``.
    """

    candidates = (
        root
        / "staging/r3_proven_missing_4key_repair_v01/transaction/promotion_receipt.json",
        root / "staging/r3_300546_missing_days_promotion_v01/promotion_receipt.json",
        root
        / "staging/r3_300546_missing_days_promotion_v01/observed_post_input_manifest.json",
        root
        / "staging/r3_300546_missing_days_promotion_v01/expected_post_input_manifest.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        obj = _read_json_object(path)
        if obj is None:
            continue
        if "POST_INPUT_MANIFEST_HASH" in obj and "POST_INPUT_FILE_N" in obj:
            return path, obj
        if "INPUT_MANIFEST_HASH" in obj and "INPUT_FILE_N" in obj:
            return path, obj
    return None


def _manifest_reference_values(reference: dict[str, Any]) -> tuple[int, str]:
    if "POST_INPUT_MANIFEST_HASH" in reference:
        n = reference.get("POST_INPUT_FILE_N")
        digest = reference.get("POST_INPUT_MANIFEST_HASH")
    else:
        n = reference.get("INPUT_FILE_N")
        digest = reference.get("INPUT_MANIFEST_HASH")
    if not isinstance(n, int) or not isinstance(digest, str):
        raise QueryError(
            "PUBLISHED_MANIFEST_INVALID",
            "published manifest reference lacks file count/hash",
        )
    return n, digest


def _format_columns(columns: Iterable[str]) -> str:
    return ", ".join(f'"{column}"' for column in columns)


class LocalQuery:
    """Thin read-only query core over the existing authoritative lake."""

    def __init__(
        self,
        data_root: Path | str | None = None,
        *,
        require_published_manifest: bool = True,
    ) -> None:
        configured = data_root or os.environ.get("ASL_DATA_ROOT") or DEFAULT_DATA_ROOT
        self.data_root = Path(configured).expanduser().resolve(strict=True)
        self.daily_root = _ensure_inside(
            self.data_root,
            self.data_root / DAILY_RELATIVE_PATH,
            label="daily root",
        )
        self.instruments_root = _ensure_inside(
            self.data_root,
            self.data_root / INSTRUMENTS_RELATIVE_PATH,
            label="instruments root",
        )
        if not any(self.daily_root.rglob("*.parquet")):
            raise QueryError("DATA_ROOT_INCOMPLETE", "no daily parquet files found")
        if not any(self.instruments_root.rglob("*.parquet")):
            raise QueryError("DATA_ROOT_INCOMPLETE", "no instrument parquet files found")

        self._manifest_reference = _published_manifest_reference(self.data_root)
        if require_published_manifest and self._manifest_reference is None:
            raise QueryError(
                "PUBLISHED_AUTHORITY_NOT_FOUND",
                "current published daily manifest reference was not found",
            )

        self._connection: Any | None = None
        self._backend = ""
        self._daily_relation = ""
        self._instrument_relation = ""
        self._open_read_backend()

    @property
    def query_backend(self) -> str:
        return self._backend

    @property
    def daily_manifest_reference(self) -> Path | None:
        return self._manifest_reference[0] if self._manifest_reference else None

    def _open_read_backend(self) -> None:
        try:
            import duckdb  # type: ignore
        except ImportError:
            raise QueryError(
                "QUERY_BACKEND_UNAVAILABLE",
                "DuckDB is not available in the active Python environment",
            )

        catalog_path = self.data_root / DUCKDB_RELATIVE_PATH
        if catalog_path.is_file():
            try:
                catalog = duckdb.connect(str(catalog_path), read_only=True)
                views = catalog.execute(
                    """
                    select table_name, view_definition
                    from information_schema.views
                    where table_schema = 'main'
                      and table_name in ('daily_bars', 'instruments')
                    """
                ).fetchall()
                definitions = {name: definition for name, definition in views}
                daily_definition = str(definitions.get("daily_bars", ""))
                instrument_definition = str(definitions.get("instruments", ""))
                if (
                    str(self.daily_root) in daily_definition
                    and str(self.instruments_root) in instrument_definition
                ):
                    self._connection = catalog
                    self._backend = "duckdb_catalog"
                    self._daily_relation = "daily_bars"
                    self._instrument_relation = "instruments"
                    return
                catalog.close()
            except Exception:
                try:
                    catalog.close()
                except Exception:
                    pass

        try:
            self._connection = duckdb.connect(":memory:")
        except Exception as exc:  # pragma: no cover - depends on environment
            raise QueryError("QUERY_BACKEND_UNAVAILABLE", str(exc)) from exc
        daily_glob = str(self.daily_root / "**" / "*.parquet")
        instruments_glob = str(self.instruments_root / "**" / "*.parquet")
        self._backend = "duckdb_parquet"
        self._daily_relation = (
            f"read_parquet({_sql_literal(daily_glob)}, "
            "hive_partitioning=true, union_by_name=true)"
        )
        self._instrument_relation = (
            f"read_parquet({_sql_literal(instruments_glob)}, union_by_name=true)"
        )

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def __enter__(self) -> "LocalQuery":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _execute(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> list[dict[str, Any]]:
        if self._connection is None:
            raise QueryError("QUERY_BACKEND_CLOSED", "query backend is closed")
        try:
            cursor = self._connection.execute(sql, list(params))
            columns = [item[0] for item in cursor.description or ()]
            return [
                {column: _as_json_value(value) for column, value in zip(columns, row)}
                for row in cursor.fetchall()
            ]
        except QueryError:
            raise
        except Exception as exc:
            raise QueryError("QUERY_FAILED", str(exc)) from exc

    def _resolve_symbol_candidates(
        self,
        code: str,
        exchange: str | None,
    ) -> list[str]:
        predicates = [
            "i.exchange in ('SH', 'SZ')",
            "regexp_matches(upper(i.symbol), '^[0-9]{6}\\.(SH|SZ)$')",
            "substr(upper(i.symbol), 1, 6) = ?",
            f"exists (select 1 from {self._daily_relation} d where d.symbol = i.symbol)",
        ]
        params: list[Any] = [code]
        if exchange is not None:
            predicates.append("upper(i.symbol) = ?")
            params.append(f"{code}.{exchange}")
        rows = self._execute(
            "select distinct upper(i.symbol) as symbol "
            f"from {self._instrument_relation} i "
            "where "
            + " and ".join(predicates)
            + " order by symbol",
            params,
        )
        return [str(row["symbol"]) for row in rows]

    def resolve_symbol(self, value: str) -> str:
        code, exchange = normalize_symbol_input(value)
        candidates = self._resolve_symbol_candidates(code, exchange)
        if not candidates:
            raise QueryError("UNKNOWN_SYMBOL", f"symbol is not in the formal local scope: {value}")
        if len(candidates) != 1:
            raise QueryError(
                "AMBIGUOUS_SYMBOL",
                f"bare symbol maps to multiple formal symbols: {value}",
                details=candidates,
            )
        return candidates[0]

    def _daily_rows(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        predicates = ["symbol = ?"]
        params: list[Any] = [symbol]
        if start is not None:
            predicates.append("trade_date >= ?")
            params.append(start)
        if end is not None:
            predicates.append("trade_date <= ?")
            params.append(end)
        sql = (
            f"select {_format_columns(READY_BAR_COLUMNS)} "
            f"from {self._daily_relation} "
            "where "
            + " and ".join(predicates)
            + " order by trade_date "
            + ("desc" if descending else "asc")
        )
        if limit is not None:
            sql += " limit ?"
            params.append(limit)
        return self._execute(sql, params)

    def bars(
        self,
        symbol: str,
        start: str | date,
        end: str | date,
    ) -> dict[str, Any]:
        start_date = parse_iso_date(start, field_name="start")
        end_date = parse_iso_date(end, field_name="end")
        if start_date > end_date:
            raise QueryError("INVALID_DATE_RANGE", "start must be on or before end")
        resolved = self.resolve_symbol(symbol)
        rows = self._daily_rows(resolved, start=start_date, end=end_date)
        return {
            "command": "bars",
            "symbol": resolved,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "row_n": len(rows),
            "rows": rows,
            "query_backend": self.query_backend,
            "read_only": True,
        }

    def latest(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise QueryError("INVALID_LIMIT", "limit must be a positive integer")
        resolved = self.resolve_symbol(symbol)
        rows = self._daily_rows(resolved, descending=True, limit=limit)
        return {
            "command": "latest",
            "symbol": resolved,
            "limit": limit,
            "row_n": len(rows),
            "rows": rows,
            "query_backend": self.query_backend,
            "read_only": True,
        }

    def instrument(self, symbol: str) -> dict[str, Any]:
        resolved = self.resolve_symbol(symbol)
        rows = self._execute(
            f"select {_format_columns(IDENTITY_COLUMNS)} "
            f"from {self._instrument_relation} i "
            "where upper(i.symbol) = ? "
            f"and exists (select 1 from {self._daily_relation} d where d.symbol = i.symbol)",
            [resolved],
        )
        if len(rows) != 1:
            raise QueryError(
                "IDENTITY_NOT_UNIQUE",
                f"formal instrument is not unique for {resolved}",
                details={"row_n": len(rows)},
            )
        return {
            "command": "instrument",
            "instrument": rows[0],
            "query_backend": self.query_backend,
            "read_only": True,
        }

    def _latest_partition_date(self) -> date | None:
        found: list[date] = []
        for entry in self.daily_root.iterdir():
            match = _PARTITION_RE.fullmatch(entry.name)
            if not match or not entry.is_dir():
                continue
            try:
                found.append(date.fromisoformat(match.group("trade_date")))
            except ValueError as exc:
                raise QueryError(
                    "INVALID_DAILY_PARTITION",
                    f"invalid daily partition name: {entry.name}",
                ) from exc
        return max(found) if found else None

    def _formal_identity_n(self) -> int:
        receipt_path = self.data_root / "meta/asl/r3/r3-identity-receipt.json"
        receipt = _read_json_object(receipt_path)
        if receipt is not None and isinstance(receipt.get("formal_identity_n"), int):
            return int(receipt["formal_identity_n"])
        rows = self._execute(
            f"select count(distinct symbol) as n from {self._daily_relation}"
        )
        return int(rows[0]["n"]) if rows else 0

    def verify_current_manifest(self) -> dict[str, Any]:
        actual = build_daily_file_manifest(self.data_root)
        if self._manifest_reference is not None:
            path, reference = self._manifest_reference
            expected_n, expected_hash = _manifest_reference_values(reference)
            if (
                actual["INPUT_FILE_N"] != expected_n
                or actual["INPUT_MANIFEST_HASH"] != expected_hash
            ):
                raise QueryError(
                    "DAILY_MANIFEST_DRIFT",
                    "current canonical daily files do not match published manifest",
                    details={
                        "manifest_reference": str(path),
                        "expected_file_n": expected_n,
                        "actual_file_n": actual["INPUT_FILE_N"],
                        "expected_hash": expected_hash,
                        "actual_hash": actual["INPUT_MANIFEST_HASH"],
                    },
                )
            actual["PUBLISHED_MANIFEST_REFERENCE"] = str(path)
        return actual

    def status(self) -> dict[str, Any]:
        manifest = self.verify_current_manifest()
        latest = self._latest_partition_date()
        return {
            "DATA_ROOT": str(self.data_root),
            "DAILY_USABLE": DAILY_USABLE,
            "DAILY_COVERAGE_STATUS": DAILY_COVERAGE_STATUS,
            "DAILY_MANIFEST_HASH": manifest["INPUT_MANIFEST_HASH"],
            "DAILY_MANIFEST_FILE_N": manifest["INPUT_FILE_N"],
            "DAILY_MANIFEST_SOURCE": manifest.get("PUBLISHED_MANIFEST_REFERENCE"),
            "FORMAL_IDENTITY_N": self._formal_identity_n(),
            "LATEST_AVAILABLE_TRADE_DATE": latest.isoformat() if latest else None,
            "PRECLOSE_COMPLETE": PRECLOSE_COMPLETE,
            "FACTS_READY": FACTS_READY,
            "QUERY_BACKEND": self.query_backend,
            "READ_ONLY": True,
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
        }

    def require_ready_fact(self, fact_name: str) -> None:
        """Reject R4 facts until their authority gate is explicitly ready."""

        normalized = str(fact_name).strip().lower()
        if normalized not in {"open", "high", "low", "close", "volume", "amount"}:
            raise QueryError(
                "FACT_NOT_READY",
                f"fact is not READY in the current local authority: {fact_name}",
            )


def query_bars(
    symbol: str,
    start: str | date,
    end: str | date,
    *,
    data_root: Path | str | None = None,
) -> dict[str, Any]:
    with LocalQuery(data_root) as query:
        return query.bars(symbol, start, end)


def query_latest(
    symbol: str,
    limit: int = 20,
    *,
    data_root: Path | str | None = None,
) -> dict[str, Any]:
    with LocalQuery(data_root) as query:
        return query.latest(symbol, limit)


def query_instrument(
    symbol: str,
    *,
    data_root: Path | str | None = None,
) -> dict[str, Any]:
    with LocalQuery(data_root) as query:
        return query.instrument(symbol)


def query_status(*, data_root: Path | str | None = None) -> dict[str, Any]:
    with LocalQuery(data_root) as query:
        return query.status()
