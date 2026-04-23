"""Plugin ABC and entry-point discovery for Hummingbird."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from importlib.metadata import entry_points

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
