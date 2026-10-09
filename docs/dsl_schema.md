# dsl_schema

Canonical JSON Schema for the **Socratic DSL v3.0** — the shared contract across fusion-k12-teacher (generate), fusion-bench (evaluate), fusion-artifacts-engine (parse), and fusion-trainer (sample validation).

Source spec: `architecture/k12-train-model-autumation-1009.md` (§"JSON Schema 定义 socratic_dsl_v3.json").

## Symbols

- [`SOCRATIC_DSL_V3_SCHEMA`](#socratic_dsl_v3_schema)
- [`validate_dsl(dsl_dict)`](#validate_dsl)
- [`validate_dsl_str(dsl_str)`](#validate_dsl_str)

## SOCRATIC_DSL_V3_SCHEMA

```python
SOCRATIC_DSL_V3_SCHEMA: dict
```

A JSON Schema (Draft 2020-12) dict. Top-level required keys: `meta`, `parameters`, `entities`, `pipeline`.

### meta

Required: `id` (string), `grade` (number), `topic` (string), `visual_type` (enum, 8 values), `fallback_type` (enum, 2 values).

| Field | Enum values |
|-------|-------------|
| `visual_type` | `array_grid`, `tape_diagram`, `geometry_2d`, `isometric_3d`, `track_timeline`, `bucket_divider`, `data_chart`, `flow_card` |
| `fallback_type` | `tape_diagram`, `flow_card` |

### parameters

A map of `key → {label (string), value (number), unit (string)}`.

### entities

Array of `{id (string), type (string), label (string), properties (object)}`.

### pipeline

Array of steps. Each step required: `step` (number), `title` (string), `formula` (string), `eval` (string), `result_unit` (string), `visual_state`.

`visual_state` required: `progress_range` (array of 2 numbers), `active_entities` (array of strings), `action` (string).

All objects use `additionalProperties: false` — the contract is closed; unknown fields are rejected so consumers can rely on the exact shape.

## validate_dsl

```python
def validate_dsl(dsl_dict: dict) -> bool
```

Validate a parsed DSL dict against `SOCRATIC_DSL_V3_SCHEMA`. Returns `True` if valid, `False` otherwise (the first error is logged at WARNING level with its JSON path). Non-dict input returns `False`.

```python
from fusion_core import validate_dsl

ok = validate_dsl(dsl_dict)
if not ok:
    # log already has the reason; reject the sample
    ...
```

## validate_dsl_str

```python
def validate_dsl_str(dsl_str: str) -> tuple[bool, str]
```

Parse a JSON string then validate. Returns `(True, "")` on success, or `(False, error_msg)` where `error_msg` is either a parse error or a schema error prefixed with the JSON path (e.g. `meta.grade: 'six' is not of type 'number'`).

```python
from fusion_core import validate_dsl_str

ok, err = validate_dsl_str(raw_llm_output)
if not ok:
    log.warning("dsl rejected: %s", err)
```

## Dependency

`jsonschema` is imported lazily inside the validators — `import fusion_core` stays I/O-free and adds no hard dependency. To enable validation, install the extra:

```bash
pip install -e "fusion-core[schema]"
```

Calling `validate_dsl` / `validate_dsl_str` without `jsonschema` installed raises `ImportError` with a clear install hint.
