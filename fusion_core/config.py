from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MLX_PORT = 11434
DEFAULT_GATEWAY_PORT = 11432

_SETTINGS_CACHE: dict[str, tuple[dict, float]] = {}

_BOOL_TRUE = {"1", "true", "yes", "on", "y", "t"}
_BOOL_FALSE = {"0", "false", "no", "off", "n", "f"}


def default_mlx_base_url() -> str:
    return os.environ.get("FUSION_MLX_URL", f"http://localhost:{DEFAULT_MLX_PORT}/v1")


def default_gateway_base_url() -> str:
    return os.environ.get("FUSION_GATEWAY_URL", f"http://localhost:{DEFAULT_GATEWAY_PORT}")


def get_env(key: str, default: Any = None, *, cast: type = str) -> Any:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in _BOOL_TRUE
    try:
        return cast(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("get_env cast %s for %s failed: %s", cast.__name__, key, exc)
        return default


def load_settings(path: str | Path | None = None) -> dict:
    if path is None:
        path = Path.home() / ".fusion-mlx" / "settings.json"
    key = str(path)
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except FileNotFoundError:
        raise FileNotFoundError(f"settings file not found: {key}") from None
    cached = _SETTINGS_CACHE.get(key)
    if cached is not None:
        data, cached_mtime = cached
        if cached_mtime == mtime:
            return data
        logger.info("load_settings cache invalidated (mtime changed) for %s", key)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("load_settings malformed json %s: %s", key, exc)
        raise
    except OSError as exc:
        logger.warning("load_settings read %s failed: %s", key, exc)
        raise
    if not isinstance(data, dict):
        raise ValueError(f"settings file {key} is not a json object")
    _SETTINGS_CACHE[key] = (data, mtime)
    return data


def resolve_api_key(
    explicit: str | None = None,
    *,
    env_var: str = "FUSION_MLX_API_KEY",
    settings_path: str | Path | None = None,
) -> str:
    if explicit:
        return explicit
    key = os.environ.get(env_var, "")
    if key:
        return key
    try:
        cfg = load_settings(settings_path)
    except FileNotFoundError:
        logger.debug("resolve_api_key: settings.json absent, no api_key resolved")
        return ""
    auth = cfg.get("auth", {})
    if not isinstance(auth, dict):
        logger.warning(
            "resolve_api_key: auth is not a dict in settings, got %s; cannot read api_key",
            type(auth).__name__,
        )
        return ""
    raw = auth.get("api_key", "")
    if raw is None:
        logger.warning("resolve_api_key: auth.api_key is null in settings.json")
        return ""
    if not isinstance(raw, str):
        logger.warning("resolve_api_key: auth.api_key not a string, got %s", type(raw).__name__)
        return ""
    return raw


def load_api_key(name: str = "fusion-mlx") -> str:
    short = name.removeprefix("fusion-")
    env_var = f"FUSION_{short.upper().replace('-', '_')}_API_KEY"
    key = resolve_api_key(env_var=env_var)
    if not key:
        raise KeyError(f"api key not found for {name}: set env {env_var} or auth.api_key in settings.json")
    return key


def clear_cache() -> None:
    _SETTINGS_CACHE.clear()
    logger.debug("config cache cleared")
