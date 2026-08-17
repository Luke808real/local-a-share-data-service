from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
from typing import Iterable, Sequence


LEGACY_ROOTS = (
    Path("/Users/luke808/AI/asl-shared"),
    Path("/Users/luke808/AI/asl-r8-5m-lake"),
    Path("/Users/luke808/AI/V flash/data"),
)

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_FOOTER_FILES = 16
UNKNOWN = "UNKNOWN"
READER_UNAVAILABLE = "READER_UNAVAILABLE"


@dataclass(frozen=True)
class RootInventory:
    path: str
    exists: bool
    file_count: int
    total_bytes: int
    top_level_entries: tuple[str, ...]
    scan_max_depth: int


@dataclass(frozen=True)
class DatasetInventory:
    root: str
    dataset: str
    storage_path: str
    storage_kind: str
    file_count: int
    total_bytes: int
    row_count_or_unknown: int | str
    symbol_count_or_unknown: str
    min_date_or_unknown: str
    max_date_or_unknown: str
    schema_or_unknown: str
    source_values_or_unknown: tuple[str, ...] | str
    data_version_values_or_unknown: tuple[str, ...] | str
    reader_status: str
    footer_files_inspected: int
    limitations: tuple[str, ...]


DATASET_DIRECTORY_ALIASES = {
    "adj_factors": "adj_factors",
    "adjustment_factor": "adj_factors",
    "corporate_actions": "corporate_actions",
    "daily_bars": "daily_bars",
    "daily_basic": "daily_basic",
    "float_shares": "float_shares",
    "index": "index",
    "index_bars": "index",
    "industry": "industry",
    "instruments": "instruments",
    "limit_up_pool": "limit_up_pool",
    "minute_bars": "minute_bars",
    "minute_bars_5m": "minute_bars_5m",
    "price_limits": "price_limits",
    "suspension": "suspension",
    "trading_calendar": "trading_calendar",
    "trading_status": "trading_status",
    "turnover": "turnover",
}


def _bounded_walk_files(root: Path, max_depth: int) -> Iterable[Path]:
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")

    for directory, child_directories, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        directory_path = Path(directory)
        relative = directory_path.relative_to(root)
        depth = len(relative.parts)
        child_directories[:] = [
            child
            for child in child_directories
            if not (directory_path / child).is_symlink()
        ]
        if depth >= max_depth:
            child_directories[:] = []
        for filename in filenames:
            path = directory_path / filename
            if not path.is_symlink():
                yield path


def _top_level_entries(root: Path) -> tuple[str, ...]:
    try:
        return tuple(sorted(path.name for path in root.iterdir()))
    except OSError:
        return ()


def inventory_root(root: Path, max_depth: int = DEFAULT_MAX_DEPTH) -> RootInventory:
    if not root.exists():
        return RootInventory(str(root), False, 0, 0, (), max_depth)

    file_count = 0
    total_bytes = 0
    for path in _bounded_walk_files(root, max_depth):
        try:
            stat = path.stat()
        except OSError:
            continue
        file_count += 1
        total_bytes += stat.st_size
    return RootInventory(
        path=str(root),
        exists=True,
        file_count=file_count,
        total_bytes=total_bytes,
        top_level_entries=_top_level_entries(root),
        scan_max_depth=max_depth,
    )


def _dataset_identity(root: Path, path: Path) -> tuple[str, Path]:
    relative_parts = path.relative_to(root).parts[:-1]
    for index in range(len(relative_parts) - 1, -1, -1):
        alias = DATASET_DIRECTORY_ALIASES.get(relative_parts[index].lower())
        if alias:
            return alias, root.joinpath(*relative_parts[: index + 1])
    return "unclassified_parquet", path.parent


def _normalise_scalar(value: object) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, (str, int, float, date, datetime)):
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    return None


def _normalise_date(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return None


def _footer_column_values(
    metadata: object,
    field_names: tuple[str, ...],
    value_normaliser: object,
    *,
    constant_only: bool = False,
) -> tuple[tuple[str, ...] | None, bool]:
    values: list[str] = []
    field_seen = False
    complete = True
    for row_group_index in range(metadata.num_row_groups):
        row_group = metadata.row_group(row_group_index)
        matching_columns = [
            row_group.column(column_index)
            for column_index in range(row_group.num_columns)
            if row_group.column(column_index).path_in_schema in field_names
        ]
        if not matching_columns:
            complete = False
            continue
        field_seen = True
        if len(matching_columns) != 1:
            complete = False
            continue
        statistics = matching_columns[0].statistics
        if statistics is None or not statistics.has_min_max:
            complete = False
            continue
        minimum = value_normaliser(statistics.min)
        maximum = value_normaliser(statistics.max)
        if minimum is None or maximum is None:
            complete = False
            continue
        if constant_only:
            if minimum != maximum:
                complete = False
                continue
            values.append(minimum)
        else:
            values.extend((minimum, maximum))
    if not field_seen or not complete or not values:
        return None, False
    return tuple(values), True


def _footer_metadata(
    files: Sequence[Path], max_footer_files: int
) -> dict[str, object]:
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-not-found]
    except ImportError:
        return {
            "row_count_or_unknown": UNKNOWN,
            "min_date_or_unknown": UNKNOWN,
            "max_date_or_unknown": UNKNOWN,
            "schema_or_unknown": UNKNOWN,
            "source_values_or_unknown": UNKNOWN,
            "data_version_values_or_unknown": UNKNOWN,
            "reader_status": READER_UNAVAILABLE,
            "footer_files_inspected": 0,
            "limitations": ("PyArrow parquet footer reader is unavailable.",),
        }

    selected_files = tuple(files[:max_footer_files])
    if not selected_files:
        return {
            "row_count_or_unknown": UNKNOWN,
            "min_date_or_unknown": UNKNOWN,
            "max_date_or_unknown": UNKNOWN,
            "schema_or_unknown": UNKNOWN,
            "source_values_or_unknown": UNKNOWN,
            "data_version_values_or_unknown": UNKNOWN,
            "reader_status": "NO_PARQUET_FILES",
            "footer_files_inspected": 0,
            "limitations": (),
        }

    row_count = 0
    schemas: set[str] = set()
    date_values: list[str] = []
    source_values: set[str] = set()
    data_version_values: set[str] = set()
    dates_complete = True
    sources_complete = True
    versions_complete = True
    errors: list[str] = []

    for path in selected_files:
        try:
            parquet_file = parquet.ParquetFile(path)
            metadata = parquet_file.metadata
            row_count += metadata.num_rows
            schemas.add(str(parquet_file.schema_arrow))
            dates, date_complete = _footer_column_values(
                metadata, ("trade_date", "date"), _normalise_date
            )
            sources, source_complete = _footer_column_values(
                metadata,
                ("source", "provider"),
                _normalise_scalar,
                constant_only=True,
            )
            versions, version_complete = _footer_column_values(
                metadata,
                ("data_version", "provider_version"),
                _normalise_scalar,
                constant_only=True,
            )
        except Exception as error:  # Reader failures are reported, never repaired.
            errors.append(f"{path.name}: {type(error).__name__}")
            dates_complete = False
            sources_complete = False
            versions_complete = False
            continue

        dates_complete = dates_complete and date_complete
        sources_complete = sources_complete and source_complete
        versions_complete = versions_complete and version_complete
        if dates:
            date_values.extend(dates)
        if sources:
            source_values.update(sources)
        if versions:
            data_version_values.update(versions)

    all_files_inspected = len(selected_files) == len(files)
    if errors:
        return {
            "row_count_or_unknown": UNKNOWN,
            "min_date_or_unknown": UNKNOWN,
            "max_date_or_unknown": UNKNOWN,
            "schema_or_unknown": UNKNOWN,
            "source_values_or_unknown": UNKNOWN,
            "data_version_values_or_unknown": UNKNOWN,
            "reader_status": "READER_ERROR",
            "footer_files_inspected": len(selected_files),
            "limitations": tuple(errors),
        }

    limitations: list[str] = []
    if not all_files_inspected:
        limitations.append(
            f"Footer inspection capped at {max_footer_files} of {len(files)} files."
        )
    return {
        "row_count_or_unknown": row_count if all_files_inspected else UNKNOWN,
        "min_date_or_unknown": (
            min(date_values) if all_files_inspected and dates_complete else UNKNOWN
        ),
        "max_date_or_unknown": (
            max(date_values) if all_files_inspected and dates_complete else UNKNOWN
        ),
        "schema_or_unknown": (
            " | ".join(sorted(schemas))
            if all_files_inspected and len(schemas) == 1
            else UNKNOWN
        ),
        "source_values_or_unknown": (
            tuple(sorted(source_values))
            if all_files_inspected and sources_complete
            else UNKNOWN
        ),
        "data_version_values_or_unknown": (
            tuple(sorted(data_version_values))
            if all_files_inspected and versions_complete
            else UNKNOWN
        ),
        "reader_status": "FOOTER_METADATA",
        "footer_files_inspected": len(selected_files),
        "limitations": tuple(limitations),
    }


def discover_datasets(
    root: Path,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_footer_files: int = DEFAULT_MAX_FOOTER_FILES,
) -> tuple[DatasetInventory, ...]:
    if not root.exists():
        return ()
    if max_footer_files <= 0:
        raise ValueError("max_footer_files must be positive")

    parquet_groups: dict[tuple[str, Path], list[Path]] = {}
    database_files: list[Path] = []
    for path in _bounded_walk_files(root, max_depth):
        if path.suffix.lower() == ".parquet":
            dataset, storage_path = _dataset_identity(root, path)
            parquet_groups.setdefault((dataset, storage_path), []).append(path)
        elif path.suffix.lower() in {".db", ".duckdb", ".sqlite"}:
            database_files.append(path)

    inventories: list[DatasetInventory] = []
    for (dataset, storage_path), files in sorted(
        parquet_groups.items(), key=lambda item: (item[0][0], str(item[0][1]))
    ):
        sorted_files = tuple(sorted(files))
        metadata = _footer_metadata(sorted_files, max_footer_files)
        inventories.append(
            DatasetInventory(
                root=str(root),
                dataset=dataset,
                storage_path=str(storage_path),
                storage_kind="PARQUET",
                file_count=len(sorted_files),
                total_bytes=sum(path.stat().st_size for path in sorted_files),
                symbol_count_or_unknown=UNKNOWN,
                **metadata,
            )
        )

    for path in sorted(database_files):
        inventories.append(
            DatasetInventory(
                root=str(root),
                dataset=path.stem,
                storage_path=str(path),
                storage_kind="DATABASE_METADATA_ONLY",
                file_count=1,
                total_bytes=path.stat().st_size,
                row_count_or_unknown=UNKNOWN,
                symbol_count_or_unknown=UNKNOWN,
                min_date_or_unknown=UNKNOWN,
                max_date_or_unknown=UNKNOWN,
                schema_or_unknown=UNKNOWN,
                source_values_or_unknown=UNKNOWN,
                data_version_values_or_unknown=UNKNOWN,
                reader_status="DATABASE_NOT_OPENED",
                footer_files_inspected=0,
                limitations=("SQLite and DuckDB files are metadata-only and not opened.",),
            )
        )
    return tuple(inventories)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bounded, read-only inventory of explicitly selected legacy roots."
    )
    parser.add_argument("--root", action="append", type=Path, default=[])
    parser.add_argument("--all-legacy-roots", action="store_true")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument(
        "--max-footer-files", type=int, default=DEFAULT_MAX_FOOTER_FILES
    )
    return parser


def selected_roots(args: argparse.Namespace) -> tuple[Path, ...]:
    roots = list(args.root)
    if args.all_legacy_roots:
        roots.extend(LEGACY_ROOTS)
    return tuple(dict.fromkeys(roots))


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    roots = selected_roots(args)
    if not roots:
        parser.error("select at least one --root or pass --all-legacy-roots")
    if args.max_depth < 0:
        parser.error("--max-depth must be non-negative")
    if args.max_footer_files <= 0:
        parser.error("--max-footer-files must be positive")

    payload = {
        "roots": [
            {
                "root": asdict(inventory_root(root, args.max_depth)),
                "datasets": [
                    asdict(record)
                    for record in discover_datasets(
                        root, args.max_depth, args.max_footer_files
                    )
                ],
            }
            for root in roots
        ]
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
