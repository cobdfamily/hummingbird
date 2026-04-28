"""/download helpers: find cached file, or proxy from a configured public source.

The plugin surface deliberately does NOT participate in /download. When a
file is missing from the local cache and `HUMMINGBIRD_PUBLIC_CONTENT_URL`
is set, we proxy from `{url}/{format}/{id}/{filename}` to the cache
atomically and then serve it. Otherwise 404.
"""

from __future__ import annotations

import logging
import re
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


async def ensure_cached(fmt: int, node_id: int) -> Path | None:
    """Return a cached file for (fmt, node_id), fetching from the public
    source if configured and necessary. None means nothing is available."""
    existing = find_cached_file(fmt, node_id)
    if existing is not None:
        return existing
    return await fetch_from_public_source(fmt, node_id)
