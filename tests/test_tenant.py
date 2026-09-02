from __future__ import annotations

import base64
import json
import logging
import time

import pytest

from fusion_core.logging import setup_logging
from fusion_core.tenant import (
    TenantContext,
    TenantContextError,
    current,
    from_mapping,
    reset,
    set_context,
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_jwt(claims: dict) -> str:
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.sig"


def _make_app(captured: dict):
    pytest.importorskip("starlette")
    from starlette.applications import Starlette
    from starlette.routing import Route

    async def data(request):
        ctx = current()
        captured["tenant_id"] = ctx.tenant_id if ctx else None
        captured["user_id"] = ctx.user_id if ctx else None
        captured["role"] = ctx.role if ctx else None
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/data", data), Route("/health", data)])
    return app


class TestTenantMiddleware:
    def test_missing_tenant_id_rejects_401(self):
        from fusion_core.tenant import install_tenant_middleware

        client = pytest.importorskip("starlette.testclient")
        captured: dict = {}
        app = _make_app(captured)
        install_tenant_middleware(app, require_jwt=False)
        with client.TestClient(app) as c:
            r = c.get("/data")
            assert r.status_code == 401
            assert r.json()["detail"] == "missing X-Tenant-Id"
            assert captured == {}, "app must not run after reject (fail-closed)"

    def test_tenant_mismatch_rejects_401(self):
        from fusion_core.tenant import install_tenant_middleware

        client = pytest.importorskip("starlette.testclient")
        captured: dict = {}
        token = _make_jwt({"tid": "tenant-a", "sub": "u1", "role": "member"})
        app = _make_app(captured)
        install_tenant_middleware(app, require_jwt=True)
        with client.TestClient(app) as c:
            r = c.get(
                "/data",
                headers={
                    "X-Tenant-Id": "tenant-b",
                    "Authorization": f"Bearer {token}",
                },
            )
            assert r.status_code == 401
            assert r.json()["detail"] == "tenant mismatch"
            assert captured == {}

    def test_exempt_route_passes(self):
        from fusion_core.tenant import install_tenant_middleware

        client = pytest.importorskip("starlette.testclient")
        captured: dict = {}
        app = _make_app(captured)
        install_tenant_middleware(app, require_jwt=True)
        with client.TestClient(app) as c:
            r = c.get("/health")
            assert r.status_code == 200

    def test_context_bound_and_reset(self):
        from fusion_core.tenant import install_tenant_middleware

        client = pytest.importorskip("starlette.testclient")
        captured: dict = {}
        token = _make_jwt({"tid": "tenant-x", "sub": "u9", "role": "operator", "scope": ["read"]})
        app = _make_app(captured)
        install_tenant_middleware(app, require_jwt=True)
        with client.TestClient(app) as c:
            r = c.get(
                "/data",
                headers={
                    "X-Tenant-Id": "tenant-x",
                    "X-User-Id": "u9",
                    "Authorization": f"Bearer {token}",
                },
            )
            assert r.status_code == 200
            assert captured["tenant_id"] == "tenant-x"
            assert captured["user_id"] == "u9"
            assert captured["role"] == "operator"
        assert current() is None, "contextvar must reset after request (no leak)"


class TestJwtUtils:
    def test_decode_jwt_claims_extracts_tid(self):
        from fusion_core.tenant import decode_jwt_claims

        token = _make_jwt({"tid": "acme", "sub": "u1"})
        claims = decode_jwt_claims(token)
        assert claims["tid"] == "acme"
        assert claims["sub"] == "u1"

    def test_decode_jwt_claims_expired_raises(self):
        from fusion_core.tenant import decode_jwt_claims

        token = _make_jwt({"tid": "acme", "exp": int(time.time()) - 3600})
        with pytest.raises(TenantContextError, match="expired"):
            decode_jwt_claims(token)


class TestLoggingInjection:
    def test_logging_injects_tenant_id(self, capsys):
        name = "fusion_test_tenant_log"
        root_logger = logging.getLogger(name)
        saved_handlers = list(root_logger.handlers)
        try:
            logger = setup_logging(name, json_format=True)
            ctx = TenantContext(tenant_id="tenant-log", user_id="u-log")
            tok = set_context(ctx)
            try:
                logger.info("tenant scoped log line")
            finally:
                reset(tok)
            for h in logger.handlers:
                h.flush()
            captured = capsys.readouterr()
            line = captured.err.strip().splitlines()[-1]
            payload = json.loads(line)
            assert payload["tenant_id"] == "tenant-log"
            assert payload["user_id"] == "u-log"
        finally:
            root_logger.handlers = saved_handlers


class TestCoworkPrincipalBridge:
    def test_cowork_principal_bridge(self):
        ctx = from_mapping({"tid": "tenant-cw", "sub": "u-cw", "role": "member", "scope": ["run"]})
        assert ctx.tenant_id == "tenant-cw"
        assert ctx.user_id == "u-cw"
        assert ctx.role == "member"
        assert "run" in ctx.scopes
        tok = set_context(ctx)
        try:
            assert current() is ctx
            assert ctx.tenant_id == "tenant-cw"
        finally:
            reset(tok)
        assert current() is None
