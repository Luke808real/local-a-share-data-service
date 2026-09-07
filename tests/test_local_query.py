from __future__ import annotations

import ast
import json
import os
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
    assert value["DAILY_MANIFEST_FILE_N"] == 2595
    assert value["DAILY_MANIFEST_HASH"] == "97aa4d16c82abfa144ab6c2d8fd2d9cde6dadb1e82e78889136fa5d950e56c7f"
    assert value["LATEST_PUBLISHED_TRADE_DATE"] == "2026-09-07"
    assert value["PENDING_FILE_N"] == 0
    assert value["FORMAL_IDENTITY_N"] == 5456
    assert value["LATEST_AVAILABLE_TRADE_DATE"] == "2026-09-07"
    assert value["DAILY_PUBLISHED_AS_OF"] == "2026-09-07"
    assert value["R7_FIRST_PUBLISH_PASS"] is False
    assert value["PHYSICAL_DAILY_FILE_N"] >= 2580
    assert value["PENDING_FILE_N"] == value["PHYSICAL_DAILY_FILE_N"] - 2595
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


TRANSACTION = Path("staging/r3_proven_missing_4key_repair_v01/transaction")


def _publish_fixture(root: Path) -> None:
    manifest = build_daily_file_manifest(root)
    transaction = root / TRANSACTION
    transaction.mkdir(parents=True, exist_ok=True)
    (transaction / "promotion_plan.json").write_text(json.dumps({
        "EXPECTED_POST_INPUT_MANIFEST": manifest,
    }))
    (transaction / "promotion_receipt.json").write_text(json.dumps({
        "STATE": "COMMITTED",
        "POST_INPUT_FILE_N": manifest["INPUT_FILE_N"],
        "POST_INPUT_MANIFEST_HASH": manifest["INPUT_MANIFEST_HASH"],
    }))


@pytest.fixture
def published_root(tmp_path: Path) -> Path:
    duckdb = pytest.importorskip("duckdb")
    daily = tmp_path / "curated/daily_bars/trade_date=2026-01-02"
    instruments = tmp_path / "curated/instruments"
    daily.mkdir(parents=True)
    instruments.mkdir(parents=True)
    with duckdb.connect(":memory:") as connection:
        connection.execute("""
            copy (select '000001.SZ' as symbol, date '2026-01-02' as trade_date,
                1.0 as open, 2.0 as high, 1.0 as low, 2.0 as close,
                10::bigint as volume, 20.0 as amount, 'fixture' as source,
                'v2' as data_version) to ? (format parquet)
        """, [str(daily / "part.parquet")])
        connection.execute("""
            copy (select '000001.SZ' as symbol, 'fixture' as name, 'SZ' as exchange,
                'stock' as asset_type, date '1991-01-01' as list_date,
                null::date as delist_date, null::varchar as prev_symbol,
                'fixture' as source, 'v2' as data_version) to ? (format parquet)
        """, [str(instruments / "part.parquet")])
    _publish_fixture(tmp_path)
    return tmp_path


def _baseline(root: Path) -> Path:
    return root / "curated/daily_bars/trade_date=2026-01-02/part.parquet"


def test_unpublished_files_never_enter_sql_and_cap_is_explicit(published_root: Path) -> None:
    daily = published_root / "curated/daily_bars"
    pending = daily / "trade_date=2026-01-05"
    pending.mkdir()
    # Invalid parquet proves the excluded file is not even opened by DuckDB.
    (pending / "candidate.parquet").write_bytes(b"unpublished and unreadable")
    (daily / "trade_date=2026-01-02/extra.parquet").write_bytes(b"same-date candidate")
    with LocalQuery(published_root) as query:
        assert "candidate.parquet" not in query._daily_relation
        assert "extra.parquet" not in query._daily_relation
        assert "*" not in query._daily_relation
        result = query.bars("000001", "2026-01-01", "2026-01-31")
        assert result["row_n"] == 1
        assert result["EFFECTIVE_AS_OF"] == "2026-01-02"
        assert result["REQUESTED_AS_OF"] == "2026-01-31"
        assert result["AS_OF_STATUS"] == "CAPPED_TO_LATEST_GOOD"
        assert result["PENDING_FILE_N"] == 2
        assert result["LATEST_PHYSICAL_TRADE_DATE"] == "2026-01-05"
        future = query.bars("000001", "2030-01-01", "2030-01-02")
        assert future["rows"] == []
        assert future["EFFECTIVE_AS_OF"] == "2026-01-02"
        assert query.latest("000001", as_of="2026-01-01")["rows"] == []
        assert query.latest("000001", as_of="2030-01-01")["row_n"] == 1
        status = query.status()
        assert status["DAILY_MANIFEST_FILE_N"] == 1
        assert status["PHYSICAL_DAILY_FILE_N"] == 3
        assert status["R7_FIRST_PUBLISH_PASS"] is False
        assert "LATEST_GOOD_AS_OF" not in status


@pytest.mark.parametrize("after_init", [False, True])
@pytest.mark.parametrize("mutation", ["missing", "same_size", "symlink"])
def test_baseline_drift_fails_before_query(published_root: Path, after_init: bool, mutation: str) -> None:
    query = LocalQuery(published_root) if after_init else None
    path = _baseline(published_root)
    original = path.read_bytes()
    identity = path.stat()
    if mutation == "missing":
        path.unlink()
    elif mutation == "same_size":
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        os.utime(path, ns=(identity.st_atime_ns, identity.st_mtime_ns))
    else:
        replacement = published_root / "replacement.parquet"
        replacement.write_bytes(original)
        path.unlink()
        path.symlink_to(replacement)
    try:
        with pytest.raises(QueryError) as error:
            if query:
                query.latest("000001")
            else:
                LocalQuery(published_root)
        assert error.value.code in {"DAILY_MANIFEST_DRIFT", "DATA_ROOT_ESCAPE", "DATA_ROOT_INCOMPLETE"}
    finally:
        if query:
            query.close()


@pytest.mark.parametrize("mutation", ["hash", "count", "state", "payload", "escape", "date", "duplicate"])
def test_publication_authority_is_validated(published_root: Path, mutation: str) -> None:
    transaction = published_root / TRANSACTION
    receipt_path = transaction / "promotion_receipt.json"
    plan_path = transaction / "promotion_plan.json"
    receipt = json.loads(receipt_path.read_text())
    plan = json.loads(plan_path.read_text())
    manifest = plan["EXPECTED_POST_INPUT_MANIFEST"]
    if mutation == "hash":
        receipt["POST_INPUT_MANIFEST_HASH"] = "0" * 64
    elif mutation == "count":
        receipt["POST_INPUT_FILE_N"] += 1
    elif mutation == "state":
        receipt["STATE"] = "PREPARED"
    elif mutation == "payload":
        manifest["FILES"][0]["sha256"] = "0" * 64
    elif mutation == "escape":
        manifest["FILES"][0]["relative_path"] = "../../outside.parquet"
    elif mutation == "date":
        manifest["FILES"][0]["relative_path"] = "curated/daily_bars/trade_date=2026-02-30/a.parquet"
    else:
        manifest["FILES"].append(manifest["FILES"][0])
    receipt_path.write_text(json.dumps(receipt))
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(QueryError):
        LocalQuery(published_root)


@pytest.mark.parametrize("mutation", ["malformed", "schema", "hash", "quality"])
def test_new_authority_pointer_fails_closed(published_root: Path, mutation: str) -> None:
    transaction = published_root / TRANSACTION
    receipt_path = transaction / "promotion_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["QUALITY"] = {"STRUCTURAL_PASS": True, "COVERAGE_PASS": True,
                          "PROVENANCE_PASS": True, "UNRESOLVED_KEY_N": 0,
                          "SOURCE_ERROR_N": 0, "MAX_TRADE_DATE": "2026-01-02"}
    receipt_path.write_text(json.dumps(receipt))
    pointer = published_root / "meta/asl/r3/published-daily-authority.json"
    pointer.parent.mkdir(parents=True)
    if mutation == "malformed":
        pointer.write_text("{")
    else:
        payload = {"schema": "R3_PUBLISHED_DAILY_AUTHORITY_V01",
                   "receipt": str((TRANSACTION / "promotion_receipt.json").as_posix()),
                   "plan": str((TRANSACTION / "promotion_plan.json").as_posix()),
                   "manifest_hash": receipt["POST_INPUT_MANIFEST_HASH"]}
        if mutation == "schema": payload["schema"] = "wrong"
        if mutation == "hash": payload["manifest_hash"] = "0" * 64
        if mutation == "quality": receipt["QUALITY"]["COVERAGE_PASS"] = False; receipt_path.write_text(json.dumps(receipt))
        pointer.write_text(json.dumps(payload))
    with pytest.raises(QueryError): LocalQuery(published_root)


def test_publication_flag_cannot_bypass_gate(published_root: Path) -> None:
    with pytest.raises(QueryError) as error:
        LocalQuery(published_root, require_published_manifest=False)
    assert error.value.code == "PUBLISHED_AUTHORITY_REQUIRED"
    (published_root / TRANSACTION / "promotion_receipt.json").unlink()
    with pytest.raises(QueryError) as error:
        LocalQuery(published_root)
    assert error.value.code == "PUBLISHED_AUTHORITY_NOT_FOUND"


def test_warm_query_does_not_rehash_files(published_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with LocalQuery(published_root) as query:
        def forbidden_hash(*_args, **_kwargs):
            raise AssertionError("warm query must reuse same-stat verification")
        monkeypatch.setattr("ashare_data.local_query.hashlib.sha256", forbidden_hash)
        assert query.latest("000001")["row_n"] == 1
        assert query.bars("000001", "2026-01-01", "2026-01-03")["row_n"] == 1
        assert query.status()["DAILY_MANIFEST_FILE_N"] == 1


def test_publication_revoked_during_session(published_root: Path) -> None:
    with LocalQuery(published_root) as query:
        receipt = published_root / TRANSACTION / "promotion_receipt.json"
        receipt.write_text('{"STATE":"REVOKED"}')
        with pytest.raises(QueryError, match="publication evidence changed"):
            query.latest("000001")


def test_instrument_without_published_daily_membership_is_rejected(published_root: Path) -> None:
    import duckdb
    extra = published_root / "curated/instruments/extra.parquet"
    with duckdb.connect(":memory:") as c:
        c.execute("copy (select * replace ('999999.SZ' as symbol) from read_parquet($src)) to $dest (format parquet)",
                  {"src": str(published_root / "curated/instruments/part.parquet"), "dest": str(extra)})
    with LocalQuery(published_root) as query:
        with pytest.raises(QueryError) as exc:
            query.instrument("999999.SZ")
        assert exc.value.code == "UNKNOWN_SYMBOL"


def test_mutation_during_sql_is_rejected(published_root: Path) -> None:
    with LocalQuery(published_root) as query:
        connection = query._connection

        class MutatingConnection:
            def execute(self, *args):
                cursor = connection.execute(*args)
                _baseline(published_root).write_bytes(b"changed during SQL")
                return cursor

            def close(self):
                connection.close()

        query._connection = MutatingConnection()
        with pytest.raises(QueryError) as error:
            query.latest("000001")
        assert error.value.code == "DAILY_MANIFEST_DRIFT"


def test_fixture_no_network_and_cli_table_cap(published_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_socket(*_args, **_kwargs):
        raise AssertionError("network socket opened")
    monkeypatch.setattr(socket, "socket", forbidden_socket)
    with LocalQuery(published_root) as query:
        query.bars("000001", "2026-01-01", "2026-01-31")
        query.latest("000001")
        query.instrument("000001")
        query.status()
    cli = Path(__file__).resolve().parents[1] / "tools/query_local_a_share.py"
    result = subprocess.run([
        sys.executable, str(cli), "--data-root", str(published_root),
        "bars", "--symbol", "000001", "--start", "2030-01-01", "--end", "2030-01-31",
    ], capture_output=True, text=True, check=True)
    assert "CAPPED_TO_LATEST_GOOD" in result.stdout
    assert "DAILY_PUBLISHED_AS_OF  2026-01-02" in result.stdout
    assert "PENDING_FILE_N  0" in result.stdout
    assert "R7_FIRST_PUBLISH_PASS  False" in result.stdout
    assert "no rows" in result.stdout
