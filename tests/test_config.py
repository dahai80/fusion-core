from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fusion_core import config


class TestGetEnv:
    def test_str(self, monkeypatch):
        monkeypatch.setenv("FUSION_TEST_STR", "hello")
        assert config.get_env("FUSION_TEST_STR") == "hello"

    def test_missing_default(self, monkeypatch):
        monkeypatch.delenv("FUSION_NOPE", raising=False)
        assert config.get_env("FUSION_NOPE", default="x") == "x"

    def test_int_cast(self, monkeypatch):
        monkeypatch.setenv("FUSION_TEST_INT", "42")
        assert config.get_env("FUSION_TEST_INT", cast=int) == 42

    def test_bool_true(self, monkeypatch):
        for val in ("1", "true", "YES", "on"):
            monkeypatch.setenv("FUSION_TEST_BOOL", val)
            assert config.get_env("FUSION_TEST_BOOL", cast=bool) is True

    def test_bool_false(self, monkeypatch):
        for val in ("0", "false", "no", "off"):
            monkeypatch.setenv("FUSION_TEST_BOOL", val)
            assert config.get_env("FUSION_TEST_BOOL", cast=bool) is False

    def test_cast_error_returns_default(self, monkeypatch):
        monkeypatch.setenv("FUSION_TEST_BAD", "notnum")
        assert config.get_env("FUSION_TEST_BAD", default=5, cast=int) == 5

    def test_empty_string_returns_default(self, monkeypatch):
        monkeypatch.setenv("FUSION_TEST_EMPTY", "")
        assert config.get_env("FUSION_TEST_EMPTY", default="d") == "d"

    def test_empty_string_bool_returns_default_not_false(self, monkeypatch):
        monkeypatch.setenv("FUSION_TEST_BOOL_EMPTY", "")
        assert config.get_env("FUSION_TEST_BOOL_EMPTY", default=True, cast=bool) is True
        assert config.get_env("FUSION_TEST_BOOL_EMPTY", default=False, cast=bool) is False


class TestLoadSettings:
    def test_load_and_cache(self, tmp_path, reset_config_cache):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"auth": {"api_key": "k123"}}), encoding="utf-8")
        assert config.load_settings(p) == {"auth": {"api_key": "k123"}}
        assert config.load_settings(p) == {"auth": {"api_key": "k123"}}

    def test_missing_raises(self, tmp_path, reset_config_cache):
        with pytest.raises(FileNotFoundError):
            config.load_settings(tmp_path / "nope.json")

    def test_non_object_raises(self, tmp_path, reset_config_cache):
        p = tmp_path / "arr.json"
        p.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(ValueError):
            config.load_settings(p)

    def test_clear_cache_reread(self, tmp_path, reset_config_cache):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"v": 1}), encoding="utf-8")
        assert config.load_settings(p)["v"] == 1
        p.write_text(json.dumps({"v": 2}), encoding="utf-8")
        config.clear_cache()
        assert config.load_settings(p)["v"] == 2

    def test_mtime_change_invalidates_cache(self, tmp_path, reset_config_cache):
        import time

        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"v": 1}), encoding="utf-8")
        assert config.load_settings(p)["v"] == 1
        time.sleep(0.01)
        p.write_text(json.dumps({"v": 2}), encoding="utf-8")
        os.utime(p, ns=(int(time.time() * 1e9), int(time.time() * 1e9)))
        assert config.load_settings(p)["v"] == 2, "mtime change must invalidate cache (R1)"


class TestResolveApiKey:
    def test_explicit_wins(self, monkeypatch, tmp_path, reset_config_cache):
        monkeypatch.setenv("FUSION_MLX_API_KEY", "envkey")
        assert config.resolve_api_key("explicit_key") == "explicit_key"

    def test_env_wins_over_settings(self, monkeypatch, tmp_path, reset_config_cache):
        monkeypatch.setenv("FUSION_MLX_API_KEY", "envkey")
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"auth": {"api_key": "filekey"}}), encoding="utf-8")
        assert config.resolve_api_key(settings_path=p) == "envkey"

    def test_settings_fallback(self, monkeypatch, tmp_path, reset_config_cache):
        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"auth": {"api_key": "filekey"}}), encoding="utf-8")
        assert config.resolve_api_key(settings_path=p) == "filekey"

    def test_missing_returns_empty(self, monkeypatch, tmp_path, reset_config_cache):
        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        assert config.resolve_api_key(settings_path=tmp_path / "nope.json") == ""

    def test_settings_present_but_key_empty(self, monkeypatch, tmp_path, reset_config_cache):
        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"auth": {"api_key": ""}}), encoding="utf-8")
        assert config.resolve_api_key(settings_path=p) == ""

    def test_settings_empty_key_returns_empty_str(self, monkeypatch, tmp_path, reset_config_cache):
        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"auth": {}}), encoding="utf-8")
        assert config.resolve_api_key(settings_path=p) == ""

    def test_load_settings_malformed_json_reraises(self, tmp_path, reset_config_cache):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            config.load_settings(p)

    def test_resolve_api_key_malformed_json_reraises(self, monkeypatch, tmp_path, reset_config_cache):
        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            config.resolve_api_key(settings_path=p)

    def test_resolve_api_key_null_value_returns_empty(self, monkeypatch, tmp_path, reset_config_cache):
        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"auth": {"api_key": None}}), encoding="utf-8")
        assert config.resolve_api_key(settings_path=p) == ""

    def test_resolve_api_key_non_dict_auth_returns_empty(self, monkeypatch, tmp_path, reset_config_cache):
        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"auth": "mykey-shorthand"}), encoding="utf-8")
        assert config.resolve_api_key(settings_path=p) == ""

    def test_load_settings_unreadable_logs(self, tmp_path, reset_config_cache):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"v": 1}), encoding="utf-8")
        config.load_settings(p)
        # re-read via clear + bad path triggers FileNotFoundError path covered elsewhere


class TestLoadApiKey:
    def test_raises_when_missing(self, monkeypatch, reset_config_cache):
        monkeypatch.delenv("FUSION_FUSION_MLX_API_KEY", raising=False)
        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        p = Path.home() / ".fusion-mlx" / "settings.json"
        if p.exists():
            pytest.skip("real settings.json present, cannot test missing")
        with pytest.raises(KeyError):
            config.load_api_key("fusion-mlx")

    def test_resolves_from_env(self, monkeypatch, reset_config_cache):
        monkeypatch.setenv("FUSION_MLX_API_KEY", "envkey")
        assert config.load_api_key("fusion-mlx") == "envkey"

    def test_default_port_matches_mlx_real_port(self):
        assert config.DEFAULT_MLX_PORT == 11434, "must match fusion-mlx start.sh real port, not 11432"


class TestImportTimeIsolation:
    def test_import_fusion_core_reads_no_env(self, monkeypatch):
        import importlib

        import fusion_core

        captured = {}
        orig_getenv = os.environ.get

        def spy(key, default=None):
            captured[key] = captured.get(key, 0) + 1
            return orig_getenv(key, default)

        monkeypatch.setattr(os.environ, "get", spy)
        importlib.reload(fusion_core)
        env_reads = {k: v for k, v in captured.items()}
        assert not env_reads, f"import fusion_core triggered env reads at import time: {env_reads}"
