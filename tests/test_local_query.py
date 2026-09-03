from __future__ import annotations

import ast
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ashare_data.local_query import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    LocalQuery,
    QueryError,
    build_daily_file_manifest,
    manifest_byte_form,
    normalize_symbol_input,
    query_status,
)


def _runtime_available() -> bool:
    return DEFAULT_DATA_ROOT.is_dir() and __import__("importlib.util").util.find_spec("duckdb") is not None


def test_symbol_normalization_does_not_guess_exchange() -> None:
    assert normalize_symbol_input("600519") == ("600519", None)
    assert normalize_symbol_input("000001.sz") == ("000001", "SZ")
    with pytest.raises(QueryError, match="six digits"):
        normalize_symbol_input("600519.SH.extra")


def test_manifest_serialization_is_deterministic(tmp_path: Path) -> None:
    daily = tmp_path / "curated/daily_bars/trade_date=2026-01-02"
    daily.mkdir(parents=True)
    (daily / "part-merged.parquet").write_bytes(b"fixture")
    first = build_daily_file_manifest(tmp_path)
    second = build_daily_file_manifest(tmp_path)
    assert first == second
    assert first["INPUT_FILE_N"] == 1
    assert first["INPUT_MANIFEST_HASH"] == __import__("hashlib").sha256(
        manifest_byte_form(first["FILES"])
    ).hexdigest()


@pytest.mark.skipif(not _runtime_available(), reason="local data root or DuckDB unavailable")
def test_known_sh_sz_and_bare_symbols() -> None:
    with LocalQuery() as query:
        assert query.resolve_symbol("600519.SH") == "600519.SH"
        assert query.resolve_symbol("000001") == "000001.SZ"
        assert query.resolve_symbol("002165.SZ") == "002165.SZ"


@pytest.mark.skipif(not _runtime_available(), reason="local data root or DuckDB unavailable")
def test_bars_are_bounded_and_json_serializable() -> None:
    with LocalQuery() as query:
        result = query.bars("600519.SH", "2026-08-01", "2026-08-31")
    assert result["symbol"] == "600519.SH"
    assert all("preclose" not in row for row in result["rows"])
    assert all("trading_status" not in row for row in result["rows"])
    assert all("2026-08-31" >= row["trade_date"] >= "2026-08-01" for row in result["rows"])
    json.dumps(result, ensure_ascii=False, sort_keys=True)


@pytest.mark.skipif(not _runtime_available(), reason="local data root or DuckDB unavailable")
def test_future_and_before_history_ranges_return_no_rows() -> None:
    with LocalQuery() as query:
        future = query.bars("600519.SH", "2030-01-01", "2030-01-31")
        before_history = query.bars("600519.SH", "2010-01-01", "2015-12-31")
    assert future["rows"] == []
    assert before_history["rows"] == []


@pytest.mark.skipif(not _runtime_available(), reason="local data root or DuckDB unavailable")
def test_cli_json_output_is_structured() -> None:
    cli = Path(__file__).resolve().parents[1] / "tools/query_local_a_share.py"
    completed = subprocess.run(
        [sys.executable, str(cli), "latest", "--symbol", "002165.SZ", "--limit", "1", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["command"] == "latest"
    assert payload["row_n"] == 1
    assert payload["rows"][0]["symbol"] == "002165.SZ"


@pytest.mark.skipif(not _runtime_available(), reason="local data root or DuckDB unavailable")
def test_latest_is_descending_and_instrument_is_identity_only() -> None:
    with LocalQuery() as query:
        latest = query.latest("002165.SZ", 5)
        instrument = query.instrument("002165.SZ")
    dates = [row["trade_date"] for row in latest["rows"]]
    assert dates == sorted(dates, reverse=True)
    assert instrument["instrument"]["symbol"] == "002165.SZ"
    assert "preclose" not in instrument["instrument"]


@pytest.mark.skipif(not _runtime_available(), reason="local data root or DuckDB unavailable")
def test_unknown_symbol_fails_closed() -> None:
    with LocalQuery() as query:
        with pytest.raises(QueryError) as exc_info:
            query.latest("999999.SZ", 1)
    assert exc_info.value.code == "UNKNOWN_SYMBOL"


@pytest.mark.skipif(not _runtime_available(), reason="local data root or DuckDB unavailable")
def test_status_reports_current_readiness_truthfully() -> None:
    value = query_status()
    assert value["DAILY_USABLE"] is True
    assert value["DAILY_COVERAGE_STATUS"] == "PARTIAL"
    assert value["DAILY_MANIFEST_FILE_N"] == 2580
    assert value["DAILY_MANIFEST_HASH"] == "dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045"
    assert value["FORMAL_IDENTITY_N"] == 5456
    assert value["LATEST_AVAILABLE_TRADE_DATE"] == "2026-08-17"
    assert value["PRECLOSE_COMPLETE"] is False
    assert value["FACTS_READY"] is False


def test_r4_fact_is_not_silently_derived() -> None:
    if not _runtime_available():
        pytest.skip("local data root or DuckDB unavailable")
    with LocalQuery() as query:
        with pytest.raises(QueryError) as exc_info:
            query.require_ready_fact("preclose")
    assert exc_info.value.code == "FACT_NOT_READY"


@pytest.mark.skipif(not _runtime_available(), reason="local data root or DuckDB unavailable")
def test_query_does_not_open_network_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_socket(*_args, **_kwargs):
        raise AssertionError("network socket opened")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    with LocalQuery() as query:
        result = query.latest("600519.SH", 1)
    assert result["row_n"] == 1


def test_query_module_has_no_provider_or_write_imports() -> None:
    source = (SRC_ROOT / "ashare_data/local_query.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )
    assert not imported.intersection({"requests", "urllib", "baostock", "tushare", "akshare"})
    assert "os.replace" not in source
