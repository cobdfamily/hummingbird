"""Plugin ABC and entry-point discovery for Hummingbird."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from importlib.metadata import entry_points
from pathlib import Path  # noqa: F401  (referenced in Plugin.download type hint)

from .config import settings
from .models import BookRecord, SearchResult

logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "hummingbird.plugins"


class Plugin(ABC):
    """Hooks a plugin may override. Every hook is optional — a plugin may
    `raise NotImplementedError` to defer to the default backend."""

    name: str = "plugin"

    @abstractmethod
    async def authenticate(self, username: str, password: str) -> bool:
        ...

    @abstractmethod
    async def list_bookshelf(self, username: str) -> list[BookRecord]:
        ...

    @abstractmethod
    async def add_to_bookshelf(self, username: str, node_id: int) -> bool:
        ...

    @abstractmethod
    async def remove_from_bookshelf(self, username: str, node_id: int) -> bool:
        ...

    @abstractmethod
    async def search(
        self,
        username: str,
        query: str,
        formats: list[int] | None,
        page: int,
    ) -> SearchResult:
        ...

    @abstractmethod
    async def set_bookmark(
        self, username: str, content_id: int, bookmark: dict
    ) -> bool:
        """Persist a bookmark/progress payload. The ``bookmark`` dict is
        treated as opaque -- DODP names like ``position`` are the
        convention but the storage layer round-trips whatever shape
        the caller sent. Plugins without an upstream sync API can
        ``raise NotImplementedError`` to defer to local file storage."""
        ...

    @abstractmethod
    async def get_bookmark(self, username: str, content_id: int) -> dict:
        """Return the stored bookmark dict, or ``{}`` if no bookmark
        is set. ``raise NotImplementedError`` to defer to local
        file storage."""
        ...

    @abstractmethod
    async def download(
        self,
        username: str,
        fmt: int,
        node_id: int,
        cache_dir: "Path",
    ) -> "Path | None":
        """Fetch the (fmt, node_id) audio file from the upstream library
        using whatever credentials/session this plugin manages, write it
        into ``cache_dir`` (creating the dir if needed), and return the
        absolute path. ``None`` means "not available."

        For libraries whose files are publicly hosted, the plugin can
        ``raise NotImplementedError`` and Hummingbird falls back to
        its built-in cache + ``HUMMINGBIRD_PUBLIC_CONTENT_URL`` proxy.
        For libraries like NNELS where the actual file is gated behind
        a per-user authenticated session that only the plugin has, the
        plugin MUST implement this hook -- the default path can't see
        the upstream credentials.
        """
        ...


_active: Plugin | None = None
_loaded = False


def _load_active_plugin() -> Plugin | None:
    name = settings.plugin.strip()
    if not name:
        logger.info("no plugin configured — running standalone")
        return None

    eps = entry_points(group=_ENTRY_POINT_GROUP)
    match = next((ep for ep in eps if ep.name == name), None)
    if match is None:
        available = [ep.name for ep in eps]
        logger.warning(
            "plugin %r not found (group %s); available: %s",
            name, _ENTRY_POINT_GROUP, available,
        )
        return None

    try:
        cls = match.load()
    except Exception:
        logger.exception("failed to import plugin %r", name)
        return None

    try:
        instance = cls()
    except Exception:
        logger.exception("failed to instantiate plugin %r", name)
        return None

    logger.info("loaded plugin %r (%s)", name, cls)
    return instance


def active_plugin() -> Plugin | None:
    """Return the active plugin instance, or None for standalone mode."""
    global _active, _loaded
    if not _loaded:
        _active = _load_active_plugin()
        _loaded = True
    return _active
