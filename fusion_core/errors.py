from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Shared base for the fusion error tree (issue #47): a stable machine-readable
# `code`, an operator-facing `remediation` hint, structured `context`, and the
# `http_status` the error maps to. Subclasses set class-level defaults;
# instances may override per-raise. Wrap upstream failures with
# `raise ... from` so the chain stays intact.
class FusionError(Exception):
    code: str = "fusion_error"
    http_status: int = 500
    remediation: str = ""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        remediation: str | None = None,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if remediation is not None:
            self.remediation = remediation
        self.context = dict(context or {})
        logger.debug(
            "FusionError raised code=%s status=%s context_keys=%s",
            self.code,
            self.http_status,
            sorted(self.context),
        )

    def to_http(self) -> dict:
        # Sanitized payload for API error responses (fusion_core.http handlers or app-level).
        return {
            "status": self.http_status,
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
            "context": self.context,
        }
