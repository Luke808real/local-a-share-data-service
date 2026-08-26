#!/usr/bin/env python3
"""Offline provenance closure for R3 TDX targeted-refetch staging V01.1.

Reads the already-complete isolated staging artifacts and produces compact,
remote-auditable changed-key evidence.  This module has no provider client,
does not write the staging root, and never reads or writes canonical data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Callable


TASK = "R3_TDX_VOLUME_TARGETED_REFETCH_STAGING_V01_1"
TARGET_FREEZE_V01_COMMIT = "f99918c786c1f0654ce642a460853141b723f61e"
TARGET_FREEZE_V01_1_COMMIT = "c35f893917d28fbddd9e250aaadc59259534856d"
STAGING_IMPLEMENTATION_COMMIT = "859fab37125c7f6576e273563b597521760617e6"

REQUEST_MANIFEST_HASH = "d75bd59ac2e6f0159383deb4b7a783c7dc255a40a47b893e1749c5a8ba7b9386"
STAGING_OUTPUT_HASH = "7616ca4aba1bac1c532a770c195b451d83979859cca089ef4eb1a905314f2d64"
REPAIR_SUPERSET_MANIFEST_HASH = "1d579988015ef61fe06ae0eede91e2c63c2356a966f58363a6ebbd2f126e7e5b"
INPUT_MANIFEST_HASH = "f9025a5cbc52d757fdc05d9e6ebb5f3c75c1cd93414a2f6c51bb83314594a6ec"

DEFAULT_STAGING_ROOT = Path(
    "/Users/luke808/AI/local-a-share-data-service-data/staging/"
    "r3_tdx_volume_targeted_refetch_v01"
)
REQUIRED_STAGING_FILES = (
    "request_manifest.json",
    "per_key_staging_results.json",
    "execution_receipt.json",
    "reconciliation.json",
    "quality_gate.json",
)


class OfflineAuditError(RuntimeError):
    """Any mismatch is terminal: this audit must fail closed without refetch."""


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


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfflineAuditError(f"UNREADABLE_STAGING_ARTIFACT:{path}:{exc}") from exc


def write_canonical_json(path: Path, payload: Any) -> str:
    data = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256_bytes(data)


def repo_root_for_tool() -> Path:
    return Path(__file__).resolve().parents[2]


def _key_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"symbol": row["symbol"], "trade_date": row["trade_date"]}
        for row in sorted(rows, key=lambda row: (row["symbol"], row["trade_date"]))
    ]


def _keyset(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(str(row["symbol"]), str(row["trade_date"])) for row in rows}


def _hash_keyset(rows: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(_key_rows(rows)))


def validate_authority_values(
    *,
    target_freeze_v01_commit: str,
    target_freeze_v01_1_commit: str,
    staging_implementation_commit: str,
) -> dict[str, str]:
    if target_freeze_v01_commit != TARGET_FREEZE_V01_COMMIT:
        raise OfflineAuditError("TARGET_FREEZE_V01_COMMIT_MISMATCH")
    if target_freeze_v01_1_commit != TARGET_FREEZE_V01_1_COMMIT:
        raise OfflineAuditError("TARGET_FREEZE_V01_1_COMMIT_MISMATCH")
    if staging_implementation_commit != STAGING_IMPLEMENTATION_COMMIT:
        raise OfflineAuditError("STAGING_IMPLEMENTATION_COMMIT_MISMATCH")
    return {
        "TARGET_FREEZE_V01_COMMIT": target_freeze_v01_commit,
        "TARGET_FREEZE_V01_1_COMMIT": target_freeze_v01_1_commit,
        "STAGING_IMPLEMENTATION_COMMIT": staging_implementation_commit,
        "FORMAL_STAGING_AUTHORITY": (
            "R3_TDX_VOLUME_REBUILD_TARGET_FREEZE_V01_1@"
            f"{target_freeze_v01_1_commit}"
        ),
    }


def verify_git_authority(repo_root: Path, execution_base_head: str) -> dict[str, str]:
    for commit in (
        TARGET_FREEZE_V01_COMMIT,
        TARGET_FREEZE_V01_1_COMMIT,
        STAGING_IMPLEMENTATION_COMMIT,
    ):
        actual = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
            cwd=repo_root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        if actual != commit:
            raise OfflineAuditError(f"GIT_AUTHORITY_MISMATCH:{commit}")
    if execution_base_head != STAGING_IMPLEMENTATION_COMMIT:
        raise OfflineAuditError("EXECUTION_BASE_HEAD_MISMATCH")
    report_path = "reports/implementation/R3_TDX_VOLUME_REBUILD_TARGET_FREEZE_V01_1.json"
    frozen_report = json.loads(
        subprocess.run(
            ["git", "show", f"{TARGET_FREEZE_V01_1_COMMIT}:{report_path}"],
            cwd=repo_root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    )
    if frozen_report.get("REPORT") != "R3_TDX_VOLUME_REBUILD_TARGET_FREEZE_V01_1":
        raise OfflineAuditError("TARGET_FREEZE_V01_1_REPORT_MISMATCH")
    if frozen_report.get("TARGET_FREEZE_V01_COMMIT") != TARGET_FREEZE_V01_COMMIT:
        raise OfflineAuditError("TARGET_FREEZE_V01_PARENT_MISMATCH")
    return validate_authority_values(
        target_freeze_v01_commit=TARGET_FREEZE_V01_COMMIT,
        target_freeze_v01_1_commit=TARGET_FREEZE_V01_1_COMMIT,
        staging_implementation_commit=STAGING_IMPLEMENTATION_COMMIT,
    )


def load_frozen_target_rows(repo_root: Path) -> list[dict[str, Any]]:
    path = repo_root / "reports" / "implementation" / "R3_TDX_VOLUME_REBUILD_TARGET_MANIFEST_V01.json"
    if sha256_file(path) != REPAIR_SUPERSET_MANIFEST_HASH:
        raise OfflineAuditError("REPAIR_SUPERSET_MANIFEST_HASH_MISMATCH")
    rows = load_json(path)
    if not isinstance(rows, list) or len(rows) != 29_299 or len(_keyset(rows)) != 29_299:
        raise OfflineAuditError("FROZEN_TARGET_KEYSET_MISMATCH")
    return rows


def _default_expectations() -> dict[str, Any]:
    return {
        "request_manifest_hash": REQUEST_MANIFEST_HASH,
        "staging_output_hash": STAGING_OUTPUT_HASH,
        "target_key_n": 29_299,
        "target_symbol_n": 4_577,
        "resolved_n": 29_299,
        "changed_n": 1_169,
        "unchanged_n": 28_130,
        "missing_n": 0,
        "duplicate_n": 0,
        "provider_failed_n": 0,
        "invalid_n": 0,
        "affected_n": 32,
        "affected_prediction_mismatch_n": 0,
    }


def verify_staging_artifacts(
    staging_root: Path,
    frozen_rows: list[dict[str, Any]],
    *,
    expectations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read and bind existing staging bytes; this function never writes staging."""
    expected = _default_expectations() if expectations is None else expectations
    paths = {name: staging_root / name for name in REQUIRED_STAGING_FILES}
    if any(not path.is_file() for path in paths.values()):
        raise OfflineAuditError("REQUIRED_STAGING_ARTIFACT_MISSING")
    request_bytes = paths["request_manifest.json"].read_bytes()
    staging_bytes = paths["per_key_staging_results.json"].read_bytes()
    request_hash = sha256_bytes(request_bytes)
    staging_hash = sha256_bytes(staging_bytes)
    if request_hash != expected["request_manifest_hash"]:
        raise OfflineAuditError("REQUEST_MANIFEST_HASH_MISMATCH")
    if staging_hash != expected["staging_output_hash"]:
        raise OfflineAuditError("STAGING_OUTPUT_HASH_MISMATCH")
    request = json.loads(request_bytes)
    results = json.loads(staging_bytes)
    receipt = load_json(paths["execution_receipt.json"])
    reconciliation = load_json(paths["reconciliation.json"])
    quality = load_json(paths["quality_gate.json"])
    if receipt.get("REQUEST_MANIFEST_HASH") != request_hash:
        raise OfflineAuditError("RECEIPT_REQUEST_HASH_MISMATCH")
    if receipt.get("STAGING_OUTPUT_HASH") != staging_hash:
        raise OfflineAuditError("RECEIPT_STAGING_HASH_MISMATCH")
    if receipt.get("INPUT_MANIFEST_HASH") != INPUT_MANIFEST_HASH:
        raise OfflineAuditError("RECEIPT_INPUT_MANIFEST_HASH_MISMATCH")
    if receipt.get("REPAIR_SUPERSET_MANIFEST_HASH") != REPAIR_SUPERSET_MANIFEST_HASH:
        raise OfflineAuditError("RECEIPT_REPAIR_SUPERSET_HASH_MISMATCH")

    request_rows = [
        {"symbol": item["symbol"], "trade_date": key["trade_date"]}
        for item in request.get("REQUESTS", [])
        for key in item.get("expected_target_keys", [])
    ]
    frozen_keyset = _keyset(frozen_rows)
    request_keyset = _keyset(request_rows)
    result_keyset = _keyset(results)
    if len(request_rows) != expected["target_key_n"] or len(request_keyset) != len(request_rows):
        raise OfflineAuditError("REQUEST_KEYSET_CARDINALITY_MISMATCH")
    if request_keyset != frozen_keyset:
        raise OfflineAuditError("REQUEST_NOT_EXACT_FROZEN_TARGET")
    if len(results) != expected["target_key_n"] or len(result_keyset) != len(results):
        raise OfflineAuditError("STAGING_DUPLICATE_OR_MISSING_KEY")
    if result_keyset != frozen_keyset:
        raise OfflineAuditError("STAGING_NOT_EXACT_FROZEN_TARGET")
    if len({row["symbol"] for row in results}) != expected["target_symbol_n"]:
        raise OfflineAuditError("STAGING_SYMBOL_N_MISMATCH")

    status_counts = Counter(row.get("fetch_status") for row in results)
    changed = [row for row in results if row.get("fresh_vs_old") == "CHANGED"]
    unchanged = [row for row in results if row.get("fresh_vs_old") == "UNCHANGED"]
    affected = [row for row in results if row.get("classification") == "PROVABLY_AFFECTED"]
    affected_prediction_mismatch_n = sum(
        row.get("classification") == "PROVABLY_AFFECTED"
        and row.get("fresh_equals_predicted_corrected") is not True
        for row in results
    )
    observed = {
        "TARGET_KEY_N": len(results),
        "TARGET_SYMBOL_N": len({row["symbol"] for row in results}),
        "RESOLVED_KEY_N": status_counts["RESOLVED"],
        "CHANGED_KEY_N": len(changed),
        "UNCHANGED_KEY_N": len(unchanged),
        "MISSING_KEY_N": status_counts["MISSING_TARGET_KEY"],
        "DUPLICATE_KEY_N": status_counts["DUPLICATE_PROVIDER_ROW"],
        "PROVIDER_FAILED_KEY_N": status_counts["PROVIDER_FAILED"],
        "INVALID_VOLUME_KEY_N": status_counts["INVALID_VOLUME"],
        "PROVABLY_AFFECTED_N": len(affected),
        "PROVABLY_AFFECTED_PREDICTION_MISMATCH_N": affected_prediction_mismatch_n,
    }
    expected_pairs = {
        "TARGET_KEY_N": expected["target_key_n"],
        "TARGET_SYMBOL_N": expected["target_symbol_n"],
        "RESOLVED_KEY_N": expected["resolved_n"],
        "CHANGED_KEY_N": expected["changed_n"],
        "UNCHANGED_KEY_N": expected["unchanged_n"],
        "MISSING_KEY_N": expected["missing_n"],
        "DUPLICATE_KEY_N": expected["duplicate_n"],
        "PROVIDER_FAILED_KEY_N": expected["provider_failed_n"],
        "INVALID_VOLUME_KEY_N": expected["invalid_n"],
        "PROVABLY_AFFECTED_N": expected["affected_n"],
        "PROVABLY_AFFECTED_PREDICTION_MISMATCH_N": expected[
            "affected_prediction_mismatch_n"
        ],
    }
    if any(observed[key] != value for key, value in expected_pairs.items()):
        raise OfflineAuditError("STAGING_RECONCILIATION_MISMATCH")
    if not reconciliation.get("STAGING_COMPLETE") or not quality.get("QUALITY_PASS"):
        raise OfflineAuditError("STAGING_QUALITY_GATE_NOT_PASS")
    if quality.get("CANONICAL_WRITE_EXECUTED") is not False:
        raise OfflineAuditError("CANONICAL_WRITE_EVIDENCE_MISMATCH")

    changed_keyset = _keyset(changed)
    unchanged_keyset = _keyset(unchanged)
    if changed_keyset & unchanged_keyset:
        raise OfflineAuditError("CHANGED_UNCHANGED_INTERSECTION_NONEMPTY")
    if changed_keyset | unchanged_keyset != frozen_keyset:
        raise OfflineAuditError("CHANGED_UNCHANGED_NOT_EXACT_PARTITION")
    return {
        "REQUEST_MANIFEST_HASH": request_hash,
        "STAGING_OUTPUT_HASH": staging_hash,
        "RESULTS": results,
        "CHANGED_ROWS": changed,
        "UNCHANGED_ROWS": unchanged,
        "ALL_STAGING_KEYSET_HASH": _hash_keyset(results),
        "UNCHANGED_KEYSET_HASH": _hash_keyset(unchanged),
        "OBSERVED": observed,
        "RECONCILIATION": reconciliation,
        "QUALITY_GATE": quality,
        "PARTITION": {
            "CHANGED_UNION_UNCHANGED_EQUALS_FROZEN": True,
            "CHANGED_INTERSECTION_UNCHANGED_EMPTY": True,
        },
    }


def build_changed_key_manifest(changed_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """The compact manifest is exactly the values eligible for a future repair."""
    rows = [
        {
            "symbol": row["symbol"],
            "trade_date": row["trade_date"],
            "classification": row["classification"],
            "old_volume": row["old_volume"],
            "fresh_tdx_volume": row["fresh_tdx_volume"],
            "predicted_corrected_volume": row["predicted_corrected_volume"],
            "fetch_status": row["fetch_status"],
        }
        for row in changed_rows
    ]
    rows.sort(key=lambda row: (row["symbol"], row["trade_date"]))
    if len(_keyset(rows)) != len(rows):
        raise OfflineAuditError("CHANGED_MANIFEST_DUPLICATE_KEY")
    return rows, sha256_bytes(canonical_json_bytes(rows))


def build_report(
    *,
    authority: dict[str, str],
    verified: dict[str, Any],
    changed_manifest_hash: str,
) -> dict[str, Any]:
    changed_rows = verified["CHANGED_ROWS"]
    affected_changed_n = sum(row["classification"] == "PROVABLY_AFFECTED" for row in changed_rows)
    ambiguous_changed_n = sum(row["classification"] == "AMBIGUOUS" for row in changed_rows)
    if affected_changed_n != 32 or ambiguous_changed_n != 1_137:
        raise OfflineAuditError("CHANGED_CLASSIFICATION_BUCKET_MISMATCH")
    return {
        "REPORT": TASK,
        "AUTHOR_STATUS": "PASS_PENDING_SOL_AUDIT",
        "EXECUTION_BASE_HEAD": TARGET_FREEZE_V01_1_COMMIT,
        **authority,
        "REQUEST_MANIFEST_HASH": verified["REQUEST_MANIFEST_HASH"],
        "STAGING_OUTPUT_HASH": verified["STAGING_OUTPUT_HASH"],
        "ALL_STAGING_KEYSET_HASH": verified["ALL_STAGING_KEYSET_HASH"],
        "CHANGED_KEY_MANIFEST_HASH": changed_manifest_hash,
        "UNCHANGED_KEYSET_HASH": verified["UNCHANGED_KEYSET_HASH"],
        **verified["OBSERVED"],
        "PROVABLY_AFFECTED_CHANGED_N": affected_changed_n,
        "AMBIGUOUS_CHANGED_N": ambiguous_changed_n,
        "PARTITION": verified["PARTITION"],
        "SAFETY": {
            "NETWORK_PROVIDER_DATA_FETCH": "NO",
            "TDX_REFETCH_EXECUTED": False,
            "BAOSTOCK_EXECUTED": False,
            "CANONICAL_WRITE_EXECUTED": False,
            "300546_MISSING_DAYS_MUTATED": False,
            "R4A9_CHECKPOINT_MUTATED": False,
            "R4A9_RESUME_AUTHORIZED": False,
            "PRECLOSE_COMPLETE": False,
            "PRODUCTION": False,
            "FORWARD": False,
            "TRADEPLAN": False,
        },
    }


def markdown_report(report: dict[str, Any]) -> str:
    safety = report["SAFETY"]
    return "\n".join(
        [
            "# R3 TDX VOLUME TARGETED REFETCH STAGING V01.1",
            "",
            "AUTHOR_STATUS: `PASS_PENDING_SOL_AUDIT`",
            "",
            "## Authority correction",
            "",
            f"- TARGET_FREEZE_V01_COMMIT: `{report['TARGET_FREEZE_V01_COMMIT']}`",
            f"- TARGET_FREEZE_V01_1_COMMIT: `{report['TARGET_FREEZE_V01_1_COMMIT']}`",
            f"- STAGING_IMPLEMENTATION_COMMIT: `{report['STAGING_IMPLEMENTATION_COMMIT']}`",
            f"- EXECUTION_BASE_HEAD: `{report['EXECUTION_BASE_HEAD']}`",
            f"- FORMAL_STAGING_AUTHORITY: `{report['FORMAL_STAGING_AUTHORITY']}`",
            "",
            "## Offline binding",
            "",
            f"- REQUEST_MANIFEST_HASH: `{report['REQUEST_MANIFEST_HASH']}`",
            f"- STAGING_OUTPUT_HASH: `{report['STAGING_OUTPUT_HASH']}`",
            f"- ALL_STAGING_KEYSET_HASH: `{report['ALL_STAGING_KEYSET_HASH']}`",
            f"- CHANGED_KEY_MANIFEST_HASH: `{report['CHANGED_KEY_MANIFEST_HASH']}`",
            f"- UNCHANGED_KEYSET_HASH: `{report['UNCHANGED_KEYSET_HASH']}`",
            "",
            "## Reconciliation",
            "",
            f"- TARGET_KEY_N: {report['TARGET_KEY_N']}",
            f"- CHANGED_KEY_N: {report['CHANGED_KEY_N']}",
            f"- UNCHANGED_KEY_N: {report['UNCHANGED_KEY_N']}",
            f"- PROVABLY_AFFECTED_CHANGED_N: {report['PROVABLY_AFFECTED_CHANGED_N']}",
            f"- AMBIGUOUS_CHANGED_N: {report['AMBIGUOUS_CHANGED_N']}",
            f"- MISSING_KEY_N: {report['MISSING_KEY_N']}",
            f"- DUPLICATE_KEY_N: {report['DUPLICATE_KEY_N']}",
            f"- PROVIDER_FAILED_KEY_N: {report['PROVIDER_FAILED_KEY_N']}",
            f"- INVALID_VOLUME_KEY_N: {report['INVALID_VOLUME_KEY_N']}",
            "",
            "## Safety",
            "",
            *[
                f"- {key}={str(value).lower() if isinstance(value, bool) else value}"
                for key, value in safety.items()
            ],
            "",
        ]
    )


def run_offline_audit(
    *,
    repo_root: Path,
    staging_root: Path,
    execution_base_head: str,
    git_verifier: Callable[[Path, str], dict[str, str]] = verify_git_authority,
) -> dict[str, Any]:
    authority = git_verifier(repo_root, execution_base_head)
    frozen_rows = load_frozen_target_rows(repo_root)
    verified = verify_staging_artifacts(staging_root, frozen_rows)
    changed_rows, changed_hash = build_changed_key_manifest(verified["CHANGED_ROWS"])
    report = build_report(authority=authority, verified=verified, changed_manifest_hash=changed_hash)
    return {"REPORT": report, "CHANGED_ROWS": changed_rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-head", required=True)
    parser.add_argument("--staging-root", type=Path, default=DEFAULT_STAGING_ROOT)
    args = parser.parse_args()
    repo_root = repo_root_for_tool()
    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, text=True, capture_output=True
    ).stdout.strip()
    if current_head != args.base_head:
        print("BASE_HEAD_MISMATCH")
        return 2
    try:
        outcome = run_offline_audit(
            repo_root=repo_root,
            staging_root=args.staging_root,
            execution_base_head=args.base_head,
        )
    except OfflineAuditError as exc:
        print(str(exc))
        return 2
    implementation = repo_root / "reports" / "implementation"
    changed_path = implementation / "R3_TDX_VOLUME_TARGETED_REFETCH_CHANGED_MANIFEST_V01_1.json"
    report_json_path = implementation / "R3_TDX_VOLUME_TARGETED_REFETCH_STAGING_V01_1.json"
    report_md_path = implementation / "R3_TDX_VOLUME_TARGETED_REFETCH_STAGING_V01_1.md"
    changed_hash = write_canonical_json(changed_path, outcome["CHANGED_ROWS"])
    if changed_hash != outcome["REPORT"]["CHANGED_KEY_MANIFEST_HASH"]:
        raise OfflineAuditError("CHANGED_MANIFEST_WRITE_HASH_MISMATCH")
    write_canonical_json(report_json_path, outcome["REPORT"])
    report_md_path.write_text(markdown_report(outcome["REPORT"]), encoding="utf-8")
    print(json.dumps(outcome["REPORT"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
