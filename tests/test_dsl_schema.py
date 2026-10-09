from __future__ import annotations

import copy
import json

import pytest

from fusion_core import SOCRATIC_DSL_V3_SCHEMA, validate_dsl, validate_dsl_str
from fusion_core.dsl_schema import _get_validator

VALID_DSL = {
    "meta": {
        "id": "k12-geom-001",
        "grade": 6,
        "topic": "cylinder volume",
        "visual_type": "isometric_3d",
        "fallback_type": "flow_card",
    },
    "parameters": {
        "base_area": {"label": "底面积", "value": 12.0, "unit": "cm²"},
        "height": {"label": "高", "value": 5.0, "unit": "cm"},
    },
    "entities": [
        {"id": "cyl", "type": "shape", "label": "圆柱", "properties": {"r": 2.0}},
    ],
    "pipeline": [
        {
            "step": 1,
            "title": "总体积",
            "formula": "V=S*h",
            "eval": "params.base_area * params.height",
            "result_unit": "cm³",
            "visual_state": {
                "progress_range": [0, 0.5],
                "active_entities": ["cyl"],
                "action": "grow",
            },
        },
        {
            "step": 2,
            "title": "削去体积",
            "formula": "V_cut=V*2/3",
            "eval": "results[1] * (2/3)",
            "result_unit": "cm³",
            "visual_state": {
                "progress_range": [0.5, 1.0],
                "active_entities": ["cyl"],
                "action": "cut",
            },
        },
    ],
}


@pytest.fixture(autouse=True)
def _reset_validator_cache():
    import fusion_core.dsl_schema as mod

    saved = mod._VALIDATOR
    mod._VALIDATOR = None
    yield
    mod._VALIDATOR = saved


def test_schema_dict_shape():
    assert SOCRATIC_DSL_V3_SCHEMA["type"] == "object"
    assert SOCRATIC_DSL_V3_SCHEMA["required"] == ["meta", "parameters", "entities", "pipeline"]
    meta_props = SOCRATIC_DSL_V3_SCHEMA["properties"]["meta"]["properties"]
    assert len(meta_props["visual_type"]["enum"]) == 8
    assert meta_props["fallback_type"]["enum"] == ["tape_diagram", "flow_card"]


def test_valid_dsl_passes():
    assert validate_dsl(copy.deepcopy(VALID_DSL)) is True


def test_valid_dsl_str_passes():
    ok, err = validate_dsl_str(json.dumps(VALID_DSL))
    assert ok is True
    assert err == ""


def test_missing_top_level_key():
    dsl = copy.deepcopy(VALID_DSL)
    del dsl["entities"]
    assert validate_dsl(dsl) is False
    ok, err = validate_dsl_str(json.dumps(dsl))
    assert ok is False
    assert "entities" in err or "required" in err


def test_invalid_visual_type_enum():
    dsl = copy.deepcopy(VALID_DSL)
    dsl["meta"]["visual_type"] = "hologram"
    assert validate_dsl(dsl) is False


def test_invalid_fallback_type_enum():
    dsl = copy.deepcopy(VALID_DSL)
    dsl["meta"]["fallback_type"] = "array_grid"
    assert validate_dsl(dsl) is False


@pytest.mark.parametrize("missing", ["step", "title", "formula", "eval", "result_unit", "visual_state"])
def test_pipeline_step_missing_required(missing):
    dsl = copy.deepcopy(VALID_DSL)
    del dsl["pipeline"][0][missing]
    assert validate_dsl(dsl) is False


def test_visual_state_missing_required():
    dsl = copy.deepcopy(VALID_DSL)
    del dsl["pipeline"][0]["visual_state"]["action"]
    assert validate_dsl(dsl) is False


def test_progress_range_wrong_length():
    dsl = copy.deepcopy(VALID_DSL)
    dsl["pipeline"][0]["visual_state"]["progress_range"] = [0]
    assert validate_dsl(dsl) is False


def test_progress_range_non_number():
    dsl = copy.deepcopy(VALID_DSL)
    dsl["pipeline"][0]["visual_state"]["progress_range"] = [0, "1"]
    assert validate_dsl(dsl) is False


def test_parameter_value_wrong_type():
    dsl = copy.deepcopy(VALID_DSL)
    dsl["parameters"]["base_area"]["value"] = "twelve"
    assert validate_dsl(dsl) is False


def test_entity_missing_properties():
    dsl = copy.deepcopy(VALID_DSL)
    del dsl["entities"][0]["properties"]
    assert validate_dsl(dsl) is False


def test_validate_dsl_non_dict():
    assert validate_dsl(["not", "a", "dict"]) is False
    assert validate_dsl("string") is False
    assert validate_dsl(None) is False


def test_validate_dsl_str_non_str():
    ok, err = validate_dsl_str(123)  # type: ignore[arg-type]
    assert ok is False
    assert "str" in err


def test_validate_dsl_str_empty():
    ok, err = validate_dsl_str("   ")
    assert ok is False
    assert "empty" in err


def test_validate_dsl_str_bad_json():
    ok, err = validate_dsl_str("{not json")
    assert ok is False
    assert "json parse" in err


def test_validate_dsl_str_json_array_not_object():
    ok, err = validate_dsl_str("[1, 2, 3]")
    assert ok is False
    assert "dict" in err


def test_validate_dsl_str_reports_path():
    dsl = copy.deepcopy(VALID_DSL)
    dsl["meta"]["grade"] = "six"
    ok, err = validate_dsl_str(json.dumps(dsl))
    assert ok is False
    assert "meta.grade" in err


def test_validator_cached():
    v1 = _get_validator()
    v2 = _get_validator()
    assert v1 is v2


def test_all_visual_types_accepted():
    for vt in [
        "array_grid",
        "tape_diagram",
        "geometry_2d",
        "isometric_3d",
        "track_timeline",
        "bucket_divider",
        "data_chart",
        "flow_card",
    ]:
        dsl = copy.deepcopy(VALID_DSL)
        dsl["meta"]["visual_type"] = vt
        assert validate_dsl(dsl) is True, f"visual_type={vt} should pass"


def test_additional_properties_rejected():
    dsl = copy.deepcopy(VALID_DSL)
    dsl["meta"]["extra_field"] = "x"
    assert validate_dsl(dsl) is False
