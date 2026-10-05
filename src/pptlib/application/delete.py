"""Remove decks or individual slides from the local index.

Deletion is deliberately *index only*: it clears the SQLite rows and the cached
thumbnail/preview JPEGs, but it never touches the original PPTX on disk. The
source file is only ever referenced by path, so "从页库移除" leaves the user's
file exactly where it was.

The schema does most of the cascade for us — deleting a ``decks`` row cascades
to ``deck_versions`` → ``slides`` → ``slide_taxonomy``. Two things are not
covered by foreign keys and are handled here explicitly:

- ``slide_fts`` is an FTS5 virtual table with no foreign key, so its rows must
  be deleted by ``slide_id`` before the slides disappear.
- the rendered thumbnail/preview files live on disk under ``assets_dir`` and
  are removed by their deterministic ``{slide_id}.jpg`` name.

A slide that is still referenced by a saved selection (``selection_items`` uses
``ON DELETE RESTRICT``) cannot be removed; the caller gets a clear
:class:`AppError` instead of a raw integrity error.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pptlib.config import Settings
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.infrastructure.db.connection import connect


@dataclass(frozen=True, slots=True)
class DeleteResult:
    decks_removed: int
    slides_removed: int
    thumbnails_removed: int


def _slide_ids_for_decks(
    connection: sqlite3.Connection, deck_ids: tuple[str, ...]
) -> list[str]:
    if not deck_ids:
        return []
    placeholders = ",".join("?" * len(deck_ids))
    rows = connection.execute(
        f"""
        SELECT s.id
        FROM slides s
        JOIN deck_versions v ON v.id = s.deck_version_id
        WHERE v.deck_id IN ({placeholders})
        """,
        deck_ids,
    ).fetchall()
    return [str(row[0]) for row in rows]


def _remove_assets(assets_dir: Path, slide_ids: list[str]) -> int:
    """Delete cached thumbnail + preview JPEGs for the given slides."""
    removed = 0
    for slide_id in slide_ids:
        for sub in ("thumbnails", "previews"):
            target = assets_dir / sub / f"{slide_id}.jpg"
            try:
                target.unlink()
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                # A locked/unreadable cache file must not abort the deletion; the
                # DB rows are the source of truth and are already gone.
                continue
    return removed


def _guard_selection_references(
    connection: sqlite3.Connection, slide_ids: list[str]
) -> None:
    """Raise a friendly error if any slide is still used by a saved selection."""
    if not slide_ids:
        return
    placeholders = ",".join("?" * len(slide_ids))
    row = connection.execute(
        f"""
        SELECT s.slide_id, COUNT(*) AS n
        FROM selection_items s
        WHERE s.slide_id IN ({placeholders})
        GROUP BY s.slide_id
        LIMIT 1
        """,
        slide_ids,
    ).fetchone()
    if row is not None:
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "有页面仍在已保存的选片清单中，无法删除。请先从清单移除后再试。",
            details={"slide_id": str(row["slide_id"])},
        )


def _delete_fts(connection: sqlite3.Connection, slide_ids: list[str]) -> None:
    for slide_id in slide_ids:
        connection.execute("DELETE FROM slide_fts WHERE slide_id = ?", (slide_id,))


def _remove_orphan_html_caches(connection: sqlite3.Connection, assets_dir: Path) -> None:
    versions = {
        str(row[0]) for row in connection.execute("SELECT id FROM deck_versions").fetchall()
    }
    for kind in ("html-manifests", "html-render-meta"):
        for target in (assets_dir / kind).glob("ver_*.json"):
            if target.stem not in versions:
                try:
                    target.unlink()
                except OSError:
                    continue


def delete_decks(settings: Settings, deck_ids: list[str] | tuple[str, ...]) -> DeleteResult:
    """Remove whole files (decks) and every page they contributed.

    Only the local index and cached images are removed — the original PPTX
    files are never touched.
    """
    ids = tuple(dict.fromkeys(str(d).strip() for d in deck_ids if str(d).strip()))
    if not ids:
        raise AppError(ErrorCode.REQUEST_INVALID, "未提供要删除的 deck_id")
    if not settings.database_path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "本地索引数据库不存在，无需删除")
    connection = connect(settings.database_path)
    try:
        slide_ids = _slide_ids_for_decks(connection, ids)
        _guard_selection_references(connection, slide_ids)
        connection.execute("BEGIN")
        try:
            _delete_fts(connection, slide_ids)
            placeholders = ",".join("?" * len(ids))
            cursor = connection.execute(
                f"DELETE FROM decks WHERE id IN ({placeholders})", ids
            )
            decks_removed = cursor.rowcount
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        thumbs = _remove_assets(settings.assets_dir, slide_ids)
        _remove_orphan_html_caches(connection, settings.assets_dir)
    finally:
        connection.close()
    return DeleteResult(
        decks_removed=max(decks_removed, 0),
        slides_removed=len(slide_ids),
        thumbnails_removed=thumbs,
    )


def delete_slides(settings: Settings, slide_ids: list[str] | tuple[str, ...]) -> DeleteResult:
    """Remove individual pages, leaving the rest of the file in the library.

    When removing the last remaining slide of a version we also prune the empty
    ``deck``/``deck_versions`` rows so the file stops appearing in the library.
    """
    ids = tuple(dict.fromkeys(str(s).strip() for s in slide_ids if str(s).strip()))
    if not ids:
        raise AppError(ErrorCode.REQUEST_INVALID, "未提供要删除的 slide_id")
    if not settings.database_path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "本地索引数据库不存在，无需删除")
    connection = connect(settings.database_path)
    try:
        _guard_selection_references(connection, list(ids))
        placeholders = ",".join("?" * len(ids))
        existing = [
            str(row[0])
            for row in connection.execute(
                f"SELECT id FROM slides WHERE id IN ({placeholders})", ids
            ).fetchall()
        ]
        connection.execute("BEGIN")
        try:
            _delete_fts(connection, existing)
            cursor = connection.execute(
                f"DELETE FROM slides WHERE id IN ({placeholders})", ids
            )
            slides_removed = cursor.rowcount
            # Prune emptied versions first, then any deck left with no versions,
            # so a fully-emptied file disappears from the library instead of
            # lingering. Done in this order so a multi-version deck only drops
            # the versions that actually became empty.
            connection.execute(
                """
                DELETE FROM deck_versions
                WHERE NOT EXISTS (
                    SELECT 1 FROM slides s WHERE s.deck_version_id = deck_versions.id
                )
                """
            )
            connection.execute(
                """
                DELETE FROM decks
                WHERE NOT EXISTS (
                    SELECT 1 FROM deck_versions v WHERE v.deck_id = decks.id
                )
                """
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        thumbs = _remove_assets(settings.assets_dir, existing)
        _remove_orphan_html_caches(connection, settings.assets_dir)
    finally:
        connection.close()
    return DeleteResult(
        decks_removed=0,
        slides_removed=max(slides_removed, 0),
        thumbnails_removed=thumbs,
    )
