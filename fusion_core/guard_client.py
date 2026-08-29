from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Any

from fusion_core.config import default_guard_sock

logger = logging.getLogger(__name__)

_RPC_VERSION = "2.0"
_FRAMING_BYTE = 0x0A
_MAX_LINE_BYTES = 1024 * 1024
_DEFAULT_TIMEOUT = 2.0


# === error hierarchy ===


class GuardError(Exception):
    pass


class GuardParseError(GuardError):
    RPC_CODE = -32700


class GuardInvalidRequestError(GuardError):
    RPC_CODE = -32600


class GuardMethodNotFoundError(GuardError):
    RPC_CODE = -32601


class GuardInvalidParamsError(GuardError):
    RPC_CODE = -32602


class GuardInternalError(GuardError):
    RPC_CODE = -32603


class GuardUnauthorizedError(GuardError):
    RPC_CODE = -32001


class GuardRateLimitError(GuardError):
    RPC_CODE = -32002


class StaleEpochError(GuardError):
    RPC_CODE = -32003

    def __init__(self, message: str, *, caller_epoch: int = 0, guard_epoch: int = 0):
        self.caller_epoch = caller_epoch
        self.guard_epoch = guard_epoch
        super().__init__(message)


class GuardEngineError(GuardError):
    RPC_CODE = -32010


_CODE_TO_ERROR: dict[int, type[GuardError]] = {
    -32700: GuardParseError,
    -32600: GuardInvalidRequestError,
    -32601: GuardMethodNotFoundError,
    -32602: GuardInvalidParamsError,
    -32603: GuardInternalError,
    -32001: GuardUnauthorizedError,
    -32002: GuardRateLimitError,
    -32003: StaleEpochError,
    -32010: GuardEngineError,
}


# === response dataclasses ===


@dataclass
class GuardVerdict:
    action: str
    risk_level: str
    reason: str
    stage: str = ""
    requires_approval: bool = False
    redacted_content: str | None = None
    seatbelt_required: bool = False
    action_id: str | None = None
    verdict_epoch: int = 0
    verdict_ttl_secs: int = 0
    inferred_category: str = ""
    category_hint: str | None = None


@dataclass
class GuardRule:
    name: str
    pattern: str
    stage: str = ""
    action: str = "allow"
    risk_level: str = "l1"
    reason: str = ""
    scope: str = "content"


@dataclass
class RedactResult:
    redacted_content: str
    token_map_id: str | None = None


@dataclass
class ChainVerification:
    total_rows: int = 0
    unhashed_rows: int = 0
    verified_links: int = 0
    broken_links: int = 0
    tampered: bool = False
    first_broken_at: int | None = None


@dataclass
class AllChainsVerification:
    audit: ChainVerification = field(default_factory=ChainVerification)
    tcc: ChainVerification = field(default_factory=ChainVerification)
    rules: ChainVerification = field(default_factory=ChainVerification)
    dead_letter: ChainVerification = field(default_factory=ChainVerification)
    tampered: bool = False


# === client ===


class FusionGuardClient:
    def __init__(self, sock_path: str | None = None, *, timeout: float = _DEFAULT_TIMEOUT):
        # env resolved lazily at call sites, never at import (PRD §0.2: import = no I/O).
        self._sock_path = sock_path
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._id = 0

    def _resolve_sock_path(self) -> str:
        if self._sock_path is not None:
            return self._sock_path
        return default_guard_sock()

    def _connect(self) -> socket.socket:
        if self._sock is not None:
            try:
                self._sock.getpeername()
                return self._sock
            except OSError:
                logger.debug("guard socket %s stale, reconnecting", self._resolve_sock_path())
                self._safe_close()
        path = self._resolve_sock_path()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect(path)
        except OSError as exc:
            sock.close()
            logger.warning("guard_client connect to %s failed: %s", path, exc)
            raise GuardError(f"guard unreachable at {path}: {exc}") from exc
        self._sock = sock
        logger.debug("guard_client connected to %s", path)
        return sock

    def _safe_close(self) -> None:
        if self._sock is not None:
            with _suppress_oserror():
                self._sock.close()
            self._sock = None

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, sock: socket.socket, payload: dict) -> None:
        data = (json.dumps(payload, separators=(",", ":")) + chr(_FRAMING_BYTE)).encode("utf-8")
        sock.sendall(data)

    def _recv_line(self, sock: socket.socket) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                if not chunks:
                    # no bytes yet = server dropped us mid-call; retryable.
                    raise ConnectionError("guard socket closed before any response data")
                raise GuardError("guard socket closed mid-response (truncated framing)")
            total += len(chunk)
            if total > _MAX_LINE_BYTES:
                raise GuardError(f"guard response exceeded {_MAX_LINE_BYTES} bytes")
            chunks.append(chunk)
            if chunk[-1] == _FRAMING_BYTE:
                break
        line = b"".join(chunks)
        return line.rstrip(bytes([_FRAMING_BYTE]))

    def _call(self, method: str, params: dict | None = None) -> Any:
        # one JSON-RPC request per call; reconnect on dropped socket, retry once.
        payload = {"jsonrpc": _RPC_VERSION, "id": self._next_id(), "method": method}
        if params:
            payload["params"] = params
        for attempt in (1, 2):
            try:
                sock = self._connect()
                self._send(sock, payload)
                raw = self._recv_line(sock)
            except (GuardError, OSError) as exc:
                if attempt == 1 and isinstance(exc, OSError):
                    logger.debug("guard call %s dropped, reconnecting once: %s", method, exc)
                    self._safe_close()
                    continue
                raise
            break
        try:
            resp = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise GuardParseError(f"guard response unparseable: {exc}") from exc
        if not isinstance(resp, dict):
            raise GuardParseError(f"guard response not a JSON object: {resp!r}")
        if "error" in resp and resp["error"] is not None:
            raise self._raise_error(resp["error"])
        return resp.get("result")

    def _raise_error(self, err: Any) -> GuardError:
        if not isinstance(err, dict):
            return GuardError(f"guard error payload malformed: {err!r}")
        code = err.get("code", 0)
        msg = err.get("message", "guard error")
        exc_cls = _CODE_TO_ERROR.get(code, GuardError)
        if exc_cls is StaleEpochError:
            data = err.get("data") or {}
            return StaleEpochError(
                msg,
                caller_epoch=int(data.get("caller_epoch", 0)),
                guard_epoch=int(data.get("guard_epoch", 0)),
            )
        return exc_cls(msg)

    def __enter__(self) -> FusionGuardClient:
        self._connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._safe_close()
        logger.debug("guard_client closed")

    def ping(self) -> dict:
        result = self._call("guard.ping")
        if not isinstance(result, dict):
            raise GuardError(f"guard.ping result not a dict: {result!r}")
        return result

    def evaluate(
        self,
        content: str,
        *,
        caller_epoch: int = 0,
        tenant_id: str | None = None,
        requester: str | None = None,
        content_type: str | None = None,
        category_hint: str | None = None,
    ) -> GuardVerdict:
        params: dict[str, Any] = {"content": content, "caller_epoch": caller_epoch}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id
        if requester is not None:
            params["requester"] = requester
        if content_type is not None:
            params["content_type"] = content_type
        if category_hint is not None:
            params["category_hint"] = category_hint
        result = self._call("guard.evaluate", params)
        if not isinstance(result, dict):
            raise GuardError(f"guard.evaluate result not a dict: {result!r}")
        # block verdicts come back as result.action="block", NOT as RPC errors (E5):
        # callers inspect GuardVerdict.action; a block is a normal verdict here.
        return GuardVerdict(
            action=str(result.get("action", "allow")),
            risk_level=str(result.get("risk_level", "l1")),
            reason=str(result.get("reason", "")),
            stage=str(result.get("stage", "")),
            requires_approval=bool(result.get("requires_approval", False)),
            redacted_content=result.get("redacted_content"),
            seatbelt_required=bool(result.get("seatbelt_required", False)),
            action_id=_str_or_none(result.get("action_id")),
            verdict_epoch=int(result.get("verdict_epoch", 0)),
            verdict_ttl_secs=int(result.get("verdict_ttl_secs", 0)),
            inferred_category=str(result.get("inferred_category", "")),
            category_hint=result.get("category_hint"),
        )

    def list_rules(self) -> tuple[list[GuardRule], int]:
        result = self._call("guard.rule.list")
        if not isinstance(result, dict):
            raise GuardError(f"guard.rule.list result not a dict: {result!r}")
        raw_rules = result.get("rules", [])
        rules = [self._map_rule(r) for r in raw_rules if isinstance(r, dict)]
        epoch = int(result.get("epoch", 0))
        return rules, epoch

    def _map_rule(self, r: dict) -> GuardRule:
        return GuardRule(
            name=str(r.get("name", "")),
            pattern=str(r.get("pattern", "")),
            stage=str(r.get("stage", "")),
            action=str(r.get("action", "allow")),
            risk_level=str(r.get("risk_level", "l1")),
            reason=str(r.get("reason", "")),
            scope=str(r.get("scope", "content")),
        )

    def redact(self, content: str, *, reversible: bool = True) -> RedactResult:
        result = self._call("guard.redact", {"content": content, "reversible": reversible})
        if not isinstance(result, dict):
            raise GuardError(f"guard.redact result not a dict: {result!r}")
        return RedactResult(
            redacted_content=str(result.get("redacted_content", "")),
            token_map_id=_str_or_none(result.get("token_map_id")),
        )

    def reveal(self, content: str, token_map_id: str = "") -> str:
        result = self._call("guard.reveal", {"content": content, "token_map_id": token_map_id})
        if not isinstance(result, dict):
            raise GuardError(f"guard.reveal result not a dict: {result!r}")
        return str(result.get("content", ""))

    def confirm(
        self,
        action_id: str,
        *,
        approved: bool,
        approved_by: str | None = None,
        tenant_id: str | None = None,
    ) -> GuardVerdict:
        params: dict[str, Any] = {"action_id": action_id, "approved": approved}
        if approved_by is not None:
            params["approved_by"] = approved_by
        if tenant_id is not None:
            params["tenant_id"] = tenant_id
        result = self._call("guard.confirm", params)
        if not isinstance(result, dict):
            raise GuardError(f"guard.confirm result not a dict: {result!r}")
        inner = result.get("verdict")
        if not isinstance(inner, dict):
            raise GuardError(f"guard.confirm verdict missing: {result!r}")
        return GuardVerdict(
            action=str(inner.get("action", "allow")),
            risk_level=str(inner.get("risk_level", "l1")),
            reason=str(inner.get("reason", "")),
            stage=str(inner.get("stage", "")),
            requires_approval=bool(inner.get("requires_approval", False)),
            redacted_content=inner.get("redacted_content"),
            seatbelt_required=bool(inner.get("seatbelt_required", False)),
            action_id=_str_or_none(inner.get("action_id")),
            verdict_epoch=int(inner.get("verdict_epoch", 0)),
            verdict_ttl_secs=int(inner.get("verdict_ttl_secs", 0)),
            inferred_category=str(inner.get("inferred_category", "")),
            category_hint=inner.get("category_hint"),
        )

    def tcc_status(self) -> list[dict]:
        result = self._call("guard.tcc.status")
        if not isinstance(result, dict):
            raise GuardError(f"guard.tcc.status result not a dict: {result!r}")
        statuses = result.get("statuses", [])
        return [s for s in statuses if isinstance(s, dict)]

    def tcc_events(self, *, limit: int = 100) -> list[dict]:
        result = self._call("guard.tcc.events", {"limit": limit})
        if not isinstance(result, dict):
            raise GuardError(f"guard.tcc.events result not a dict: {result!r}")
        events = result.get("events", [])
        return [e for e in events if isinstance(e, dict)]

    def audit_verify(self) -> AllChainsVerification:
        result = self._call("guard.audit.verify")
        if not isinstance(result, dict):
            raise GuardError(f"guard.audit.verify result not a dict: {result!r}")
        return AllChainsVerification(
            audit=_map_chain(result.get("audit")),
            tcc=_map_chain(result.get("tcc")),
            rules=_map_chain(result.get("rules")),
            dead_letter=_map_chain(result.get("dead_letter")),
            tampered=bool(result.get("tampered", False)),
        )


def _map_chain(v: Any) -> ChainVerification:
    if not isinstance(v, dict):
        return ChainVerification()
    return ChainVerification(
        total_rows=int(v.get("total_rows", 0)),
        unhashed_rows=int(v.get("unhashed_rows", 0)),
        verified_links=int(v.get("verified_links", 0)),
        broken_links=int(v.get("broken_links", 0)),
        tampered=bool(v.get("tampered", False)),
        first_broken_at=_int_or_none(v.get("first_broken_at")),
    )


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class _suppress_oserror:
    def __enter__(self) -> None:
        pass

    def __exit__(self, *exc: Any) -> bool:
        return exc[0] is OSError
