from __future__ import annotations

import pytest

fusion_http = pytest.importorskip("fusion_core.http")
from fusion_core.http import create_app, install_auth  # noqa: E402


class TestCreateApp:
    def test_health_endpoint(self):
        app = create_app("svc-test")
        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app) as c:
            r = c.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "ok"
            assert body["service"] == "svc-test"

    def test_request_id_header(self):
        app = create_app("svc-rid")
        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app) as c:
            r = c.get("/health")
            assert r.headers.get("x-request-id")

    def test_error_handler_format(self):
        app = create_app("svc-err")

        @app.get("/boom")
        async def boom():
            raise RuntimeError("kaboom")

        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/boom")
            assert r.status_code == 500
            body = r.json()
            assert body["error"] == "RuntimeError"
            assert "request_id" in body

    def test_error_handler_does_not_leak_exception_text(self):
        app = create_app("svc-leak")

        @app.get("/secret")
        async def secret():
            raise FileNotFoundError("/etc/fusion/secrets.json")

        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/secret")
            assert r.status_code == 500
            detail = r.json()["detail"]
            assert "secrets.json" not in detail
            assert "/etc/fusion" not in detail

    def test_cors_middleware(self):
        app = create_app("svc-cors", cors_origins=["*"])
        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app) as c:
            r = c.options("/health", headers={"Origin": "http://x", "Access-Control-Request-Method": "GET"})
            assert r.status_code in (200, 204)

    def test_cors_wildcard_with_credentials_raises(self):
        with pytest.raises(ValueError):
            create_app("svc-badcors", cors_origins=["*"], cors_credentials=True)

    def test_http_exception_preserved_not_500(self):
        from fastapi import HTTPException

        app = create_app("svc-httpexc")

        @app.get("/missing")
        async def missing():
            raise HTTPException(status_code=404, detail="nope")

        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/missing")
            assert r.status_code == 404
            assert r.json()["detail"] == "nope"


class TestInstallAuth:
    def test_no_key_raises(self, monkeypatch):
        monkeypatch.delenv("FUSION_MLX_API_KEY", raising=False)
        import fusion_core.config as _cfg

        monkeypatch.setattr(_cfg, "resolve_api_key", lambda *a, **k: "")
        app = create_app("svc-noauth")
        with pytest.raises(KeyError):
            install_auth(app, api_keys=None)

    def test_explicit_keys_accepted(self):
        app = create_app("svc-auth")
        install_auth(app, api_keys=["secret123"])
        auth_mw = next(
            (m for m in app.user_middleware if m.cls.__name__ == "_AuthASGIMiddleware"),
            None,
        )
        assert auth_mw is not None, "install_auth must add _AuthASGIMiddleware (I1)"
        assert auth_mw.kwargs["keys_list"] == ["secret123"]
        assert not hasattr(app.state, "_fusion_auth_keys"), "api keys must NOT live in app.state (I2)"

    def test_trailing_slash_health_not_401(self):
        app = create_app("svc-slash")
        install_auth(app, api_keys=["secret123"])
        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app) as c:
            r = c.get("/health/")
            assert r.status_code == 200, "trailing-slash health must not 401 after rstrip normalization (R7)"

    def test_validation_error_sanitized(self):
        from pydantic import BaseModel

        app = create_app("svc-422")

        class Body(BaseModel):
            name: str

        @app.post("/validate")
        async def validate(body: Body):
            return {"ok": True}

        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app, raise_server_exceptions=False) as c:
            r = c.post("/validate", json={"name": 123})
            assert r.status_code == 422, "type-mismatch body must 422"
            body = r.json()
            assert body["error"] == "RequestValidationError"
            assert "request_id" in body
            assert "name" not in str(body["detail"]), "422 detail must be sanitized, no field introspection leaked (R8)"

    def test_auth_rejects_missing_token(self):
        app = create_app("svc-auth2")
        install_auth(app, api_keys=["secret123"])
        client = pytest.importorskip("starlette.testclient")

        @app.get("/protected")
        async def protected():
            return {"ok": True}

        with client.TestClient(app) as c:
            r = c.get("/protected")
            assert r.status_code == 401
            r2 = c.get("/protected", headers={"authorization": "bearer secret123"})
            assert r2.status_code == 200

    def test_401_response_carries_request_id(self):
        app = create_app("svc-rid-auth")
        install_auth(app, api_keys=["secret123"])

        @app.get("/secret")
        async def secret():
            return {"ok": True}

        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/secret")
            assert r.status_code == 401
            assert r.headers.get("x-request-id"), "401 must carry request_id from outer middleware"

    def test_auth_rejects_near_miss_token(self):
        app = create_app("svc-nearmiss")
        install_auth(app, api_keys=["secret123"])

        @app.get("/protected")
        async def protected():
            return {"ok": True}

        client = pytest.importorskip("starlette.testclient")
        with client.TestClient(app) as c:
            r = c.get("/protected", headers={"authorization": "bearer secret124"})
            assert r.status_code == 401
            r2 = c.get("/protected", headers={"authorization": "bearer secret123"})
            assert r2.status_code == 200
