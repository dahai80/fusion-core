"""Fusion-Core — shared FusionMLXClient for all Fusion components.

All LLM calls go through fusion-mlx's OpenAI-compatible HTTP API.
This module is the sole bridge between any Fusion component and
fusion-mlx. It communicates exclusively through HTTP — no direct
imports of mlx, mlx-lm, or any MLX framework code.
"""

from __future__ import annotations

from .mlx_client import FusionMLXClient, LLMResponse

__all__ = ["FusionMLXClient", "LLMResponse"]