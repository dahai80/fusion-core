from __future__ import annotations

import httpx
import pytest

from fusion_core import http_client
from fusion_core.http_client import close_all, get_async_client, with_retry


class TestPool:
    async def test_same_base_url_reuses(self):
        await close_all()
        c1 = get_async_client("http://a/v1")
        c2 = get_async_client("http://a/v1")
        assert c1 is c2
        await close_all()

    async def test_different_base_url_separate(self):
        await close_all()
        c1 = get_async_client("http://a/v1")
        c2 = get_async_client("http://b/v1")
        assert c1 is not c2
        await close_all()

    async def test_close_all_clears_pool(self):
        c = get_async_client("http://c/v1")
        await close_all()
        assert "http://c/v1" not in http_client._client_pool
        assert c.is_closed

    async def test_pool_evicts_when_full(self):
        await close_all()
        clients = []
        for i in range(http_client._MAX_POOL_SIZE + 1):
            clients.append(get_async_client(f"http://host{i}/v1"))
        assert len(http_client._client_pool) <= http_client._MAX_POOL_SIZE
        await close_all()

    async def test_evict_is_lru_keeps_recently_used(self):
        await close_all()
        for i in range(http_client._MAX_POOL_SIZE):
            get_async_client(f"http://h{i}/v1")
        # touch h0 -> most recently used, LRU must keep it
        get_async_client("http://h0/v1")
        http_client._evict_lru()
        keys = [k.split(":", 1)[1] for k in http_client._client_pool]
        assert "http://h0/v1" in keys, "LRU must keep recently-used h0, evict coldest instead"
        await close_all()

    async def test_closed_client_not_reused(self):
        await close_all()
        c1 = get_async_client("http://reuse/v1")
        await c1.aclose()
        c2 = get_async_client("http://reuse/v1")
        assert c1 is not c2
        await close_all()

    async def test_per_loop_key_isolation(self):
        await close_all()
        key_a = http_client._pool_key("http://x/v1")
        assert key_a.endswith("http://x/v1")
        assert http_client._loop_id() == id(__import__("asyncio").get_running_loop())
        await close_all()

    def test_close_all_sync_no_loop(self):
        http_client._client_pool["phantom"] = httpx.AsyncClient()
        http_client.close_all_sync()
        assert http_client._client_pool == {}

    def test_loop_id_zero_outside_loop(self):
        assert http_client._loop_id() == 0

    async def test_evict_only_same_loop_keys(self):
        await close_all()
        cur_loop = http_client._loop_id()
        foreign_key = f"{cur_loop + 999}:http://foreign/v1"
        http_client._client_pool[foreign_key] = httpx.AsyncClient(base_url="http://foreign/v1")
        for i in range(http_client._MAX_POOL_SIZE + 1):
            get_async_client(f"http://same{i}/v1")
        assert foreign_key in http_client._client_pool, "evict must never drop cross-loop keys (R2)"
        await close_all()


class TestWithRetry:
    async def test_returns_response_first_try(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return httpx.Response(200, json={"ok": True})

        resp = await with_retry(fn, retries=2, initial_backoff=0)
        assert resp.status_code == 200
        assert calls["n"] == 1

    async def test_retries_on_503(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        resp = await with_retry(fn, retries=3, initial_backoff=0, retry_on=(503,))
        assert resp.status_code == 200
        assert calls["n"] == 3

    async def test_exhausted_raises_http_status_error(self):
        async def fn():
            return httpx.Response(503)

        with pytest.raises(httpx.HTTPStatusError) as ei:
            await with_retry(fn, retries=2, initial_backoff=0, retry_on=(503,))
        assert ei.value.response.status_code == 503

    async def test_exhausted_raises_retry_exhausted_subclass(self):
        from fusion_core.http_client import RetryExhaustedError

        async def fn():
            return httpx.Response(503)

        with pytest.raises(RetryExhaustedError) as ei:
            await with_retry(fn, retries=1, initial_backoff=0, retry_on=(503,))
        assert isinstance(ei.value, httpx.HTTPStatusError), "RetryExhaustedError must subclass HTTPStatusError (R9)"
        assert ei.value.response.status_code == 503

    async def test_instant_401_returned_not_retried_not_retry_exhausted(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return httpx.Response(401)

        resp = await with_retry(fn, retries=2, initial_backoff=0)
        assert resp.status_code == 401, "non-retry status returned as-is; caller raise_for_status (R9)"
        assert calls["n"] == 1, "401 must not be retried"

    async def test_disable_skips_retry(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return httpx.Response(503)

        resp = await with_retry(fn, retries=3, initial_backoff=0, retry_on=(503,), disable=True)
        assert resp.status_code == 503
        assert calls["n"] == 1, "disable=True must call fn exactly once (A4)"

    async def test_total_deadline_aborts_before_retry(self):
        from fusion_core.http_client import RetryTimeoutError

        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return httpx.Response(503)

        with pytest.raises(RetryTimeoutError):
            await with_retry(fn, retries=10, initial_backoff=10, retry_on=(503,), total_deadline=0.0)
        assert calls["n"] <= 1, "total_deadline=0 must abort before first retry (R6)"

    async def test_retries_on_connect_error(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"ok": True})

        resp = await with_retry(fn, retries=2, initial_backoff=0)
        assert resp.status_code == 200
        assert calls["n"] == 2

    async def test_exhausted_connect_raises(self):
        async def fn():
            raise httpx.ConnectError("down")

        with pytest.raises(httpx.ConnectError):
            await with_retry(fn, retries=1, initial_backoff=0)

    async def test_non_retry_status_returned(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return httpx.Response(404)

        resp = await with_retry(fn, retries=3, initial_backoff=0, retry_on=(503,))
        assert resp.status_code == 404
        assert calls["n"] == 1

    async def test_jitter_disabled_uses_full_backoff(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return httpx.Response(200) if calls["n"] > 1 else httpx.Response(503)

        resp = await with_retry(fn, retries=2, initial_backoff=0, jitter=False, retry_on=(503,))
        assert resp.status_code == 200

    async def test_non_retry_exception_propagates(self):
        async def fn():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await with_retry(fn, retries=2, initial_backoff=0)

    def test_jittered_within_range(self):
        for _ in range(20):
            v = http_client._jittered(5.0)
            assert 0.0 <= v <= 5.0


class TestGatewayCircuitBreakerProbe:
    # H3/E4: gateway_circuit_breaker_ok probes gateway /readyz to confirm the
    # circuit breaker exists and is not open before with_retry hands retry off.

    async def test_ready_returns_true(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/readyz"
            return httpx.Response(200, json={"status": "ready", "mode": "full"})

        ok = await _probe_with_transport(httpx.MockTransport(handler), "http://gw")
        assert ok is True

    async def test_not_ready_503_returns_false(self):
        transport = httpx.MockTransport(
            lambda r: httpx.Response(503, json={"status": "not_ready", "local_reasons": ["circuit_breaker_open"]})
        )
        ok = await _probe_with_transport(transport, "http://gw")
        assert ok is False, "open circuit breaker (503) must report unsafe to hand retry off"

    async def test_unreachable_returns_false(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        ok = await _probe_with_transport(httpx.MockTransport(handler), "http://gw")
        assert ok is False, "unreachable gateway must report unsafe (H3: no capability vacuum)"

    async def test_disable_verify_gateway_falls_back_when_unsafe(self):
        # E4: disable=True + verify_gateway=True + breaker open/unreachable →
        # must fall back to core retry (not a capability vacuum), so fn() is
        # retried until exhausted, not called once.
        from fusion_core.http_client import RetryExhaustedError

        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return httpx.Response(503)

        async def fake_probe(gateway_url=None, *, timeout=2.0):
            return False

        import fusion_core.http_client as hc

        orig = hc.gateway_circuit_breaker_ok
        hc.gateway_circuit_breaker_ok = fake_probe
        try:
            with pytest.raises(RetryExhaustedError):
                await with_retry(
                    fn, retries=2, initial_backoff=0, retry_on=(503,), disable=True, verify_gateway=True
                )
        finally:
            hc.gateway_circuit_breaker_ok = orig
        assert calls["n"] == 3, "unsafe gateway must trigger core retry fallback (3 attempts), not 1"

    async def test_disable_verify_gateway_hands_off_when_safe(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return httpx.Response(503)

        import fusion_core.http_client as hc

        async def fake_probe(gateway_url=None, *, timeout=2.0):
            return True

        orig = hc.gateway_circuit_breaker_ok
        hc.gateway_circuit_breaker_ok = fake_probe
        try:
            resp = await with_retry(
                fn, retries=2, initial_backoff=0, retry_on=(503,), disable=True, verify_gateway=True
            )
        finally:
            hc.gateway_circuit_breaker_ok = orig
        assert resp.status_code == 503
        assert calls["n"] == 1, "verified-safe gateway must hand retry off (1 call), not fall back"


async def _probe_with_transport(transport: httpx.MockTransport, gateway_url: str) -> bool:
    # gateway_circuit_breaker_ok builds its own client; redirect by patching
    # httpx.AsyncClient to inject the mock transport for the probe's base_url.
    import fusion_core.http_client as hc

    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        if kwargs.get("base_url", "").rstrip("/") == gateway_url.rstrip("/"):
            kwargs["transport"] = transport
        return orig_init(self, *args, **kwargs)

    httpx.AsyncClient.__init__ = patched_init
    try:
        return await hc.gateway_circuit_breaker_ok(gateway_url, timeout=1.0)
    finally:
        httpx.AsyncClient.__init__ = orig_init
