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
