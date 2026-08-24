# parse

LLM-output JSON parsing. Failures are visible, never silent.

## Symbols

- [`ParseError`](#parseerror)
- [`strip_code_fence(text)`](#strip_code_fence)
- [`parse_llm_json(text)`](#parse_llm_json)
- [`parse_llm_json_safe(text, *, default)`](#parse_llm_json_safe)
- [`parse_llm_json_lenient(text)`](#parse_llm_json_lenient)

## ParseError

```python
class ParseError(ValueError):
    text: str
    reason: str
```

Raised by all parse functions on failure. Carries the original `text` and a `reason`. Message includes a 120-char preview.

## strip_code_fence

```python
def strip_code_fence(text: str) -> str
```

Strips a single paired code fence (first open ```` ``` ```` … last close ```` ``` ````). Handles multi-block LLM output: pairs the first opening fence with the last closing fence, so inner content with stray fences is preserved. Returns the inner content stripped; if fewer than 2 fences, returns `text.strip()`.

## parse_llm_json

```python
def parse_llm_json(text: str) -> dict | list
```

Strict parse. Strips fences, then `json.loads`. Returns dict or list only.

Raises:
- `TypeError` — `text` not a `str`
- `ParseError` — empty input, `JSONDecodeError`, or result not dict/list

```python
from fusion_core import parse_llm_json

data = parse_llm_json('```json\n{"a": 1}\n```')  # {"a": 1}
```

## parse_llm_json_safe

```python
def parse_llm_json_safe(text: str, *, default: dict | list) -> dict | list
```

Returns `parse_llm_json(text)` on success; on `ParseError` returns `default`. `default` is **required** and must be dict or list (no implicit `{}` — forces the caller to choose a deliberate fallback shape).

Raises:
- `TypeError` — `default` not dict/list

```python
from fusion_core import parse_llm_json_safe

data = parse_llm_json_safe(bad_text, default={"items": []})
```

## parse_llm_json_lenient

```python
def parse_llm_json_lenient(text: str) -> dict | list
```

Lenient parse for LLM output with prose around the JSON. Tries strict first; on `JSONDecodeError`, scans for the first `{` or `[` and `raw_decode`s from there. Scan cap `_MAX_LENIENT_SCAN = 200000` chars (guards against O(n²) on brace-heavy code output). Returns dict or list.

Raises:
- `TypeError` — `text` not a `str`
- `ParseError` — empty, result not dict/list, or no parseable object found

```python
from fusion_core import parse_llm_json_lenient

data = parse_llm_json_lenient('Here is the result: {"score": 9}. Done.')  # {"score": 9}
```

## Design notes

- No `or {}` / `or []` fallback anywhere — every failure path raises `ParseError` (except `_safe`, which requires an explicit `default`).
- `RETRY`-free: parsing is pure, no I/O.
- Fence handling uses one `_FENCE_RE` (`^```[a-zA-Z]*\s*$` multiline) matching first-open/last-close — correct for multi-block output.
