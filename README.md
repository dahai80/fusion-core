# fusion-core

> Shared technical foundation for the Fusion ecosystem. Pure-tech, zero business logic.
>
> [中文文档](README_CN.md)

fusion-core is the common technical base shared by the 20+ Python domain projects in the Fusion ecosystem ("一核九端"). It eliminates 7+ duplicate LLM clients and 10+ copies of `_parse_json` by providing one well-tested implementation of each primitive: an LLM client, JSON parsing, config loading, logging, an httpx connection pool with retry, a FastAPI factory, and prompt-template management.

It depends on nothing Fusion-specific. `import fusion_core` triggers no I/O (no env read, no file read, no network connection) — safe to import anywhere.

## Install

```bash
pip install -e fusion-core            # core (httpx only; zero business deps, no pydantic)
pip install -e "fusion-core[test]"    # + test stack (pytest, ruff, fastapi)
pip install -e "fusion-core[fastapi]" # + fastapi, uvicorn, pydantic
```

Requirements: Python >=3.12.

## 7 modules at a glance

| Module | Key symbols | Purpose |
|--------|-------------|---------|
| `mlx_client` | `FusionMLXClient`, `create_async_client(*, backend=)`, `LLMResponse`, `EmbeddingResponse`, `ServerStats`, `StreamError` | Unified MLX inference client (chat / embedding / stream). Default base_url `localhost:11434` (resolved at runtime from `FUSION_MLX_URL`; point it at fusion-gateway for multi-node). Retry delegated to `http_client`. `chat(total_deadline=)` is an explicit named param (R5) for the end-to-end budget; `**kwargs` allowlist-passes (`top_p`/`seed` etc.); `stream_chat` raises `StreamError(delivered=, resume_offset=)` for **all** stream-failure paths (mid-stream severed OR retriable-exhausted-no-output) so callers have ONE stream-failure type (H4/R4); non-retriable 4xx still raise the original `HTTPStatusError`; `create_async_client(model=...)` records a default model; `health()` reuses a `probe_client` with 1s throttle (no main-connection leak); `get_server_stats()` returns a `ServerStats` dataclass |
| `parse` | `parse_llm_json` (raises `ParseError`, no fallback), `parse_llm_json_safe` (explicit `default` required, must be dict/list), `parse_llm_json_lenient` (`raw_decode` extracts first object, scan cap 200k), `strip_code_fence` | Parse JSON from LLM output. **Failures are visible, never silent** |
| `config` | `load_settings` (mtime-invalidated cache), `resolve_api_key`, `load_api_key`, `get_env`, `default_mlx_base_url`, `clear_cache` | Lazy config load + api_key resolution + cache invalidation (settings file mtime change → cache miss) |
| `logging` | `setup_logging`, `get_logger` | Idempotent logging init (every `setLevel` applies; `propagate` defaults True so host root still receives logs; package-level `NullHandler` for library mode). Optional JSON format |
| `http_client` | `get_async_client` (per-loop connection pool, `OrderedDict` LRU cap 8, evicts only same-loop keys), `gateway_circuit_breaker_ok` (probes gateway `/readyz`, H3/E4), `with_retry` (full jitter; `disable=` + `verify_gateway=` to hand retry off to gateway circuit breaker safely; `total_deadline=` end-to-end budget; exhausted → `RetryExhaustedError` / `RetryTimeoutError`), `close_all`, `close_all_sync`, `set_metrics_callback`, `get_metrics_snapshot`, `reset_metrics` | httpx async client pool + retry. Single source of truth for retry codes/exceptions: `RETRY_STATUS` / `RETRY_EXCEPTIONS` |
| `http` | `create_app`, `install_auth`, `standard_error_handler` | FastAPI app factory + pure-ASGI middleware (`install_auth` re-orders `user_middleware` so request_id is outermost — 401 carries the same id, H1/E1; SSE not truncated; auth keys encapsulated in the middleware instance, never on `app.state`; 422 and 500 sanitized equally; whitelist paths `rstrip`-normalized) |
| `prompt` | `PromptManager` | Prompt-template management (engine only, no domain content; missing dir raises `FileNotFoundError`; **mtime-gated cache** — on-disk edits are picked up at runtime (mtime change invalidates the entry), `clear_cache()` forces a full refresh (E3)) |

## Usage

### LLM chat + JSON parse

```python
from fusion_core import create_async_client, parse_llm_json, get_logger

log = get_logger(__name__)

client = create_async_client(
    base_url="http://localhost:11434",
    api_key="...",
    model="qwen2.5-7b",
)
resp = await client.chat(messages=[{"role": "user", "content": "Return JSON"}])
data = parse_llm_json(resp.content)  # invalid JSON raises ParseError, not silent {}
```

### Streaming (with mid-stream recovery envelope)

```python
collected = []
try:
    async for chunk in client.stream_chat(messages=[...]):
        collected.append(chunk)
except StreamError as e:
    # ALL stream-failure paths raise StreamError (H4/R4): mid-stream severed
    # (e.delivered > 0) OR retriable-exhausted-no-output (e.delivered == 0).
    # Non-retriable 4xx raise HTTPStatusError instead (bad request, not severed).
    log.warning("stream severed after %d chars, resume at %d", e.delivered, e.resume_offset)
```

### End-to-end deadline

```python
# total_deadline caps the whole retry budget, not just one request
resp = await client.chat(messages=[...], total_deadline=30.0)
```

### Hand retry off to fusion-gateway (avoid double-retry)

```python
from fusion_core import with_retry

# when fusion-gateway's circuit breaker owns retry, disable core's own retry.
# verify_gateway=True probes gateway /readyz first; if the breaker is open or
# the gateway is unreachable, core falls back to its own retry (H3/E4 — no
# capability vacuum).
resp = await with_retry(fn, disable=True, verify_gateway=True)
```

### FastAPI factory

```python
from fusion_core.http import create_app, install_auth

# cors_credentials defaults False; "*" with credentials=True raises ValueError
app = create_app("my-svc", cors_origins=["https://example.com"], cors_credentials=True)
install_auth(app, api_keys=["secret"])  # request_id is outermost middleware; 401 also carries it
```

### Embeddings

```python
resp = await client.embed("hello world", model="bge-m3")
print(resp.vector)  # single input → .vector
batch = await client.embed(["a", "b"], model="bge-m3")
print(batch.vectors)  # batch input → .vectors list
```

## Design principles

- **Pure tech, zero business**: no K12 grading, no finance thresholds, no medical contraindications, no DAG nodes. When a boundary is blurry, it stays out.
- **Non-invasive, standalone**: `fusion_core` imports on its own, depends on no other fusion-* project.
- **Fail visibly, no fallbacks**: `parse_llm_json` raises instead of returning `{}`; client failure raises instead of returning empty content; `get_server_stats` failure raises instead of returning `{}`.
- **Tests are isolatable**: `-m 'not integration'` skips real-engine tests; integration fixtures record `was_running` and only stop engines they started.

## Boundary declaration — cluster capabilities belong to fusion-gateway

fusion-core is a **single-process, single-engine client library**, not a cluster control plane. The PRD §0.2 four iron rules (pure tech / non-invasive / fail-visible / isolatable tests) draw the boundary. The cluster-level capabilities below are **already implemented and live in fusion-gateway (Go, :11432)** — core does not rebuild them (rebuilding = duplication + violates "pure tech, zero business"). Core only does the minimal "don't conflict with gateway behavior" fixes.

| Capability | gateway implementation | core action |
|------------|------------------------|-------------|
| Endpoint registry / routing / failover | `discovery` (node register/health/evict) + `router/engine` | `default_mlx_base_url()` reads `FUSION_MLX_URL`; point at gateway for multi-node |
| Circuit breaker | `router/circuit_breaker.go:CircuitBreaker` | `with_retry(disable=True)` disables core retry, hands to gateway breaker, avoids double-retry |
| Per-endpoint concurrency gate | `router/engine.go:MaxConcurrent` | core pool adds no concurrency cap |
| Model registry model→endpoint | `router` routes by model | caller passes model, gateway resolves endpoint; core holds no topology |
| Metrics (Prometheus) | `observability/metrics` (circuitBreakerState/Trips, routeDecisions, requestDuration, requestTotal) | core does not double-instrument (`http_client` metrics callback kept for single-process use) |
| Agent scheduling (slots/queue/cancel) | routing-layer concurrency governance | scheduling is business orchestration → fusion-cowork / agent-studio |

**Multi-node access**: `export FUSION_MLX_URL=http://<gateway-host>:11432/v1` — core then hits the gateway, which routes to cluster nodes. Core itself is always a single `base_url` view.

See `audit/fusion-core-audit-report-0824.md` §六 for the landing state.

## Migration guide (self-built client → fusion-core)

Projects still on bare `httpx` to MLX (no retry/timeout/metrics): `fusion-health`, `fusion-science`, `fusion-rag`, `fusion-simulation`, `fusion-code-modelization`, `fusion-security`, `fusion-trainer`.

Steps (one PR per project, see `architecture/venv-fix-0823.md` §5):

1. `httpx.AsyncClient.post(.../chat/completions)` → `create_async_client(...)` + `await client.chat(...)`
2. Self-built `_parse_json` → `parse_llm_json` (raises on failure, not `return {}`)
3. No `base_url` → uses core default `localhost:11434/v1` (aligned with fusion-mlx `start.sh`, not the gateway)
4. Silent degrade `return LLMResult(content="", error=...)` → raise (fixes audit D-H3 silent failure)

```python
# Before (health llm_gateway.py silent failure)
try:
    resp = await client.post(f"{url}/chat/completions", ...)
    return LLMResult(content=resp.json()["choices"][0]["message"]["content"])
except Exception as e:
    return LLMResult(content="", error=str(e))  # silent! downstream proceeds on empty

# After
from fusion_core import create_async_client

self._client = create_async_client(base_url=url, api_key=key, model=model)
result = await self._client.chat(messages=messages)  # raises on failure
return LLMResult(content=result.content, model=result.model)
```

## PRD §7.1 acceptance (measured, not declared)

| Acceptance item | Status | Evidence |
|-----------------|--------|----------|
| `import fusion_core` triggers no I/O (no env/file/conn) | ✅ | `mlx_client` dropped module-level `os.environ.get`; resolves `default_mlx_base_url()` at call time; `tests/test_config.py::TestImportTimeIsolation` guards with env-get spy |
| grep source has no `or {}` / `or []` / `or ""` silent fallback | ✅ | `resolve_api_key` dropped `or ""`; `get_server_stats` raises, not `return {}` |
| LLM client holds no retry logic (single responsibility) | ✅ | `mlx_client.chat` routes to `http_client.with_retry`; single source `RETRY_STATUS`/`RETRY_EXCEPTIONS` |
| Integration tests hit 11434 (PRD §7.1) | ✅ | `DEFAULT_MLX_PORT = 11434`, aligned with fusion-mlx `start.sh`; integration fixture `was_running` doesn't kill user's engine |
| CORS `*`+credentials rejected | ✅ | `create_app(cors_origins=["*"], cors_credentials=True)` raises `ValueError`; credentials defaults False |

## Testing

```bash
pytest tests/ -m "not integration"   # unit: 164 passed, 1 skipped
pytest tests/ -m integration          # real fusion-mlx engine (starts/stops its own)
ruff check . && ruff format --check . # lint clean
```

## Documentation

- [中文文档 (Chinese)](README_CN.md)
- Module reference: [`docs/`](docs/) — per-module API signatures, params, returns, exceptions, examples
- Audit: `../audit/fusion-core-audit-report-0824.md` — 28 findings, 21 core fixes + 7 boundary declarations
- PRD: `../architecture/fusion-core-prd-0823.md`

## Related

- Fix plan: `../architecture/venv-fix-0823.md` §5 (client rollout)
- Audit: `../audit/fusion-audit-all-report.md` Chapter 4 Q1
- License: Apache-2.0
