"""hummingbird FastAPI app.

Mounts two protocol surfaces against the same plugin
registry:

  Hummingbird v1 REST -- the modern shape
                         (/v1/books, /v1/sources, ...)
  KADOS RPC           -- Kolibre-compatible SOAP-ish
                         endpoint for DAISY clients

Both routers consume the same underlying source + TTS
plugins, so a new content source surfaces on both
protocols simultaneously. The plugin system discovers
entry points at import time -- see the two
protocols/.../router.py modules for how each side
reaches the shared registry.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from . import __version__
from .config import settings
from .download import prune_cache
from .formats import HUMAN_READABLE_FORMATS
from .protocols.hummingbird.router import router as hummingbird_router
from .protocols.kados.router import router as kados_router

logger = logging.getLogger(__name__)


_PRUNE_INTERVAL_SECONDS = 86400  # daily check; deletes anything older than cache_max_age_days


async def _cache_prune_loop() -> None:
    """Background coroutine: run prune_cache once on startup, then once
    a day. Cancelled on shutdown via FastAPI's lifespan handling."""
    while True:
        try:
            removed = await asyncio.to_thread(prune_cache)
            if removed:
                logger.info("pruned %d stale cached file(s)", removed)
        except Exception:
            logger.exception("cache prune failed")
        await asyncio.sleep(_PRUNE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task: asyncio.Task | None = None
    if settings.cache_max_age_days > 0:
        task = asyncio.create_task(_cache_prune_loop())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()

app = FastAPI(
    title="hummingbird",
    version=__version__,
    description=(
        "Accessible-library HTTP server. Two protocol surfaces: "
        "/protocols/hummingbird/v1 (REST) and /protocols/kados/v1 (RPC, "
        "Kolibre KADOS adapter compatible)."
    ),
    redoc_url="/redocs",
    lifespan=lifespan,
)
app.include_router(hummingbird_router)
app.include_router(kados_router)


@app.get("/", tags=["Health"])
async def root():
    return {"service": "hummingbird", "status": "ok", "version": app.version}


@app.get("/formats")
async def formats() -> dict[int, str]:
    """Integer -> human-readable format map (aliased from the protocol routes)."""
    return {i: label for i, label in enumerate(HUMAN_READABLE_FORMATS) if label}


def run() -> None:
    uvicorn.run("hummingbird.main:app", host=settings.host, port=settings.port, reload=False)
