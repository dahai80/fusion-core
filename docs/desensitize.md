---
doc_id: fusion-core/desensitize-reference
product: fusion-core
class: platform
entry: developer
doc_type: api-reference
title: {zh: "desensitize 模块", en: "desensitize module"}
audience: engineer
status: draft
version: 0.15.0
updated: 2026-11-19
owner: "@dahai"
source_of_truth:
  - fusion_core/desensitize.py
---

# desensitize

Desensitization toolkit v1 (issue #56, flywheel Loop A) — the standard shared by all fusion-* services. Nobody hand-rolls PII regex.

## Symbols

- [`desensitize(text, *, username=None, tenant=None) -> DesensitizeResult`](#desensitize)
- [`pii_classes(text) -> list[str]](#pii_classes)
- [`DesensitizeResult`](#desensitizeresult)

## Mask classes

| Class | Pattern | Mask |
|-------|---------|------|
| `jwt` | `eyJ...` three-segment JWT | `[SECRET]` |
| `secret` | `sk-` keys, hex ≥32, base64 ≥32 | `[SECRET]` |
| `email` | RFC-style email | `[EMAIL]` |
| `idcard` | CN 18-digit resident id | `[IDCARD]` |
| `phone` | CN mobile `1[3-9]` + 9 digits (boundary-guarded) | `[PHONE]` |
| `credit_card` | 13-19 digit run **passing Luhn** only | `[CREDITCARD]` |
| `ip` | dotted quad, octet-range validated | `[IP]` |
| `cred_url` | `user:pass@` authority + credential query params (`token=`, `api_key=`, `secret=`, ...) | `***` |

Username/tenant: caller-supplied `username`/`tenant` values replaced by 8-char sha256 hash tokens; quoted JSON fields (`"username": "x"`) hash independently.

## Hard-deny list

A sample containing a raw credential **value** fails the whole sample: `password=...` / `passwd` / `pwd` / `session_token=...` / `captcha_answer=...` (key/value patterns, quoted or bare). Denied samples return `masked=""` — persist nothing. The key name alone ("user forgot their password") does not deny.

## desensitize

```python
desensitize(text: str, *, username: str | None = None, tenant: str | None = None) -> DesensitizeResult
```

Pipeline: hard-deny check → credential URL strip → secret masks → PII masks → identity hash tokens.

```python
from fusion_core import desensitize

r = desensitize("user a@b.com paid with 4111111111111111", username="zhangsan")
r.masked  # "user [EMAIL] paid with [CREDITCARD]"
r.classes  # ["email", "credit_card"]
r.denied  # False
```

## pii_classes

```python
pii_classes(text: str) -> list[str]
```

Detected classes in stable check order — the same checks `desensitize` applies (minus identity hashing, which needs caller context). Credit-card detection applies the Luhn filter, so `pii_classes` and the masks always agree.

## DesensitizeResult

Dataclass: `masked: str`, `classes: list[str]`, `denied: bool`. `denied=True` → `masked == ""`, do not persist the sample.
