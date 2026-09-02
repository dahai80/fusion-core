from __future__ import annotations

from fusion_core.tenant.context import (
    TenantContext,
    TenantContextError,
    current,
    from_mapping,
    has_scope,
    reset,
    set_context,
)
from fusion_core.tenant.jwt_utils import (
    decode_jwt_claims,
    tenant_context_from_token,
)
from fusion_core.tenant.middleware import (
    TenantMiddleware,
    install_tenant_middleware,
)

try:
    from fusion_core.tenant.deps import get_tenant_dep  # noqa: F401
except ImportError:
    get_tenant_dep = None

__all__ = [
    "TenantContext",
    "TenantContextError",
    "current",
    "set_context",
    "reset",
    "from_mapping",
    "has_scope",
    "decode_jwt_claims",
    "tenant_context_from_token",
    "TenantMiddleware",
    "install_tenant_middleware",
    "get_tenant_dep",
]
