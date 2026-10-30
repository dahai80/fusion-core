from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

from fusion_core.mcp_registry import McpServerRecord, load_registry

logger = logging.getLogger(__name__)

STDIO_ELIGIBLE_STATUS = ("proposed", "approved")


class McpToolError(RuntimeError):
    pass


def known_servers(registry_path: str | Path | None = None) -> dict[str, McpServerRecord]:
    records = load_registry(registry_path)
    out = {}
    for record in records:
        if record.status not in STDIO_ELIGIBLE_STATUS:
            continue
        if record.stdio_command and record.stdio_command.get("command"):
            out[record.name] = record
    logger.debug("known_servers: %d stdio-capable servers", len(out))
    return out


def _resolve_env(overrides: dict[str, str]) -> dict[str, str]:
    resolved = {}
    for key, value in overrides.items():
        if value.startswith("env:"):
            resolved[key] = os.environ.get(value[4:], "")
        else:
            resolved[key] = value
    return resolved


class McpToolClient:
    def __init__(self, server_name: str, *, registry_path: str | Path | None = None):
        if not server_name:
            raise ValueError("McpToolClient: server_name required")
        record = known_servers(registry_path).get(server_name)
        if record is None:
            raise McpToolError(f"MCP server {server_name!r} not in registry (or no stdio_command)")
        self.record = record
        launch = record.stdio_command or {}
        self.command = [launch["command"], *launch.get("args", [])]
        self.env = {**os.environ, **_resolve_env(launch.get("env") or {})}

    async def call_tool(self, tool: str, arguments: dict, timeout_s: float = 30) -> dict:
        if not tool:
            raise ValueError("call_tool: tool required")
        proc = await asyncio.create_subprocess_exec(
            *self.command,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        rpc_id = 0

        async def rpc(method: str, params: dict) -> dict:
            nonlocal rpc_id
            rpc_id += 1
            req = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
            proc.stdin.write((json.dumps(req) + "\n").encode())
            await proc.stdin.drain()
            while True:
                line = await proc.stdout.readline()
                if not line:
                    raise McpToolError(f"MCP server {self.record.name} closed stdout during {method}")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("mcp_client: non-json line from %s", self.record.name)
                    continue
                if msg.get("id") == rpc_id:
                    if "error" in msg:
                        raise McpToolError(f"{method} failed: {msg['error']}")
                    return msg.get("result", {})

        try:
            await rpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "fusion-core", "version": "0"},
                },
            )
            proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            await proc.stdin.drain()
            try:
                result = await asyncio.wait_for(
                    rpc("tools/call", {"name": tool, "arguments": arguments}),
                    timeout=timeout_s,
                )
            except TimeoutError as exc:
                raise McpToolError(f"tools/call {tool!r} on {self.record.name} timed out after {timeout_s}s") from exc
        finally:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        content = result.get("content") or []
        text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        logger.debug("mcp_client: %s.%s ok=%s", self.record.name, tool, not result.get("isError"))
        return {"ok": not result.get("isError"), "text": text, "raw": result}
