# prompt_registry

Immutable prompt-template versioning + replay — closes the reproducibility gap (issue #28): every AI output can be recorded with `(trace_id, prompt_version, model_version)` and re-materialized later from `(prompt_version, input)`. Cross-app reusable (originated from ai-speech-exam gap G3, contains no business logic).

## Symbols

- [`PromptVersionRegistry`](#promptversionregistry)
- [`PromptVersionRecord`](#promptversionrecord)
- [`compute_version_id(name, template_text)`](#compute_version_id)
- [`is_valid_version_id(prompt_version)`](#is_valid_version_id)
- Exceptions: `PromptVersionError` (base), `UnknownPromptVersionError` (also a `KeyError`), `SnapshotIntegrityError`

## PromptVersionRegistry

```python
PromptVersionRegistry(registry_dir: str | Path)
```

Content-addressed version store. On-disk layout (all plain files, human-inspectable):

```
registry_dir/
  index.json            # lineage: [{name, version_id, sha256, created_at, source}, ...]
  versions/<id>.txt     # immutable template snapshots, one per version
```

- `version_id` = first 16 hex of `sha256(name + "\x00" + template_text)` — deterministic, so registering the same content twice is a no-op (dedupe), and any edit yields a new ID.
- Snapshots are write-once: an existing snapshot file is never overwritten. Index writes are atomic (`tmp` + `os.replace`).
- Every read re-hashes the snapshot and compares against the recorded `sha256` — a tampered or missing snapshot raises `SnapshotIntegrityError`, never a silently-wrong replay.

### Methods

| Method | Behavior |
|--------|----------|
| `register(name, template_text, *, source="manual")` | Compute `version_id`; return existing record on dedupe; else write snapshot + append index entry. Returns `PromptVersionRecord` |
| `register_from_manager(manager, name)` | Convenience: snapshot the current on-disk template of a `PromptManager` (`source` records the prompts dir). Hot edits (mtime change) register as new versions |
| `get_record(prompt_version)` | Load record; raises `UnknownPromptVersionError` if the ID is not in the index |
| `render(prompt_version, **variables)` | Re-materialize the exact prompt. Same `{{var}}` semantics as `PromptManager.render` (shared helper), so a replay is byte-identical to the original send. Missing variable raises `KeyError` |
| `replay(prompt_version, client, *, model=None, **variables)` | Render + re-run through the injected `FusionMLXClient` (`client.chat(prompt, model=model)`). Route through fusion-gateway by pointing the client's `base_url` at `:11432`. Client is injected — the registry holds no engine config |
| `list_versions(name=None)` | All records, or filtered by name, in registration order |
| `latest(name)` | Most recently registered record for `name`; raises `UnknownPromptVersionError` if none |
| `reload()` | Re-read `index.json` (pick up external writes from another process) |

## PromptVersionRecord

Frozen-slots dataclass: `name`, `version_id`, `sha256`, `template_text`, `created_at` (UTC ISO), `source`. `to_metadata()` returns the storable subset (everything except `template_text`).

## compute_version_id

```python
def compute_version_id(name: str, template_text: str) -> str
```

The version-ID scheme itself, exported so callers can predict IDs before registering (e.g. to check "is this content already versioned?").

## is_valid_version_id

```python
def is_valid_version_id(prompt_version: str) -> bool
```

Format check: 16 lowercase hex chars. Use to validate IDs arriving from external systems (logs, DB rows) before hitting the registry.

## Usage

```python
from fusion_core import FusionMLXClient, PromptManager, PromptVersionRegistry

manager = PromptManager("app/prompts")
registry = PromptVersionRegistry("app/prompt_registry")

# snapshot the current template whenever it changes (cron, deploy hook, or per-run)
record = registry.register_from_manager(manager, "scoring")
print(record.version_id, record.created_at)  # store with your trace: (trace_id, prompt_version, model_version)

# replay: exact same prompt bytes, any time later
prompt = registry.render(record.version_id, essay="...")

# replay + re-run through fusion-mlx (or fusion-gateway :11432)
client = FusionMLXClient(base_url="http://localhost:11434/v1")
resp = registry.replay(record.version_id, client, essay="...")
```

## Design notes

- **No new dependencies** — stdlib `hashlib` / `json` / `os.replace` only. Importing `fusion_core` stays I/O-free.
- **Immutability by construction**: snapshots are content-addressed (same ID = same bytes, verified by sha256), so history cannot be rewritten without detection.
- **Fail visibly**: unknown ID, tampered snapshot, malformed index all raise — never a best-effort fallback.
