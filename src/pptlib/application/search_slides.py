from __future__ import annotations

import sqlite3

from pptlib.infrastructure.db.repositories import SearchResult, search_slides


def find_slides(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[SearchResult]:
    return search_slides(connection, query, limit=limit, offset=offset)
