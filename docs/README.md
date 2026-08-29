# fusion-core module reference

Per-module API reference for fusion-core. Each doc covers signatures, params, returns, raised exceptions, and a runnable example.

> Top-level guide: [`../README.md`](../README.md) (English) | [`../README_CN.md`](../README_CN.md) (中文)

## Modules

| Module | Doc | One-line |
|--------|-----|----------|
| `parse` | [parse.md](parse.md) | LLM output JSON parsing — fail visibly, never silent |
| `config` | [config.md](config.md) | Lazy settings load + api_key resolution + mtime-invalidated cache |
| `logging` | [logging.md](logging.md) | Idempotent logging init, library-mode NullHandler, optional JSON |
| `http_client` | [http_client.md](http_client.md) | httpx async connection pool + retry (single source RETRY_STATUS/RETRY_EXCEPTIONS) + metrics hook |
| `mlx_client` | [mlx_client.md](mlx_client.md) | Unified MLX inference client — chat/embed/stream, StreamError envelope, ServerStats |
| `http` | [http.md](http.md) | FastAPI app factory + pure-ASGI middleware (request_id, auth, 422/500 sanitize) |
| `prompt` | [prompt.md](prompt.md) | Prompt template management — permanent cache, `{{var}}` render |
| `guard_client` | [guard_client.md](guard_client.md) | Pure-Python UDS JSON-RPC client for fusion-guard — zero-trust authorization, typed verdicts/errors |

## Conventions

- **Fail visibly**: `parse_llm_json`, `get_server_stats`, client calls raise on failure — no silent `{}`/`""`/`[]` fallback.
- **No I/O at import**: `import fusion_core` reads no env/file/conn. Resolved at call time.
- **Retry single source**: retry codes `RETRY_STATUS`, retry exceptions `RETRY_EXCEPTIONS` — both in `http_client`.
- **Cluster boundary**: endpoint routing/circuit-breaker/concurrency-gate/model-registry/metrics live in fusion-gateway (Go :11432). Core is single-process single-engine. See [`../README.md` §Boundary declaration](../README.md#boundary-declaration--cluster-capabilities-belong-to-fusion-gateway).
