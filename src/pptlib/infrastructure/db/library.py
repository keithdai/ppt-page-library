from __future__ import annotations

import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pptlib.application.library import (
    DEFAULT_LIBRARY_FILTERS,
    DeckSummary,
    LibraryFacets,
    LibraryFilters,
    SearchPage,
    SelectionStore,
    SlideCatalog,
    SlideSummary,
    validate_filters,
)
from pptlib.domain.ids import new_id
from pptlib.export.ooxml import SlideRef
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.repositories import (
    count_search_slides,
    library_facets,
    search_slides,
)


@contextmanager
def _open(database_path: Path) -> Generator[sqlite3.Connection, None, None]:
    connection = connect(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _thumbnail_url(slide_id: str, assets_dir: Path | None) -> str | None:
    if assets_dir is None:
        return None
    thumbnail = assets_dir / "thumbnails" / f"{slide_id}.jpg"
    return f"/assets/thumbnails/{slide_id}.jpg" if thumbnail.is_file() else None


def _preview_url(slide_id: str, assets_dir: Path | None) -> str | None:
    if assets_dir is None:
        return None
    preview = assets_dir / "previews" / f"{slide_id}.jpg"
    if preview.is_file():
        return f"/assets/previews/{slide_id}.jpg"
    thumbnail = assets_dir / "thumbnails" / f"{slide_id}.jpg"
    return f"/assets/thumbnails/{slide_id}.jpg" if thumbnail.is_file() else None


def _summary(row: sqlite3.Row, assets_dir: Path | None = None) -> SlideSummary:
    text = "\n".join(value for value in (row["body_text"], row["notes_text"]) if value)
    thumbnail_url = _thumbnail_url(str(row["slide_id"]), assets_dir)
    return SlideSummary(
        id=str(row["slide_id"]),
        deck_id=str(row["deck_id"]),
        deck_name=str(row["display_name"]),
        slide_number=int(row["slide_number"]),
        title=str(row["title"]),
        text=text,
        thumbnail_url=thumbnail_url,
        preview_url=_preview_url(str(row["slide_id"]), assets_dir),
        topic=str(row["topic"] or ""),
        page_type=str(row["page_type"] or ""),
        subtopic=str(row["subtopic"] or ""),
        confidence=str(row["confidence"] or ""),
        classification_source=str(row["classification_source"] or ""),
        classifier_version=str(row["classifier_version"] or ""),
    )


class SqliteSlideCatalog(SlideCatalog):
    def __init__(self, database_path: Path, assets_dir: Path | None = None) -> None:
        self.database_path = database_path
        self.assets_dir = assets_dir

    def search(
        self,
        query: str,
        *,
        page: int,
        page_size: int,
        filters: LibraryFilters = DEFAULT_LIBRARY_FILTERS,
    ) -> SearchPage:
        validate_filters(filters)
        offset = (page - 1) * page_size
        with _open(self.database_path) as connection:
            results = search_slides(
                connection, query, limit=page_size, offset=offset, filters=filters
            )
            total = count_search_slides(connection, query, filters=filters)
        items = tuple(
            SlideSummary(
                id=result.slide_id,
                deck_id=result.deck_id,
                deck_name=result.path.stem,
                slide_number=result.slide_number,
                title=result.title,
                text="\n".join(value for value in (result.body_text, result.notes_text) if value),
                thumbnail_url=_thumbnail_url(result.slide_id, self.assets_dir),
                preview_url=_preview_url(result.slide_id, self.assets_dir),
                topic=result.topic,
                page_type=result.page_type,
                subtopic=result.subtopic,
                confidence=result.confidence,
                classification_source=result.classification_source,
                classifier_version=result.classifier_version,
            )
            for result in results
        )
        return SearchPage(items, total, page, page_size, query, filters)

    def facets(self) -> LibraryFacets:
        with _open(self.database_path) as connection:
            return library_facets(connection)

    def get(self, slide_id: str) -> SlideSummary | None:
        with _open(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT s.id AS slide_id, d.id AS deck_id, d.display_name,
                       s.slide_number, s.title, s.body_text, s.notes_text,
                       t.topic, t.page_type, t.subtopic, t.confidence,
                       t.classification_source, t.classifier_version
                FROM slides s
                JOIN deck_versions v ON v.id = s.deck_version_id
                JOIN decks d ON d.id = v.deck_id
                LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
                WHERE s.id = ? AND v.status = 'parsed' AND d.current_version_id = v.id
                """,
                (slide_id,),
            ).fetchone()
        return _summary(row, self.assets_dir) if row is not None else None

    def decks(self) -> tuple[DeckSummary, ...]:
        with _open(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT d.id, d.display_name, v.slide_count,
                       (
                         SELECT s.id FROM slides s
                         WHERE s.deck_version_id = v.id
                         ORDER BY s.slide_number LIMIT 1
                       ) AS cover_slide_id
                FROM decks d
                JOIN deck_versions v ON v.id = d.current_version_id
                WHERE v.status = 'parsed'
                ORDER BY d.display_name
                """
            ).fetchall()
        return tuple(
            DeckSummary(
                id=str(row[0]),
                name=str(row[1]),
                slide_count=int(row[2]),
                cover_thumbnail_url=(
                    _thumbnail_url(str(row[3]), self.assets_dir) if row[3] else None
                ),
            )
            for row in rows
        )

    def get_deck(self, deck_id: str) -> DeckSummary | None:
        return next((deck for deck in self.decks() if deck.id == deck_id), None)


class SqliteSelectionStore(SelectionStore):
    def __init__(
        self,
        database_path: Path,
        selection_id: str = "selection_default",
        assets_dir: Path | None = None,
    ) -> None:
        self.database_path = database_path
        self.selection_id = selection_id
        self.assets_dir = assets_dir

    def _ensure(self, connection: sqlite3.Connection) -> None:
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO selections(id, name, created_at, updated_at)
            VALUES (?, '默认选片单', ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (self.selection_id, now, now),
        )

    def list(self) -> tuple[SlideSummary, ...]:
        with _open(self.database_path) as connection:
            self._ensure(connection)
            rows = connection.execute(
                """
                SELECT s.id AS slide_id, d.id AS deck_id, d.display_name,
                       s.slide_number, s.title, s.body_text, s.notes_text,
                       t.topic, t.page_type, t.subtopic, t.confidence,
                       t.classification_source, t.classifier_version
                FROM selection_items i
                JOIN slides s ON s.id = i.slide_id
                JOIN deck_versions v ON v.id = s.deck_version_id
                JOIN decks d ON d.id = v.deck_id
                LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
                WHERE i.selection_id = ?
                ORDER BY i.sort_order
                """,
                (self.selection_id,),
            ).fetchall()
        return tuple(_summary(row, self.assets_dir) for row in rows)

    def add(self, slide: SlideSummary) -> tuple[SlideSummary, ...]:
        with _open(self.database_path) as connection:
            self._ensure(connection)
            row = connection.execute(
                "SELECT deck_version_id FROM slides WHERE id = ?", (slide.id,)
            ).fetchone()
            if row is None:
                return self.list()
            existing = connection.execute(
                """
                SELECT 1 FROM selection_items
                WHERE selection_id = ? AND slide_id = ?
                """,
                (self.selection_id, slide.id),
            ).fetchone()
            if existing is not None:
                return self.list()
            next_order = connection.execute(
                """
                SELECT COALESCE(MAX(sort_order), 0) + 1
                FROM selection_items WHERE selection_id = ?
                """,
                (self.selection_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO selection_items(
                    id, selection_id, slide_id, deck_version_id, source_page_number,
                    sort_order, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("selection_item"),
                    self.selection_id,
                    slide.id,
                    row["deck_version_id"],
                    slide.slide_number,
                    next_order,
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.execute(
                "UPDATE selections SET revision = revision + 1, updated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), self.selection_id),
            )
        return self.list()

    def remove(self, slide_id: str) -> tuple[SlideSummary, ...]:
        with _open(self.database_path) as connection:
            self._ensure(connection)
            connection.execute(
                "DELETE FROM selection_items WHERE selection_id = ? AND slide_id = ?",
                (self.selection_id, slide_id),
            )
            connection.execute(
                "UPDATE selections SET revision = revision + 1, updated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), self.selection_id),
            )
            self._compact(connection)
        return self.list()

    def reorder(self, slide_ids: Sequence[str]) -> tuple[SlideSummary, ...]:
        current = self.list()
        current_ids = {item.id for item in current}
        if set(slide_ids) != current_ids or len(slide_ids) != len(current):
            from pptlib.domain.errors import AppError, ErrorCode

            raise AppError(
                ErrorCode.REQUEST_INVALID,
                "reorder must contain every selected slide exactly once",
                details={"expected_ids": list(current_ids)},
            )
        with connect(self.database_path) as connection:
            self._ensure(connection)
            for index, slide_id in enumerate(slide_ids, start=1):
                connection.execute(
                    """
                    UPDATE selection_items
                    SET sort_order = ? WHERE selection_id = ? AND slide_id = ?
                    """,
                    (index * 1000, self.selection_id, slide_id),
                )
            self._compact(connection)
            connection.execute(
                "UPDATE selections SET revision = revision + 1, updated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), self.selection_id),
            )
        return self.list()

    def _compact(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT id FROM selection_items WHERE selection_id = ? ORDER BY sort_order",
            (self.selection_id,),
        ).fetchall()
        for index, row in enumerate(rows, start=1):
            connection.execute(
                "UPDATE selection_items SET sort_order = ? WHERE id = ?",
                (index, row["id"]),
            )


def selection_slide_refs(
    database_path: Path, selection_id: str = "selection_default"
) -> tuple[SlideRef, ...]:
    with _open(database_path) as connection:
        rows = connection.execute(
            """
            SELECT v.id, d.canonical_path, i.source_page_number, v.sha256
            FROM selection_items i
            JOIN deck_versions v ON v.id = i.deck_version_id
            JOIN decks d ON d.id = v.deck_id
            WHERE i.selection_id = ?
            ORDER BY i.sort_order
            """,
            (selection_id,),
        ).fetchall()
    return tuple(
        SlideRef(str(row[0]), Path(str(row[1])), int(row[2]), str(row[3])) for row in rows
    )
