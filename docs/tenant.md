# tenant

L1 multi-tenant fabric. `TenantContext` (frozen-slots dataclass over `contextvars`) carries `tenant_id`/`user_id`/`role`/`jti`/`scopes` request-scoped; `TenantMiddleware` is a **fail-closed** ASGI middleware. Core stays pure-tech: this module owns the context plumbing + header/claims extraction, not token signing or signature verification (that lives in fusion-identity).

## Symbols

- [`TenantContext`](#tenantcontext)
- [Context helpers](#context-helpers)
- [`TenantMiddleware`](#tenantmiddleware)
- [JWT-less claims decode](#jwt-less-claims-decode)
- [FastAPI dependency](#fastapi-dependency)
- [Log injection](#log-injection)
- [Example](#example)
- [Design notes](#design-notes)

## TenantContext

```python
@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: str
    user_id: str | None = None
    role: str | None = None
    jti: str | None = None
    scopes: tuple[str, ...] = ()
```

Frozen-slots dataclass: immutable, memory-tight. One instance per request, bound to a `contextvars.ContextVar` (not thread-local) so it propagates correctly across `asyncio` tasks and survives structured-concurrency fan-out without leaking across requests.

| Field | Type | Source |
|-------|------|--------|
| `tenant_id` | `str` | `X-Tenant-Id` header (middleware) or `tid`/`tenant` claim (token) |
| `user_id` | `str \| None` | `X-User-Id` header or JWT `sub` |
| `role` | `str \| None` | JWT `role` claim |
| `jti` | `str \| None` | JWT `jti` (token id, for revocation lookup) |
| `scopes` | `tuple[str, ...]` | JWT `scope` (space-str) or `scopes` (array) |

`tenant_id` is mandatory — a context without a tenant is not a context (middleware rejects it 401 before binding).

## Context helpers

```python
def current() -> TenantContext | None
def set_context(ctx: TenantContext | None) -> contextvars.Token
def reset(token: contextvars.Token) -> None
def has_scope(scope: str) -> bool
def from_mapping(data: dict[str, Any]) -> TenantContext
```

| Helper | Behavior |
|--------|----------|
| `current()` | Returns the active `TenantContext`, or `None` if none bound. Safe to call outside a request (returns `None`). |
| `set_context(ctx)` | Binds `ctx` to the current async context; returns a `Token` for `reset()`. |
| `reset(token)` | Restores the prior context. **Always** called in a `finally` — never skip, or the context leaks into the next request on the same task. |
| `has_scope(scope)` | `True` if `scope` is in `current().scopes`; `False` if no context bound. |
| `from_mapping(data)` | Builds a `TenantContext` from a claims dict: reads `tid`/`tenant` (required → `TenantContextError`), `sub`→`user_id`, `role`, `jti`, `scope`/`scopes`. A `scope` string is coerced to a 1-tuple. |

`from_mapping` raises `TenantContextError("missing tid/tenant in mapping")` if neither `tid` nor `tenant` is present — fail visibly, no default tenant.

## TenantMiddleware

```python
class TenantMiddleware:
    def __init__(self, app, *, exempt_paths: frozenset[str] | None = None,
                 verify_jwt: Callable[[str], dict] | None = None,
                 require_jwt: bool = True) -> None
    async def __call__(self, scope, receive, send) -> None

def install_tenant_middleware(app, *, exempt_paths=None, verify_jwt=None,
                              require_jwt=True) -> None
```

Pure-ASGI middleware (no Starlette coupling at the signature — `scope`/`receive`/`send`). **Fail-closed**: every rejection returns HTTP 401 with a JSON body `{"error":"Unauthorized","detail":...,"request_id":...}`. It never lets a request through without a tenant.

### Request flow

1. Non-`http` scope (lifespan/websocket) → pass through unchanged.
2. Path in `exempt_paths` (default: `/health`, `/health/deep`, `/api/health`, `/docs`, `/redoc`, `/openapi.json`) → pass through. Paths are `rstrip`-normalized (`/health/` == `/health`).
3. Read `X-Tenant-Id`, `X-User-Id`, `Authorization: Bearer <token>`.
4. **Missing `X-Tenant-Id`** → 401 `missing X-Tenant-Id`.
5. Token present:
   - `verify_jwt` hook set → call it; exception → 401 `invalid token`. (Real signature verification happens here, in fusion-identity.)
   - no hook → `decode_jwt_claims(token)` (base64url + `exp` only); `TenantContextError` → 401 `invalid token`.
6. **JWT `tid`/`tenant` ≠ `X-Tenant-Id`** → 401 `tenant mismatch`. Prevents a token from tenant A impersonating tenant B.
7. No token and `require_jwt=True` → 401 `missing token`. Set `require_jwt=False` for header-only mode (internal mesh calls that carry `X-Tenant-Id` but no JWT).
8. Build `TenantContext`, `set_context()`, `await self._app(...)`, **`reset()` in `finally`** — no cross-request leak even on exception.

### install_tenant_middleware

Wraps `app.add_middleware(TenantMiddleware, ...)` + rebuilds the middleware stack + logs the install. Use this rather than constructing `TenantMiddleware` directly so the stack order is correct.

### verify_jwt hook

```python
VerifyJwt = Callable[[str], dict[str, Any]]
```

A `(token: str) -> claims: dict` callable. Inject the real verifier from **fusion-identity** (signature check, issuer/audience, key rotation). Core ships none — keeping signature verification out of core respects the "pure-tech, zero business" rule. Without a hook, `decode_jwt_claims` does **claims decode + `exp` check only, no signature verify** — suitable only for trusted upstreams that already verified the signature (e.g. behind a gateway that strips and re-signs).

## JWT-less claims decode

```python
def decode_jwt_claims(token: str, *, verify_exp: bool = True) -> dict[str, Any]
def tenant_context_from_token(token: str, *, verify_exp: bool = True) -> TenantContext
```

`decode_jwt_claims` splits the JWT into 3 segments, base64url-decodes the payload, `json.loads` it, and checks `exp` against `time.time()`. **No PyJWT dependency, no signature verification.** It only answers "what does this token claim?" — not "is this token authentic?". That is the `verify_jwt` hook's job.

| Failure | Exception |
|---------|-----------|
| empty token | `TenantContextError("empty token")` |
| not 3 segments | `TenantContextError("expected 3 jwt segments, got N")` |
| bad base64url | `TenantContextError("invalid base64url segment: ...")` |
| payload not JSON | `TenantContextError("jwt payload not json: ...")` |
| payload not object | `TenantContextError("jwt payload not an object")` |
| `exp` in past | `TenantContextError("jwt expired")` |
| `exp` unparseable | `TenantContextError("invalid exp claim: ...")` |

Set `verify_exp=False` to skip the expiry check (e.g. a gateway already validated and the token is a stripped inner form).

`tenant_context_from_token` is `decode_jwt_claims` + `from_mapping` in one step — decode claims, then build the `TenantContext`. Raises `TenantContextError` on any failure (missing `tid`/`tenant`, bad token, expired).

## FastAPI dependency

```python
def get_tenant_dep(request: Any) -> TenantContext
```

A FastAPI dependency (`Depends(get_tenant_dep)`) that returns the active `TenantContext`, mirrors it onto `request.state` (`tenant_id`/`user_id`/`role`) for middleware/handlers that read state directly, and falls back to a legacy `request.state.tenant_id` if no context is bound. If neither exists → `HTTPException(401, "missing tenant context")`.

```python
from fastapi import FastAPI, Depends
from fusion_core.tenant import install_tenant_middleware, get_tenant_dep, TenantContext

app = FastAPI()
install_tenant_middleware(app, verify_jwt=my_identity_verifier)


@app.get("/items")
async def items(ctx: TenantContext = Depends(get_tenant_dep)):
    return {"tenant": ctx.tenant_id, "user": ctx.user_id}
```

`get_tenant_dep` is imported guarded in `tenant/__init__`: if `fastapi` is absent, it is `None` (core stays importable without the `fastapi` extra).

## Log injection

The `logging` module's `_JsonFormatter` auto-injects tenant fields into every JSON log record — no manual `extra=` at call sites:

```python
from fusion_core.logging import _JsonFormatter  # configured via setup_logging(json=True)
```

On each record it calls `current()`; if a context is bound, the JSON payload gains `tenant_id` (always) and `user_id` (when not `None`). Outside a request (`current()` is `None`) the fields are simply absent — no noise, no `null` litter. This means every log line produced during a request is tenant-attributable for free, which is the whole point of binding the context at the middleware edge rather than passing `tenant_id` through every function signature.

## Example

```python
import logging
from fastapi import FastAPI, Depends
from fusion_core.tenant import (
    install_tenant_middleware,
    get_tenant_dep,
    TenantContext,
    current,
    has_scope,
)

log = logging.getLogger("my-svc")
app = FastAPI()


# inject the real signature verifier from fusion-identity; None = decode-only
def my_identity_verifier(token: str) -> dict:
    # verify signature, issuer, audience, key rotation ...
    return verified_claims


install_tenant_middleware(app, verify_jwt=my_identity_verifier)


@app.get("/items")
async def list_items(ctx: TenantContext = Depends(get_tenant_dep)):
    # ctx is the fail-closed-guaranteed tenant context; no manual header parsing
    log.info("listing items for tenant=%s user=%s", ctx.tenant_id, ctx.user_id)
    if not has_scope("items:read"):
        from fastapi import HTTPException

        raise HTTPException(403, "missing items:read scope")
    return {"tenant": ctx.tenant_id, "items": []}
```

Outside a framework (background tasks, tests), bind the context directly:

```python
from fusion_core.tenant import TenantContext, set_context, reset, current

ctx = TenantContext(tenant_id="acme", user_id="u1", scopes=("items:read",))
token = set_context(ctx)
try:
    assert current().tenant_id == "acme"
    assert has_scope("items:read")
finally:
    reset(token)  # never skip — or the next task on this loop sees "acme"
```

## Design notes

- **Fail-closed, not fail-open**: the middleware rejects (401) on every ambiguous case — missing header, token-tenant mismatch, missing bearer under `require_jwt`, bad token. The only way a request reaches the app is with a verified `TenantContext` bound (or via an explicit `exempt_paths` entry). A misconfiguration cannot silently widen access.
- **`contextvars`, not thread-local**: the context uses `contextvars.ContextVar`, so it flows correctly across `asyncio` tasks and survives `asyncio.gather` fan-out, yet is scoped to the request's context and reset in `finally` — no leak across requests reusing the same loop.
- **No PyJWT, no signature verify in core**: `decode_jwt_claims` does base64url + `exp` only. Real signature verification is the injected `verify_jwt` hook (lives in fusion-identity). This keeps core pure-tech: no crypto policy, no key management, no business of who-trusts-whom. A token without a `verify_jwt` hook is trusted **only** for claims extraction — deploy it behind an upstream that already verified the signature.
- **Tenant/header agreement**: JWT `tid`/`tenant` must equal `X-Tenant-Id` or the request is rejected. This blocks a tenant-A token attacking tenant-B's `X-Tenant-Id` header — the two must agree.
- **Log attribution for free**: `_JsonFormatter` reads `current()` so every in-request log line carries `tenant_id`/`user_id` without threading the values through every call. The middleware edge is the single bind point.
- **Import = no I/O, framework optional**: `import fusion_core.tenant` reads no env, opens no socket. `get_tenant_dep` is guarded — absent `fastapi` extra → `None`, core still imports. `TenantMiddleware` is pure-ASGI, usable with any ASGI server.
- **Trilingual mirror anchor**: this is the Python variant of the tenant fabric. Rust and Swift variants land in their own repos (fusion-cli / fusion-studio), mirroring the same `TenantContext` shape and fail-closed contract.
