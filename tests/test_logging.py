from __future__ import annotations

import json
import logging

from fusion_core.logging import get_logger, setup_logging


class TestSetupLogging:
    def test_idempotent_no_dup_handlers(self):
        logger = setup_logging("fusion_test_idem", level="DEBUG")
        n1 = len(logger.handlers)
        logger2 = setup_logging("fusion_test_idem", level="INFO")
        assert logger is logger2
        assert len(logger2.handlers) == n1

    def test_returns_named_logger(self):
        logger = setup_logging("fusion_test_named")
        assert logger.name == "fusion_test_named"
        assert len(logger.handlers) >= 1

    def test_json_format(self, capsys):
        logger = setup_logging("fusion_test_json", json_format=True)
        logger.info("hello world")
        captured = capsys.readouterr()
        line = captured.err.strip().splitlines()[-1]
        payload = json.loads(line)
        assert payload["msg"] == "hello world"
        assert payload["level"] == "INFO"
        assert payload["name"] == "fusion_test_json"

    def test_log_file(self, tmp_path):
        p = tmp_path / "sub" / "out.log"
        logger = setup_logging("fusion_test_file", log_file=p)
        assert p.parent.exists()
        logger.warning("to file")
        for h in logger.handlers:
            h.flush()
        assert p.exists()
        assert "to file" in p.read_text(encoding="utf-8")


class TestGetLogger:
    def test_child_inherits(self):
        setup_logging("fusion_test_parent", level="WARNING")
        child = get_logger("fusion_test_parent.child")
        assert child.getEffectiveLevel() == logging.WARNING

    def test_setup_on_fusion_core_name_adds_real_handler(self):
        root_logger = logging.getLogger("fusion_core")
        saved_handlers = list(root_logger.handlers)
        saved_propagate = root_logger.propagate
        try:
            logger = setup_logging("fusion_core", level="INFO")
            has_real = any(not isinstance(h, logging.NullHandler) for h in logger.handlers)
            assert has_real, (
                "setup_logging('fusion_core') must add a real handler, not no-op on the package NullHandler"
            )
        finally:
            root_logger.handlers = saved_handlers
            root_logger.propagate = saved_propagate

    def test_default_propagate_true_not_blocking_host_root(self):
        root_logger = logging.getLogger("fusion_test_propagate")
        saved_handlers = list(root_logger.handlers)
        saved_propagate = root_logger.propagate
        try:
            logger = setup_logging("fusion_test_propagate", level="INFO")
            assert logger.propagate is True, "default propagate must be True so host root still receives logs (A6)"
        finally:
            root_logger.handlers = saved_handlers
            root_logger.propagate = saved_propagate
