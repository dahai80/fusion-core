from __future__ import annotations

import asyncio
import logging
import random
from collections import OrderedDict
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

RETRY_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
RETRY_EXCEPTIONS = (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)

_MAX_POOL_SIZE = 8

_client_pool: OrderedDict[str, httpx.AsyncClient] = OrderedDict()

# --- metrics hook (§5.2-F) ---
# Per-base_url counters, optional callback for gateway /metrics aggregation.
# Backward-compatible: no callback = zero IO, only in-process counter bumps.
_metrics: dict[str, dict[str, float | int]] = {}
_metrics_callback: Callable[[dict], None] | None = None


def _metrics_label(resp: httpx.Response | None, exc: Exception | None) -> str:
    if resp is not None and getattr(resp, "_request", None) is not None:
        url = resp.request.url
        host = url.host or "unknown"
        if url.port:
            return f"{host}:{url.port}"
        return host
    if exc is not None:
        return "error_no_response"
    return "unknown"


def _bump_metrics(label: str, *, latency_s: float, status: int, retries: int, error: str | None) -> None:
    bucket = _metrics.setdefault(
        label, {"calls": 0, "errors": 0, "retries": 0, "total_latency_s": 0.0, "last_status": 0}
    )
    bucket["calls"] = int(bucket["calls"]) + 1
    bucket["retries"] = int(bucket["retries"]) + retries
    bucket["total_latency_s"] = float(bucket["total_latency_s"]) + latency_s
    bucket["last_status"] = status
    if error is not None:
        bucket["errors"] = int(bucket["errors"]) + 1
    if _metrics_callback is not None:
        try:
            _metrics_callback(
                {
                    "label": label,
                    "latency_s": latency_s,
                    "status": status,
                    "retries": retries,
                    "error": error,
                }
            )
        except Exception:
            logger.debug("metrics_callback raised, swallowed to avoid breaking with_retry", exc_info=True)


def set_metrics_callback(cb: Callable[[dict], None] | None) -> None:
    global _metrics_callback
    _metrics_callback = cb
    logger.info("metrics callback %s", "set" if cb is not None else "cleared")


def get_metrics_snapshot() -> dict[str, dict[str, float | int]]:
    return {label: dict(counters) for label, counters in _metrics.items()}


def reset_metrics() -> None:
    _metrics.clear()
    logger.info("metrics counters reset")


class RetryExhaustedError(httpx.HTTPStatusError):
    pass


class RetryTimeoutError(TimeoutError):
    pass


def _loop_id() -> int:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


def _pool_key(base_url: str) -> str:
    return f"{_loop_id()}:{base_url.rstrip('/')}"


def _evict_lru() -> None:
    cur_loop = _loop_id()
    while _client_pool:
        key = next((k for k in _client_pool if k.startswith(f"{cur_loop}:")), None)
        if key is None:
            logger.warning("http client pool full, no same-loop client to evict; skipping (cross-loop evict avoided)")
            return
        client = _client_pool.pop(key)
        logger.warning("http client pool full, evicting same-loop LRU base_url=%s", key)
        if client.is_closed:
            continue
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("evict outside loop, client %s not aclosed (fd may leak)", key)
            return
        loop.create_task(client.aclose())
        return


def get_async_client(
    base_url: str,
    *,
    timeout: float = 120.0,
    headers: dict | None = None,
) -> httpx.AsyncClient:
    base = base_url.rstrip("/")
    key = _pool_key(base)
    client = _client_pool.get(key)
    if client is not None and not client.is_closed:
        _client_pool.move_to_end(key)
        return client
    same_loop_count = sum(1 for k in _client_pool if k.startswith(f"{_loop_id()}:"))
    if same_loop_count >= _MAX_POOL_SIZE:
        _evict_lru()
    client = httpx.AsyncClient(
        base_url=base,
        timeout=timeout,
        headers=headers or {},
    )
    _client_pool[key] = client
    logger.info("http client pooled for base_url=%s loop=%s", base, _loop_id())
    return client


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
) -> httpx.Response:
    if disable:
        resp = await fn()
        return resp
    retry_codes = frozenset(retry_on) if retry_on is not None else RETRY_STATUS
    backoff = initial_backoff
    last_exc: Exception | None = None
    loop = asyncio.get_event_loop()
    started = loop.time()
    _retries_used = 0
    _last_resp: httpx.Response | None = None
    try:
        for attempt in range(retries + 1):
            if total_deadline is not None:
                elapsed = loop.time() - started
                if elapsed >= total_deadline:
                    logger.warning(
                        "with_retry total_deadline=%.2fs exceeded before attempt %d", total_deadline, attempt + 1
                    )
                    raise RetryTimeoutError(f"with_retry total_deadline {total_deadline}s exceeded")
            try:
                resp = await fn()
                _last_resp = resp
                if resp.status_code in retry_codes:
                    if attempt < retries:
                        _retries_used += 1
                        delay = _jittered(backoff) if jitter else backoff
                        if total_deadline is not None:
                            elapsed = loop.time() - started
                            if elapsed + delay >= total_deadline:
                                logger.warning(
                                    "with_retry total_deadline=%.2fs would exceed on sleep, aborting", total_deadline
                                )
                                raise RetryTimeoutError(
                                    f"with_retry total_deadline {total_deadline}s exceeded during backoff"
                                )
                        logger.warning(
                            "with_retry HTTP %s, retry %d/%d in %.2fs", resp.status_code, attempt + 1, retries, delay
                        )
                        await asyncio.sleep(delay)
                        backoff = min(backoff * 2, max_backoff)
                        continue
                    logger.error("with_retry exhausted after %d attempts: HTTP %s", retries + 1, resp.status_code)
                    if getattr(resp, "_request", None) is None:
                        resp.request = httpx.Request("POST", "http://with-retry-exhausted")
                    raise RetryExhaustedError(
                        f"with_retry exhausted: HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                latency = loop.time() - started
                _bump_metrics(
                    _metrics_label(resp, None),
                    latency_s=latency,
                    status=resp.status_code,
                    retries=_retries_used,
                    error=None,
                )
                return resp
            except RETRY_EXCEPTIONS as exc:
                last_exc = exc
                if attempt < retries:
                    _retries_used += 1
                    delay = _jittered(backoff) if jitter else backoff
                    if total_deadline is not None:
                        elapsed = loop.time() - started
                        if elapsed + delay >= total_deadline:
                            logger.warning(
                                "with_retry total_deadline=%.2fs would exceed on sleep, aborting", total_deadline
                            )
                            raise RetryTimeoutError(
                                f"with_retry total_deadline {total_deadline}s exceeded during backoff"
                            ) from exc
                    logger.warning(
                        "with_retry attempt %d/%d failed: %s, retrying in %.2fs", attempt + 1, retries, exc, delay
                    )
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, max_backoff)
                else:
                    logger.error("with_retry exhausted after %d attempts: %s", retries + 1, exc)
                    raise last_exc from exc
        raise RuntimeError("with_retry exhausted without response or exception")
    except RetryExhaustedError as exc:
        latency = loop.time() - started
        _bump_metrics(
            _metrics_label(_last_resp, None),
            latency_s=latency,
            status=_last_resp.status_code if _last_resp else 0,
            retries=_retries_used,
            error="retry_exhausted",
        )
        raise exc from exc
    except (*RETRY_EXCEPTIONS, RetryTimeoutError, RuntimeError) as exc:
        latency = loop.time() - started
        _bump_metrics(
            _metrics_label(_last_resp, exc),
            latency_s=latency,
            status=_last_resp.status_code if _last_resp else 0,
            retries=_retries_used,
            error=type(exc).__name__,
        )
        raise exc from exc


def _jittered(backoff: float) -> float:
    return random.uniform(0, backoff)


async def close_all() -> None:
    closed = 0
    items = list(_client_pool.items())
    _client_pool.clear()
    for _, client in items:
        if not client.is_closed:
            await client.aclose()
            closed += 1
    logger.info("close_all: closed %d pooled http clients", closed)


def close_all_sync() -> None:
    items = list(_client_pool.items())
    _client_pool.clear()
    closed = 0
    for key, client in items:
        if client.is_closed:
            continue
        try:
            asyncio.run(client.aclose())
            closed += 1
        except RuntimeError as exc:
            logger.warning("close_all_sync: client %s not aclosed (%s); fd may leak", key, exc)
    logger.info("close_all_sync: closed %d pooled http clients", closed)
