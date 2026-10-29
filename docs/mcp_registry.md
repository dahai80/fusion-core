# mcp_registry

MCP server registry: platform single source of truth for which MCP tool servers are approved across the Fusion ecosystem, and how their credentials are issued. Resolution layer for `architecture/mcp-server-registry-1029.md` (issue #40), mirroring how `config` is the resolution layer for config-conventions. No I/O at import, no new runtime deps (stdlib `json`).

## Symbols

- [`REGISTRY_STATUS_LIFECYCLE`](#constants)
- [`load_registry(path)`](#load_registry)
- [`resolve_server(name, *, registry_path)`](#resolve_server)
- [`check_approval(url, tool_scope, *, registry_path)`](#check_approval)
- [`clear_cache()`](#clear_cache)

## Constants

- `DEFAULT_REGISTRY_FILENAME = "mcp-server-registry.json"` — sidecar file name under `architecture/`.
- `REGISTRY_STATUS_LIFECYCLE = ("proposed", "reviewed", "approved", "deprecated")` — only `approved` servers may be referenced by business apps.
- `CREDENTIAL_MODES = ("identity_issuance", "env_ref")` — declaration only; the registry never stores or issues secrets.
- `APPROVAL_ACTIONS = ("approved", "denied", "unknown")` — the three verdict actions.

## default_registry_path

```python
def default_registry_path() -> Path
```

Returns `Path(os.environ["FUSION_MCP_REGISTRY"])` when set, else `<monorepo>/architecture/mcp-server-registry.json` if present (dev layout), else the bundled copy `fusion_core/mcp-server-registry.json` shipped inside the package (pip-installed / CI checkouts without the monorepo). Read at **call** time, not import time. Keep the two copies in sync when adding a server.

## load_registry

```python
def load_registry(path: str | Path | None = None) -> tuple[McpServerRecord, ...]
```

Loads and validates the registry (mtime-invalidated cache keyed by path string, same pattern as `load_settings`; on-disk edits picked up at runtime). Each entry becomes a frozen `McpServerRecord` (`name`, `transport`, `purpose`, `credential_mode`, `status`, `url_prefixes`, `tool_scopes`, `port`, `notes`).

Raises (fail visibly, never a silent fallback):
- `FileNotFoundError` — file missing
- `RegistryIntegrityError` — invalid JSON, non-object root, unsupported `version`, missing/malformed `servers` list, duplicate server names, or per-entry field violations (non-empty string `name`; `status` in lifecycle; `credential_mode` in modes; string `transport`/`purpose`/`notes`; `port` int or null; `url_prefixes`/`tool_scopes` lists of strings)

## resolve_server

```python
def resolve_server(name: str, *, registry_path: str | Path | None = None) -> McpServerRecord
```

Exact-name lookup. Empty `name` → `ValueError`; not found → `RegistryLookupError` (also a `KeyError`).

## check_approval

```python
def check_approval(url: str, tool_scope: str, *, registry_path: str | Path | None = None) -> ApprovalVerdict
```

Returns a frozen `ApprovalVerdict(action, server, reason)`. The verdict is a **result, not an error** — enforcement policy belongs to callers.

Match rules:
- URL match: `url.rstrip("/").startswith(prefix.rstrip("/"))` — same semantics as cowork `Permission.matches` `url:` scopes. Empty `url_prefixes` never matches.
- Multiple matches: **longest URL prefix wins**.
- Status != `approved` → `denied` (reason names the status).
- Tool scope: exact match, `*` (all), or namespace wildcard `ns.*` (matches any scope starting with `ns.`).
- No URL match → `unknown` (per-URL operator approval decides, e.g. the cowork `mcp_client` node's explicit `approve("mcp_client", scope="url:...")`).

Empty `url` or `tool_scope` → `ValueError`.

```python
verdict = check_approval("http://127.0.0.1:11438/mcp", "cowork.skills.list")
if verdict.action != "approved":
    logger.warning("mcp connect not registry-approved: %s", verdict.reason)
```

## clear_cache

```python
def clear_cache() -> None
```

Clears the registry cache. Access via `fusion_core.mcp_registry.clear_cache()` (not re-exported at package level — name clashes with `config.clear_cache`).

## Design notes

- **Registry format**: JSON sidecar `architecture/mcp-server-registry.json` (stdlib `json`, zero new runtime deps), mirrored as a bundled `fusion_core/mcp-server-registry.json` so pip installs and CI checkouts resolve without the monorepo. The table in `architecture/mcp-server-registry-1029.md` stays as the human summary — edit all three when adding a server.
- **Non-goals** (deliberate): credential storage/issuance (identity service owns it), network calls / MCP client, enforcement defaults (callers decide warn vs block).
- Consumption-side enforcement is natural-owner split: fusion-cowork's `mcp_client` node calls `check_approval()` on connect (warn-level, overridable by explicit operator approval); fusion-agent-studio's ConnectorManager consumes the same registry via fusion-core.
