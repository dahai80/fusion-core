# logging

Idempotent logging init. Library-mode `NullHandler`, host-root propagation preserved, optional JSON.

## Symbols

- [`setup_logging(name, *, level, json_format, log_file, propagate)`](#setup_logging)
- [`get_logger(name)`](#get_logger)

## setup_logging

```python
def setup_logging(
    name: str,
    *,
    level: str = "INFO",
    json_format: bool = False,
    log_file: str | Path | None = None,
    propagate: bool = True,
) -> logging.Logger
```

Configures logger `name`. Idempotent: if a non-`NullHandler` handler already exists, only applies `setLevel` (no duplicate handler). Otherwise removes prior `NullHandler`, adds a `StreamHandler(sys.stderr)` with the chosen formatter. Optional `log_file` adds a `FileHandler`.

- `level` — string name (`"INFO"`, `"DEBUG"`, ...); invalid → `ValueError`.
- `json_format=True` — `_JsonFormatter`: `{"ts","level","name","msg"}` (+`exc` if present), `ensure_ascii=False`, ISO-8601 UTC.
- `propagate=True` (default, A6) — logger bubbles to host root, so a host's unified JSON collector receives fusion-core logs. The old unconditional `propagate=False` broke cross-module log correlation; do not set `False` unless you intentionally detach.

```python
from fusion_core import setup_logging
setup_logging("fusion_core", level="DEBUG", json_format=True, log_file="/tmp/svc.log")
```

## get_logger

```python
def get_logger(name: str) -> logging.Logger
```

Thin `logging.getLogger(name)`. Use for child loggers (`fusion_core.mlx_client` etc.) — they inherit the parent's handler/level via propagation.

```python
from fusion_core import get_logger
log = get_logger(__name__)
log.info("chat ok model=%s", model)
```

## Design notes

- Package root `fusion_core` gets a `NullHandler` at import (`__init__.py`) — standard library-mode, suppresses "no handler" warnings without configuring output.
- `propagate=True` default (A6): the library does **not** decide propagation for the host. Host configures root; fusion-core logs flow there. Set `propagate=False` only for standalone demo scripts.
- Idempotent re-init: safe to call `setup_logging("x")` multiple times; only first call installs the StreamHandler, every call applies `setLevel`.
