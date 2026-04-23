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
    """One book with one-or-more formats. Used both by plugins and by JSON storage."""

    id: int
    title: str
    formats: list[FormatEntry] = field(default_factory=list)


@dataclass
class SearchResult:
    query: str
    page: int
    books: list[BookRecord]
    total_pages: int | None = None
    total_results: int | None = None
