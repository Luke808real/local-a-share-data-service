import ast
from datetime import date
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/r1_asset_audit.py"

LEGACY_ROOTS = {
    "/Users/luke808/AI/asl-shared",
    "/Users/luke808/AI/asl-r8-5m-lake",
    "/Users/luke808/AI/V flash/data",
}


def load_audit_module():
    spec = importlib.util.spec_from_file_location("r1_asset_audit", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def tree_snapshot(root: Path) -> tuple[tuple[str, int], ...]:
    return tuple(
        sorted(
            (path.relative_to(root).as_posix(), path.stat().st_size)
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_audit_script_exists():
    assert SCRIPT.is_file()


def test_legacy_roots_are_declared_inputs():
    text = SCRIPT.read_text(encoding="utf-8")
    for root in LEGACY_ROOTS:
        assert root in text


def test_missing_root_returns_empty_readonly_inventory(tmp_path):
    audit = load_audit_module()
    missing = tmp_path / "does-not-exist"
    before = tree_snapshot(tmp_path)

    root = audit.inventory_root(missing)
    datasets = audit.discover_datasets(missing)

    assert root.exists is False
    assert root.file_count == 0
    assert datasets == ()
    assert tree_snapshot(tmp_path) == before


def test_synthetic_inventory_does_not_create_files_inside_target(tmp_path):
    audit = load_audit_module()
    target = tmp_path / "legacy"
    dataset = target / "daily_bars"
    dataset.mkdir(parents=True)
    (dataset / "part-000.parquet").write_bytes(b"not-a-parquet-footer")
    before = tree_snapshot(target)

    root = audit.inventory_root(target)
    datasets = audit.discover_datasets(target, max_depth=2)

    assert root.exists is True
    assert root.file_count == 1
    assert len(datasets) == 1
    record = datasets[0]
    assert record.dataset == "daily_bars"
    assert record.storage_kind == "PARQUET"
    assert record.file_count == 1
    assert record.total_bytes == len(b"not-a-parquet-footer")
    assert record.row_count_or_unknown == "UNKNOWN"
    assert record.symbol_count_or_unknown == "UNKNOWN"
    assert record.reader_status in {"READER_UNAVAILABLE", "READER_ERROR"}
    assert tree_snapshot(target) == before


def test_footer_categorical_provenance_requires_constant_values(tmp_path):
    pyarrow = pytest.importorskip("pyarrow")
    parquet = pytest.importorskip("pyarrow.parquet")
    audit = load_audit_module()
    target = tmp_path / "legacy"
    dataset = target / "daily_bars"
    dataset.mkdir(parents=True)
    parquet.write_table(
        pyarrow.table(
            {
                "symbol": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
                "source": ["alpha", "beta"],
                "data_version": ["v1", "v1"],
            }
        ),
        dataset / "part-000.parquet",
    )
    before = tree_snapshot(target)

    record = audit.discover_datasets(target, max_depth=2)[0]

    assert record.reader_status == "FOOTER_METADATA"
    assert record.source_values_or_unknown == audit.UNKNOWN
    assert record.data_version_values_or_unknown == ("v1",)
    assert tree_snapshot(target) == before


def test_cli_root_selection_is_explicit_and_does_not_default_to_legacy_roots():
    audit = load_audit_module()
    args = audit.build_parser().parse_args([])

    assert audit.selected_roots(args) == ()
    with pytest.raises(SystemExit):
        audit.main([])


def test_script_does_not_use_obvious_destructive_filesystem_calls():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    banned = {
        "unlink",
        "remove",
        "rmdir",
        "rename",
        "replace",
        "write_text",
        "write_bytes",
        "mkdir",
        "touch",
    }
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in banned:
            offenders.append(node.attr)
    assert offenders == []


def test_script_does_not_import_or_connect_to_sqlite_or_duckdb():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imports = []
    connect_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, ast.Attribute) and node.attr == "connect":
            connect_calls.append(ast.unparse(node))

    assert not any(name.split(".")[0] in {"sqlite3", "duckdb"} for name in imports)
    assert connect_calls == []
