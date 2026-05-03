# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: SemVer; pre-1.0 minor bumps may break.

## [Unreleased]

## [0.1.8] - 2026-05-02

### Tests
- Coverage push from 70% to 89% (branch on). v0.1.7 covered
  storage / plugins / download / the hummingbird REST surface
  but left the entire kados protocol untested
  (methods.py 29%, router.py 31%).

  Added 38 tests in ``tests/test_router_kados.py`` covering:

  - Router envelope validation (method/path mismatch -> 400),
    unknown method -> 404, X-API-Key when KADOS_API_KEY is
    set (missing/wrong -> 401, match -> 200), Authorization
    parse (no header / wrong prefix / unknown token all
    treated as anonymous), stub method
    NotImplementedError -> 501, generic exception -> 500.
  - Method handlers: ``authenticate`` happy / empty-username
    / wrong-password, ``contentListExists`` known + unknown,
    ``contentList`` anon / non-bookshelf / populated,
    ``contentExists`` anon / invalid-id / present,
    ``contentMetadata`` and ``contentResources`` with and
    without contentId, ``contentAddBookshelf`` and
    ``contentReturn`` anon / invalid-id / success,
    ``startSession`` / ``stopSession`` /
    ``setProtocolVersion``, and ``setBookmarks`` /
    ``getBookmarks`` round-trip + every empty-result path.

  kados/methods.py is now at 88%, kados/router.py at 99%.

### Changed
- ``tool.coverage.report.fail_under`` raised from 65 to 85
  to reflect the new floor. The 4-point buffer absorbs
  short-term drift. The remaining gap is mostly the
  plugin-active code paths across both protocol routers —
  those need a fake plugin fixture to exercise.

## [0.1.7] - 2026-05-02

### Tests
- Coverage push from 53% to 74%. The previous suite covered
  the formats helpers and a single round-trip, but skipped
  most of ``storage.py``, all of ``plugins.py`` discovery,
  the ``download.py`` public-source proxy, and the entire
  hummingbird-protocol REST surface.

  Added 57 tests across:

  - ``tests/test_storage.py`` (new): bookshelf
    add / remove / list / idempotent-duplicate / multi-format
    paths, session write / read / clear / clear-no-op.
    storage.py is now at 100%.
  - ``tests/test_plugins.py`` (new): entry-point lookup
    misses, ``load()`` failure, instantiation failure,
    cache-on-success, and the abstract-class lock.
    plugins.py is now at 100%.
  - ``tests/test_download.py`` (new): cache-hit, the
    public-source proxy across JSON listing
    (``{"files":[...]}`` and bare list), HTML href
    scrape, no-filename-found, stream failure cleanup,
    no-public-url short-circuit. Uses
    ``httpx.MockTransport`` rather than network. download.py
    is now at 97%.
  - ``tests/test_router_hummingbird.py`` (new): integration
    tests through ``TestClient`` covering /login (env creds,
    401, 400), /bookshelf list / add / remove / username
    fallback, /search empty + validation, /download listing
    (single + zip + 404), /download fetch (single, zip
    member, 404 paths), /formats. router.py is now at 81%.

### Changed
- ``tool.coverage.report.fail_under`` introduced at 65 to
  reflect the new floor. With branch coverage enabled real
  coverage is ~70%; the 5-point buffer absorbs short-term
  drift when new code lands ahead of its tests. Push this
  number up over time as the kados protocol surface
  (currently ~30%) gets coverage.

## [0.1.6] - 2026-05-02

### Added
- Health endpoint at ``/`` now returns ``"version"``. Sourced
  from ``app.version`` so it stays in lockstep with
  ``__version__``. Useful for confirming the running build
  without hitting ``/docs``.

## [0.1.5] - 2026-05-02

### Fixed
- ``pytest-cov`` added to the dev group. The CI workflow
  invokes ``uv run pytest -q --cov --cov-report=term
  --cov-report=xml`` but the dependency was missing; the
  pytest job had been failing with "unrecognized arguments"
  since v0.1.1. No ``fail_under`` gate is set yet (current
  coverage is 64%); the report is informational until tests
  fill it out.

## [0.1.4] - 2026-05-02

### Fixed
- 0.1.3 only half-fixed CI: ``uv sync --frozen`` started
  succeeding but ``uv run ruff check`` still tried to
  resolve the editable ``nnels`` source and failed on the
  missing ``../nnels`` path. The fix is to drop the
  ``[tool.uv.sources]`` block and the ``local-plugins``
  group entirely. Local devs who want to layer the editable
  plugin in on top of a synced env do it explicitly with
  ``uv pip install --editable ../nnels`` — we don't bake it
  into pyproject.toml.

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

[Unreleased]: https://github.com/cobdfamily/hummingbird/compare/v0.1.8...HEAD
[0.1.8]: https://github.com/cobdfamily/hummingbird/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/cobdfamily/hummingbird/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/cobdfamily/hummingbird/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/cobdfamily/hummingbird/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/cobdfamily/hummingbird/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/cobdfamily/hummingbird/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/cobdfamily/hummingbird/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/cobdfamily/hummingbird/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/cobdfamily/hummingbird/commits/v0.1.0
