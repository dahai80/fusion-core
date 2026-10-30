from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_FILENAME = "mcp-server-registry.json"
REGISTRY_STATUS_LIFECYCLE = ("proposed", "reviewed", "approved", "deprecated")
CREDENTIAL_MODES = ("identity_issuance", "env_ref")
APPROVAL_ACTIONS = ("approved", "denied", "unknown")


class McpRegistryError(Exception):
    pass


class RegistryIntegrityError(McpRegistryError):
    pass


class RegistryLookupError(McpRegistryError, KeyError):
    pass


@dataclass(frozen=True, slots=True)
class McpServerRecord:
    name: str
    transport: str = ""
    purpose: str = ""
    credential_mode: str = ""
    status: str = ""
    url_prefixes: tuple[str, ...] = ()
    tool_scopes: tuple[str, ...] = ()
    port: int | None = None
    notes: str = ""
    stdio_command: dict | None = None


@dataclass(frozen=True, slots=True)
class ApprovalVerdict:
    action: str
    server: str | None = None
    reason: str = ""


_REGISTRY_CACHE: dict[str, tuple[tuple[McpServerRecord, ...], float]] = {}


def default_registry_path() -> Path:
    env_path = os.environ.get("FUSION_MCP_REGISTRY", "")
    if env_path:
        return Path(env_path)
    monorepo = Path(__file__).resolve().parents[2] / "architecture" / DEFAULT_REGISTRY_FILENAME
    if monorepo.is_file():
        return monorepo
    bundled = Path(__file__).resolve().parent / DEFAULT_REGISTRY_FILENAME
    logger.debug("default_registry_path: monorepo sidecar missing, using bundled %s", bundled)
    return bundled


def load_registry(path: str | Path | None = None) -> tuple[McpServerRecord, ...]:
    resolved = Path(path) if path is not None else default_registry_path()
    key = str(resolved)
    try:
        mtime = resolved.stat().st_mtime
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"mcp server registry not found: {resolved}") from exc
    cached = _REGISTRY_CACHE.get(key)
    if cached is not None and cached[1] == mtime:
        logger.debug("load_registry: cache hit %s", key)
        return cached[0]
    servers = _parse_registry(resolved)
    _REGISTRY_CACHE[key] = (servers, mtime)
    logger.debug("load_registry: loaded %d servers from %s", len(servers), key)
    return servers


def resolve_server(name: str, *, registry_path: str | Path | None = None) -> McpServerRecord:
    if not name:
        raise ValueError("resolve_server: name required")
    for record in load_registry(registry_path):
        if record.name == name:
            return record
    raise RegistryLookupError(f"mcp server {name!r} not in registry")


def check_approval(
    url: str,
    tool_scope: str,
    *,
    registry_path: str | Path | None = None,
) -> ApprovalVerdict:
    if not url:
        raise ValueError("check_approval: url required")
    if not tool_scope:
        raise ValueError("check_approval: tool_scope required")
    servers = load_registry(registry_path)
    target = url.rstrip("/")
    matches = []
    for record in servers:
        for prefix in record.url_prefixes:
            normalized = prefix.rstrip("/")
            if target.startswith(normalized):
                matches.append((len(normalized), record))
    if not matches:
        return ApprovalVerdict(
            action="unknown",
            server=None,
            reason=f"no registry server matches url {url!r}",
        )
    matches.sort(key=lambda m: m[0])
    record = matches[-1][1]
    if record.status != "approved":
        return ApprovalVerdict(
            action="denied",
            server=record.name,
            reason=f"server status is {record.status!r}, only approved servers may be referenced",
        )
    if _scope_matches(record.tool_scopes, tool_scope):
        return ApprovalVerdict(
            action="approved",
            server=record.name,
            reason=f"tool scope {tool_scope!r} approved for {record.name!r}",
        )
    return ApprovalVerdict(
        action="denied",
        server=record.name,
        reason=f"tool scope {tool_scope!r} not in approved scopes {record.tool_scopes}",
    )


def clear_cache() -> None:
    _REGISTRY_CACHE.clear()


def _scope_matches(scopes: tuple[str, ...], requested: str) -> bool:
    for scope in scopes:
        if scope == "*" or scope == requested:
            return True
        if scope.endswith(".*") and requested.startswith(scope[:-1]):
            return True
    return False


def _parse_registry(path: Path) -> tuple[McpServerRecord, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryIntegrityError(f"mcp server registry {path} is not valid json: {exc}") from exc
    if not isinstance(raw, dict):
        raise RegistryIntegrityError(f"mcp server registry {path} must be a json object")
    version = raw.get("version")
    if version is not None and version != 1:
        raise RegistryIntegrityError(f"unsupported registry version {version!r} in {path} (expected 1)")
    servers = raw.get("servers")
    if not isinstance(servers, list):
        raise RegistryIntegrityError(f"mcp server registry {path} must have a 'servers' list")
    records = []
    seen: set[str] = set()
    for i, entry in enumerate(servers):
        record = _parse_server(entry, path, i)
        if record.name in seen:
            raise RegistryIntegrityError(f"duplicate server name {record.name!r} in {path}")
        seen.add(record.name)
        records.append(record)
    return tuple(records)


def _parse_server(entry: object, path: Path, index: int) -> McpServerRecord:
    where = f"{path} servers[{index}]"
    if not isinstance(entry, dict):
        raise RegistryIntegrityError(f"{where} must be an object")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise RegistryIntegrityError(f"{where} requires a non-empty string 'name'")
    status = entry.get("status", "")
    if status not in REGISTRY_STATUS_LIFECYCLE:
        raise RegistryIntegrityError(f"{where} ({name!r}) status {status!r} not in {REGISTRY_STATUS_LIFECYCLE}")
    credential_mode = entry.get("credential_mode", "")
    if credential_mode not in CREDENTIAL_MODES:
        raise RegistryIntegrityError(
            f"{where} ({name!r}) credential_mode {credential_mode!r} not in {CREDENTIAL_MODES}"
        )
    transport = entry.get("transport", "")
    if not isinstance(transport, str):
        raise RegistryIntegrityError(f"{where} ({name!r}) transport must be a string")
    purpose = entry.get("purpose", "")
    if not isinstance(purpose, str):
        raise RegistryIntegrityError(f"{where} ({name!r}) purpose must be a string")
    notes = entry.get("notes", "")
    if not isinstance(notes, str):
        raise RegistryIntegrityError(f"{where} ({name!r}) notes must be a string")
    port = entry.get("port")
    if port is not None and not isinstance(port, int):
        raise RegistryIntegrityError(f"{where} ({name!r}) port must be an int or null")
    url_prefixes = _parse_str_tuple(entry.get("url_prefixes", []), "url_prefixes", where, name)
    tool_scopes = _parse_str_tuple(entry.get("tool_scopes", []), "tool_scopes", where, name)
    stdio_command = _parse_stdio_command(entry.get("stdio_command"), where, name)
    return McpServerRecord(
        name=name,
        transport=transport,
        purpose=purpose,
        credential_mode=credential_mode,
        status=status,
        url_prefixes=url_prefixes,
        tool_scopes=tool_scopes,
        port=port,
        notes=notes,
        stdio_command=stdio_command,
    )


def _parse_stdio_command(raw: object, where: str, name: str) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RegistryIntegrityError(f"{where} ({name!r}) stdio_command must be an object")
    command = raw.get("command")
    if not isinstance(command, str) or not command:
        raise RegistryIntegrityError(f"{where} ({name!r}) stdio_command requires a non-empty string 'command'")
    args = raw.get("args", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise RegistryIntegrityError(f"{where} ({name!r}) stdio_command args must be a list of strings")
    env = raw.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise RegistryIntegrityError(f"{where} ({name!r}) stdio_command env must be a string-to-string object")
    return {"command": command, "args": list(args), "env": dict(env)}


def _parse_str_tuple(raw: object, field_name: str, where: str, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise RegistryIntegrityError(f"{where} ({name!r}) {field_name} must be a list of strings")
    return tuple(raw)
