#!/usr/bin/env python3
"""Run the bounded R3 status0 secondary-authority pilot.

The primary session authority is frozen BaoStock.  This pilot uses only
independent evidence for the exact frozen 52-key scope: verified local
Tushare raw rows where available, then a bounded EastMoney public-kline
fallback for the remaining keys.  It never writes canonical data or the
frozen session-authority artifact.

The input gate is deliberately executed before the provider transport is
constructed or called.  Provider responses are written only below the
isolated data-root staging directory; repository outputs contain compact
manifests, hashes, and adjudication evidence rather than the raw corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import audit_r3_baostock_tradestatus0_consistency_v01 as prior  # noqa: E402
import research_r3_baostock_tradestatus0_semantic_refinement_v01 as refinement  # noqa: E402


TASK = "R3_STATUS0_SECONDARY_AUTHORITY_PILOT_V01"
BRANCH = "codex/r3-status0-secondary-authority-pilot-v01"
BASE_HEAD = "1309b3c4ddc7444245034bb11ed9e644026b064c"
REFINEMENT_COMMIT = BASE_HEAD
DATA_ROOT_DEFAULT = Path("/Users/luke808/AI/local-a-share-data-service-data")

PRIMARY_AUTHORITY_SOURCE = "baostock"
PRIMARY_AUTHORITY_RUNTIME = "baostock-0.9.3"
SECONDARY_PROVIDER = "tushare_pro"
SECONDARY_PROVIDER_API = "api.tushare.pro"
SECONDARY_PROVIDER_RUNTIME = "Tushare Pro API via stdlib urllib"
ALTERNATE_SECONDARY_PROVIDER = "eastmoney_public_kline"
ALTERNATE_SECONDARY_PROVIDER_API = "push2his.eastmoney.com"
ALTERNATE_SECONDARY_PROVIDER_RUNTIME = "EastMoney public kline API via stdlib urllib"
EASTMONEY_UT = "fa5fd1943c7b386f172d6893dbfba10b"

STAGING_DIRNAME = "r3_status0_secondary_authority_pilot_v01"
SESSION_STAGE_DIRNAME = "r3_full_session_completeness_authority_v01"
SESSION_AUTHORITY_NAME = "session_authority.parquet"

SCOPE_NAME = (
    "R3_BAOSTOCK_TRADESTATUS0_SEMANTIC_REFINEMENT_V01_SECONDARY_SCOPE.json"
)
PRIOR_REPORT_NAME = "R3_BAOSTOCK_TRADESTATUS0_SEMANTIC_REFINEMENT_V01.json"
PRIOR_CONTRADICTION_NAME = (
    "R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01_CONTRADICTIONS.json"
)

INPUT_MANIFEST_HASH = (
    "ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731"
)
SESSION_AUTHORITY_DATASET_HASH = (
    "0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47"
)
SCOPE_KEYSET_HASH = (
    "756258b487c409843a4accdf42a5a12b6ca5f87a117fb9ac35d55ef74ab60971"
)
SCOPE_MANIFEST_HASH = (
    "61a515d1ff56379a7fb5535fc2e6defb71ac20783fd7823a074a6f3f08b42d76"
)
SCOPE_KEY_N = 52
CONTRADICTION_KEY_N = 8
REPRESENTATIVE_KEY_N = 44

STATUS0_CLASSIFICATION = "NOT_EXPECTED_BAR"
CONTRADICTION = "STATUS0_CONTRADICTION"
INDETERMINATE = "STATUS0_NUMERIC_INDETERMINATE"

MAX_RETRY = 1
REQUEST_INTERVAL_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30.0

REPORT_NAME = f"{TASK}.json"
REPORT_MD_NAME = f"{TASK}.md"
REQUEST_MANIFEST_NAME = f"{TASK}_REQUEST_MANIFEST.json"
ADJUDICATION_MANIFEST_NAME = f"{TASK}_ADJUDICATION_MANIFEST.json"
EVIDENCE_INDEX_NAME = f"{TASK}_EVIDENCE_INDEX.json"
EMPTY_KEYSET_HASH = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)

LOCAL_TUSHARE_ROOT = Path("/Users/luke808/AI/V flash/data/raw/tushare/daily_bars")
PRELIMINARY_TUSHARE_RUN_ID = "run_20260829T074340Z"
PRELIMINARY_TUSHARE_REQUEST_MANIFEST_HASH = (
    "3da9fbb52ed346d5c58cbf56bc9041ac9ead098c4ecc4e66e20441c056788f03"
)
PRELIMINARY_TUSHARE_REQUEST_N = 96


class PilotError(RuntimeError):
    """Terminal fail-closed pilot error."""


def canonical_json_bytes(value: Any) -> bytes:
    return refinement.canonical_json_bytes(value)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_json_file_payload(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value) + b"\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PilotError(f"LOCAL_FILE_READ_FAILED:{path}") from exc
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise PilotError(f"LOCAL_JSON_READ_FAILED:{path}") from exc


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise PilotError(f"INVALID_DATE:{value!r}")


def key_of(row: dict[str, Any]) -> tuple[str, date]:
    return str(row["symbol"]), parse_date(row["trade_date"])


def keyset_hash(keys: Iterable[tuple[str, date]]) -> str:
    ordered = sorted(keys)
    if len(ordered) != len(set(ordered)):
        raise PilotError("KEYSET_DUPLICATE")
    if not ordered:
        return EMPTY_KEYSET_HASH
    return prior.keyset_hash(ordered)


def key_strings(keys: Iterable[tuple[str, date]]) -> list[str]:
    return [f"{symbol}\t{trade_date.isoformat()}" for symbol, trade_date in sorted(keys)]


def write_repo_json(repo_root: Path, name: str, value: Any) -> str:
    root = Path(repo_root).resolve()
    path = (root / "reports" / "implementation" / name).resolve()
    if root not in path.parents:
        raise PilotError("REPO_OUTPUT_PATH_ESCAPE")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(value) + b"\n"
    path.write_bytes(data)
    return sha256_bytes(data)


def write_repo_text(repo_root: Path, name: str, value: str) -> str:
    root = Path(repo_root).resolve()
    path = (root / "reports" / "implementation" / name).resolve()
    if root not in path.parents:
        raise PilotError("REPO_OUTPUT_PATH_ESCAPE")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def _contains_symlink(path: Path, stop: Path) -> bool:
    current = path
    stop_resolved = stop.resolve(strict=False)
    while True:
        if current.is_symlink():
            return True
        if current == stop_resolved:
            return False
        parent = current.parent
        if parent == current:
            return False
        current = parent


def require_isolated_stage_root(data_root: Path, stage_root: Path) -> Path:
    """Resolve and enforce the only permitted pilot staging root.

    This guard is intentionally called before any stage-root mkdir or write.
    It rejects symlink components, traversal aliases, curated paths, and all
    other data-root or external paths.
    """

    data_input = Path(data_root).expanduser()
    stage_input = Path(stage_root).expanduser()
    if not data_input.exists() or not data_input.is_dir():
        raise PilotError("DATA_ROOT_NOT_DIRECTORY")
    if _contains_symlink(data_input, data_input):
        raise PilotError("DATA_ROOT_SYMLINK_NOT_ALLOWED")
    staging_parent = data_input / "staging"
    if not staging_parent.exists() or not staging_parent.is_dir():
        raise PilotError("DATA_ROOT_STAGING_NOT_DIRECTORY")
    if _contains_symlink(staging_parent, data_input):
        raise PilotError("DATA_ROOT_STAGING_SYMLINK_NOT_ALLOWED")
    if _contains_symlink(stage_input, data_input):
        raise PilotError("PILOT_STAGE_SYMLINK_NOT_ALLOWED")
    try:
        data = data_input.resolve(strict=True)
        stage = stage_input.resolve(strict=False)
        expected = (data / "staging" / STAGING_DIRNAME).resolve(strict=False)
    except OSError as exc:
        raise PilotError("PILOT_STAGE_RESOLUTION_FAILED") from exc
    if stage != expected:
        raise PilotError(f"PILOT_STAGE_ROOT_MISMATCH:{stage}:{expected}")
    if stage.exists() and not stage.is_dir():
        raise PilotError("PILOT_STAGE_ROOT_NOT_DIRECTORY")
    return stage


def _stage_write_path(stage_root: Path, relative: str) -> Path:
    path = (stage_root / relative).resolve(strict=False)
    if stage_root not in path.parents:
        raise PilotError(f"PILOT_STAGE_PATH_ESCAPE:{relative}")
    return path


def _write_stage_json(stage_root: Path, relative: str, value: Any) -> str:
    path = _stage_write_path(stage_root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(value) + b"\n"
    path.write_bytes(data)
    return sha256_bytes(data)


def verify_scope(repo_root: Path) -> dict[str, Any]:
    path = Path(repo_root) / "reports" / "implementation" / SCOPE_NAME
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise PilotError("SECONDARY_SCOPE_NOT_OBJECT")
    body = dict(payload)
    declared_manifest = body.pop("MANIFEST_HASH", None)
    rows = body.get("ROWS")
    if declared_manifest != SCOPE_MANIFEST_HASH or sha256_json(body) != SCOPE_MANIFEST_HASH:
        raise PilotError("SECONDARY_SCOPE_MANIFEST_HASH_DRIFT")
    if payload.get("KEY_N") != SCOPE_KEY_N or payload.get("KEYSET_HASH") != SCOPE_KEYSET_HASH:
        raise PilotError("SECONDARY_SCOPE_KEYSET_AUTHORITY_DRIFT")
    if not isinstance(rows, list) or len(rows) != SCOPE_KEY_N:
        raise PilotError("SECONDARY_SCOPE_CARDINALITY_DRIFT")
    keys = [key_of(row) for row in rows]
    if len(keys) != len(set(keys)) or keyset_hash(keys) != SCOPE_KEYSET_HASH:
        raise PilotError("SECONDARY_SCOPE_KEYSET_RECOMPUTE_DRIFT")
    classification_counts = Counter(str(row.get("classification")) for row in rows)
    if classification_counts != Counter({INDETERMINATE: 44, CONTRADICTION: 8}):
        raise PilotError("SECONDARY_SCOPE_CLASSIFICATION_DRIFT")
    prior_contradiction = load_json(
        Path(repo_root) / "reports" / "implementation" / PRIOR_CONTRADICTION_NAME
    )
    contradiction_keys = {
        key_of(row) for row in (prior_contradiction.get("ROWS") or [])
    }
    scope_contradiction_keys = {
        key_of(row) for row in rows if row.get("classification") == CONTRADICTION
    }
    if contradiction_keys != scope_contradiction_keys or len(contradiction_keys) != 8:
        raise PilotError("SECONDARY_SCOPE_MISSING_OR_EXTRA_CONTRADICTION")
    return {
        "path": str(path),
        "body": body,
        "payload": payload,
        "rows": sorted(rows, key=lambda row: key_of(row)),
        "key_n": len(keys),
        "keyset_hash": keyset_hash(keys),
        "manifest_hash": declared_manifest,
        "contradiction_keys": contradiction_keys,
    }


def verify_input_gate(
    repo_root: Path,
    data_root: Path,
    session_stage_root: Path,
    scope: dict[str, Any],
) -> dict[str, Any]:
    """Verify all frozen local authority before any secondary transport call."""

    try:
        refinement._verify_prior_frozen_artifacts(repo_root)
        verified = refinement._load_and_verify_inputs(
            repo_root,
            data_root,
            session_stage_root,
        )
    except (
        refinement.RefinementError,
        prior.AuditError,
        prior.reconciliation.ReconciliationError,
    ) as exc:
        raise PilotError(f"FROZEN_INPUT_GATE:{exc}") from exc
    input_manifest = verified.get("input_manifest") or {}
    if (
        input_manifest.get("INPUT_FILE_N") != 2_580
        or input_manifest.get("INPUT_MANIFEST_HASH") != INPUT_MANIFEST_HASH
        or len(input_manifest.get("FILES", [])) != 2_580
    ):
        raise PilotError("DAILY_INPUT_MANIFEST_DRIFT")
    session = verified.get("session") or {}
    if (
        session.get("SESSION_AUTHORITY_KEY_N") != 10_897_229
        or session.get("SESSION_AUTHORITY_DATASET_HASH") != SESSION_AUTHORITY_DATASET_HASH
    ):
        raise PilotError("SESSION_AUTHORITY_HASH_DRIFT")
    # The frozen refinement commit must remain addressable locally.  This is
    # a local object check only; it does not contact a remote.
    try:
        import subprocess

        checked = subprocess.run(
            ["git", "cat-file", "-e", f"{REFINEMENT_COMMIT}^{{commit}}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise PilotError("BASE_COMMIT_CHECK_FAILED") from exc
    if checked.returncode != 0:
        raise PilotError("BASE_REFINEMENT_COMMIT_UNAVAILABLE")
    return verified


def _raw_receipt_path(stage_root: Path, request_id: str) -> Path:
    return _stage_write_path(stage_root, f"raw_receipts/{request_id}.json")


def _request_map(verified: dict[str, Any]) -> dict[str, dict[str, Any]]:
    context_requests = (verified.get("context") or {}).get("requests")
    if not isinstance(context_requests, list):
        raise PilotError("PRIMARY_REQUEST_MANIFEST_UNAVAILABLE")
    result = {str(row["request_id"]): row for row in context_requests}
    if len(result) != len(context_requests):
        raise PilotError("PRIMARY_REQUEST_MANIFEST_DUPLICATE")
    return result


def load_primary_target_evidence(
    verified: dict[str, Any],
    scope_rows: list[dict[str, Any]],
) -> dict[tuple[str, date], dict[str, Any]]:
    """Read and hash only primary receipts needed for the 52 frozen keys."""

    status0_by_request, _ = prior.collect_status0_keys(verified["session_path"])
    target_keys = {key_of(row) for row in scope_rows}
    authority_by_key: dict[tuple[str, date], dict[str, Any]] = {}
    for request_id, by_date in status0_by_request.items():
        for trade_date, authority in by_date.items():
            key = (str(authority["symbol"]), trade_date)
            if key in target_keys:
                if key in authority_by_key:
                    raise PilotError(f"PRIMARY_TARGET_DUPLICATE:{key}")
                authority_by_key[key] = authority
    if set(authority_by_key) != target_keys:
        missing = sorted(target_keys - set(authority_by_key))
        raise PilotError(f"PRIMARY_TARGET_SCOPE_MISSING:{missing[:3]}")

    request_map = _request_map(verified)
    receipt_index_rows = (verified.get("receipt_index") or {}).get("REQUESTS")
    if not isinstance(receipt_index_rows, list):
        raise PilotError("PRIMARY_RECEIPT_INDEX_UNAVAILABLE")
    index_by_id = {str(row["request_id"]): row for row in receipt_index_rows}
    grouped: dict[str, list[tuple[str, date]]] = defaultdict(list)
    for key, authority in authority_by_key.items():
        grouped[str(authority["request_id"])].append(key)

    result: dict[tuple[str, date], dict[str, Any]] = {}
    stage = verified["stage"]
    for request_id in sorted(grouped):
        request = request_map.get(request_id)
        index_row = index_by_id.get(request_id)
        if request is None or index_row is None:
            raise PilotError(f"PRIMARY_RECEIPT_REQUEST_MISSING:{request_id}")
        raw_path = (stage / prior.FULL_RAW_DIRNAME / f"{request_id}.json").resolve()
        normalized_path = (
            stage / prior.FULL_NORMALIZED_DIRNAME / f"{request_id}.json"
        ).resolve()
        if stage not in raw_path.parents or stage not in normalized_path.parents:
            raise PilotError(f"PRIMARY_RECEIPT_PATH_ESCAPE:{request_id}")
        if not raw_path.is_file() or not normalized_path.is_file():
            raise PilotError(f"PRIMARY_RECEIPT_PAIR_MISSING:{request_id}")
        raw_receipt = load_json(raw_path)
        normalized_receipt = load_json(normalized_path)
        if raw_receipt.get("request") != request or normalized_receipt.get("request") != request:
            raise PilotError(f"PRIMARY_RECEIPT_REQUEST_DRIFT:{request_id}")
        try:
            raw_payload_hash, normalized_payload_hash = prior.verify_receipt_payload_hashes(
                raw_receipt, normalized_receipt, index_row
            )
        except prior.AuditError as exc:
            raise PilotError(f"PRIMARY_RECEIPT_HASH_DRIFT:{request_id}") from exc
        if (
            raw_receipt.get("request_id") != request_id
            or normalized_receipt.get("request_id") != request_id
            or raw_receipt.get("status") != "COMPLETE"
            or normalized_receipt.get("status") != "COMPLETE"
            or str(raw_receipt.get("provider_error_code")) != "0"
            or str(normalized_receipt.get("provider_error_code")) != "0"
        ):
            raise PilotError(f"PRIMARY_RECEIPT_METADATA_DRIFT:{request_id}")
        raw_rows = (raw_receipt.get("raw_payload") or {}).get("rows")
        normalized_payload = normalized_receipt.get("normalized_payload") or {}
        cases = normalized_payload.get("CASES")
        if not isinstance(raw_rows, list) or not isinstance(cases, list):
            raise PilotError(f"PRIMARY_RECEIPT_PAYLOAD_MALFORMED:{request_id}")
        for key in sorted(grouped[request_id]):
            symbol, trade_date = key
            raw_candidates = []
            for raw_row in raw_rows:
                if not isinstance(raw_row, list) or not raw_row:
                    continue
                try:
                    raw_date = parse_date(raw_row[0])
                except PilotError:
                    continue
                if raw_date == trade_date:
                    raw_candidates.append(raw_row)
            if len(raw_candidates) != 1:
                raise PilotError(
                    f"PRIMARY_TARGET_ROW_CARDINALITY:{symbol}:{trade_date}:{len(raw_candidates)}"
                )
            raw_row = raw_candidates[0]
            if len(raw_row) < 10 or str(raw_row[1]) != str(request["bs_code"]):
                raise PilotError(f"PRIMARY_TARGET_CODE_DRIFT:{symbol}:{trade_date}")
            try:
                tradestatus = int(str(raw_row[9]))
            except (TypeError, ValueError) as exc:
                raise PilotError(f"PRIMARY_TARGET_STATUS_INVALID:{symbol}:{trade_date}") from exc
            if tradestatus != 0:
                raise PilotError(f"PRIMARY_TARGET_STATUS_DRIFT:{symbol}:{trade_date}")
            matching_cases = [
                case
                for case in cases
                if case.get("symbol") == symbol
                and parse_date(case.get("trade_date")) == trade_date
            ]
            if (
                len(matching_cases) != 1
                or matching_cases[0].get("classification") != STATUS0_CLASSIFICATION
                or matching_cases[0].get("basis") != "PROVIDER_TRADESTATUS_0"
                or matching_cases[0].get("provider_tradestatus") != 0
            ):
                raise PilotError(f"PRIMARY_TARGET_NORMALIZED_DRIFT:{symbol}:{trade_date}")
            result[key] = {
                "symbol": symbol,
                "trade_date": trade_date.isoformat(),
                "basis": "PROVIDER_TRADESTATUS_0",
                "classification": STATUS0_CLASSIFICATION,
                "tradestatus": 0,
                "request_id": request_id,
                "request_order": int(request["request_order"]),
                "provider_code": str(raw_row[1]),
                "open": str(raw_row[2]),
                "high": str(raw_row[3]),
                "low": str(raw_row[4]),
                "close": str(raw_row[5]),
                "volume_raw": raw_row[6],
                "amount_raw": raw_row[7],
                "preclose": str(raw_row[8]),
                "raw_receipt_file_sha256": sha256_file(raw_path),
                "normalized_receipt_file_sha256": sha256_file(normalized_path),
                "raw_payload_sha256": raw_payload_hash,
                "normalized_payload_sha256": normalized_payload_hash,
            }
    if set(result) != target_keys:
        raise PilotError("PRIMARY_TARGET_EVIDENCE_SCOPE_DRIFT")
    return result


def _symbol_from_code(value: Any) -> str:
    text = str(value).strip()
    if "." in text:
        code, suffix = text.split(".", 1)
        return f"{code.zfill(6)}.{suffix.upper()}"
    code = text.zfill(6)
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "1", "2", "3")):
        return f"{code}.SZ"
    return f"{code}.UNKNOWN"


def _positive(value: Any) -> bool:
    if value is None or str(value).strip() == "":
        return False
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return decimal.is_finite() and decimal > 0


def scan_local_independent_tushare(
    scope_rows: list[dict[str, Any]],
) -> dict[tuple[str, date], dict[str, Any]]:
    """Inventory existing independent Tushare daily raw rows, read-only."""

    target_keys = {key_of(row) for row in scope_rows}
    matched: dict[tuple[str, date], list[dict[str, Any]]] = defaultdict(list)
    root = LOCAL_TUSHARE_ROOT
    if not root.is_dir():
        return {}
    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise PilotError("PYARROW_REQUIRED_FOR_LOCAL_TUSHARE_INVENTORY") from exc
    for path in sorted(root.glob("*.parquet")):
        file_hash = sha256_file(path)
        try:
            parquet = pq.ParquetFile(path)
            required = {
                "code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "preclose",
                "volume",
                "amount",
                "trade_status",
                "provider",
                "provider_version",
            }
            if not required.issubset(set(parquet.schema_arrow.names)):
                continue
            for batch in parquet.iter_batches(
                columns=sorted(required), batch_size=100_000
            ):
                values = batch.to_pydict()
                for index, code in enumerate(values["code"]):
                    symbol = _symbol_from_code(code)
                    trade_date = parse_date(values["trade_date"][index])
                    key = (symbol, trade_date)
                    if key not in target_keys:
                        continue
                    row = {
                        "symbol": symbol,
                        "trade_date": trade_date.isoformat(),
                        "open": str(values["open"][index]),
                        "high": str(values["high"][index]),
                        "low": str(values["low"][index]),
                        "close": str(values["close"][index]),
                        "preclose": str(values["preclose"][index]),
                        "volume": str(values["volume"][index]),
                        "amount": str(values["amount"][index]),
                        "trade_status": values["trade_status"][index],
                        "provider": str(values["provider"][index]),
                        "provider_version": str(values["provider_version"][index]),
                        "file": str(path),
                        "file_sha256": file_hash,
                    }
                    matched[key].append(row)
        except Exception as exc:
            raise PilotError(f"LOCAL_TUSHARE_INVENTORY_FAILED:{path}") from exc
    result: dict[tuple[str, date], dict[str, Any]] = {}
    for key, rows in matched.items():
        if len(rows) != 1:
            continue
        row = rows[0]
        if (
            row.get("provider", "").upper() != "TUSHARE"
            or row.get("trade_status") is not True
            or not (_positive(row.get("volume")) or _positive(row.get("amount")))
        ):
            continue
        row["evidence_id"] = f"LOCAL-TUSHARE-{key[0]}-{key[1].isoformat()}"
        row["evidence_level"] = "LOCAL_INDEPENDENT_RAW_OBSERVATION"
        result[key] = row
    return result


def canonical_rows_for_scope(
    data_root: Path,
    scope_rows: list[dict[str, Any]],
) -> dict[tuple[str, date], dict[str, Any]]:
    target_keys = {key_of(row) for row in scope_rows}
    try:
        rows = prior.canonical_lookup(data_root, target_keys)
    except prior.AuditError as exc:
        raise PilotError(f"CANONICAL_SCOPE_LOOKUP_FAILED:{exc}") from exc
    for key, row in rows.items():
        partition = (
            Path(data_root)
            / "curated"
            / "daily_bars"
            / f"trade_date={row['trade_date']}"
            / "part-merged.parquet"
        )
        row["partition_file"] = str(partition)
        if not partition.is_file():
            raise PilotError(f"CANONICAL_PARTITION_FILE_MISSING:{key}")
    return rows


def load_preliminary_tushare_attempt(stage_root: Path) -> dict[str, Any] | None:
    """Reuse the already-recorded failed Tushare attempt without refetching."""

    run_root = _stage_write_path(stage_root, PRELIMINARY_TUSHARE_RUN_ID)
    manifest_path = run_root / "request_manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise PilotError("PRELIMINARY_TUSHARE_MANIFEST_NOT_OBJECT")
    body = dict(manifest)
    declared_hash = body.pop("REQUEST_MANIFEST_HASH", None)
    if (
        declared_hash != PRELIMINARY_TUSHARE_REQUEST_MANIFEST_HASH
        or sha256_json(body) != PRELIMINARY_TUSHARE_REQUEST_MANIFEST_HASH
        or body.get("SECONDARY_PROVIDER") != SECONDARY_PROVIDER
        or body.get("NETWORK_KEY_N") != 48
        or len(body.get("REQUESTS") or []) != PRELIMINARY_TUSHARE_REQUEST_N
    ):
        raise PilotError("PRELIMINARY_TUSHARE_ATTEMPT_DRIFT")
    receipt_dir = run_root / "raw_receipts"
    receipt_paths = sorted(receipt_dir.glob("R3S0SAP-*.json"))
    if len(receipt_paths) != PRELIMINARY_TUSHARE_REQUEST_N:
        raise PilotError("PRELIMINARY_TUSHARE_RECEIPT_COUNT_DRIFT")
    failed = 0
    request_ids = set()
    for path in receipt_paths:
        receipt = load_json(path)
        if not isinstance(receipt, dict):
            raise PilotError(f"PRELIMINARY_TUSHARE_RECEIPT_NOT_OBJECT:{path.name}")
        request_ids.add(str(receipt.get("request_id") or (receipt.get("request") or {}).get("request_id")))
        if (
            receipt.get("status") != "INVALID_PROVIDER_RESPONSE"
            or receipt.get("provider_error_code") != "API_CODE_40101"
        ):
            raise PilotError(f"PRELIMINARY_TUSHARE_RECEIPT_UNEXPECTED:{path.name}")
        failed += 1
    if len(request_ids) != PRELIMINARY_TUSHARE_REQUEST_N:
        raise PilotError("PRELIMINARY_TUSHARE_RECEIPT_ID_DRIFT")
    return {
        "provider": SECONDARY_PROVIDER,
        "run_id": PRELIMINARY_TUSHARE_RUN_ID,
        "request_manifest_hash": PRELIMINARY_TUSHARE_REQUEST_MANIFEST_HASH,
        "request_n": PRELIMINARY_TUSHARE_REQUEST_N,
        "failed_request_n": failed,
        "failed_key_n": 48,
        "status": "REUSED_FAILED_ATTEMPT",
    }


def _date_text(value: date) -> str:
    return value.strftime("%Y%m%d")


def build_request_manifest(
    scope_rows: list[dict[str, Any]],
    local_evidence: dict[tuple[str, date], dict[str, Any]],
    *,
    provider: str = ALTERNATE_SECONDARY_PROVIDER,
    prior_provider_attempt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered_scope = sorted(scope_rows, key=key_of)
    network_rows = [row for row in ordered_scope if key_of(row) not in local_evidence]
    requests: list[dict[str, Any]] = []
    request_order = 0
    for row in network_rows:
        symbol, trade_date = key_of(row)
        if provider == ALTERNATE_SECONDARY_PROVIDER:
            request_order += 1
            endpoint = "eastmoney_kline"
            secid = f"{'1' if symbol.endswith('.SH') else '0'}.{symbol.split('.')[0]}"
            requests.append(
                {
                    "request_id": f"R3S0SAP-EM-{request_order:04d}",
                    "request_order": request_order,
                    "symbol": symbol,
                    "trade_date": trade_date.isoformat(),
                    "endpoint": endpoint,
                    "api_name": endpoint,
                    "params": {
                        "secid": secid,
                        "klt": "101",
                        "fqt": "0",
                        "beg": trade_date.strftime("%Y%m%d"),
                        "end": trade_date.strftime("%Y%m%d"),
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                        "ut": EASTMONEY_UT,
                    },
                    "fields": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "provider": ALTERNATE_SECONDARY_PROVIDER,
                    "provider_runtime": ALTERNATE_SECONDARY_PROVIDER_RUNTIME,
                }
            )
            continue
        if provider != SECONDARY_PROVIDER:
            raise PilotError(f"UNSUPPORTED_SECONDARY_PROVIDER:{provider}")
        for endpoint, fields in (
            (
                "daily",
                "ts_code,trade_date,open,high,low,close,pre_close,vol,amount,pct_chg",
            ),
            ("suspend_d", "ts_code,trade_date,suspend_type,suspend_timing"),
        ):
            request_order += 1
            requests.append(
                {
                    "request_id": f"R3S0SAP-{request_order:04d}",
                    "request_order": request_order,
                    "symbol": symbol,
                    "trade_date": trade_date.isoformat(),
                    "endpoint": endpoint,
                    "api_name": endpoint,
                    "params": {
                        "ts_code": symbol,
                        "start_date": _date_text(trade_date),
                        "end_date": _date_text(trade_date),
                    },
                    "fields": fields,
                    "provider": SECONDARY_PROVIDER,
                    "provider_runtime": SECONDARY_PROVIDER_RUNTIME,
                }
            )
    body = {
        "TASK": TASK,
        "BASE_REFINEMENT_COMMIT": REFINEMENT_COMMIT,
        "PRIMARY_AUTHORITY_SOURCE": PRIMARY_AUTHORITY_SOURCE,
        "PRIMARY_AUTHORITY_RUNTIME": PRIMARY_AUTHORITY_RUNTIME,
        "SECONDARY_PROVIDER": provider,
        "SECONDARY_PROVIDER_API": (
            ALTERNATE_SECONDARY_PROVIDER_API
            if provider == ALTERNATE_SECONDARY_PROVIDER
            else SECONDARY_PROVIDER_API
        ),
        "SCOPE_KEY_N": SCOPE_KEY_N,
        "SCOPE_KEYSET_HASH": SCOPE_KEYSET_HASH,
        "SCOPE_MANIFEST_HASH": SCOPE_MANIFEST_HASH,
        "LOCAL_REUSE_KEYSET_HASH": keyset_hash(local_evidence),
        "LOCAL_REUSE_KEY_N": len(local_evidence),
        "NETWORK_KEY_N": len(network_rows),
        "PRIOR_PROVIDER_ATTEMPT": prior_provider_attempt,
        "KEY_PLAN": [
            {
                "symbol": row["symbol"],
                "trade_date": row["trade_date"],
                "classification": row["classification"],
                "execution": (
                    "LOCAL_REUSE"
                    if key_of(row) in local_evidence
                    else (
                        "EASTMONEY_KLINE"
                        if provider == ALTERNATE_SECONDARY_PROVIDER
                        else "TUSHARE_DAILY_AND_SUSPEND_D"
                    )
                ),
            }
            for row in ordered_scope
        ],
        "REQUESTS": requests,
        "SERIALIZATION": (
            "canonical JSON UTF-8, sorted keys, compact separators; KEY_PLAN and "
            "REQUESTS sorted by scope key then endpoint request_order; credentials are never persisted"
        ),
    }
    return {**body, "REQUEST_MANIFEST_HASH": sha256_json(body)}


@dataclass(frozen=True)
class ProviderResponse:
    payload: Any
    raw_bytes: bytes
    http_status: int


class ProviderFailure(RuntimeError):
    """A transport or API-level provider failure."""


Transport = Callable[[dict[str, Any]], ProviderResponse]


def _redact(value: str) -> str:
    token = os.environ.get("TUSHARE_TOKEN", "")
    return value.replace(token, "<redacted>") if token else value


def make_tushare_transport() -> Transport:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise ProviderFailure("TUSHARE_TOKEN_NOT_CONFIGURED")

    def transport(spec: dict[str, Any]) -> ProviderResponse:
        request_body = {
            "api_name": spec["api_name"],
            "token": token,
            "params": spec["params"],
            "fields": spec["fields"],
        }
        data = canonical_json_bytes(request_body)
        request = urllib.request.Request(
            f"https://{SECONDARY_PROVIDER_API}",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            ) as response:
                raw_bytes = response.read()
                status = int(response.status)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderFailure(_redact(f"{type(exc).__name__}:{exc}")) from exc
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderFailure(f"MALFORMED_JSON:{type(exc).__name__}") from exc
        return ProviderResponse(payload=payload, raw_bytes=raw_bytes, http_status=status)

    return transport


def make_eastmoney_transport() -> Transport:
    """Create the unauthenticated, exact-date EastMoney kline transport."""

    def transport(spec: dict[str, Any]) -> ProviderResponse:
        from urllib.parse import urlencode

        query = urlencode(spec["params"])
        request = urllib.request.Request(
            f"https://{ALTERNATE_SECONDARY_PROVIDER_API}/api/qt/stock/kline/get?{query}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            ) as response:
                raw_bytes = response.read()
                status = int(response.status)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderFailure(_redact(f"{type(exc).__name__}:{exc}")) from exc
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderFailure(f"MALFORMED_JSON:{type(exc).__name__}") from exc
        return ProviderResponse(payload=payload, raw_bytes=raw_bytes, http_status=status)

    return transport


def _response_rows(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(payload, dict):
        return [], "RESPONSE_NOT_OBJECT"
    if payload.get("code") not in (0, "0"):
        return [], f"API_CODE_{payload.get('code')}"
    data = payload.get("data")
    if data is None:
        return [], None
    if not isinstance(data, dict):
        return [], "DATA_NOT_OBJECT"
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, list) or not isinstance(items, list):
        return [], "DATA_FIELDS_ITEMS_MALFORMED"
    if not all(isinstance(field, str) for field in fields):
        return [], "DATA_FIELDS_INVALID"
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            return [], "DATA_ITEM_WIDTH_INVALID"
        rows.append(dict(zip(fields, item)))
    return rows, None


def _valid_provider_date(value: Any, target: date) -> bool:
    try:
        return parse_date(value) == target
    except PilotError:
        return False


def normalize_secondary_observation(
    spec: dict[str, Any],
    response: ProviderResponse,
) -> dict[str, Any]:
    target = parse_date(spec["trade_date"])
    if spec["endpoint"] == "eastmoney_kline":
        payload = response.payload
        if not isinstance(payload, dict):
            return {
                "request_id": spec["request_id"],
                "endpoint": spec["endpoint"],
                "status": "INVALID_PROVIDER_RESPONSE",
                "provider_error_code": "RESPONSE_NOT_OBJECT",
                "rows": [],
                "raw_payload_sha256": sha256_bytes(response.raw_bytes),
            }
        data = payload.get("data")
        if data is None:
            return {
                "request_id": spec["request_id"],
                "endpoint": spec["endpoint"],
                "status": "COMPLETE",
                "provider_error_code": "0",
                "rows": [],
                "raw_payload_sha256": sha256_bytes(response.raw_bytes),
            }
        if not isinstance(data, dict) or str(data.get("code", "")) != spec["symbol"].split(".")[0]:
            return {
                "request_id": spec["request_id"],
                "endpoint": spec["endpoint"],
                "status": "INVALID_PROVIDER_ROW",
                "provider_error_code": "SYMBOL_MISMATCH",
                "rows": [],
                "raw_payload_sha256": sha256_bytes(response.raw_bytes),
            }
        klines = data.get("klines")
        if klines is None:
            klines = []
        if not isinstance(klines, list):
            return {
                "request_id": spec["request_id"],
                "endpoint": spec["endpoint"],
                "status": "INVALID_PROVIDER_RESPONSE",
                "provider_error_code": "KLINES_NOT_LIST",
                "rows": [],
                "raw_payload_sha256": sha256_bytes(response.raw_bytes),
            }
        normalized_rows: list[dict[str, Any]] = []
        for kline in klines:
            if not isinstance(kline, str):
                return {
                    "request_id": spec["request_id"],
                    "endpoint": spec["endpoint"],
                    "status": "INVALID_PROVIDER_ROW",
                    "provider_error_code": "KLINE_NOT_TEXT",
                    "rows": [],
                    "raw_payload_sha256": sha256_bytes(response.raw_bytes),
                }
            fields = [item.strip() for item in kline.split(",")]
            if len(fields) < 7:
                return {
                    "request_id": spec["request_id"],
                    "endpoint": spec["endpoint"],
                    "status": "INVALID_PROVIDER_ROW",
                    "provider_error_code": "KLINE_FIELD_COUNT_INVALID",
                    "rows": [],
                    "raw_payload_sha256": sha256_bytes(response.raw_bytes),
                }
            if not _valid_provider_date(fields[0], target):
                return {
                    "request_id": spec["request_id"],
                    "endpoint": spec["endpoint"],
                    "status": "INVALID_PROVIDER_ROW",
                    "provider_error_code": "DATE_MISMATCH",
                    "rows": [],
                    "raw_payload_sha256": sha256_bytes(response.raw_bytes),
                }
            try:
                numeric = [Decimal(fields[index]) for index in range(1, 7)]
            except (InvalidOperation, ValueError, IndexError):
                return {
                    "request_id": spec["request_id"],
                    "endpoint": spec["endpoint"],
                    "status": "INVALID_PROVIDER_ROW",
                    "provider_error_code": "KLINE_NUMERIC_INVALID",
                    "rows": [],
                    "raw_payload_sha256": sha256_bytes(response.raw_bytes),
                }
            if any(not value.is_finite() or value < 0 for value in numeric[0:6]):
                return {
                    "request_id": spec["request_id"],
                    "endpoint": spec["endpoint"],
                    "status": "INVALID_PROVIDER_ROW",
                    "provider_error_code": "KLINE_NUMERIC_DOMAIN",
                    "rows": [],
                    "raw_payload_sha256": sha256_bytes(response.raw_bytes),
                }
            normalized_rows.append(
                {
                    "ts_code": data["code"],
                    "trade_date": target.isoformat(),
                    "open": fields[1],
                    "close": fields[2],
                    "high": fields[3],
                    "low": fields[4],
                    "vol": fields[5],
                    "amount": fields[6],
                    "raw_row": fields,
                }
            )
        if len(normalized_rows) > 1:
            return {
                "request_id": spec["request_id"],
                "endpoint": spec["endpoint"],
                "status": "DUPLICATE_PROVIDER_ROW",
                "provider_error_code": "DUPLICATE_TARGET_DATE",
                "rows": normalized_rows,
                "raw_payload_sha256": sha256_bytes(response.raw_bytes),
            }
        return {
            "request_id": spec["request_id"],
            "endpoint": spec["endpoint"],
            "status": "COMPLETE",
            "provider_error_code": "0",
            "rows": normalized_rows,
            "raw_payload_sha256": sha256_bytes(response.raw_bytes),
        }
    rows, error = _response_rows(response.payload)
    if error is not None:
        return {
            "request_id": spec["request_id"],
            "endpoint": spec["endpoint"],
            "status": (
                "PROVIDER_FAILED"
                if error.startswith("API_CODE_")
                else "INVALID_PROVIDER_RESPONSE"
            ),
            "provider_error_code": error,
            "rows": [],
            "raw_payload_sha256": sha256_bytes(response.raw_bytes),
        }
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        row_code = str(row.get("ts_code", ""))
        if row_code.upper() != str(spec["symbol"]).upper():
            return {
                "request_id": spec["request_id"],
                "endpoint": spec["endpoint"],
                "status": "INVALID_PROVIDER_ROW",
                "provider_error_code": "SYMBOL_MISMATCH",
                "rows": [],
                "raw_payload_sha256": sha256_bytes(response.raw_bytes),
            }
        if not _valid_provider_date(row.get("trade_date"), target):
            return {
                "request_id": spec["request_id"],
                "endpoint": spec["endpoint"],
                "status": "INVALID_PROVIDER_ROW",
                "provider_error_code": "DATE_MISMATCH",
                "rows": [],
                "raw_payload_sha256": sha256_bytes(response.raw_bytes),
            }
        if spec["endpoint"] == "daily":
            numeric_fields = (
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "vol",
                "amount",
            )
            parsed: dict[str, Any] = {}
            for field in numeric_fields:
                value = row.get(field)
                if value is None or str(value).strip() == "":
                    return {
                        "request_id": spec["request_id"],
                        "endpoint": spec["endpoint"],
                        "status": "INVALID_PROVIDER_ROW",
                        "provider_error_code": f"NUMERIC_BLANK:{field}",
                        "rows": [],
                        "raw_payload_sha256": sha256_bytes(response.raw_bytes),
                    }
                try:
                    decimal = Decimal(str(value))
                except (InvalidOperation, TypeError, ValueError):
                    return {
                        "request_id": spec["request_id"],
                        "endpoint": spec["endpoint"],
                        "status": "INVALID_PROVIDER_ROW",
                        "provider_error_code": f"NUMERIC_INVALID:{field}",
                        "rows": [],
                        "raw_payload_sha256": sha256_bytes(response.raw_bytes),
                    }
                if not decimal.is_finite() or decimal < 0:
                    return {
                        "request_id": spec["request_id"],
                        "endpoint": spec["endpoint"],
                        "status": "INVALID_PROVIDER_ROW",
                        "provider_error_code": f"NUMERIC_DOMAIN:{field}",
                        "rows": [],
                        "raw_payload_sha256": sha256_bytes(response.raw_bytes),
                    }
                parsed[field] = str(value)
            normalized_rows.append(
                {
                    "ts_code": row_code,
                    "trade_date": target.isoformat(),
                    **parsed,
                    "pct_chg": row.get("pct_chg"),
                    "raw_row": row,
                }
            )
        else:
            suspend_type = row.get("suspend_type")
            if suspend_type is None or str(suspend_type).strip() == "":
                return {
                    "request_id": spec["request_id"],
                    "endpoint": spec["endpoint"],
                    "status": "INVALID_PROVIDER_ROW",
                    "provider_error_code": "SUSPEND_TYPE_INVALID",
                    "rows": [],
                    "raw_payload_sha256": sha256_bytes(response.raw_bytes),
                }
            normalized_rows.append(
                {
                    "ts_code": row_code,
                    "trade_date": target.isoformat(),
                    "suspend_type": str(suspend_type),
                    "suspend_timing": row.get("suspend_timing"),
                    "raw_row": row,
                }
            )
    if len(normalized_rows) > 1:
        return {
            "request_id": spec["request_id"],
            "endpoint": spec["endpoint"],
            "status": "DUPLICATE_PROVIDER_ROW",
            "provider_error_code": "DUPLICATE_TARGET_DATE",
            "rows": normalized_rows,
            "raw_payload_sha256": sha256_bytes(response.raw_bytes),
        }
    return {
        "request_id": spec["request_id"],
        "endpoint": spec["endpoint"],
        "status": "COMPLETE",
        "provider_error_code": "0",
        "rows": normalized_rows,
        "raw_payload_sha256": sha256_bytes(response.raw_bytes),
    }


def execute_request(
    spec: dict[str, Any],
    transport: Transport,
    stage_root: Path,
    *,
    max_retry: int = MAX_RETRY,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> tuple[dict[str, Any], int]:
    last_failure: str | None = None
    for attempt in range(1, max_retry + 2):
        if attempt > 1:
            sleep_fn(REQUEST_INTERVAL_SECONDS)
        try:
            response = transport(spec)
            observation = normalize_secondary_observation(spec, response)
            receipt = {
                "TASK": TASK,
                "request": spec,
                "attempt_n": attempt,
                "completed_at": now_fn().isoformat(),
                "http_status": response.http_status,
                "status": observation["status"],
                "provider_error_code": observation["provider_error_code"],
                "raw_payload_sha256": observation["raw_payload_sha256"],
                "provider_payload": response.payload,
            }
            receipt_file_sha = _write_stage_json(
                stage_root,
                f"raw_receipts/{spec['request_id']}.json",
                receipt,
            )
            observation["attempt_n"] = attempt
            observation["receipt_file_sha256"] = receipt_file_sha
            return observation, attempt
        except ProviderFailure as exc:
            last_failure = str(exc)
        except Exception as exc:  # pragma: no cover - defensive terminal path
            last_failure = f"{type(exc).__name__}:{_redact(str(exc))}"
    receipt = {
        "TASK": TASK,
        "request": spec,
        "attempt_n": max_retry + 1,
        "completed_at": now_fn().isoformat(),
        "status": "PROVIDER_FAILED",
        "provider_error_code": last_failure or "PROVIDER_FAILED",
        "provider_payload": None,
        "raw_payload_sha256": None,
    }
    receipt_file_sha = _write_stage_json(
        stage_root,
        f"raw_receipts/{spec['request_id']}.json",
        receipt,
    )
    return (
        {
            "request_id": spec["request_id"],
            "endpoint": spec["endpoint"],
            "status": "PROVIDER_FAILED",
            "provider_error_code": last_failure or "PROVIDER_FAILED",
            "rows": [],
            "attempt_n": max_retry + 1,
            "receipt_file_sha256": receipt_file_sha,
            "raw_payload_sha256": None,
        },
        max_retry + 1,
    )


def verify_network_receipts(
    run_root: Path,
    request_specs: list[dict[str, Any]],
    observations: dict[tuple[str, date], dict[str, dict[str, Any]]],
) -> None:
    """Re-read every bounded network receipt and verify its durable hashes.

    This is deliberately provider-free.  The check binds the in-memory
    observation used for adjudication to the receipt written under the
    isolated run root and rejects missing, mismatched, or tampered receipts.
    """

    for spec in request_specs:
        request_id = str(spec["request_id"])
        path = _raw_receipt_path(run_root, request_id)
        if not path.is_file():
            raise PilotError(f"SECONDARY_RECEIPT_MISSING:{request_id}")
        receipt = load_json(path)
        if not isinstance(receipt, dict) or receipt.get("request") != spec:
            raise PilotError(f"SECONDARY_RECEIPT_REQUEST_DRIFT:{request_id}")
        file_hash = sha256_file(path)
        key = (str(spec["symbol"]), parse_date(spec["trade_date"]))
        observed = observations.get(key, {}).get(str(spec["endpoint"]))
        if observed is None:
            raise PilotError(f"SECONDARY_OBSERVATION_MISSING:{request_id}")
        if observed.get("receipt_file_sha256") != file_hash:
            raise PilotError(f"SECONDARY_RECEIPT_FILE_HASH_DRIFT:{request_id}")
        if receipt.get("status") != observed.get("status"):
            raise PilotError(f"SECONDARY_RECEIPT_STATUS_DRIFT:{request_id}")
        if receipt.get("provider_error_code") != observed.get("provider_error_code"):
            raise PilotError(f"SECONDARY_RECEIPT_ERROR_DRIFT:{request_id}")
        if receipt.get("raw_payload_sha256") != observed.get("raw_payload_sha256"):
            raise PilotError(f"SECONDARY_RECEIPT_PAYLOAD_HASH_DRIFT:{request_id}")
        if receipt.get("attempt_n") != observed.get("attempt_n"):
            raise PilotError(f"SECONDARY_RECEIPT_ATTEMPT_DRIFT:{request_id}")


def _canonical_source_is_same_as_tushare(canonical_row: dict[str, Any] | None) -> bool:
    if canonical_row is None:
        return False
    return str(canonical_row.get("source", "")).strip().lower() in {
        "tushare",
        "tushare_pro",
        "tushare_daily",
    }


def independence_record(
    secondary_source: str,
    canonical_row: dict[str, Any] | None,
) -> dict[str, bool]:
    """Make same-source limitations explicit for every adjudication row."""

    same_as_primary = secondary_source.lower().startswith(PRIMARY_AUTHORITY_SOURCE)
    same_as_canonical = _canonical_source_is_same_as_tushare(canonical_row)
    return {
        "same_source_as_primary": same_as_primary,
        "same_source_as_canonical": same_as_canonical,
        "independent_of_baostock_primary": not same_as_primary,
        "independent_of_canonical_source": not same_as_canonical,
        "same_source_is_not_independent": bool(same_as_primary or same_as_canonical),
    }


def _canonical_compact(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"present": False}
    return {
        "present": True,
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "volume": row.get("volume"),
        "amount": row.get("amount"),
        "source": row.get("source"),
        "data_version": row.get("data_version"),
        "fetched_at": row.get("fetched_at"),
        "partition_file": row.get("partition_file"),
    }


def _selection_category(reasons: list[str]) -> list[str]:
    categories: set[str] = set()
    for reason in reasons:
        if reason.startswith("YEAR_BOUNDARY"):
            categories.add("YEAR_BOUNDARY")
        elif reason == "LONGEST_RUN_ENDPOINT":
            categories.add("LONG_RUN_ENDPOINT")
        elif reason == "LIFECYCLE_BOUNDARY_OBSERVATION":
            categories.add("LIFECYCLE_BOUNDARY")
        elif reason == "SAME_DATE_CLUSTER_REPRESENTATIVE":
            categories.add("SAME_DATE_CLUSTER")
        elif reason == "GLOBAL_DATE_BOUNDARY":
            categories.add("GLOBAL_BOUNDARY")
        elif reason.startswith("NUMERIC_PATTERN_BOUNDARY"):
            categories.add("NUMERIC_PATTERN_BOUNDARY")
        elif reason.startswith("CANONICAL_STATE_REPRESENTATIVE"):
            categories.add("CANONICAL_STATE")
        elif reason == "SINGLE_DAY_OBSERVATION":
            categories.add("SINGLE_DAY")
        else:
            categories.add("OTHER")
    return sorted(categories or {"OTHER"})


def _secondary_endpoint_result(
    results: dict[str, dict[str, Any]],
    endpoint: str,
) -> dict[str, Any] | None:
    return results.get(endpoint)


def double_blank_pattern_status(
    representative_rows: list[dict[str, Any]],
    category_results: dict[str, dict[str, int]],
) -> str:
    """Return the bounded-pattern status without extrapolating partial evidence."""

    supported = (
        len(representative_rows) == REPRESENTATIVE_KEY_N
        and all(row["decision"] == "NOT_EXPECTED_BAR" for row in representative_rows)
        and not any(
            row["provider_failed"]
            or row["invalid_provider_row"]
            or row["duplicate_provider_row"]
            for row in representative_rows
        )
        and all(
            value["EXPECTED_BAR_N"] == 0 and value["UNKNOWN_N"] == 0
            for value in category_results.values()
        )
    )
    return (
        "SUPPORTED_FOR_EXTRAPOLATION_DESIGN"
        if supported
        else "NOT_SUPPORTED_FOR_EXTRAPOLATION_DESIGN"
    )


def adjudicate_key(
    scope_row: dict[str, Any],
    primary_row: dict[str, Any],
    canonical_row: dict[str, Any] | None,
    local_evidence: dict[str, Any] | None,
    secondary_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    symbol, trade_date = key_of(scope_row)
    if local_evidence is not None:
        decision = "EXPECTED_BAR"
        reason = "LOCAL_TUSHARE_POSITIVE_DAILY_ROW"
        evidence_level = "LOCAL_INDEPENDENT_RAW_OBSERVATION"
        secondary_source = "TUSHARE_LOCAL_RAW_DAILY"
        secondary_observation = {"daily": local_evidence, "suspend_d": None}
        provider_failed = False
        invalid = False
        duplicate = False
    else:
        daily = _secondary_endpoint_result(secondary_results, "daily")
        suspend = _secondary_endpoint_result(secondary_results, "suspend_d")
        eastmoney = _secondary_endpoint_result(secondary_results, "eastmoney_kline")
        if eastmoney is not None:
            daily = eastmoney
            suspend = None
        provider_failed = any(
            result is None or result.get("status") == "PROVIDER_FAILED"
            for result in ((daily, suspend) if eastmoney is None else (daily,))
        )
        invalid = any(
            result is not None
            and result.get("status") == "INVALID_PROVIDER_RESPONSE"
            or result is not None
            and result.get("status") == "INVALID_PROVIDER_ROW"
            for result in ((daily, suspend) if eastmoney is None else (daily,))
        )
        duplicate = any(
            result is not None and result.get("status") == "DUPLICATE_PROVIDER_ROW"
            for result in ((daily, suspend) if eastmoney is None else (daily,))
        )
        daily_rows = (daily or {}).get("rows", [])
        suspend_rows = (suspend or {}).get("rows", [])
        daily_positive = bool(
            len(daily_rows) == 1
            and (
                _positive(daily_rows[0].get("vol"))
                or _positive(daily_rows[0].get("amount"))
            )
        )
        daily_zero_zero = bool(
            len(daily_rows) == 1
            and not _positive(daily_rows[0].get("vol"))
            and not _positive(daily_rows[0].get("amount"))
        )
        explicit_suspend = bool(
            len(suspend_rows) == 1
            and str(suspend_rows[0].get("suspend_type", "")).strip().upper() == "S"
        )
        if provider_failed or invalid or duplicate:
            decision = "UNKNOWN"
            reason = (
                "SECONDARY_PROVIDER_FAILURE"
                if provider_failed
                else "SECONDARY_INVALID_OR_DUPLICATE_ROW"
            )
            evidence_level = "SECONDARY_INCOMPLETE"
        elif daily_positive and explicit_suspend:
            decision = "UNKNOWN"
            reason = "CONFLICTING_SECONDARY_DAILY_AND_SUSPENSION"
            evidence_level = "SECONDARY_CONFLICT"
        elif daily_positive:
            decision = "EXPECTED_BAR"
            reason = "TUSHARE_DAILY_POSITIVE_ROW"
            evidence_level = "NETWORK_INDEPENDENT_PROVIDER_OBSERVATION"
        elif explicit_suspend and not daily_rows:
            decision = "NOT_EXPECTED_BAR"
            reason = "TUSHARE_EXPLICIT_SUSPENSION_ROW"
            evidence_level = "NETWORK_INDEPENDENT_PROVIDER_SUSPENSION"
        elif daily_zero_zero and explicit_suspend:
            decision = "UNKNOWN"
            reason = "CONFLICTING_SECONDARY_ZERO_DAILY_AND_SUSPENSION"
            evidence_level = "SECONDARY_CONFLICT"
        else:
            decision = "UNKNOWN"
            reason = "SECONDARY_ROW_ABSENT_WITHOUT_EXPLICIT_SUSPENSION"
            evidence_level = "SECONDARY_ABSENCE_NOT_DECISIVE"
        secondary_source = (
            "EASTMONEY_PUBLIC_KLINE" if eastmoney is not None else "TUSHARE_PRO"
        )
        secondary_observation = (
            {"eastmoney_kline": eastmoney}
            if eastmoney is not None
            else {"daily": daily, "suspend_d": suspend}
        )
        if secondary_source == "EASTMONEY_PUBLIC_KLINE":
            evidence_level = (
                "NETWORK_INDEPENDENT_PROVIDER_OBSERVATION"
                if decision == "EXPECTED_BAR"
                else evidence_level
            )

    canonical_source = None if canonical_row is None else canonical_row.get("source")
    independence = independence_record(secondary_source, canonical_row)
    repair_required = bool(decision == "EXPECTED_BAR" and canonical_row is None)
    return {
        "symbol": symbol,
        "trade_date": trade_date.isoformat(),
        "scope_classification": scope_row["classification"],
        "old_classification": STATUS0_CLASSIFICATION,
        "primary_authority_source": PRIMARY_AUTHORITY_SOURCE,
        "primary_authority_runtime": PRIMARY_AUTHORITY_RUNTIME,
        "primary_basis": primary_row["basis"],
        "primary_tradestatus": primary_row["tradestatus"],
        "primary_request_id": primary_row["request_id"],
        "primary_provider_code": primary_row["provider_code"],
        "primary_provider_row": {
            field: primary_row[field]
            for field in (
                "open",
                "high",
                "low",
                "close",
                "volume_raw",
                "amount_raw",
                "preclose",
            )
        },
        "primary_receipt_provenance": {
            field: primary_row[field]
            for field in (
                "raw_receipt_file_sha256",
                "normalized_receipt_file_sha256",
                "raw_payload_sha256",
                "normalized_payload_sha256",
            )
        },
        "canonical": _canonical_compact(canonical_row),
        "secondary_source": secondary_source,
        "secondary_provider_runtime": (
            ALTERNATE_SECONDARY_PROVIDER_RUNTIME
            if secondary_source == "EASTMONEY_PUBLIC_KLINE"
            else SECONDARY_PROVIDER_RUNTIME
        ),
        "secondary_observation": secondary_observation,
        "decision": decision,
        "decision_reason": reason,
        "evidence_level": evidence_level,
        "canonical_source": canonical_source,
        "independence": independence,
        "selection_reasons": list(scope_row.get("selection_reasons") or []),
        "selection_categories": _selection_category(
            list(scope_row.get("selection_reasons") or [])
        ),
        "canonical_present": canonical_row is not None,
        "repair_required": repair_required,
        "provider_failed": provider_failed,
        "invalid_provider_row": invalid,
        "duplicate_provider_row": duplicate,
    }


def _manifest_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (str(row["symbol"]), str(row["trade_date"])))
    keys = [(str(row["symbol"]), parse_date(row["trade_date"])) for row in ordered]
    return {"KEY_N": len(ordered), "KEYSET_HASH": keyset_hash(keys), "ROWS": ordered}


def build_adjudication_manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    compact_rows = []
    for row in sorted(rows, key=lambda item: (item["symbol"], item["trade_date"])):
        compact_rows.append(
            {
                "symbol": row["symbol"],
                "trade_date": row["trade_date"],
                "scope_classification": row["scope_classification"],
                "decision": row["decision"],
                "decision_reason": row["decision_reason"],
                "evidence_level": row["evidence_level"],
                "secondary_source": row["secondary_source"],
                "primary_request_id": row["primary_request_id"],
                "canonical_present": row["canonical_present"],
                "repair_required": row["repair_required"],
                "provider_failed": row["provider_failed"],
                "invalid_provider_row": row["invalid_provider_row"],
                "duplicate_provider_row": row["duplicate_provider_row"],
                "selection_categories": row["selection_categories"],
            }
        )
    body = {
        "TASK": TASK,
        "BASE_REFINEMENT_COMMIT": REFINEMENT_COMMIT,
        "SCOPE_KEY_N": SCOPE_KEY_N,
        "SCOPE_KEYSET_HASH": SCOPE_KEYSET_HASH,
        "ROWS": compact_rows,
        "SERIALIZATION": "canonical JSON UTF-8; rows sorted by (symbol, trade_date)",
    }
    return {**body, "MANIFEST_HASH": sha256_json(body)}


def build_report(
    *,
    scope: dict[str, Any],
    verified: dict[str, Any],
    local_evidence: dict[tuple[str, date], dict[str, Any]],
    request_manifest: dict[str, Any],
    adjudication_rows: list[dict[str, Any]],
    actual_network_request_n: int,
    prior_provider_attempt: dict[str, Any] | None,
    prior_network_request_n: int,
    execution_run_id: str,
    post_input_manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    ordered = sorted(adjudication_rows, key=lambda row: (row["symbol"], row["trade_date"]))
    contradiction_rows = [row for row in ordered if row["scope_classification"] == CONTRADICTION]
    representative_rows = [row for row in ordered if row["scope_classification"] == INDETERMINATE]
    by_decision = {
        decision: _manifest_for_rows([row for row in ordered if row["decision"] == decision])
        for decision in ("EXPECTED_BAR", "NOT_EXPECTED_BAR", "UNKNOWN")
    }
    contradiction_by_decision = {
        decision: _manifest_for_rows(
            [row for row in contradiction_rows if row["decision"] == decision]
        )
        for decision in ("EXPECTED_BAR", "NOT_EXPECTED_BAR", "UNKNOWN")
    }
    representative_by_decision = {
        decision: _manifest_for_rows(
            [row for row in representative_rows if row["decision"] == decision]
        )
        for decision in ("EXPECTED_BAR", "NOT_EXPECTED_BAR", "UNKNOWN")
    }
    category_results: dict[str, dict[str, int]] = defaultdict(
        lambda: {"KEY_N": 0, "EXPECTED_BAR_N": 0, "NOT_EXPECTED_BAR_N": 0, "UNKNOWN_N": 0}
    )
    for row in representative_rows:
        for category in row["selection_categories"]:
            entry = category_results[category]
            entry["KEY_N"] += 1
            entry[f"{row['decision']}_N"] += 1
    pattern_status = double_blank_pattern_status(representative_rows, category_results)
    local_keyset = keyset_hash(local_evidence)
    input_manifest = verified["input_manifest"]
    post_equal = prior.reconciliation._input_equal(input_manifest, post_input_manifest)
    if not post_equal:
        raise PilotError("DAILY_INPUT_MANIFEST_DRIFT_AFTER_PILOT")
    report = {
        "TASK": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_INDEPENDENT_AUDIT",
        "BASE_HEAD": BASE_HEAD,
        "BRANCH": BRANCH,
        "BASE_REFINEMENT_COMMIT": REFINEMENT_COMMIT,
        "INPUT_MANIFEST_HASH": input_manifest.get("INPUT_MANIFEST_HASH"),
        "SESSION_AUTHORITY_DATASET_HASH": SESSION_AUTHORITY_DATASET_HASH,
        "SECONDARY_AUTHORITY_KEY_N": SCOPE_KEY_N,
        "SECONDARY_AUTHORITY_SCOPE_HASH": SCOPE_KEYSET_HASH,
        "SECONDARY_AUTHORITY_SCOPE_MANIFEST_HASH": SCOPE_MANIFEST_HASH,
        "LOCAL_INDEPENDENT_EVIDENCE_KEY_N": len(local_evidence),
        "LOCAL_INDEPENDENT_EVIDENCE_KEYSET_HASH": local_keyset,
        "NETWORK_REQUIRED_KEY_N": SCOPE_KEY_N - len(local_evidence),
        "NETWORK_PROVIDER_REQUEST_N": prior_network_request_n + actual_network_request_n,
        "NETWORK_PROVIDER_REQUEST_N_BY_PROVIDER": {
            "TUSHARE_PRO": prior_network_request_n,
            "EASTMONEY_PUBLIC_KLINE": actual_network_request_n,
        },
        "PRIOR_PROVIDER_ATTEMPT": prior_provider_attempt,
        "PRIOR_PROVIDER_FAILURE_REQUEST_N": (
            0 if prior_provider_attempt is None else prior_provider_attempt["failed_request_n"]
        ),
        "PRIOR_PROVIDER_FAILURE_KEY_N": (
            0 if prior_provider_attempt is None else prior_provider_attempt["failed_key_n"]
        ),
        "REQUEST_MANIFEST_HASH": request_manifest["REQUEST_MANIFEST_HASH"],
        "REQUEST_MANIFEST_FILE_HASH": sha256_json_file_payload(request_manifest),
        "EXECUTION_RUN_ID": execution_run_id,
        "PRIMARY_AUTHORITY_SOURCE": PRIMARY_AUTHORITY_SOURCE,
        "SECONDARY_PROVIDER": ALTERNATE_SECONDARY_PROVIDER,
        "CONTRADICTION_KEY_N": len(contradiction_rows),
        "INDETERMINATE_REPRESENTATIVE_N": len(representative_rows),
        "CONTRADICTION_EXPECTED_N": sum(row["decision"] == "EXPECTED_BAR" for row in contradiction_rows),
        "CONTRADICTION_NOT_EXPECTED_N": sum(row["decision"] == "NOT_EXPECTED_BAR" for row in contradiction_rows),
        "CONTRADICTION_UNKNOWN_N": sum(row["decision"] == "UNKNOWN" for row in contradiction_rows),
        "CONTRADICTION_REPAIR_REQUIRED_N": sum(
            row["repair_required"] for row in contradiction_rows
        ),
        "CONTRADICTION_DECISION_MANIFESTS": contradiction_by_decision,
        "REP_EXPECTED_N": sum(row["decision"] == "EXPECTED_BAR" for row in representative_rows),
        "REP_NOT_EXPECTED_N": sum(
            row["decision"] == "NOT_EXPECTED_BAR" for row in representative_rows
        ),
        "REP_UNKNOWN_N": sum(row["decision"] == "UNKNOWN" for row in representative_rows),
        "REPRESENTATIVE_DECISION_MANIFESTS": representative_by_decision,
        "REPRESENTATIVE_CATEGORY_RESULTS": dict(sorted(category_results.items())),
        "DOUBLE_BLANK_PATTERN_STATUS": pattern_status,
        "ALL_DECISION_MANIFESTS": by_decision,
        # This is the manifest's declared body hash, matching the execution
        # receipt and the committed compact manifest.  The file hash is
        # reported separately below.
        "ADJUDICATION_MANIFEST_HASH": build_adjudication_manifest(ordered)[
            "MANIFEST_HASH"
        ],
        "PROVIDER_FAILED_KEY_N": sum(row["provider_failed"] for row in ordered),
        "INVALID_PROVIDER_ROW_KEY_N": sum(row["invalid_provider_row"] for row in ordered),
        "DUPLICATE_PROVIDER_KEY_N": sum(row["duplicate_provider_row"] for row in ordered),
        "PILOT_STATUS": (
            "COMPLETE"
            if prior_provider_attempt is None
            and not any(
                row["provider_failed"]
                or row["invalid_provider_row"]
                or row["duplicate_provider_row"]
                for row in ordered
            )
            else (
                "COMPLETE_WITH_PRIOR_PROVIDER_FAILURES"
                if prior_provider_attempt is not None
                and not any(
                    row["provider_failed"]
                    or row["invalid_provider_row"]
                    or row["duplicate_provider_row"]
                    for row in ordered
                )
                else "COMPLETE_WITH_PROVIDER_EXCEPTIONS"
            )
        ),
        "SESSION_AUTHORITY_PROMOTED": False,
        "R3_REFREEZE_RECOMMENDATION": "BLOCKED",
        "SAFETY": {
            "NETWORK_PROVIDER_DATA_FETCH": "YES_BOUNDED_FROZEN_SCOPE_ONLY",
            "NETWORK_PROVIDER_SCOPE_KEY_N": SCOPE_KEY_N - len(local_evidence),
            "BAOSTOCK_EXECUTED": False,
            "TDX_EXECUTED": False,
            "CANONICAL_WRITE_EXECUTED": False,
            "CANONICAL_BYTES_MUTATED": False,
            "SESSION_AUTHORITY_PROMOTED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRODUCTION": False,
            "FORWARD": False,
            "TRADEPLAN": False,
        },
        "TEST_RESULT": "PENDING_TARGETED_TESTS",
        "PY_COMPILE": "PENDING",
        "GIT_DIFF_CHECK": "PENDING",
    }
    evidence_index = {
        "TASK": TASK,
        "BASE_REFINEMENT_COMMIT": REFINEMENT_COMMIT,
        "SCOPE_KEY_N": SCOPE_KEY_N,
        "SCOPE_KEYSET_HASH": SCOPE_KEYSET_HASH,
        "REQUEST_MANIFEST_HASH": request_manifest["REQUEST_MANIFEST_HASH"],
        "NETWORK_PROVIDER_REQUEST_N": actual_network_request_n,
        "ROWS": ordered,
        "SERIALIZATION": "canonical JSON UTF-8; rows sorted by (symbol, trade_date)",
    }
    adjudication_manifest = build_adjudication_manifest(ordered)
    return report, evidence_index, adjudication_manifest


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        f"# {TASK}",
        "",
        "Bounded independent secondary-authority pilot for the frozen 52-key R3 status0 scope.",
        "",
        "## Authority and scope",
        "",
        f"- `BASE_REFINEMENT_COMMIT={report['BASE_REFINEMENT_COMMIT']}`",
        f"- `INPUT_MANIFEST_HASH={report['INPUT_MANIFEST_HASH']}`",
        f"- `SESSION_AUTHORITY_DATASET_HASH={report['SESSION_AUTHORITY_DATASET_HASH']}`",
        f"- `SECONDARY_AUTHORITY_KEY_N={report['SECONDARY_AUTHORITY_KEY_N']}`",
        f"- `SECONDARY_AUTHORITY_SCOPE_HASH={report['SECONDARY_AUTHORITY_SCOPE_HASH']}`",
        f"- `REQUEST_MANIFEST_HASH={report['REQUEST_MANIFEST_HASH']}`",
        f"- `NETWORK_REQUIRED_KEY_N={report['NETWORK_REQUIRED_KEY_N']}`; `NETWORK_PROVIDER_REQUEST_N={report['NETWORK_PROVIDER_REQUEST_N']}`",
        "",
        "## Adjudication",
        "",
        f"- contradictions: expected=`{report['CONTRADICTION_EXPECTED_N']}`, not-expected=`{report['CONTRADICTION_NOT_EXPECTED_N']}`, unknown=`{report['CONTRADICTION_UNKNOWN_N']}`, repair-required=`{report['CONTRADICTION_REPAIR_REQUIRED_N']}`.",
        f"- representatives: expected=`{report['REP_EXPECTED_N']}`, not-expected=`{report['REP_NOT_EXPECTED_N']}`, unknown=`{report['REP_UNKNOWN_N']}`.",
        f"- `DOUBLE_BLANK_PATTERN_STATUS={report['DOUBLE_BLANK_PATTERN_STATUS']}`; no extrapolation to all 15,004 keys is performed.",
        f"- `R3_REFREEZE_RECOMMENDATION={report['R3_REFREEZE_RECOMMENDATION']}`; `SESSION_AUTHORITY_PROMOTED={str(report['SESSION_AUTHORITY_PROMOTED']).lower()}`.",
        "",
        "## Independence",
        "",
        "- Local independent evidence is reused first; the bounded network fallback is EastMoney public kline. BaoStock is never used as secondary confirmation.",
        "- Same-source evidence is explicitly non-independent; canonical source identity is retained per key.",
        "",
        "## Safety",
        "",
    ]
    for key, value in report["SAFETY"].items():
        lines.append(f"- `{key}={str(value).lower() if isinstance(value, bool) else value}`")
    lines.extend(
        [
            "",
            f"- `TEST_RESULT={report['TEST_RESULT']}`; `PY_COMPILE={report['PY_COMPILE']}`; `GIT_DIFF_CHECK={report['GIT_DIFF_CHECK']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def run_pilot(
    *,
    repo_root: Path = REPO_ROOT,
    data_root: Path = DATA_ROOT_DEFAULT,
    stage_root: Path | None = None,
    transport: Transport | None = None,
    request_interval_seconds: float = REQUEST_INTERVAL_SECONDS,
    test_result: str = "PENDING_TARGETED_TESTS",
    py_compile_result: str = "PENDING",
    diff_check_result: str = "PENDING",
) -> dict[str, Any]:
    # This must remain the first data-root operation.  Direct Python callers
    # cannot bypass the stage-root guard by skipping CLI parsing.
    if stage_root is None:
        stage_root = Path(data_root) / "staging" / STAGING_DIRNAME
    isolated_stage = require_isolated_stage_root(data_root, stage_root)

    scope = verify_scope(repo_root)
    session_stage = (
        Path(data_root).resolve()
        / "staging"
        / SESSION_STAGE_DIRNAME
    )
    verified = verify_input_gate(repo_root, Path(data_root), session_stage, scope)
    scope_rows = scope["rows"]

    primary = load_primary_target_evidence(verified, scope_rows)
    canonical = canonical_rows_for_scope(Path(data_root), scope_rows)
    local_evidence = scan_local_independent_tushare(scope_rows)
    preliminary_tushare = load_preliminary_tushare_attempt(
        Path(data_root).resolve() / "staging" / STAGING_DIRNAME
    )
    request_manifest = build_request_manifest(
        scope_rows,
        local_evidence,
        provider=ALTERNATE_SECONDARY_PROVIDER,
        prior_provider_attempt=preliminary_tushare,
    )

    isolated_stage.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = isolated_stage / f"run_{run_id}"
    run_root.mkdir(parents=True, exist_ok=False)
    raw_stage = run_root / "raw_receipts"
    raw_stage.mkdir(parents=True, exist_ok=False)
    # Request manifest is deterministic and can be safely inspected before
    # any network call.  The token is absent by construction.
    request_manifest_stage_hash = _write_stage_json(
        run_root,
        "request_manifest.json",
        request_manifest,
    )

    network_specs = request_manifest["REQUESTS"]
    network_results: dict[tuple[str, date], dict[str, dict[str, Any]]] = defaultdict(dict)
    actual_request_n = 0
    if network_specs:
        active_transport = transport or make_eastmoney_transport()
        last_request_at: float | None = None
        for spec in network_specs:
            if last_request_at is not None:
                wait = request_interval_seconds - (time.monotonic() - last_request_at)
                if wait > 0:
                    time.sleep(wait)
            last_request_at = time.monotonic()
            observation, attempts = execute_request(
                spec,
                active_transport,
                run_root,
            )
            actual_request_n += attempts
            key = (str(spec["symbol"]), parse_date(spec["trade_date"]))
            network_results[key][str(spec["endpoint"])] = observation

        verify_network_receipts(run_root, network_specs, network_results)

    adjudication_rows = []
    for scope_row in scope_rows:
        key = key_of(scope_row)
        adjudication_rows.append(
            adjudicate_key(
                scope_row,
                primary[key],
                canonical.get(key),
                local_evidence.get(key),
                network_results.get(key, {}),
            )
        )

    post_input = verified["context"]["plan"].build_input_file_manifest(Path(data_root))
    report, evidence_index, adjudication_manifest = build_report(
        scope=scope,
        verified=verified,
        local_evidence=local_evidence,
        request_manifest=request_manifest,
        adjudication_rows=adjudication_rows,
        actual_network_request_n=actual_request_n,
        prior_provider_attempt=preliminary_tushare,
        prior_network_request_n=(
            0 if preliminary_tushare is None else preliminary_tushare["request_n"]
        ),
        execution_run_id=run_id,
        post_input_manifest=post_input,
    )
    execution_receipt = {
        "TASK": TASK,
        "EXECUTION_RUN_ID": run_id,
        "REQUEST_MANIFEST_HASH": request_manifest["REQUEST_MANIFEST_HASH"],
        "REQUEST_MANIFEST_STAGE_FILE_SHA256": request_manifest_stage_hash,
        "NETWORK_PROVIDER_REQUEST_N": report["NETWORK_PROVIDER_REQUEST_N"],
        "NETWORK_PROVIDER_REQUEST_N_BY_PROVIDER": report[
            "NETWORK_PROVIDER_REQUEST_N_BY_PROVIDER"
        ],
        "PRIOR_PROVIDER_ATTEMPT": preliminary_tushare,
        "NETWORK_KEY_N": request_manifest["NETWORK_KEY_N"],
        "LOCAL_REUSE_KEY_N": len(local_evidence),
        "ADJUDICATION_MANIFEST_HASH": adjudication_manifest["MANIFEST_HASH"],
        "STATUS": report["PILOT_STATUS"],
        "SAFETY": report["SAFETY"],
    }
    _write_stage_json(run_root, "execution_receipt.json", execution_receipt)

    report["TEST_RESULT"] = test_result
    report["PY_COMPILE"] = py_compile_result
    report["GIT_DIFF_CHECK"] = diff_check_result
    report["REQUEST_MANIFEST_STAGE_FILE_SHA256"] = request_manifest_stage_hash
    report["STAGING_RUN_ROOT"] = str(run_root)
    report["EVIDENCE_INDEX_HASH"] = sha256_json(evidence_index)
    report["ADJUDICATION_MANIFEST_FILE_HASH"] = sha256_json_file_payload(
        adjudication_manifest
    )
    report["EVIDENCE_INDEX_FILE_HASH"] = sha256_json_file_payload(evidence_index)

    write_repo_json(repo_root, REQUEST_MANIFEST_NAME, request_manifest)
    write_repo_json(repo_root, ADJUDICATION_MANIFEST_NAME, adjudication_manifest)
    write_repo_json(repo_root, EVIDENCE_INDEX_NAME, evidence_index)
    write_repo_json(repo_root, REPORT_NAME, report)
    write_repo_text(repo_root, REPORT_MD_NAME, markdown_report(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--stage-root", type=Path, default=None)
    parser.add_argument("--request-interval-seconds", type=float, default=REQUEST_INTERVAL_SECONDS)
    parser.add_argument("--test-result", default="PENDING_TARGETED_TESTS")
    parser.add_argument("--py-compile-result", default="PENDING")
    parser.add_argument("--diff-check-result", default="PENDING")
    args = parser.parse_args(argv)
    try:
        report = run_pilot(
            repo_root=args.repo_root,
            data_root=args.data_root,
            stage_root=args.stage_root,
            request_interval_seconds=args.request_interval_seconds,
            test_result=args.test_result,
            py_compile_result=args.py_compile_result,
            diff_check_result=args.diff_check_result,
        )
    except (PilotError, ProviderFailure, prior.AuditError, prior.reconciliation.ReconciliationError) as exc:
        print(
            json.dumps(
                {
                    "TASK": TASK,
                    "AUTHOR_STATUS": "BLOCKED_FAIL_CLOSED",
                    "ERROR": str(exc),
                    "NETWORK_PROVIDER_DATA_FETCH": "NO",
                    "NETWORK_PROVIDER_REQUEST_N": 0,
                    "CANONICAL_WRITE_EXECUTED": False,
                    "CANONICAL_BYTES_MUTATED": False,
                    "SESSION_AUTHORITY_PROMOTED": False,
                    "R4A9_RESUME_AUTHORIZED": False,
                    "R3_REFREEZE_RECOMMENDATION": "BLOCKED",
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
