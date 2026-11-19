from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ops_events schema per architecture/fusion-flywheel-1119.md §2:
# id BIGSERIAL, ts, source, tenant_hash, event_type, latency_ms, status, payload JSONB.
_EVENT_COLUMNS = ("ts", "source", "tenant_hash", "event_type", "latency_ms", "status", "payload")

_DDL = """
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
"""


class OpsEmitterDependencyError(RuntimeError):
    def __init__(self):
        super().__init__(
            "OpsEmitter requires asyncpg (not installed). Install fusion-core with the [ops] extra: "
            "pip install 'fusion-core[ops]'"
        )


@dataclass
class EmitterStats:
    emitted: int = 0
    flushed: int = 0
    dropped: int = 0
    reconnects: int = 0


class OpsEmitter:
    # Async batched writer to PG fusion_ops.ops_events (issue #56, flywheel Loop A).
    # Backpressure contract: emit() NEVER blocks the caller — on a full queue the
    # event is dropped and counted (spec §2: backpressure 时丢弃计数不阻塞业务).
    # Flush failures drop the batch, count it, and reconnect with jittered backoff.
    def __init__(
        self,
        dsn: str,
        *,
        source: str = "unknown",
        batch_size: int = 100,
        flush_interval: float = 5.0,
        max_queue: int = 10000,
        connect: Any = None,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if flush_interval <= 0:
            raise ValueError("flush_interval must be > 0")
        self._dsn = dsn
        self._source = source
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._connect = connect  # test hook: async callable returning a connection
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.stats = EmitterStats()

    async def start(self) -> None:
        if self._task is not None:
            logger.warning("OpsEmitter.start: already started")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="fusion-core-ops-emitter")
        logger.info(
            "OpsEmitter started source=%s batch_size=%d flush_interval=%.2fs max_queue=%d",
            self._source,
            self._batch_size,
            self._flush_interval,
            self._queue.maxsize,
        )

    async def emit(self, event: dict) -> bool:
        # Enqueue one ops event. Returns True if accepted, False if dropped.
        # Never raises, never awaits on a full queue (backpressure = drop, issue #56).
        if self._stopping:
            self.stats.dropped += 1
            return False
        try:
            self._queue.put_nowait(event)
            self.stats.emitted += 1
            return True
        except asyncio.QueueFull:
            self.stats.dropped += 1
            # Throttle drop logs: 1-in-100 plus the first drop, keeps caller logs sane.
            if self.stats.dropped == 1 or self.stats.dropped % 100 == 0:
                logger.warning(
                    "OpsEmitter queue full, dropped=%d total (source=%s) — consumer slower than producer",
                    self.stats.dropped,
                    self._source,
                )
            return False

    async def stop(self) -> None:
        # Signal the worker, then wait. Worker drains the queue, does a final flush,
        # and exits. Max latency = flush_interval (remaining events are drained first).
        self._stopping = True
        if self._task is not None:
            try:
                await self._task
            except Exception:
                logger.exception("OpsEmitter worker crashed during stop")
            self._task = None
        logger.info("OpsEmitter stopped source=%s stats=%s", self._source, self.stats)

    async def _connect_db(self):
        if self._connect is not None:
            result = self._connect(self._dsn)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        try:
            import asyncpg
        except ImportError as e:
            raise OpsEmitterDependencyError() from e
        return await asyncpg.connect(self._dsn)

    @staticmethod
    def _to_row(event: dict, source_default: str) -> tuple:
        payload = event.get("payload", {})
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False, default=str)
        ts = event.get("ts") or datetime.now(UTC)
        return (
            ts,
            event.get("source") or source_default,
            event.get("tenant_hash"),
            event.get("event_type") or "unknown",
            event.get("latency_ms"),
            event.get("status"),
            payload,
        )

    async def _run(self) -> None:
        conn = None
        backoff = 1.0
        batch: list[tuple] = []
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval)
                batch.append(self._to_row(item, self._source))
                while len(batch) < self._batch_size and not self._queue.empty():
                    batch.append(self._to_row(self._queue.get_nowait(), self._source))
            except TimeoutError:
                if self._stopping and self._queue.empty() and not batch:
                    if conn is not None:
                        await conn.close()
                    logger.debug("OpsEmitter worker exiting cleanly, stats=%s", self.stats)
                    return
            if not batch:
                continue
            try:
                if conn is None:
                    conn = await self._connect_db()
                    backoff = 1.0
                await conn.copy_records_to_table(
                    "ops_events", records=batch, columns=_EVENT_COLUMNS, schema_name="fusion_ops"
                )
                self.stats.flushed += len(batch)
                logger.debug("OpsEmitter flushed %d events (total flushed=%d)", len(batch), self.stats.flushed)
                batch.clear()
            except Exception:
                # Spec contract: never crash, never block — drop the batch, count it,
                # reconnect with jittered backoff (issue #56 acceptance: kill PG mid-run).
                self.stats.dropped += len(batch)
                logger.exception(
                    "OpsEmitter flush failed, dropped %d events (total dropped=%d), reconnecting",
                    len(batch),
                    self.stats.dropped,
                )
                batch.clear()
                if conn is not None:
                    with contextlib.suppress(Exception):
                        await conn.close()
                    conn = None
                self.stats.reconnects += 1
                await asyncio.sleep(random.uniform(0.5, 1.5) * backoff)
                backoff = min(backoff * 2, 30.0)
