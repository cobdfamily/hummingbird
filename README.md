# hummingbird

[![test](https://github.com/cobdfamily/hummingbird/actions/workflows/test.yml/badge.svg)](https://github.com/cobdfamily/hummingbird/actions/workflows/test.yml)

HTTP server for accessible-library workflows with a small plugin
surface and a Kados-compatible RPC endpoint.

> Deploying hummingbird in production? See **[DEPLOYMENT.md](DEPLOYMENT.md)**
> for the full checklist (image pull from the kibble registry,
> configure / run / verify, upgrades).

Two protocol surfaces mount on the same FastAPI app:

| Path | Purpose |
| --- | --- |
| `/protocols/hummingbird/v1/...` | Clean path-based REST: login, bookshelf, search, download |
| `/protocols/kados/v1/methods/{name}/` | RPC surface compatible with Kolibre KADOS via `@cobdfamily/openapi-kados` |

## Plugin model

Hummingbird is fully functional with no plugin — bookshelves live
in JSON files on disk, sessions live in JSON files on disk, `/login`
checks credentials from `.env`, `/search` returns an empty list, and
`/download` serves from a public HTTP source if one is configured.

A plugin can override exactly five hooks:

```python
class Plugin:
    async def authenticate(self, username, password) -> bool: ...
    async def list_bookshelf(self, username) -> list[BookRecord]: ...
    async def add_to_bookshelf(self, username, node_id) -> bool: ...
    async def remove_from_bookshelf(self, username, node_id) -> bool: ...
    async def search(self, username, query, formats, page) -> SearchResult: ...
```

Plugins are discovered via the `hummingbird.plugins` entry-point
group. In a plugin's `pyproject.toml`:

```toml
[project.entry-points."hummingbird.plugins"]
nnels = "nnels.plugin:NnelsPlugin"
```

Select an active plugin with `HUMMINGBIRD_PLUGIN=nnels` in `.env`.
Leave unset to run standalone.

The reference NNELS plugin lives at `cobdfamily/nnels`.

## Configure

```
HUMMINGBIRD_USERNAME=...          # used by /login default when no plugin
HUMMINGBIRD_PASSWORD=...
HUMMINGBIRD_PLUGIN=nnels          # optional; entry-point name
HUMMINGBIRD_PUBLIC_CONTENT_URL=   # optional; fallback source for /download
HUMMINGBIRD_DATA_DIR=./data
HUMMINGBIRD_CACHE_DIR=./cache
```

## Run

```
uv sync
uv run hummingbird
# or: uv run uvicorn hummingbird.main:app --reload
```

Docs at `/docs`, `/redoc`.

## Routes

```
POST  /protocols/hummingbird/v1/login
GET   /protocols/hummingbird/v1/bookshelf/list
POST  /protocols/hummingbird/v1/bookshelf/add/{id}
POST  /protocols/hummingbird/v1/bookshelf/remove/{id}
GET   /protocols/hummingbird/v1/search?q=...&formats=1&formats=2&page=0
GET   /protocols/hummingbird/v1/download/{format}/{id}/
GET   /protocols/hummingbird/v1/download/{format}/{id}/{path:path}

POST  /protocols/kados/v1/methods/{name}/
```

Kados payload envelope:

```json
{ "method": "methodName", "data": { ... args ... } }
```

Response envelope:

```json
{ "data": <value> }
```

Auth:
- `X-API-Key: <key>` — app-level, sent on every request when configured
- `Authorization: Session <token>` — user-level, after `authenticate`

## License

AGPL-3.0 — see `LICENSE`.
