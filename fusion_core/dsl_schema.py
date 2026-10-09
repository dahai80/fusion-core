from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_VISUAL_TYPES = (
    "array_grid",
    "tape_diagram",
    "geometry_2d",
    "isometric_3d",
    "track_timeline",
    "bucket_divider",
    "data_chart",
    "flow_card",
)

_FALLBACK_TYPES = ("tape_diagram", "flow_card")

SOCRATIC_DSL_V3_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Socratic DSL v3.0",
    "description": "Canonical JSON Schema for the Fusion Socratic DSL v3.0 (k12-train-model spec).",
    "type": "object",
    "required": ["meta", "parameters", "entities", "pipeline"],
    "additionalProperties": False,
    "properties": {
        "meta": {
            "type": "object",
            "required": ["id", "grade", "topic", "visual_type", "fallback_type"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "grade": {"type": "number"},
                "topic": {"type": "string"},
                "visual_type": {"type": "string", "enum": list(_VISUAL_TYPES)},
                "fallback_type": {"type": "string", "enum": list(_FALLBACK_TYPES)},
            },
        },
        "parameters": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["label", "value", "unit"],
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": "string"},
                },
            },
        },
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type", "label", "properties"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                    "label": {"type": "string"},
                    "properties": {"type": "object"},
                },
            },
        },
        "pipeline": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["step", "title", "formula", "eval", "result_unit", "visual_state"],
                "additionalProperties": False,
                "properties": {
                    "step": {"type": "number"},
                    "title": {"type": "string"},
                    "formula": {"type": "string"},
                    "eval": {"type": "string"},
                    "result_unit": {"type": "string"},
                    "visual_state": {
                        "type": "object",
                        "required": ["progress_range", "active_entities", "action"],
                        "additionalProperties": False,
                        "properties": {
                            "progress_range": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "active_entities": {"type": "array", "items": {"type": "string"}},
                            "action": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}

_VALIDATOR = None


def _get_validator():
    global _VALIDATOR
    if _VALIDATOR is None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError as exc:
            raise ImportError(
                "dsl_schema requires jsonschema: pip install 'fusion-core[schema]' or pip install jsonschema"
            ) from exc
        _VALIDATOR = Draft202012Validator(SOCRATIC_DSL_V3_SCHEMA)
        logger.debug("dsl_schema validator initialized (jsonschema Draft202012)")
    return _VALIDATOR


def validate_dsl(dsl_dict: dict) -> bool:
    if not isinstance(dsl_dict, dict):
        logger.warning("validate_dsl expects dict, got %s", type(dsl_dict).__name__)
        return False
    validator = _get_validator()
    errors = sorted(validator.iter_errors(dsl_dict), key=lambda e: list(e.path))
    if not errors:
        return True
    first = errors[0]
    path = ".".join(str(p) for p in first.path) or "<root>"
    logger.warning("dsl validation failed at %s: %s", path, first.message)
    return False


def validate_dsl_str(dsl_str: str) -> tuple[bool, str]:
    if not isinstance(dsl_str, str):
        return False, f"expected str, got {type(dsl_str).__name__}"
    if not dsl_str.strip():
        return False, "empty input"
    try:
        parsed = json.loads(dsl_str)
    except json.JSONDecodeError as exc:
        logger.debug("validate_dsl_str json parse fail: %s", exc)
        return False, f"json parse error: {exc}"
    if not isinstance(parsed, dict):
        return False, f"expected dict, got {type(parsed).__name__}"
    validator = _get_validator()
    errors = sorted(validator.iter_errors(parsed), key=lambda e: list(e.path))
    if not errors:
        return True, ""
    first = errors[0]
    path = ".".join(str(p) for p in first.path) or "<root>"
    return False, f"{path}: {first.message}"
