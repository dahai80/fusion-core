---
doc_id: fusion-core/ops-reference
product: fusion-core
class: platform
entry: developer
doc_type: api-reference
title: {zh: "ops 模块", en: "ops module"}
audience: engineer
status: draft
version: 0.15.0
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

Async batched writer to PG `public.ops_events` (schema v1, dahai80/fusion-ops migrations 0001+0002 — issue #58). `source` must satisfy the schema CHECK constraint (`gateway` / `identity` / `commerce` / `cowork` / `autotest` / `support` / `ops`) and is validated at construction (`ValueError` otherwise). `start()` spawns the worker task; `emit()` enqueues; `stop()` drains, final-flushes, reports pending drops, exits.

### Backpressure contract (spec §2 — 禁止阻塞业务)

`emit()` **never blocks and never raises**. On a full queue the event is dropped and counted (`stats.dropped`); drop logs are throttled (first drop + every 100th). On flush failure the whole batch is dropped, counted, the connection closed, and reconnect happens with jittered exponential backoff (0.5-1.5s × 2^n, cap 30s). PG dying mid-run = drops counted + reconnects, never a crash in the caller.

Drops (queue-full and flush-failure) are reported into the schema-v1 `drop_counters` table (`source`, `bucket_day`, `dropped` upsert) after the next successful flush — so the fusion-ops monthly rollup sees silent losses. Reporting is best-effort: a failed report keeps the counter pending and retries on the next cycle; it never crashes the worker or blocks the caller.

### Event field coercion (schema v1)

- `status` — INTEGER in schema v1. Numeric strings are coerced (`"201"` → `201`); non-numeric values become NULL with a warning (one bad event never fails a COPY batch).
- `tenant_hash` — NOT NULL in schema v1; missing value defaults to `""`.
- `source` — per-event override is checked against the schema CHECK set; an invalid override logs a warning and falls back to the emitter's source.

### Methods

- `async start()` — spawn the worker (idempotent; second call logs and returns).
- `async emit(event: dict) -> bool` — `True` accepted, `False` dropped. Event keys: `ts` (default now UTC), `source` (default emitter's), `tenant_hash`, `event_type` (default `"unknown"`), `latency_ms`, `status`, `payload` (dict/list serialized to JSON; str passed through).
- `async stop()` — max latency = `flush_interval`; remaining queue is drained and flushed first.
- `stats` — `EmitterStats(emitted, flushed, dropped, reconnects)`.

### Table schema

Authoritative DDL lives in dahai80/fusion-ops (`migrations/0001_schema_v1.sql` + `0002_ops_source.sql`); the emitter never self-provisions. Target: `public.ops_events` — `status INTEGER`, `tenant_hash NOT NULL DEFAULT ''`, `payload JSONB NOT NULL DEFAULT '{}'`, `source` CHECK-constrained. Companion table `public.drop_counters` (`source`, `bucket_day`, `dropped`, UNIQUE(source, bucket_day)) receives backpressure drop reports.

```python
from fusion_core import OpsEmitter

emitter = OpsEmitter("postgres://ops@localhost:5432/fusion_ops", source="gateway", batch_size=200)
await emitter.start()
await emitter.emit({"event_type": "chat", "latency_ms": 42, "status": 200, "payload": {"model": "qwen"}})
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
