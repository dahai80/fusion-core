---
doc_id: fusion-core/recipe-tenant-fastapi
product: fusion-core
class: platform
entry: developer
doc_type: recipe
title: {zh: "Recipe：给 FastAPI 服务装上鉴权与租户中间件", en: "Recipe: add auth and tenant middleware to a FastAPI service"}
audience: engineer
status: draft
version: 0.10.1
updated: 2026-11-07
owner: @dahai
source_of_truth:
  - fusion_core/http/app.py
  - fusion_core/tenant/
  - docs/api-reference.md
---

# Recipe：鉴权 + 租户中间件 / Auth + tenant middleware on FastAPI

场景：你的服务要暴露 HTTP，需要 401 鉴权、健康探针和请求级租户上下文，但不想自己写中间件排序。

## 步骤 / Steps

```python
from fastapi import Depends
from fusion_core import client_key
from fusion_core.http import create_app, install_auth
from fusion_core.tenant import get_tenant_dep, install_tenant_middleware, TenantContext

app = create_app("my-service")                       # /health liveness mounted
install_auth(app, api_keys=[client_key()])           # resolves FUSION_CLIENT_KEY at call time
install_tenant_middleware(app, require_jwt=False)    # fail-closed on X-Tenant-Id

@app.get("/items")
async def list_items(ctx: TenantContext = Depends(get_tenant_dep)):
    return {"tenant": ctx.tenant_id}
```

请求头约定：

```text
X-Tenant-Id: tenant-a
Authorization: Bearer <jwt>     # required only when require_jwt=True
```

## 确认点 / Verify

- 缺 `X-Tenant-Id` 的请求得到 401，且响应体携带与请求相同的 request_id（中间件排序已由 `install_auth` 重排）。
- 携带 `X-Tenant-Id: tenant-a` 但 JWT `tid` 为 `tenant-b`：401。
- `GET /health` 无需鉴权即 200。

## 约束 / Constraints

- `tenant.jwt_utils` 只做 base64url 解码 + `exp` 检查，不验签。生产必须注入 `verify_jwt` 钩子（同步或异步均可，awaitable 返回会被等待）——fusion-identity 提供实现。
- `TenantContext.authenticated` 为 `True` 仅当验签后的 JWT 产出了 claims；匿名上下文不要用 `role`/`scopes` 反推。
- 中间件是 fail-closed 的：白名单路径按 `rstrip('/')` 归一化，除此之外一律要求租户头。
