# mcp_client

Thin stdio JSON-RPC client for MCP tool servers, paired with [`mcp_registry`](mcp_registry.md): the registry resolves *which* servers exist and are approved, this module *calls* them.

Moved from `business/ai-doc-automation/backend/mcp_client.py` (issue #42). No server code, config only — MCP-first.

## Symbols

- `McpToolClient` — minimal stdio JSON-RPC client (initialize → `notifications/initialized` → `tools/call`), one process per call
- `McpToolError` — transport/protocol/timeout failure (RuntimeError)
- `known_servers(registry_path=None)` — `name -> McpServerRecord` for registry servers with status `proposed`/`approved` **and** a non-empty `stdio_command.command`

## Usage

```python
from fusion_core.mcp_client import McpToolClient, known_servers

servers = known_servers()          # stdio-capable, non-deprecated servers
client = McpToolClient("fusion-cowork-mcp")
result = await client.call_tool("sheet.write", {"range": "A1:B2", "values": [[...]]}, timeout_s=30)
# result == {"ok": bool, "text": str, "raw": <full tools/call result>}
```

Registry location resolves through `mcp_registry.default_registry_path()` (`FUSION_MCP_REGISTRY` env → monorepo sidecar → bundled copy); pass `registry_path=` to override.

## Behavior

- **Spawn-per-call**: a fresh subprocess per `call_tool` — MCP servers are cheap to spawn at per-task volumes; no connection state to leak.
- **`stdio_command`** in the registry entry (validated by `mcp_registry`): `{"command": str, "args": [str], "env": {str: str}}`. `env` values with an `env:VAR` prefix are resolved from the **operator's** environment at client construction — credentials are never stored in the registry.
- **Timeout**: `call_tool(timeout_s=30)` bounds the `tools/call` roundtrip; expiry raises `McpToolError` (the process is killed in cleanup).
- **`isError` is a result, not an error** (E5 pattern): a tool-level error returns `{"ok": False, ...}`; JSON-RPC-level errors and transport failures raise `McpToolError`.
- Fail visibly: unknown server, malformed registry, closed stdout all raise — no silent `{}` fallback.
- Import stays I/O-free: registry and env reads happen at call/construction time only.

## Non-goals (deliberate)

- No HTTP/SSE transports — stdio only
- No registry writes — authoring happens in `architecture/mcp-server-registry.json` (edit all three copies; see `mcp_registry.md`)
- No credential storage — `credential_mode` is a declaration; identity service owns issuance
