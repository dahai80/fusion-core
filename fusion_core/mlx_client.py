from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from fusion_core.http_client import RETRY_EXCEPTIONS, RETRY_STATUS, with_retry

logger = logging.getLogger(__name__)

_SSE_DATA_PREFIX = "data: "
_SSE_DONE = "[DONE]"
_HEALTH_THROTTLE_SECONDS = 1.0

_CHAT_KWARGS_ALLOWLIST = frozenset(
    {
        "top_p",
        "seed",
        "response_format",
        "user",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "logit_bias",
        "logprobs",
        "top_logprobs",
    }
)


class StreamError(RuntimeError):
    def __init__(self, message: str, *, delivered: int = 0, resume_offset: int = 0):
        self.delivered = delivered
        self.resume_offset = resume_offset
        super().__init__(f"{message} (delivered={delivered}, resume_offset={resume_offset})")


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )


@dataclass
class EmbeddingResponse:
    vector: list[float]
    vectors: list[list[float]] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 0})


@dataclass
class ServerStats:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_tokens_generated: int = 0
    total_prompt_tokens: int = 0
    active_requests: int = 0
    uptime_seconds: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerStats:
        return cls(
            total_requests=int(data.get("total_requests", 0)),
            successful_requests=int(data.get("successful_requests", 0)),
            failed_requests=int(data.get("failed_requests", 0)),
            total_tokens_generated=int(data.get("total_tokens_generated", 0)),
            total_prompt_tokens=int(data.get("total_prompt_tokens", 0)),
            active_requests=int(data.get("active_requests", 0)),
            uptime_seconds=float(data.get("uptime_seconds", 0.0)),
            raw=data,
        )


class FusionMLXClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
        model: str | None = None,
    ):
        from fusion_core.config import default_mlx_base_url, resolve_api_key

        self.base_url = (base_url or default_mlx_base_url()).rstrip("/")
        self.api_key = resolve_api_key(api_key)
        if not self.api_key:
            logger.warning(
                "api_key missing: MLX calls unauthenticated, upstream may 401 "
                "(set env FUSION_MLX_API_KEY or settings.json auth.api_key)"
            )
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.transport = transport
        self.default_model = model
        self._client: httpx.AsyncClient | None = None
        self._probe_client: httpx.AsyncClient | None = None
        self._last_health_at: float = 0.0
        self._last_health: bool | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.api_key}"},
                transport=self.transport,
            )
        return self._client

    @property
    def probe_client(self) -> httpx.AsyncClient:
        if self._probe_client is None:
            self._probe_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=2.0,
                headers={"Authorization": f"Bearer {self.api_key}"},
                transport=self.transport,
            )
        return self._probe_client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._probe_client:
            await self._probe_client.aclose()
            self._probe_client = None

    async def aclose(self) -> None:
        await self.close()

    async def __aenter__(self) -> FusionMLXClient:
        _ = self.client
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def _resolve_model(self, model: str | None) -> str:
        resolved = model or self.default_model
        if not resolved:
            raise ValueError("chat requires model: pass model= or set create_async_client(model=...)")
        return resolved

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
        total_deadline: float | None = None,
        **kwargs,
    ) -> LLMResponse:
        resolved = self._resolve_model(model)
        if stream:
            raise ValueError("chat(stream=True) not supported; use stream_chat() for streaming")
        # total_deadline is a with_retry control param, not an MLX payload field.
        # Accept it both as explicit kwarg and (legacy) via **kwargs to avoid the
        # "dropping non-allowlisted kwarg" false warning (R5).
        legacy_deadline = kwargs.pop("total_deadline", None)
        deadline = total_deadline if total_deadline is not None else legacy_deadline
        payload = {
            "model": resolved,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        for key, value in kwargs.items():
            if key in _CHAT_KWARGS_ALLOWLIST:
                if key in payload:
                    logger.warning("chat kwarg %s overrides explicit param %s", key, key)
                payload[key] = value
            else:
                logger.warning("chat dropping non-allowlisted kwarg %s=%r", key, value)

        resp = await with_retry(
            lambda: self.client.post("/chat/completions", json=payload),
            retries=self.max_retries,
            initial_backoff=self.retry_delay,
            total_deadline=deadline,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices")
        if not choices:
            raise ValueError("chat response missing choices")
        choice = choices[0]
        message = choice.get("message", {})
        return LLMResponse(
            content=message.get("content", ""),
            tool_calls=message.get("tool_calls", []),
            finish_reason=choice.get("finish_reason", "stop"),
            usage=data.get("usage", {}),
        )

    async def chat_text(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        total_deadline: float | None = None,
        **kwargs,
    ) -> str:
        resp = await self.chat(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            total_deadline=total_deadline,
            **kwargs,
        )
        return resp.content

    async def stream_chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        resolved = self._resolve_model(model)
        payload = {
            "model": resolved,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            yielded = 0
            try:
                async with self.client.stream("POST", "/chat/completions", json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith(_SSE_DATA_PREFIX):
                            continue
                        chunk = line[len(_SSE_DATA_PREFIX) :]
                        if chunk.strip() == _SSE_DONE:
                            return
                        try:
                            data = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        choices = data.get("choices")
                        if not choices:
                            continue
                        content = choices[0].get("delta", {}).get("content", "")
                        if content:
                            yielded += len(content)
                            yield content
                    return
            except httpx.HTTPStatusError as e:
                if e.response.status_code in RETRY_STATUS and attempt < self.max_retries and not yielded:
                    last_exc = e
                    logger.warning("stream_chat HTTP %s, retrying", e.response.status_code)
                    continue
                if yielded:
                    raise StreamError(
                        "stream failed after partial output, not retried",
                        delivered=yielded,
                        resume_offset=yielded,
                    ) from e
                if e.response.status_code in RETRY_STATUS:
                    # Retriable status but retries exhausted, no output: wrap in
                    # StreamError so callers have ONE stream-failure type (H4/R4).
                    raise StreamError(
                        f"stream_chat retries exhausted on retriable HTTP {e.response.status_code}",
                        delivered=0,
                        resume_offset=0,
                    ) from e
                # Non-retriable 4xx (400/401/403...): a request error, not a stream
                # failure. Re-raise the original HTTPStatusError so callers can
                # distinguish bad-request from severed-stream.
                raise
            except RETRY_EXCEPTIONS as e:
                last_exc = e
                if attempt < self.max_retries and not yielded:
                    logger.warning("stream_chat attempt %d failed: %s, retrying", attempt + 1, e)
                    continue
                if yielded:
                    raise StreamError(
                        "stream failed after partial output, not retried",
                        delivered=yielded,
                        resume_offset=yielded,
                    ) from e
                # Retriable exception but retries exhausted, no output: wrap (H4/R4).
                raise StreamError(
                    f"stream_chat retries exhausted on {type(e).__name__}",
                    delivered=0,
                    resume_offset=0,
                ) from e
        # Loop exited without return/raise (e.g. max_retries==0 and a retriable
        # status slipped through the guard): wrap defensively as StreamError.
        if last_exc is not None:
            raise StreamError(
                f"stream_chat exhausted on {type(last_exc).__name__}",
                delivered=0,
                resume_offset=0,
            ) from last_exc
        raise StreamError("stream_chat exhausted with no exception recorded", delivered=0, resume_offset=0)

    async def embed(
        self,
        text: str | list[str],
        *,
        model: str,
    ) -> EmbeddingResponse:
        batched = isinstance(text, list)
        payload = {
            "model": model,
            "input": text,
        }
        resp = await self.client.post("/embeddings", json=payload)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data")
        if not items:
            raise ValueError("embeddings response missing data")
        vectors = [item["embedding"] for item in items]
        first = vectors[0]
        return EmbeddingResponse(
            vector=first,
            vectors=vectors if batched else [],
            model=data.get("model", model),
            usage=data.get("usage", {}),
        )

    async def list_models(self) -> list[dict[str, Any]]:
        resp = await self.client.get("/models")
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])

    async def health(self) -> bool:
        now = time.monotonic()
        if self._last_health is not None and now - self._last_health_at < _HEALTH_THROTTLE_SECONDS:
            logger.debug("health throttled, returning cached=%s", self._last_health)
            return self._last_health
        try:
            resp = await self.probe_client.get("/models")
            ok = resp.status_code == 200
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError) as e:
            logger.warning("health check failed (unreachable): %s", e)
            ok = False
        self._last_health = ok
        self._last_health_at = now
        return ok

    async def get_server_stats(self) -> ServerStats:
        resp = await self.client.get("/stats", timeout=5.0)
        resp.raise_for_status()
        return ServerStats.from_dict(resp.json())


def create_async_client(
    *,
    backend: str = "mlx",
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 120.0,
    max_retries: int = 2,
    retry_delay: float = 1.0,
) -> FusionMLXClient:
    if backend != "mlx":
        raise ValueError(f"unsupported backend: {backend!r}; only 'mlx' is supported")
    return FusionMLXClient(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
        transport=transport,
        model=model,
    )
