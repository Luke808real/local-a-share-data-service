"""Tool-only MCP adapter for LocalQuery's published, local market facts.

No data is opened at import/startup. Each call owns and closes one LocalQuery;
publication and AS_OF semantics remain entirely in the query core.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import PurePath
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from ashare_data.local_query import DAILY_FACT_FIELDS, IDENTITY_COLUMNS, READY_BAR_COLUMNS, LocalQuery, QueryError

Symbol = Annotated[str, Field(strict=True, min_length=1, max_length=16)]
ISODate = Annotated[str, Field(strict=True, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
Limit = Annotated[int, Field(strict=True, ge=1, le=250)]
Fields = Annotated[list[Annotated[str, Field(strict=True, max_length=64)]], Field(max_length=32)]

_PARAMETERS = {
    "bars": {"symbol", "start", "end", "fields"},
    "latest": {"symbol", "limit", "fields"},
    "instrument": {"symbol", "fields"},
    "status": set(),
}
_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, openWorldHint=False, idempotentHint=True
)
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,95}")
_HOST = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::([0-9]{1,5}))?")
_PATH_TEXT = re.compile(r"(?:^|[\s='\"(])(?:/|~[/\\]|\.{1,2}[/\\]|[A-Za-z]:[/\\])|file://")


def _public_value(value: Any) -> Any:
    """Remove filesystem metadata recursively without dropping readiness/hashes.

    Query exceptions' free-form messages/details never enter this function.
    Relative paths under unrecognized metadata keys are redacted too.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key)
            normalized = name.lower()
            tokens = set(normalized.split("_"))
            if tokens & {"path", "paths", "root", "directory", "directories"}:
                continue
            if "manifest" in tokens and tokens & {"source", "reference"}:
                continue
            if _PATH_TEXT.search(name) or "/" in name or "\\" in name:
                continue
            result[name] = _public_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    if isinstance(value, PurePath):
        return "[REDACTED_PATH]"
    if isinstance(value, str) and (
        _PATH_TEXT.search(value)
        or "\\" in value
        or re.search(r"\S+/\S+", value)
    ):
        return "[REDACTED_PATH]"
    return value


def _result(value: dict[str, Any], *, error: bool = False) -> CallToolResult:
    return CallToolResult(
        structuredContent=value,
        content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False, allow_nan=False))],
        isError=error,
    )


def _error(code: str) -> CallToolResult:
    # Neither exception text nor details are safe to expose: DuckDB errors may
    # contain SQL, full paths, and local configuration. Keep the stable code.
    safe_code = code if isinstance(code, str) and _CODE.fullmatch(code) else "QUERY_FAILED"
    return _result({"error": {"code": safe_code, "message": f"Local query failed: {safe_code}."}}, error=True)


def _invoke(command: str, fields: list[str] | None = None, **arguments: Any) -> CallToolResult:
    try:
        if fields:
            ready = IDENTITY_COLUMNS if command == "instrument" else (*READY_BAR_COLUMNS, *DAILY_FACT_FIELDS)
            if any(field not in ready for field in fields):
                return _error("FACT_NOT_READY")
        if command == "bars":
            try:
                start, end = date.fromisoformat(arguments["start"]), date.fromisoformat(arguments["end"])
            except ValueError:
                return _error("INVALID_DATE")
            if start > end:
                return _error("INVALID_DATE_RANGE")
            if (end - start).days + 1 > 1096:
                return _error("DATE_SPAN_EXCEEDED")
        with LocalQuery() as query:
            if fields and command in {"bars", "latest"}:
                arguments["fact_fields"] = fields
            value = getattr(query, command)(**arguments)
        # fields validates availability; always retain the complete ready record
        # and provenance envelope, rather than stripping authority metadata.
        return _result(_public_value(value))
    except QueryError as exc:
        return _error(exc.code)
    except Exception:
        return _error("QUERY_FAILED")


class LocalMarketMCP(FastMCP):
    """Sanitize SDK validation errors, including errors before tool dispatch."""

    async def list_tools(self):
        listed = await super().list_tools()
        for tool in listed:
            tool.inputSchema["additionalProperties"] = False
            tool.outputSchema = {"type": "object", "additionalProperties": True}
        return listed

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        if name not in _PARAMETERS:
            return _error("UNKNOWN_TOOL")
        if not isinstance(arguments, dict) or set(arguments) - _PARAMETERS[name]:
            return _error("INVALID_ARGUMENT")
        if arguments.get("fields") is not None and not isinstance(arguments["fields"], list):
            return _error("INVALID_ARGUMENT")
        try:
            return await super().call_tool(name, arguments)
        except Exception:
            # FastMCP otherwise includes Pydantic's raw input in error content.
            return _error("INVALID_ARGUMENT")


def create_server(*, port: int = 8766) -> LocalMarketMCP:
    """Build a loopback-only development server; no data-root tool input.

    ASL_MCP_ALLOWED_HOSTS is a comma-separated list of exact Host header values
    to add (including a port when used). Wildcards, URLs and empty entries fail
    closed. A tunnel changes the allowed Host, never the listening interface.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid MCP port")
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
    configured = os.environ.get("ASL_MCP_ALLOWED_HOSTS")
    if configured is not None:
        for item in configured.split(","):
            host = item.strip()
            match = _HOST.fullmatch(host)
            if not match or (match.group(1) and not 1 <= int(match.group(1)) <= 65535):
                raise ValueError("ASL_MCP_ALLOWED_HOSTS must contain exact host[:port] values")
            if host not in hosts:
                hosts.append(host)
    server = LocalMarketMCP(
        "ASL Local Market Data",
        instructions=(
            "Read-only local published R3 daily facts, not real-time prices. "
            "Use status for publication dates, PARTIAL coverage and readiness. "
            "R3 availability does not imply R7 first publish or FACTS_READY. "
            "Unavailable facts return FACT_NOT_READY; no strategy or trading tools."
        ),
        host="127.0.0.1",
        port=port,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[f"http://127.0.0.1:{port}", f"http://localhost:{port}"],
        ),
    )

    @server.tool(annotations=_ANNOTATIONS)
    def bars(symbol: Symbol, start: ISODate, end: ISODate, fields: Fields | None = None) -> CallToolResult:
        """Read one symbol's RAW daily OHLCV, inclusive dates (at most 1096 days).

        LocalQuery caps dates to its published authority and reports effective
        dates. Optional fields checks availability, not projection: full ready
        records/provenance are retained. Unsupported facts return FACT_NOT_READY.
        """
        return _invoke("bars", fields, symbol=symbol, start=start, end=end)

    @server.tool(annotations=_ANNOTATIONS)
    def latest(symbol: Symbol, limit: Limit = 20, fields: Fields | None = None) -> CallToolResult:
        """Read latest published RAW daily rows for one symbol, newest first (1–250).

        Default 20 rows. fields checks availability, not projection; unsupported
        facts return FACT_NOT_READY. Provider provenance and readiness remain.
        """
        return _invoke("latest", fields, symbol=symbol, limit=limit)

    @server.tool(annotations=_ANNOTATIONS)
    def instrument(symbol: Symbol, fields: Fields | None = None) -> CallToolResult:
        """Read formal local identity and provenance for one symbol.

        fields checks identity-field availability, not projection. Unsupported
        facts return FACT_NOT_READY. No inferred historical trading status.
        """
        return _invoke("instrument", fields, symbol=symbol)

    @server.tool(annotations=_ANNOTATIONS)
    def status() -> CallToolResult:
        """Read publication hashes/dates, physical availability and fact readiness.

        Physical availability is not publication. PARTIAL and false readiness
        flags are preserved. No fields parameter or filesystem paths returned.
        """
        return _invoke("status")

    return server
