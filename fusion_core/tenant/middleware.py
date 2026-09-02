from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from fusion_core.tenant.context import (
    TenantContext,
    TenantContextError,
    reset,
    set_context,
)
from fusion_core.tenant.jwt_utils import decode_jwt_claims

logger = logging.getLogger(__name__)

_DEFAULT_EXEMPT = frozenset({"/health", "/docs", "/openapi.json", "/redoc", "/health/deep", "/api/health"})

VerifyJwt = Callable[[str], dict[str, Any]]


def _hget(scope: dict[str, Any], name: str) -> str:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1")
    return ""


async def _reject(send, status: int, detail: str, rid: str) -> None:
    body = (
        b'{"error": "Unauthorized", "detail": "'
        + detail.encode("latin-1")
        + b'", "request_id": "'
        + rid.encode("latin-1")
        + b'"}'
    )
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class TenantMiddleware:
    def __init__(
        self,
        app: Any,
        *,
        exempt_paths: frozenset[str] | None = None,
        verify_jwt: VerifyJwt | None = None,
        require_jwt: bool = True,
    ) -> None:
        self._app = app
        self._exempt = exempt_paths or _DEFAULT_EXEMPT
        self._verify_jwt = verify_jwt
        self._require_jwt = require_jwt

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = (scope["path"] or "/").rstrip("/") or "/"
        rid = scope.get("state", {}).get("request_id") or str(uuid.uuid4())
        if path in self._exempt:
            await self._app(scope, receive, send)
            return
        tenant_header = _hget(scope, b"x-tenant-id")
        user_header = _hget(scope, b"x-user-id")
        auth_header = _hget(scope, b"authorization")
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        if not tenant_header:
            logger.warning(
                "tenant middleware reject: missing X-Tenant-Id path=%s rid=%s",
                path,
                rid,
            )
            await _reject(send, 401, "missing X-Tenant-Id", rid)
            return
        claims: dict[str, Any] | None = None
        if token:
            if self._verify_jwt is not None:
                try:
                    claims = self._verify_jwt(token)
                except Exception as exc:
                    logger.warning(
                        "tenant middleware reject: jwt verify failed rid=%s: %s",
                        rid,
                        exc,
                    )
                    await _reject(send, 401, "invalid token", rid)
                    return
            else:
                try:
                    claims = decode_jwt_claims(token)
                except TenantContextError as exc:
                    logger.warning(
                        "tenant middleware reject: jwt decode failed rid=%s: %s",
                        rid,
                        exc,
                    )
                    await _reject(send, 401, "invalid token", rid)
                    return
        if claims is not None:
            jwt_tid = claims.get("tid") or claims.get("tenant")
            if jwt_tid is not None and str(jwt_tid) != tenant_header:
                logger.warning(
                    "tenant middleware reject: mismatch X-Tenant-Id=%s jwt.tid=%s rid=%s",
                    tenant_header,
                    jwt_tid,
                    rid,
                )
                await _reject(send, 401, "tenant mismatch", rid)
                return
        elif self._require_jwt:
            logger.warning(
                "tenant middleware reject: missing bearer token path=%s rid=%s",
                path,
                rid,
            )
            await _reject(send, 401, "missing token", rid)
            return
        ctx = TenantContext(
            tenant_id=tenant_header,
            user_id=user_header or None,
            role=claims.get("role") if claims else None,
            jti=claims.get("jti") if claims else None,
            scopes=tuple(claims.get("scope") or claims.get("scopes") or ()) if claims else (),
        )
        token_ctx = set_context(ctx)
        try:
            await self._app(scope, receive, send)
        finally:
            reset(token_ctx)


def install_tenant_middleware(
    app,
    *,
    exempt_paths: frozenset[str] | None = None,
    verify_jwt: VerifyJwt | None = None,
    require_jwt: bool = True,
) -> None:
    app.add_middleware(
        TenantMiddleware,
        exempt_paths=exempt_paths,
        verify_jwt=verify_jwt,
        require_jwt=require_jwt,
    )
    app.middleware_stack = None
    app.build_middleware_stack()
    logger.info(
        "install_tenant_middleware: configured fail-closed tenant isolation for %s",
        getattr(app, "title", type(app).__name__),
    )
