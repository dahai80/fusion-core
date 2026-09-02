from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

_current: contextvars.ContextVar[TenantContext | None] = contextvars.ContextVar("fusion_tenant_ctx", default=None)


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: str
    user_id: str | None = None
    role: str | None = None
    jti: str | None = None
    scopes: tuple[str, ...] = ()


class TenantContextError(Exception):
    pass


def current() -> TenantContext | None:
    return _current.get()


def set_context(ctx: TenantContext | None) -> contextvars.Token:
    return _current.set(ctx)


def reset(token: contextvars.Token) -> None:
    _current.reset(token)


def has_scope(scope: str) -> bool:
    ctx = _current.get()
    if ctx is None:
        return False
    return scope in ctx.scopes


def from_mapping(data: dict[str, Any]) -> TenantContext:
    tid = data.get("tid") or data.get("tenant")
    if not tid:
        raise TenantContextError("missing tid/tenant in mapping")
    scopes = data.get("scope") or data.get("scopes") or ()
    if isinstance(scopes, str):
        scopes = (scopes,)
    return TenantContext(
        tenant_id=str(tid),
        user_id=str(data["sub"]) if data.get("sub") is not None else None,
        role=data.get("role"),
        jti=data.get("jti"),
        scopes=tuple(scopes),
    )
