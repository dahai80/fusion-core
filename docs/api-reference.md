---
doc_id: fusion-core/api-reference
product: fusion-core
class: platform
entry: developer
doc_type: api-reference
title: {zh: "Fusion-Core API 参考", en: "Fusion-Core API Reference"}
audience: engineer
status: draft
version: 0.10.1
updated: 2026-11-07
owner: @dahai
source_of_truth:
  - fusion_core/__init__.py
  - README.md
---

# Fusion-Core API 参考 / API Reference

入口：`from fusion_core import ...`。14 个模块的详细说明在 `docs/<module>.md`（如 `docs/mlx_client.md`），本文件只列公开符号与签名要点，以源码为准。

## mlx_client

- `FusionMLXClient` — unified MLX inference client. Methods: `chat(messages, model=None, tools=None, temperature=0.7, max_tokens=4096, stream=False, total_deadline=None, **kwargs)` (`stream=True` raises; use `stream_chat`), `chat_text`, `stream_chat` (raises `StreamError(delivered=, resume_offset=)` on all stream-failure paths), `embed`, `health()` (probe client with 1s throttle), `get_server_stats()`.
- `create_async_client(*, backend="mlx", base_url=None, api_key=None, model=None, transport=None, timeout=120.0, max_retries=2, retry_delay=1.0)` — only `backend="mlx"` is supported.
- Types: `LLMResponse` (`.content`, `.tool_calls`, `.finish_reason`, `.usage`), `EmbeddingResponse`, `ServerStats`, `StreamError`.

## parse

- `parse_llm_json(text) -> dict | list` — raises `ParseError`, no fallback.
- `parse_llm_json_safe(text, *, default)` — `default` required, must be dict or list.
- `parse_llm_json_lenient(text)` — `raw_decode` first object, scan cap 200k chars.
- `strip_code_fence(text) -> str`.

## config

- `load_settings(path=None) -> dict` — default `~/.fusion-mlx/settings.json`, mtime-invalidated cache.
- `resolve_api_key(...)`, `load_api_key(name="fusion-mlx")`, `get_env(key, default=None, *, cast=str)`.
- Convention resolvers: `client_key()`, `admin_key()`, `gateway_admin_key()`, `profile()`, `gateway_base_url()`, `service_port(app, default)`, `service_data_dir(app, default)`, `default_mlx_base_url()`, `clear_cache()`.
- Details: `docs/config-reference.md`.

## logging

- `setup_logging(name, *, level="INFO", json_format=False, log_file=None, propagate=True)` — idempotent.
- `get_logger(name)` — package ships a `NullHandler` for library mode.

## http_client

- `get_async_client(base_url, timeout=120.0)` — per-event-loop pooled client, LRU cap 8, timeout is part of the pool key (#16).
- `with_retry(...)` — full jitter; `disable=`, `verify_gateway=`, `total_deadline=`; exhausted raises `RetryExhaustedError` (status) / `RetryTimeoutError`.
- `gateway_circuit_breaker_ok(...)` — probes gateway `/readyz`.
- `set_metrics_callback(cb)` / `get_metrics_snapshot()` / `reset_metrics()`.
- Retry constants: `RETRY_STATUS`, `RETRY_EXCEPTIONS` (single source of truth).

## http

- `create_app(name, *, cors_origins=None, cors_credentials=False, version="0.1.0", enable_health=True, readiness_probe=None)` — `/health` liveness always; `/ready` mounted only when `readiness_probe` given (200 ready / 503 not_ready, unauth).
- `install_auth(app, *, api_keys=None)` — pure-ASGI middleware; keys stay encapsulated in the middleware instance.
- `standard_error_handler` — 422 and 500 sanitized equally; 401 keeps the request_id of the outer middleware.

## prompt / prompt_registry

- `PromptManager` — engine only, no domain content; missing dir raises `FileNotFoundError`; mtime-gated cache, `clear_cache()` forces refresh.
- `PromptVersionRegistry`, `PromptVersionRecord`, `compute_version_id`, `is_valid_version_id` — content-addressed 16-hex `version_id`, write-once snapshots with sha256 re-verification, `render()` / `replay(prompt_version, client)`. Storage: `versions/<id>.txt` + `index.json`.

## guard_client

- `FusionGuardClient` — UDS JSON-RPC 2.0 to fusion-guard, newline-framed (1 MiB cap), 2s default timeout, reconnect-per-call with one retry. Methods map 1:1 to `guard.ping` / `guard.evaluate` / `guard.rule.list` / `guard.redact` / `guard.reveal` / `guard.confirm` / `guard.tcc.status` / `guard.tcc.events` / `guard.audit.verify`.
- Types: `GuardVerdict` (block is a result, not an error), `GuardRule`, `RedactResult`, `ChainVerification`, `AllChainsVerification`, `GuardError` + 8 typed subclasses (`GuardUnauthorizedError` -32001, `GuardRateLimitError` -32002, `StaleEpochError` -32003, `GuardEngineError` -32010).

## tenant

- `TenantContext` (frozen-slots dataclass: `tenant_id` / `user_id` / `role` / `jti` / `scopes` / `authenticated`), `current()`, `set_context()`, `reset()`, `has_scope()`, `from_mapping()`.
- `decode_jwt_claims`, `tenant_context_from_token` — base64url + `exp` only, no signature verify; inject a `verify_jwt` hook (sync or async).
- `TenantMiddleware` / `install_tenant_middleware` — fail-closed (missing `X-Tenant-Id` → 401; JWT `tid` mismatch → 401; missing bearer when `require_jwt=True` → 401); `get_tenant_dep` for FastAPI DI.
- Log records get `tenant_id` / `user_id` auto-injected from `current()` by `_JsonFormatter`.

## dsl_schema

- `SOCRATIC_DSL_V3_SCHEMA`, `validate_dsl(dsl) -> bool`, `validate_dsl_str(s) -> tuple[bool, str]` — Draft 2020-12; jsonschema is a lazy import, enable via the `[schema]` extra.

## mcp_registry / mcp_client

- `load_registry()` (mtime cache), `resolve_server()`, `check_approval(url, tool_scope)` — longest prefix wins, non-approved → denied, no match → `unknown`; `McpServerRecord`, `ApprovalVerdict`, `RegistryIntegrityError` on malformed registry.
- `McpToolClient.call_tool(tool, arguments, timeout_s=30)` — minimal JSON-RPC over stdio, spawn-per-call, returns `{"ok": bool, "text": str, "raw": ...}` (`isError` is a result, not an error); `McpToolError` for transport/protocol/timeout failures; `known_servers()`.

## proc

- `set_process_title(title)` — fail-open; missing `setproctitle` logs at DEBUG, never blocks startup. `[proc]` extra.
