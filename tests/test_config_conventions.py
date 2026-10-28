from __future__ import annotations

import logging

import pytest

import fusion_core.config as config
from fusion_core.config import (
    admin_key,
    client_key,
    gateway_admin_key,
    gateway_base_url,
    profile,
    service_data_dir,
    service_port,
)

_KEY_ENVS = (
    "FUSION_CLIENT_KEY",
    "FUSION_MLX_API_KEY",
    "MLX_API_KEY",
    "FUSION_ADMIN_KEY",
    "FUSION_GATEWAY_API_KEY",
    "FUSION_PROFILE",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env in _KEY_ENVS:
        monkeypatch.delenv(env, raising=False)
    config._ANON_WARNED.clear()
    yield
    config._ANON_WARNED.clear()


class TestProfile:
    def test_default_dev(self):
        assert profile() == "dev"

    def test_explicit_prod(self, monkeypatch):
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        assert profile() == "prod"


class TestClientKey:
    def test_primary(self, monkeypatch):
        monkeypatch.setenv("FUSION_CLIENT_KEY", "fm-primary")
        assert client_key() == "fm-primary"

    def test_alias_fusion_mlx_api_key(self, monkeypatch):
        monkeypatch.setenv("FUSION_MLX_API_KEY", "fm-legacy1")
        assert client_key() == "fm-legacy1"

    def test_alias_mlx_api_key(self, monkeypatch):
        monkeypatch.setenv("MLX_API_KEY", "fm-legacy2")
        assert client_key() == "fm-legacy2"

    def test_precedence_primary_first(self, monkeypatch):
        monkeypatch.setenv("FUSION_CLIENT_KEY", "fm-primary")
        monkeypatch.setenv("FUSION_MLX_API_KEY", "fm-legacy1")
        monkeypatch.setenv("MLX_API_KEY", "fm-legacy2")
        assert client_key() == "fm-primary"

    def test_precedence_second_alias(self, monkeypatch):
        monkeypatch.setenv("FUSION_MLX_API_KEY", "fm-legacy1")
        monkeypatch.setenv("MLX_API_KEY", "fm-legacy2")
        assert client_key() == "fm-legacy1"

    def test_dev_missing_empty_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert client_key() == ""
        assert any("anonymous dev mode" in r.message for r in caplog.records)

    def test_dev_warns_once(self, caplog):
        with caplog.at_level(logging.WARNING):
            client_key()
            client_key()
        warns = [r for r in caplog.records if "anonymous dev mode" in r.message]
        assert len(warns) == 1

    def test_prod_missing_raises(self, monkeypatch):
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        with pytest.raises(RuntimeError, match="FUSION_CLIENT_KEY not set"):
            client_key()

    def test_prod_message_has_three_parts(self, monkeypatch):
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        with pytest.raises(RuntimeError) as exc_info:
            client_key()
        msg = str(exc_info.value)
        assert "FUSION_CLIENT_KEY" in msg
        assert "gateway admin API" in msg
        assert "config-conventions.md" in msg

    def test_prod_with_key_ok(self, monkeypatch):
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        monkeypatch.setenv("FUSION_CLIENT_KEY", "fm-live")
        assert client_key() == "fm-live"

    def test_empty_string_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("FUSION_CLIENT_KEY", "")
        with pytest.raises(RuntimeError):
            monkeypatch.setenv("FUSION_PROFILE", "prod")
            client_key()

    def test_legacy_alias_logs_deprecation(self, monkeypatch, caplog):
        monkeypatch.setenv("FUSION_MLX_API_KEY", "fm-legacy1")
        with caplog.at_level(logging.WARNING):
            assert client_key() == "fm-legacy1"
        assert any("deprecated" in r.message for r in caplog.records)


class TestAdminKey:
    def test_set(self, monkeypatch):
        monkeypatch.setenv("FUSION_ADMIN_KEY", "fm-admin")
        assert admin_key() == "fm-admin"

    def test_no_alias_chain(self, monkeypatch):
        monkeypatch.setenv("FUSION_CLIENT_KEY", "fm-primary")
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        with pytest.raises(RuntimeError, match="FUSION_ADMIN_KEY not set"):
            admin_key()

    def test_dev_missing_empty(self):
        assert admin_key() == ""

    def test_prod_missing_raises(self, monkeypatch):
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        with pytest.raises(RuntimeError, match="FUSION_ADMIN_KEY not set"):
            admin_key()


class TestGatewayBaseUrl:
    def test_default(self):
        assert gateway_base_url() == "http://127.0.0.1:11432"

    def test_env(self, monkeypatch):
        monkeypatch.setenv("FUSION_GATEWAY_BASE_URL", "http://gw:11432")
        assert gateway_base_url() == "http://gw:11432"

    def test_legacy_alias_with_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("FUSION_GATEWAY_URL", "http://old-gw:11432")
        with caplog.at_level(logging.WARNING):
            assert gateway_base_url() == "http://old-gw:11432"
        assert any("deprecated" in r.message for r in caplog.records)

    def test_primary_wins_over_legacy(self, monkeypatch):
        monkeypatch.setenv("FUSION_GATEWAY_BASE_URL", "http://gw:11432")
        monkeypatch.setenv("FUSION_GATEWAY_URL", "http://old-gw:11432")
        assert gateway_base_url() == "http://gw:11432"


class TestServicePort:
    def test_env(self, monkeypatch):
        monkeypatch.setenv("FUSION_RAG_PORT", "11501")
        assert service_port("rag") == 11501

    def test_fusion_prefix_stripped(self, monkeypatch):
        monkeypatch.setenv("FUSION_RAG_PORT", "11501")
        assert service_port("fusion-rag") == 11501

    def test_both_forms_identical(self, monkeypatch):
        monkeypatch.setenv("FUSION_MLX_PORT", "11434")
        assert service_port("mlx") == service_port("fusion-mlx") == 11434

    def test_dashes_normalized(self, monkeypatch):
        monkeypatch.setenv("FUSION_MODEL_HUB_PORT", "11510")
        assert service_port("model-hub") == 11510

    def test_default(self):
        assert service_port("rag", default=11501) == 11501

    def test_env_wins_over_default(self, monkeypatch):
        monkeypatch.setenv("FUSION_RAG_PORT", "11501")
        assert service_port("rag", default=9999) == 11501

    def test_missing_no_default_raises(self):
        with pytest.raises(ValueError, match="FUSION_RAG_PORT not set"):
            service_port("rag")

    def test_missing_no_default_raises_full_name(self):
        with pytest.raises(ValueError, match="FUSION_RAG_PORT not set"):
            service_port("fusion-rag")

    def test_non_integer_raises(self, monkeypatch):
        monkeypatch.setenv("FUSION_RAG_PORT", "http")
        with pytest.raises(ValueError, match="not an integer"):
            service_port("rag")

    def test_empty_app_raises(self):
        with pytest.raises(ValueError):
            service_port("")


class TestServiceDataDir:
    def test_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FUSION_RAG_DATA_DIR", str(tmp_path))
        assert service_data_dir("rag") == tmp_path

    def test_fusion_prefix_stripped(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FUSION_RAG_DATA_DIR", str(tmp_path))
        assert service_data_dir("fusion-rag") == tmp_path

    def test_explicit_default(self, tmp_path):
        assert service_data_dir("rag", default=tmp_path / "d") == tmp_path / "d"

    def test_home_default_strips_fusion_prefix(self):
        from pathlib import Path

        assert service_data_dir("fusion-rag") == Path.home() / ".rag"
        assert str(service_data_dir("rag")).endswith("/.rag")

    def test_empty_app_raises(self):
        with pytest.raises(ValueError):
            service_data_dir("")


class TestGatewayAdminKey:
    def test_env(self, monkeypatch):
        monkeypatch.setenv("FUSION_GATEWAY_API_KEY", "gk-env")
        assert gateway_admin_key() == "gk-env"

    def test_settings_fallback(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text('{"shell": {"gateway_admin_key": "gk-file"}}', encoding="utf-8")
        assert gateway_admin_key(settings_path=settings) == "gk-file"

    def test_env_wins_over_settings(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FUSION_GATEWAY_API_KEY", "gk-env")
        settings = tmp_path / "settings.json"
        settings.write_text('{"shell": {"gateway_admin_key": "gk-file"}}', encoding="utf-8")
        assert gateway_admin_key(settings_path=settings) == "gk-env"

    def test_missing_settings_dev_empty(self, tmp_path):
        assert gateway_admin_key(settings_path=tmp_path / "absent.json") == ""

    def test_missing_settings_prod_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        with pytest.raises(RuntimeError, match="FUSION_GATEWAY_API_KEY not set"):
            gateway_admin_key(settings_path=tmp_path / "absent.json")

    def test_prod_message_has_three_parts(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        with pytest.raises(RuntimeError) as exc_info:
            gateway_admin_key(settings_path=tmp_path / "absent.json")
        msg = str(exc_info.value)
        assert "FUSION_GATEWAY_API_KEY" in msg
        assert "gateway admin API" in msg
        assert "config-conventions.md" in msg

    def test_dev_warns_once(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING, logger="fusion_core.config")
        assert gateway_admin_key(settings_path=tmp_path / "absent.json") == ""
        assert gateway_admin_key(settings_path=tmp_path / "absent.json") == ""
        warns = [r for r in caplog.records if "anonymous dev mode" in r.message]
        assert len(warns) == 1

    def test_shell_not_dict_ignored(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text('{"shell": "bogus"}', encoding="utf-8")
        assert gateway_admin_key(settings_path=settings) == ""

    def test_shell_key_null_ignored(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text('{"shell": {"gateway_admin_key": null}}', encoding="utf-8")
        assert gateway_admin_key(settings_path=settings) == ""

    def test_shell_key_non_string_ignored(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text('{"shell": {"gateway_admin_key": 123}}', encoding="utf-8")
        assert gateway_admin_key(settings_path=settings) == ""

    def test_prod_with_env_ok(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        monkeypatch.setenv("FUSION_GATEWAY_API_KEY", "gk-live")
        assert gateway_admin_key(settings_path=tmp_path / "absent.json") == "gk-live"

    def test_malformed_settings_dev_empty(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text("{not json", encoding="utf-8")
        assert gateway_admin_key(settings_path=settings) == ""
