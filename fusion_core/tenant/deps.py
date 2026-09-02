from __future__ import annotations

from typing import Any

from fusion_core.tenant.context import TenantContext, current


def get_tenant_dep(request: Any) -> TenantContext:
    ctx = current()
    if ctx is not None:
        request.state.tenant_id = ctx.tenant_id
        if ctx.user_id is not None:
            request.state.user_id = ctx.user_id
        if ctx.role is not None:
            request.state.role = ctx.role
        return ctx
    legacy = getattr(request.state, "tenant_id", None)
    if legacy is not None:
        return TenantContext(tenant_id=str(legacy))
    from fastapi import HTTPException

    raise HTTPException(status_code=401, detail="missing tenant context")
