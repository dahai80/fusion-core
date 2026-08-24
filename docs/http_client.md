# http_client

httpx async connection pool + retry. Single source of truth for retry codes/exceptions. Optional metrics hook for gateway aggregation.

## Symbols

- [Constants](#constants)
- [`RetryExhaustedError`](#retryexhaustederror) / [`RetryTimeoutError`](#retrytimeouterror)
- [`get_async_client(base_url, *, timeout, headers)`](#get_async_client)
- [`with_retry(fn, *, retries, initial_backoff, max_backoff, retry_on, jitter, total_deadline, disable)`](#with_retry)
- [`close_all()`](#close_all) / [`close_all_sync()`](#close_all_sync)
- [Metrics](#metrics): `set_metrics_callback`, `get_metrics_snapshot`, `reset_metrics`

## Constants

- `RETRY_STATUS = frozenset({429, 500, 502, 503, 504})` — HTTP status codes retried by default.
- `RETRY_EXCEPTIONS = (ConnectError, ReadTimeout, PoolTimeout, RemoteProtocolError)` — httpx exceptions retried by default.
- `_MAX_POOL_SIZE = 8` — max pooled clients per event loop.

## RetryExhaustedError / RetryTimeoutError

```python
class RetryExhaustedError(httpx.HTTPStatusError): ...   # retries used up on a retriable status
class RetryTimeoutError(TimeoutError): ...              # total_deadline exceeded
```

`RetryExhaustedError` subclasses `HTTPStatusError` (R9) so existing `except HTTPStatusError` callers keep working, but callers can now distinguish "retried-out 503" (`RetryExhaustedError`) from "first-hit 401" (plain `HTTPStatusError`).

## get_async_client

```python
def get_async_client(
    base_url: str,
    *,
    timeout: float = 120.0,
    headers: dict | None = None,
) -> httpx.AsyncClient
```

Returns a pooled `AsyncClient` for `base_url` on the **current event loop**. Pool key = `f"{loop_id}:{base_url}"`. LRU via `OrderedDict`, cap 8 per loop. On full pool, evicts the **same-loop** LRU key only (R2: never schedules `aclose` on another loop's client → no `RuntimeError: attached to a different loop`, no fd leak). Calling from a loop with no same-loop candidates logs a warning and skips eviction (cross-loop evict avoided).

```python
from fusion_core import get_async_client
client = get_async_client("http://localhost:11434/v1")
```

## with_retry

```python
async def with_retry(
    fn: Callable[[], Awaitable[httpx.Response]],
    *,
    retries: int = 3,
    initial_backoff: float = 1.0,
    max_backoff: float = 60.0,
    retry_on: tuple[int, ...] | None = None,
    jitter: bool = True,
    total_deadline: float | None = None,
    disable: bool = False,
) -> httpx.Response
```

Calls `fn()` with exponential backoff + full jitter.

- `retries` — max retries (total attempts = `retries + 1`).
- `retry_on` — override retriable statuses (default `RETRY_STATUS`).
- `jitter=True` — `random.uniform(0, backoff)`; `False` → exact backoff.
- `total_deadline` (R6) — end-to-end budget in seconds. Checked before each attempt and before each sleep; exceeded → `RetryTimeoutError`. Caps the whole retry budget, not just one request.
- `disable=True` (A4) — calls `fn()` once, no retry. Use when fusion-gateway's circuit breaker owns retry (avoid double-retry). No metrics bumped on the disable path.

Outcomes:
- Success (non-retriable status) → returns `Response`, bumps metrics.
- Retriable status, retries used up → `RetryExhaustedError` (HTTPStatusError subclass), bumps `error="retry_exhausted"`.
- `RETRY_EXCEPTIONS`, retries used up → re-raises last exception, bumps `error=type(exc).__name__`.
- `total_deadline` exceeded → `RetryTimeoutError`.

```python
from fusion_core import with_retry
resp = await with_retry(lambda: client.post("/chat/completions", json=payload),
                        retries=2, total_deadline=30.0)
```

## close_all / close_all_sync

```python
async def close_all() -> None       # call in an async context
def close_all_sync() -> None        # call at shutdown (uses asyncio.run per client)
```

Closes all pooled clients. `close_all_sync` best-effort: clients whose loop is gone log a warning and are skipped (fd may leak — unavoidable without a running loop).

## Metrics

```python
def set_metrics_callback(cb: Callable[[dict], None] | None) -> None
def get_metrics_snapshot() -> dict[str, dict[str, float | int]]
def reset_metrics() -> None
```

Per-`base_url` host:port counters: `calls`, `errors`, `retries`, `total_latency_s`, `last_status`. Bumped inside `with_retry` (the single bottleneck). Callback fires with `{label, latency_s, status, retries, error}` after each `with_retry`; callback exceptions are swallowed (logged debug) so a bad callback never breaks retry. Default no callback = zero IO. Kept for single-process observability; cluster metrics live in fusion-gateway (R10 boundary).

```python
from fusion_core import set_metrics_callback, get_metrics_snapshot
set_metrics_callback(lambda m: print(m))   # {"label": "localhost:11434", "status": 200, ...}
snap = get_metrics_snapshot()               # {"localhost:11434": {"calls": 42, ...}}
```

## Design notes

- Retry single source (R9/R10): `RETRY_STATUS`/`RETRY_EXCEPTIONS` defined here only; `mlx_client` imports them.
- Cross-loop safety (R2): eviction scoped to same-loop keys; cross-loop `aclose` avoided.
- End-to-end deadline (R6): `total_deadline` includes inter-retry sleep, not just per-request timeout.
- Disable path (A4): hands retry to gateway circuit breaker; no metrics, no retry.
