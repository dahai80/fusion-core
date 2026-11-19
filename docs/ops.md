---
doc_id: fusion-core/ops-reference
product: fusion-core
class: platform
entry: developer
doc_type: api-reference
title: {zh: "ops 模块", en: "ops module"}
audience: engineer
status: draft
version: 0.14.0
updated: 2026-11-19
owner: "@dahai"
source_of_truth:
  - fusion_core/ops.py
---

# ops

Async ops-event emitter — the shared telemetry write path for the flywheel plan (issue #56, `architecture/fusion-flywheel-1119.md` §2). All fusion-* services emit through this module; nobody hand-rolls SQL.

## Symbols

- [`OpsEmitter(dsn, *, source, batch_size, flush_interval, max_queue, connect)`](#opsemitter)
- [`EmitterStats`](#emitterstats)
- [`OpsEmitterDependencyError`](#opsemitterdependencyerror)

## OpsEmitter

```python
OpsEmitter(
    dsn: str,
    *,
    source: str = "unknown",
    batch_size: int = 100,
    flush_interval: float = 5.0,
    max_queue: int = 10000,
    connect: Any = None,
)
```

Async batched writer to PG `fusion_ops.ops_events`. `start()` spawns the worker task; `emit()` enqueues; `stop()` drains, final-flushes, exits.

### Backpressure contract (spec §2 — 禁止阻塞业务)

`emit()` **never blocks and never raises**. On a full queue the event is dropped and counted (`stats.dropped`); drop logs are throttled (first drop + every 100th). On flush failure the whole batch is dropped, counted, the connection closed, and reconnect happens with jittered exponential backoff (0.5-1.5s × 2^n, cap 30s). PG dying mid-run = drops counted + reconnects, never a crash in the caller.

### Methods

- `async start()` — spawn the worker (idempotent; second call logs and returns).
- `async emit(event: dict) -> bool` — `True` accepted, `False` dropped. Event keys: `ts` (default now UTC), `source` (default emitter's), `tenant_hash`, `event_type` (default `"unknown"`), `latency_ms`, `status`, `payload` (dict/list serialized to JSON; str passed through).
- `async stop()` — max latency = `flush_interval`; remaining queue is drained and flushed first.
- `stats` — `EmitterStats(emitted, flushed, dropped, reconnects)`.

### Table schema

```sql
CREATE TABLE IF NOT EXISTS fusion_ops.ops_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT NOT NULL,
    tenant_hash TEXT,
    event_type TEXT NOT NULL,
    latency_ms INTEGER,
    status TEXT,
    payload JSONB
)
```

```python
from fusion_core import OpsEmitter

emitter = OpsEmitter("postgres://ops@localhost:5432/fusion_ops", source="gateway", batch_size=200)
await emitter.start()
await emitter.emit({"event_type": "chat", "latency_ms": 42, "status": "ok", "payload": {"model": "qwen"}})
...
await emitter.stop()
```

### Dependency

`asyncpg` is lazy-imported at first connect — install the `[ops]` extra (`pip install "fusion-core[ops]"`). Missing dep surfaces as `OpsEmitterDependencyError` from `start()`-time flush, not at import (import stays I/O-free).

The `connect=` hook (async or sync callable `(dsn) -> connection`) is a test seam — tests inject fake connections; production code never passes it.

## EmitterStats

Dataclass: `emitted` (accepted by queue), `flushed` (rows inserted), `dropped` (queue-full + flush-failure drops), `reconnects`.

## OpsEmitterDependencyError

`RuntimeError` subclass raised when `asyncpg` is not installed. Message names the fix (`[ops]` extra).
