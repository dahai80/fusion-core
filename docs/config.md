# config

Lazy settings load, api_key resolution, mtime-invalidated cache. No I/O at import. Since v0.7.0 also the single-point resolution layer for `architecture/config-conventions.md` (issues #31/#32): global secrets, profile, gateway URL, per-service port/data-dir.

## Symbols

- [`DEFAULT_MLX_PORT`](#constants)
- [`default_mlx_base_url()`](#default_mlx_base_url)
- [`get_env(key, default, *, cast)`](#get_env)
- [`load_settings(path)`](#load_settings)
- [`resolve_api_key(explicit, *, env_var, settings_path)`](#resolve_api_key)
- [`load_api_key(name)`](#load_api_key)
- [`clear_cache()`](#clear_cache)
- [`profile()`](#profile)
- [`client_key()`](#client_key)
- [`admin_key()`](#admin_key)
- [`gateway_base_url()`](#gateway_base_url)
- [`gateway_admin_key()`](#gateway_admin_key)
- [`service_port(app, default)`](#service_port)
- [`service_data_dir(app, default)`](#service_data_dir)

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

# config-conventions layer (v0.7.0, issues #31/#32)

Single-point resolution for `architecture/config-conventions.md`. Domain apps call these instead of hand-rolling `os.environ` lookups — CI (`scripts/check-env.py`) flags bespoke `_resolve_*key` helpers.

## profile

```python
def profile() -> str
```

Returns `FUSION_PROFILE` env value, default `"dev"`. `dev` = lenient (missing secrets warn, return `""`); `prod` = fail-fast (`RuntimeError`). No import-time read.

## client_key

```python
def client_key() -> str
```

Client secret for gateway auth. Resolution order (alias chain, config-conventions §2): `FUSION_CLIENT_KEY` → `FUSION_MLX_API_KEY` → `MLX_API_KEY`. Legacy names log a deprecation warning pointing at config-conventions §6.

- dev + none set: warn-once ("anonymous dev mode", module-level `_ANON_WARNED` set) and return `""`
- prod + none set: `RuntimeError` with 3 parts — var name, "obtain from gateway admin API", doc path

## admin_key

```python
def admin_key() -> str
```

Admin secret via `FUSION_ADMIN_KEY`. No alias chain (admin key has no legacy names). Same prod fail-fast / dev warn-once semantics as `client_key`.

## gateway_base_url

```python
def gateway_base_url() -> str
```

`FUSION_GATEWAY_BASE_URL` → legacy `FUSION_GATEWAY_URL` (deprecation warning) → default `http://127.0.0.1:11432` (dev default per config-conventions §1).

## gateway_admin_key

```python
def gateway_admin_key(settings_path: str | Path | None = None) -> str
```

Gateway admin secret (issue #35 — the key fusion-gateway `:11432` actually accepts on admin-authenticated deployments). Resolution order: `FUSION_GATEWAY_API_KEY` env → `shell.gateway_admin_key` in settings.json (default `~/.fusion-mlx/settings.json`). Malformed/missing settings, non-dict `shell`, null/non-string key → graceful skip with debug/warning logs.

- dev + nothing found: warn-once ("anonymous dev mode") and return `""`
- prod + nothing found: `RuntimeError` with 3 parts — var name, "obtain from gateway admin API", doc path

```python
key = gateway_admin_key()  # FUSION_GATEWAY_API_KEY or shell.gateway_admin_key
```

## service_port

```python
def service_port(app: str, default: int | None = None) -> int
```

Reads `FUSION_<APP>_PORT` — the `fusion-` prefix is **stripped first** (issues #34/#36), so `"rag"` and `"fusion-rag"` resolve the same env var `FUSION_RAG_PORT` (never the doubled `FUSION_FUSION_RAG_PORT`). App name upper-cased, dashes to underscores. Env wins over `default`. Non-integer env → `ValueError`. Unset + no default → `ValueError` hinting to register the port in `architecture/port-registry.yaml`.

```python
port = service_port("fusion-rag", default=11501)  # FUSION_RAG_PORT or 11501
```

## service_data_dir

```python
def service_data_dir(app: str, default: str | Path | None = None) -> Path
```

Reads `FUSION_<APP>_DATA_DIR` (prefix stripped, same as `service_port`) → explicit `default` → `~/.<short>` where `<short>` strips the `fusion-` prefix (`fusion-rag` → `~/.rag`). Returns a `Path`. Services whose established runtime dir differs (e.g. fusion-mlx uses `~/.fusion-mlx`) pass an explicit `default`.

## Conventions-layer design notes

- Zero-downtime migration: legacy alias chains keep existing deployments working while warning (Phase 2 of config-conventions §6); aliases are deleted in Phase 3.
- Prefix normalization (issues #34/#36): `service_port`/`service_data_dir` strip a leading `fusion-` before building the env name, so the canonical full repo name and the bare short name resolve identically.
- Warn-once: repeated anonymous dev-mode calls log one warning per env var per process.
- Prod fail-fast message is 3-part by design (var name + where to obtain + doc path) — enforced by `tests/test_config_conventions.py`.
- All functions read env at call time; `import fusion_core` remains I/O-free.
