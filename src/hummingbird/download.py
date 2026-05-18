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
        from .plugins import active_plugin

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
