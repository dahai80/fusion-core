from __future__ import annotations

import json

import pytest

from fusion_core.prompt import PromptManager
from fusion_core.prompt_registry import (
    PromptVersionError,
    PromptVersionRegistry,
    SnapshotIntegrityError,
    UnknownPromptVersionError,
    compute_version_id,
    is_valid_version_id,
)


@pytest.fixture
def registry(tmp_path):
    return PromptVersionRegistry(tmp_path / "prompt_registry")


class TestVersionId:
    def test_deterministic(self):
        assert compute_version_id("greet", "hello {name}") == compute_version_id("greet", "hello {name}")

    def test_name_scoped(self):
        assert compute_version_id("a", "text") != compute_version_id("b", "text")

    def test_text_scoped(self):
        assert compute_version_id("a", "text1") != compute_version_id("a", "text2")

    def test_is_valid(self):
        vid = compute_version_id("a", "text")
        assert is_valid_version_id(vid)
        assert not is_valid_version_id("short")
        assert not is_valid_version_id("Z" * 16)
        assert not is_valid_version_id(123)  # type: ignore[arg-type]


class TestRegister:
    def test_register_returns_record(self, registry):
        rec = registry.register("greet", "hello {name}", source="unit-test")
        assert rec.name == "greet"
        assert rec.version_id == compute_version_id("greet", "hello {name}")
        assert rec.template_text == "hello {name}"
        assert rec.source == "unit-test"
        assert rec.created_at

    def test_snapshot_written(self, registry, tmp_path):
        rec = registry.register("greet", "hello {name}")
        snap = tmp_path / "prompt_registry" / "versions" / f"{rec.version_id}.txt"
        assert snap.read_text(encoding="utf-8") == "hello {name}"

    def test_idempotent_dedupe(self, registry):
        r1 = registry.register("greet", "hello {name}")
        r2 = registry.register("greet", "hello {name}")
        assert r1.version_id == r2.version_id
        assert len(registry.list_versions("greet")) == 1

    def test_edit_creates_new_version(self, registry):
        v1 = registry.register("greet", "hello {name}")
        v2 = registry.register("greet", "hello {name}!")
        assert v1.version_id != v2.version_id
        versions = registry.list_versions("greet")
        assert [v.version_id for v in versions] == [v1.version_id, v2.version_id]

    def test_history_immutable(self, registry, tmp_path):
        v1 = registry.register("greet", "hello {name}")
        registry.register("greet", "hello {name}!")
        snap = tmp_path / "prompt_registry" / "versions" / f"{v1.version_id}.txt"
        assert snap.read_text(encoding="utf-8") == "hello {name}"

    def test_index_persisted(self, registry, tmp_path):
        registry.register("greet", "hello {name}")
        index_path = tmp_path / "prompt_registry" / "index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(data["versions"]) == 1
        assert data["versions"][0]["name"] == "greet"

    def test_persistence_across_instances(self, tmp_path):
        r1 = PromptVersionRegistry(tmp_path / "reg")
        rec = r1.register("greet", "hello {name}")
        r2 = PromptVersionRegistry(tmp_path / "reg")
        assert r2.get_record(rec.version_id).template_text == "hello {name}"

    def test_invalid_name(self, registry):
        with pytest.raises(ValueError):
            registry.register("", "text")

    def test_invalid_text(self, registry):
        with pytest.raises(ValueError):
            registry.register("greet", 123)  # type: ignore[arg-type]


class TestRender:
    def test_render_materializes(self, registry):
        rec = registry.register("greet", "hello {name}, score {score}")
        out = registry.render(rec.version_id, name="bob", score=9)
        assert out == "hello bob, score 9"

    def test_render_escapes_braces(self, registry):
        rec = registry.register("json_out", 'return {{"a": 1}} for {x}')
        assert registry.render(rec.version_id, x="y") == 'return {"a": 1} for y'

    def test_render_missing_variable_raises(self, registry):
        rec = registry.register("greet", "hello {name}")
        with pytest.raises(KeyError):
            registry.render(rec.version_id)

    def test_render_matches_prompt_manager(self, registry, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "greet.txt").write_text("hello {name}", encoding="utf-8")
        manager = PromptManager(prompts_dir)
        rec = registry.register_from_manager(manager, "greet")
        assert registry.render(rec.version_id, name="bob") == manager.render("greet", name="bob")

    def test_render_old_version_after_edit(self, registry):
        v1 = registry.register("greet", "hello {name}")
        registry.register("greet", "hi {name}")
        assert registry.render(v1.version_id, name="bob") == "hello bob"


class TestReplay:
    def test_replay_uses_client(self, registry, mocker):
        rec = registry.register("greet", "hello {name}")
        client = mocker.MagicMock()
        registry.replay(rec.version_id, client, model="test-model", name="bob")
        client.chat.assert_called_once_with("hello bob", model="test-model")

    def test_replay_default_model(self, registry, mocker):
        rec = registry.register("greet", "hello {name}")
        client = mocker.MagicMock()
        registry.replay(rec.version_id, client, name="bob")
        client.chat.assert_called_once_with("hello bob", model=None)


class TestIntegrity:
    def test_unknown_version_raises(self, registry):
        with pytest.raises(UnknownPromptVersionError):
            registry.get_record("0" * 16)

    def test_tampered_snapshot_detected(self, registry, tmp_path):
        rec = registry.register("greet", "hello {name}")
        snap = tmp_path / "prompt_registry" / "versions" / f"{rec.version_id}.txt"
        snap.write_text("tampered", encoding="utf-8")
        with pytest.raises(SnapshotIntegrityError):
            registry.get_record(rec.version_id)

    def test_missing_snapshot_detected(self, registry, tmp_path):
        rec = registry.register("greet", "hello {name}")
        snap = tmp_path / "prompt_registry" / "versions" / f"{rec.version_id}.txt"
        snap.unlink()
        with pytest.raises(SnapshotIntegrityError):
            registry.get_record(rec.version_id)

    def test_malformed_index_raises(self, tmp_path):
        reg_dir = tmp_path / "reg"
        reg_dir.mkdir()
        (reg_dir / "index.json").write_text("not json", encoding="utf-8")
        with pytest.raises(PromptVersionError):
            PromptVersionRegistry(reg_dir)

    def test_index_wrong_shape_raises(self, tmp_path):
        reg_dir = tmp_path / "reg"
        reg_dir.mkdir()
        (reg_dir / "index.json").write_text('{"versions": "oops"}', encoding="utf-8")
        with pytest.raises(PromptVersionError):
            PromptVersionRegistry(reg_dir)


class TestListAndLatest:
    def test_list_all_and_filter(self, registry):
        a1 = registry.register("a", "one")
        b1 = registry.register("b", "two")
        assert {r.version_id for r in registry.list_versions()} == {a1.version_id, b1.version_id}
        assert [r.version_id for r in registry.list_versions("a")] == [a1.version_id]

    def test_latest(self, registry):
        registry.register("greet", "v1")
        v2 = registry.register("greet", "v2")
        assert registry.latest("greet").version_id == v2.version_id

    def test_latest_unknown(self, registry):
        with pytest.raises(UnknownPromptVersionError):
            registry.latest("nope")

    def test_reload_picks_up_external_writes(self, tmp_path):
        r1 = PromptVersionRegistry(tmp_path / "reg")
        r2 = PromptVersionRegistry(tmp_path / "reg")
        rec = r2.register("greet", "hello")
        with pytest.raises(UnknownPromptVersionError):
            r1.get_record(rec.version_id)
        r1.reload()
        assert r1.get_record(rec.version_id).template_text == "hello"


class TestRegisterFromManager:
    def test_registers_current_template(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "greet.txt").write_text("hello {name}", encoding="utf-8")
        manager = PromptManager(prompts_dir)
        registry = PromptVersionRegistry(tmp_path / "reg")
        rec = registry.register_from_manager(manager, "greet")
        assert rec.source.startswith("prompt_manager:")
        assert rec.template_text == "hello {name}"

    def test_hot_edit_creates_new_version(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        p = prompts_dir / "greet.txt"
        p.write_text("hello {name}", encoding="utf-8")
        manager = PromptManager(prompts_dir)
        registry = PromptVersionRegistry(tmp_path / "reg")
        v1 = registry.register_from_manager(manager, "greet")
        p.write_text("hi {name}", encoding="utf-8")
        v2 = registry.register_from_manager(manager, "greet")
        assert v1.version_id != v2.version_id

    def test_bad_manager_raises(self, tmp_path):
        registry = PromptVersionRegistry(tmp_path / "reg")
        with pytest.raises(TypeError):
            registry.register_from_manager(object(), "greet")
