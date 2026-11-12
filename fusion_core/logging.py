from __future__ import annotations

import json as _json
import logging as _stdlib_logging
import logging.handlers as _stdlib_handlers
import sys
from datetime import UTC, datetime
from pathlib import Path

_logger = _stdlib_logging.getLogger(__name__)


class _JsonFormatter(_stdlib_logging.Formatter):
    def format(self, record: _stdlib_logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        try:
            from fusion_core.tenant.context import current as _tenant_current

            ctx = _tenant_current()
        except Exception:
            ctx = None
        if ctx is not None:
            payload["tenant_id"] = ctx.tenant_id
            if ctx.user_id is not None:
                payload["user_id"] = ctx.user_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False)


def setup_logging(
    name: str,
    *,
    level: str = "INFO",
    json_format: bool = False,
    log_file: str | Path | None = None,
    propagate: bool = True,
    max_bytes: int | None = None,
    backup_count: int = 3,
) -> _stdlib_logging.Logger:
    logger = _stdlib_logging.getLogger(name)
    level_attr = getattr(_stdlib_logging, level.upper(), None)
    if level_attr is None:
        raise ValueError(f"invalid log level: {level!r}")
    logger.setLevel(level_attr)
    logger.propagate = propagate
    has_real_handler = any(not isinstance(h, _stdlib_logging.NullHandler) for h in logger.handlers)
    if has_real_handler:
        _logger.debug("setup_logging: %s already configured, level applied, skip handler add", name)
        return logger
    for h in list(logger.handlers):
        if isinstance(h, _stdlib_logging.NullHandler):
            logger.removeHandler(h)
    fmt = (
        _JsonFormatter()
        if json_format
        else _stdlib_logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler = _stdlib_logging.StreamHandler(sys.stderr)
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    if log_file:
        p = Path(log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        if max_bytes is not None:
            # size-based rotation (issue #47): e.g. max_bytes=10 * 1024 * 1024,
            # backup_count=3 -> my.log + my.log.1..my.log.3
            fh = _stdlib_handlers.RotatingFileHandler(p, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        else:
            fh = _stdlib_logging.FileHandler(p, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    logger.info("logging initialized, name=%s level=%s max_bytes=%s", name, level, max_bytes)
    return logger


def get_logger(name: str) -> _stdlib_logging.Logger:
    return _stdlib_logging.getLogger(name)
