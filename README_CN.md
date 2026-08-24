# fusion-core

> Fusion 生态公共技术底座。纯技术，零业务逻辑。
>
> [English](README.md)

fusion-core 是 Fusion 生态（"一核九端"）20+ Python 领域项目共享的公共技术底座。它消除 7+ 套重复 LLM 客户端与 10+ 份 `_parse_json`，为每个原语提供一份经过测试的实现：LLM 客户端、JSON 解析、配置加载、日志、httpx 连接池+重试、FastAPI 工厂、prompt 模板管理。

不依赖任何 Fusion 专属内容。`import fusion_core` 不触发任何 I/O（不读 env、不读文件、不建连接）——任何地方安全导入。

## 安装

```bash
pip install -e fusion-core            # 基础（仅 httpx，核心依赖零业务、零 pydantic）
pip install -e "fusion-core[test]"    # 加测试栈（pytest、ruff、fastapi）
pip install -e "fusion-core[fastapi]" # 加 fastapi、uvicorn、pydantic
```

要求：Python >=3.12。

## 7 模块速查

| 模块 | 关键符号 | 用途 |
|------|----------|------|
| `mlx_client` | `FusionMLXClient`、`create_async_client(*, backend=)`、`LLMResponse`、`EmbeddingResponse`、`ServerStats`、`StreamError` | 统一 MLX 推理客户端（chat / embedding / stream）。默认 base_url `localhost:11434`（运行时解析 `FUSION_MLX_URL`，指向 fusion-gateway 即多节点）。重试下沉 `http_client`。`chat(total_deadline=)` 为显式命名参数（R5）传端到端预算；`**kwargs` 白名单透传（`top_p`/`seed` 等）；`stream_chat` **所有**流失败路径抛 `StreamError(delivered=, resume_offset=)`（中断 severed 或 可重试耗尽无输出），调用方只认一种流失败类型（H4/R4）；不可重试 4xx 仍抛原 `HTTPStatusError`；`create_async_client(model=...)` 记默认 model；`health()` 复用 `probe_client` + 1s 节流不泄漏主连接；`get_server_stats()` 返回 `ServerStats` dataclass |
| `parse` | `parse_llm_json`（抛 `ParseError` 不兜底）、`parse_llm_json_safe`（显式默认，default 必传 dict/list）、`parse_llm_json_lenient`（`raw_decode` 提取首个对象，扫描上限 200k）、`strip_code_fence` | LLM 输出 JSON 解析，**失败可见不静默** |
| `config` | `load_settings`（mtime 失效缓存）、`resolve_api_key`、`load_api_key`、`get_env`、`default_mlx_base_url`、`clear_cache` | 配置懒加载 + api_key 解析 + 缓存失效（settings 文件 mtime 变即失效） |
| `logging` | `setup_logging`、`get_logger` | 幂等日志初始化（每次 setLevel 生效，`propagate` 默认 True 不阻断 host root，区分包级 `NullHandler`），JSON 格式可选 |
| `http_client` | `get_async_client`（per-loop 连接池，`OrderedDict` LRU 上限 8，驱逐只动同 loop 键）、`gateway_circuit_breaker_ok`（探活 gateway `/readyz`，H3/E4）、`with_retry`（full jitter，`disable=` + `verify_gateway=` 安全关重试交 gateway 熔断，`total_deadline=` 总预算；耗尽抛 `RetryExhaustedError`/`RetryTimeoutError`）、`close_all`、`close_all_sync`、`set_metrics_callback`、`get_metrics_snapshot`、`reset_metrics` | httpx async 客户端池 + 重试（重试码/异常单一来源 `RETRY_STATUS`/`RETRY_EXCEPTIONS`） |
| `http` | `create_app`、`install_auth`、`standard_error_handler` | FastAPI 应用工厂 + 纯 ASGI 中间件（`install_auth` 重排 `user_middleware` 使 request_id 最外层——401 带同一 id，H1/E1；SSE 不截断；认证密钥封进中间件实例不落 `app.state`；422/500 同等脱敏；白名单路径 `rstrip` 规范化） |
| `prompt` | `PromptManager` | prompt 模板管理（只管引擎不含领域内容，缺失目录直接抛 `FileNotFoundError`；**mtime 闸门缓存**——运行期改盘即生效（mtime 变即失效重读），`clear_cache()` 强制全刷新，E3） |

## 用法

### LLM 对话 + JSON 解析

```python
from fusion_core import create_async_client, parse_llm_json, get_logger

log = get_logger(__name__)

client = create_async_client(
    base_url="http://localhost:11434",
    api_key="...",
    model="qwen2.5-7b",
)
resp = await client.chat(messages=[{"role": "user", "content": "返回 JSON"}])
data = parse_llm_json(resp.content)  # 非法 JSON 抛 ParseError，不静默返回 {}
```

### 流式（带中途失败恢复信封）

```python
collected = []
try:
    async for chunk in client.stream_chat(messages=[...]):
        collected.append(chunk)
except StreamError as e:
    # 所有流失败路径都抛 StreamError（H4/R4）：中断 severed（e.delivered > 0）
    # 或 可重试耗尽无输出（e.delivered == 0）。不可重试 4xx 抛 HTTPStatusError（坏请求非中断）。
    log.warning("流式中断，已交付 %d 字符，续传偏移 %d", e.delivered, e.resume_offset)
```

### 端到端 deadline

```python
# total_deadline 限整个重试预算，不只单请求
resp = await client.chat(messages=[...], total_deadline=30.0)
```

### 关重试交 fusion-gateway（避免双重重试）

```python
from fusion_core import with_retry

# 当 fusion-gateway 熔断器接管重试时，关掉 core 自身重试。
# verify_gateway=True 先探活 gateway /readyz；熔断开或 gateway 不可达，
# core 回退自身重试（H3/E4——不留能力真空）。
resp = await with_retry(fn, disable=True, verify_gateway=True)
```

### FastAPI 工厂

```python
from fusion_core.http import create_app, install_auth

# cors_credentials 默认 False；"*" + credentials=True 抛 ValueError
app = create_app("my-svc", cors_origins=["https://example.com"], cors_credentials=True)
install_auth(app, api_keys=["secret"])  # request_id 自动为最外层中间件，401 也带 id
```

### 嵌入

```python
resp = await client.embed("hello world", model="bge-m3")
print(resp.vector)  # 单输入 → .vector
batch = await client.embed(["a", "b"], model="bge-m3")
print(batch.vectors)  # 批量输入 → .vectors 列表
```

## 设计原则

- **纯技术零业务**：不含 K12 评分/金融阈值/医疗禁忌/DAG 节点等领域逻辑。边界模糊默认不抽入。
- **非侵入可独立**：fusion-core 可独立 `import` 不依赖任何 fusion-* 项目。
- **失败可见不兜底**：`parse_llm_json` 抛错而非返回空 dict；client 失败抛异常而非返回空 content；`get_server_stats` 失败抛异常不返回 `{}`。
- **测试可隔离**：`-m 'not integration'` 跳过真实引擎测试；集成 fixture 记录 `was_running`，仅停自己启动的引擎。

## 边界声明 —— 集群能力归 fusion-gateway

fusion-core 是**单进程单引擎客户端库**，不是集群治理面。PRD §0.2 四铁律（纯技术零业务 / 非侵入可独立 / 失败可见不兜底 / 测试可隔离）划定边界。下列集群级能力**已在 fusion-gateway（Go，:11432）实现并上线，core 不重建**（重建即重复 + 违反"纯技术零业务"）。core 侧只做"避免与 gateway 行为冲突"的最小修复。

| 能力 | gateway 实现 | core 侧动作 |
|------|-------------|-----------|
| 端点注册表 / 路由 / 故障转移 | `discovery`（节点注册/健康/驱逐）+ `router/engine` | `default_mlx_base_url()` 读 `FUSION_MLX_URL`，指向 gateway 即多节点 |
| 熔断器 | `router/circuit_breaker.go:CircuitBreaker` | `with_retry(disable=True)` 关 core 重试，交 gateway 熔断，避免双重重试 |
| 每端点并发度闸门 | `router/engine.go:MaxConcurrent` | core 连接池不叠加并发上限 |
| 模型注册表 model→endpoint | `router` 按 model 路由 | 调用方传 model，gateway 解析端点，core 不持拓扑 |
| 指标埋点 Prometheus | `observability/metrics`（circuitBreakerState/Trips、routeDecisions、requestDuration、requestTotal） | core 不重复埋点（`http_client` metrics 回调保留供单进程场景） |
| Agent 调度（槽位/队列/取消） | 路由层并发治理 | 调度属业务编排，归 fusion-cowork / agent-studio |

**多节点接入**：`export FUSION_MLX_URL=http://<gateway-host>:11432/v1`，core 即打到 gateway，gateway 负责路由到集群节点。core 自身永远是单 `base_url` 视角。

详见 `../audit/fusion-core-audit-report-0824.md` §六 落地状态。

## 迁移指引（自建客户端 → fusion-core）

仍用裸 `httpx` 直连 MLX（无重试/超时/指标）的项目：`fusion-health`、`fusion-science`、`fusion-rag`、`fusion-simulation`、`fusion-code-modelization`、`fusion-security`、`fusion-trainer`。

迁移步骤（每项目独立 PR，见 `architecture/venv-fix-0823.md` §5）：

1. `httpx.AsyncClient.post(.../chat/completions)` → `create_async_client(...)` + `await client.chat(...)`
2. 自建 `_parse_json` → `parse_llm_json`（失败抛错，不兜底 `return {}`）
3. 不传 base_url 即用 fusion-core 默认 `localhost:11434/v1`（与 fusion-mlx `start.sh` 实际端口对齐，非网关）
4. 静默降级 `return LLMResult(content="", error=...)` → 抛错（治审计 D-H3 静默失败）

```python
# 迁移前（health llm_gateway.py 静默失败）
try:
    resp = await client.post(f"{url}/chat/completions", ...)
    return LLMResult(content=resp.json()["choices"][0]["message"]["content"])
except Exception as e:
    return LLMResult(content="", error=str(e))  # 静默！下游按空继续

# 迁移后
from fusion_core import create_async_client

self._client = create_async_client(base_url=url, api_key=key, model=model)
result = await self._client.chat(messages=messages)  # 失败抛异常
return LLMResult(content=result.content, model=result.model)
```

## PRD §7.1 验收对照（实测，非声明）

| 验收项 | 状态 | 实测依据 |
|--------|------|----------|
| `import fusion_core` 不触发任何 I/O（不读 env/文件/连接） | ✅ 达标 | `mlx_client` 删除模块级 `os.environ.get`，运行时调 `default_mlx_base_url()`；`tests/test_config.py::TestImportTimeIsolation` 用 env-get spy 守护 |
| grep 源码无 `or {}` / `or []` / `or ""` 静默兜底 | ✅ 达标 | `resolve_api_key` 删 `or ""`；`get_server_stats` 失败抛错不 `return {}` |
| LLM 客户端不含重试逻辑（单一职责） | ✅ 达标 | `mlx_client.chat` 路由 `http_client.with_retry`；重试码/异常单一来源 `RETRY_STATUS`/`RETRY_EXCEPTIONS` |
| 集成测试访问 11434（PRD §7.1） | ✅ 达标 | `DEFAULT_MLX_PORT = 11434`，对齐 fusion-mlx `start.sh`；集成 fixture `was_running` 不误杀用户引擎 |
| CORS `*`+credentials 拒绝 | ✅ 达标 | `create_app(cors_origins=["*"], cors_credentials=True)` 抛 `ValueError`；credentials 默认 False |

## 测试

```bash
pytest tests/ -m "not integration"   # 单元：164 passed, 1 skipped（环境相关）
pytest tests/ -m integration          # 真实 fusion-mlx 引擎：7 passed, 0 skipped
ruff check . && ruff format --check . # lint clean
```

## 文档

- [English](README.md)
- 模块参考：[`docs/`](docs/) —— 各模块 API 签名、参数、返回、异常、示例
- 审计：`../audit/fusion-core-audit-report-0824.md` —— 28 发现，21 core 内修复 + 7 边界声明
- PRD：`../architecture/fusion-core-prd-0823.md`

## 相关

- 修复方案：`../architecture/venv-fix-0823.md` §5（客户端推广）
- 审计：`../audit/fusion-audit-all-report.md` 第四章 Q1
- License：Apache-2.0
