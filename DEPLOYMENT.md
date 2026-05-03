# Deployment

hummingbird ships as a container image to the kibble
registry on every `git tag v*`. It serves two protocol
surfaces from one process:

- `/protocols/hummingbird/v1/...` — the REST surface
  cobd.ca apps speak.
- `/protocols/kados/v1/methods/{name}/` — RPC
  compatible with Kolibre KADOS via
  `@cobdfamily/openapi-kados`.

## Pre-flight checklist

- [ ] Public hostname for hummingbird (eg.
      `library.cobd.ca`) with an A record pointing at
      the host. The service speaks plain HTTP on
      `:8000` behind your reverse proxy / TLS
      terminator.
- [ ] A persistent path for `data/` (bookshelves,
      sessions, bookmarks) and `cache/` (audio cache).
      Both are container-internal at `/app/data` and
      `/app/cache`; bind-mount from the host.
- [ ] If using a plugin: the plugin's Python package
      must be on the runtime image. Either install
      via a downstream Dockerfile or
      `docker exec ... uv pip install --system <pkg>`
      then restart.

## Image distribution

`.github/workflows/release.yml` builds and pushes the
image on every `git tag v*`. Anonymous push to
kibble, no secrets to configure.

```sh
git tag -a v0.1.8 -m "Release 0.1.8"
git push origin v0.1.8
```

Within a couple of minutes:

- `kibble.apps.blindhub.ca/cobdfamily/hummingbird:0.1.8`
- `kibble.apps.blindhub.ca/cobdfamily/hummingbird:latest`

## Configure

hummingbird reads config from environment variables.
The defaults in `src/hummingbird/config.py` cover dev.
For production, override:

```sh
# Default-backend credentials. Used by /login when no
# plugin is configured (single-tenant fallback). With a
# plugin loaded, the plugin authenticates and these are
# ignored.
HUMMINGBIRD_USERNAME=alice
HUMMINGBIRD_PASSWORD=hunter2

# Where the JSON state lives. Default /app/data.
HUMMINGBIRD_DATA_DIR=/app/data

# Audio cache. Default /app/cache.
HUMMINGBIRD_CACHE_DIR=/app/cache

# Plugin entry-point name. Empty = standalone (no plugin).
HUMMINGBIRD_PLUGIN=

# Optional fallback for /download cache misses. When set,
# missing files are proxied from
#   {url}/{format-id}/{node-id}/  (directory index)
# atomically into the cache, then served.
HUMMINGBIRD_PUBLIC_CONTENT_URL=

# Optional KADOS app-level key. When non-empty, every
# /protocols/kados/v1/methods/* request must carry the
# matching X-API-Key header. Per-user auth still happens
# via the Authorization: Session <token> header issued
# at authenticate-time.
KADOS_API_KEY=
```

Check `src/hummingbird/config.py` for the full list
and defaults.

## Run

Production-shaped `docker-compose.yml`:

```yaml
services:
  hummingbird:
    image: kibble.apps.blindhub.ca/cobdfamily/hummingbird:0.1.8
    container_name: hummingbird
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      HUMMINGBIRD_USERNAME: ${HUMMINGBIRD_USERNAME}
      HUMMINGBIRD_PASSWORD: ${HUMMINGBIRD_PASSWORD}
      HUMMINGBIRD_PUBLIC_CONTENT_URL: ${HUMMINGBIRD_PUBLIC_CONTENT_URL:-}
      KADOS_API_KEY: ${KADOS_API_KEY:-}
    volumes:
      - ./data:/app/data
      - ./cache:/app/cache
```

Bring it up:

```sh
mkdir -p /opt/hummingbird/{data,cache}
chmod 700 /opt/hummingbird/{data,cache}
cd /opt/hummingbird
docker compose pull
docker compose up -d
docker compose logs -f hummingbird
```

Behind your TLS reverse proxy, route
`https://library.cobd.ca/*` to `127.0.0.1:8000`.

## Verify

```sh
# Hummingbird liveness — returns the running version too:
# {"service":"hummingbird","status":"ok","version":"0.1.8"}
curl -fsS https://library.cobd.ca/

# Generated OpenAPI docs:
#   https://library.cobd.ca/docs    (Swagger UI)
#   https://library.cobd.ca/redocs  (ReDoc, trailing s)

# REST surface (Hummingbird-native): list a bookshelf.
curl -fsS \
  "https://library.cobd.ca/protocols/hummingbird/v1/bookshelf/list?username=alice"

# KADOS RPC surface — every method is POST with a JSON
# envelope {"method": "<name>", "data": {...}}.
curl -fsS \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"method":"contentListExists","data":{"list":"bookshelf"}}' \
  https://library.cobd.ca/protocols/kados/v1/methods/contentListExists/
```

## Routine operations

### Upgrading

```sh
git tag -a v0.1.9 -m "Release 0.1.9"
git push origin v0.1.9
# CI builds and pushes the image.

# Deploy host:
sed -i 's|hummingbird:[^ ]*|hummingbird:0.1.9|' docker-compose.yml
docker compose pull
docker compose up -d --no-deps hummingbird
```

### Backups

What must persist:

- `data/` — bookshelves, sessions, bookmarks.
  Without this users lose their reading progress.
- `.env` (or your secret store) for the
  `HUMMINGBIRD_PASSWORD` and `KADOS_API_KEY` values.
  Treat as a secret.

Safe to lose:

- `cache/` — audio cache. Re-derives from
  `HUMMINGBIRD_PUBLIC_CONTENT_URL` (or the active
  plugin) on next request. Saves bandwidth, not
  correctness.
- Container logs (ship them to your aggregator).
