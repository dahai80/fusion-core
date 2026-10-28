from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def set_process_title(title: str) -> None:
    try:
        import setproctitle

        setproctitle.setproctitle(title)
        logger.debug("process title set: %s", title)
    except Exception as exc:
        logger.debug("setproctitle unavailable, process title stays default (%s): %s", title, exc)
