"""Protocol/boundary tests with no canonical data or provider dependencies."""

from __future__ import annotations

import ast
import asyncio
import builtins
import json
import socket
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ashare_data import mcp_server as adapter  # noqa: E402
from ashare_data.local_query import QueryError  # noqa: E402


class FakeQuery:
    instances = []
    failure = None

    def __init__(self):
        self.closed = False
        self.calls = []
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def _value(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.failure:
            raise self.failure
        return {
            "command": command,
            "rows": [{"symbol": "600519.SH", "close": 1400, "source": "tdx", "data_version": "r3"}],
            "REQUESTED_AS_OF": "2026-09-06",
            "EFFECTIVE_AS_OF": "2026-08-17",
            "AS_OF_STATUS": "CAPPED_TO_LATEST_GOOD",
            "LATEST_PHYSICAL_TRADE_DATE": "2026-08-28",
            "DAILY_MANIFEST_HASH": "a" * 64,
            "DAILY_COVERAGE_STATUS": "PARTIAL",
            "R7_FIRST_PUBLISH_PASS": False,
            "FACTS_READY": False,
            "DATA_ROOT": "/private/canonical",
            "DAILY_MANIFEST_SOURCE": "/private/receipt.json",
            "details": {
                "relative_path": "curated/daily/file.parquet",
                "unexpected": "/private/secret.parquet",
                "other": "curated/daily/file.parquet",
                "expected_hash": "b" * 64,
            },
        }

    def bars(self, **kwargs):
        return self._value("bars", **kwargs)

    def latest(self, **kwargs):
        return self._value("latest", **kwargs)

    def instrument(self, **kwargs):
        return self._value("instrument", **kwargs)

    def status(self):
        return self._value("status")


@pytest.fixture
def server(monkeypatch):
    FakeQuery.instances = []
    FakeQuery.failure = None
    monkeypatch.delenv("ASL_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(adapter, "LocalQuery", FakeQuery)
    return adapter.create_server()


def call(server, name, arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    assert json.loads(result.content[0].text) == result.structuredContent
    return result


def test_tool_surface_schemas_annotations(server):
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {"bars", "latest", "instrument", "status"}
    for tool in tools.values():
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema["type"] == "object"
        assert not {"root", "url", "sql", "execute"} & tool.inputSchema["properties"].keys()
    limit = tools["latest"].inputSchema["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 250, 20)
    assert tools["bars"].inputSchema["properties"]["symbol"]["maxLength"] == 16
    assert asyncio.run(server.list_resources()) == []
    assert asyncio.run(server.list_prompts()) == []
    assert not FakeQuery.instances


@pytest.mark.parametrize("name,arguments,expected", [
    ("bars", {"symbol": "600519.SH", "start": "2026-01-01", "end": "2026-01-31"},
     {"symbol": "600519.SH", "start": "2026-01-01", "end": "2026-01-31"}),
    ("latest", {"symbol": "600519.SH"}, {"symbol": "600519.SH", "limit": 20}),
    ("latest", {"symbol": "600519.SH", "limit": 250}, {"symbol": "600519.SH", "limit": 250}),
    ("instrument", {"symbol": "600519.SH"}, {"symbol": "600519.SH"}),
    ("status", {}, {}),
])
def test_delegation_and_sanitized_provenance(server, name, arguments, expected):
    result = call(server, name, arguments)
    assert not result.isError
    payload = result.structuredContent
    assert payload["DAILY_MANIFEST_HASH"] == "a" * 64
    assert payload["details"]["expected_hash"] == "b" * 64
    assert payload["rows"][0]["source"] == "tdx"
    assert payload["rows"][0]["data_version"] == "r3"
    assert payload["EFFECTIVE_AS_OF"] == "2026-08-17"
    assert payload["LATEST_PHYSICAL_TRADE_DATE"] == "2026-08-28"
    assert payload["AS_OF_STATUS"] == "CAPPED_TO_LATEST_GOOD"
    assert payload["DAILY_COVERAGE_STATUS"] == "PARTIAL"
    assert payload["FACTS_READY"] is False
    assert payload["R7_FIRST_PUBLISH_PASS"] is False
    encoded = result.model_dump_json()
    for private in ("/private", "curated/", "DATA_ROOT", "MANIFEST_SOURCE", "relative_path"):
        assert private not in encoded
    assert len(FakeQuery.instances) == 1
    assert FakeQuery.instances[0].calls == [(name, expected)]
    assert FakeQuery.instances[0].closed


@pytest.mark.parametrize("limit", [0, -1, 251, True, 2.0, "20", None])
def test_limit_rejected_before_query(server, limit):
    assert call(server, "latest", {"symbol": "600519.SH", "limit": limit}).isError
    assert not FakeQuery.instances


@pytest.mark.parametrize("symbol", ["", "x" * 17, 600519, None, ["600519.SH"]])
def test_symbol_bounds_before_query(server, symbol):
    assert call(server, "instrument", {"symbol": symbol}).isError
    assert not FakeQuery.instances


@pytest.mark.parametrize("start,end,code", [
    ("2026-02-30", "2026-03-01", "INVALID_DATE"),
    ("2026-03-01", "2026-02-01", "INVALID_DATE_RANGE"),
    ("2020-01-01", "2026-01-01", "DATE_SPAN_EXCEEDED"),
    ("20260101", "2026-01-31", "INVALID_ARGUMENT"),
    ("/private/key", "2026-01-31", "INVALID_ARGUMENT"),
])
def test_date_bounds(server, start, end, code):
    result = call(server, "bars", {"symbol": "600519.SH", "start": start, "end": end})
    assert result.isError
    assert result.structuredContent["error"]["code"] == code
    assert "/private" not in result.model_dump_json()
    assert not FakeQuery.instances


def test_inclusive_date_span_boundary(server):
    start = date(2020, 1, 1)
    args = {"symbol": "600519.SH", "start": start.isoformat(), "end": (start + timedelta(days=1095)).isoformat()}
    assert not call(server, "bars", args).isError
    args["end"] = (start + timedelta(days=1096)).isoformat()
    assert call(server, "bars", args).isError
    assert len(FakeQuery.instances) == 1


@pytest.mark.parametrize("name,arguments", [
    ("latest", {"symbol": "600519.SH", "fields": ["preclose"]}),
    ("bars", {"symbol": "600519.SH", "start": "2026-01-01", "end": "2026-01-01", "fields": ["turnover_rate"]}),
    ("instrument", {"symbol": "600519.SH", "fields": ["trading_status"]}),
])
def test_fact_not_ready(server, name, arguments):
    result = call(server, name, arguments)
    assert result.isError
    assert result.structuredContent["error"]["code"] == "FACT_NOT_READY"
    assert not FakeQuery.instances


def test_ready_fields_preserve_provenance_and_new_query_per_call(server):
    for _ in range(2):
        result = call(server, "latest", {"symbol": "600519.SH", "fields": ["close"]})
        assert not result.isError
        assert result.structuredContent["rows"][0]["source"] == "tdx"
    assert len(FakeQuery.instances) == 2
    assert all(query.closed for query in FakeQuery.instances)


@pytest.mark.parametrize("name,args", [
    ("latest", {"symbol": "600519.SH", "root": "/private/root"}),
    ("status", {"sql": "select 1"}),
    ("status", {"fields": ["close"]}),
    ("latest", {"symbol": "600519.SH", "fields": '["close"]'}),
    ("/private/tool", {}),
])
def test_extra_inputs_and_unknown_tools_fail_closed(server, name, args):
    result = call(server, name, args)
    assert result.isError
    assert "/private" not in result.model_dump_json()
    assert not FakeQuery.instances


@pytest.mark.parametrize("failure,code", [
    (QueryError("DAILY_MANIFEST_DRIFT", "/private/secret", details={"path": "/private/data"}), "DAILY_MANIFEST_DRIFT"),
    (QueryError("/private/code", "unsafe"), "QUERY_FAILED"),
    (OSError("/private/data"), "QUERY_FAILED"),
])
def test_errors_visible_sanitized_and_connections_closed(server, failure, code):
    FakeQuery.failure = failure
    result = call(server, "status", {})
    assert result.isError
    assert result.structuredContent["error"]["code"] == code
    assert "/private" not in result.model_dump_json()
    assert FakeQuery.instances[0].closed


def test_constructor_failure_is_sanitized(server, monkeypatch):
    def fail():
        raise FileNotFoundError("/private/missing")
    monkeypatch.setattr(adapter, "LocalQuery", fail)
    result = call(server, "status", {})
    assert result.isError
    assert "/private" not in result.model_dump_json()


def rpc(client, method, params=None, **headers):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                       headers={"accept": "application/json, text/event-stream", **headers})


def test_streamable_http_protocol_and_transport_protection(server):
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8766
    assert server.settings.stateless_http and server.settings.json_response
    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:8766") as client:
        response = rpc(client, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                              "clientInfo": {"name": "fixture", "version": "1"}})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "mcp-session-id" not in response.headers
        assert "serverInfo" in response.json()["result"]
        listed = rpc(client, "tools/list").json()["result"]["tools"]
        assert {tool["name"] for tool in listed} == {"bars", "latest", "instrument", "status"}
        for name, args in [("status", {}), ("latest", {"symbol": "600519.SH"}),
                           ("instrument", {"symbol": "600519.SH"}),
                           ("bars", {"symbol": "600519.SH", "start": "2026-01-01", "end": "2026-01-02"})]:
            result = rpc(client, "tools/call", {"name": name, "arguments": args}).json()["result"]
            assert result["isError"] is False
            assert isinstance(result["structuredContent"], dict)
        invalid = rpc(client, "tools/call", {"name": "latest", "arguments": {"symbol": "/private/very-long-secret"}})
        assert invalid.json()["result"]["isError"] is True
        assert "/private" not in invalid.text
        assert rpc(client, "tools/list", host="evil.example").status_code == 421
        assert rpc(client, "tools/list", origin="https://evil.example").status_code == 403
        assert rpc(client, "tools/list", host="127.0.0.1:9999").status_code == 421
        assert client.get("/").status_code == 404


def test_exact_tunnel_host_allowlist(monkeypatch):
    monkeypatch.setenv("ASL_MCP_ALLOWED_HOSTS", "data.example.test,data.example.test:443")
    server = adapter.create_server()
    assert server.settings.transport_security.enable_dns_rebinding_protection
    assert server.settings.host == "127.0.0.1"
    with TestClient(server.streamable_http_app(), base_url="https://data.example.test") as client:
        assert rpc(client, "tools/list").status_code == 200
        assert rpc(client, "tools/list", host="data.example.test:444").status_code == 421
        assert rpc(client, "tools/list", host="evil.data.example.test").status_code == 421


@pytest.mark.parametrize("hosts", ["*", "localhost:*", "https://data.example", "x:0", "x:65536", "", "x,", "x/path", "x\r\nHost:evil"])
def test_invalid_host_configuration(monkeypatch, hosts):
    monkeypatch.setenv("ASL_MCP_ALLOWED_HOSTS", hosts)
    with pytest.raises(ValueError):
        adapter.create_server()


def test_query_call_has_no_network_or_writes(server, monkeypatch):
    async def check():
        # Install guards inside the running event loop (its creation itself
        # uses a socketpair). No file fixtures, real data or canonical writes.
        def forbidden(*args, **kwargs):
            raise AssertionError("unexpected I/O")
        with monkeypatch.context() as guarded:
            guarded.setattr(socket, "socket", forbidden)
            guarded.setattr(builtins, "open", forbidden)
            guarded.setattr(Path, "open", forbidden)
            for name, args in [("status", {}), ("latest", {"symbol": "600519.SH"})]:
                assert not (await server.call_tool(name, args)).isError
    asyncio.run(check())


def test_no_provider_imports_and_entrypoint_is_cwd_independent():
    source = (ROOT / "src/ashare_data/mcp_server.py").read_text()
    tree = ast.parse(source)
    modules = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any(name.split(".")[0] in {"cnequity", "akshare", "baostock", "tushare", "requests", "duckdb", "pyarrow"} for name in modules)
    result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/serve_local_a_share_mcp.py"), "--help"],
                            cwd="/", capture_output=True, text=True, check=True)
    assert "streamable-http" in result.stdout and "stdio" in result.stdout
    assert "--host" not in result.stdout and "--data-root" not in result.stdout
