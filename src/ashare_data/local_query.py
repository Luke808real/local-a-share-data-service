"""Read-only queries over the published ASL daily data.

This module deliberately has no provider imports and no write path.  It uses
an in-memory DuckDB connection over an explicitly verified published file
allowlist. Physical candidate files never enter the daily SQL relation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
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
DAILY_FACT_FIELDS = ("preclose", "pct_chg", "turnover_rate", "trade_status", "is_st")

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


def _file_identity(root: Path, path: Path) -> tuple[int, ...]:
    """Reject symlinks in every component, including in-root redirections."""
    if not path.is_relative_to(root):
        raise QueryError("DATA_ROOT_ESCAPE", "file escapes data root")
    current = root
    try:
        for part in path.relative_to(root).parts:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise QueryError("DATA_ROOT_ESCAPE", "symlink in query path")
        if not stat.S_ISREG(info.st_mode):
            raise QueryError("DAILY_MANIFEST_DRIFT", "query file is not regular")
        return (info.st_dev, info.st_ino, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)
    except OSError as exc:
        raise QueryError("DAILY_MANIFEST_DRIFT", "query file missing or inaccessible") from exc


def _partition_date(relative: str) -> date:
    parts = Path(relative).parts
    if (len(parts) != 4 or parts[:2] != DAILY_RELATIVE_PATH.parts
            or Path(relative).as_posix() != relative
            or any(c in relative for c in "*?[]")
            or not parts[-1].endswith(".parquet")):
        raise QueryError("PUBLISHED_MANIFEST_INVALID", "invalid published daily path")
    match = _PARTITION_RE.fullmatch(parts[2])
    try:
        if match:
            return date.fromisoformat(match.group("trade_date"))
    except ValueError:
        pass
    raise QueryError("INVALID_DAILY_PARTITION", "invalid daily partition date")


def _published_manifest_reference(root: Path) -> tuple[Path, dict[str, Any]]:
    pointer_path = root / "meta/asl/r3/published-daily-authority.json"
    if not pointer_path.exists():
        # Historical immutable authority for roots not yet advanced to the
        # pointer contract.
        directory = root / "staging/r3_proven_missing_4key_repair_v01/transaction"
        receipt_path = directory / "promotion_receipt.json"
        plan_path = directory / "promotion_plan.json"
    else:
        pointer = _read_json_object(pointer_path)
        if pointer is None or pointer.get("schema") != "R3_PUBLISHED_DAILY_AUTHORITY_V01":
            raise QueryError("PUBLISHED_MANIFEST_INVALID", "invalid publication authority pointer")
        receipt_rel, plan_rel = pointer.get("receipt"), pointer.get("plan")
        if not all(isinstance(value, str) and value.startswith("staging/")
                   and ".." not in Path(value).parts for value in (receipt_rel, plan_rel)):
            raise QueryError("PUBLISHED_MANIFEST_INVALID", "invalid publication authority pointer")
        receipt_path, plan_path = root / receipt_rel, root / plan_rel
    if not receipt_path.exists():
        raise QueryError("PUBLISHED_AUTHORITY_NOT_FOUND", "published receipt is required")
    authority_paths = (receipt_path, plan_path)
    before = {p: _file_identity(root, p) for p in authority_paths}
    receipt = _read_json_object(receipt_path)
    plan = _read_json_object(plan_path)
    if any(_file_identity(root, p) != identity for p, identity in before.items()):
        raise QueryError("PUBLISHED_MANIFEST_INVALID", "publication evidence changed during read")
    manifest = plan.get("EXPECTED_POST_INPUT_MANIFEST") if plan else None
    if not receipt or receipt.get("STATE") != "COMMITTED" or not isinstance(manifest, dict):
        raise QueryError("PUBLISHED_MANIFEST_INVALID", "committed receipt and full manifest required")
    rows = manifest.get("FILES")
    if not isinstance(rows, list) or not rows:
        raise QueryError("PUBLISHED_MANIFEST_INVALID", "published FILES must be nonempty")
    paths = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"relative_path", "file_size", "sha256"}
                or not isinstance(row["relative_path"], str)
                or type(row["file_size"]) is not int or row["file_size"] < 0
                or not isinstance(row["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])):
            raise QueryError("PUBLISHED_MANIFEST_INVALID", "invalid published file record")
        _partition_date(row["relative_path"])
        paths.append(row["relative_path"])
    expected_n, expected_hash = _manifest_reference_values(receipt)
    if (paths != sorted(set(paths))
            or manifest.get("CANONICAL_SERIALIZATION") != CANONICAL_SERIALIZATION
            or manifest.get("INPUT_FILE_N") != len(rows) or expected_n != len(rows)
            or manifest.get("INPUT_MANIFEST_HASH") != expected_hash
            or _hash_bytes(manifest_byte_form(rows)) != expected_hash
            or receipt.get("EXPECTED_POST_INPUT_FILE_N", expected_n) != expected_n
            or receipt.get("EXPECTED_POST_INPUT_MANIFEST_HASH", expected_hash) != expected_hash):
        raise QueryError("PUBLISHED_MANIFEST_INVALID", "manifest payload and receipt disagree")
    if pointer_path.exists() and pointer.get("manifest_hash") != expected_hash:
        raise QueryError("PUBLISHED_MANIFEST_INVALID", "publication authority pointer hash disagrees")
    quality = receipt.get("POST_VALIDATION", {}).get("QUALITY", receipt.get("QUALITY", {}))
    if pointer_path.exists() and not all((
        quality.get("STRUCTURAL_PASS") is True,
        quality.get("COVERAGE_PASS") is True,
        quality.get("PROVENANCE_PASS") is True,
        quality.get("UNRESOLVED_KEY_N") == 0,
        quality.get("SOURCE_ERROR_N") == 0,
    )):
        raise QueryError("PUBLISHED_MANIFEST_INVALID", "published quality gate is not satisfied")
    maximum = max(_partition_date(p) for p in paths).isoformat()
    if quality.get("MAX_TRADE_DATE", maximum) != maximum:
        raise QueryError("PUBLISHED_MANIFEST_INVALID", "receipt date disagrees with partitions")
    return receipt_path, manifest


def _manifest_reference_values(reference: dict[str, Any]) -> tuple[int, str]:
    if "POST_INPUT_MANIFEST_HASH" in reference:
        n = reference.get("POST_INPUT_FILE_N")
        digest = reference.get("POST_INPUT_MANIFEST_HASH")
    else:
        n = reference.get("INPUT_FILE_N")
        digest = reference.get("INPUT_MANIFEST_HASH")
    if type(n) is not int or not isinstance(digest, str):
        raise QueryError(
            "PUBLISHED_MANIFEST_INVALID",
            "published manifest reference lacks file count/hash",
        )
    return n, digest


def _daily_facts_authority(root: Path) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Read the separate facts authority; absent means facts are not ready."""
    pointer_path = root / "meta/asl/daily_facts/published-daily-facts-authority.json"
    if not pointer_path.exists():
        return None
    pointer = _read_json_object(pointer_path)
    if not pointer or pointer.get("schema") != "ASL_PUBLISHED_DAILY_FACTS_AUTHORITY_V01":
        raise QueryError("DAILY_FACTS_AUTHORITY_INVALID", "invalid daily facts pointer")
    paths=[]
    for key in ("plan", "receipt"):
        relative=pointer.get(key)
        if not isinstance(relative,str) or not relative.startswith("staging/") or ".." in Path(relative).parts:
            raise QueryError("DAILY_FACTS_AUTHORITY_INVALID", "invalid daily facts evidence path")
        paths.append(root / relative)
    plan, receipt = (_read_json_object(path) for path in paths)
    if not plan or not receipt or receipt.get("STATE") != "COMMITTED":
        raise QueryError("DAILY_FACTS_AUTHORITY_INVALID", "uncommitted daily facts authority")
    manifest=plan.get("manifest")
    quality=receipt.get("quality")
    if (not isinstance(manifest,dict) or not isinstance(quality,dict)
            or pointer.get("manifest_hash") != manifest.get("manifest_hash")
            or receipt.get("manifest_hash") != manifest.get("manifest_hash")
            or quality.get("PASS") is not True):
        raise QueryError("DAILY_FACTS_AUTHORITY_INVALID", "daily facts evidence disagrees")
    files=manifest.get("files")
    if not isinstance(files,list) or not files:
        raise QueryError("DAILY_FACTS_AUTHORITY_INVALID", "missing daily facts manifest")
    for item in files:
        if not isinstance(item,dict) or not isinstance(item.get("relative_path"),str) or not item["relative_path"].startswith("curated/daily_facts/trade_date="):
            raise QueryError("DAILY_FACTS_AUTHORITY_INVALID", "invalid daily facts manifest path")
        path=root/item["relative_path"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256"):
            raise QueryError("DAILY_FACTS_MANIFEST_DRIFT", "daily facts file drift")
    return manifest, receipt


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
        if (self.daily_root != self.data_root / DAILY_RELATIVE_PATH
                or self.instruments_root != self.data_root / INSTRUMENTS_RELATIVE_PATH):
            raise QueryError("DATA_ROOT_ESCAPE", "symlink in dataset root")
        if not any(self.daily_root.rglob("*.parquet")):
            raise QueryError("DATA_ROOT_INCOMPLETE", "no daily parquet files found")
        if not any(self.instruments_root.rglob("*.parquet")):
            raise QueryError("DATA_ROOT_INCOMPLETE", "no instrument parquet files found")

        if not require_published_manifest:
            raise QueryError(
                "PUBLISHED_AUTHORITY_REQUIRED",
                "public queries cannot disable publication verification",
            )
        self._manifest_reference = _published_manifest_reference(self.data_root)
        pointer = self.data_root / "meta/asl/r3/published-daily-authority.json"
        evidence = [self._manifest_reference[0], self._manifest_reference[0].parent / "promotion_plan.json"]
        if pointer.exists():
            evidence.append(pointer)
        self._authority_identities = {path: _file_identity(self.data_root, path) for path in evidence}
        self._published_files = self._manifest_reference[1]["FILES"]
        self._facts_authority = _daily_facts_authority(self.data_root)
        self.latest_good_as_of = max(_partition_date(r["relative_path"]) for r in self._published_files)
        self._verified_identities: dict[Path, tuple[int, ...]] = {}
        self.verify_current_manifest()
        self._instrument_files = sorted(self.instruments_root.rglob("*.parquet"))
        self._instrument_identities = {
            p: _file_identity(self.data_root, p) for p in self._instrument_files
        }

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

        try:
            self._connection = duckdb.connect(":memory:")
        except Exception as exc:  # pragma: no cover - depends on environment
            raise QueryError("QUERY_BACKEND_UNAVAILABLE", str(exc)) from exc
        self._backend = "duckdb_parquet"
        self._daily_relation = self._daily_allowlist()
        self._instrument_relation = (
            "read_parquet([" + ",".join(_sql_literal(str(p)) for p in self._instrument_files)
            + "], union_by_name=true)"
        )

    def _facts_for_rows(self, rows: list[dict[str, Any]], fields: Sequence[str] | None) -> list[dict[str, Any]]:
        requested=tuple(field for field in (fields or ()) if field in DAILY_FACT_FIELDS)
        if not requested: return rows
        if self._facts_authority is None:
            raise QueryError("FACT_NOT_READY", "daily facts are not published")
        manifest, _receipt=self._facts_authority
        paths=[self.data_root / item["relative_path"] for item in manifest["files"]]
        relation="read_parquet(["+",".join(_sql_literal(str(p)) for p in paths)+"], union_by_name=true)"
        keys=[(row["symbol"],row["trade_date"]) for row in rows]
        if not keys: return rows
        facts=self._execute("select symbol, trade_date, "+_format_columns(requested)+" from "+relation+" where (symbol, cast(trade_date as varchar)) in ("+",".join("(?,?)" for _ in keys)+")", [part for key in keys for part in key])
        indexed={(item["symbol"],item["trade_date"]):item for item in facts}
        if len(indexed) != len(keys): raise QueryError("FACT_NOT_READY", "requested rows are outside facts scope")
        for row in rows: row.update({field:indexed[(row["symbol"],row["trade_date"])][field] for field in requested})
        return rows

    def _daily_allowlist(self, start: date | None = None, end: date | None = None) -> str:
        paths = [self.data_root / row["relative_path"] for row in self._published_files
                 if (start is None or _partition_date(row["relative_path"]) >= start)
                 and (end is None or _partition_date(row["relative_path"]) <= end)]
        # Empty date windows are handled before constructing SQL.
        return "read_parquet([" + ",".join(_sql_literal(str(p)) for p in paths) + \
            "], hive_partitioning=true, union_by_name=true)"

    def _check_query_files(self) -> None:
        self.verify_current_manifest()
        for path, identity in self._instrument_identities.items():
            if _file_identity(self.data_root, path) != identity:
                raise QueryError("IDENTITY_FILE_DRIFT", "instrument file changed during query session")

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
        self._check_query_files()
        try:
            cursor = self._connection.execute(sql, list(params))
            columns = [item[0] for item in cursor.description or ()]
            rows = [
                {column: _as_json_value(value) for column, value in zip(columns, row)}
                for row in cursor.fetchall()
            ]
            self._check_query_files()
            return rows
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
        end = min(end or self.latest_good_as_of, self.latest_good_as_of)
        if not any((start is None or _partition_date(r["relative_path"]) >= start)
                   and _partition_date(r["relative_path"]) <= end for r in self._published_files):
            self._check_query_files()
            return []
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
            f"from {self._daily_allowlist(start, end)} "
            "where "
            + " and ".join(predicates)
            + " order by trade_date "
            + ("desc" if descending else "asc")
        )
        if limit is not None:
            sql += " limit ?"
            params.append(limit)
        return self._execute(sql, params)

    def _as_of_metadata(self, requested: date | None = None) -> dict[str, Any]:
        effective = min(requested or self.latest_good_as_of, self.latest_good_as_of)
        return {
            "REQUESTED_AS_OF": (requested or self.latest_good_as_of).isoformat(),
            "EFFECTIVE_AS_OF": effective.isoformat(),
            "AS_OF_STATUS": "CAPPED_TO_LATEST_GOOD" if requested and requested > effective else "OK",
            "DAILY_PUBLISHED_AS_OF": self.latest_good_as_of.isoformat(),
            "LATEST_PUBLISHED_TRADE_DATE": self.latest_good_as_of.isoformat(),
            "PUBLICATION_SCOPE": "R3_DAILY_PROMOTION",
            "R7_FIRST_PUBLISH_PASS": False,
            "DAILY_MANIFEST_HASH": self._manifest_reference[1]["INPUT_MANIFEST_HASH"],
            "DAILY_COVERAGE_STATUS": DAILY_COVERAGE_STATUS,
            "PRECLOSE_COMPLETE": PRECLOSE_COMPLETE,
            "FACTS_READY": FACTS_READY,
            **self._physical_metadata(),
        }

    def _physical_metadata(self) -> dict[str, Any]:
        physical = list(self.daily_root.rglob("*.parquet"))
        published = {row["relative_path"] for row in self._published_files}
        pending = [p for p in physical if str(p.relative_to(self.data_root)) not in published]
        dates = []
        for path in physical:
            try:
                dates.append(_partition_date(str(path.relative_to(self.data_root))))
            except QueryError:
                # Unpublished files have no authority, even with unparseable paths.
                continue
        return {
            "LATEST_PHYSICAL_TRADE_DATE": max(dates).isoformat() if dates else None,
            "PHYSICAL_DAILY_FILE_N": len(physical),
            "PENDING_FILE_N": len(pending),
            "PUBLICATION_STATUS": "PENDING_UNPUBLISHED_FILES" if pending else "PUBLISHED_BASELINE_VERIFIED",
        }

    def bars(
        self,
        symbol: str,
        start: str | date,
        end: str | date,
        *, fact_fields: Sequence[str] | None = None) -> dict[str, Any]:
        start_date = parse_iso_date(start, field_name="start")
        end_date = parse_iso_date(end, field_name="end")
        if start_date > end_date:
            raise QueryError("INVALID_DATE_RANGE", "start must be on or before end")
        resolved = self.resolve_symbol(symbol)
        rows = self._facts_for_rows(self._daily_rows(resolved, start=start_date, end=end_date), fact_fields)
        return {
            "command": "bars",
            "symbol": resolved,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            **self._as_of_metadata(end_date),
            "row_n": len(rows),
            "rows": rows,
            "query_backend": self.query_backend,
            "read_only": True,
        }

    def latest(self, symbol: str, limit: int = 20, *, as_of: str | date | None = None, fact_fields: Sequence[str] | None = None) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise QueryError("INVALID_LIMIT", "limit must be a positive integer")
        resolved = self.resolve_symbol(symbol)
        requested = parse_iso_date(as_of, field_name="as_of") if as_of is not None else None
        rows = self._facts_for_rows(self._daily_rows(resolved, end=requested, descending=True, limit=limit), fact_fields)
        return {
            "command": "latest",
            "symbol": resolved,
            "limit": limit,
            **self._as_of_metadata(requested),
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
            "where upper(i.symbol) = ? ",
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
            "IDENTITY_SCOPE": "CURRENT_LOCAL_SNAPSHOT_WITH_PUBLISHED_DAILY_MEMBERSHIP",
            "HISTORICAL_IDENTITY_AS_OF_CERTIFIED": False,
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
            f"select count(distinct symbol) as n from {self._instrument_relation} "
            "where exchange in ('SH', 'SZ') and regexp_matches(symbol, '^[0-9]{6}\\.(SH|SZ)$')"
        )
        return int(rows[0]["n"]) if rows else 0

    def verify_current_manifest(self) -> dict[str, Any]:
        for path, identity in self._authority_identities.items():
            if _file_identity(self.data_root, path) != identity:
                raise QueryError("PUBLISHED_MANIFEST_INVALID", "publication evidence changed during query session")
        for row in self._published_files:
            path = self.data_root / row["relative_path"]
            before = _file_identity(self.data_root, path)
            cached = self._verified_identities.get(path)
            if cached is not None:
                if before != cached:
                    raise QueryError("DAILY_MANIFEST_DRIFT", "published file identity changed")
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            if (before != _file_identity(self.data_root, path)
                    or before[2] != row["file_size"] or digest.hexdigest() != row["sha256"]):
                raise QueryError(
                    "DAILY_MANIFEST_DRIFT",
                    "published file does not match verified manifest",
                )
            self._verified_identities[path] = before
        return {**self._manifest_reference[1],
                "PUBLISHED_MANIFEST_REFERENCE": str(self._manifest_reference[0])}

    def status(self) -> dict[str, Any]:
        manifest = self.verify_current_manifest()
        return {
            "DATA_ROOT": str(self.data_root),
            "DAILY_USABLE": DAILY_USABLE,
            "DAILY_COVERAGE_STATUS": DAILY_COVERAGE_STATUS,
            "DAILY_MANIFEST_HASH": manifest["INPUT_MANIFEST_HASH"],
            "DAILY_MANIFEST_FILE_N": manifest["INPUT_FILE_N"],
            "DAILY_MANIFEST_SOURCE": manifest.get("PUBLISHED_MANIFEST_REFERENCE"),
            "FORMAL_IDENTITY_N": self._formal_identity_n(),
            "LATEST_AVAILABLE_TRADE_DATE": self.latest_good_as_of.isoformat(),
            "LATEST_PUBLISHED_TRADE_DATE": self.latest_good_as_of.isoformat(),
            "DAILY_PUBLISHED_AS_OF": self.latest_good_as_of.isoformat(),
            "R7_FIRST_PUBLISH_PASS": False,
            "PUBLICATION_SCOPE": "R3_DAILY_PROMOTION",
            **self._physical_metadata(),
            "PRECLOSE_COMPLETE": PRECLOSE_COMPLETE,
            "FACTS_READY": FACTS_READY,
            "DAILY_FACTS_PHASE1_STATUS": "VERTICAL_SLICE_PUBLISHED" if self._facts_authority else "NOT_PUBLISHED",
            "DAILY_FACTS_PHASE1_SCOPE": "VERTICAL_SLICE" if self._facts_authority else None,
            "DAILY_FACTS_MANIFEST_HASH": self._facts_authority[0]["manifest_hash"] if self._facts_authority else None,
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
