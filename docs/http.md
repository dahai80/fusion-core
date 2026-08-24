# http

FastAPI app factory + pure-ASGI middleware. SSE-safe, request_id outermost, auth keys encapsulated, 422/500 sanitized equally.

> Requires the `fastapi` extra: `pip install -e "fusion-core[fastapi]"`. If fastapi isn't installed, `from fusion_core.http import ...` is skipped silently (logged debug).

## Symbols

- [`create_app(name, *, cors_origins, cors_credentials, version, enable_health)`](#create_app)
- [`install_auth(app, *, api_keys)`](#install_auth)
- [`standard_error_handler(request, exc)`](#standard_error_handler)

## create_app

```python
def create_app(
    name: str,
    *,
    cors_origins: list[str] | None = None,
    cors_credentials: bool = False,
    version: str = "0.1.0",
    enable_health: bool = True,
) -> FastAPI
```

Builds a `FastAPI(title=name, version=version)`.

- `cors_origins` — if set, adds `CORSMiddleware`. `cors_credentials=True` with `"*"` in origins → `ValueError` (CORS Fetch spec 3.2 forbids credentials with wildcard). `cors_credentials` defaults `False`.
- `enable_health=True` — registers `GET /health` → `{"status":"ok","service":name,"version":version}`.
- Registers `standard_error_handler` for `Exception` (500 path) and `validation_error_handler` for `RequestValidationError` (422 path) — both sanitized.
- Adds `_RequestIdASGIMiddleware` as a middleware (pure ASGI, no `BaseHTTPMiddleware` buffering). Its final outermost position is enforced by `install_auth` (see below) — `create_app` alone leaves request_id innermost if `install_auth` is never called.

```python
from fusion_core.http import create_app, install_auth
app = create_app("my-svc", cors_origins=["https://example.com"], cors_credentials=True)
install_auth(app, api_keys=["secret"])
```

## install_auth

```python
def install_auth(app: FastAPI, *, api_keys: list[str] | None = None) -> None
```

Adds `_AuthASGIMiddleware` with bearer-token auth.

- `api_keys=None` → resolves via `resolve_api_key()`; if none resolvable → `KeyError` (refuses to start unauthenticated — fail visibly).
- Keys are stored in the middleware instance's private `_keys_list` (I2), **not** on `app.state` — no `app.state._fusion_auth_keys` for third-party middleware to read.
- Whitelist (`_UNAUTH_PATHS = {"/health","/docs","/openapi.json","/redoc"}`) is `rstrip`-normalized (R7): `/health/` matches `/health`, no spurious 401.
- Token check uses `hmac.compare_digest` (constant-time). Failure → 401 JSON `{"error":"Unauthorized","detail":"invalid or missing api key","request_id":...}`, carrying the request_id (so even auth failures stay correlated).
- **Middleware order enforcement** (H1/E1): `install_auth` adds `_AuthASGIMiddleware`, then removes the existing `_RequestIdASGIMiddleware` entry from `app.user_middleware` and re-adds it **last**. Since `add_middleware` inserts at the front (last added = outermost), this puts request_id outside auth — so 401 responses carry the **same** id the request_id middleware assigned, not a divergent uuid fallback the auth layer would generate. `app.middleware_stack = None` + `app.build_middleware_stack()` force the reorder to take effect even if the stack was already built (e.g. after `TestClient` startup). Regression guard: `test_401_request_id_matches_client_sent_id` asserts a client-sent `x-request-id` is echoed verbatim on the 401.

## standard_error_handler / validation_error_handler

```python
def standard_error_handler(request: Request, exc: Exception) -> JSONResponse       # 500 path
def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse  # 422 path
```

Both emit a `request_id` (from `request.state`, or a fresh uuid) and sanitize detail:

- `standard_error_handler`: `HTTPException` → preserves `exc.detail`; other `Exception` → 500 with `detail="internal error; see server logs with request_id"` (no traceback/inner type leaked, R8).
- `validation_error_handler` (R8): 422 with `detail="request body validation failed; see server logs with request_id"` — full Pydantic errors go to **server logs only**, not the response body (no schema field-name / input-value leakage).

## Middleware internals (pure ASGI)

### _RequestIdASGIMiddleware

Outermost. Reads `x-request-id` header (or generates uuid4), stores on `scope["state"]["request_id"]`. Wraps `send` to inject `x-request-id` and `x-process-ms` into `http.response.start` headers. Pure ASGI (A5): works with `StreamingResponse`/SSE — no `BaseHTTPMiddleware` buffering/truncation, headers land on streamed responses.

### _AuthASGIMiddleware

Inner layer (request_id is outermost after `install_auth` re-orders). `rstrip`-normalizes path, short-circuits whitelist, validates bearer token, emits 401 on failure. Pure ASGI so it composes under the request_id layer. The 401 response reads `scope["state"]["request_id"]` (set by the outer request_id middleware) and does **not** add its own `x-request-id` header — the outer `send_wrapper` injects it, avoiding a duplicated header.

## Design notes

- Pure ASGI replaces `BaseHTTPMiddleware` (A5): SSE chunk streaming not buffered/truncated; response headers injectable on stream start.
- Middleware order fixed by `install_auth` (H1/E1): it re-orders `app.user_middleware` so request_id sits outside auth and 401s carry the same id. The `create_app`-only path (no `install_auth`) leaves request_id as the sole middleware — still correct, just no auth layer to be outside of.
- Auth keys encapsulated in middleware instance (I2): not reachable via `app.state`.
- 422 sanitized equal to 500 (R8); whitelist `rstrip`-normalized (R7).
- CORS `*`+credentials rejected at factory (PRD §7.1 acceptance).
