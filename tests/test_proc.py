from __future__ import annotations

import builtins
import logging
import subprocess
import sys

from fusion_core import set_process_title
from fusion_core.proc import set_process_title as module_set_process_title


class TestSetProcessTitle:
    def test_sets_title_real(self):
        import setproctitle

        set_process_title("fusion-test-proc-helper")
        try:
            assert setproctitle.getproctitle() == "fusion-test-proc-helper"
        finally:
            setproctitle.setproctitle("pytest")

    def test_module_import_identity(self):
        assert module_set_process_title is set_process_title

    def test_missing_dependency_fail_open(self, monkeypatch, caplog):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "setproctitle":
                raise ImportError("No module named 'setproctitle'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        caplog.set_level(logging.DEBUG, logger="fusion_core.proc")
        set_process_title("fusion-test-should-not-crash")
        assert any("setproctitle unavailable" in r.message for r in caplog.records)

    def test_import_is_io_free(self):
        code = "import fusion_core, sys; print('setproctitle' in sys.modules)"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert out.stdout.strip() == "False"
