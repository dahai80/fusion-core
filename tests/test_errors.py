from __future__ import annotations

import pytest

from fusion_core import FusionError


class TestFusionError:
    def test_defaults(self):
        err = FusionError("boom")
        assert err.code == "fusion_error"
        assert err.http_status == 500
        assert err.remediation == ""
        assert err.context == {}
        assert str(err) == "boom"

    def test_subclass_defaults_and_instance_overrides(self):
        class NotFound(FusionError):
            code = "not_found"
            http_status = 404
            remediation = "check the resource id"

        err = NotFound("thing missing", context={"id": "x1"})
        assert err.code == "not_found"
        assert err.http_status == 404
        assert err.remediation == "check the resource id"
        assert err.context == {"id": "x1"}

        custom = NotFound("thing missing", code="widget_missing", http_status=410)
        assert custom.code == "widget_missing"
        assert custom.http_status == 410

    def test_to_http_payload(self):
        err = FusionError(
            "bad input", code="bad_input", http_status=400, remediation="fix it", context={"field": "email"}
        )
        assert err.to_http() == {
            "status": 400,
            "code": "bad_input",
            "message": "bad input",
            "remediation": "fix it",
            "context": {"field": "email"},
        }

    def test_context_is_copied(self):
        ctx = {"a": 1}
        err = FusionError("m", context=ctx)
        ctx["b"] = 2
        assert err.context == {"a": 1}

    def test_exception_chaining_preserved(self):
        with pytest.raises(FusionError) as ei:
            try:
                raise ValueError("upstream broke")
            except ValueError as e:
                raise FusionError("wrapped", context={"step": 2}) from e
        assert isinstance(ei.value.__cause__, ValueError)

    def test_is_exception(self):
        with pytest.raises(Exception, match="boom"):
            raise FusionError("boom")
