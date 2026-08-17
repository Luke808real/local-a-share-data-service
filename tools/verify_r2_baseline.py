"""Verify the R2 layout-only CNEquity baseline without mutating it."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import sqlite3
import stat
import sys
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import duckdb


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "cnequity.toml"
DEFAULT_LOCK_PATH = REPO_ROOT / "uv.lock"
UPSTREAM_REPOSITORY = "https://github.com/rootSunc/CNEquity.git"
UPSTREAM_SHA = "a18ee0484dfb0801650175471724def3228b8a17"
CNEQUITY_VERSION = "0.7.2"

EXPECTED_DIRECTORIES = (
    ".",
    "backups",
    "curated",
    "derived",
    "duckdb",
    "meta",
    "meta/adj_factors_cache",
    "meta/on_demand",
    "meta/quality",
    "meta/quality/findings",
    "meta/quality/source_diffs",
    "meta/seeds",
    "meta/source_snapshots",
    "meta/state",
    "raw",
    "staging",
)
REQUIRED_REGULAR_FILES = frozenset(
    {"meta/manifest.db", "duckdb/cnequity.duckdb"}
)
OBSERVED_ZERO_DATA_SIDECARS = frozenset()
ALLOWED_REGULAR_FILES = REQUIRED_REGULAR_FILES | OBSERVED_ZERO_DATA_SIDECARS
EMPTY_DATA_DIRECTORIES = (
    "backups",
    "curated",
    "derived",
    "meta/adj_factors_cache",
    "meta/on_demand",
    "meta/quality/findings",
    "meta/quality/source_diffs",
    "meta/seeds",
    "meta/source_snapshots",
    "meta/state",
    "raw",
    "staging",
)
EXPECTED_DUCKDB_VIEWS = (
    "adj_factors",
    "analyst_consensus",
    "announcement_index",
    "block_trades",
    "commodity_bars",
    "corporate_actions",
    "daily_bars",
    "daily_bars_adj",
    "delisting_events",
    "dragon_tiger",
    "earnings_disclosure_schedule",
    "economic_calendar",
    "financial_statement_items",
    "flash_news_wire",
    "fund_flow",
    "hot_rank",
    "index_bars",
    "index_constituents",
    "industry_index",
    "industry_members",
    "institutional_holdings",
    "instruments",
    "macro_indicators",
    "margin_trading",
    "market_breadth",
    "minute_bars",
    "minute_bars_5m",
    "news_headlines",
    "northbound_flows",
    "northbound_holdings",
    "regulatory_events",
    "sector_bars",
    "sector_fund_flow",
    "sector_members",
    "sentiment_scores",
    "share_structure",
    "share_unlock_schedule",
    "shareholder_counts",
    "top_holders",
    "trade_ticks",
    "trading_calendar",
    "trading_status",
    "valuation_metrics",
)
REQUIRED_MANIFEST_TABLES = frozenset({"ingestion_runs", "ingestion_batches"})


class VerificationError(RuntimeError):
    """The target root violates the R2 zero-data baseline contract."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_type(st: os.stat_result) -> str:
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISREG(st.st_mode):
        return "file"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    return "special"


def _require_regular_file(path: Path) -> os.stat_result:
    try:
        st = os.lstat(path)
    except FileNotFoundError as exc:
        raise VerificationError(f"required file is missing: {path}") from exc
    if stat.S_ISLNK(st.st_mode):
        raise VerificationError(f"required file is a symlink: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise VerificationError(f"required path is not a regular file: {path}")
    return st


def _validate_root_path(root: Path) -> Path:
    if not root.is_absolute():
        raise VerificationError(f"data.root is not absolute: {root}")
    if os.path.normpath(os.fspath(root)) != os.fspath(root):
        raise VerificationError(f"data.root is not lexically normalized: {root}")

    current = Path(root.anchor)
    try:
        root_anchor = os.lstat(current)
    except FileNotFoundError as exc:
        raise VerificationError(f"data.root anchor is missing: {current}") from exc
    if stat.S_ISLNK(root_anchor.st_mode) or not stat.S_ISDIR(root_anchor.st_mode):
        raise VerificationError(f"unsafe data.root anchor: {current}")

    for component in root.parts[1:]:
        current /= component
        try:
            st = os.lstat(current)
        except FileNotFoundError as exc:
            raise VerificationError(f"data.root component is missing: {current}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise VerificationError(f"data.root contains a symlink component: {current}")
        if not stat.S_ISDIR(st.st_mode):
            raise VerificationError(f"data.root component is not a directory: {current}")

    resolved = root.resolve(strict=True)
    if resolved != root:
        raise VerificationError(f"data.root resolves differently: {root} -> {resolved}")
    return resolved


def snapshot_tree(root: Path) -> dict[str, dict[str, object]]:
    """Capture all target-root entries, metadata, and regular-file hashes."""
    root = _validate_root_path(Path(root))
    snapshot: dict[str, dict[str, object]] = {}

    def visit(path: Path, relative: Path) -> None:
        st = os.lstat(path)
        kind = _record_type(st)
        relative_key = relative.as_posix()
        if kind == "symlink":
            raise VerificationError(f"symlink is forbidden: {relative_key}")
        if kind == "special":
            raise VerificationError(f"special filesystem entry is forbidden: {relative_key}")

        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise VerificationError(f"path escapes data.root: {relative_key}") from exc

        record: dict[str, object] = {
            "type": kind,
            "size": st.st_size,
            "inode": st.st_ino,
            "mode": stat.S_IMODE(st.st_mode),
            "mtime_ns": st.st_mtime_ns,
            "ctime_ns": st.st_ctime_ns,
        }
        if kind == "file":
            record["sha256"] = _sha256_file(path)
        else:
            with os.scandir(path) as entries:
                record["entries"] = sorted(entry.name for entry in entries)
        snapshot[relative_key] = record

        if kind == "directory":
            for entry_name in record["entries"]:
                visit(path / entry_name, relative / entry_name)

    visit(root, Path("."))
    return snapshot


def _snapshot_digest(snapshot: dict[str, dict[str, object]]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_layout(snapshot: dict[str, dict[str, object]]) -> None:
    actual_directories = {
        path for path, record in snapshot.items() if record["type"] == "directory"
    }
    expected_directories = set(EXPECTED_DIRECTORIES)
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        unexpected = sorted(actual_directories - expected_directories)
        raise VerificationError(
            f"directory layout mismatch: missing={missing}, unexpected={unexpected}"
        )

    actual_files = {path for path, record in snapshot.items() if record["type"] == "file"}
    missing_files = sorted(REQUIRED_REGULAR_FILES - actual_files)
    if missing_files:
        raise VerificationError(f"required regular file(s) missing: {missing_files}")
    unexpected_files = sorted(actual_files - ALLOWED_REGULAR_FILES)
    if unexpected_files:
        raise VerificationError(f"unexpected regular file(s): {unexpected_files}")

    for directory in EMPTY_DATA_DIRECTORIES:
        entries = snapshot[directory]["entries"]
        if entries:
            raise VerificationError(f"zero-data directory is not empty: {directory}: {entries}")


def _read_config_root(config_path: Path) -> Path:
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise VerificationError(f"configuration is missing: {config_path}") from exc
    try:
        root_text = config["data"]["root"]
    except (KeyError, TypeError) as exc:
        raise VerificationError("configuration has no [data].root") from exc
    if not isinstance(root_text, str):
        raise VerificationError("configuration [data].root must be a string")
    return Path(root_text)


def _verify_manifest(manifest_path: Path) -> dict[str, object]:
    _require_regular_file(manifest_path)
    wal_path = manifest_path.with_name(f"{manifest_path.name}-wal")
    if wal_path.exists() and _require_regular_file(wal_path).st_size > 0:
        raise VerificationError(f"non-empty SQLite WAL must not be ignored: {wal_path}")

    sqlite_uri = f"file:{quote(manifest_path.as_posix())}?mode=ro&immutable=1"
    connection = sqlite3.connect(sqlite_uri, uri=True)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        if set(tables) != REQUIRED_MANIFEST_TABLES:
            raise VerificationError(f"unexpected manifest schema tables: {tables}")
        run_count = connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0]
        batch_count = connection.execute("SELECT COUNT(*) FROM ingestion_batches").fetchone()[0]
    finally:
        connection.close()

    if run_count != 0 or batch_count != 0:
        raise VerificationError(
            f"manifest is not zero-data: ingestion_runs={run_count}, ingestion_batches={batch_count}"
        )
    return {
        "tables": tables,
        "ingestion_runs": run_count,
        "ingestion_batches": batch_count,
    }


def _reject_nonempty_manifest_wal(manifest_path: Path) -> None:
    wal_path = manifest_path.with_name(f"{manifest_path.name}-wal")
    if wal_path.exists() and _require_regular_file(wal_path).st_size > 0:
        raise VerificationError(f"non-empty SQLite WAL must not be ignored: {wal_path}")


def _verify_duckdb(duckdb_path: Path) -> dict[str, object]:
    _require_regular_file(duckdb_path)
    connection = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        catalog_rows = connection.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
    finally:
        connection.close()

    views = [name for name, table_type in catalog_rows if table_type == "VIEW"]
    physical_tables = [name for name, table_type in catalog_rows if table_type != "VIEW"]
    if physical_tables:
        raise VerificationError(f"DuckDB contains physical tables: {physical_tables}")
    if tuple(views) != EXPECTED_DUCKDB_VIEWS:
        raise VerificationError(f"DuckDB view set mismatch: {views}")
    return {"views": views, "physical_tables": physical_tables}


def _verify_lock_and_runtime(lock_path: Path) -> dict[str, str]:
    _require_regular_file(lock_path)
    with lock_path.open("rb") as handle:
        lock = tomllib.load(handle)
    cnequity_packages = [
        package
        for package in lock.get("package", [])
        if package.get("name", "").lower() == "cnequity"
    ]
    if len(cnequity_packages) != 1:
        raise VerificationError("uv.lock must contain exactly one cnequity package")
    package = cnequity_packages[0]
    if package.get("version") != CNEQUITY_VERSION:
        raise VerificationError(f"unexpected locked cnequity version: {package.get('version')}")
    source = package.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("git"), str):
        raise VerificationError("cnequity lock source is not a Git source")
    git_source = urlsplit(source["git"])
    repository = f"{git_source.scheme}://{git_source.netloc}{git_source.path}"
    if repository != UPSTREAM_REPOSITORY:
        raise VerificationError(f"unexpected locked repository: {repository}")
    if parse_qs(git_source.query) != {"rev": [UPSTREAM_SHA]}:
        raise VerificationError("uv.lock does not request the pinned Git SHA")
    if git_source.fragment != UPSTREAM_SHA:
        raise VerificationError("uv.lock does not resolve the pinned Git SHA")

    if metadata.version("cnequity") != CNEQUITY_VERSION:
        raise VerificationError("installed cnequity metadata version does not match the pin")
    distribution = metadata.distribution("cnequity")
    direct_url_text = distribution.read_text("direct_url.json")
    if direct_url_text is None:
        raise VerificationError("installed cnequity has no direct_url.json")
    direct_url = json.loads(direct_url_text)
    vcs_info = direct_url.get("vcs_info")
    if (
        direct_url.get("url") != UPSTREAM_REPOSITORY
        or not isinstance(vcs_info, dict)
        or vcs_info.get("vcs") != "git"
        or vcs_info.get("commit_id") != UPSTREAM_SHA
        or "archive_info" in direct_url
        or direct_url.get("dir_info", {}).get("editable") is True
    ):
        raise VerificationError("installed cnequity direct_url.json is not the pinned Git origin")

    return {
        "config_sha256": "",
        "lock_sha256": _sha256_file(lock_path),
        "package_version": CNEQUITY_VERSION,
        "direct_url_commit": UPSTREAM_SHA,
    }


def verify_baseline(config_path: Path, lock_path: Path | None = None) -> dict[str, object]:
    """Verify one configuration's root without writing to it."""
    config_path = Path(config_path)
    lock_path = DEFAULT_LOCK_PATH if lock_path is None else Path(lock_path)
    root = _read_config_root(config_path)
    before = snapshot_tree(root)
    _reject_nonempty_manifest_wal(root / "meta" / "manifest.db")
    _assert_layout(before)

    state_files = sorted(path for path in before if path.startswith("meta/state/"))
    if state_files:
        raise VerificationError(f"published/latest-good state is forbidden: {state_files}")

    manifest = _verify_manifest(root / "meta" / "manifest.db")
    duckdb_catalog = _verify_duckdb(root / "duckdb" / "cnequity.duckdb")
    runtime = _verify_lock_and_runtime(lock_path)
    runtime["config_sha256"] = _sha256_file(config_path)

    after = snapshot_tree(root)
    if before != after:
        changed = sorted(
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        )
        raise VerificationError(f"verifier changed target tree: {changed}")

    sidecars = sorted(
        path for path in before if path in ALLOWED_REGULAR_FILES - REQUIRED_REGULAR_FILES
    )
    return {
        "root": str(root),
        "config_sha256": runtime["config_sha256"],
        "lock_sha256": runtime["lock_sha256"],
        "package_version": runtime["package_version"],
        "direct_url_commit": runtime["direct_url_commit"],
        "manifest": manifest,
        "duckdb": duckdb_catalog,
        "published_state": {"state_files": state_files},
        "tree": {
            "entries": len(before),
            "before_sha256": _snapshot_digest(before),
            "after_sha256": _snapshot_digest(after),
            "sidecars": sidecars,
        },
        "tree_unchanged": True,
        "zero_market_data": True,
    }


def assert_doctor_probe_transition(
    before: dict[str, dict[str, object]], after: dict[str, dict[str, object]]
) -> dict[str, int]:
    """Allow only root directory timestamps to change during a doctor probe."""
    probe_key = ".cne_write_probe"
    if probe_key in before or probe_key in after:
        raise VerificationError("doctor write probe remains in the target tree")
    before_without_root = {path: record for path, record in before.items() if path != "."}
    after_without_root = {path: record for path, record in after.items() if path != "."}
    if before_without_root != after_without_root:
        changed = sorted(
            path
            for path in set(before_without_root) | set(after_without_root)
            if before_without_root.get(path) != after_without_root.get(path)
        )
        raise VerificationError(f"doctor changed target entries or file content: {changed}")

    root_before = dict(before["."])
    root_after = dict(after["."])
    root_before.pop("mtime_ns")
    root_before.pop("ctime_ns")
    root_after.pop("mtime_ns")
    root_after.pop("ctime_ns")
    if root_before != root_after:
        raise VerificationError("doctor changed root metadata beyond timestamps")
    if (
        before["."]["mtime_ns"] == after["."]["mtime_ns"]
        and before["."]["ctime_ns"] == after["."]["ctime_ns"]
    ):
        raise VerificationError("doctor did not change root mtime/ctime as expected")
    return {
        "root_mtime_ns_before": int(before["."]["mtime_ns"]),
        "root_mtime_ns_after": int(after["."]["mtime_ns"]),
        "root_ctime_ns_before": int(before["."]["ctime_ns"]),
        "root_ctime_ns_after": int(after["."]["ctime_ns"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args()

    try:
        if args.snapshot:
            root = _read_config_root(args.config)
            snapshot = snapshot_tree(root)
            output: dict[str, object] = {
                "root": str(root),
                "snapshot": snapshot,
                "snapshot_sha256": _snapshot_digest(snapshot),
            }
        else:
            output = verify_baseline(args.config)
    except VerificationError as exc:
        print(json.dumps({"error": str(exc), "status": "FAIL"}), file=sys.stderr)
        return 1

    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
