"""/download helpers: find cached file, ask plugin to populate it, or proxy from a public source.

Lookup order on a /download miss:
  1. ``find_cached_file`` -- if it's already on disk, serve immediately.
  2. ``active_plugin().download(...)`` -- libraries like NNELS whose
     actual file lives behind an authenticated upstream session
     populate the cache via this hook. Raising ``NotImplementedError``
     defers to step 3 (the default path).
  3. ``fetch_from_public_source`` -- pulls from
     ``HUMMINGBIRD_PUBLIC_CONTENT_URL`` if configured.

Also exposes ``prune_cache`` (delete files older than
``HUMMINGBIRD_CACHE_MAX_AGE_DAYS``, default 30) so a periodic job can
keep disk usage bounded; the audiobook files we cache are large and
rarely re-played, so we'd rather re-fetch on demand than store them
forever.
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import logging
import re
import time
from pathlib import Path
from urllib.parse import unquote

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def cache_dir_for(fmt: int, node_id: int) -> Path:
    return settings.cache_dir / str(fmt) / str(node_id)


def find_cached_file(fmt: int, node_id: int) -> Path | None:
    cache_dir = cache_dir_for(fmt, node_id)
    if not cache_dir.is_dir():
        return None
    files = [
        p for p in cache_dir.iterdir()
        if p.is_file() and not p.name.endswith(".tmp") and p.suffix.lower() != ""
    ]
    return files[0] if files else None


async def fetch_from_public_source(fmt: int, node_id: int) -> Path | None:
    """Try to fetch `{public_url}/{fmt}/{id}/` index and download its one file."""
    base = settings.public_content_url.rstrip("/")
    if not base:
        return None
    cache_dir = cache_dir_for(fmt, node_id)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Try directory index first; fall back to a bare file.
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        try:
            r = await client.get(f"{base}/{fmt}/{node_id}/")
            r.raise_for_status()
        except Exception:
            logger.warning(
                "public content source returned no index for %s/%s", fmt, node_id
            )
            return None

        # Simple strategy: look for the first file link in the index. If the
        # public source returns a JSON listing, try that first.
        content_type = r.headers.get("content-type", "")
        filename: str | None = None

        if "application/json" in content_type:
            try:
                data = r.json()
                if isinstance(data, dict) and "files" in data and data["files"]:
                    filename = Path(data["files"][0]).name
                elif isinstance(data, list) and data:
                    filename = Path(data[0]).name
            except Exception:
                pass

        if filename is None:
            # Scrape for <a href="*.*"> inside an HTML directory listing.
            m = re.search(r'href="([^"/?#]+\.[^"/?#]+)"', r.text)
            if m:
                filename = unquote(m.group(1))

        if filename is None:
            logger.warning(
                "could not determine filename at %s/%s/%s/", base, fmt, node_id
            )
            return None

        dest = cache_dir / filename
        tmp = cache_dir / (filename + ".tmp")
        try:
            async with client.stream("GET", f"{base}/{fmt}/{node_id}/{filename}") as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
            tmp.replace(dest)
            logger.info("cached %s/%s from public source -> %s", fmt, node_id, dest)
            return dest
        except Exception:
            logger.exception(
                "failed to fetch %s/%s from public source", fmt, node_id
            )
            tmp.unlink(missing_ok=True)
            return None


def list_resources(cache_path: Path, fmt: int, node_id: int, base_url: str) -> list[dict]:
    """Enumerate DODP-shaped resources from a cached file.

    For a single-file cache (e.g. an MP3) -> a one-element list. For a
    zip archive (e.g. a DAISY 2.02 audio book) -> one entry per file
    inside the archive. Each resource has the four DODP fields the
    KADOS ``getContentResources`` method returns: ``uri``, ``mimeType``,
    ``size``, ``localURI``. Callers (KADOS handler / REST endpoint /
    BookPlayer) consume the same shape.

    Audio-only filtering is the *client's* job -- we return everything
    so DODP-aware clients can still navigate by SMIL.
    """
    import mimetypes as _mt
    import zipfile as _zf

    out: list[dict] = []
    download_prefix = f"{base_url.rstrip('/')}/protocols/hummingbird/v1/download/{fmt}/{node_id}"

    if cache_path.suffix.lower() == ".zip":
        with _zf.ZipFile(cache_path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                mime, _ = _mt.guess_type(info.filename)
                out.append({
                    "uri": f"{download_prefix}/{info.filename}",
                    "mimeType": mime or "application/octet-stream",
                    "size": info.file_size,
                    "localURI": info.filename,
                })
    else:
        # Single-file cache: one resource pointing at the cached file.
        mime, _ = _mt.guess_type(cache_path.name)
        out.append({
            "uri": f"{download_prefix}/{cache_path.name}",
            "mimeType": mime or "application/octet-stream",
            "size": cache_path.stat().st_size,
            "localURI": cache_path.name,
        })
    return out


class CacheState(enum.Enum):
    """Result state of ``ensure_cached_or_prefetch``. Routes use this to
    decide between serving the file (READY), responding 503 +
    Retry-After (PREPARING), 404 (MISSING / FAILED), or 401
    (SESSION_EXPIRED) so the client can re-authenticate."""

    READY = "ready"
    PREPARING = "preparing"
    MISSING = "missing"
    FAILED = "failed"
    SESSION_EXPIRED = "session_expired"


@dataclasses.dataclass
class CacheResult:
    state: CacheState
    path: Path | None = None
    error: str | None = None


# (fmt, node_id) -> asyncio.Task that's pulling the file from the
# plugin's upstream into the cache. Tracked process-globally so a
# request that comes in while a prefetch is mid-flight observes the
# in-progress task rather than triggering a duplicate fetch. Cleared
# when the task completes and a follow-up request consumes the result.
#
# Keyed on (fmt, node_id) -- NOT on username -- because the cache is
# content-keyed: two users requesting the same audiobook should share
# one cached copy and one in-flight fetch, not duplicate the (often
# multi-GB) download. The plugin's download hook still receives the
# requesting user so an authenticated upstream session can be used.
# If that fetch fails the task entry is cleared, so a second user's
# subsequent request can spawn a fresh task with their own credentials.
_INFLIGHT: dict[tuple[int, int], asyncio.Task] = {}
_INFLIGHT_LOCK = asyncio.Lock()


async def _run_plugin_prefetch(
    username: str, fmt: int, node_id: int
) -> Path | None:
    """Background-task body for a plugin-driven prefetch. Returns the
    cached path on success, None on failure (logged + swallowed).
    A SessionExpired raised by the plugin propagates so the caller
    can map it to HTTP 401."""
    # Lazy import: ``plugins`` imports from us at module init time.
    from .plugins import SessionExpired, active_plugin

    plugin = active_plugin()
    if plugin is None:
        return await fetch_from_public_source(fmt, node_id)
    try:
        return await plugin.download(
            username, fmt, node_id, cache_dir_for(fmt, node_id)
        )
    except NotImplementedError:
        return await fetch_from_public_source(fmt, node_id)
    except SessionExpired:
        # Don't swallow -- the route layer maps this to 401 so the
        # client can re-auth instead of seeing a generic FAILED.
        raise
    except Exception:
        logger.exception(
            "plugin prefetch failed for fmt=%s node_id=%s (user=%s)",
            fmt, node_id, username,
        )
        return None


async def ensure_cached_or_prefetch(
    fmt: int, node_id: int, *, username: str | None = None
) -> CacheResult:
    """Cache-or-async-prefetch variant of ``ensure_cached``.

    DODP-clean async pattern: routes call this, get back one of four
    states, and respond accordingly. Used by ``/resources`` and
    ``/download`` so a cold-cache request returns 503 + Retry-After
    immediately instead of holding the client connection open for
    20+s while the plugin pulls a multi-hundred-MB DAISY archive
    from S3.

    - READY: file is in the cache, ``path`` is set, route serves it.
    - PREPARING: a prefetch task is in flight; route returns 503 +
      ``Retry-After``. The task continues in the background.
    - MISSING: no plugin, no public source, no cached file.
    - FAILED: prefetch task ran and explicitly returned None.
    """
    existing = find_cached_file(fmt, node_id)
    if existing is not None:
        return CacheResult(state=CacheState.READY, path=existing)

    if not username:
        # Anonymous: try the public-source fallback only (matches the
        # old ensure_cached anon path). No plugin invocation.
        public = await fetch_from_public_source(fmt, node_id)
        if public is not None:
            return CacheResult(state=CacheState.READY, path=public)
        return CacheResult(state=CacheState.MISSING)

    # Lazy import to avoid a circular dep at module init.
    from .plugins import active_plugin as _active_plugin

    if _active_plugin() is None:
        # Standalone mode (no plugin). Skip the async-task machinery and
        # just try the public-source fallback synchronously -- matches
        # the old ensure_cached behavior for this code path, so
        # standalone-mode clients don't get 503/Retry-After replies for
        # what's effectively a "nothing here" answer.
        public = await fetch_from_public_source(fmt, node_id)
        if public is not None:
            return CacheResult(state=CacheState.READY, path=public)
        return CacheResult(state=CacheState.MISSING)

    key = (fmt, node_id)
    async with _INFLIGHT_LOCK:
        task = _INFLIGHT.get(key)
        if task is None:
            task = asyncio.create_task(_run_plugin_prefetch(username, fmt, node_id))
            _INFLIGHT[key] = task

    if not task.done():
        return CacheResult(state=CacheState.PREPARING)

    # Task completed; drain it and clean up so a subsequent request
    # either serves from cache (READY) or kicks off a new prefetch
    # (e.g. cache was pruned in the meantime).
    async with _INFLIGHT_LOCK:
        _INFLIGHT.pop(key, None)
    if task.cancelled():
        return CacheResult(state=CacheState.FAILED, error="prefetch cancelled")
    exc = task.exception()
    if exc is not None:
        # Lazy import to avoid the plugins<->download cycle at module init.
        from .plugins import SessionExpired
        if isinstance(exc, SessionExpired):
            return CacheResult(
                state=CacheState.SESSION_EXPIRED,
                error=str(exc) or "upstream session expired",
            )
        return CacheResult(state=CacheState.FAILED, error="prefetch failed")
    result = task.result()
    if isinstance(result, Path):
        return CacheResult(state=CacheState.READY, path=result)
    return CacheResult(state=CacheState.MISSING)


async def ensure_cached(
    fmt: int, node_id: int, *, username: str | None = None
) -> Path | None:
    """Return a cached file for (fmt, node_id), populating the cache via
    the active plugin (if any) or the public-source proxy. None means
    nothing is available.

    ``username`` is the authenticated user the file belongs to. It's
    required for the plugin path -- libraries like NNELS gate the
    actual file behind a per-user session.
    """
    existing = find_cached_file(fmt, node_id)
    if existing is not None:
        return existing

    # Plugin path: only the active plugin has the authenticated upstream
    # session, so for libraries like NNELS this is the only way the file
    # ever lands in the cache. The username gate keeps an anonymous
    # caller from triggering an authenticated fetch under someone else's
    # credentials.
    if username:
        # Import lazily to avoid a circular dependency at module import.
        from .plugins import SessionExpired, active_plugin

        plugin = active_plugin()
        if plugin is not None:
            try:
                fetched = await plugin.download(
                    username, fmt, node_id, cache_dir_for(fmt, node_id)
                )
                if fetched is not None:
                    return fetched
            except NotImplementedError:
                pass
            except SessionExpired:
                # Propagate so the route can map to HTTP 401 instead
                # of silently falling through to public-source / 404.
                raise
            except Exception:
                logger.exception(
                    "plugin download failed for %s/%s (user=%s)", fmt, node_id, username
                )
                # Fall through to the public-source path; if that also
                # fails, the route returns 404.

    return await fetch_from_public_source(fmt, node_id)


def prune_cache(*, max_age_days: int | None = None) -> int:
    """Delete cached files older than ``max_age_days`` (default: read
    from settings.cache_max_age_days, normally 30). Returns the count
    of files removed.

    Cached audiobook files are large (multi-GB for some titles) and a
    user typically replays a given book at most a handful of times, so
    we'd rather re-fetch on demand than keep cold copies forever.
    Empty dirs are pruned too.
    """
    if max_age_days is None:
        max_age_days = settings.cache_max_age_days
    if max_age_days <= 0:
        return 0
    if not settings.cache_dir.exists():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for path in settings.cache_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.warning("failed to prune cached file %s", path)
    # Drop now-empty per-(fmt, node_id) dirs so listings stay clean.
    for path in sorted(
        (p for p in settings.cache_dir.rglob("*") if p.is_dir()),
        key=lambda p: -len(p.parts),
    ):
        try:
            if not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass
    return removed
