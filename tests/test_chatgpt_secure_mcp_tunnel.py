from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/chatgpt_secure_mcp_tunnel.py"
spec = importlib.util.spec_from_file_location("chatgpt_secure_mcp_tunnel", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_tunnel_id_is_required_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(module.TUNNEL_ID_ENV, raising=False)
    with pytest.raises(module.TunnelSetupError):
        module._tunnel_id(None)
    monkeypatch.setenv(module.TUNNEL_ID_ENV, "bad")
    with pytest.raises(module.TunnelSetupError):
        module._tunnel_id(None)
    monkeypatch.setenv(module.TUNNEL_ID_ENV, "tunnel_0123456789abcdef")
    assert module._tunnel_id(None) == "tunnel_0123456789abcdef"


def test_connect_uses_secret_reference_not_literal_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(module.RUNTIME_KEY_ENV, "sk-secret-must-not-appear")
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command: list[str], *, capture: bool = False):
        calls.append((command, capture))
        if capture:
            return SimpleNamespace(stdout='{"process_running":true,"healthy":true,"ready":true}')
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(module, "_run", fake_run)
    payload = module.connect(
        client="/usr/local/bin/tunnel-client",
        alias="asl-market-data",
        tunnel_id="tunnel_0123456789abcdef",
        mcp_server_url="http://127.0.0.1:8766/mcp",
    )
    flattened = " ".join(part for command, _ in calls for part in command)
    assert "sk-secret-must-not-appear" not in flattened
    assert "env:CONTROL_PLANE_API_KEY" in flattened
    assert "http://127.0.0.1:8766/mcp" in flattened
    assert payload["ready"] is True


def test_connect_fails_closed_when_status_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(module.RUNTIME_KEY_ENV, "sk-redacted")

    def fake_run(command: list[str], *, capture: bool = False):
        if capture:
            return SimpleNamespace(stdout='{"process_running":true,"healthy":true,"ready":false}')
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(module, "_run", fake_run)
    with pytest.raises(module.TunnelSetupError, match="not fully ready"):
        module.connect(
            client="tunnel-client",
            alias="asl-market-data",
            tunnel_id="tunnel_0123456789abcdef",
            mcp_server_url="http://127.0.0.1:8766/mcp",
        )


def test_missing_runtime_key_fails_before_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(module.RUNTIME_KEY_ENV, raising=False)
    monkeypatch.setattr(
        module,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("subprocess must not run without runtime key"),
    )
    with pytest.raises(module.TunnelSetupError, match=module.RUNTIME_KEY_ENV):
        module.connect(
            client="tunnel-client",
            alias="asl-market-data",
            tunnel_id="tunnel_0123456789abcdef",
            mcp_server_url="http://127.0.0.1:8766/mcp",
        )
