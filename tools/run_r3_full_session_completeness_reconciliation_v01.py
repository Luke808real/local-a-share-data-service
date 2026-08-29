#!/usr/bin/env python3
"""Reconcile frozen R3 session authority with canonical daily bars.

This is an offline, read-only data-root audit.  It recomputes the extraction
authority from local staging, streams the session-authority Parquet artifact,
compares exact composite keys with canonical daily bars, and writes only
compact audit artifacts below the repository's reports directory.  It never
imports a provider client and never writes the data root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_r3_full_session_completeness_authority_extraction_v01 as extraction  # noqa: E402


TASK = "R3_FULL_SESSION_COMPLETENESS_RECONCILIATION_V01"
BRANCH = "codex/r3-full-session-completeness-reconciliation-v01"
BASE_HEAD = "9f4eec67ea1c9e8c2d11a46264a0d2864caeac5a"
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

STAGING_DIRNAME = "r3_full_session_completeness_authority_v01"
STAGE_RELATIVE = f"staging/{STAGING_DIRNAME}"
SESSION_AUTHORITY_NAME = "session_authority.parquet"
FULL_REQUEST_MANIFEST_NAME = "full_request_manifest.json"
FULL_RECEIPT_INDEX_NAME = "full_request_receipt_index.json"
FULL_EXECUTION_RECEIPT_NAME = "full_execution_receipt.json"
FULL_QUALITY_REPORT_NAME = "full_quality_report.json"
FULL_SESSION_METADATA_NAME = "full_session_authority_metadata.json"

DAILY_INPUT_MANIFEST_HASH = "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
FULL_REQUEST_N = 48_345
FULL_REQUEST_MANIFEST_HASH = "4654e4282d5191cf680dc6b539025c816bc4d21028a4f2e200230209348412ed"
REQUEST_RECEIPT_INDEX_HASH = "0884112d77b75469bc0e4145dfde8ad749b4ae16b7d6974e365029bca799ab8b"
LIFECYCLE_SESSION_KEY_N = 10_897_229
LIFECYCLE_SESSION_KEYSET_HASH = "eacb645dc8ac6273458fe3a59ea46b6be5072a234f0acf3fabbe2dd7463f2a65"
SESSION_AUTHORITY_DATASET_HASH = "0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47"
SESSION_AUTHORITY_FILE_SHA256 = "33df049921b8a9158f5910d58020707a39d9493a50ddf252aab6c49ebee85cd8"

EXPECTED_KEY_N = 10_709_989
NOT_EXPECTED_KEY_N = 187_201
UNKNOWN_KEY_N = 39

UNKNOWN_BASIS = "PROVIDER_ROW_ABSENT_IN_LIFETIME"
EXPECTED_BASIS = "PROVIDER_TRADESTATUS_1"
NOT_EXPECTED_BASIS = "PROVIDER_TRADESTATUS_0"
ALLOWED_CLASSIFICATIONS = {"EXPECTED_BAR", "NOT_EXPECTED_BAR", "UNKNOWN"}

KEYSET_SERIALIZATION = (
    "UTF-8 lines: symbol<TAB>trade_date ISO YYYY-MM-DD<LF>, sorted by "
    "(symbol, trade_date); duplicate keys are rejected before hashing"
)
MANIFEST_SERIALIZATION = (
    "canonical JSON UTF-8, ensure_ascii=True, sort_keys=True, compact separators; "
    "rows sorted by (symbol, trade_date)"
)

ANCHORS = (
    ("300546.SZ", date(2016, 9, 29)),
    ("300546.SZ", date(2016, 10, 10)),
)

REPORT_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"
UNKNOWN_MANIFEST_NAME = f"{TASK}_UNKNOWN_MANIFEST.json"
MISSING_EXPECTED_NAME = f"{TASK}_MISSING_EXPECTED_KEYS.json"
NOT_EXPECTED_NAME = f"{TASK}_UNEXPECTED_NOT_EXPECTED_KEYS.json"
ON_UNKNOWN_NAME = f"{TASK}_CANONICAL_ON_UNKNOWN_KEYS.json"
OUTSIDE_NAME = f"{TASK}_CANONICAL_OUTSIDE_AUTHORITY_KEYS.json"

MAX_MANIFEST_ROWS = 100_000


class ReconciliationError(RuntimeError):
    """Terminal fail-closed error."""


def canonical_json_bytes(value: Any) -> bytes:
    def default(item: Any) -> str:
        if isinstance(item, datetime):
            return item.isoformat()
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReconciliationError(f"LOCAL_FILE_READ_FAILED:{path}") from exc
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"LOCAL_JSON_READ_FAILED:{path}") from exc


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(f"INVALID_DATE:{value!r}") from exc


def key_line(key: tuple[str, date]) -> bytes:
    return f"{key[0]}\t{key[1].isoformat()}\n".encode("utf-8")


def keyset_hash(keys: Iterable[tuple[str, date]]) -> str:
    digest = hashlib.sha256()
    previous: tuple[str, date] | None = None
    for key in keys:
        if previous is not None and key <= previous:
            raise ReconciliationError("KEYSET_NOT_STRICTLY_SORTED_OR_DUPLICATE")
        digest.update(key_line(key))
        previous = key
    return digest.hexdigest()


def require_exact_stage_root(data_root: Path, stage_root: Path) -> Path:
    """Require the existing stage root to be the exact resolved data-root path."""

    data_input = Path(data_root).expanduser()
    stage_input = Path(stage_root).expanduser()
    if data_input.is_symlink() or (data_input / "staging").is_symlink() or stage_input.is_symlink():
        raise ReconciliationError("STAGING_ROOT_SYMLINK_NOT_ALLOWED")
    try:
        data = data_input.resolve(strict=True)
        stage = stage_input.resolve(strict=True)
    except OSError as exc:
        raise ReconciliationError("STAGING_ROOT_RESOLUTION_FAILED") from exc
    expected = (data / "staging" / STAGING_DIRNAME).resolve()
    if stage != expected:
        raise ReconciliationError(f"STAGING_ROOT_MISMATCH:{stage}:{expected}")
    if not stage.is_dir():
        raise ReconciliationError("STAGING_ROOT_NOT_DIRECTORY")
    return stage


def repo_output_path(repo_root: Path, name: str) -> Path:
    root = Path(repo_root).resolve()
    path = (root / "reports" / "implementation" / name).resolve()
    if root not in path.parents:
        raise ReconciliationError("REPO_OUTPUT_PATH_ESCAPE")
    return path


def write_repo_json(repo_root: Path, name: str, payload: Any) -> str:
    path = repo_output_path(repo_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(payload) + b"\n"
    path.write_bytes(data)
    return sha256_bytes(data)


def write_repo_text(repo_root: Path, name: str, text: str) -> str:
    path = repo_output_path(repo_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def _stage_path(stage: Path, name: str) -> Path:
    path = (stage / name).resolve()
    if stage not in path.parents:
        raise ReconciliationError(f"STAGE_FILE_PATH_ESCAPE:{name}")
    return path


def _verify_current_input(context: dict[str, Any]) -> dict[str, Any]:
    manifest = context.get("input_manifest")
    if not isinstance(manifest, dict):
        raise ReconciliationError("DAILY_INPUT_MANIFEST_UNAVAILABLE")
    if manifest.get("INPUT_FILE_N") != 2_580:
        raise ReconciliationError("DAILY_INPUT_FILE_N_MISMATCH")
    if manifest.get("INPUT_MANIFEST_HASH") != DAILY_INPUT_MANIFEST_HASH:
        raise ReconciliationError("DAILY_INPUT_MANIFEST_DRIFT")
    if not isinstance(manifest.get("FILES"), list) or len(manifest["FILES"]) != 2_580:
        raise ReconciliationError("DAILY_INPUT_FILES_MISMATCH")
    return manifest


def verify_receipt_index(receipt_index: dict[str, Any]) -> list[dict[str, Any]]:
    """Recompute and validate the persisted full-run receipt-index hash."""

    if not isinstance(receipt_index, dict):
        raise ReconciliationError("REQUEST_RECEIPT_INDEX_NOT_OBJECT")
    receipt_rows = receipt_index.get("REQUESTS")
    recomputed_index_hash = sha256_json(receipt_rows)
    if receipt_index.get("REQUEST_RECEIPT_INDEX_HASH") != REQUEST_RECEIPT_INDEX_HASH:
        raise ReconciliationError("REQUEST_RECEIPT_INDEX_DECLARED_HASH_MISMATCH")
    if recomputed_index_hash != REQUEST_RECEIPT_INDEX_HASH:
        raise ReconciliationError("REQUEST_RECEIPT_INDEX_RECOMPUTED_HASH_MISMATCH")
    if (
        receipt_index.get("FULL_REQUEST_N") != FULL_REQUEST_N
        or receipt_index.get("FULL_REQUEST_MANIFEST_HASH") != FULL_REQUEST_MANIFEST_HASH
        or receipt_index.get("LIFECYCLE_SESSION_KEYSET_HASH") != LIFECYCLE_SESSION_KEYSET_HASH
        or not isinstance(receipt_rows, list)
        or len(receipt_rows) != FULL_REQUEST_N
        or receipt_index.get("COMPLETE_REQUEST_N") != FULL_REQUEST_N
        or receipt_index.get("FAILED_REQUEST_N") != 0
        or receipt_index.get("PROVIDER_FAILED_REQUEST_N") != 0
        or receipt_index.get("DUPLICATE_PROVIDER_KEY_N") != 0
        or receipt_index.get("INVALID_PROVIDER_ROW_N") != 0
    ):
        raise ReconciliationError("REQUEST_RECEIPT_INDEX_AUTHORITY_MISMATCH")
    return receipt_rows


def _load_stage_authority(
    repo_root: Path,
    data_root: Path,
    stage_root: Path,
    *,
    authority_loader: Callable[[Path, Path], dict[str, Any]] = extraction.load_and_verify_authority,
) -> dict[str, Any]:
    stage = require_exact_stage_root(data_root, stage_root)
    try:
        context = authority_loader(Path(repo_root), Path(data_root))
    except extraction.ExtractionError as exc:
        raise ReconciliationError(f"EXTRACTION_AUTHORITY_GATE:{exc}") from exc
    _verify_current_input(context)

    request_manifest = load_json(_stage_path(stage, FULL_REQUEST_MANIFEST_NAME))
    if not isinstance(request_manifest, dict):
        raise ReconciliationError("FULL_REQUEST_MANIFEST_STAGE_NOT_OBJECT")
    persisted_request_hash = request_manifest.get("FULL_REQUEST_MANIFEST_HASH")
    request_body = dict(request_manifest)
    request_body.pop("FULL_REQUEST_MANIFEST_HASH", None)
    if persisted_request_hash != FULL_REQUEST_MANIFEST_HASH or sha256_json(request_body) != FULL_REQUEST_MANIFEST_HASH:
        raise ReconciliationError("FULL_REQUEST_MANIFEST_STAGE_HASH_MISMATCH")
    requests = request_body.get("requests")
    if not isinstance(requests, list) or len(requests) != FULL_REQUEST_N:
        raise ReconciliationError("FULL_REQUEST_MANIFEST_STAGE_SCOPE_MISMATCH")
    if requests != context.get("requests"):
        raise ReconciliationError("FULL_REQUEST_MANIFEST_STAGE_ROW_MISMATCH")

    receipt_index = load_json(_stage_path(stage, FULL_RECEIPT_INDEX_NAME))
    verify_receipt_index(receipt_index)

    execution = load_json(_stage_path(stage, FULL_EXECUTION_RECEIPT_NAME))
    quality = load_json(_stage_path(stage, FULL_QUALITY_REPORT_NAME))
    metadata = load_json(_stage_path(stage, FULL_SESSION_METADATA_NAME))
    if not isinstance(execution, dict) or not isinstance(quality, dict) or not isinstance(metadata, dict):
        raise ReconciliationError("EXTRACTION_STAGE_METADATA_NOT_OBJECT")
    if execution.get("FULL_EXTRACTION_COMPLETE") is not True or quality.get("FULL_EXTRACTION_COMPLETE") is not True:
        raise ReconciliationError("EXTRACTION_STAGE_NOT_COMPLETE")
    if metadata.get("SESSION_AUTHORITY_KEY_N") != LIFECYCLE_SESSION_KEY_N:
        raise ReconciliationError("SESSION_AUTHORITY_METADATA_KEY_N_MISMATCH")
    if metadata.get("SESSION_AUTHORITY_DATASET_HASH") != SESSION_AUTHORITY_DATASET_HASH:
        raise ReconciliationError("SESSION_AUTHORITY_METADATA_DATASET_HASH_MISMATCH")
    if metadata.get("SESSION_AUTHORITY_FILE_SHA256") != SESSION_AUTHORITY_FILE_SHA256:
        raise ReconciliationError("SESSION_AUTHORITY_METADATA_FILE_SHA_MISMATCH")

    session_path = _stage_path(stage, SESSION_AUTHORITY_NAME)
    if not session_path.is_file():
        raise ReconciliationError("SESSION_AUTHORITY_FILE_MISSING")
    return {
        "context": context,
        "stage": stage,
        "input_manifest": context["input_manifest"],
        "request_manifest": request_manifest,
        "receipt_index": receipt_index,
        "execution": execution,
        "quality": quality,
        "metadata": metadata,
        "session_path": session_path,
        "REQUEST_MANIFEST_FILE_SHA256": sha256_file(_stage_path(stage, FULL_REQUEST_MANIFEST_NAME)),
        "REQUEST_RECEIPT_INDEX_FILE_SHA256": sha256_file(_stage_path(stage, FULL_RECEIPT_INDEX_NAME)),
    }


def _expected_status(classification: str) -> tuple[str, int | None]:
    if classification == "EXPECTED_BAR":
        return EXPECTED_BASIS, 1
    if classification == "NOT_EXPECTED_BAR":
        return NOT_EXPECTED_BASIS, 0
    if classification == "UNKNOWN":
        return UNKNOWN_BASIS, None
    raise ReconciliationError(f"INVALID_SESSION_CLASSIFICATION:{classification}")


def verify_session_authority_file_hash(path: Path) -> str:
    """Verify the physical session-authority artifact before accepting it."""

    if not path.is_file():
        raise ReconciliationError("SESSION_AUTHORITY_FILE_MISSING")
    actual = sha256_file(path)
    if actual != SESSION_AUTHORITY_FILE_SHA256:
        raise ReconciliationError("SESSION_AUTHORITY_FILE_SHA256_MISMATCH")
    return actual


def scan_session_authority(path: Path, context: dict[str, Any]) -> dict[str, Any]:
    """Recompute the Parquet hash and exact frozen-key coverage in one stream."""

    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise ReconciliationError("PYARROW_REQUIRED_FOR_RECONCILIATION") from exc
    parquet = pq.ParquetFile(path)
    expected_columns = [
        "symbol",
        "trade_date",
        "classification",
        "basis",
        "tradestatus",
        "request_id",
        "provider",
        "provider_runtime",
        "provider_distribution_version",
        "provider_code",
    ]
    if list(parquet.schema_arrow.names) != expected_columns:
        raise ReconciliationError("SESSION_AUTHORITY_SCHEMA_MISMATCH")

    expected_iter = iter(extraction._iter_expected_session_keys(context))
    previous_key: tuple[str, date] | None = None
    dataset_digest = hashlib.sha256()
    partition_digests = {name: hashlib.sha256() for name in sorted(ALLOWED_CLASSIFICATIONS)}
    counts: Counter[str] = Counter()
    unknown_basis_counts: Counter[str] = Counter()
    unknown_rows: list[dict[str, Any]] = []
    anchors: dict[tuple[str, date], dict[str, Any]] = {}
    actual_n = 0

    for batch in parquet.iter_batches(batch_size=100_000):
        for row in batch.to_pylist():
            symbol = str(row.get("symbol"))
            trade_date = parse_date(row.get("trade_date"))
            actual_key = (symbol, trade_date)
            if previous_key is not None and actual_key <= previous_key:
                raise ReconciliationError("SESSION_AUTHORITY_DUPLICATE_OR_UNSORTED_KEY")
            try:
                expected_key = next(expected_iter)
            except StopIteration as exc:
                raise ReconciliationError("SESSION_AUTHORITY_EXTRA_KEY") from exc
            if actual_key != expected_key:
                raise ReconciliationError(
                    f"SESSION_AUTHORITY_KEY_COVERAGE_MISMATCH:{actual_key}:{expected_key}"
                )
            previous_key = actual_key
            actual_n += 1

            classification = str(row.get("classification"))
            if classification not in ALLOWED_CLASSIFICATIONS:
                raise ReconciliationError("SESSION_AUTHORITY_INVALID_CLASSIFICATION")
            basis, required_status = _expected_status(classification)
            if row.get("basis") != basis:
                raise ReconciliationError("SESSION_AUTHORITY_BASIS_MISMATCH")
            status = row.get("tradestatus")
            if required_status is None:
                if status is not None:
                    raise ReconciliationError("SESSION_AUTHORITY_UNKNOWN_STATUS_NOT_NULL")
            elif status != required_status:
                raise ReconciliationError("SESSION_AUTHORITY_TRADESTATUS_MISMATCH")

            counts[classification] += 1
            if classification == "UNKNOWN":
                unknown_basis_counts[basis] += 1
            dataset_digest.update(canonical_json_bytes(row) + b"\n")
            partition_digests[classification].update(key_line(actual_key))
            if classification == "UNKNOWN":
                unknown_rows.append(
                    {
                        "symbol": symbol,
                        "trade_date": trade_date.isoformat(),
                        "basis": basis,
                        "request_id": str(row.get("request_id")),
                        "provider_code": str(row.get("provider_code")),
                    }
                )
            if actual_key in ANCHORS:
                anchors[actual_key] = {
                    "symbol": symbol,
                    "trade_date": trade_date.isoformat(),
                    "classification": classification,
                    "basis": basis,
                    "tradestatus": status,
                    "request_id": str(row.get("request_id")),
                    "provider_code": str(row.get("provider_code")),
                }

    try:
        next(expected_iter)
    except StopIteration:
        pass
    else:
        raise ReconciliationError("SESSION_AUTHORITY_MISSING_KEY")
    if actual_n != LIFECYCLE_SESSION_KEY_N:
        raise ReconciliationError("SESSION_AUTHORITY_KEY_N_MISMATCH")
    if counts != Counter(
        {"EXPECTED_BAR": EXPECTED_KEY_N, "NOT_EXPECTED_BAR": NOT_EXPECTED_KEY_N, "UNKNOWN": UNKNOWN_KEY_N}
    ):
        raise ReconciliationError(f"SESSION_AUTHORITY_CLASSIFICATION_COUNTS_MISMATCH:{dict(counts)}")
    if unknown_basis_counts != Counter({UNKNOWN_BASIS: UNKNOWN_KEY_N}) or len(unknown_rows) != UNKNOWN_KEY_N:
        raise ReconciliationError("UNKNOWN_BASIS_OR_ROWS_MISMATCH")
    if set(anchors) != set(ANCHORS):
        raise ReconciliationError("300546_ANCHOR_MISSING_FROM_SESSION_AUTHORITY")

    recomputed_dataset_hash = dataset_digest.hexdigest()
    if recomputed_dataset_hash != SESSION_AUTHORITY_DATASET_HASH:
        raise ReconciliationError("SESSION_AUTHORITY_DATASET_HASH_MISMATCH")
    file_sha = verify_session_authority_file_hash(path)
    return {
        "SESSION_AUTHORITY_KEY_N": actual_n,
        "EXPECTED_KEY_N": counts["EXPECTED_BAR"],
        "NOT_EXPECTED_KEY_N": counts["NOT_EXPECTED_BAR"],
        "UNKNOWN_KEY_N": counts["UNKNOWN"],
        "UNKNOWN_BASIS_COUNTS": dict(sorted(unknown_basis_counts.items())),
        "UNKNOWN_ROWS": unknown_rows,
        "UNKNOWN_KEYSET_HASH": partition_digests["UNKNOWN"].hexdigest(),
        "PARTITION_KEYSET_HASHES": {
            name: partition_digests[name].hexdigest()
            for name in sorted(ALLOWED_CLASSIFICATIONS)
        },
        "SESSION_AUTHORITY_DATASET_HASH": recomputed_dataset_hash,
        "SESSION_AUTHORITY_FILE_SHA256": file_sha,
        "SESSION_AUTHORITY_FILE_SIZE": path.stat().st_size,
        "ANCHORS": anchors,
    }


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _fetch_record_batches(cursor: Any, batch_size: int = 100_000) -> Iterable[Any]:
    try:
        reader = cursor.to_arrow_reader(batch_size)
    except AttributeError:
        reader = cursor.fetch_record_batch(batch_size)
    yield from reader


def _stream_key_query(connection: Any, query: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    previous: tuple[str, date] | None = None
    count = 0
    for batch in _fetch_record_batches(connection.execute(query)):
        symbols = batch.column("symbol").to_pylist()
        dates = batch.column("trade_date").to_pylist()
        for symbol, value in zip(symbols, dates):
            key = (str(symbol), parse_date(value))
            if previous is not None and key <= previous:
                raise ReconciliationError("CANONICAL_KEY_QUERY_NOT_STRICTLY_SORTED")
            digest.update(key_line(key))
            previous = key
            count += 1
    return count, digest.hexdigest()


def _fetch_key_query(connection: Any, query: str) -> list[tuple[str, date]]:
    rows: list[tuple[str, date]] = []
    for batch in _fetch_record_batches(connection.execute(query)):
        symbols = batch.column("symbol").to_pylist()
        dates = batch.column("trade_date").to_pylist()
        for symbol, value in zip(symbols, dates):
            rows.append((str(symbol), parse_date(value)))
            if len(rows) > MAX_MANIFEST_ROWS:
                raise ReconciliationError("MISMATCH_MANIFEST_TOO_LARGE")
    return rows


def reconcile_canonical(data_root: Path, session_path: Path) -> dict[str, Any]:
    try:
        import duckdb
    except Exception as exc:
        raise ReconciliationError("DUCKDB_REQUIRED_FOR_RECONCILIATION") from exc

    canonical_glob = (Path(data_root).resolve() / "curated" / "daily_bars" / "**" / "*.parquet").as_posix()
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE VIEW canonical_keys AS "
            f"SELECT CAST(symbol AS VARCHAR) AS symbol, CAST(trade_date AS DATE) AS trade_date "
            f"FROM read_parquet('{_sql_path(Path(canonical_glob))}')"
        )
        connection.execute(
            "CREATE VIEW authority_keys AS "
            f"SELECT CAST(symbol AS VARCHAR) AS symbol, CAST(trade_date AS DATE) AS trade_date, classification "
            f"FROM read_parquet('{_sql_path(session_path)}')"
        )
        connection.execute(
            "CREATE VIEW canonical_unique_keys AS "
            "SELECT symbol, trade_date FROM canonical_keys GROUP BY symbol, trade_date"
        )
        invalid_n = int(
            connection.execute(
                "SELECT COUNT(*) FROM canonical_keys WHERE symbol IS NULL OR trade_date IS NULL"
            ).fetchone()[0]
        )
        if invalid_n:
            raise ReconciliationError(f"CANONICAL_INVALID_KEY_N:{invalid_n}")

        canonical_row_n = int(connection.execute("SELECT COUNT(*) FROM canonical_keys").fetchone()[0])
        canonical_unique_n = int(connection.execute("SELECT COUNT(*) FROM canonical_unique_keys").fetchone()[0])
        duplicate_key_n = int(
            connection.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT symbol, trade_date FROM canonical_keys "
                "GROUP BY symbol, trade_date HAVING COUNT(*) > 1"
                ")"
            ).fetchone()[0]
        )
        canonical_key_n, canonical_key_hash = _stream_key_query(
            connection,
            "SELECT symbol, trade_date FROM canonical_unique_keys ORDER BY symbol, trade_date",
        )
        if canonical_key_n != canonical_unique_n:
            raise ReconciliationError("CANONICAL_KEYSET_COUNT_MISMATCH")

        missing_expected = _fetch_key_query(
            connection,
            "SELECT a.symbol, a.trade_date FROM authority_keys a "
            "LEFT JOIN canonical_unique_keys c USING (symbol, trade_date) "
            "WHERE a.classification = 'EXPECTED_BAR' AND c.symbol IS NULL "
            "ORDER BY a.symbol, a.trade_date",
        )
        on_not_expected = _fetch_key_query(
            connection,
            "SELECT c.symbol, c.trade_date FROM canonical_unique_keys c "
            "JOIN authority_keys a USING (symbol, trade_date) "
            "WHERE a.classification = 'NOT_EXPECTED_BAR' "
            "ORDER BY c.symbol, c.trade_date",
        )
        on_unknown = _fetch_key_query(
            connection,
            "SELECT c.symbol, c.trade_date FROM canonical_unique_keys c "
            "JOIN authority_keys a USING (symbol, trade_date) "
            "WHERE a.classification = 'UNKNOWN' "
            "ORDER BY c.symbol, c.trade_date",
        )
        outside = _fetch_key_query(
            connection,
            "SELECT c.symbol, c.trade_date FROM canonical_unique_keys c "
            "LEFT JOIN authority_keys a USING (symbol, trade_date) "
            "WHERE a.symbol IS NULL "
            "ORDER BY c.symbol, c.trade_date",
        )
        expected_present = int(
            connection.execute(
                "SELECT COUNT(*) FROM authority_keys a "
                "JOIN canonical_unique_keys c USING (symbol, trade_date) "
                "WHERE a.classification = 'EXPECTED_BAR'"
            ).fetchone()[0]
        )
        anchor_rows = connection.execute(
            "SELECT symbol, trade_date FROM canonical_unique_keys "
            "WHERE (symbol = '300546.SZ' AND trade_date = DATE '2016-09-29') "
            "   OR (symbol = '300546.SZ' AND trade_date = DATE '2016-10-10') "
            "ORDER BY symbol, trade_date"
        ).fetchall()
        anchor_present = {(str(symbol), parse_date(value)) for symbol, value in anchor_rows}
        return {
            "CANONICAL_ROW_N": canonical_row_n,
            "CANONICAL_UNIQUE_KEY_N": canonical_unique_n,
            "CANONICAL_DUPLICATE_KEY_N": duplicate_key_n,
            "CANONICAL_KEYSET_HASH": canonical_key_hash,
            "EXPECTED_PRESENT_KEY_N": expected_present,
            "MISSING_EXPECTED_KEYS": missing_expected,
            "CANONICAL_ON_NOT_EXPECTED_KEYS": on_not_expected,
            "CANONICAL_ON_UNKNOWN_KEYS": on_unknown,
            "CANONICAL_OUTSIDE_KEYS": outside,
            "ANCHOR_CANONICAL_PRESENT": anchor_present,
        }
    finally:
        connection.close()


def _key_manifest(kind: str, keys: list[tuple[str, date]]) -> dict[str, Any]:
    sorted_keys = sorted(keys)
    if len(set(sorted_keys)) != len(sorted_keys):
        raise ReconciliationError(f"{kind}_MANIFEST_DUPLICATE_KEY")
    return {
        "TASK": TASK,
        "MANIFEST_KIND": kind,
        "KEY_N": len(sorted_keys),
        "KEYSET_HASH": keyset_hash(sorted_keys),
        "KEYSET_SERIALIZATION": KEYSET_SERIALIZATION,
        "KEYS": [
            {"symbol": symbol, "trade_date": trade_date.isoformat()}
            for symbol, trade_date in sorted_keys
        ],
    }


def _unknown_manifest(rows: list[dict[str, Any]], unknown_hash: str) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (row["symbol"], row["trade_date"]))
    return {
        "TASK": TASK,
        "MANIFEST_KIND": "UNKNOWN_SESSION_KEYS",
        "KEY_N": len(ordered),
        "KEYSET_HASH": unknown_hash,
        "KEYSET_SERIALIZATION": KEYSET_SERIALIZATION,
        "MANIFEST_SERIALIZATION": MANIFEST_SERIALIZATION,
        "ROWS": ordered,
    }


def _input_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("INPUT_FILE_N") == right.get("INPUT_FILE_N")
        and left.get("INPUT_MANIFEST_HASH") == right.get("INPUT_MANIFEST_HASH")
        and left.get("FILES") == right.get("FILES")
    )


def unexpected_canonical_total(
    on_not_expected_n: int,
    on_unknown_n: int,
    outside_authority_n: int,
) -> int:
    """Return the additive total of canonical keys outside EXPECTED_BAR."""

    return on_not_expected_n + on_unknown_n + outside_authority_n


def _anchor_report(session: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for symbol, trade_date in ANCHORS:
        suffix = trade_date.strftime("%Y%m%d")
        authority = session["ANCHORS"][(symbol, trade_date)]
        present = (symbol, trade_date) in canonical["ANCHOR_CANONICAL_PRESENT"]
        if authority["classification"] != "EXPECTED_BAR":
            raise ReconciliationError(f"300546_ANCHOR_NOT_EXPECTED:{suffix}")
        result[f"300546_{suffix}_STATUS"] = authority["classification"]
        result[f"300546_{suffix}_SESSION_CLASSIFICATION"] = authority["classification"]
        result[f"300546_{suffix}_SESSION_BASIS"] = authority["basis"]
        result[f"300546_{suffix}_CANONICAL_PRESENT"] = present
    return result


def _report_markdown(report: dict[str, Any]) -> str:
    anchor_lines = []
    for suffix in ("20160929", "20161010"):
        anchor_lines.extend(
            [
                f"- 300546.SZ/{suffix}: status=`{report[f'300546_{suffix}_STATUS']}`, "
                f"basis=`{report[f'300546_{suffix}_SESSION_BASIS']}`, "
                f"canonical_present=`{str(report[f'300546_{suffix}_CANONICAL_PRESENT']).lower()}`",
            ]
        )
    safety = report["SAFETY"]
    lines = [
        f"# {TASK}",
        "",
        "Offline exact-key reconciliation between the frozen BaoStock session authority and the current canonical daily-bars dataset.",
        "",
        "## Dataset and grain",
        "",
        f"- Session authority grain: `(symbol, trade_date)`; rows=`{report['SESSION_AUTHORITY_KEY_N']}`; dataset hash=`{report['SESSION_AUTHORITY_DATASET_HASH']}`.",
        f"- Canonical grain: `(symbol, trade_date)`; rows=`{report['CANONICAL_ROW_N']}`; unique keys=`{report['CANONICAL_UNIQUE_KEY_N']}`; duplicate keys=`{report['CANONICAL_DUPLICATE_KEY_N']}`.",
        f"- Frozen daily input: files=`{report['INPUT_FILE_N']}`; manifest hash=`{report['INPUT_MANIFEST_HASH']}`.",
        "",
        "## Authority partition",
        "",
        f"- EXPECTED_KEY_N / NOT_EXPECTED_KEY_N / UNKNOWN_KEY_N: `{report['EXPECTED_KEY_N']}` / `{report['NOT_EXPECTED_KEY_N']}` / `{report['UNKNOWN_KEY_N']}`.",
        f"- UNKNOWN_BASIS_COUNTS: `{json.dumps(report['UNKNOWN_BASIS_COUNTS'], sort_keys=True)}`.",
        f"- UNKNOWN_KEYSET_HASH: `{report['UNKNOWN_KEYSET_HASH']}`.",
        "",
        "## Exact reconciliation",
        "",
        f"- EXPECTED_PRESENT_KEY_N / MISSING_EXPECTED_KEY_N: `{report['EXPECTED_PRESENT_KEY_N']}` / `{report['MISSING_EXPECTED_KEY_N']}`.",
        f"- CANONICAL_ON_NOT_EXPECTED_KEY_N: `{report['CANONICAL_ON_NOT_EXPECTED_KEY_N']}`.",
        f"- CANONICAL_ON_UNKNOWN_KEY_N: `{report['CANONICAL_ON_UNKNOWN_KEY_N']}`.",
        f"- CANONICAL_OUTSIDE_SESSION_AUTHORITY_KEY_N: `{report['CANONICAL_OUTSIDE_SESSION_AUTHORITY_KEY_N']}`.",
        f"- UNEXPECTED_CANONICAL_TOTAL_N (additive exact-key formula): `{report['UNEXPECTED_CANONICAL_TOTAL_N']}`.",
        f"- EXPECTED_KEY_N == CANONICAL_ROW_N: `{str(report['EXPECTED_KEY_N'] == report['CANONICAL_ROW_N']).lower()}`; this is not a completeness PASS because the exact key gate remains authoritative.",
        "",
        "## 300546 audit anchors",
        "",
        *anchor_lines,
        "",
        "These two keys are present in the current frozen post-repair canonical input and are classified EXPECTED_BAR by the independently extracted authority; no special-case mutation was applied by this reconciliation.",
        "",
        "## Gate and safety",
        "",
        f"- R3_COMPLETENESS_RECONCILIATION_PASS: `{str(report['R3_COMPLETENESS_RECONCILIATION_PASS']).lower()}` (UNKNOWN_KEY_N=39 is retained, not converted to PASS).",
    ]
    lines.extend(f"- {key}: `{str(value).lower() if isinstance(value, bool) else value}`" for key, value in safety.items())
    lines.extend(
        [
            "",
            "## Verification",
            "",
            f"- TEST_RESULT: `{report['TEST_RESULT']}`",
            f"- PY_COMPILE: `{report['PY_COMPILE']}`",
            f"- GIT_DIFF_CHECK: `{report['GIT_DIFF_CHECK']}`",
            "",
            "The session Parquet, provider receipts, and canonical Parquet remain outside Git in local staging/data-root paths.",
            "",
        ]
    )
    return "\n".join(lines)


def run_reconciliation(
    *,
    repo_root: Path = REPO_ROOT,
    data_root: Path = DATA_ROOT_DEFAULT,
    stage_root: Path | None = None,
    authority_loader: Callable[[Path, Path], dict[str, Any]] = extraction.load_and_verify_authority,
    test_result: str = "PENDING_TARGETED_TESTS",
    py_compile_result: str = "PENDING",
    diff_check_result: str = "PENDING",
) -> dict[str, Any]:
    if stage_root is None:
        stage_root = Path(data_root) / "staging" / STAGING_DIRNAME
    verified = _load_stage_authority(repo_root, data_root, stage_root, authority_loader=authority_loader)
    context = verified["context"]
    pre_input = verified["input_manifest"]
    session = scan_session_authority(verified["session_path"], context)
    canonical = reconcile_canonical(data_root, verified["session_path"])
    try:
        post_input = context["plan"].build_input_file_manifest(Path(data_root))
    except Exception as exc:
        raise ReconciliationError("DAILY_INPUT_POSTSCAN_RECOMPUTE_FAILED") from exc
    if not _input_equal(pre_input, post_input):
        raise ReconciliationError("DAILY_INPUT_DRIFT_DURING_RECONCILIATION")

    unknown_manifest = _unknown_manifest(session["UNKNOWN_ROWS"], session["UNKNOWN_KEYSET_HASH"])
    missing_manifest = _key_manifest("MISSING_EXPECTED_KEYS", canonical["MISSING_EXPECTED_KEYS"])
    not_expected_manifest = _key_manifest(
        "CANONICAL_ON_NOT_EXPECTED_KEYS", canonical["CANONICAL_ON_NOT_EXPECTED_KEYS"]
    )
    on_unknown_manifest = _key_manifest("CANONICAL_ON_UNKNOWN_KEYS", canonical["CANONICAL_ON_UNKNOWN_KEYS"])
    outside_manifest = _key_manifest("CANONICAL_OUTSIDE_SESSION_AUTHORITY_KEYS", canonical["CANONICAL_OUTSIDE_KEYS"])
    anchors = _anchor_report(session, canonical)

    report: dict[str, Any] = {
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_INDEPENDENT_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "REPORT_COMMIT_NOT_CLAIMED": True,
        "INPUT_FILE_N": pre_input["INPUT_FILE_N"],
        "INPUT_MANIFEST_HASH": pre_input["INPUT_MANIFEST_HASH"],
        "INPUT_MANIFEST_FILES_EQUAL_PRE_POST": _input_equal(pre_input, post_input),
        "FULL_REQUEST_N": FULL_REQUEST_N,
        "FULL_REQUEST_MANIFEST_HASH": FULL_REQUEST_MANIFEST_HASH,
        "REQUEST_RECEIPT_INDEX_HASH": REQUEST_RECEIPT_INDEX_HASH,
        "REQUEST_RECEIPT_INDEX_FILE_SHA256": verified["REQUEST_RECEIPT_INDEX_FILE_SHA256"],
        "SESSION_AUTHORITY_KEY_N": session["SESSION_AUTHORITY_KEY_N"],
        "SESSION_AUTHORITY_DATASET_HASH": session["SESSION_AUTHORITY_DATASET_HASH"],
        "SESSION_AUTHORITY_FILE_SHA256": session["SESSION_AUTHORITY_FILE_SHA256"],
        "EXPECTED_KEY_N": session["EXPECTED_KEY_N"],
        "NOT_EXPECTED_KEY_N": session["NOT_EXPECTED_KEY_N"],
        "UNKNOWN_KEY_N": session["UNKNOWN_KEY_N"],
        "UNKNOWN_BASIS_COUNTS": session["UNKNOWN_BASIS_COUNTS"],
        "UNKNOWN_KEYSET_HASH": session["UNKNOWN_KEYSET_HASH"],
        "PARTITION_KEYSET_HASHES": session["PARTITION_KEYSET_HASHES"],
        "CANONICAL_ROW_N": canonical["CANONICAL_ROW_N"],
        "CANONICAL_UNIQUE_KEY_N": canonical["CANONICAL_UNIQUE_KEY_N"],
        "CANONICAL_DUPLICATE_KEY_N": canonical["CANONICAL_DUPLICATE_KEY_N"],
        "CANONICAL_KEYSET_HASH": canonical["CANONICAL_KEYSET_HASH"],
        "EXPECTED_PRESENT_KEY_N": canonical["EXPECTED_PRESENT_KEY_N"],
        "MISSING_EXPECTED_KEY_N": len(canonical["MISSING_EXPECTED_KEYS"]),
        "MISSING_EXPECTED_KEYSET_HASH": missing_manifest["KEYSET_HASH"],
        "CANONICAL_ON_NOT_EXPECTED_KEY_N": len(canonical["CANONICAL_ON_NOT_EXPECTED_KEYS"]),
        "CANONICAL_ON_NOT_EXPECTED_KEYSET_HASH": not_expected_manifest["KEYSET_HASH"],
        "CANONICAL_ON_UNKNOWN_KEY_N": len(canonical["CANONICAL_ON_UNKNOWN_KEYS"]),
        "CANONICAL_ON_UNKNOWN_KEYSET_HASH": on_unknown_manifest["KEYSET_HASH"],
        "CANONICAL_OUTSIDE_SESSION_AUTHORITY_KEY_N": len(canonical["CANONICAL_OUTSIDE_KEYS"]),
        "CANONICAL_OUTSIDE_SESSION_AUTHORITY_KEYSET_HASH": outside_manifest["KEYSET_HASH"],
        "UNEXPECTED_CANONICAL_TOTAL_N": unexpected_canonical_total(
            len(canonical["CANONICAL_ON_NOT_EXPECTED_KEYS"]),
            len(canonical["CANONICAL_ON_UNKNOWN_KEYS"]),
            len(canonical["CANONICAL_OUTSIDE_KEYS"]),
        ),
        "UNKNOWN_MANIFEST": {"name": UNKNOWN_MANIFEST_NAME, "KEY_N": unknown_manifest["KEY_N"], "KEYSET_HASH": unknown_manifest["KEYSET_HASH"]},
        "MISMATCH_MANIFESTS": {
            "missing_expected_keys": {"name": MISSING_EXPECTED_NAME, "KEY_N": missing_manifest["KEY_N"], "KEYSET_HASH": missing_manifest["KEYSET_HASH"]},
            "unexpected_not_expected_keys": {"name": NOT_EXPECTED_NAME, "KEY_N": not_expected_manifest["KEY_N"], "KEYSET_HASH": not_expected_manifest["KEYSET_HASH"]},
            "canonical_on_unknown_keys": {"name": ON_UNKNOWN_NAME, "KEY_N": on_unknown_manifest["KEY_N"], "KEYSET_HASH": on_unknown_manifest["KEYSET_HASH"]},
            "canonical_outside_authority_keys": {"name": OUTSIDE_NAME, "KEY_N": outside_manifest["KEY_N"], "KEYSET_HASH": outside_manifest["KEYSET_HASH"]},
        },
        "R3_COMPLETENESS_RECONCILIATION_PASS": (
            len(canonical["MISSING_EXPECTED_KEYS"]) == 0
            and len(canonical["CANONICAL_ON_NOT_EXPECTED_KEYS"]) == 0
            and len(canonical["CANONICAL_OUTSIDE_KEYS"]) == 0
            and canonical["CANONICAL_DUPLICATE_KEY_N"] == 0
            and session["UNKNOWN_KEY_N"] == 0
        ),
        **anchors,
        "SAFETY": {
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "BAOSTOCK_EXECUTED": False,
            "TDX_EXECUTED": False,
            "CANONICAL_WRITE_EXECUTED": False,
            "CANONICAL_BYTES_MUTATED": False,
            "R4A9_CHECKPOINT_MUTATED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRECLOSE_COMPLETE": False,
            "PRODUCTION": False,
            "FORWARD": False,
            "TRADEPLAN": False,
        },
        "TEST_RESULT": test_result,
        "PY_COMPILE": py_compile_result,
        "GIT_DIFF_CHECK": diff_check_result,
    }

    # All data-root reads and every fail-closed gate complete before any repo write.
    write_repo_json(repo_root, UNKNOWN_MANIFEST_NAME, unknown_manifest)
    write_repo_json(repo_root, MISSING_EXPECTED_NAME, missing_manifest)
    write_repo_json(repo_root, NOT_EXPECTED_NAME, not_expected_manifest)
    write_repo_json(repo_root, ON_UNKNOWN_NAME, on_unknown_manifest)
    write_repo_json(repo_root, OUTSIDE_NAME, outside_manifest)
    write_repo_json(repo_root, REPORT_NAME, report)
    write_repo_text(repo_root, REPORT_MD_NAME, _report_markdown(report))
    return report


def reconcile_keysets(
    authority_rows: Iterable[dict[str, Any]],
    canonical_rows: Iterable[tuple[str, date | str]],
) -> dict[str, Any]:
    """Small pure helper used by tests for exact-key gate semantics."""

    authority: dict[tuple[str, date], str] = {}
    for row in authority_rows:
        key = (str(row["symbol"]), parse_date(row["trade_date"]))
        if key in authority:
            raise ReconciliationError("SESSION_AUTHORITY_DUPLICATE_KEY")
        authority[key] = str(row["classification"])
    canonical_list = [(str(symbol), parse_date(value)) for symbol, value in canonical_rows]
    counts = Counter(canonical_list)
    duplicate_key_n = sum(1 for count in counts.values() if count > 1)
    canonical_keys = set(counts)
    expected = {key for key, value in authority.items() if value == "EXPECTED_BAR"}
    not_expected = {key for key, value in authority.items() if value == "NOT_EXPECTED_BAR"}
    unknown = {key for key, value in authority.items() if value == "UNKNOWN"}
    return {
        "EXPECTED_KEY_N": len(expected),
        "NOT_EXPECTED_KEY_N": len(not_expected),
        "UNKNOWN_KEY_N": len(unknown),
        "CANONICAL_ROW_N": len(canonical_list),
        "CANONICAL_UNIQUE_KEY_N": len(canonical_keys),
        "CANONICAL_DUPLICATE_KEY_N": duplicate_key_n,
        "EXPECTED_PRESENT_KEY_N": len(expected & canonical_keys),
        "MISSING_EXPECTED_KEYS": sorted(expected - canonical_keys),
        "CANONICAL_ON_NOT_EXPECTED_KEYS": sorted(canonical_keys & not_expected),
        "CANONICAL_ON_UNKNOWN_KEYS": sorted(canonical_keys & unknown),
        "CANONICAL_OUTSIDE_KEYS": sorted(canonical_keys - set(authority)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path, default=None)
    parser.add_argument("--test-result", default="PENDING_TARGETED_TESTS")
    parser.add_argument("--py-compile-result", default="PENDING")
    parser.add_argument("--diff-check-result", default="PENDING")
    args = parser.parse_args(argv)
    try:
        report = run_reconciliation(
            repo_root=args.repo_root,
            data_root=args.data_root,
            stage_root=args.stage_root,
            test_result=args.test_result,
            py_compile_result=args.py_compile_result,
            diff_check_result=args.diff_check_result,
        )
    except ReconciliationError as exc:
        print(
            json.dumps(
                {
                    "TASK": TASK,
                    "AUTHOR_STATUS": "BLOCKED_FAIL_CLOSED",
                    "ERROR": str(exc),
                    "NETWORK_PROVIDER_DATA_FETCH": "NO",
                    "BAOSTOCK_EXECUTED": False,
                    "TDX_EXECUTED": False,
                    "CANONICAL_WRITE_EXECUTED": False,
                    "CANONICAL_BYTES_MUTATED": False,
                    "R4A9_RESUME_AUTHORIZED": False,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
