---
doc_id: fusion-core/product-page
product: fusion-core
class: platform
entry: developer
doc_type: product-page
title: {zh: "Fusion-Core 产品页", en: "Fusion-Core Product Page"}
audience: engineer
status: draft
version: 0.12.0
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - README.md
  - pyproject.toml
  - fusion_core/__init__.py
---

# Fusion-Core 产品页 / Fusion-Core Product Page

Fusion-Core 是 Fusion 生态的公共技术底座：LLM 客户端、JSON 解析、配置解析、日志、httpx 连接池与重试、FastAPI 工厂、Prompt 管理、guard/MCP 治理客户端。纯技术、零业务逻辑，`import fusion_core` 不触发任何 I/O。运行时依赖只有 httpx。它不是一个服务——没有自己的端口，通过 `pip install -e fusion-core` 被 20+ 个 Python 领域项目 import。

Fusion-Core is the shared technical foundation of the Fusion ecosystem: LLM client, JSON parsing, config resolution, logging, httpx connection pool with retry, FastAPI factory, prompt management, and guard/MCP governance clients. Pure tech, zero business logic; `import fusion_core` triggers no I/O. The only runtime dependency is httpx. It is not a service — it has no port of its own and is imported by 20+ Python domain projects via `pip install -e fusion-core`.

## Platform block

```yaml
platform:
  serves:            # 谁在消费 / who consumes it
    - 20+ Python domain projects in the monorepo (fusion-rag, fusion-trainer, fusion-bench, fusion-k12-teacher, fusion-code-modelization, ...)
    - any Python code that needs an LLM HTTP client, JSON parsing, config keys, or a FastAPI skeleton
  builds_on:         # 依赖的底座 / underlying dependencies
    - httpx (only runtime dependency, upper bound <1.0)
    - optional extras: fastapi/uvicorn/pydantic ([fastapi]), jsonschema ([schema]), setproctitle ([proc])
  hosts:             # 托管的状态 / state it owns
    - none at rest — the library owns only in-process state: client pool, settings cache, prompt cache, metrics counters
  integration_surface: # 接入面 / external contract
    - Python import (`from fusion_core import ...`), shipped via pip install -e
    - environment variables resolved at call time (FUSION_MLX_URL, FUSION_CLIENT_KEY, FUSION_GUARD_SOCK, ...)
    - no HTTP endpoints, no sockets opened at import time
  extension_points:  # 二次开发挂点 / where to extend
    - tenant jwt_utils `verify_jwt` hook — inject a real signature verifier (fusion-identity provides one)
    - http_client `set_metrics_callback` — feed pool/retry counters into your own /metrics
    - `create_app(readiness_probe=)` — supply a service-specific readiness probe to the shared FastAPI factory
    - `FUSION_MCP_REGISTRY` — point mcp_registry at your own registry JSON
```

## 核心能力 / Core capabilities

### 1. 统一 LLM 客户端 / Unified LLM client

应用方不再自己写：httpx 客户端管理、重试与退避、流式 SSE 解析、chat/embedding 响应模型。`FusionMLXClient` / `create_async_client()` 覆盖 chat、embedding、stream、health、server stats；重试统一走 `http_client.with_retry`（full jitter、`total_deadline` 端到端预算、网关熔断让渡）。约束：默认 base_url `localhost:11434`，多节点须经 fusion-gateway，用 `FUSION_MLX_URL` 指向 `:11432`；流式失败统一抛 `StreamError(delivered=, resume_offset=)`，非重试 4xx 仍抛原始 `HTTPStatusError`。

What consumers no longer write: httpx client lifecycle, retry/backoff, SSE stream parsing, chat/embedding response models. Constraints: default base_url is `localhost:11434` — for multi-node point `FUSION_MLX_URL` at the fusion-gateway `:11432`; all stream-failure paths raise `StreamError` (one type, with `delivered`/`resume_offset`); non-retriable 4xx raise the original `HTTPStatusError`.

### 2. JSON 解析 / JSON parsing from LLM output

应用方不再自己写：从 LLM 输出里抠 JSON 的正则与 try/except 链。`parse_llm_json` 失败即抛 `ParseError`（无静默 fallback）；`parse_llm_json_safe` 强制显式 `default`；`parse_llm_json_lenient` 用 `raw_decode` 提取首个对象（扫描上限 200k 字符）。约束：失败必须可见——库不提供"解析失败返回 None"的默认路径，静默兜底要自己写且必须显式。

What consumers no longer write: regex and try/except chains for extracting JSON from LLM text. Constraints: failures are visible — `parse_llm_json` raises `ParseError` with no fallback, `parse_llm_json_safe` requires an explicit `default`; the lenient variant caps scanning at 200k characters.

### 3. 配置单点解析 / Config single-point resolution

应用方不再自己写：api key 的多级 env 回退、profile 校验、端口/数据目录约定。`client_key()` / `admin_key()` / `gateway_admin_key()` / `profile()` / `service_port()` / `service_data_dir()` 是 `architecture/config-conventions.md` 的单点实现（issue #31/#32/#34/#35/#36）。约束：旧环境变量名（`MLX_API_KEY` 等）会 warn；`FUSION_PROFILE=dev` 只允许开发环境（prod 直接 `RuntimeError`）；settings 文件 mtime 变化即失效缓存。

What consumers no longer write: env-var fallback ladders for API keys, profile checks, port/data-dir conventions. Constraints: legacy env names warn at resolve time; `FUSION_PROFILE=dev` raises `RuntimeError` in prod contexts; the settings cache invalidates on file mtime change.

### 4. FastAPI 工厂与租户中间件 / FastAPI factory + tenant middleware

应用方不再自己写：FastAPI 骨架、request_id 中间件排序、鉴权 401 包装、liveness/readiness 双探针、请求级租户上下文绑定。`create_app()` / `install_auth()` / `standard_error_handler` 加 `TenantMiddleware`（缺 `X-Tenant-Id` 或 JWT `tid` 不匹配 → 401，fail-closed，`finally` 中 reset 无跨请求泄漏）。约束：jwt_utils 只做 base64url 解码 + `exp` 检查，**不验签**——真实验签通过 `verify_jwt` 钩子注入（fusion-identity 提供），传 awaitable 不会阻塞事件循环；SSE 响应不会被中间件截断。

What consumers no longer write: FastAPI app skeleton, middleware ordering, 401 wrapping, liveness/readiness probes, request-scoped tenant binding. Constraints: `jwt_utils` decodes and checks `exp` but never verifies signatures — inject a `verify_jwt` hook (fusion-identity provides one); `TenantMiddleware` is fail-closed on missing/mismatched tenant headers.

### 5. 治理客户端 / Governance clients (guard + MCP)

应用方不再自己写：与 fusion-guard 的 UDS JSON-RPC 对接、MCP server 注册表解析与审批判定。`FusionGuardClient` 覆盖 `guard.evaluate` / `guard.redact` / `guard.confirm` / `guard.tcc.*` 等 9 个方法，block 判定是结果不是异常；`mcp_registry.check_approval()` 返回 longest-prefix 审批判定，`McpToolClient` 负责 stdio 调用。约束：guard 默认 socket `/tmp/fusion-guard.sock`（`FUSION_GUARD_SOCK` 覆盖），2s 默认超时；mcp_registry 只做声明（无凭据存储、无网络调用、不强制 warn/block——由调用方决定）；`isError` 是结果不是错误。

What consumers no longer write: UDS JSON-RPC wiring to fusion-guard, MCP registry parsing and approval checks. Constraints: guard default socket `/tmp/fusion-guard.sock` (`FUSION_GUARD_SOCK`), 2s default timeout, block verdicts are results not errors; mcp_registry stores no credentials, makes no network calls, and does not enforce warn-vs-block — callers decide; MCP tool `isError` is a result, not an exception.

## 边界 / Non-goals

- 不做推理：模型推理在 fusion-mlx（`:11434`），本库只是 HTTP 客户端。
- 不提供服务端口：没有 server、没有 `start.sh`；需要服务形态的请看各领域项目。
- 不做业务逻辑：不带任何领域 prompt、语料或业务规则。

No inference here (that is fusion-mlx; this repo is only an HTTP client), no service endpoint of its own (no server, no start.sh), no business logic (no domain prompts, corpora, or rules).
