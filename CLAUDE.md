# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`fusion-core` is the in-tree shared technical foundation for the Fusion ecosystem's 20+ Python domain projects. It is **pure-tech, zero business logic** — one well-tested implementation of each primitive the domain apps duplicate (LLM client, JSON parse, config, logging, httpx pool + retry, FastAPI factory, prompt templates, guard client, tenant fabric). Most Python projects in `/Users/dahai/fusion` do `pip install -e fusion-core` and depend on it.

It depends on nothing Fusion-specific. The only runtime dependency is `httpx` (pinned `>=0.27.0,<1.0`). `fastapi`/`uvicorn`/`pydantic` are optional (`[fastapi]` extra); `http` and `tenant` submodules import them lazily and degrade gracefully.

## Build / test / lint

```bash
source .venv/bin/activate                          # repo-root shared venv (REQUIRED)
pip install -e ".[test]"                           # core + test stack (pytest, ruff, fastapi)

pytest tests/                                      # unit suite (default: excludes integration)
pytest tests/test_http.py::test_bar -v             # single test
pytest tests/ -m integration                       # integration suite (needs real fusion-mlx engine)
pytest tests/ --cov=fusion_core --cov-branch       # with coverage (gate: 90%)

ruff check .                                       # lint
ruff format .                                      # format (CI runs `ruff format --check`)
ruff format --check .                              # verify formatting without writing
```

- **Python**: `>=3.12` (repo `.python-version` pins `3.12`; CI matrix tests 3.12/3.13/3.14).
- **Build backend**: setuptools, `[tool.setuptools.packages.find]` includes `fusion_core*`.
- **pytest config**: `asyncio_mode=auto`, `testpaths=["tests"]`, `addopts="-m 'not integration'"`. The `integration` marker guards tests that start/stop a real fusion-mlx engine.
- **ruff**: `target-version=py312`, `line-length=120`, `select=["E","F","W","I","UP","B","SIM"]`, `ignore=["E501","B008","SIM117"]`. `known-first-party=["fusion_core"]`.

## Release flow

Tags matching `v*` trigger `.github/workflows/release.yml`, which builds wheel + sdist and attaches them to a GitHub Release. Before tagging, keep **4-way version consistency**: `pyproject.toml` `[project].version`, `fusion_core/__init__.py` `__version__` (resolved via `importlib.metadata`), the git tag, and the published GitHub Release must all agree. Conventional-commit scopes used historically: `feat(tenant)`, `fix(http)`, `release(vX.Y.Z)`, `style(docs)`.

## Architecture — the 10 modules

All public symbols are re-exported from `fusion_core/__init__.py`. Importing `fusion_core` performs **no I/O** — no env read, no file read, no network connection. Every default (`FUSION_MLX_URL`, `FUSION_GUARD_SOCK`, settings path) is resolved lazily at call time. This is a hard design rule: the library must be safe to import anywhere.

### `mlx_client` — unified MLX inference client
`FusionMLXClient` + `create_async_client(*, backend=)`. Chat / embedding / streaming against fusion-mlx (default `localhost:11434`, overridable via `FUSION_MLX_URL` — point at fusion-gateway for multi-node). **Retry is delegated to `http_client.with_retry`** — mlx_client owns no retry logic. `chat(total_deadline=)` is an explicit named param for the end-to-end budget. `stream_chat` raises `StreamError(delivered=, resume_offset=)` for **all** stream-failure paths (mid-stream severed OR retriable-exhausted-no-output); non-retriable 4xx still raise the original `HTTPStatusError`. `health()` reuses a throttled (1s) `probe_client` so health checks never leak main connections.

### `parse` — JSON from LLM output
`parse_llm_json` raises `ParseError` on invalid JSON (no silent `{}` fallback). `parse_llm_json_safe` requires an explicit `default` (must be dict/list). `parse_llm_json_lenient` uses `raw_decode` to extract the first JSON object (scan cap 200k chars). `strip_code_fence` strips ```` ```json ```` fences. **Failures are visible, never silent** — this is deliberate; do not add fallbacks.

### `config` — lazy settings + api_key
`load_settings` (mtime-invalidated cache: settings file mtime change → cache miss), `resolve_api_key`, `load_api_key`, `get_env`, `default_mlx_base_url`, `clear_cache`. API key resolution order: env var → settings.json `auth.api_key` → empty string. `load_api_key(name="fusion-mlx")` derives env var `FUSION_MLX_API_KEY` and raises `KeyError` if absent.

### `logging` — idempotent logging init
`setup_logging`, `get_logger`. Every `setLevel` applies; `propagate` defaults True so host root still receives logs; package-level `NullHandler` for library mode. Optional JSON format.

### `http_client` — httpx pool + retry (single source of truth)
`get_async_client` (per-event-loop connection pool, `OrderedDict` LRU cap 8, evicts only same-loop keys; tracks owning loop so `close_all_sync` can close cross-loop clients). `with_retry` — full jitter; `disable=` + `verify_gateway=` hand retry off to fusion-gateway's circuit breaker (avoids double-retry); `total_deadline=` caps the whole retry budget; exhaustion → `RetryExhaustedError` / `RetryTimeoutError`. `gateway_circuit_breaker_ok` probes gateway `/readyz`. `close_all` / `close_all_sync` / `set_metrics_callback` / `get_metrics_snapshot` / `reset_metrics`. **`RETRY_STATUS` / `RETRY_EXCEPTIONS` are the single source of truth for what gets retried** — do not duplicate retry decisions in callers.

### `http` — FastAPI app factory (optional dep)
`create_app`, `install_auth`, `standard_error_handler`. Pure-ASGI middleware. `install_auth` re-orders `user_middleware` so request_id is outermost (401 carries the same id). SSE is not truncated. Auth keys are encapsulated in the middleware instance, never on `app.state`. Whitelist paths are `rstrip`-normalized. `create_app(readiness_probe=)` mounts `/ready` (readiness: 200 ready / 503 not_ready, unauth) alongside `/health` (liveness). Unauth path set: `/health`, `/ready`, `/docs`, `/openapi.json`, `/redoc`.

### `prompt` — prompt-template manager
`PromptManager`. Engine only, no domain content. Missing dir raises `FileNotFoundError`. **mtime-gated cache** — on-disk edits picked up at runtime (mtime change invalidates the entry); `clear_cache()` forces full refresh.

### `guard_client` — fusion-guard UDS JSON-RPC client
`FusionGuardClient` + `GuardVerdict` / `GuardRule` / `RedactResult` / `ChainVerification` / `GuardError` + 8 typed subclasses. Pure-Python client over Unix Domain Socket for **fusion-guard** (the zero-trust action-authorization daemon). Newline-framed (`0x0A`, 1 MiB cap), 2s default timeout, per-call reconnect-on-drop with one retry. Methods map 1:1 to `guard.ping` / `guard.evaluate` / `guard.rule.list` / `guard.redact` / `guard.reveal` / `guard.confirm` / `guard.tcc.status` / `guard.tcc.events` / `guard.audit.verify`. **Block verdicts are results, not errors** (E5) — `evaluate()` returns `GuardVerdict(action="block")`. RPC error codes map to typed exceptions (`GuardUnauthorizedError` -32001, `GuardRateLimitError` -32002, `StaleEpochError` -32003, `GuardEngineError` -32010). Default socket `/tmp/fusion-guard.sock` resolved lazily from `FUSION_GUARD_SOCK`. Native `fg-pyo3` stays an optional fast path.

### `tenant` — L1 multi-tenant fabric
`tenant/` subpackage: `context.py` (`TenantContext` frozen-slots dataclass over `contextvars`, `current`/`set_context`/`reset`/`has_scope`/`from_mapping`), `jwt_utils.py` (`decode_jwt_claims`, `tenant_context_from_token`), `middleware.py` (`TenantMiddleware`, `install_tenant_middleware`, `get_tenant_dep`). `TenantMiddleware` is **fail-closed** ASGI: exempt paths skip, missing `X-Tenant-Id` → 401, JWT `tid ≠ X-Tenant-Id` → 401, missing bearer when `require_jwt=True` → 401; binds context for the request then `reset()` in `finally` (no cross-request leak). `jwt_utils` does base64url decode + `exp` check with **no PyJWT dependency, no signature verify** — real signature verification is injected via the `verify_jwt` hook (sync or async; an awaitable return is awaited so a blocking verifier does not stall the event loop). The hook implementation lives in **fusion-identity**, not here. `_JsonFormatter` auto-injects `tenant_id`/`user_id` from `current()` into log records. This is the Python variant of a trilingual mirror (Rust/Swift variants live in their own repos).

### `dsl_schema` — Socratic DSL v3.0 JSON Schema
`SOCRATIC_DSL_V3_SCHEMA` (Draft 2020-12 dict) + `validate_dsl(dict) -> bool` + `validate_dsl_str(str) -> tuple[bool, str]`. Canonical contract shared across fusion-k12-teacher / fusion-bench / fusion-artifacts-engine / fusion-trainer. Enforces `meta` (id/grade/topic/visual_type/fallback_type), `parameters`, `entities`, `pipeline`; `visual_type` enum (8 values), `fallback_type` enum (2 values); pipeline step required fields. All objects `additionalProperties: false` (closed contract). `jsonschema` is **lazy-imported inside validators only** — `import fusion_core` stays I/O-free; install `[schema]` extra to enable. Source spec: `architecture/k12-train-model-autumation-1009.md`.

## Cross-project boundaries (upstream deps, not editable here)

This repo consumes but does not own these services — file an issue upstream first, then a PR, per the monorepo flow:
- **fusion-mlx** (`~/fusion/fusion-mlx`) — inference engine on `:11434`. Integration tests start/stop it via `~/fusion/fusion-mlx/start.sh start|stop`. Model downloads via `https://hf-mirror.com`.
- **fusion-guard** — UDS daemon backing `guard_client`. Real-socket tests need it running.
- **fusion-gateway** (`:11432`) — API gateway; `with_retry(verify_gateway=True)` probes its `/readyz` circuit breaker.
- **fusion-identity** — owns the real `verify_jwt` signature-verification hook injected into `TenantMiddleware`.

## Conventions

- 4-space indentation (multiples of 4). No docstrings. Always include logging (module-level `logger = logging.getLogger(__name__)`).
- `from __future__ import annotations` at the top of every module.
- Fail visibly: parse raises, guard block = result, tenant fail-closed. Never add silent fallbacks.
- Optional deps (`fastapi`, `fg-pyo3`) are imported in `try/except ImportError` blocks and degrade gracefully — never make them hard requirements.
- Tests use `pytest-asyncio` in `auto` mode; async test functions need no decorator.
- Commits/branches/PRs happen inside this repo (default branch `master`), never at the monorepo root (there is no root `.git`).
