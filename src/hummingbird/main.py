import uvicorn
from fastapi import FastAPI

from . import __version__
from .config import settings
from .formats import HUMAN_READABLE_FORMATS
from .protocols.hummingbird.router import router as hummingbird_router
from .protocols.kados.router import router as kados_router

app = FastAPI(
    title="hummingbird",
    version=__version__,
    description=(
        "Accessible-library HTTP server. Two protocol surfaces: "
        "/protocols/hummingbird/v1 (REST) and /protocols/kados/v1 (RPC, "
        "Kolibre KADOS adapter compatible)."
    ),
    redoc_url="/redocs",
)
app.include_router(hummingbird_router)
app.include_router(kados_router)


@app.get("/", tags=["Health"])
async def root():
    return {"service": "hummingbird", "status": "ok"}


@app.get("/formats")
async def formats() -> dict[int, str]:
    """Integer -> human-readable format map (aliased from the protocol routes)."""
    return {i: label for i, label in enumerate(HUMAN_READABLE_FORMATS) if label}


def run() -> None:
    uvicorn.run("hummingbird.main:app", host=settings.host, port=settings.port, reload=False)
