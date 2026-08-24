from __future__ import annotations

import pytest

from fusion_core.parse import (
    ParseError,
    parse_llm_json,
    parse_llm_json_lenient,
    parse_llm_json_safe,
    strip_code_fence,
)


class TestStripCodeFence:
    def test_plain_json(self):
        assert strip_code_fence('{"a": 1}') == '{"a": 1}'

    def test_json_fence(self):
        text = '```json\n{"a": 1}\n```'
        assert strip_code_fence(text) == '{"a": 1}'

    def test_plain_fence(self):
        text = '```\n{"a": 1}\n```'
        assert strip_code_fence(text) == '{"a": 1}'

    def test_empty(self):
        assert strip_code_fence("") == ""

    def test_multi_fence_strips_outer_pair_only(self):
        text = '```json\n{"a": 1}\n```\n说明文字\n```json\n{"b": 2}\n```'
        out = strip_code_fence(text)
        assert out.startswith('{"a": 1}'), "outer open fence stripped, first object exposed"
        assert out.endswith('{"b": 2}'), "outer close fence stripped, last object exposed"

    def test_multi_fence_lenient_extracts_first_object(self):
        text = '```json\n{"a": 1}\n```\n说明文字\n```json\n{"b": 2}\n```'
        assert parse_llm_json_lenient(text) == {"a": 1}

    def test_single_fence_not_stripped(self):
        text = '```\n{"a": 1}'
        assert strip_code_fence(text) == '```\n{"a": 1}'

    def test_lenient_large_brace_text_has_scan_cap(self):
        big = "{" * 50000 + "not json" + "}" * 50000
        with pytest.raises(ParseError):
            parse_llm_json_lenient(big)


class TestParseStrict:
    def test_plain_dict(self):
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_list(self):
        assert parse_llm_json("[1, 2, 3]") == [1, 2, 3]

    def test_fenced(self):
        assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_malformed_raises(self):
        with pytest.raises(ParseError):
            parse_llm_json("not json at all")

    def test_empty_raises(self):
        with pytest.raises(ParseError):
            parse_llm_json("")

    def test_non_string_raises(self):
        with pytest.raises(TypeError):
            parse_llm_json(None)  # type: ignore[arg-type]

    def test_no_silent_fallback(self):
        with pytest.raises(ParseError):
            parse_llm_json("解释: 结果如下 {bad}")

    def test_scalar_int_rejected(self):
        with pytest.raises(ParseError):
            parse_llm_json("123")

    def test_scalar_null_rejected(self):
        with pytest.raises(ParseError):
            parse_llm_json("null")

    def test_scalar_bool_rejected(self):
        with pytest.raises(ParseError):
            parse_llm_json("true")


class TestParseLenient:
    def test_extracts_brace_with_text(self):
        text = '结果如下:\n{"score": 90}\n结束'
        assert parse_llm_json_lenient(text) == {"score": 90}

    def test_extracts_bracket_with_text(self):
        text = "列表:\n[1, 2, 3]\n结束"
        assert parse_llm_json_lenient(text) == [1, 2, 3]

    def test_fenced_still_works(self):
        assert parse_llm_json_lenient('```json\n{"a": 1}\n```') == {"a": 1}

    def test_no_json_raises(self):
        with pytest.raises(ParseError):
            parse_llm_json_lenient("纯文本无任何 json 结构")

    def test_extracts_first_of_multiple_objects(self):
        text = 'first {"a": 1} noise {"b": 2} end'
        assert parse_llm_json_lenient(text) == {"a": 1}

    def test_nested_object_extracted(self):
        assert parse_llm_json_lenient('wrap {"a": {"x": 1}} tail') == {"a": {"x": 1}}


class TestParseSafe:
    def test_returns_default_on_fail(self):
        assert parse_llm_json_safe("not json", default={"fallback": True}) == {"fallback": True}

    def test_returns_parsed_on_success(self):
        assert parse_llm_json_safe('{"a": 1}', default={}) == {"a": 1}

    def test_default_none_raises(self):
        with pytest.raises(TypeError):
            parse_llm_json_safe("bad", default=None)  # type: ignore[arg-type]

    def test_default_returned_preserves_type(self):
        result = parse_llm_json_safe("bad", default=[])
        assert result == []

    def test_default_scalar_raises(self):
        with pytest.raises(TypeError):
            parse_llm_json_safe("bad", default=42)  # type: ignore[arg-type]
