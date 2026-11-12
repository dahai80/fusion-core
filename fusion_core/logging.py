from __future__ import annotations

import json as _json
import logging as _stdlib_logging
import logging.handlers as _stdlib_handlers
import sys
from datetime import UTC, datetime
from pathlib import Path

_logger = _stdlib_logging.getLogger(__name__)

# Standard LogRecord attributes — anything else in record.__dict__ came in via
# extra={} (issue #50) and is rendered by the formatters.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def _extra_fields(record: _stdlib_logging.LogRecord) -> dict:
    return {k: v for k, v in record.__dict__.items() if k not in _RESERVED_RECORD_ATTRS and not k.startswith("_")}


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
        extra = _extra_fields(record)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False)


class _TextFormatter(_stdlib_logging.Formatter):
    def __init__(self, fmt: str, datefmt: str):
        super().__init__(fmt=fmt, datefmt=datefmt)

    def format(self, record: _stdlib_logging.LogRecord) -> str:
        line = super().format(record)
        extra = _extra_fields(record)
        if extra:
            # extra={} fields appended as key=value pairs, sorted for stable output
            suffix = " ".join(f"{k}={v!r}" for k, v in sorted(extra.items()))
            line = f"{line} {suffix}"
        return line


def setup_logging(
    name: str = "fusion",
    *,
    level: str = "INFO",
    json_format: bool = False,
    log_file: str | Path | None = None,
    propagate: bool = True,
    max_bytes: int | None = None,
    backup_count: int = 3,
    root: bool = False,
) -> _stdlib_logging.Logger:
    # root=True (issue #50): attach handlers to the root logger for apps migrating
    # from root-logger patterns. name is ignored; idempotent via the same
    # already-configured guard as named loggers.
    logger = _stdlib_logging.getLogger() if root else _stdlib_logging.getLogger(name)
    level_attr = getattr(_stdlib_logging, level.upper(), None)
    if level_attr is None:
        raise ValueError(f"invalid log level: {level!r}")
    logger.setLevel(level_attr)
    logger.propagate = propagate if not root else False
    has_real_handler = any(not isinstance(h, _stdlib_logging.NullHandler) for h in logger.handlers)
    if has_real_handler:
        _logger.debug("setup_logging: %s already configured, level applied, skip handler add", logger.name)
        return logger
    for h in list(logger.handlers):
        if isinstance(h, _stdlib_logging.NullHandler):
            logger.removeHandler(h)
    if json_format:
        fmt: _stdlib_logging.Formatter = _JsonFormatter()
    else:
        fmt = _TextFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
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
    logger.info("logging initialized, name=%s level=%s max_bytes=%s root=%s", logger.name, level, max_bytes, root)
    return logger


def get_logger(name: str) -> _stdlib_logging.Logger:
    return _stdlib_logging.getLogger(name)
