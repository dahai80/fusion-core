from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*$", re.MULTILINE)

_DECODER = json.JSONDecoder()
_MAX_LENIENT_SCAN = 200000


class ParseError(ValueError):
    def __init__(self, text: str, reason: str):
        self.text = text
        self.reason = reason
        preview = (text or "").strip()[:120]
        super().__init__(f"parse failed: {reason}; preview={preview!r}")


def strip_code_fence(text: str) -> str:
    if not text:
        return ""
    opens = list(_FENCE_RE.finditer(text))
    if len(opens) < 2:
        return text.strip()
    first_open = opens[0]
    last_close = opens[-1]
    if last_close.start() <= first_open.end():
        return text.strip()
    inner = text[first_open.end() : last_close.start()]
    return inner.strip()


def parse_llm_json(text: str) -> dict | list:
    if not isinstance(text, str):
        raise TypeError(f"parse_llm_json expects str, got {type(text).__name__}")
    if not text:
        raise ParseError(text, "empty input")
    stripped = strip_code_fence(text)
    try:
        result = json.loads(stripped)
    except json.JSONDecodeError as exc:
        logger.debug("parse_llm_json strict fail: %s", exc)
        raise ParseError(text, f"json.loads error: {exc}") from exc
    if not isinstance(result, (dict, list)):
        raise ParseError(text, f"expected dict or list, got {type(result).__name__}: {result!r:.60}")
    return result


def _extract_first_json(text: str) -> dict | list | None:
    limit = min(len(text), _MAX_LENIENT_SCAN)
    for i in range(limit):
        ch = text[i]
        if ch not in "{[":
            continue
        try:
            obj, _end = _DECODER.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        return obj
    return None


def parse_llm_json_lenient(text: str) -> dict | list:
    if not isinstance(text, str):
        raise TypeError(f"parse_llm_json_lenient expects str, got {type(text).__name__}")
    if not text:
        raise ParseError(text, "empty input")
    stripped = strip_code_fence(text)
    try:
        result = json.loads(stripped)
    except json.JSONDecodeError:
        result = _extract_first_json(stripped)
    if result is not None and not isinstance(result, (dict, list)):
        raise ParseError(text, f"expected dict or list, got {type(result).__name__}: {result!r:.60}")
    if result is not None:
        return result
    raise ParseError(text, "no parseable json object found after fence strip + raw_decode extraction")


def parse_llm_json_safe(text: str, *, default: dict | list) -> dict | list:
    if not isinstance(default, (dict, list)):
        raise TypeError(f"parse_llm_json_safe default must be dict or list, got {type(default).__name__}")
    try:
        return parse_llm_json(text)
    except ParseError as exc:
        logger.debug("parse_llm_json_safe falling back to default: %s", exc)
        return default
