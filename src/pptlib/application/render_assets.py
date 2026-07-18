from __future__ import annotations

import logging
from pathlib import Path

from pptlib.config import Settings
from pptlib.infrastructure.db.connection import connect
from pptlib.rendering.thumbnails import ThumbnailError, render_deck_thumbnails

logger = logging.getLogger("pptlib.render")


def backfill_thumbnails(settings: Settings) -> int:
    """Render missing thumbnails for already-indexed current deck versions."""
    if not settings.database_path.is_file():
        return 0
    connection = connect(settings.database_path)
    try:
        rows = connection.execute(
            """
            SELECT d.canonical_path, v.id, v.slide_count
            FROM decks d
            JOIN deck_versions v ON v.id = d.current_version_id
            WHERE v.status = 'parsed'
            ORDER BY d.canonical_path
            """
        ).fetchall()
    finally:
        connection.close()

    rendered = 0
    for row in rows:
        try:
            rendered += render_deck_thumbnails(
                Path(str(row[0])),
                str(row[1]),
                int(row[2]),
                assets_dir=settings.assets_dir,
                temp_dir=settings.temp_dir,
            )
        except ThumbnailError as error:
            logger.warning(
                "thumbnail backfill skipped",
                extra={"path": str(row[0]), "error": str(error)},
            )
    return rendered
