# proc

Process-title helper (issue #30). `setproctitle` is the only reliable title mechanism on macOS — Python.app overwrites `argv[0]` and `exec -a` is unreliable. Fail-open by design: title is cosmetic, service must start.

## Symbols

- [`set_process_title(title)`](#set_process_title)

## set_process_title

```python
def set_process_title(title: str) -> None
```

Sets the OS process title via `setproctitle` (lazy-imported inside the function — `import fusion_core` stays I/O-free and never loads `setproctitle`). Any failure (missing dependency, runtime error) is swallowed with a debug log; the process keeps its default title.

```python
from fusion_core import set_process_title

set_process_title("fusion-rag-service")
```

## Install

`setproctitle` is an optional dependency:

```bash
pip install -e ".[proc]"
```

## Design notes

- Fail-open: a cosmetic title must never block service startup. Failures log at DEBUG (`fusion_core.proc`), never raise.
- Lazy import: `setproctitle` never enters `sys.modules` on plain `import fusion_core` (guarded by `tests/test_proc.py::TestSetProcessTitle::test_import_is_io_free`).
- Call it once at service startup, right before the long-running loop, so `ps` / `top` / supervisor logs show a human-readable name.
