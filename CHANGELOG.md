# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: SemVer; pre-1.0 minor bumps may break.

## [Unreleased]

## [0.6.0] - 2026-05-18

Server-side correctness release. Two themes:

1. **Defensive cleanup of long-standing footguns** (Scope A): path
   traversal, silent session-expired UX, multi-user prefetch race.
2. **KADOS surface aligned to the real `OpenAPIAdapter` contract**
   (Scope B), with the PHP adapter in `cobdfamily/openapi-kados` as
   the authority. Plus three forward-looking promotions for the
   in-flight BookPlayer `.dodp` source.

### Fixed
- **Path-traversal defense at the storage layer.** Usernames flow in
  from HTTP Basic auth (REST) and the KADOS Session-token resolver;
  KADOS `contentId`s flow in from arbitrary RPC callers. Both were
  being interpolated directly into filesystem paths
  (`bookshelves/{user}.json`, `bookmarks/{user}/{cid}.json`, etc.).
  A KADOS client passing `contentId="../sessions/admin"` could write
  to any path the server process could reach. New
  `storage._safe_component(...)` helper rejects empty, overlong,
  `.`/`..`, and `/` / `\` / NUL-containing components with
  `ValueError` before path construction. Applied to every shelf /
  session / bookmark path on both surfaces.

- **NNELS expired-cookie no longer surfaces as a silent empty
  bookshelf.** New `hummingbird.plugins.SessionExpired` exception:
  plugins raise it when their upstream session is no longer usable,
  the REST router maps to HTTP 401 + `WWW-Authenticate: Basic`, the
  KADOS router maps to HTTP 401 *and* drops the caller's token from
  the in-memory `_SESSIONS` map so a follow-up `authenticate` call
  mints a fresh one. The previous behavior -- `list_bookshelf`
  returning `[]` when NNELS had logged the user out -- read to users
  as "all my books vanished." Every plugin-touching route (bookshelf
  list / add / remove, search, bookmark get / set, resources,
  download, download-fetch) carries the new branch.

- **Plugin-driven download `SessionExpired` propagates through the
  async prefetch.** New `CacheState.SESSION_EXPIRED` state. When
  `plugin.download` raises `SessionExpired`, the background prefetch
  task surfaces it on the next poll instead of swallowing it as
  generic FAILED. Route returns 401 (REST) or 401 with token drop
  (KADOS) so clients can re-auth instead of seeing an opaque 404.

- **N1: User-scoped KADOS calls return HTTP 401 without a valid
  session token.** Previously they returned shape-appropriate empty
  data (`{totalItems:0, contentItem:[]}` for contentList, `False`
  for contentExists, etc.), so a stale or missing token looked to
  KADOS like an empty bookshelf -- the SOAP layer relayed that to
  the client instead of re-triggering `logOn`. The `OpenAPIAdapter.md`
  contract is explicit: "Endpoints that require a session and
  receive no valid token MUST respond with an HTTP 401." Dispatcher
  now enforces it; an `_ANONYMOUS_METHODS` allow-list
  (`authenticate`, `label`, `setProtocolVersion`,
  `logSoapRequestAndResponse`, `announcements`,
  `termsOfServiceAccepted`, PDTB2 + ToS hooks) preserves the
  pre-logOn handshake.

- **N2: `stopSession` drops the caller's token from `_SESSIONS`.**
  The adapter clears its own local sessionToken AFTER calling
  `stopSession`, expecting the backend to be done with it.
  Previously hummingbird returned `True` without touching
  `_SESSIONS`, so tokens lived forever server-side (memory leak)
  AND violated the adapter's invariant that `stopSession` ->
  `startSession` returns `False`. Router pops the token after a
  successful dispatch; re-using the dropped token returns 401.

- **N3: `contentResources` reads `accessMethod`** (the DODP-spec key
  the PHP adapter sends via
  `contentResources($contentId, $accessMethod)`) instead of `format`.
  Previously fell back to the DAISY-202 default for every PHP-adapter
  call -- a real format-selection bug masked by the default for
  NNELS, real noise for any other backend. `format` still accepted
  as a legacy alias for hummingbird-native clients.

### Changed
- **Prefetch in-flight dedupe is now content-keyed, not user-keyed.**
  `download._INFLIGHT` was keyed on `(username, fmt, node_id)`, which
  meant two users requesting the same multi-GB audiobook spawned two
  independent fetch tasks against the same shared cache slot. The
  key is now `(fmt, node_id)`: the audiobook bytes are identical for
  any user with access, so one fetch task feeds both. The plugin
  still receives the user that triggered the first request (for the
  authenticated upstream session); on task failure the slot is
  cleared and a subsequent request from a different user spawns a
  fresh task.

- **`contentMetadata` consults the active plugin when it can.** New
  optional `Plugin.get_metadata(user, content_id)` hook (NOT
  `@abstractmethod` -- existing plugins keep working). When a plugin
  overrides it, real metadata (NNELS' 30-day cache: title, authors,
  narrators, format) flows to KADOS' DC envelope instead of the
  empty stub. Plugins that don't override get the prior minimal
  `{dc:identifier, dc:title:"", dc:format:"", dc:creator:""}`.

- **`/resources` and `/download` accept `Authorization: Session <token>`
  in addition to Basic.** New `auth.current_user_basic_or_session`
  dependency tries the KADOS session header first, falls back to
  Basic. The resource URIs returned by KADOS `contentResources`
  point at REST `/download`; DAISY-Online clients (EasyReader, a
  future BookPlayer `.dodp` source) authenticate via session token
  only and previously got 401 + a `WWW-Authenticate: Basic`
  challenge they couldn't satisfy. BookPlayer's existing Basic-auth
  flow is unchanged (regression-pinned).

- **Startup warning when `HUMMINGBIRD_PUBLIC_BASE_URL` is unset.**
  KADOS clients don't carry an HTTP base URL through their RPC, so
  the KADOS surface emits relative resource URIs when the env var
  isn't set. Some DAISY-Online clients won't resolve those against
  the SOAP endpoint. The warning runs at app startup so operators
  catch it before users do.

### Tests
38 new tests total. `test_storage.py` (path-component sanitization),
`test_download.py` (content-keyed dedupe + prefetch SessionExpired
propagation), `test_plugin_active.py` (SessionExpired -> 401 across
every plugin-touching REST route + resources/download +
`get_metadata` plugin path), `test_router_kados.py` (401 enforcement
matrix, `stopSession` token-drop, `accessMethod` key, anonymous
allow-list), `test_router_hummingbird.py` (`/download` and
`/resources` accept Session tokens; Basic regression guard). 244
total tests, coverage 94.39%.

### KADOS contract reference
- `cobdfamily/openapi-kados/services/kados/includes/adapters/OpenAPIAdapter.md`
- `cobdfamily/openapi-kados/services/kados/includes/adapters/OpenAPIAdapter.class.php`

## [0.1.11] - 2026-05-03

### Tests
- Coverage push from 89% to 99% (branch on). The previous
  suite covered the standalone (no-plugin) path through
  both protocol surfaces but never instantiated a real
  ``Plugin`` and stuffed it through ``active_plugin()``,
  so every plugin-active branch in
  ``protocols/hummingbird/router.py`` and
  ``protocols/kados/methods.py`` (delegate-to-plugin on
  success, fall through to default storage on
  ``NotImplementedError``) was uncovered.

  Added ``tests/test_plugin_active.py`` (25 tests) with
  a deterministic ``FakePlugin`` that subclasses
  ``Plugin`` and exposes one attribute per hook. Tests
  flip a single attribute to either a real return value
  or to ``NotImplementedError`` to walk both branches.

  Coverage map:

  - ``protocols/hummingbird/router.py`` 79% -> 99%
    (login plugin success / failure / fall-through;
    bookshelf list / add / remove plugin paths +
    NI fall-through; search via plugin + format-filter
    + NI fall-through; ``_guess_mime`` extra-mimes
    table; ``_flatten_to_items`` format-id-zero skip;
    download single-file 404).
  - ``protocols/kados/methods.py`` 90% -> 100%
    (authenticate via plugin + NI fall-through;
    contentList via plugin + NI fall-through;
    contentAddBookshelf via plugin + NI fall-through;
    contentReturn via plugin + NI fall-through).
  - ``protocols/kados/router.py`` 97% -> 100%
    (HTTPException re-raise from a handler;
    NotImplementedError from a handler -> 501).

### Changed
- ``tool.coverage.report.fail_under`` raised from 85
  to 92.
- ``tool.coverage.report.exclude_lines`` adds a pattern
  matching a line containing only ``...`` so the
  ``@abstractmethod`` body sentinels in
  ``Plugin`` don't get flagged as missing.

## [0.1.10] - 2026-05-03

### Fixed
- Continuing the kados-fronting integration work from
  v0.1.9. The full SOAP logOn round-trip surfaced two more
  layers of issues:

  - The stub factory raised ``NotImplementedError`` for
    every unimplemented method, which the router turned
    into 501. The kolibreorg/kados PHP adapter treats any
    non-200 as a fatal ``AdapterException``, so a
    501 from any stub crashed every DODP request that
    walked through it. Stub factory now returns
    ``None`` (matching the mock_backend.py philosophy);
    PHP decodes null and the adapter caller falls back
    to whatever default makes sense for the return
    contract.
  - KADOS' response builder validates that
    ``logOnResponse.serviceAttributes.serviceProvider.label.text``
    is non-empty. A null or empty-string label crashes
    the build with "logOnResponse could not be built".

  Adds shape-typed default handlers for the four methods
  KADOS strictly requires non-null responses from during
  the standard logOn / list / read flow:

  - ``label``       -> ``{text: <id>, audio: null, lang: "en"}``
                       (echoes the requested id so text is
                       always non-empty)
  - ``contentAccessible``  -> ``True``
  - ``contentReturnable``  -> ``True``
  - ``contentIssuable``    -> ``False`` (no loan ceremony)

  Removed the same four from ``_STUBS``. Five new tests
  in ``tests/test_router_kados.py`` lock the contract,
  and the existing ``test_stub_method_returns_501`` is
  rewritten as ``test_stub_method_returns_null`` to
  reflect the new default.

  End-to-end SOAP logOn through
  ``cobdfamily/openapi-kados`` now passes against the
  fleet hummingbird image.

## [0.1.9] - 2026-05-03

### Fixed
- KADOS' default log level (INFO) makes it ping
  ``logSoapRequestAndResponse`` on every SOAP request, and
  the logOn flow always pulls ``announcements`` and
  ``termsOfServiceAccepted``. v0.1.8 had all three on the
  ``_STUBS`` list, so each call returned 501 and crashed
  the openapi-kados adapter with an ``AdapterException``
  (which the SOAP layer surfaced as a 500 internal server
  error to every client). Surfaced by the new
  hummingbird-backed integration suite in
  ``cobdfamily/openapi-kados``.

  Promotes the three from stubs to fire-and-forget no-op
  handlers:

  - ``logSoapRequestAndResponse`` -> ``None``
  - ``announcements`` -> ``[]``
  - ``termsOfServiceAccepted`` -> ``True``

  Plugins that want real behaviour can override by
  replacing the entry in ``_REGISTRY``. Three new tests
  in ``tests/test_router_kados.py`` lock the contract
  so a future stub-list edit can't regress this.

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

[Unreleased]: https://github.com/cobdfamily/hummingbird/compare/v0.1.11...HEAD
[0.1.11]: https://github.com/cobdfamily/hummingbird/compare/v0.1.10...v0.1.11
</content>
</invoke>