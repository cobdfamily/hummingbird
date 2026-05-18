"""Shared data shapes used by plugins, storage, and HTTP response builders."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FormatEntry:
    id: int
    label: str
    narrator: str | None = None


@dataclass
class BookRecord:
    """One book with one-or-more formats. Used both by plugins and by JSON storage.

    ``due_date`` is an ISO-8601 UTC timestamp ('2026-06-01T00:00:00+00:00')
    when set; ``None`` for libraries without a loan period (NNELS keeps
    books on the bookshelf indefinitely). Clients (eg. BookPlayer) read
    this to auto-delete + auto-return expired loans without the user
    having to remember the deadline.
    """

    id: int
    title: str
    formats: list[FormatEntry] = field(default_factory=list)
    due_date: str | None = None


@dataclass
class SearchResult:
    query: str
    page: int
    books: list[BookRecord]
    total_pages: int | None = None
    total_results: int | None = None
