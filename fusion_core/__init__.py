from __future__ import annotations

import logging as _stdlib_logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("fusion-core")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

_logger = _stdlib_logging.getLogger("fusion_core")
if not _logger.handlers:
    _handler = _stdlib_logging.NullHandler()
    _logger.addHandler(_handler)

from fusion_core.config import (  # noqa: E402
    clear_cache,
    get_env,
    load_api_key,
    load_settings,
    resolve_api_key,
)
from fusion_core.http_client import (  # noqa: E402
    close_all,
    get_async_client,
    get_metrics_snapshot,
    reset_metrics,
    set_metrics_callback,
    with_retry,
)
from fusion_core.logging import get_logger, setup_logging  # noqa: E402
from fusion_core.mlx_client import (  # noqa: E402
    EmbeddingResponse,
    FusionMLXClient,
    LLMResponse,
    create_async_client,
)
from fusion_core.parse import (  # noqa: E402
    ParseError,
    parse_llm_json,
    parse_llm_json_lenient,
    parse_llm_json_safe,
    strip_code_fence,
)
from fusion_core.prompt import PromptManager  # noqa: E402

__all__ = [
    "__version__",
    "ParseError",
    "parse_llm_json",
    "parse_llm_json_lenient",
    "parse_llm_json_safe",
    "strip_code_fence",
    "get_env",
    "load_api_key",
    "resolve_api_key",
    "load_settings",
    "clear_cache",
    "get_logger",
    "setup_logging",
    "FusionMLXClient",
    "LLMResponse",
    "EmbeddingResponse",
    "create_async_client",
    "get_async_client",
    "with_retry",
    "close_all",
    "set_metrics_callback",
    "get_metrics_snapshot",
    "reset_metrics",
    "PromptManager",
]

try:
    from fusion_core.http import create_app, install_auth, standard_error_handler  # noqa: F401

    __all__.extend(["create_app", "install_auth", "standard_error_handler"])
except ImportError:
    _logger.debug("fastapi not installed; fusion_core.http factory unavailable")
