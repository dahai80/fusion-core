from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time

import pytest

from fusion_core.guard_client import (
    AllChainsVerification,
    ChainVerification,
    FusionGuardClient,
    GuardError,
    GuardMethodNotFoundError,
    GuardParseError,
    GuardRule,
    GuardUnauthorizedError,
    GuardVerdict,
    RedactResult,
    StaleEpochError,
)

_FRAMING = b"\n"


class FakeGuardServer:
    def __init__(self, handler):
        self._dir = tempfile.mkdtemp(prefix="guard-test-")
        self.path = os.path.join(self._dir, "guard.sock")
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(self.path)
        self._listener.listen(8)
        self._listener.settimeout(5.0)
        self._handler = handler
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._conns: list[socket.socket] = []

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        with _suppress():
            self._listener.close()
        for c in self._conns:
            with _suppress():
                c.close()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)
        with _suppress():
            os.unlink(self.path)
        with _suppress():
            os.rmdir(self._dir)

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except OSError:
                break
            self._conns.append(conn)
            t = threading.Thread(target=self._handle, args=(conn,), daemon=True)
            t.start()

    def _handle(self, conn: socket.socket):
        conn.settimeout(2.0)
        try:
            while not self._stop.is_set():
                req = self._read_line(conn)
                if req is None:
                    break
                resp = self._handler(json.loads(req.decode("utf-8")))
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            with _suppress():
                conn.close()

    def _read_line(self, conn: socket.socket) -> bytes | None:
        chunks: list[bytes] = []
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                if not chunks:
                    return None
                break
            chunks.append(chunk)
            if chunk[-1:] == _FRAMING:
                break
        return b"".join(chunks).rstrip(_FRAMING)


class _suppress:
    def __enter__(self):
        pass

    def __exit__(self, *exc):
        return exc[0] is OSError


def _ok(result, rid):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(code, msg, rid, data=None):
    e = {"code": code, "message": msg}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": e}


@pytest.fixture
def guard_server():
    server = FakeGuardServer(_default_handler)
    server.start()
    yield server
    server.stop()


def _default_handler(req):
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    if method == "guard.ping":
        return _ok({"pong": True, "version": "0.1.0", "rules_epoch": 7}, rid)
    if method == "guard.evaluate":
        if params.get("content") == "BLOCK-ME":
            return _ok(_verdict_dict("block", risk_level="l3", reason="blocked"), rid)
        return _ok(_verdict_dict("allow"), rid)
    if method == "guard.rule.list":
        return _ok({"rules": [_rule_dict("no-rm", "rm -rf", "block")], "epoch": 7}, rid)
    if method == "guard.redact":
        return _ok({"redacted_content": "hi [REDACTED]", "token_map_id": "tm-1"}, rid)
    if method == "guard.reveal":
        return _ok({"content": "hi secret"}, rid)
    if method == "guard.confirm":
        return _ok({"verdict": _verdict_dict("allow")}, rid)
    if method == "guard.tcc.status":
        return _ok({"statuses": [{"bundle": "id.com.x", "granted": True}]}, rid)
    if method == "guard.tcc.events":
        return _ok({"events": [{"action": "grant", "bundle": "id.com.x"}]}, rid)
    if method == "guard.audit.verify":
        return _ok(
            {
                "audit": {"total_rows": 10, "verified_links": 9, "broken_links": 1, "tampered": True},
                "tcc": {"total_rows": 0},
                "rules": {"total_rows": 5},
                "dead_letter": {"total_rows": 0},
                "tampered": True,
            },
            rid,
        )
    if method == "guard.err.unauthorized":
        return _err(-32001, "unauthorized", rid)
    if method == "guard.err.method":
        return _err(-32601, "method not found", rid)
    if method == "guard.err.stale":
        return _err(-32003, "stale epoch", rid, data={"caller_epoch": 3, "guard_epoch": 9})
    if method == "guard.err.parse":
        return _err(-32700, "parse error", rid)
    return _err(-32601, "unknown method", rid)


def _verdict_dict(action, *, risk_level="l1", reason="ok"):
    return {
        "action": action,
        "risk_level": risk_level,
        "reason": reason,
        "stage": "semantic",
        "requires_approval": False,
        "redacted_content": None,
        "seatbelt_required": False,
        "action_id": "11111111-1111-1111-1111-111111111111",
        "verdict_epoch": 9,
        "verdict_ttl_secs": 60,
        "inferred_category": "shell",
        "category_hint": None,
    }


def _rule_dict(name, pattern, action):
    return {
        "name": name,
        "pattern": pattern,
        "stage": "regex",
        "action": action,
        "risk_level": "l2",
        "reason": "dangerous",
        "scope": "command",
    }


class TestPing:
    def test_ping_roundtrip(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            r = c.ping()
        assert r["pong"] is True
        assert r["version"] == "0.1.0"
        assert r["rules_epoch"] == 7


class TestEvaluate:
    def test_evaluate_allow(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            v = c.evaluate("hello", caller_epoch=9)
        assert v.action == "allow"
        assert v.risk_level == "l1"
        assert v.stage == "semantic"
        assert v.verdict_epoch == 9
        assert v.action_id == "11111111-1111-1111-1111-111111111111"

    def test_evaluate_block_is_result_not_error(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            v = c.evaluate("BLOCK-ME")
        assert v.action == "block"
        assert v.risk_level == "l3"
        assert v.reason == "blocked"

    def test_evaluate_optional_params_omitted_when_none(self, guard_server):
        captured = {}
        orig = _default_handler

        def spy(req):
            captured["params"] = req.get("params") or {}
            return orig(req)

        server = FakeGuardServer(spy)
        server.start()
        try:
            with FusionGuardClient(server.path) as c:
                c.evaluate("hi", tenant_id="t1", requester="bob", content_type="text", category_hint="shell")
        finally:
            server.stop()
        p = captured["params"]
        assert p["tenant_id"] == "t1"
        assert p["requester"] == "bob"
        assert p["content_type"] == "text"
        assert p["category_hint"] == "shell"


class TestListRules:
    def test_list_rules(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            rules, epoch = c.list_rules()
        assert epoch == 7
        assert len(rules) == 1
        r = rules[0]
        assert isinstance(r, GuardRule)
        assert r.name == "no-rm"
        assert r.pattern == "rm -rf"
        assert r.action == "block"
        assert r.scope == "command"


class TestRedactReveal:
    def test_redact(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            res = c.redact("hi secret")
        assert isinstance(res, RedactResult)
        assert res.redacted_content == "hi [REDACTED]"
        assert res.token_map_id == "tm-1"

    def test_reveal(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            content = c.reveal("hi [REDACTED]", token_map_id="tm-1")
        assert content == "hi secret"


class TestConfirm:
    def test_confirm_unwraps_nested_verdict(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            v = c.confirm("action-1", approved=True, approved_by="alice")
        assert isinstance(v, GuardVerdict)
        assert v.action == "allow"
        assert v.verdict_epoch == 9


class TestTcc:
    def test_tcc_status(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            s = c.tcc_status()
        assert s == [{"bundle": "id.com.x", "granted": True}]

    def test_tcc_events(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            e = c.tcc_events(limit=5)
        assert e == [{"action": "grant", "bundle": "id.com.x"}]


class TestAuditVerify:
    def test_audit_verify_maps_chains(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            v = c.audit_verify()
        assert isinstance(v, AllChainsVerification)
        assert v.tampered is True
        assert v.audit.total_rows == 10
        assert v.audit.verified_links == 9
        assert v.audit.broken_links == 1
        assert v.audit.tampered is True
        assert v.tcc.total_rows == 0
        assert v.rules.total_rows == 5
        assert v.dead_letter.total_rows == 0

    def test_audit_verify_missing_chain_defaults(self):
        def handler(req):
            rid = req.get("id")
            return _ok({"audit": {"total_rows": 1}, "tampered": False}, rid)

        server = FakeGuardServer(handler)
        server.start()
        try:
            with FusionGuardClient(server.path) as c:
                v = c.audit_verify()
        finally:
            server.stop()
        assert isinstance(v.tcc, ChainVerification)
        assert v.tcc.total_rows == 0
        assert v.tampered is False


class TestErrorMapping:
    def test_unauthorized(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            with pytest.raises(GuardUnauthorizedError):
                c._call("guard.err.unauthorized")

    def test_method_not_found(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            with pytest.raises(GuardMethodNotFoundError):
                c._call("guard.err.method")

    def test_parse_error(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            with pytest.raises(GuardParseError):
                c._call("guard.err.parse")

    def test_stale_epoch_carries_epochs(self, guard_server):
        with FusionGuardClient(guard_server.path) as c:
            with pytest.raises(StaleEpochError) as exc_info:
                c._call("guard.err.stale")
        e = exc_info.value
        assert e.caller_epoch == 3
        assert e.guard_epoch == 9

    def test_unknown_code_falls_back_to_base(self, guard_server):
        def handler(req):
            return _err(-99999, "weird", req.get("id"))

        server = FakeGuardServer(handler)
        server.start()
        try:
            with FusionGuardClient(server.path) as c:
                with pytest.raises(GuardError) as exc_info:
                    c._call("guard.ping")
        finally:
            server.stop()
        assert "weird" in str(exc_info.value)


class TestFraming:
    def test_request_is_newline_framed(self, guard_server):
        captured = {}
        orig = _default_handler

        def spy(req):
            captured["raw_method"] = req.get("method")
            captured["raw_id"] = req.get("id")
            captured["rpc"] = req.get("jsonrpc")
            return orig(req)

        server = FakeGuardServer(spy)
        server.start()
        try:
            with FusionGuardClient(server.path) as c:
                c.ping()
        finally:
            server.stop()
        assert captured["rpc"] == "2.0"
        assert captured["raw_method"] == "guard.ping"
        assert isinstance(captured["raw_id"], int)


class TestLifecycle:
    def test_context_manager_connects_and_closes(self, guard_server):
        c = FusionGuardClient(guard_server.path)
        assert c._sock is None
        with c:
            assert c._sock is not None
        assert c._sock is None

    def test_unreachable_raises(self):
        with pytest.raises(GuardError):
            with FusionGuardClient("/tmp/nonexistent-guard-sock-xyz123"):
                pass

    def test_env_resolved_at_call_time(self, monkeypatch, guard_server):
        monkeypatch.setenv("FUSION_GUARD_SOCK", guard_server.path)
        c = FusionGuardClient()
        try:
            r = c.ping()
            assert r["pong"] is True
        finally:
            c.close()

    def test_reconnect_after_drop(self):
        dropped = threading.Event()

        def handler(req):
            if not dropped.is_set():
                dropped.set()
                raise OSError("simulated drop")
            return _ok({"pong": True}, req.get("id"))

        server = FakeGuardServer(handler)
        server.start()
        try:
            c = FusionGuardClient(server.path)
            try:
                r = c.ping()
                assert r["pong"] is True
            finally:
                c.close()
        finally:
            server.stop()


class TestTimeout:
    def test_default_timeout_is_two_seconds(self):
        c = FusionGuardClient()
        try:
            assert c._timeout == 2.0
        finally:
            c.close()

    def test_timeout_fires_on_no_response(self):
        def handler(req):
            time.sleep(5.0)
            return _ok({"pong": True}, req.get("id"))

        server = FakeGuardServer(handler)
        server.start()
        try:
            c = FusionGuardClient(server.path, timeout=0.2)
            try:
                start = time.monotonic()
                with pytest.raises((GuardError, OSError)):
                    c.ping()
                elapsed = time.monotonic() - start
                assert elapsed < 1.5
            finally:
                c.close()
        finally:
            server.stop()
