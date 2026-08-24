# mlx_client

Unified MLX inference client — chat, embedding, streaming. Retry delegated to `http_client`. Single-process single-engine; multi-node via `FUSION_MLX_URL` → fusion-gateway.

## Symbols

- [`StreamError`](#streamerror)
- [`LLMResponse`](#llmresponse) / [`EmbeddingResponse`](#embeddingresponse) / [`ServerStats`](#serverstats)
- [`FusionMLXClient`](#fusionmlxclient)
- [`create_async_client(*, backend, base_url, api_key, model, ...)`](#create_async_client)

## StreamError

```python
class StreamError(RuntimeError):
    delivered: int  # chars already yielded to caller
    resume_offset: int  # where to resume (== delivered for char streams)
```

Raised by `stream_chat` for **all** stream-failure outcomes — the single stream-failure type a caller must handle (H4/R4). Cases:

- Failure **after** partial output (`delivered > 0`) → `StreamError(delivered=yielded, resume_offset=yielded)`, not retried (can't safely resume an OpenAI-style char stream). The envelope lets a caller decide: discard-and-regenerate, or resume from `resume_offset`.
- Retriable failure, retries exhausted, **no** output (`delivered == 0`) → `StreamError("stream_chat retries exhausted on ...", delivered=0)` — wrapped so callers have ONE except type for every severed/exhausted stream.
- Non-retriable 4xx (400/401/403) → the original `httpx.HTTPStatusError` is **re-raised** (a request error, not a stream failure — lets callers distinguish bad-request from severed-stream).

## LLMResponse / EmbeddingResponse / ServerStats

```python
@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = []
    finish_reason: str = "stop"
    usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}  # I3: total_tokens present

@dataclass
class EmbeddingResponse:
    vector: list[float]
    vectors: list[list[float]] = []   # populated only for batch input
    model: str = ""
    usage: dict = {"prompt_tokens": 0}

@dataclass
class ServerStats:                    # I12: typed stats, raw passthrough
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_tokens_generated: int = 0
    total_prompt_tokens: int = 0
    active_requests: int = 0
    uptime_seconds: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerStats
```

`LLMResponse.usage` default includes `total_tokens=0` (I3) — no `KeyError` on the fallback path. `ServerStats` holds a stable subset + `raw` for any extra upstream fields (avoids losing data when MLX `/stats` schema varies).

## FusionMLXClient

```python
class FusionMLXClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
        model: str | None = None,
    )
```

- `base_url` — default `default_mlx_base_url()` (reads `FUSION_MLX_URL` at call time).
- `api_key` — resolved via `resolve_api_key` if None. Missing → warning log (calls unauthenticated, upstream may 401).
- `model` — recorded as `default_model`; `chat(model=None)` uses it.
- `transport` — inject for tests (e.g. a custom `AsyncBaseTransport`).
- Async context manager (`async with`); also `await client.close()` / `await client.aclose()`.

### chat

```python
async def chat(
    self,
    messages: list[dict],
    model: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    stream: bool = False,
    total_deadline: float | None = None,
    **kwargs,
) -> LLMResponse
```

Non-streaming chat. `stream=True` raises `ValueError` (use `stream_chat`). `**kwargs` allowlist-passed into the payload: `top_p`, `seed`, `response_format`, `user`, `n`, `presence_penalty`, `frequency_penalty`, `stop`, `logit_bias`, `logprobs`, `top_logprobs`. Non-allowlisted kwargs logged-and-dropped.

`total_deadline` (R5) is an **explicit named param** — the end-to-end budget passed to `with_retry` in seconds. Accepted as a kwarg too (legacy compat) but the explicit form is preferred; neither path logs a spurious "dropping" warning.

Raises: `ValueError` (no model, `stream=True`, missing `choices`), `httpx.HTTPStatusError` (non-retriable 4xx), `RetryExhaustedError`/`RetryTimeoutError` (via `with_retry`).

### chat_text

```python
async def chat_text(self, messages, model=None, temperature=0.7, max_tokens=4096, total_deadline=None, **kwargs) -> str
```

Shortcut: `chat(...).content`. `total_deadline` forwarded to `chat` (R5).

### stream_chat

```python
async def stream_chat(self, messages, model=None, temperature=0.7, max_tokens=4096) -> AsyncIterator[str]
```

SSE streaming. Yields `delta.content` chunks. Pre-output retriable failures (`RETRY_STATUS` / `RETRY_EXCEPTIONS`, `attempt < max_retries`, `not yielded`) retry. All non-retried failure paths surface as `StreamError` (H4/R4 — see [StreamError](#streamerror)): partial-output failure → `StreamError(delivered=yielded)`; retriable-but-exhausted-no-output → `StreamError(delivered=0)`. Only non-retriable 4xx re-raise the original `HTTPStatusError`.

```python
try:
    async for chunk in client.stream_chat(messages=[...]):
        sys.stdout.write(chunk)
except StreamError as e:
    log.warning("severed after %d chars", e.delivered)
```

### embed

```python
async def embed(self, text: str | list[str], *, model: str) -> EmbeddingResponse
```

`text` str → `EmbeddingResponse.vector`. `text` list → `EmbeddingResponse.vectors` (and `.vector` = first). Raises `ValueError` if response missing `data`.

### list_models

```python
async def list_models(self) -> list[dict[str, Any]]
```

`GET /models` → `data` array. Note: model→endpoint routing lives in fusion-gateway (A3 boundary); core just lists what its single `base_url` serves.

### health

```python
async def health(self) -> bool
```

`GET /models` on a separate `probe_client` (2s timeout, R3). Throttled: within `_HEALTH_THROTTLE_SECONDS = 1.0s` returns cached result — high-frequency polling doesn't spawn a handshake storm or materialize the main connection. Connection-class exceptions → `False` (warning log).

### get_server_stats

```python
async def get_server_stats(self) -> ServerStats
```

`GET /stats` (5s timeout) → `ServerStats.from_dict(resp.json())` (I12). Raises on HTTP error (no silent `{}`).

## create_async_client

```python
def create_async_client(
    *,
    backend: str = "mlx",
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 120.0,
    max_retries: int = 2,
    retry_delay: float = 1.0,
) -> FusionMLXClient
```

Factory. `backend` is **keyword-only** (I11 — prevents positional `create_async_client("mlx")`). Only `"mlx"` supported; other values raise `ValueError`.

```python
from fusion_core import create_async_client

client = create_async_client(base_url="http://localhost:11434/v1", api_key="k", model="qwen2.5-7b")
```

## Design notes

- No retry logic in `mlx_client` (single responsibility): `chat` routes to `http_client.with_retry`; `RETRY_STATUS`/`RETRY_EXCEPTIONS` imported from `http_client` (I6: top-level import, no function-internal lazy import).
- `health()` reuses a long-lived `probe_client` + 1s throttle (R3), not a fresh client per call.
- `StreamError` envelope (R4/H4): one stream-failure type for every severed/exhausted stream; caller knows how much was delivered. Non-retriable 4xx still surface as the original `HTTPStatusError`.
- `usage.total_tokens` always present (I3); `get_server_stats` typed (I12) with `raw` passthrough.
- Cluster boundary: endpoint routing / model registry / circuit breaker / concurrency gate / metrics → fusion-gateway. Core is single `base_url`.
