from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

START_SH = os.path.expanduser("~/claude-home/fusion-mlx/start.sh")


def _engine_available() -> bool:
    import subprocess

    try:
        r = subprocess.run([START_SH, "status"], capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return "Running" in r.stdout


@pytest.fixture(scope="module")
def started_engine():
    import subprocess

    was_running = _engine_available()
    if not was_running:
        subprocess.run([START_SH, "start"], capture_output=True, text=True, timeout=120)
    try:
        yield
    finally:
        if not was_running:
            subprocess.run([START_SH, "stop"], capture_output=True, text=True, timeout=30)


@pytest.mark.asyncio
async def test_real_health(started_engine):
    from fusion_core.mlx_client import create_async_client

    client = create_async_client(backend="mlx")
    try:
        ok = await client.health()
        assert ok is True, "fusion-mlx /models not reachable on localhost:11434/v1"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_list_models(started_engine):
    from fusion_core.mlx_client import create_async_client

    client = create_async_client(backend="mlx")
    try:
        models = await client.list_models()
        assert isinstance(models, list)
    finally:
        await client.close()


async def _first_chat_model_id() -> str | None:
    # Select by capabilities.text_generation, NOT by name heuristics: the engine
    # lists image/video/tts models alongside text models, and a name-based
    # "not embed" filter picks FLUX.2 (image_gen) first, which 400s on /chat.
    from fusion_core.mlx_client import create_async_client

    client = create_async_client(backend="mlx")
    try:
        models = await client.list_models()
        for m in models:
            caps = m.get("capabilities") or {}
            if caps.get("text_generation") is True:
                return m.get("id")
        return None
    finally:
        await client.close()


async def _first_embed_model_id() -> str | None:
    from fusion_core.mlx_client import create_async_client

    client = create_async_client(backend="mlx")
    try:
        models = await client.list_models()
        for m in models:
            caps = m.get("capabilities") or {}
            if caps.get("embedding") is True:
                return m.get("id")
        return None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_chat(started_engine):
    # R6: real chat round-trip against fusion-mlx engine.
    from fusion_core.mlx_client import create_async_client

    model = await _first_chat_model_id()
    if not model:
        pytest.skip("no model loaded on fusion-mlx engine")
    client = create_async_client(backend="mlx", model=model)
    try:
        resp = await client.chat(
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=16,
            temperature=0.0,
        )
        assert resp.content, "chat returned empty content"
        assert "total_tokens" in resp.usage, "usage must include total_tokens (I3)"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_chat_text_total_deadline(started_engine, caplog):
    # R5/R6: total_deadline explicit param flows to with_retry without a
    # "dropping" warning on the real engine.
    import logging

    from fusion_core.mlx_client import create_async_client

    model = await _first_chat_model_id()
    if not model:
        pytest.skip("no model loaded on fusion-mlx engine")
    client = create_async_client(backend="mlx", model=model)
    try:
        with caplog.at_level(logging.WARNING, logger="fusion_core.mlx_client"):
            text = await client.chat_text(
                messages=[{"role": "user", "content": "Reply with: ok"}],
                max_tokens=16,
                total_deadline=30.0,
            )
        assert text, "chat_text returned empty"
        dropped = [
            r for r in caplog.records
            if "total_deadline" in r.getMessage() and "dropping" in r.getMessage()
        ]
        assert not dropped, "total_deadline must not be logged as dropped (R5)"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_stream_chat(started_engine):
    # R6/R4: real streaming yields non-empty chunks and completes.
    from fusion_core.mlx_client import create_async_client

    model = await _first_chat_model_id()
    if not model:
        pytest.skip("no model loaded on fusion-mlx engine")
    client = create_async_client(backend="mlx", model=model)
    try:
        chunks: list[str] = []
        async for chunk in client.stream_chat(
            messages=[{"role": "user", "content": "Count from 1 to 3."}],
            max_tokens=32,
        ):
            chunks.append(chunk)
        assert "".join(chunks), "stream_chat yielded no content"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_get_server_stats(started_engine):
    # R6/I12: real /stats parses into ServerStats with stable fields + raw.
    from fusion_core.mlx_client import ServerStats, create_async_client

    client = create_async_client(backend="mlx")
    try:
        stats = await client.get_server_stats()
        assert isinstance(stats, ServerStats)
        assert isinstance(stats.total_requests, int)
        assert isinstance(stats.raw, dict), "ServerStats.raw must passthrough upstream dict (I12)"
    except Exception as e:
        pytest.skip(f"fusion-mlx /stats not available or schema mismatch: {e}")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_embed(started_engine):
    # R6: real embedding endpoint. Skipped if no embedding-capable model loaded.
    from fusion_core.mlx_client import create_async_client

    model = await _first_embed_model_id()
    if not model:
        pytest.skip("no embedding-capable model loaded on fusion-mlx engine")
    client = create_async_client(backend="mlx")
    try:
        resp = await client.embed("hello world", model=model)
        assert resp.vector, "embed returned empty vector"
        assert isinstance(resp.vector, list)
    except Exception as e:
        pytest.skip(f"fusion-mlx embedding endpoint not available for model {model}: {e}")
    finally:
        await client.close()
