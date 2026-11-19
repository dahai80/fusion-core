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
        self.drop_reports: list[tuple] = []
        self.closed = False

    async def copy_records_to_table(self, table, records=None, columns=None, schema_name=None):
        self.copy_calls += 1
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ConnectionError("pg killed mid-run")
        self.inserted.extend(records)

    async def execute(self, sql, *args):
        # drop_counters upsert lands here (issue #58)
        self.drop_reports.append(args)
        return "INSERT 0 1"

    async def close(self):
        self.closed = True


def _event(n: int) -> dict:
    return {"event_type": "chat", "latency_ms": n, "status": 200, "payload": {"i": n}}


class TestOpsEmitter:
    async def test_batch_insert_non_blocking_10k(self):
        # issue #56 acceptance: 10k events batch-inserted without blocking the caller.
        conn = FakeConn()
        emitter = OpsEmitter(
            "postgres://fake",
            source="gateway",
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
        # rows carry the schema-v1 columns; payload serialized to JSON text
        first = conn.inserted[0]
        assert first[1] == "gateway"
        assert first[3] == "chat"
        assert first[5] == 200
        assert json.loads(first[6]) == {"i": 0}

    async def test_writes_public_schema(self):
        # issue #58: emitter targets public.ops_events (schema_name=None), not legacy fusion_ops.
        conn = FakeConn()
        emitter = OpsEmitter("postgres://fake", source="gateway", flush_interval=0.02, connect=lambda dsn: conn)
        await emitter.start()
        await emitter.emit(_event(1))
        await emitter.stop()

        captured = {}

        async def spy_copy(table, records=None, columns=None, schema_name=None):
            captured.update(table=table, schema_name=schema_name)
            return await conn.copy_records_to_table(table, records=records, columns=columns, schema_name=schema_name)

        conn.copy_records_to_table = spy_copy
        emitter2 = OpsEmitter("postgres://fake", source="gateway", flush_interval=0.02, connect=lambda dsn: conn)
        await emitter2.start()
        await emitter2.emit(_event(2))
        await emitter2.stop()
        assert captured["table"] == "ops_events"
        assert captured["schema_name"] is None

    async def test_backpressure_drops_never_blocks(self):
        # issue #56: full queue -> drop + counter, caller never blocks.
        emitter = OpsEmitter("postgres://fake", source="gateway", max_queue=2)
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
            "postgres://fake", source="gateway", batch_size=10, flush_interval=0.02, max_queue=100, connect=connect
        )
        await emitter.start()
        for i in range(50):
            await emitter.emit(_event(i))
        await emitter.stop()
        assert emitter.stats.dropped > 0, "first flush must drop its batch"
        assert emitter.stats.reconnects >= 1
        assert len(conns[1].inserted) > 0, "post-reconnect events must land"

    async def test_drops_reported_to_drop_counters(self):
        # issue #58: backpressure drops land in drop_counters (source, bucket_day, dropped).
        conn = FakeConn()
        emitter = OpsEmitter("postgres://fake", source="gateway", max_queue=2, connect=lambda dsn: conn)
        await emitter.start()
        for i in range(5):
            await emitter.emit(_event(i))
        await emitter.stop()
        assert emitter.stats.dropped == 3
        assert len(conn.drop_reports) == 1
        source, bucket_day, dropped = conn.drop_reports[0]
        assert source == "gateway"
        assert dropped == 3
        assert bucket_day is not None

    async def test_drop_report_failure_stays_pending(self):
        # report failure must not crash or lose the counter — retried next cycle.
        conn = FakeConn()
        calls = {"n": 0}

        async def flaky_execute(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("pg blip")
            conn.drop_reports.append(args)

        conn.execute = flaky_execute
        emitter = OpsEmitter(
            "postgres://fake", source="gateway", max_queue=2, flush_interval=0.02, connect=lambda dsn: conn
        )
        await emitter.start()
        for i in range(5):
            await emitter.emit(_event(i))
        await emitter.stop()
        assert emitter.stats.dropped == 3
        assert conn.drop_reports, "retry after failure must land the report"
        assert conn.drop_reports[0][2] == 3

    async def test_stop_flushes_remaining(self):
        conn = FakeConn()
        emitter = OpsEmitter(
            "postgres://fake",
            source="gateway",
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

    async def test_source_check_constraint_enforced(self):
        # issue #58: schema v1 CHECK — invalid source fails fast at construction.
        with pytest.raises(ValueError, match="source"):
            OpsEmitter("postgres://fake", source="test-service")
        for allowed in ("gateway", "identity", "commerce", "cowork", "autotest", "support", "ops"):
            OpsEmitter("postgres://fake", source=allowed)

    async def test_event_source_override_validated(self):
        # bad per-event source falls back to the emitter source (warn), never a failed batch
        row = OpsEmitter._to_row({"event_type": "x", "source": "nope"}, "gateway")
        assert row[1] == "gateway"
        assert OpsEmitter._to_row({"event_type": "x", "source": "ops"}, "gateway")[1] == "ops"

    async def test_status_coerced_to_integer(self):
        # issue #58: schema v1 status INTEGER — numeric strings coerced, junk -> NULL
        assert OpsEmitter._to_row({"event_type": "x", "status": "201"}, "gateway")[5] == 201
        assert OpsEmitter._to_row({"event_type": "x", "status": 500}, "gateway")[5] == 500
        assert OpsEmitter._to_row({"event_type": "x", "status": "ok"}, "gateway")[5] is None
        assert OpsEmitter._to_row({"event_type": "x"}, "gateway")[5] is None

    async def test_tenant_hash_defaults_empty(self):
        # issue #58: schema v1 tenant_hash NOT NULL
        assert OpsEmitter._to_row({"event_type": "x"}, "gateway")[2] == ""

    async def test_emit_defaults_and_overrides(self):
        emitter = OpsEmitter("postgres://fake", source="gateway")
        row = emitter._to_row({"event_type": "x"}, "gateway")
        assert row[1] == "gateway"
        row2 = emitter._to_row({"event_type": "x", "source": "identity"}, "gateway")
        assert row2[1] == "identity"
        row3 = emitter._to_row({"payload": {"a": 1}}, "gateway")
        assert row3[3] == "unknown"
        assert json.loads(row3[6]) == {"a": 1}

    async def test_invalid_config_raises(self):
        with pytest.raises(ValueError):
            OpsEmitter("postgres://fake", batch_size=0)
        with pytest.raises(ValueError):
            OpsEmitter("postgres://fake", flush_interval=0)

    async def test_start_twice_warns_not_duplicates(self):
        conn = FakeConn()
        emitter = OpsEmitter("postgres://fake", source="gateway", flush_interval=0.02, connect=lambda dsn: conn)
        await emitter.start()
        task1 = emitter._task
        await emitter.start()
        assert emitter._task is task1
        await emitter.stop()
