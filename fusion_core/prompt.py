from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{|\}\}|\{(\w+)\}")
_SUFFIXES = (".txt", ".md")


class PromptManager:
    def __init__(self, prompts_dir: str | Path):
        self.prompts_dir = Path(prompts_dir)
        if not self.prompts_dir.is_dir():
            raise FileNotFoundError(f"prompts_dir not a directory: {self.prompts_dir}")
        # mtime-gated cache: first read wins, but on-disk edits are picked up on
        # the next get() when mtime_ns changes (E3 — parallel to config R1 hot-reload).
        # Cache maps name -> (text, mtime_ns).
        self._cache: dict[str, tuple[str, int]] = {}

    def _resolve(self, name: str) -> Path:
        p = self.prompts_dir / name
        if p.is_file():
            return p
        for suf in _SUFFIXES:
            cand = self.prompts_dir / f"{name}{suf}"
            if cand.is_file():
                return cand
        raise FileNotFoundError(f"prompt template not found: {name} in {self.prompts_dir}")

    def get(self, name: str) -> str:
        p = self._resolve(name)
        mtime = p.stat().st_mtime_ns
        cached = self._cache.get(name)
        if cached is not None and cached[1] == mtime:
            return cached[0]
        text = p.read_text(encoding="utf-8")
        self._cache[name] = (text, mtime)
        return text

    def clear_cache(self) -> None:
        self._cache.clear()
        logger.info("prompt cache cleared")

    def render(self, template_name: str, **variables: Any) -> str:
        template = self.get(template_name)

        def repl(match: re.Match[str]) -> str:
            whole = match.group(0)
            if whole == "{{":
                return "{"
            if whole == "}}":
                return "}"
            key = match.group(1)
            if key not in variables:
                raise KeyError(f"missing prompt variable {key!r} for template {template_name!r}")
            return str(variables[key])

        return _VAR_RE.sub(repl, template)

    def list_names(self) -> list[str]:
        names: list[str] = []
        for p in sorted(self.prompts_dir.iterdir()):
            if p.is_file() and p.suffix in _SUFFIXES:
                names.append(p.stem)
        return names
