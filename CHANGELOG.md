# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: SemVer; pre-1.0 minor bumps may break.

## [Unreleased]

## [0.1.3] - 2026-05-02

### Fixed
- CI's `uv sync --frozen` no longer fails on the missing
  `../nnels` editable path. The local-only nnels dep is
  moved to a non-default `local-plugins` dependency group
  that operators install manually with
  `uv sync --group local-plugins` when they want the
  editable plugin. Default sync (CI, fresh-checkout devs)
  skips it. The CI test job has been failing with this
  error since v0.1.1; now it passes.

## [0.1.2] - 2026-05-02

Standardise the FastAPI health and docs endpoints to
match the cobdfamily microservice fleet conventions.

### Changed
- Liveness moved from `GET /health` to `GET /`.
  The new payload is
  `{"service": "hummingbird", "status": "ok"}` (no
  more `version` field — `/openapi.json` already
  exposes that).
- ReDoc moved from the FastAPI default `/redoc` to
  `/redocs` (trailing s) via
  `redoc_url="/redocs"` on the `FastAPI()`
  constructor. Swagger UI stays at `/docs`.
- `DEPLOYMENT.md` "Verify" curl now hits `/`
  instead of the (never-existed)
  `/protocols/hummingbird/v1/health`.

### Removed
- `GET /health` — replaced by `GET /`.

## [0.1.1] - 2026-04-28

First containerised release. Brings hummingbird into
the cobdfamily project shape (CI, kibble registry,
Dockerfile, docs).

### Added
- `Dockerfile` — two-stage uv build,
  `python:3.12-slim` runtime, non-root user, uvicorn
  as PID 1. Operators should bind-mount `/app/data`
  (bookshelves / sessions / bookmarks) and
  `/app/cache` (audio cache) so state survives
  rebuilds.
- `.dockerignore` — keeps secrets, the data dir, the
  audio cache, and tests out of the build context.
  README.md is whitelisted because hatchling reads it
  during `uv sync`.
- `.github/workflows/test.yml` — ruff lint gates
  pytest with coverage; `coverage.xml` uploaded as an
  artifact.
- `.github/workflows/release.yml` — pushes a container
  image to
  `kibble.apps.blindhub.ca/cobdfamily/hummingbird` on
  every `git tag v*`. Plain HTTP, anonymous push.
- `CHANGELOG.md` (this file).
- `DEPLOYMENT.md` — production deploy checklist.
- README test-workflow status badge + DEPLOYMENT
  link.

### Fixed
- `src/hummingbird/protocols/kados/methods.py` had a
  module-level `import json` near line 194 that ruff
  flagged as `E402` (not at top of file). Hoisted to
  the imports block.
- `src/hummingbird/download.py` had an unused
  `urlparse` import. Ruff `--fix` removed it.

## [0.1.0] - earlier

Initial release. FastAPI HTTP server with two
protocol surfaces:

- `/protocols/hummingbird/v1/...` — clean REST for
  login / bookshelf / search / download.
- `/protocols/kados/v1/methods/{name}/` — RPC surface
  compatible with Kolibre KADOS via
  `@cobdfamily/openapi-kados`.

Pluggable: a single optional plugin can override
five hooks (login, bookshelf, search, download,
content). Without a plugin the server is fully
functional with JSON-on-disk state.

[Unreleased]: https://github.com/cobdfamily/hummingbird/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/cobdfamily/hummingbird/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/cobdfamily/hummingbird/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/cobdfamily/hummingbird/commits/v0.1.0
