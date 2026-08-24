from __future__ import annotations

import json

import httpx
import pytest

from fusion_core.mlx_client import (
    EmbeddingResponse,
    FusionMLXClient,
    LLMResponse,
    ServerStats,
    StreamError,
    create_async_client,
)


def _make_client(handler, base_url="http://test-mlx/v1", api_key="k", max_retries=0):
    transport = httpx.MockTransport(handler)
    return FusionMLXClient(base_url=base_url, api_key=api_key, transport=transport, max_retries=max_retries)


def _chat_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/chat/completions"
    payload = json.loads(request.content)
    assert payload["model"] == "test-model"
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": "hello reply", "tool_calls": []},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        },
    )


class TestChat:
    async def test_chat_returns_llmresponse(self):
        client = _make_client(_chat_handler)
        resp = await client.chat(model="test-model", messages=[{"role": "user", "content": "hi"}])
        assert isinstance(resp, LLMResponse)
        assert resp.content == "hello reply"
        assert resp.finish_reason == "stop"
        assert resp.usage["completion_tokens"] == 2
        await client.close()

    async def test_chat_text_returns_str(self):
        client = _make_client(_chat_handler)
        text = await client.chat_text(model="test-model", messages=[{"role": "user", "content": "hi"}])
        assert text == "hello reply"
        await client.close()

    async def test_chat_403_raises_immediately(self):
        def handler(request):
            return httpx.Response(403, json={"error": "forbidden"})

        client = _make_client(handler, max_retries=0)
        with pytest.raises(httpx.HTTPStatusError):
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}])
        await client.close()

    async def test_chat_retries_on_503(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(503, json={"error": "busy"})
            return _chat_handler(request)

        client = FusionMLXClient(
            base_url="http://test/v1",
            api_key="k",
            transport=httpx.MockTransport(handler),
            max_retries=2,
            retry_delay=0,
        )
        resp = await client.chat(model="test-model", messages=[{"role": "user", "content": "hi"}])
        assert resp.content == "hello reply"
        assert calls["n"] == 2
        await client.close()


class TestStream:
    async def test_stream_chat_yields_chunks(self):
        sse = (
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            "data: [DONE]\n\n"
        )

        def handler(request):
            return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

        client = _make_client(handler)
        chunks = []
        async for c in client.stream_chat(model="m", messages=[{"role": "user", "content": "x"}]):
            chunks.append(c)
        assert "".join(chunks) == "Hello"
        await client.close()

    async def test_stream_error_carries_delivered_envelope(self):
        sse = 'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        state = {"sent_ok": False}

        def handler(request):
            if not state["sent_ok"]:
                state["sent_ok"] = True
                return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
            raise httpx.RemoteProtocolError("connection severed mid-stream")

        client = FusionMLXClient(
            base_url="http://test/v1",
            api_key="k",
            transport=httpx.MockTransport(handler),
            max_retries=0,
        )
        collected = []
        with pytest.raises(StreamError) as ei:
            async for c in client.stream_chat(model="m", messages=[{"role": "user", "content": "x"}]):
                collected.append(c)
        assert ei.value.delivered == 5, "envelope must report bytes delivered before failure (R4)"
        assert ei.value.resume_offset == 5
        assert "".join(collected) == "Hello"
        await client.close()


class TestUsageDefault:
    def test_llm_response_usage_has_total_tokens(self):
        resp = LLMResponse(content="hi")
        assert resp.usage["total_tokens"] == 0, "usage default must include total_tokens=0 (I3)"
        assert resp.usage["prompt_tokens"] == 0
        assert resp.usage["completion_tokens"] == 0


class TestHealthThrottle:
    async def test_health_reuses_probe_client_and_throttles(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"data": []})

        client = _make_client(handler)
        ok1 = await client.health()
        ok2 = await client.health()
        assert ok1 is True and ok2 is True
        assert calls["n"] == 1, "second health() within throttle window must hit cache, not upstream (R3)"
        assert client._probe_client is not None, "health must reuse a long-lived probe client (R3)"
        assert client._client is None, "probe must not materialize the main client"
        await client.close()


class TestEmbed:
    async def test_embed_returns_vector(self):
        def handler(request):
            assert request.url.path == "/v1/embeddings"
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}], "model": "BGE-M3", "usage": {}})

        client = _make_client(handler)
        resp = await client.embed("hello", model="BGE-M3")
        assert isinstance(resp, EmbeddingResponse)
        assert resp.vector == [0.1, 0.2]
        await client.close()

    async def test_embed_batch_returns_vectors(self):
        seen_input = {}

        def handler(request):
            payload = json.loads(request.content)
            seen_input["input"] = payload["input"]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"embedding": [0.1, 0.2]},
                        {"embedding": [0.3, 0.4]},
                    ],
                    "model": "BGE-M3",
                    "usage": {},
                },
            )

        client = _make_client(handler)
        resp = await client.embed(["hello", "world"], model="BGE-M3")
        assert seen_input["input"] == ["hello", "world"]
        assert resp.vector == [0.1, 0.2]
        assert resp.vectors == [[0.1, 0.2], [0.3, 0.4]]
        await client.close()


class TestHealth:
    async def test_health_true_on_200(self):
        client = _make_client(lambda r: httpx.Response(200, json={"data": []}))
        assert await client.health() is True
        await client.close()

    async def test_health_false_on_connect_error(self):
        def handler(request):
            raise httpx.ConnectError("nope")

        client = _make_client(handler)
        assert await client.health() is False
        await client.close()


class TestCreateAsyncClient:
    async def test_factory_returns_client_with_resolved_key(self, monkeypatch):
        monkeypatch.setenv("FUSION_MLX_API_KEY", "envkey")
        client = create_async_client(backend="mlx", transport=httpx.MockTransport(_chat_handler))
        assert isinstance(client, FusionMLXClient)
        assert client.api_key == "envkey"
        await client.close()

    async def test_factory_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("FUSION_MLX_API_KEY", "envkey")
        client = create_async_client(backend="mlx", api_key="explicit", transport=httpx.MockTransport(_chat_handler))
        assert client.api_key == "explicit"
        await client.close()

    def test_factory_rejects_unknown_backend(self):
        with pytest.raises(ValueError):
            create_async_client(backend="ollama")


class TestContextManager:
    async def test_async_context_closes_client(self):
        async with _make_client(_chat_handler) as client:
            assert client._client is not None
        assert client._client is None


class TestRetryPaths:
    async def test_chat_connect_error_exhausted_raises(self):
        def handler(request):
            raise httpx.ConnectError("down")

        client = FusionMLXClient(
            base_url="http://test/v1",
            api_key="k",
            transport=httpx.MockTransport(handler),
            max_retries=1,
            retry_delay=0,
        )
        with pytest.raises(httpx.ConnectError):
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}])
        await client.close()

    async def test_chat_429_retries_then_succeeds(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(429)
            return _chat_handler(request)

        client = FusionMLXClient(
            base_url="http://test/v1",
            api_key="k",
            transport=httpx.MockTransport(handler),
            max_retries=2,
            retry_delay=0,
        )
        resp = await client.chat(model="test-model", messages=[{"role": "user", "content": "hi"}])
        assert resp.content == "hello reply"
        await client.close()

    async def test_stream_chat_retries_on_503(self):
        calls = {"n": 0}
        sse = 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(503)
            return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

        client = FusionMLXClient(
            base_url="http://test/v1",
            api_key="k",
            transport=httpx.MockTransport(handler),
            max_retries=2,
            retry_delay=0,
        )
        chunks = []
        async for c in client.stream_chat(model="m", messages=[{"role": "user", "content": "x"}]):
            chunks.append(c)
        assert chunks == ["ok"]
        await client.close()

    async def test_stream_chat_connect_error_exhausted(self):
        def handler(request):
            raise httpx.ConnectError("down")

        client = FusionMLXClient(
            base_url="http://test/v1",
            api_key="k",
            transport=httpx.MockTransport(handler),
            max_retries=1,
            retry_delay=0,
        )
        with pytest.raises(httpx.ConnectError):
            async for _ in client.stream_chat(model="m", messages=[{"role": "user", "content": "x"}]):
                pass
        await client.close()


class TestModelListing:
    async def test_list_models(self):
        def handler(request):
            return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})

        client = _make_client(handler)
        models = await client.list_models()
        assert [m["id"] for m in models] == ["m1", "m2"]
        await client.close()

    async def test_get_server_stats_ok(self):
        def handler(request):
            return httpx.Response(200, json={"loaded": ["m1"]})

        client = _make_client(handler)
        stats = await client.get_server_stats()
        assert isinstance(stats, ServerStats), "get_server_stats must return ServerStats (I12)"
        assert stats.raw == {"loaded": ["m1"]}, "raw passthrough preserves upstream keys (I12)"
        assert stats.total_requests == 0
        await client.close()

    async def test_get_server_stats_typed_fields_from_metrics(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "total_requests": 42,
                    "successful_requests": 40,
                    "failed_requests": 2,
                    "total_tokens_generated": 100,
                    "total_prompt_tokens": 50,
                    "active_requests": 3,
                    "uptime_seconds": 120.0,
                },
            )

        client = _make_client(handler)
        stats = await client.get_server_stats()
        assert stats.total_requests == 42
        assert stats.successful_requests == 40
        assert stats.failed_requests == 2
        assert stats.total_tokens_generated == 100
        assert stats.active_requests == 3
        assert stats.uptime_seconds == 120.0
        await client.close()

    async def test_get_server_stats_error_raises(self):
        def handler(request):
            raise httpx.ConnectError("nope")

        client = _make_client(handler)
        with pytest.raises(httpx.ConnectError):
            await client.get_server_stats()
        await client.close()


class TestMisc:
    async def test_close_when_no_client(self):
        client = FusionMLXClient(base_url="http://test/v1", api_key="k")
        await client.close()

    async def test_no_api_key_logs_warning(self, caplog, monkeypatch):
        import logging

        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        import fusion_core.config as _cfg

        monkeypatch.setattr(_cfg, "resolve_api_key", lambda *a, **k: "")
        with caplog.at_level(logging.WARNING, logger="fusion_core.mlx_client"):
            FusionMLXClient(base_url="http://test/v1", api_key="")
        assert any("FUSION_MLX_API_KEY" in r.message for r in caplog.records)


class TestChatKwargsGuard:
    async def test_stream_true_rejected(self):
        client = _make_client(_chat_handler)
        with pytest.raises(ValueError):
            await client.chat(model="test-model", messages=[{"role": "user", "content": "x"}], stream=True)
        await client.close()

    async def test_non_allowlisted_kwarg_dropped_and_warned(self, caplog):
        import logging

        sent = {}

        def handler(request):
            sent["payload"] = json.loads(request.content)
            return _chat_handler(request)

        client = _make_client(handler, max_retries=0)
        with caplog.at_level(logging.WARNING, logger="fusion_core.mlx_client"):
            await client.chat(
                model="test-model",
                messages=[{"role": "user", "content": "x"}],
                modle="TYPO",
            )
        assert "modle" not in sent["payload"]
        assert any("modle" in r.message for r in caplog.records)
        await client.close()

    async def test_allowlisted_kwarg_passed_through(self):
        sent = {}

        def handler(request):
            sent["payload"] = json.loads(request.content)
            return _chat_handler(request)

        client = _make_client(handler, max_retries=0)
        await client.chat(
            model="test-model",
            messages=[{"role": "user", "content": "x"}],
            top_p=0.5,
            seed=42,
        )
        assert sent["payload"]["top_p"] == 0.5
        assert sent["payload"]["seed"] == 42
        await client.close()


class TestDefaultModel:
    async def test_factory_default_model_used_when_chat_omits_model(self):
        seen = {}

        def handler(request):
            payload = json.loads(request.content)
            seen["model"] = payload["model"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "hello reply", "tool_calls": []},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                },
            )

        client = create_async_client(
            backend="mlx",
            base_url="http://test/v1",
            api_key="k",
            model="factory-default-model",
            transport=httpx.MockTransport(handler),
        )
        resp = await client.chat(messages=[{"role": "user", "content": "hi"}])
        assert resp.content == "hello reply"
        assert seen["model"] == "factory-default-model"
        assert client.default_model == "factory-default-model"
        await client.close()

    async def test_chat_without_model_and_no_default_raises(self):
        client = _make_client(_chat_handler)
        with pytest.raises(ValueError):
            await client.chat(messages=[{"role": "user", "content": "hi"}])
        await client.close()


class TestHealthIsolation:
    async def test_health_does_not_materialize_main_client(self):
        client = _make_client(lambda r: httpx.Response(200, json={"data": []}))
        assert await client.health() is True
        assert client._client is None, "health() must not leak into the persistent client"
        await client.close()
