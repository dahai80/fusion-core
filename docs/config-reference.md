---
doc_id: fusion-core/config-reference
product: fusion-core
class: platform
entry: developer
doc_type: config-reference
title: {zh: "Fusion-Core 配置参考", en: "Fusion-Core Config Reference"}
audience: engineer
status: draft
version: 0.12.1
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - fusion_core/config.py
  - fusion_core/mlx_client.py
  - fusion_core/guard_client.py
  - fusion_core/mcp_registry.py
---

# Fusion-Core 配置参考 / Config Reference

fusion-core 没有配置文件，只有环境变量 + 一个共享的 settings.json（`~/.fusion-mlx/settings.json`，由 fusion-mlx 维护，本库只读）。所有解析在调用时惰性发生，import 不读环境。

## Settings 文件

`load_settings(path=None)` — 默认 `~/.fusion-mlx/settings.json`。缓存按文件 mtime 失效：文件被编辑后下次调用即重新加载，无需重启。`clear_cache()` 强制全量失效。使用的字段：

- `auth.api_key` — `resolve_api_key` 的 settings 侧来源
- `shell.gateway_admin_key` — `gateway_admin_key()` 的 settings 侧来源

## 环境变量

| Env var | Consumer | Effect |
|---|---|---|
| `FUSION_MLX_URL` | `default_mlx_base_url()` / `create_async_client(base_url=None)` | LLM endpoint; default `http://localhost:11434`. Point at `http://127.0.0.1:11432` to route through fusion-gateway |
| `FUSION_CLIENT_KEY` | `client_key()` | First choice for the client-facing key |
| `FUSION_MLX_API_KEY` | `client_key()` | Second choice |
| `MLX_API_KEY` | `client_key()` | Legacy name — resolves but warns; do not use in new code |
| `FUSION_ADMIN_KEY` | `admin_key()` | Admin key; no aliases by design |
| `FUSION_GATEWAY_API_KEY` | `gateway_admin_key()` | Key the gateway actually accepts; falls back to settings `shell.gateway_admin_key` |
| `FUSION_PROFILE` | `profile()` | Unset resolves to `dev`; with `prod`, secret resolvers raise `RuntimeError` on missing keys instead of warning |
| `FUSION_<APP>_PORT` | `service_port(app, default)` | App name prefix `fusion-` is stripped — `"fusion-rag"` and `"rag"` resolve identically |
| `FUSION_<APP>_DATA_DIR` | `service_data_dir(app, default)` | Same prefix-stripping rule |
| `FUSION_GUARD_SOCK` | `guard_client` | UDS path for fusion-guard; default `/tmp/fusion-guard.sock`, resolved lazily at call time |
| `FUSION_MCP_REGISTRY` | `mcp_registry.default_registry_path()` | Registry JSON path; fallback order: env → monorepo sidecar `architecture/mcp-server-registry.json` → bundled `fusion_core/mcp-server-registry.json` |

## 解析顺序（key 类）

`client_key()`: `FUSION_CLIENT_KEY` → `FUSION_MLX_API_KEY` → `MLX_API_KEY`（后两个是旧名，命中即 warn）；都缺失时 dev 语境 warn 一次并返回空串，prod 语境抛 `RuntimeError`。`admin_key()` 只读 `FUSION_ADMIN_KEY`，无别名，同样的 prod 抛错语义。`gateway_admin_key()`: `FUSION_GATEWAY_API_KEY` → settings `shell.gateway_admin_key`（为 null 时 warn）。

## 约定

- prod 语境下 key 缺失直接抛 `RuntimeError`——配置错误必须可见，不静默降级。
- `service_port` / `service_data_dir` 的 default 参数由调用方提供；库不猜端口。端口注册的权威文件是 monorepo 的 `architecture/port-registry.yaml`。
- 所有 key 的日志输出只出现变量名，不出现值。
