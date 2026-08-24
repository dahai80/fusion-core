# http_client

httpx async connection pool + retry. Single source of truth for retry codes/exceptions. Optional metrics hook for gateway aggregation.

## Symbols

- [Constants](#constants)
- [`RetryExhaustedError`](#retryexhaustederror) / [`RetryTimeoutError`](#retrytimeouterror)
- [`get_async_client(base_url, *, timeout, headers)`](#get_async_client)
- [`gateway_circuit_breaker_ok(gateway_url, *, timeout)`](#gateway_circuit_breaker_ok)
- [`with_retry(fn, *, retries, initial_backoff, max_backoff, retry_on, jitter, total_deadline, disable, verify_gateway, gateway_url)`](#with_retry)
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

Returns a pooled `AsyncClient` for `base_url` on the **current event loop**. Pool key = `f"{loop_id}:{base_url}"`. LRU via `OrderedDict`, cap 8 per loop. On full pool, evicts the **same-loop** LRU key only (R2: never schedules `aclose` on another loop's client → no `RuntimeError: attached to a different loop`). The evicted client's `aclose()` is scheduled via `loop.create_task()` and the resulting task held in a module-level `_pending_closes` set with a done-callback that removes it — preventing the GC from destroying the task mid-flight and leaking fds (the original fire-and-forget `create_task` lost its only reference). `_pending_closes` is drained by `close_all`/`close_all_sync`. Calling from a loop with no same-loop candidates logs a warning and skips eviction (cross-loop evict avoided).

```python
from fusion_core import get_async_client
client = get_async_client("http://localhost:11434/v1")
```

## gateway_circuit_breaker_ok

```python
async def gateway_circuit_breaker_ok(
    gateway_url: str | None = None,
    *,
    timeout: float = 2.0,
) -> bool
```

H3/E4: probes fusion-gateway's `/readyz` to confirm the gateway is reachable **and** its circuit breaker is not open, *before* `with_retry(disable=True)` hands retry off. The gateway `/readyz` returns `{"status":"ready","mode":"full|degraded"}` (200) when the local `CircuitBreakerState != StateOpen`, or `{"status":"not_ready","local_reasons":["circuit_breaker_open",...]}` (503) when the breaker is open.

- `gateway_url` — defaults to `default_gateway_base_url()` (`$FUSION_GATEWAY_URL` or `http://localhost:11432`).
- Returns `True` only on HTTP 200 + `status == "ready"`.
- Returns `False` on 503/not-ready (breaker open), unreachable (`ConnectError`/`ReadTimeout`/`PoolTimeout`/`RemoteProtocolError`), or unparseable body (`ValueError`/`KeyError`) — every False path logs a warning naming the reason, so the probe is **observable**, not silent.

```python
from fusion_core import gateway_circuit_breaker_ok
if await gateway_circuit_breaker_ok():
    ...  # safe to hand retry to gateway
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
    verify_gateway: bool = False,
    gateway_url: str | None = None,
) -> httpx.Response
```

Calls `fn()` with exponential backoff + full jitter.

- `retries` — max retries (total attempts = `retries + 1`).
- `retry_on` — override retriable statuses (default `RETRY_STATUS`).
- `jitter=True` — `random.uniform(0, backoff)`; `False` → exact backoff.
- `total_deadline` (R6) — end-to-end budget in seconds. Checked before each attempt and before each sleep; exceeded → `RetryTimeoutError`. Caps the whole retry budget, not just one request.
- `disable=True` (A4) — calls `fn()` once, no retry. Use when fusion-gateway's circuit breaker owns retry (avoid double-retry). The disable path is **observable** (E4):
  - `verify_gateway=False` (default) — logs an info line that the caller assumes upstream handles resilience (zero core resilience), then calls `fn()` once.
  - `verify_gateway=True` — calls `gateway_circuit_breaker_ok(gateway_url)` first. If the breaker is verified ready, logs "handing retry off" and calls `fn()` once. If the breaker is open/unreachable, logs a warning and **falls back to core retry** (`disable` flipped to `False`) so there is no capability vacuum (H3/E4). `gateway_url` overrides the default gateway URL for the probe.

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

Closes all pooled clients. `close_all` runs in an async context (awaits each `aclose`, then drains `_pending_closes`). `close_all_sync` (R3) closes **cross-loop** clients correctly: each client was created on a recorded owning loop (`_client_loops`); if that loop is still running, `aclose` is scheduled on it via `asyncio.run_coroutine_threadsafe(...).result(timeout=10)` — no `RuntimeError: attached to a different loop`; if the loop is gone, a fresh `asyncio.run` is used. Clients that still can't close are counted as `leaked` (warning log, fd may leak). Pending eviction tasks are best-effort drained.

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
- Cross-loop safety (R2/R3): eviction scoped to same-loop keys; evicted `aclose` task held in `_pending_closes` (no GC/fd leak); `close_all_sync` closes cross-loop clients on their owning loop.
- End-to-end deadline (R6): `total_deadline` includes inter-retry sleep, not just per-request timeout.
- Disable path (A4): hands retry to gateway circuit breaker; no metrics, no retry.
- Capability vacuum (H3/E4): `disable=True, verify_gateway=True` probes gateway `/readyz` first; open/unreachable breaker falls back to core retry so handing retry off never leaves zero resilience.
