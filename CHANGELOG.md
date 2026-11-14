---
doc_id: fusion-core/changelog
product: fusion-core
class: platform
entry: developer
doc_type: changelog
title: {zh: "Fusion-Core 变更日志", en: "Fusion-Core Changelog"}
audience: engineer
status: draft
version: 0.12.1
updated: 2026-11-14
owner: "@dahai"
source_of_truth:
  - pyproject.toml
---

# Changelog

All notable changes are documented here. Format follows Keep a Changelog; versioning is semver.

## [0.12.1] - 2026-11-14

### Changed
- CI: `release.yml` now publishes stable tags to **PyPI** via Trusted Publisher (tokenless OIDC) — `pip install fusion-core` works outside the monorepo (#52). Prerelease tags (rc/alpha/beta) only ship as GitHub prereleases. No code change.

## [0.12.0] - 2026-11-12

### Added
- mlx_client: `chat(..., extra_body=)` / `stream_chat(..., extra_body=)` — engine-specific payload fields (e.g. `chat_template_kwargs` for GBNF grammar constraints) merged into the request verbatim; shallow merge, explicit params win on collision (conflicting keys warn-and-drop); the unknown-kwarg drop warning stays for typo protection (#49). Unblocks the BNUP K12 training path.
- logging: `extra={}` fields rendered by both formatters — any non-standard `LogRecord` attribute is treated as an extra field (JSON: merged top-level; text: `key=value` suffix, sorted) (#50). Existing `logger.info(..., extra={...})` calls keep their fields.
- logging: `setup_logging(root=True)` — attaches handlers to the root logger for business apps migrating from root-logger (`basicConfig`-style) patterns; idempotent via the same already-configured guard (#50).

## [0.11.0] - 2026-11-12

### Added
- config: `load_dotenv()` — stdlib dotenv loader (zero new deps) with `ENV_FILE` resolution and standard `override=False` semantics (#47). Replaces per-app env-file loaders.
- errors: new `FusionError` base class — stable `code`, operator-facing `remediation`, structured `context` (copied), `to_http()` sanitized API-error payload (#47). Replaces private per-app error trees.
- logging: `setup_logging(max_bytes=, backup_count=)` — size-based `RotatingFileHandler` when `log_file` is set (e.g. 10MB x 3) (#47). Existing 0.10.x API unchanged.

## [0.10.1] - 2026-11-07

### Fixed
- mlx_client: skip null-choice terminator chunks (`{"choices": [null]}` / `delta: null`) in `stream_chat` instead of raising `AttributeError` (#44, #45).

## [0.10.0]

### Added
- mcp_client: thin stdio JSON-RPC client for MCP tool servers, paired with mcp_registry (#42, #43). `isError` is a result, not an error; `env:VAR` prefixed credential values resolve from the operator environment at call time.
- mcp_registry: MCP server approval registry mirroring `architecture/mcp-server-registry-1029.md` (#40, #41). Longest-prefix `check_approval`, `RegistryIntegrityError` on malformed registry, `FUSION_MCP_REGISTRY` path resolution with bundled sidecar fallback.

## [0.9.0]

### Added
- config: `gateway_admin_key()` resolving `FUSION_GATEWAY_API_KEY` -> settings `shell.gateway_admin_key` (#35); `service_port()` / `service_data_dir()` with `fusion-` prefix stripping (#34, #36).
- tenant: `TenantContext.authenticated` flag (#37) — true only for verified-JWT-derived claims.
- proc: `set_process_title` helper, fail-open behind the `[proc]` extra (#30).
- config: config-conventions single-point resolution `client_key()` / `admin_key()` / `profile()` (#31, #32, #33).

## [0.8.0]

### Added
- prompt_registry: immutable content-addressed prompt versioning + replay (#28, #29).

## [0.7.0]

### Added
- dsl_schema: Socratic DSL v3.0 JSON Schema module (Draft 2020-12) for the shared k12/bench/artifacts/trainer contract (#26, #27).

### Fixed
- tenant: async `verify_jwt` hook support — an awaitable return is awaited so a blocking verifier does not stall the event loop (#23, #24, #25).

## [0.6.0]

### Added
- http: `/ready` readiness probe split from `/health` liveness; `create_app(readiness_probe=)` (#21, #22).
- tenant: fail-closed multi-tenant context module — `TenantContext`, `TenantMiddleware`, FastAPI DI (#18).

## Earlier

See git history (`git log --oneline`) for releases before 0.6.0, including guard_client (UDS JSON-RPC client for fusion-guard) and the http_client pool/retry single-point refactor.
