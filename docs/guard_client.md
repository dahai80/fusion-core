# guard_client

Pure-Python UDS JSON-RPC 2.0 client for **fusion-guard** — the zero-trust action-authorization daemon (Rust + Swift). Core stays pure-tech: this module is the wire-protocol adapter, not the policy engine. Block verdicts are results, not errors (E5).

## Symbols

- [`FusionGuardClient`](#fusionguardclient)
- [Result dataclasses](#result-dataclasses)
- [Error hierarchy](#error-hierarchy)

## FusionGuardClient

```python
class FusionGuardClient:
    def __init__(self, sock_path: str | None = None, *, timeout: float = 2.0)
    def __enter__(self) -> FusionGuardClient
    def __exit__(self, *exc) -> None
    def close(self) -> None
    def ping(self) -> dict
    def evaluate(self, content, *, caller_epoch=0, tenant_id=None,
                 requester=None, content_type=None, category_hint=None) -> GuardVerdict
    def list_rules(self) -> tuple[list[GuardRule], int]
    def redact(self, content, *, reversible=True) -> RedactResult
    def reveal(self, content, token_map_id="") -> str
    def confirm(self, action_id, *, approved, approved_by=None, tenant_id=None) -> GuardVerdict
    def tcc_status(self) -> list[dict]
    def tcc_events(self, *, limit=100) -> list[dict]
    def audit_verify(self) -> AllChainsVerification
```

### __init__

`sock_path` defaults to `default_guard_sock()` — `os.environ.get("FUSION_GUARD_SOCK", "/tmp/fusion-guard.sock")`, resolved **at call time** (import = no I/O). Pass an explicit path to override. `timeout` (s) applies to connect + each recv. The socket is lazy — no connection until the first call or `__enter__`.

### Methods

| Method | RPC method | Result |
|--------|-----------|--------|
| `ping()` | `guard.ping` | `dict` (`pong`, `version`, `rules_epoch`) |
| `evaluate(...)` | `guard.evaluate` | `GuardVerdict` |
| `list_rules()` | `guard.rule.list` | `(list[GuardRule], epoch: int)` |
| `redact(...)` | `guard.redact` | `RedactResult` |
| `reveal(...)` | `guard.reveal` | `str` (restored content) |
| `confirm(...)` | `guard.confirm` | `GuardVerdict` (unwrapped from `{"verdict": ...}`) |
| `tcc_status()` | `guard.tcc.status` | `list[dict]` |
| `tcc_events(...)` | `guard.tcc.events` | `list[dict]` |
| `audit_verify()` | `guard.audit.verify` | `AllChainsVerification` |

### evaluate

```python
def evaluate(self, content, *, caller_epoch=0, tenant_id=None,
             requester=None, content_type=None, category_hint=None) -> GuardVerdict
```

Sends `content` for authorization. `caller_epoch` is the caller's last-known rules epoch — if stale, the guard returns `StaleEpochError(-32003)` carrying `caller_epoch`/`guard_epoch`; refresh rules and retry. Optional `tenant_id`/`requester`/`content_type`/`category_hint` are sent only when not `None` (wire stays minimal).

**A block is a normal verdict, not an exception (E5):** `evaluate("rm -rf /")` returns `GuardVerdict(action="block", reason=...)`. Only RPC-level failures (unauthorized, rate-limited, stale epoch, engine fault) raise. This keeps "the guard decided no" separable from "the guard call failed".

### Lifecycle

```python
def __enter__(self) -> FusionGuardClient
def __exit__(self, *exc) -> None
def close(self) -> None
```

Context manager connects on entry, closes on exit. `close()` is idempotent. Outside a `with` block, the first call lazily connects; each call reconnects-on-drop with **one retry** (a dropped socket mid-call → reconnect → resend once). Reconnect is not infinite: a second failure surfaces.

## Result dataclasses

```python
@dataclass
class GuardVerdict:
    action: str                       # allow | preview | redact | block
    risk_level: str                   # l1 | l2 | l3 | l4
    reason: str
    stage: str = ""                   # regex | ast | semantic
    requires_approval: bool = False
    redacted_content: str | None = None
    seatbelt_required: bool = False
    action_id: str | None = None      # UUID string for confirm(); None or null
    verdict_epoch: int = 0
    verdict_ttl_secs: int = 0
    inferred_category: str = ""
    category_hint: str | None = None
```

```python
@dataclass
class GuardRule:
    name: str
    pattern: str
    stage: str = ""
    action: str = "allow"
    risk_level: str = "l1"
    reason: str = ""
    scope: str = "content"            # command | content | network | filesystem
```

```python
@dataclass
class RedactResult:
    redacted_content: str
    token_map_id: str | None = None   # pass to reveal() to restore
```

```python
@dataclass
class ChainVerification:
    total_rows: int = 0
    unhashed_rows: int = 0
    verified_links: int = 0
    broken_links: int = 0
    tampered: bool = False
    first_broken_at: int | None = None
```

```python
@dataclass
class AllChainsVerification:
    audit: ChainVerification = ...        # default_factory
    tcc: ChainVerification = ...
    rules: ChainVerification = ...
    dead_letter: ChainVerification = ...
    tampered: bool = False                # true if any chain tampered
```

Missing chain sub-objects in the RPC result default to an empty `ChainVerification()` (no KeyError).

## Error hierarchy

All RPC errors map to a typed subclass of `GuardError`. Unknown codes fall back to base `GuardError`.

| Exception | RPC code | Meaning |
|-----------|----------|---------|
| `GuardError` | — | base; also the fallback for unknown codes |
| `GuardParseError` | -32700 | response unparseable / not a JSON object |
| `GuardInvalidRequestError` | -32600 | invalid request |
| `GuardMethodNotFoundError` | -32601 | method not found |
| `GuardInvalidParamsError` | -32602 | invalid params |
| `GuardInternalError` | -32603 | guard internal fallback |
| `GuardUnauthorizedError` | -32001 | unauthorized / forbidden |
| `GuardRateLimitError` | -32002 | rate limited |
| `StaleEpochError` | -32003 | caller's rules epoch is stale |
| `GuardEngineError` | -32010 | engine / internal |

`StaleEpochError` carries `caller_epoch` and `guard_epoch` attributes (parsed from the error's `data`) so the caller can refresh rules and retry:

```python
try:
    verdict = guard.evaluate(content, caller_epoch=my_epoch)
except StaleEpochError as e:
    rules, my_epoch = guard.list_rules()   # refresh
    verdict = guard.evaluate(content, caller_epoch=my_epoch)
```

## Wire contract

JSON-RPC 2.0 over Unix Domain Socket, **newline-framed** (`0x0A`). Matches fusion-guard `fg-ipc`.

- Request: `{"jsonrpc":"2.0","id":<int>,"method":<str>,"params":<obj>?}` + `\n`.
- Response: `{"jsonrpc":"2.0","id":<int>,"result":<...>}` or `{"jsonrpc":"2.0","id":<int>,"error":{"code":<int>,"message":<str>,"data":<obj>?}}` + `\n`.
- Cap: 1 MiB per response line (`MAX_LINE_BYTES = 1024*1024`); exceeded → `GuardError`.
- Timeout: default 2 s on connect + recv; raises `GuardError`/`OSError`.
- Reconnect: one retry per call on a dropped socket (empty recv before any data). A truncated response (bytes received then closed) raises `GuardError` and does **not** retry — partial data is not silently resent.
- Block verdicts travel as `result`, never `error` (E5). Callers inspect `GuardVerdict.action`.

## Example

```python
from fusion_core import FusionGuardClient, StaleEpochError

with FusionGuardClient() as guard:           # FUSION_GUARD_SOCK or /tmp/fusion-guard.sock
    rules, epoch = guard.list_rules()
    try:
        v = guard.evaluate("rm -rf /", caller_epoch=epoch)
    except StaleEpochError as e:
        rules, epoch = guard.list_rules()    # refresh, then retry
        v = guard.evaluate("rm -rf /", caller_epoch=epoch)

    if v.action == "block":
        log.warning("blocked: %s (risk %s)", v.reason, v.risk_level)
    elif v.action == "redact":
        red = guard.redact("my SSN is 123-45-6789")
        # ... store red.redacted_content, later guard.reveal(content, red.token_map_id)

    chain = guard.audit_verify()
    assert not chain.tampered                # tamper-evidence across 4 chains
```

## Design notes

- **Pure-tech adapter, not policy**: this module owns the wire protocol (frame, send, parse, map errors) — the rules, redaction, and audit chain logic live in fusion-guard. Core stays zero-business.
- **E5 — block is a result, not an error**: `evaluate()` returns `GuardVerdict(action="block")` on a denial. Only RPC-level failures raise typed exceptions. This separates "guard said no" from "guard call failed" — critical for fail-closed callers that must not conflate the two.
- **Import = no I/O**: the default socket path is resolved from `FUSION_GUARD_SOCK` at call time, not import. `import fusion_core.guard_client` reads no env, opens no socket.
- **Native fast path optional**: fusion-guard ships `fg-pyo3` (PyO3 native extension) for a faster in-process path. `guard_client` is the portable pure-Python fallback — no Rust toolchain, works from any Python project.
- **Fail visibly**: unparseable response → `GuardParseError`; truncated framing → `GuardError`; missing chain → empty default (not KeyError), but a missing `verdict` in `confirm` → `GuardError`.
- **Typed errors over magic numbers**: each RPC code is a distinct exception class, so `except GuardUnauthorizedError` / `except StaleEpochError` reads at the call site instead of inspecting `.code`.
