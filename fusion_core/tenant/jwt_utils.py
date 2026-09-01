from __future__ import annotations

import base64
import json
import time
from typing import Any

from fusion_core.tenant.context import TenantContext, TenantContextError, from_mapping

_logger_log = None


def _log():
    global _logger_log
    if _logger_log is None:
        import logging

        _logger_log = logging.getLogger("fusion_core.tenant.jwt")
    return _logger_log


def _b64url_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    try:
        return base64.urlsafe_b64decode(seg + pad)
    except Exception as exc:
        raise TenantContextError(f"invalid base64url segment: {exc}") from exc


def decode_jwt_claims(token: str, *, verify_exp: bool = True) -> dict[str, Any]:
    token = token.strip()
    if not token:
        raise TenantContextError("empty token")
    parts = token.split(".")
    if len(parts) != 3:
        raise TenantContextError(f"expected 3 jwt segments, got {len(parts)}")
    try:
        payload_raw = _b64url_decode(parts[1])
        claims = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        raise TenantContextError(f"jwt payload not json: {exc}") from exc
    except TenantContextError:
        raise
    if not isinstance(claims, dict):
        raise TenantContextError("jwt payload not an object")
    if verify_exp:
        exp = claims.get("exp")
        if exp is not None:
            try:
                if int(exp) < int(time.time()):
                    raise TenantContextError("jwt expired")
            except (TypeError, ValueError) as exc:
                raise TenantContextError(f"invalid exp claim: {exc}") from exc
    return claims


def tenant_context_from_token(token: str, *, verify_exp: bool = True) -> TenantContext:
    claims = decode_jwt_claims(token, verify_exp=verify_exp)
    try:
        return from_mapping(claims)
    except TenantContextError:
        raise
