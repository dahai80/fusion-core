"""Shared FusionMLX HTTP client — unified interface for all Fusion components.

All LLM interactions go through fusion-mlx's OpenAI-compatible HTTP API.
This module never imports mlx, mlx-lm, or any MLX framework code.
It is the single shared client used by fusion-finance, fusion-k12-teacher,
fusion-science, and all other Fusion components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class LLMResponse:
    """Structured response from an LLM call via fusion-mlx."""

    content: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
    })


class FusionMLXClient:
    """HTTP client for fusion-mlx's OpenAI-compatible API.

    All LLM interactions go through this class. It never imports
    any fusion-mlx internal module or MLX framework code — only
    communicates via HTTP.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "local",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
        **kwargs,
    ) -> LLMResponse:
        """Call fusion-mlx's /v1/chat/completions endpoint.

        Args:
            model: Model name (e.g., "qwen3.5-9b").
            messages: Conversation messages in OpenAI format.
            tools: Optional tool definitions for function calling.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            stream: Enable streaming (not yet implemented).
            **kwargs: Additional parameters to pass to the API.

        Returns:
            LLMResponse with content, tool_calls, and usage.
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        payload.update(kwargs)

        resp = await self.client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice.get("message", {})

        return LLMResponse(
            content=message.get("content", ""),
            tool_calls=message.get("tool_calls", []),
            finish_reason=choice.get("finish_reason", "stop"),
            usage=data.get("usage", {}),
        )

    async def chat_text(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Convenience method: call chat and return just the text content.

        Args:
            model: Model name.
            messages: Conversation messages in OpenAI format.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.

        Returns:
            Response text content.
        """
        resp = await self.chat(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.content

    async def list_models(self) -> list[dict[str, Any]]:
        """List available models from fusion-mlx."""
        resp = await self.client.get("/models")
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])

    async def health(self) -> bool:
        """Check if fusion-mlx is healthy and reachable."""
        try:
            resp = await self.client.get("/models", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def get_server_stats(self) -> dict[str, Any]:
        """Get server statistics from fusion-mlx."""
        try:
            resp = await self.client.get("/stats", timeout=5.0)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {}