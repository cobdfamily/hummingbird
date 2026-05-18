"""/protocols/hummingbird/v1 REST surface.

Five hooks delegate to the active plugin when one is loaded; otherwise
the JSON-backed default storage is used. /download never calls a plugin
(by design) — it serves from local cache with a public-source fallback.
"""

from __future__ import annotations

import mimetypes
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ... import auth as auth_module
from ... import storage
from ...config import settings
from ...download import ensure_cached
from ...models import BookRecord, SearchResult
from ...plugins import active_plugin

router = APIRouter(prefix="/protocols/hummingbird/v1")


# ---------- response models ----------------------------------------------


class BookItem(BaseModel):
    id: int
    title: str
    url: str
    due_date: str | None = None


class LoginRequest(BaseModel):
    username: str = ""
    password: str = ""


class LoginResponse(BaseModel):
    authenticated: bool
    username: str


class BookshelfListResponse(BaseModel):
    username: str
    items: list[BookItem]
    count: int


class ShelfActionResponse(BaseModel):
    username: str
    node_id: int
    action: str  # "add" or "remove"
    success: bool


class BookmarkRequest(BaseModel):
    bookmark: dict = {}


class BookmarkResponse(BaseModel):
    username: str
    node_id: int
    bookmark: dict


class BookmarkActionResponse(BaseModel):
    username: str
    node_id: int
    action: str  # "set" or "clear"
    success: bool


class SearchResponse(BaseModel):
    username: str
    query: str
    page: int
    items: list[BookItem]
    count: int
    total_pages: int | None = None
    total_results: int | None = None


class DownloadListing(BaseModel):
    format: int
    node_id: int
    filename: str
    kind: str  # "archive" | "single"
    files: list[str]
    count: int


# ---------- helpers -------------------------------------------------------


_EXTRA_MIMES = {
    ".brf": "application/x-brf",
    ".epub": "application/epub+zip",
    ".smil": "application/smil+xml",
    ".ncc": "text/html",
    ".azw3": "application/vnd.amazon.ebook",
}


def _guess_mime(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in _EXTRA_MIMES:
        return _EXTRA_MIMES[ext]
    mt, _ = mimetypes.guess_type(name)
    return mt or "application/octet-stream"


def _is_zip_archive(path: Path) -> bool:
    return path.suffix.lower() == ".zip"


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _flatten_to_items(books: list[BookRecord], base_url: str) -> list[BookItem]:
    items: list[BookItem] = []
    for b in books:
        for fmt in b.formats:
            if fmt.id == 0:
                continue
            suffix = f", narrated by {fmt.narrator}" if fmt.narrator else ""
            items.append(
                BookItem(
                    id=b.id,
                    title=f"{b.title} ({fmt.label}{suffix})",
                    url=f"{base_url}/protocols/hummingbird/v1/download/{fmt.id}/{b.id}/",
                    due_date=b.due_date,
                )
            )
    return items


# ---------- /login --------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest) -> LoginResponse:
    # Credentials MUST come in the request body. The previous shape
    # accepted them as ?username=&password= query params, but query
    # strings get captured by access logs (uvicorn, Traefik, any CDN
    # in between, and the user's local Console.app history), which is
    # plaintext-credential-leak-by-default. Body-only closes that.
    user = payload.username or settings.username
    pw = payload.password or settings.password
    if not user or not pw:
        raise HTTPException(
            400, "username and password required (or set HUMMINGBIRD_USERNAME/PASSWORD)"
        )

    plugin = active_plugin()
    if plugin is not None:
        try:
            ok = await plugin.authenticate(user, pw)
        except NotImplementedError:
            ok = None
        if ok is not None:
            if not ok:
                raise HTTPException(401, "authentication failed")
            storage.write_session(user, via="plugin")
            # Populate the auth cache so subsequent REST hits within
            # TTL don't re-trigger the plugin's expensive authenticate.
            auth_module.remember_login(user, pw)
            return LoginResponse(authenticated=True, username=user)

    # Standalone fallback: match .env credentials.
    if user != settings.username or pw != settings.password:
        raise HTTPException(401, "authentication failed")
    storage.write_session(user, via="env")
    auth_module.remember_login(user, pw)
    return LoginResponse(authenticated=True, username=user)


# ---------- /bookshelf ----------------------------------------------------


@router.get("/bookshelf/list", response_model=BookshelfListResponse)
async def bookshelf_list(
    request: Request,
    user: str = Depends(auth_module.current_user),
) -> BookshelfListResponse:
    plugin = active_plugin()
    if plugin is not None:
        try:
            books = await plugin.list_bookshelf(user)
        except NotImplementedError:
            books = storage.list_bookshelf(user)
    else:
        books = storage.list_bookshelf(user)
    items = _flatten_to_items(books, _base_url(request))
    return BookshelfListResponse(username=user, items=items, count=len(items))


@router.post("/bookshelf/add/{node_id}", response_model=ShelfActionResponse)
async def bookshelf_add(
    node_id: int,
    user: str = Depends(auth_module.current_user),
    format: Annotated[int, Query(ge=0, description="standalone only; plugin ignores")] = 0,
    title: Annotated[str, Query(description="standalone only; plugin ignores")] = "",
) -> ShelfActionResponse:
    plugin = active_plugin()
    if plugin is not None:
        try:
            ok = await plugin.add_to_bookshelf(user, node_id)
            return ShelfActionResponse(
                username=user, node_id=node_id, action="add", success=ok
            )
        except NotImplementedError:
            pass
    ok = storage.add_to_bookshelf(user, node_id, format=format, title=title)
    return ShelfActionResponse(username=user, node_id=node_id, action="add", success=ok)


@router.post("/bookshelf/remove/{node_id}", response_model=ShelfActionResponse)
async def bookshelf_remove(
    node_id: int,
    user: str = Depends(auth_module.current_user),
    format: Annotated[int | None, Query(description="standalone only; plugin ignores")] = None,
) -> ShelfActionResponse:
    plugin = active_plugin()
    if plugin is not None:
        try:
            ok = await plugin.remove_from_bookshelf(user, node_id)
            return ShelfActionResponse(
                username=user, node_id=node_id, action="remove", success=ok
            )
        except NotImplementedError:
            pass
    ok = storage.remove_from_bookshelf(user, node_id, format=format)
    return ShelfActionResponse(username=user, node_id=node_id, action="remove", success=ok)


# ---------- /bookshelf/bookmark -------------------------------------------
#
# Bookmark / progress-sync surface. The payload is treated as opaque --
# DODP-style bookmarks have a ``position`` field, BookPlayer-style ones
# carry ``currentTime`` / ``duration`` / ``isFinished``. The storage
# layer round-trips whatever shape the client sent, and the plugin layer
# can override (sync upstream) or defer (raise NotImplementedError) to
# the JSON-backed default storage.


@router.get(
    "/bookshelf/bookmark/{node_id}", response_model=BookmarkResponse
)
async def bookmark_get(
    node_id: int,
    user: str = Depends(auth_module.current_user),
) -> BookmarkResponse:
    plugin = active_plugin()
    if plugin is not None:
        try:
            bookmark = await plugin.get_bookmark(user, node_id)
            return BookmarkResponse(
                username=user, node_id=node_id, bookmark=bookmark or {}
            )
        except NotImplementedError:
            pass
    return BookmarkResponse(
        username=user, node_id=node_id, bookmark=storage.read_bookmark(user, node_id)
    )


@router.post(
    "/bookshelf/bookmark/{node_id}", response_model=BookmarkActionResponse
)
async def bookmark_set(
    node_id: int,
    payload: BookmarkRequest,
    user: str = Depends(auth_module.current_user),
) -> BookmarkActionResponse:
    plugin = active_plugin()
    if plugin is not None:
        try:
            ok = await plugin.set_bookmark(user, node_id, payload.bookmark or {})
            return BookmarkActionResponse(
                username=user, node_id=node_id, action="set", success=ok
            )
        except NotImplementedError:
            pass
    ok = storage.write_bookmark(user, node_id, payload.bookmark or {})
    return BookmarkActionResponse(
        username=user, node_id=node_id, action="set", success=ok
    )


# ---------- /search -------------------------------------------------------


@router.get("/search", response_model=SearchResponse)
@router.get("/search/", response_model=SearchResponse, include_in_schema=False)
async def search_endpoint(
    request: Request,
    q: Annotated[str, Query(min_length=1, description="search query")],
    formats: Annotated[
        list[int] | None,
        Query(description="restrict to these format ids; repeat param: ?formats=1&formats=2"),
    ] = None,
    page: Annotated[int, Query(ge=0)] = 0,
    user: str = Depends(auth_module.current_user),
) -> SearchResponse:
    plugin = active_plugin()
    if plugin is not None:
        try:
            result: SearchResult = await plugin.search(user, q, formats, page)
        except NotImplementedError:
            result = SearchResult(query=q, page=page, books=[])
    else:
        result = SearchResult(query=q, page=page, books=[])

    # If the plugin didn't filter, enforce the formats filter here.
    books = result.books
    if formats:
        allowed = set(formats)
        books = [
            BookRecord(
                id=b.id,
                title=b.title,
                formats=[f for f in b.formats if f.id in allowed],
            )
            for b in books
        ]
        books = [b for b in books if b.formats]

    items = _flatten_to_items(books, _base_url(request))
    return SearchResponse(
        username=user,
        query=result.query,
        page=result.page,
        items=items,
        count=len(items),
        total_pages=result.total_pages,
        total_results=result.total_results,
    )


# ---------- /download -----------------------------------------------------


def _stream_zip_entry(zip_path: Path, inner_path: str) -> StreamingResponse:
    try:
        with zipfile.ZipFile(zip_path) as z:
            info = z.getinfo(inner_path)
    except KeyError:
        raise HTTPException(404, f"'{inner_path}' not in {zip_path.name}") from None
    if info.is_dir():
        raise HTTPException(400, f"'{inner_path}' is a directory")

    def iterator():
        with zipfile.ZipFile(zip_path) as z:
            with z.open(inner_path) as f:
                while chunk := f.read(65536):
                    yield chunk

    return StreamingResponse(
        iterator(),
        media_type=_guess_mime(inner_path),
        headers={
            "Content-Length": str(info.file_size),
            "Content-Disposition": f'inline; filename="{Path(inner_path).name}"',
        },
    )


@router.get(
    "/download/{fmt}/{node_id}/_info",
    response_model=DownloadListing,
)
@router.get(
    "/download/{fmt}/{node_id}/_info/",
    response_model=DownloadListing,
    include_in_schema=False,
)
async def download_info(
    fmt: int,
    node_id: int,
    user: str = Depends(auth_module.current_user),
) -> DownloadListing:
    """Return JSON metadata describing the cached file (single vs archive,
    filename, list of contents). Useful for DAISY-aware clients that want
    to inspect a DAISY 2.02 archive before fetching individual entries.

    Lived at ``/download/{fmt}/{node_id}/`` until v0.3.2 -- but clients
    like BookPlayer followed that URL blindly and got back JSON when
    they expected audio bytes, so the URL we hand out in /bookshelf/list
    now points at the file route below; this one's the explicit
    inspection path."""
    cache = await ensure_cached(fmt, node_id, username=user)
    if cache is None:
        raise HTTPException(
            404, f"no cached file for format={fmt} node_id={node_id}"
        )
    if _is_zip_archive(cache):
        with zipfile.ZipFile(cache) as z:
            files = [m.filename for m in z.infolist() if not m.is_dir()]
        kind = "archive"
    else:
        files = [cache.name]
        kind = "single"
    return DownloadListing(
        format=fmt, node_id=node_id, filename=cache.name,
        kind=kind, files=files, count=len(files),
    )


@router.get("/download/{fmt}/{node_id}/")
@router.get("/download/{fmt}/{node_id}")
async def download_file(
    fmt: int,
    node_id: int,
    user: str = Depends(auth_module.current_user),
):
    """Serve the cached file (or zip archive) for (fmt, node_id) as
    bytes. Populates the cache via the plugin path if needed. This is
    what `BookItem.url` points at so the client can do a single
    blind GET and receive playable audio (or a DAISY zip the client
    can extract locally)."""
    cache = await ensure_cached(fmt, node_id, username=user)
    if cache is None:
        raise HTTPException(
            404, f"no cached file for format={fmt} node_id={node_id}"
        )
    return FileResponse(cache, media_type=_guess_mime(cache.name), filename=cache.name)


@router.get("/download/{fmt}/{node_id}/{path:path}")
async def download_fetch(
    fmt: int,
    node_id: int,
    path: str,
    user: str = Depends(auth_module.current_user),
):
    cache = await ensure_cached(fmt, node_id, username=user)
    if cache is None:
        raise HTTPException(
            404, f"no cached file for format={fmt} node_id={node_id}"
        )
    if _is_zip_archive(cache):
        return _stream_zip_entry(cache, path)
    if path != cache.name:
        raise HTTPException(
            404,
            f"cached file is '{cache.name}', not '{path}' "
            "(single-file caches do not support arbitrary paths)",
        )
    return FileResponse(cache, media_type=_guess_mime(cache.name), filename=cache.name)
