# config

Lazy settings load, api_key resolution, mtime-invalidated cache. No I/O at import.

## Symbols

- [`DEFAULT_MLX_PORT`](#constants)
- [`default_mlx_base_url()`](#default_mlx_base_url)
- [`get_env(key, default, *, cast)`](#get_env)
- [`load_settings(path)`](#load_settings)
- [`resolve_api_key(explicit, *, env_var, settings_path)`](#resolve_api_key)
- [`load_api_key(name)`](#load_api_key)
- [`clear_cache()`](#clear_cache)

## Constants

- `DEFAULT_MLX_PORT = 11434` — aligned with fusion-mlx `start.sh`.
- `_BOOL_TRUE = {"1","true","yes","on","y","t"}`, `_BOOL_FALSE = {"0","false","no","off","n","f"}` — `""` removed (dead branch; empty string returns `default` at entry).

## default_mlx_base_url

```python
def default_mlx_base_url() -> str
```

Returns `os.environ.get("FUSION_MLX_URL", f"http://localhost:{DEFAULT_MLX_PORT}/v1")`. Read at **call** time, not import time. Point `FUSION_MLX_URL` at fusion-gateway (`http://<gateway-host>:11432/v1`) for multi-node.

## get_env

```python
def get_env(key: str, default: Any = None, *, cast: type = str) -> Any
```

Reads `os.environ[key]`. Empty/None → `default`. `cast=bool` → case-insensitive membership in `_BOOL_TRUE`. Other casts (`int`, `float`) → `cast(raw)`, on error logs warning and returns `default`.

```python
get_env("PORT", default=8080, cast=int)
get_env("DEBUG", default=False, cast=bool)
```

## load_settings

```python
def load_settings(path: str | Path | None = None) -> dict
```

Loads `~/.fusion-mlx/settings.json` (or `path`). Cache keyed by path string, value `(data, mtime)`. On hit, compares cached mtime vs current `st_mtime` — if changed, re-reads (R1: hot config rotation, e.g. api_key rollover, takes effect without restart).

Raises:
- `FileNotFoundError` — file missing (`from None`, ruff-clean B904)
- `json.JSONDecodeError` — malformed JSON (re-raised after warning log)
- `ValueError` — JSON not an object

```python
cfg = load_settings()
api_key = cfg.get("auth", {}).get("api_key", "")
```

## resolve_api_key

```python
def resolve_api_key(
    explicit: str | None = None,
    *,
    env_var: str = "FUSION_MLX_API_KEY",
    settings_path: str | Path | None = None,
) -> str
```

Resolution order: `explicit` → `os.environ[env_var]` → `settings.json` `auth.api_key`. Missing settings.json → returns `""` (debug log). Malformed auth (non-dict, null, non-str) → warning + `""`. No `or ""` — explicit empty-string branches.

```python
key = resolve_api_key()  # FUSION_MLX_API_KEY env or settings.json
```

## load_api_key

```python
def load_api_key(name: str = "fusion-mlx") -> str
```

Derives env var from `name` (`fusion-health` → `FUSION_HEALTH_API_KEY`), resolves via `resolve_api_key`. Raises `KeyError` if no key found (fail visibly — no silent empty).

```python
key = load_api_key("fusion-health")  # FUSION_HEALTH_API_KEY or settings.json
```

## clear_cache

```python
def clear_cache() -> None
```

Clears `_SETTINGS_CACHE`. Use in tests to avoid cross-test pollution.

## Design notes

- Cache invalidation by mtime (R1): config rotation effective without process restart.
- Import-time I/O isolation: `default_mlx_base_url()` reads env at call, guarded by `tests/test_config.py::TestImportTimeIsolation` env-get spy.
- `_BOOL_FALSE` dead branch `""` removed (I7): empty string returns `default` at entry, never reaches bool cast.
