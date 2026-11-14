---
doc_id: fusion-core/recipe-config-resolution
product: fusion-core
class: platform
entry: developer
doc_type: recipe
title: {zh: "Recipe：新服务接入配置单点解析", en: "Recipe: wire a new service to config single-point resolution"}
audience: engineer
status: draft
version: 0.12.1
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - fusion_core/config.py
  - docs/config-reference.md
---

# Recipe：新服务接入配置单点解析 / Wire a new service to config single-point resolution

场景：你在写一个新的 fusion 领域服务，需要 key、端口、数据目录。不要再写自己的 env 回退链——`fusion_core.config` 已是 `architecture/config-conventions.md` 的单点实现。

## 步骤 / Steps

```python
import logging

from fusion_core import client_key, admin_key, service_port, service_data_dir, setup_logging

log = logging.getLogger(__name__)

setup_logging("my-service")

api_key = client_key()  # FUSION_CLIENT_KEY -> FUSION_MLX_API_KEY -> MLX_API_KEY (warns)
admin = admin_key()  # FUSION_ADMIN_KEY only, no aliases
port = service_port("my-service", default=11500)  # reads FUSION_MY_SERVICE_PORT
data_dir = service_data_dir("my-service", default="/tmp/my-service")
```

```bash
export FUSION_CLIENT_KEY=...      # real value comes from the gateway admin API
export FUSION_ADMIN_KEY=...
export FUSION_MY_SERVICE_PORT=11500
```

## 确认点 / Verify

- `service_port("fusion-my-service", ...)` 与 `service_port("my-service", ...)` 结果一致（`fusion-` 前缀自动剥离）。
- dev 语境下不设 key：首次调用 warn 一次（anonymous dev mode），返回空串。
- `FUSION_PROFILE=prod` 语境下不设 key：`RuntimeError`，这是预期失败路径，不要 try/except 掉。

## 约束 / Constraints

- 旧名 `MLX_API_KEY` / `FUSION_MLX_API_KEY` 命中即 warn——新代码一律声明 `FUSION_CLIENT_KEY`。
- 端口的注册权威在 monorepo `architecture/port-registry.yaml`；新端口先注册再用。
- 不要把 key 值写进日志；解析器日志只输出变量名。
