---
doc_id: fusion-core/errors-reference
product: fusion-core
class: platform
entry: developer
doc_type: api-reference
title: {zh: "errors 模块", en: "errors module"}
audience: engineer
status: draft
version: 0.11.0
updated: 2026-11-12
owner: "@dahai"
source_of_truth:
  - fusion_core/errors.py
---

# errors

Shared error tree base class (issue #47). Business apps keep private error trees (e.g. ai-linguakids `core/errors.py`) — this module replaces them: one base carrying a stable machine-readable `code`, an operator-facing `remediation` hint, structured `context`, and the `http_status` the error maps to.

## Symbols

```
class FusionError(Exception):
    code: str = "fusion_error"          # class default, overridable per instance
    http_status: int = 500              # class default, overridable per instance
    remediation: str = ""               # class default, overridable per instance
    def __init__(message, *, code=None, http_status=None, remediation=None, context=None)
    def to_http(self) -> dict
```

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `message` | `str` | required | Human-readable; also `str(err)` |
| `code` | `str \| None` | class default | Stable slug for machines (`"not_found"`, `"bad_input"`, ...) |
| `http_status` | `int \| None` | class default | HTTP status `to_http()` reports |
| `remediation` | `str \| None` | class default | What the caller/operator should do next |
| `context` | `dict \| None` | `None` | Structured detail; **copied** on construction |

## to_http

```
def to_http(self) -> dict
```

Returns `{"status", "code", "message", "remediation", "context"}` — sanitized payload for API error responses (use directly in a FastAPI exception handler; `fusion_core.http.standard_error_handler` stays generic). Context keys are logged (DEBUG) but the payload is built only from what the raise supplied — no internals leak.

## Example

```python
from fusion_core import FusionError


class DocNotFound(FusionError):
    code = "doc_not_found"
    http_status = 404
    remediation = "check the doc_id, list docs via GET /docs"


err = DocNotFound("no doc x1", context={"doc_id": "x1"})
err.to_http()
# {'status': 404, 'code': 'doc_not_found', 'message': 'no doc x1',
#  'remediation': 'check the doc_id, list docs via GET /docs', 'context': {'doc_id': 'x1'}}
```

Wrap upstream failures so the chain stays intact:

```python
try:
    upstream_call()
except ValueError as e:
    raise FusionError("wrapped upstream failure", context={"step": "extract"}) from e
```

## Notes

- Subclasses set class-level defaults; per-raise kwargs override for one instance.
- `context` is copied at construction — mutating the caller's dict afterwards does not affect the error.
- No silent fallbacks: constructing with no fields still yields a valid 500 `fusion_error` payload.
