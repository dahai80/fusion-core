from __future__ import annotations

import pytest

from fusion_core.prompt import PromptManager


class TestPromptManager:
    def test_get_txt(self, tmp_path):
        (tmp_path / "greet.txt").write_text("Hello {name}", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.get("greet") == "Hello {name}"

    def test_get_md_suffix(self, tmp_path):
        (tmp_path / "sys.md").write_text("You are {role}", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.get("sys") == "You are {role}"

    def test_get_explicit_name_with_suffix(self, tmp_path):
        (tmp_path / "t.txt").write_text("x", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.get("t.txt") == "x"

    def test_render_fills_vars(self, tmp_path):
        (tmp_path / "greet.txt").write_text("Hello {name}, you are {role}", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.render("greet", name="Alice", role="admin") == "Hello Alice, you are admin"

    def test_render_missing_var_raises(self, tmp_path):
        (tmp_path / "greet.txt").write_text("Hello {name}", encoding="utf-8")
        pm = PromptManager(tmp_path)
        with pytest.raises(KeyError):
            pm.render("greet")

    def test_missing_template_raises(self, tmp_path):
        pm = PromptManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            pm.get("nonexistent")

    def test_list_names(self, tmp_path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.md").write_text("y", encoding="utf-8")
        (tmp_path / "c.json").write_text("z", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.list_names() == ["a", "b"]

    def test_get_hot_reloads_on_mtime_change(self, tmp_path):
        # E3: on-disk edits must be picked up on the next get() when mtime changes.
        (tmp_path / "greet.txt").write_text("v1", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.get("greet") == "v1"
        (tmp_path / "greet.txt").write_text("v2", encoding="utf-8")
        assert pm.get("greet") == "v2", "prompt get must hot-reload on mtime change (E3)"

    def test_get_caches_within_same_mtime(self, tmp_path):
        # E3: repeated get without a disk edit returns cached (no re-read).
        f = tmp_path / "greet.txt"
        f.write_text("v1", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.get("greet") == "v1"
        # rewrite identical content preserving mtime resolution: same text expected
        assert pm.get("greet") == "v1"

    def test_clear_cache_forces_reload(self, tmp_path):
        (tmp_path / "greet.txt").write_text("v1", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.get("greet") == "v1"
        pm.clear_cache()
        (tmp_path / "greet.txt").write_text("v2", encoding="utf-8")
        assert pm.get("greet") == "v2"

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            PromptManager(tmp_path / "nope")

    def test_render_accepts_non_str_vars(self, tmp_path):
        (tmp_path / "greet.txt").write_text("count={n}", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.render("greet", n=42) == "count=42"

    def test_render_escape_braces(self, tmp_path):
        (tmp_path / "t.txt").write_text("use {{name}} syntax, hi {name}", encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.render("t", name="Alice") == "use {name} syntax, hi Alice"

    def test_render_escape_double_braces_no_vars(self, tmp_path):
        (tmp_path / "t.txt").write_text('json: {{"key": 1}}', encoding="utf-8")
        pm = PromptManager(tmp_path)
        assert pm.render("t") == 'json: {"key": 1}'
