from __future__ import annotations

import httpx
import pytest

from fusion_core.http_client import (
    RetryExhaustedError,
    get_metrics_snapshot,
    reset_metrics,
    set_metrics_callback,
    with_retry,
)

_REQ = httpx.Request("POST", "http://127.0.0.1:11432/v1/chat/completions")


async def _resp(status: int = 200) -> httpx.Response:
    r = httpx.Response(status, json={"ok": True})
    r.request = _REQ
    return r


async def _ok() -> httpx.Response:
    return await _resp(200)


async def _err(status: int = 503) -> httpx.Response:
    return await _resp(status)


class TestMetricsCounters:
    async def test_success_bumps_calls_no_error(self):
        reset_metrics()
        resp = await with_retry(lambda: _ok(), retries=2)
        assert resp.status_code == 200
        snap = get_metrics_snapshot()
        assert "127.0.0.1:11432" in snap
        bucket = snap["127.0.0.1:11432"]
        assert bucket["calls"] == 1
        assert bucket["errors"] == 0
        assert bucket["retries"] == 0
        assert bucket["last_status"] == 200
        assert bucket["total_latency_s"] >= 0.0
        reset_metrics()

    async def test_retry_exhausted_bumps_errors(self):
        reset_metrics()
        with pytest.raises(RetryExhaustedError):
            await with_retry(lambda: _err(503), retries=1)
        snap = get_metrics_snapshot()
        bucket = snap["127.0.0.1:11432"]
        assert bucket["calls"] == 1
        assert bucket["errors"] == 1
        assert bucket["retries"] == 1
        assert bucket["last_status"] == 503
        reset_metrics()

    async def test_retries_counted_then_success(self):
        reset_metrics()
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            return await _err(503) if calls["n"] == 1 else await _ok()

        resp = await with_retry(flaky, retries=2)
        assert resp.status_code == 200
        snap = get_metrics_snapshot()
        bucket = snap["127.0.0.1:11432"]
        assert bucket["calls"] == 1
        assert bucket["retries"] == 1
        assert bucket["errors"] == 0
        assert bucket["last_status"] == 200
        reset_metrics()

    async def test_connection_error_bumps_errors(self):
        reset_metrics()

        async def boom():
            raise httpx.ConnectError("boom")

        with pytest.raises(httpx.ConnectError):
            await with_retry(boom, retries=1)
        snap = get_metrics_snapshot()
        bucket = snap["error_no_response"]
        assert bucket["calls"] == 1
        assert bucket["errors"] == 1
        assert bucket["retries"] == 1
        reset_metrics()

    async def test_reset_clears_counters(self):
        reset_metrics()
        await with_retry(lambda: _ok(), retries=1)
        assert get_metrics_snapshot()
        reset_metrics()
        assert get_metrics_snapshot() == {}


class TestMetricsCallback:
    async def test_callback_fired_on_success(self):
        reset_metrics()
        events: list[dict] = []
        set_metrics_callback(lambda e: events.append(e))
        try:
            await with_retry(lambda: _ok(), retries=1)
            assert len(events) == 1
            ev = events[0]
            assert ev["label"] == "127.0.0.1:11432"
            assert ev["status"] == 200
            assert ev["error"] is None
            assert ev["retries"] == 0
            assert "latency_s" in ev
        finally:
            set_metrics_callback(None)
            reset_metrics()

    async def test_callback_fired_on_error(self):
        reset_metrics()
        events: list[dict] = []
        set_metrics_callback(lambda e: events.append(e))
        try:
            with pytest.raises(RetryExhaustedError):
                await with_retry(lambda: _err(503), retries=1)
            assert len(events) == 1
            ev = events[0]
            assert ev["status"] == 503
            assert ev["error"] == "retry_exhausted"
        finally:
            set_metrics_callback(None)
            reset_metrics()

    async def test_callback_exception_swallowed(self):
        reset_metrics()

        def bad_cb(e):
            raise RuntimeError("callback broken")

        set_metrics_callback(bad_cb)
        try:
            resp = await with_retry(lambda: _ok(), retries=1)
            assert resp.status_code == 200
            snap = get_metrics_snapshot()
            assert snap["127.0.0.1:11432"]["calls"] == 1
        finally:
            set_metrics_callback(None)
            reset_metrics()


class TestMetricsDisable:
    async def test_disable_path_no_metrics(self):
        reset_metrics()
        resp = await with_retry(lambda: _ok(), retries=1, disable=True)
        assert resp.status_code == 200
        assert get_metrics_snapshot() == {}
        reset_metrics()
