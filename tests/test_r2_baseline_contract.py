import ast
import importlib
import importlib.util
import importlib.metadata as metadata
import json
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_REPOSITORY = "https://github.com/rootSunc/CNEquity.git"
UPSTREAM_SHA = "a18ee0484dfb0801650175471724def3228b8a17"
CNEQUITY_VERSION = "0.7.2"
DATA_ROOT = Path("/Users/luke808/AI/local-a-share-data-service-data")
LEGACY_ROOTS = (
    Path("/Users/luke808/AI/asl-shared"),
    Path("/Users/luke808/AI/asl-r8-5m-lake"),
    Path("/Users/luke808/AI/V flash/data"),
)
CONFIG_PATH = ROOT / "config" / "cnequity.toml"
CONTRACT_PATH = ROOT / "docs" / "contracts" / "R2_CNEQUITY_BASELINE_CONTRACT.md"
VERIFIER_PATH = ROOT / "tools" / "verify_r2_baseline.py"
PINNED_REQUIREMENT = (
    "cnequity @ git+https://github.com/rootSunc/CNEquity.git@"
    f"{UPSTREAM_SHA}"
)
FORBIDDEN_DIRECT_DEPENDENCY_TERMS = ("strategy", "mcp", "scheduler")
FORBIDDEN_CONFIG_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "proxy",
    "proxies",
    "secret",
    "token",
}


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _mapping_keys(value: object) -> set[str]:
    if not isinstance(value, dict):
        return set()
    keys = set(value)
    for child in value.values():
        keys.update(_mapping_keys(child))
    return keys


def _load_r2_baseline_verifier():
    assert VERIFIER_PATH.is_file()
    spec = importlib.util.spec_from_file_location("r2_baseline_verifier", VERIFIER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_synthetic_zero_data_baseline(tmp_path: Path, verifier):
    root = tmp_path / "zero-data-root"
    for relative_dir in sorted(
        verifier.EXPECTED_DIRECTORIES,
        key=lambda value: (len(Path(value).parts), value),
    ):
        (root / relative_dir).mkdir(parents=True, exist_ok=True)

    manifest_path = root / "meta" / "manifest.db"
    with sqlite3.connect(manifest_path) as connection:
        connection.execute(
            "CREATE TABLE ingestion_runs (run_id TEXT PRIMARY KEY, status TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE ingestion_batches (run_id TEXT NOT NULL, batch_id TEXT NOT NULL)"
        )

    import duckdb

    duckdb_path = root / "duckdb" / "cnequity.duckdb"
    connection = duckdb.connect(str(duckdb_path))
    try:
        for view_name in verifier.EXPECTED_DUCKDB_VIEWS:
            quoted_name = '"' + view_name.replace('"', '""') + '"'
            connection.execute(
                f"CREATE VIEW {quoted_name} AS "
                "SELECT CAST(NULL AS VARCHAR) AS _empty WHERE FALSE"
            )
    finally:
        connection.close()

    config_path = tmp_path / "cnequity.toml"
    config_path.write_text(
        f'[data]\nroot = "{root.as_posix()}"\n',
        encoding="utf-8",
    )
    return root, config_path


def test_python_version_selects_cpython_312_line():
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_project_metadata_pins_only_the_frozen_cnequity_runtime():
    project = _read_toml(ROOT / "pyproject.toml")

    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert project["project"]["dependencies"] == [PINNED_REQUIREMENT]
    assert project["dependency-groups"]["dev"] == ["pytest==8.4.2"]

    direct_dependencies = [
        *project["project"]["dependencies"],
        *project["dependency-groups"]["dev"],
    ]
    direct_dependency_text = "\n".join(direct_dependencies).lower()
    assert not any(
        term in direct_dependency_text for term in FORBIDDEN_DIRECT_DEPENDENCY_TERMS
    )


def test_lock_resolves_frozen_cnequity_git_commit():
    lock = _read_toml(ROOT / "uv.lock")
    cnequity_packages = [
        package
        for package in lock["package"]
        if package["name"].lower() == "cnequity"
    ]

    assert len(cnequity_packages) == 1
    package = cnequity_packages[0]
    assert package["version"] == CNEQUITY_VERSION

    source = package["source"]
    assert isinstance(source, dict)
    git_source = urlsplit(source["git"])
    assert f"{git_source.scheme}://{git_source.netloc}{git_source.path}" == (
        UPSTREAM_REPOSITORY
    )
    assert parse_qs(git_source.query) == {"rev": [UPSTREAM_SHA]}
    assert git_source.fragment == UPSTREAM_SHA


def test_lock_is_frozen_and_offline_checkable():
    result = subprocess.run(
        ["uv", "lock", "--check", "--offline"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_installed_cnequity_metadata_and_module_version_match_pin():
    assert metadata.version("cnequity") == CNEQUITY_VERSION

    cnequity = importlib.import_module("cnequity")
    assert cnequity.__version__ == CNEQUITY_VERSION


def test_installed_cnequity_direct_url_proves_immutable_git_origin():
    distribution = metadata.distribution("cnequity")
    direct_url_text = distribution.read_text("direct_url.json")

    assert direct_url_text is not None
    direct_url = json.loads(direct_url_text)
    assert direct_url["url"] == UPSTREAM_REPOSITORY
    assert "archive_info" not in direct_url
    assert direct_url.get("dir_info", {}).get("editable") is not True

    vcs_info = direct_url.get("vcs_info")
    assert isinstance(vcs_info, dict)
    assert vcs_info.get("vcs") == "git"
    assert vcs_info.get("commit_id") == UPSTREAM_SHA


def test_config_freezes_only_the_bounded_r2_runtime_surface():
    config = _read_toml(CONFIG_PATH)

    assert set(config) == {
        "data",
        "orchestrator",
        "tdx_protocol",
        "sources",
        "adj_factors",
        "universe",
        "minute_bars",
        "trade_ticks",
        "job",
        "on_demand",
        "duckdb",
    }
    assert config["data"] == {"root": str(DATA_ROOT)}
    assert config["orchestrator"] == {
        "workers": 1,
        "batch_size": 50,
        "max_retries": 3,
        "retry_backoff_seconds": 5,
        "batch_stale_seconds": 3600,
    }
    assert config["tdx_protocol"] == {
        "enabled": True,
        "min_interval_ms": 100,
        "lock_timeout_sec": 15.0,
        "servers": "auto",
        "connect_timeout_sec": 10,
        "allow_mock": False,
    }
    assert config["sources"] == {
        "eastmoney": {
            "enabled": True,
            "min_interval_seconds": 0.5,
            "timeout_sec": 30,
        },
        "sina": {"enabled": True, "min_interval_seconds": 0.3},
        "baostock": {
            "enabled": True,
            "min_interval_seconds": 1.0,
            "batch_size": 20,
            "batch_rest_seconds": 120,
        },
    }
    assert config["adj_factors"] == {"source": "sina", "adjust_types": ["hfq"]}
    assert config["universe"] == {"default": "all_a"}
    assert config["minute_bars"] == {
        "enabled": False,
        "scope": "all",
        "symbols": [],
        "frequencies": ["5m"],
        "fetch_workers": 1,
    }
    assert config["trade_ticks"] == {
        "enabled": False,
        "scope": "watchlist",
        "symbols": [],
        "max_symbols": 200,
        "fetch_workers": 1,
    }
    assert config["on_demand"] == {"enabled": False, "datasets": []}
    assert config["duckdb"] == {
        "path": "{data.root}/duckdb/cnequity.duckdb",
        "memory_limit": "1GB",
        "threads": 1,
    }

    config_keys = {key.lower().replace("-", "_") for key in _mapping_keys(config)}
    assert FORBIDDEN_CONFIG_KEYS.isdisjoint(config_keys)
    assert "proxy" not in CONFIG_PATH.read_text(encoding="utf-8").lower()


def test_configured_data_root_is_statically_disjoint_from_legacy_roots():
    config_root = Path(_read_toml(CONFIG_PATH)["data"]["root"])

    assert config_root == DATA_ROOT
    for legacy_root in LEGACY_ROOTS:
        # These are lexical Path comparisons only: no exists(), stat(), or resolve().
        assert config_root != legacy_root
        assert not config_root.is_relative_to(legacy_root)


def test_config_daily_waves_are_minimal_and_pinned_validator_accepts_them():
    config = _read_toml(CONFIG_PATH)
    daily = config["job"]["daily"]
    assert "groups" not in daily
    assert [
        (wave["name"], wave["parallel"], wave["steps"])
        for wave in daily["waves"]
    ] == [
        ("reference", True, ["instruments", "trading_calendar", "trading_status"]),
        ("corporate_actions_to_bars", False, ["corporate_actions", "daily_bars"]),
        ("index", True, ["index_bars"]),
        (
            "finalize",
            False,
            ["compact", "derive_adj_factors", "derive_industry_index", "audit"],
        ),
    ]

    from cnequity.config.loader import load_config, validate_config

    assert validate_config(load_config(CONFIG_PATH)) == []


def test_config_init_phases_expand_to_registered_pinned_steps():
    config = _read_toml(CONFIG_PATH)
    configured_phases = config["job"]["init"]["phases"]["names"]

    import cnequity.steps  # noqa: F401
    from cnequity.orchestrator.init_phases import DEFAULT_INIT_PHASES, INIT_PHASE_STEPS
    from cnequity.orchestrator.registry import STEP_REGISTRY

    assert configured_phases == DEFAULT_INIT_PHASES
    for phase in configured_phases:
        assert phase in INIT_PHASE_STEPS
        expanded_steps = INIT_PHASE_STEPS[phase]
        assert expanded_steps
        assert all(step in STEP_REGISTRY for step in expanded_steps)


def test_config_duckdb_path_is_absolute_and_under_authoritative_root():
    from cnequity.config.loader import load_config

    config = _read_toml(CONFIG_PATH)
    assert Path(config["data"]["root"]).is_absolute()
    assert not DATA_ROOT.is_relative_to(ROOT)

    loaded = load_config(CONFIG_PATH)
    assert loaded.data_root == DATA_ROOT
    assert loaded.duckdb_path == DATA_ROOT / "duckdb" / "cnequity.duckdb"
    assert loaded.duckdb_path.is_relative_to(loaded.data_root)


def test_baseline_contract_records_runtime_layout_and_local_only_boundary():
    text = CONTRACT_PATH.read_text(encoding="utf-8")

    for phrase in [
        "rootSunc/CNEquity",
        "v0.7.2",
        UPSTREAM_SHA,
        "CPython 3.12",
        "uv.lock",
        "direct_url.json",
        str(DATA_ROOT),
        *(str(path) for path in LEGACY_ROOTS),
        "staging/",
        "curated/",
        "derived/",
        "raw/",
        "meta/",
        "duckdb/",
        "backups/",
        "init_data_layout()",
        "does not create `logs/`",
        "manifest.db-wal",
        "manifest.db-shm",
        "WAL",
        "DuckDB",
        "view-only",
        "zero-market-data",
        "no-legacy-reuse",
        "config hash",
        "lock hash",
        "on_demand.enabled=false",
        "not an enforceable network guard",
        "cne query --dataset",
        "--live",
        "R8",
        "RAW daily authoritative",
        "HFQ factor",
        "QFQ/HFQ query derivation",
        "D007",
        "D008",
        "no factor/data execution in R2",
    ]:
        assert phrase in text


def test_baseline_contract_freezes_fail_closed_status_and_turnover_contracts():
    normalized_text = " ".join(CONTRACT_PATH.read_text(encoding="utf-8").split()).casefold()

    for phrase in [
        "PK `(symbol, trade_date)`",
        "strict point-in-time date semantics",
        "`normal` → `NORMAL`",
        "`suspended` → `SUSPENDED`",
        "`st` → `ST`",
        "`*st` → `STAR_ST`",
        "unrecognized or missing values → `UNKNOWN`",
        "`DELISTING` remains `UNKNOWN` until an explicit R4 rule/source",
        "`is_trading` alone never determines ST or delisting",
        "Row-level `source` provenance outranks conflicting registry/catalog labels",
        "`DATA_CONFLICT`",
        "no first-non-null precedence",
        "no provider is accepted as complete in R2",
        "current-snapshot EastMoney execution",
        "Baostock historical ST backfill",
        "derived bar-gap suspension",
        "`turnover_rate`",
        "percentage points",
        "`3.25` means `3.25%`",
        "`turnover_source`",
        "`turnover_semantic`",
        "`coverage`",
        "`source`",
        "`data_version`",
        "`fetched_at`",
        "null",
        "`TURNOVER_PARTIAL`",
        "Provider selection, ingestion, and reconciliation belong to R4",
    ]:
        assert phrase.casefold() in normalized_text


def test_zero_data_verifier_accepts_clean_synthetic_layout_without_tree_change(tmp_path):
    verifier = _load_r2_baseline_verifier()
    root, config_path = _make_synthetic_zero_data_baseline(tmp_path, verifier)
    before = verifier.snapshot_tree(root)

    result = verifier.verify_baseline(config_path)

    assert result["zero_market_data"] is True
    assert result["tree_unchanged"] is True
    assert result["manifest"]["ingestion_runs"] == 0
    assert result["manifest"]["ingestion_batches"] == 0
    assert result["published_state"] == {"state_files": []}
    assert result["duckdb"]["physical_tables"] == []
    assert result["duckdb"]["views"] == sorted(verifier.EXPECTED_DUCKDB_VIEWS)
    assert result["direct_url_commit"] == UPSTREAM_SHA
    assert before == verifier.snapshot_tree(root)


@pytest.mark.parametrize(
    "relative_file",
    ["meta/source_snapshots/unexpected.json", "meta/on_demand/unexpected.json"],
)
def test_zero_data_verifier_rejects_unexpected_source_and_on_demand_files(
    tmp_path, relative_file
):
    verifier = _load_r2_baseline_verifier()
    root, config_path = _make_synthetic_zero_data_baseline(tmp_path, verifier)
    (root / relative_file).write_text("unexpected", encoding="utf-8")

    with pytest.raises(verifier.VerificationError, match="unexpected regular file"):
        verifier.verify_baseline(config_path)


def test_zero_data_verifier_rejects_nonempty_manifest_wal(tmp_path):
    verifier = _load_r2_baseline_verifier()
    root, config_path = _make_synthetic_zero_data_baseline(tmp_path, verifier)
    (root / "meta" / "manifest.db-wal").write_bytes(b"uncheckpointed")

    with pytest.raises(verifier.VerificationError, match="non-empty SQLite WAL"):
        verifier.verify_baseline(config_path)


def test_zero_data_verifier_rejects_unobserved_zero_byte_sidecars(tmp_path):
    verifier = _load_r2_baseline_verifier()
    root, config_path = _make_synthetic_zero_data_baseline(tmp_path, verifier)
    (root / "meta" / "manifest.db-shm").write_bytes(b"")

    with pytest.raises(verifier.VerificationError, match="unexpected regular file"):
        verifier.verify_baseline(config_path)


def test_zero_data_verifier_rejects_symlinks_and_path_escapes(tmp_path):
    verifier = _load_r2_baseline_verifier()
    root, config_path = _make_synthetic_zero_data_baseline(tmp_path, verifier)
    (root / "raw" / "escape").symlink_to(tmp_path / "outside")

    with pytest.raises(verifier.VerificationError, match="symlink"):
        verifier.verify_baseline(config_path)


def test_zero_data_verifier_source_has_only_read_only_database_access():
    source = VERIFIER_PATH.read_text(encoding="utf-8")
    parsed = ast.parse(source)

    imported_modules = {
        node.module
        for node in ast.walk(parsed)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(module.startswith("cnequity.") for module in imported_modules)

    names = {node.id for node in ast.walk(parsed) if isinstance(node, ast.Name)}
    assert {"Manifest", "StateStore", "ensure_duckdb_views"}.isdisjoint(names)

    sqlite_connects = [
        node
        for node in ast.walk(parsed)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sqlite3"
        and node.func.attr == "connect"
    ]
    assert len(sqlite_connects) == 1
    assert any(
        keyword.arg == "uri"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in sqlite_connects[0].keywords
    )
    assert "mode=ro&immutable=1" in source

    duckdb_connects = [
        node
        for node in ast.walk(parsed)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "duckdb"
        and node.func.attr == "connect"
    ]
    assert len(duckdb_connects) == 1
    assert any(
        keyword.arg == "read_only"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in duckdb_connects[0].keywords
    )
