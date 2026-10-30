from __future__ import annotations

import json
import os
import sys
import textwrap

import pytest

from fusion_core import mcp_registry
from fusion_core.mcp_client import McpToolClient, McpToolError, known_servers


def _write_registry(path, servers):
    path.write_text(json.dumps({"version": 1, "servers": servers}), encoding="utf-8")
    return path


def _server(**overrides):
    base = {
        "name": "mock-server",
        "transport": "stdio",
        "purpose": "testing",
        "credential_mode": "env_ref",
        "status": "approved",
        "stdio_command": {"command": sys.executable, "args": [str(_mock_server_script_path())]},
    }
    base.update(overrides)
    return base


def _mock_server_script_path(tmp_dir=None):
    import tempfile

    cache = getattr(_mock_server_script_path, "_cache", None)
    if cache is not None:
        return cache
    fd, script = tempfile.mkstemp(suffix=".py", prefix="mock_mcp_server_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(
            textwrap.dedent(
                """
                import json
                import os
                import sys

                for line in sys.stdin:
                    line = line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    method = msg.get("method", "")
                    if method == "initialize":
                        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}
                        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
                    elif method == "tools/call":
                        name = msg["params"]["name"]
                        if name == "boom":
                            sys.stdout.write(
                                json.dumps(
                                    {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32000, "message": "boom"}}
                                )
                            )
                            sys.stdout.write("\\n")
                        elif name == "fail":
                            sys.stdout.write(
                                json.dumps(
                                    {
                                        "jsonrpc": "2.0",
                                        "id": msg["id"],
                                        "result": {
                                            "content": [{"type": "text", "text": "tool failed"}],
                                            "isError": True,
                                        },
                                    }
                                )
                            )
                            sys.stdout.write("\\n")
                        elif name == "sleep":
                            import time

                            time.sleep(float(msg["params"]["arguments"].get("seconds", 10)))
                        elif name == "env_echo":
                            marker = os.environ.get("MOCK_MCP_MARKER", "<unset>")
                            sys.stdout.write(
                                json.dumps(
                                    {
                                        "jsonrpc": "2.0",
                                        "id": msg["id"],
                                        "result": {"content": [{"type": "text", "text": marker}]},
                                    }
                                )
                            )
                            sys.stdout.write("\\n")
                        else:
                            sys.stdout.write(
                                json.dumps(
                                    {
                                        "jsonrpc": "2.0",
                                        "id": msg["id"],
                                        "result": {"content": [{"type": "text", "text": f"echo:{name}"}]},
                                    }
                                )
                            )
                            sys.stdout.write("\\n")
                    sys.stdout.flush()
                """
            )
        )
    _mock_server_script_path._cache = script
    return script


@pytest.fixture(autouse=True)
def _fresh_cache():
    mcp_registry.clear_cache()
    yield
    mcp_registry.clear_cache()


@pytest.fixture(autouse=True)
def _cleanup_script():
    yield
    script = getattr(_mock_server_script_path, "_cache", None)
    if script and os.path.exists(script):
        os.unlink(script)
        _mock_server_script_path._cache = None


class TestKnownServers:
    def test_filters_by_status_and_stdio(self, tmp_path):
        path = _write_registry(
            tmp_path / "reg.json",
            [
                _server(),
                _server(name="reviewed-one", status="reviewed"),
                _server(name="deprecated-one", status="deprecated"),
                _server(name="no-stdio", stdio_command=None),
                {"name": "bare", "status": "proposed", "credential_mode": "env_ref"},
            ],
        )
        servers = known_servers(registry_path=path)
        assert set(servers) == {"mock-server"}

    def test_proposed_is_eligible(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server(status="proposed")])
        assert "mock-server" in known_servers(registry_path=path)

    def test_env_override_path(self, tmp_path, monkeypatch):
        path = _write_registry(tmp_path / "env-reg.json", [_server()])
        monkeypatch.setenv("FUSION_MCP_REGISTRY", str(path))
        assert "mock-server" in known_servers()

    def test_records_are_mcp_server_records(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        record = known_servers(registry_path=path)["mock-server"]
        assert record.stdio_command["command"] == sys.executable


class TestMcpToolClient:
    def test_unknown_server_raises(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        with pytest.raises(McpToolError, match="'ghost' not in registry"):
            McpToolClient("ghost", registry_path=path)

    def test_empty_name_raises_value(self):
        with pytest.raises(ValueError, match="server_name required"):
            McpToolClient("")

    async def test_initialize_and_tools_call_roundtrip(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        client = McpToolClient("mock-server", registry_path=path)
        result = await client.call_tool("anything", {"x": 1}, timeout_s=15)
        assert result["ok"] is True
        assert result["text"] == "echo:anything"
        assert result["raw"]["content"][0]["text"] == "echo:anything"

    async def test_rpc_error_raises(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        client = McpToolClient("mock-server", registry_path=path)
        with pytest.raises(McpToolError, match="boom"):
            await client.call_tool("boom", {}, timeout_s=15)

    async def test_is_error_result_not_ok(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        client = McpToolClient("mock-server", registry_path=path)
        result = await client.call_tool("fail", {}, timeout_s=15)
        assert result["ok"] is False
        assert result["text"] == "tool failed"

    async def test_timeout_raises_mcp_tool_error(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        client = McpToolClient("mock-server", registry_path=path)
        with pytest.raises(McpToolError, match="timed out"):
            await client.call_tool("sleep", {"seconds": 10}, timeout_s=1)

    async def test_env_passthrough_env_ref(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MOCK_MCP_MARKER", "from-operator-env")
        server = _server()
        server["stdio_command"]["env"] = {"MOCK_MCP_MARKER": "env:MOCK_MCP_MARKER"}
        path = _write_registry(tmp_path / "reg.json", [server])
        client = McpToolClient("mock-server", registry_path=path)
        result = await client.call_tool("env_echo", {}, timeout_s=15)
        assert result["text"] == "from-operator-env"

    async def test_env_literal_value(self, tmp_path):
        server = _server()
        server["stdio_command"]["env"] = {"MOCK_MCP_MARKER": "literal"}
        path = _write_registry(tmp_path / "reg.json", [server])
        client = McpToolClient("mock-server", registry_path=path)
        result = await client.call_tool("env_echo", {}, timeout_s=15)
        assert result["text"] == "literal"

    async def test_empty_tool_raises(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        client = McpToolClient("mock-server", registry_path=path)
        with pytest.raises(ValueError, match="tool required"):
            await client.call_tool("", {}, timeout_s=15)


class TestRegistryStdioCommand:
    def test_stdio_command_parsed(self, tmp_path):
        path = _write_registry(
            tmp_path / "reg.json",
            [_server(stdio_command={"command": "echo", "args": ["hi"], "env": {"A": "B"}})],
        )
        record = mcp_registry.load_registry(path)[0]
        assert record.stdio_command == {"command": "echo", "args": ["hi"], "env": {"A": "B"}}

    @pytest.mark.parametrize(
        ("stdio_command", "match"),
        [
            ("not-an-object", "stdio_command must be an object"),
            ({}, "non-empty string 'command'"),
            ({"command": "x", "args": "nope"}, "args must be a list of strings"),
            ({"command": "x", "env": {"A": 1}}, "env must be a string-to-string object"),
        ],
    )
    def test_bad_stdio_command_raises(self, tmp_path, stdio_command, match):
        path = _write_registry(tmp_path / "reg.json", [_server(stdio_command=stdio_command)])
        with pytest.raises(mcp_registry.RegistryIntegrityError, match=match):
            mcp_registry.load_registry(path)

    def test_missing_stdio_command_is_none(self, tmp_path):
        path = _write_registry(
            tmp_path / "reg.json",
            [{"name": "bare", "status": "approved", "credential_mode": "env_ref"}],
        )
        assert mcp_registry.load_registry(path)[0].stdio_command is None
