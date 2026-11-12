---
doc_id: fusion-core/get-started
product: fusion-core
class: platform
entry: developer
doc_type: get-started
title: {zh: "Fusion-Core 快速上手", en: "Fusion-Core Get Started"}
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

# Fusion-Core 快速上手 / Get Started

前置条件：Python >= 3.12，仓库根 venv 已激活（`source /Users/dahai/fusion/.venv/bin/activate`）。fusion-core 安装进 venv 后即可被任何 Python 代码 import。

## 1. 安装

```bash
cd /Users/dahai/fusion
pip install -e fusion-core          # core: httpx only
```

确认点：`python -c "import fusion_core; print(fusion_core.__version__)"` 输出版本号，且无任何 I/O 副作用。

需要 FastAPI 工厂时加 extra：

```bash
pip install -e "fusion-core[fastapi]"   # + fastapi, uvicorn, pydantic
```

## 2. 三行接入 LLM（要求 fusion-mlx 在 :11434 在线）

```python
import asyncio
from fusion_core import create_async_client, parse_llm_json


async def main():
    client = create_async_client()
    resp = await client.chat(messages=[{"role": "user", "content": 'Reply with {"ok": true} only'}])
    print(parse_llm_json(resp.content))


asyncio.run(main())
```

确认点：打印 `{'ok': True}`。没有 fusion-mlx 时连接会重试后抛 `RetryExhaustedError` —— 这是预期失败路径。

## 3. 指向网关（多节点形态）

```bash
export FUSION_MLX_URL=http://127.0.0.1:11432   # fusion-gateway
```

确认点：重跑第 2 步脚本，客户端改走网关；熔断让渡见 `with_retry(verify_gateway=)`。

## 4. 起 FastAPI 服务骨架（需要 [fastapi] extra）

```python
from fusion_core.http import create_app, install_auth
from fusion_core.logging import setup_logging

setup_logging("my-service")
app = create_app("my-service", readiness_probe=lambda: True)  # async probe also accepted
install_auth(app, api_keys=["local-dev-key"])  # never commit real keys
```

确认点：`uvicorn` 启动后 `GET /health` 200、`GET /ready` 200、无 key 的业务路由 401 且响应带与请求相同的 request_id。

## 5. 运行测试（本仓库工作流）

```bash
cd /Users/dahai/fusion/fusion-core
python -m pytest tests/ -m "not integration" -q    # offline suite
ruff check .
```

确认点：离线测试全绿。集成测试需要真实 fusion-mlx，显式 `-m integration` 才会启动引擎。

## 6. 校验文档契约

```bash
python3 ci/validate_docs.py --root . --product fusion-core --class platform --entry developer
```

确认点：输出 `ERROR 0`。

## 下一步

- API 细节：`docs/api-reference.md`
- 环境变量与 key 解析：`docs/config-reference.md`
- 模块内幕：`docs/mlx_client.md`、`docs/http_client.md`、`docs/tenant.md`
