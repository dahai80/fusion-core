from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Desensitization toolkit v1 (issue #56, flywheel Loop A).
# Standard shared by all fusion-* services — nobody hand-rolls PII regex.

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}")
_SK_KEY = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_B64 = re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b")
_CREDIT_RUN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
_IP = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")
# credential-bearing URL: scheme://user:pass@rest (rest of the authority kept)
_CRED_URL = re.compile(r"\b(https?://)[^\s/:@]+:([^\s/@]+)@")
# query params that carry credentials — value replaced with ***
_URL_SECRET_PARAM = re.compile(
    r"([?&](?:token|access_token|api_key|apikey|secret|password|sig|signature|session_id|sid)=)[^&\s]+",
    re.IGNORECASE,
)
# hard-deny triggers (issue #56): a sample containing these fails the whole sample —
# the caller must not persist it. Raw credential VALUES, not identifiers.
_HARD_DENY = re.compile(
    r"(?i)\b(?:password|passwd|pwd|session[_-]?token|captcha[_-]?answer)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;}{]+"
)
_USERNAME_QUOTED = re.compile(
    r"([\"'](?:username|user|tenant)[\"']\s*[:=]\s*[\"'])([^\"']{1,64})([\"'])", re.IGNORECASE
)

# checked in order; class names stable — downstream dashboards key on them
_CLASS_CHECKS = (
    ("jwt", _JWT),
    ("secret", _SK_KEY),
    ("secret", _HEX),
    ("secret", _B64),
    ("email", _EMAIL),
    ("idcard", _IDCARD),
    ("phone", _PHONE),
    ("credit_card", _CREDIT_RUN),
    ("ip", _IP),
    ("cred_url", _CRED_URL),
)


@dataclass
class DesensitizeResult:
    masked: str
    classes: list[str]
    denied: bool


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def pii_classes(text: str) -> list[str]:
    # Detected PII/secret classes in text, in stable check order. Matches what
    # desensitize() masks (minus identity hashing, which needs caller context).
    found: list[str] = []
    for name, pattern in _CLASS_CHECKS:
        if name in found:
            continue
        if name == "credit_card":
            if any(_luhn_ok(m.group(0)) for m in pattern.finditer(text)):
                found.append(name)
        elif pattern.search(text):
            found.append(name)
    return found


def desensitize(text: str, *, username: str | None = None, tenant: str | None = None) -> DesensitizeResult:
    # v1 pipeline (issue #56): hard-deny check -> credential URL strip -> secret
    # masks -> PII masks -> username/tenant -> 8-char sha256 hash tokens.
    # Denied samples return masked="" — the whole sample fails, persist nothing.
    classes = pii_classes(text)
    if _HARD_DENY.search(text):
        logger.warning("desensitize: sample hard-denied (raw credential value present), classes=%s", classes)
        return DesensitizeResult(masked="", classes=classes, denied=True)

    masked = _CRED_URL.sub(r"\1***:***@", text)
    masked = _URL_SECRET_PARAM.sub(r"\1***", masked)
    masked = _JWT.sub("[SECRET]", masked)
    masked = _SK_KEY.sub("[SECRET]", masked)
    masked = _HEX.sub("[SECRET]", masked)
    masked = _B64.sub("[SECRET]", masked)
    masked = _EMAIL.sub("[EMAIL]", masked)
    masked = _IDCARD.sub("[IDCARD]", masked)
    masked = _PHONE.sub("[PHONE]", masked)
    masked = _CREDIT_RUN.sub(lambda m: "[CREDITCARD]" if _luhn_ok(m.group(0)) else m.group(0), masked)
    masked = _IP.sub("[IP]", masked)
    if username:
        masked = masked.replace(username, _hash_token(username))
    if tenant:
        masked = masked.replace(tenant, _hash_token(tenant))
    # quoted username/tenant fields inside JSON-ish payloads hash independently
    masked = _USERNAME_QUOTED.sub(lambda m: f"{m.group(1)}{_hash_token(m.group(2))}{m.group(3)}", masked)
    return DesensitizeResult(masked=masked, classes=classes, denied=False)
