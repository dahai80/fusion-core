from __future__ import annotations

import json

import pytest

from fusion_core.ops import OpsEmitter


class FakeConn:
    # Test double for an asyncpg connection. `fail_first` flushes raise once,
    # simulating PG dying mid-run (issue #56 acceptance: drops counted, reconnects).
    def __init__(self, fail_first: int = 0):
        self.fail_first = fail_first
        self.inserted: list[tuple] = []
        self.copy_calls = 0
        self.closed = False

    async def copy_records_to_table(self, table, records=None, columns=None, schema_name=None):
        self.copy_calls += 1
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ConnectionError("pg killed mid-run")
        self.inserted.extend(records)

    async def close(self):
        self.closed = True


def _event(n: int) -> dict:
    return {"event_type": "chat", "latency_ms": n, "status": "ok", "payload": {"i": n}}


class TestOpsEmitter:
    async def test_batch_insert_non_blocking_10k(self):
        # issue #56 acceptance: 10k events batch-inserted without blocking the caller.
        conn = FakeConn()
        emitter = OpsEmitter(
            "postgres://fake",
            source="test",
            batch_size=500,
            flush_interval=0.02,
            max_queue=20000,
            connect=lambda dsn: conn,
        )
        await emitter.start()
        for i in range(10000):
            assert await emitter.emit(_event(i)) is True
        await emitter.stop()
        assert len(conn.inserted) == 10000
        assert emitter.stats.flushed == 10000
        assert emitter.stats.dropped == 0
        assert emitter.stats.emitted == 10000
        # rows carry the flywheel schema columns; payload serialized to JSON text
        first = conn.inserted[0]
        assert first[1] == "test"
        assert first[3] == "chat"
        assert json.loads(first[6]) == {"i": 0}

    async def test_backpressure_drops_never_blocks(self):
        # issue #56: full queue -> drop + counter, caller never blocks.
        emitter = OpsEmitter("postgres://fake", source="test", max_queue=2)
        # no start(): nothing consumes the queue
        assert await emitter.emit(_event(1)) is True
        assert await emitter.emit(_event(2)) is True
        assert await emitter.emit(_event(3)) is False
        assert await emitter.emit(_event(4)) is False
        assert emitter.stats.dropped == 2
        assert emitter.stats.emitted == 2

    async def test_pg_death_drops_counted_then_reconnects(self):
        # issue #56 acceptance: kill PG mid-run -> drops counted, no crash, reconnects.
        conns = [FakeConn(fail_first=1), FakeConn()]
        calls = {"n": 0}

        async def connect(dsn):
            conn = conns[min(calls["n"], len(conns) - 1)]
            calls["n"] += 1
            return conn

        emitter = OpsEmitter(
            "postgres://fake", source="test", batch_size=10, flush_interval=0.02, max_queue=100, connect=connect
        )
        await emitter.start()
        for i in range(50):
            await emitter.emit(_event(i))
        await emitter.stop()
        assert emitter.stats.dropped > 0, "first flush must drop its batch"
        assert emitter.stats.reconnects >= 1
        assert len(conns[1].inserted) > 0, "post-reconnect events must land"

    async def test_stop_flushes_remaining(self):
        conn = FakeConn()
        emitter = OpsEmitter(
            "postgres://fake",
            source="test",
            batch_size=1000,
            flush_interval=0.02,
            max_queue=100,
            connect=lambda dsn: conn,
        )
        await emitter.start()
        for i in range(7):
            await emitter.emit(_event(i))
        await emitter.stop()
        assert len(conn.inserted) == 7
        assert conn.closed

    async def test_emit_defaults_and_overrides(self):
        emitter = OpsEmitter("postgres://fake", source="svc-default")
        row = emitter._to_row({"event_type": "x"}, "svc-default")
        assert row[1] == "svc-default"
        row2 = emitter._to_row({"event_type": "x", "source": "svc-override"}, "svc-default")
        assert row2[1] == "svc-override"
        row3 = emitter._to_row({"payload": {"a": 1}}, "s")
        assert row3[3] == "unknown"
        assert json.loads(row3[6]) == {"a": 1}

    async def test_invalid_config_raises(self):
        with pytest.raises(ValueError):
            OpsEmitter("postgres://fake", batch_size=0)
        with pytest.raises(ValueError):
            OpsEmitter("postgres://fake", flush_interval=0)

    async def test_start_twice_warns_not_duplicates(self):
        conn = FakeConn()
        emitter = OpsEmitter("postgres://fake", source="test", flush_interval=0.02, connect=lambda dsn: conn)
        await emitter.start()
        task1 = emitter._task
        await emitter.start()
        assert emitter._task is task1
        await emitter.stop()
