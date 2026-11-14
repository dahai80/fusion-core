---
doc_id: fusion-core/architecture
product: fusion-core
class: platform
entry: developer
doc_type: architecture
title: {zh: "Fusion-Core 架构", en: "Fusion-Core Architecture"}
audience: engineer
status: draft
version: 0.12.1
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - fusion_core/__init__.py
  - README.md
---

# Fusion-Core 架构 / Architecture

## 定位 / Position

fusion-core 是 monorepo 20+ Python 领域项目共享的技术底座（in-tree，`pip install -e fusion-core`）。它上游只依赖 httpx，下游被所有领域应用 import；不与任何业务绑定，也不自带服务进程。关系图见 monorepo 根仓库的 `architecture/PROJECT_RELATIONSHIPS.md`（fusion 主仓，不在本仓库内）。

## 模块分层 / Layers

```text
            domain projects (fusion-rag, fusion-trainer, ...)
                              | import
  +---------------------------+-----------------------------+
  | governance          | service skeleton        | primitives
  | guard_client        | http (create_app,       | mlx_client
  | mcp_registry        |   install_auth)         | parse
  | mcp_client          | tenant (TenantMiddleware)| config
  | proc                |                         | logging
  +---------------------+-------------------------+-----------+
  | infra: http_client (pool + retry, shared by mlx_client)   |
  |        prompt, prompt_registry, dsl_schema                |
  +-----------------------------------------------------------+
                              | HTTP / UDS
        fusion-mlx :11434  |  fusion-gateway :11432  |  fusion-guard UDS
```

- **primitives**（mlx_client / parse / config / logging）：无依赖叶子，任何项目都可直接用。
- **infra**（http_client）：连接池按 `(event-loop id, base_url, timeout)` 键隔离，LRU 上限 8，只驱逐同 loop 键；重试常量 `RETRY_STATUS` / `RETRY_EXCEPTIONS` 是全仓唯一来源，mlx_client 不自带重试。
- **service skeleton**（http / tenant）：FastAPI 工厂与请求级租户上下文，middleware 为纯 ASGI 实现，`user_middleware` 重排保证 request_id 最外层。
- **governance**（guard_client / mcp_registry / mcp_client）：与 fusion-guard 的 UDS JSON-RPC 客户端、MCP server 审批注册表；只做声明与判定，不做强制。

## 关键设计决策 / Key decisions

1. **import 零 I/O**：`import fusion_core` 不读 env、不读文件、不开连接。所有惰性解析在函数调用时发生（config / guard_client / mcp_registry 都是 lazy-at-call）。jsonschema、setproctitle 等重依赖在函数内 lazy import，保持核心安装最小。
2. **失败可见**：parse 无静默 fallback（`parse_llm_json` 抛、`parse_llm_json_safe` 强制显式 default）；prod 缺 key 抛 `RuntimeError`；注册表损坏抛 `RegistryIntegrityError`；流失败统一 `StreamError`。
3. **重试单点**：`http_client.with_retry` 是唯一的重试实现；full jitter、`total_deadline` 端到端预算、`verify_gateway=` 把重试让渡给网关熔断器（`gateway_circuit_breaker_ok` 探测 `/readyz`）。
4. **中间件纯 ASGI + 明确排序**：`install_auth` 重排 `user_middleware` 使 request_id 最外层，401 携带同一 request_id；SSE 不被缓冲截断。
5. **fail-closed 租户**：`TenantMiddleware` 对缺失/不匹配的租户声明返回 401，`contextvars` 在 `finally` 中 reset；JWT 只解码不验签，验签通过 `verify_jwt` 钩子注入（fusion-identity 是 HTTP 依赖，不是 import）。
6. **写时复制快照**：prompt_registry 用内容寻址 `version_id`（sha256 16-hex），快照 write-once、读时校验哈希；存储是纯文件，无新依赖。

## 数据流 / Data flow（一次典型 chat）

```text
domain code
  -> FusionMLXClient.chat(messages, total_deadline=...)
     -> http_client.get_async_client(base_url, timeout)   # pooled, per-loop
     -> with_retry(...)                                    # jitter + deadline
        -> POST {base_url}/v1/chat/completions             # fusion-mlx :11434 or gateway :11432
  -> LLMResponse
  -> parse_llm_json(resp.content)                          # visible failure
```

## 版本与兼容 / Versioning

- Python >= 3.12（monorepo 统一 `.python-version`）。
- httpx 上界 `<1.0`（防大版本破坏 AsyncClient / MockTransport 用法）。
- 语义化版本；行为变更记录在根 `CHANGELOG.md`。
