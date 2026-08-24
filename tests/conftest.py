from __future__ import annotations

import os

import pytest


@pytest.fixture
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("FUSION_"):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def reset_config_cache():
    from fusion_core import config as _config

    _config.clear_cache()
    yield
    _config.clear_cache()
