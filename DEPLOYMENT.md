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
git tag -a v0.1.2 -m "Release 0.1.2"
git push origin v0.1.2
```

Within a couple of minutes:

- `kibble.apps.blindhub.ca/cobdfamily/hummingbird:0.1.2`
- `kibble.apps.blindhub.ca/cobdfamily/hummingbird:latest`

## Configure

hummingbird reads config from environment variables.
The defaults in `src/hummingbird/config.py` cover dev.
For production, override:

```sh
# Built-in /login users (no plugin). Comma-separated
# user:password pairs. With a plugin configured, this
# is unused.
HUMMINGBIRD_USERS=alice:hunter2,bob:passw0rd

# Where the JSON state lives. Default /app/data.
HUMMINGBIRD_DATA_DIR=/app/data

# Audio cache. Default /app/cache.
HUMMINGBIRD_CACHE_DIR=/app/cache

# Plugin name. Empty = no plugin (file-only mode).
HUMMINGBIRD_PLUGIN=
```

Check `src/hummingbird/config.py` for the full list
and defaults.

## Run

Production-shaped `docker-compose.yml`:

```yaml
services:
  hummingbird:
    image: kibble.apps.blindhub.ca/cobdfamily/hummingbird:0.1.1
    container_name: hummingbird
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      HUMMINGBIRD_USERS: ${HUMMINGBIRD_USERS}
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
# Hummingbird liveness
curl -fsS \
  https://library.cobd.ca/

# KADOS RPC surface (replace serviceAttributes with any
# other read-only method)
curl -fsS \
  https://library.cobd.ca/protocols/kados/v1/methods/serviceAttributes/
```

## Routine operations

### Upgrading

```sh
git tag -a v0.1.2 -m "Release 0.1.2"
git push origin v0.1.2
# CI builds and pushes the image.

# Deploy host:
sed -i 's|hummingbird:[^ ]*|hummingbird:0.1.2|' docker-compose.yml
docker compose pull
docker compose up -d --no-deps hummingbird
```

### Backups

What must persist:

- `data/` — bookshelves, sessions, bookmarks.
  Without this users lose their reading progress.
- `.env` if you're using `HUMMINGBIRD_USERS` for
  password storage. Treat as a secret.

Safe to lose:

- `cache/` — audio cache. Re-derives from source on
  next request. Saves bandwidth, not correctness.
- Container logs (ship them to your aggregator).
