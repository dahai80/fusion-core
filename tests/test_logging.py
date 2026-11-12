from __future__ import annotations

import json
import logging
import logging.handlers

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


class TestRotatingFileHandler:
    def test_max_bytes_enables_rotation(self, tmp_path):
        log_file = tmp_path / "rot" / "app.log"
        logger = setup_logging("fusion_rot_test", level="INFO", log_file=log_file, max_bytes=200, backup_count=3)
        try:
            handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
            assert len(handlers) == 1
            assert handlers[0].maxBytes == 200
            assert handlers[0].backupCount == 3
            for i in range(20):
                logger.info("rotation probe line %d with some padding text", i)
            for h in logger.handlers:
                h.flush()
            rolled = sorted(p.name for p in log_file.parent.glob("app.log*"))
            assert "app.log" in rolled
            assert len(rolled) > 1, f"expected rotation, got {rolled}"
        finally:
            for h in list(logger.handlers):
                h.close()
                logger.removeHandler(h)

    def test_no_max_bytes_plain_file_handler(self, tmp_path):
        log_file = tmp_path / "plain.log"
        logger = setup_logging("fusion_plain_test", level="INFO", log_file=log_file)
        try:
            assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)
            assert not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers)
        finally:
            for h in list(logger.handlers):
                h.close()
                logger.removeHandler(h)


class TestExtraFields:
    # issue #50: extra={} fields rendered by both formatters.

    def test_json_formatter_renders_extra_fields(self, capsys):
        logger = setup_logging("fusion_test_extra_json", json_format=True)
        logger.info("handled", extra={"request_id": "r1", "attempt": 2})
        captured = capsys.readouterr()
        payload = json.loads(captured.err.strip().splitlines()[-1])
        assert payload["request_id"] == "r1"
        assert payload["attempt"] == 2
        assert payload["msg"] == "handled"
        logger.handlers.clear()

    def test_text_formatter_renders_extra_fields(self, capsys):
        logger = setup_logging("fusion_test_extra_text")
        logger.info("handled", extra={"request_id": "r1"})
        captured = capsys.readouterr()
        line = captured.err.strip().splitlines()[-1]
        assert "handled" in line
        assert "request_id='r1'" in line
        logger.handlers.clear()

    def test_no_extra_fields_unchanged_output(self, capsys):
        logger = setup_logging("fusion_test_extra_none", json_format=True)
        logger.info("plain")
        captured = capsys.readouterr()
        payload = json.loads(captured.err.strip().splitlines()[-1])
        assert "request_id" not in payload
        logger.handlers.clear()


class TestRootMode:
    # issue #50: root-logger mode for legacy-pattern app migration.

    def test_root_mode_attaches_to_root_logger(self, capsys):
        # pytest attaches its own root handler, which would trip the
        # already-configured guard — save and clear root handlers first.
        saved = logging.getLogger().handlers[:]
        for h in saved:
            logging.getLogger().removeHandler(h)
        try:
            logger = setup_logging(root=True)
            assert logger is logging.getLogger()
            assert len(logger.handlers) >= 1
            logger.info("from root")
            captured = capsys.readouterr()
            assert "from root" in captured.err
        finally:
            for h in logging.getLogger().handlers[:]:
                logging.getLogger().removeHandler(h)
                h.close()
            for h in saved:
                logging.getLogger().addHandler(h)

    def test_root_mode_idempotent(self):
        saved = logging.getLogger().handlers[:]
        for h in saved:
            logging.getLogger().removeHandler(h)
        try:
            logger1 = setup_logging(root=True)
            n1 = len(logger1.handlers)
            logger2 = setup_logging(root=True)
            assert logger1 is logger2
            assert len(logger2.handlers) == n1
        finally:
            for h in logging.getLogger().handlers[:]:
                logging.getLogger().removeHandler(h)
                h.close()
            for h in saved:
                logging.getLogger().addHandler(h)

    def test_named_mode_still_works_after_root(self, capsys):
        logger = setup_logging("fusion_test_after_root")
        logger.info("named still fine")
        captured = capsys.readouterr()
        assert "named still fine" in captured.err
        logger.handlers.clear()
