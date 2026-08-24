from __future__ import annotations

import hmac
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from fusion_core import config as _config

logger = logging.getLogger(__name__)

_UNAUTH_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


def standard_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    if isinstance(exc, HTTPException):
        logger.warning("http exception request_id=%s path=%s status=%s", request_id, request.url.path, exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.__class__.__name__, "detail": exc.detail, "request_id": request_id},
        )
    logger.error("unhandled error request_id=%s path=%s: %s", request_id, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": exc.__class__.__name__,
            "detail": "internal error; see server logs with request_id",
            "request_id": request_id,
        },
    )


def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    logger.warning("validation error request_id=%s path=%s: %s", request_id, request.url.path, exc.errors())
    return JSONResponse(
        status_code=422,
        content={
            "error": "RequestValidationError",
            "detail": "request body validation failed; see server logs with request_id",
            "request_id": request_id,
        },
    )


class _RequestIdASGIMiddleware:
    def __init__(self, app: Any):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        rid = None
        for k, v in scope.get("headers") or []:
            if k == b"x-request-id":
                rid = v.decode("latin-1")
                break
        if not rid:
            rid = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = rid
        start = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                ms = f"{(time.perf_counter() - start) * 1000:.1f}"
                out = list(message.get("headers") or [])
                out.append((b"x-request-id", rid.encode("latin-1")))
                out.append((b"x-process-ms", ms.encode("latin-1")))
                message["headers"] = out
            await send(message)

        await self._app(scope, receive, send_wrapper)


class _AuthASGIMiddleware:
    def __init__(self, app: Any, keys_list: list[str]):
        self._app = app
        self._keys_list = list(keys_list)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = (scope["path"] or "/").rstrip("/") or "/"
        if path in _UNAUTH_PATHS:
            await self._app(scope, receive, send)
            return
        auth_header = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth_header = v.decode("latin-1")
                break
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        if not any(hmac.compare_digest(token, k) for k in self._keys_list):
            # request_id middleware is OUTERMOST (install_auth re-orders), so
            # scope["state"]["request_id"] is already set by it. The outer
            # middleware's send_wrapper also adds the x-request-id response
            # header, so we must NOT add it here (would duplicate). Fall back to
            # a uuid only if request_id middleware somehow didn't run.
            rid = scope.get("state", {}).get("request_id") or str(uuid.uuid4())
            body = (
                b'{"error": "Unauthorized", "detail": "invalid or missing api key", "request_id": "'
                + rid.encode("latin-1")
                + b'"}'
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self._app(scope, receive, send)


def create_app(
    name: str,
    *,
    cors_origins: list[str] | None = None,
    cors_credentials: bool = False,
    version: str = "0.1.0",
    enable_health: bool = True,
) -> FastAPI:
    app = FastAPI(title=name, version=version)

    if cors_origins:
        if cors_credentials and "*" in cors_origins:
            raise ValueError(
                "create_app: allow_credentials=True forbids wildcard origin "
                "(CORS Fetch spec 3.2); pass concrete origins or disable credentials"
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=cors_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    if enable_health:

        @app.get("/health")
        async def _health():
            return {"status": "ok", "service": name, "version": version}

    app.add_exception_handler(Exception, standard_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_middleware(_RequestIdASGIMiddleware)

    logger.info("create_app: %s v%s health=%s cors=%s", name, version, enable_health, bool(cors_origins))
    return app


def install_auth(app: FastAPI, *, api_keys: list[str] | None = None) -> None:
    keys = api_keys
    if keys is None:
        resolved = _config.resolve_api_key()
        if not resolved:
            raise KeyError(
                "install_auth: no api key provided and none resolvable from env/settings.json; refusing to start unauthenticated"
            )
        keys = [resolved]
    app.add_middleware(_AuthASGIMiddleware, keys_list=list(keys))
    # Re-order so _RequestIdASGIMiddleware is outermost: add_middleware inserts at
    # the FRONT of user_middleware, so the LAST add is outermost. create_app already
    # added _RequestIdASGIMiddleware (making auth outer when auth is added here last).
    # Remove the existing request_id entry and re-add it LAST so it sits outside auth
    # and 401 responses carry the SAME id the downstream request_id middleware would
    # have assigned (H1: previously auth's uuid fallback produced a divergent id).
    app.user_middleware = [m for m in app.user_middleware if m.cls is not _RequestIdASGIMiddleware]
    app.add_middleware(_RequestIdASGIMiddleware)
    # Force rebuild of the cached middleware stack so the new order takes effect
    # even if the stack was already built (e.g. TestClient startup).
    app.middleware_stack = None
    app.build_middleware_stack()
    logger.info("install_auth: configured bearer key auth for %s (request_id outermost)", app.title)
