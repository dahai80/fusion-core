from __future__ import annotations

import json as _json
import logging as _stdlib_logging
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
        fh = _stdlib_logging.FileHandler(p, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    logger.info("logging initialized, name=%s level=%s", name, level)
    return logger


def get_logger(name: str) -> _stdlib_logging.Logger:
    return _stdlib_logging.getLogger(name)
