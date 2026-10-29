from __future__ import annotations

import json
import os

import pytest

from fusion_core import mcp_registry
from fusion_core.mcp_registry import (
    ApprovalVerdict,
    McpServerRecord,
    RegistryIntegrityError,
    RegistryLookupError,
    check_approval,
    load_registry,
    resolve_server,
)


def _write_registry(path, servers, version=1):
    path.write_text(json.dumps({"version": version, "servers": servers}), encoding="utf-8")
    return path


def _server(**overrides):
    base = {
        "name": "test-server",
        "transport": "http",
        "purpose": "testing",
        "credential_mode": "identity_issuance",
        "status": "approved",
        "url_prefixes": ["http://127.0.0.1:20000"],
        "tool_scopes": ["test.read", "test.write", "sheet.*"],
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _fresh_cache():
    mcp_registry.clear_cache()
    yield
    mcp_registry.clear_cache()


class TestLoadRegistry:
    def test_loads_servers(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        servers = load_registry(path)
        assert len(servers) == 1
        record = servers[0]
        assert isinstance(record, McpServerRecord)
        assert record.name == "test-server"
        assert record.status == "approved"
        assert record.credential_mode == "identity_issuance"
        assert record.url_prefixes == ("http://127.0.0.1:20000",)
        assert record.tool_scopes == ("test.read", "test.write", "sheet.*")
        assert record.port is None

    def test_mtime_cache_hit_and_invalidation(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        first = load_registry(path)
        second = load_registry(path)
        assert first == second
        assert len(mcp_registry._REGISTRY_CACHE) == 1
        # mtime granularity: force a different mtime before rewriting
        st = path.stat()
        os.utime(path, (st.st_atime, st.st_mtime + 10))
        _write_registry(path, [_server(), _server(name="other")])
        third = load_registry(path)
        assert len(third) == 2

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="mcp server registry not found"):
            load_registry(tmp_path / "nope.json")

    def test_env_override_path(self, tmp_path, monkeypatch):
        path = _write_registry(tmp_path / "env-reg.json", [_server(name="env-server")])
        monkeypatch.setenv("FUSION_MCP_REGISTRY", str(path))
        servers = load_registry()
        assert servers[0].name == "env-server"

    def test_default_path_when_env_unset(self, monkeypatch):
        # env unset -> monorepo sidecar when present, else the bundled package copy
        monkeypatch.delenv("FUSION_MCP_REGISTRY", raising=False)
        resolved = mcp_registry.default_registry_path()
        monorepo = (
            mcp_registry.Path(mcp_registry.__file__).resolve().parents[2] / "architecture" / "mcp-server-registry.json"
        )
        bundled = mcp_registry.Path(mcp_registry.__file__).resolve().parent / "mcp-server-registry.json"
        assert resolved in (monorepo, bundled)
        assert resolved.is_file()

    def test_loads_real_registry_sidecar(self, monkeypatch):
        # the shipped sidecar (monorepo or bundled) parses and validates — works in CI
        # (bundled copy) and in the dev monorepo layout alike
        monkeypatch.delenv("FUSION_MCP_REGISTRY", raising=False)
        servers = load_registry()
        names = {record.name for record in servers}
        assert "fusion-cowork-mcp" in names
        for record in servers:
            assert record.status in mcp_registry.REGISTRY_STATUS_LIFECYCLE
            assert record.credential_mode in mcp_registry.CREDENTIAL_MODES


class TestRegistryIntegrity:
    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "reg.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(RegistryIntegrityError, match="not valid json"):
            load_registry(path)

    def test_non_object_raises(self, tmp_path):
        path = tmp_path / "reg.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(RegistryIntegrityError, match="must be a json object"):
            load_registry(path)

    def test_unsupported_version_raises(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [], version=2)
        with pytest.raises(RegistryIntegrityError, match="unsupported registry version"):
            load_registry(path)

    def test_missing_servers_list_raises(self, tmp_path):
        path = tmp_path / "reg.json"
        path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        with pytest.raises(RegistryIntegrityError, match="'servers' list"):
            load_registry(path)

    def test_non_object_entry_raises(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", ["nope"])
        with pytest.raises(RegistryIntegrityError, match="servers\\[0\\] must be an object"):
            load_registry(path)

    @pytest.mark.parametrize(
        ("overrides", "match"),
        [
            ({"name": ""}, "non-empty string 'name'"),
            ({"name": 5}, "non-empty string 'name'"),
            ({"status": "bogus"}, "status"),
            ({"credential_mode": "hardcoded"}, "credential_mode"),
            ({"url_prefixes": "http://x"}, "url_prefixes must be a list"),
            ({"url_prefixes": [1]}, "url_prefixes must be a list"),
            ({"tool_scopes": {"a": 1}}, "tool_scopes must be a list"),
            ({"port": "11438"}, "port must be an int"),
            ({"transport": 7}, "transport must be a string"),
        ],
    )
    def test_bad_fields_raise(self, tmp_path, overrides, match):
        path = _write_registry(tmp_path / "reg.json", [_server(**overrides)])
        with pytest.raises(RegistryIntegrityError, match=match):
            load_registry(path)

    def test_duplicate_names_raise(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server(), _server()])
        with pytest.raises(RegistryIntegrityError, match="duplicate server name"):
            load_registry(path)


class TestResolveServer:
    def test_resolves_by_name(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server(), _server(name="other")])
        record = resolve_server("test-server", registry_path=path)
        assert isinstance(record, McpServerRecord)
        assert record.name == "test-server"

    def test_unknown_name_raises_lookup(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        with pytest.raises(RegistryLookupError, match="'ghost' not in registry"):
            resolve_server("ghost", registry_path=path)

    def test_empty_name_raises_value(self, tmp_path):
        with pytest.raises(ValueError, match="name required"):
            resolve_server("")


class TestCheckApproval:
    def test_approved_exact_scope(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        verdict = check_approval("http://127.0.0.1:20000/mcp", "test.read", registry_path=path)
        assert verdict == ApprovalVerdict(
            action="approved",
            server="test-server",
            reason="tool scope 'test.read' approved for 'test-server'",
        )

    def test_url_trailing_slash_normalized(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        verdict = check_approval("http://127.0.0.1:20000/", "test.write", registry_path=path)
        assert verdict.action == "approved"

    def test_prefix_wildcard_scope(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        verdict = check_approval("http://127.0.0.1:20000", "sheet.write", registry_path=path)
        assert verdict.action == "approved"

    def test_wildcard_all_scope(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server(tool_scopes=["*"])])
        verdict = check_approval("http://127.0.0.1:20000", "anything.at.all", registry_path=path)
        assert verdict.action == "approved"

    def test_scope_not_approved_denied(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        verdict = check_approval("http://127.0.0.1:20000", "admin.delete", registry_path=path)
        assert verdict.action == "denied"
        assert verdict.server == "test-server"
        assert "not in approved scopes" in verdict.reason

    def test_unmatched_url_unknown(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        verdict = check_approval("http://evil.example.com/mcp", "test.read", registry_path=path)
        assert verdict.action == "unknown"
        assert verdict.server is None

    def test_non_approved_status_denied(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server(status="deprecated")])
        verdict = check_approval("http://127.0.0.1:20000", "test.read", registry_path=path)
        assert verdict.action == "denied"
        assert "status is 'deprecated'" in verdict.reason

    def test_longest_prefix_wins(self, tmp_path):
        servers = [
            _server(
                name="short",
                url_prefixes=["http://127.0.0.1:20000"],
                tool_scopes=["other.read"],
            ),
            _server(
                name="long",
                url_prefixes=["http://127.0.0.1:20000/deep"],
                tool_scopes=["deep.read"],
            ),
        ]
        path = _write_registry(tmp_path / "reg.json", servers)
        verdict = check_approval("http://127.0.0.1:20000/deep/x", "deep.read", registry_path=path)
        assert verdict.action == "approved"
        assert verdict.server == "long"

    def test_no_url_prefixes_never_matches(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server(url_prefixes=[])])
        verdict = check_approval("http://127.0.0.1:20000", "test.read", registry_path=path)
        assert verdict.action == "unknown"

    def test_empty_url_raises(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        with pytest.raises(ValueError, match="url required"):
            check_approval("", "test.read", registry_path=path)

    def test_empty_scope_raises(self, tmp_path):
        path = _write_registry(tmp_path / "reg.json", [_server()])
        with pytest.raises(ValueError, match="tool_scope required"):
            check_approval("http://127.0.0.1:20000", "", registry_path=path)


class TestImportIsolation:
    def test_no_import_time_env_read(self):
        # import fusion_core performs no I/O (hard design rule); the registry env
        # read is call-time only. Reload-based spies are forbidden here — reload
        # rebinds the module namespace's classes and poisons isinstance checks
        # in every later test (module-level bindings still point at the old class).
        # Guard instead: default_registry_path reads env at call time.
        import fusion_core

        assert hasattr(fusion_core, "mcp_registry")
        # call-time resolution: different env values produce different paths
        saved = os.environ.get("FUSION_MCP_REGISTRY")
        try:
            os.environ.pop("FUSION_MCP_REGISTRY", None)
            base = mcp_registry.default_registry_path()
            os.environ["FUSION_MCP_REGISTRY"] = "/tmp/custom-registry.json"
            assert mcp_registry.default_registry_path() == mcp_registry.Path("/tmp/custom-registry.json")
        finally:
            if saved is None:
                os.environ.pop("FUSION_MCP_REGISTRY", None)
            else:
                os.environ["FUSION_MCP_REGISTRY"] = saved
        assert base.name == "mcp-server-registry.json"
