from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pptlib.config import Settings
from pptlib.domain.errors import AppError
from pptlib.infrastructure.db.connection import connect
from pptlib.rendering.thumbnails import (
    ThumbnailError,
    render_cache_is_complete,
    render_deck_thumbnails,
)

logger = logging.getLogger("pptlib.render")
RenderProgress = Callable[[dict[str, object]], None]


@dataclass(frozen=True, slots=True)
class RenderAssetsReport:
    total: int
    repaired: int
    skipped: int
    pages: int
    failed: tuple[tuple[Path, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": not self.failed,
            "total": self.total,
            "repaired": self.repaired,
            "skipped": self.skipped,
            "pages": self.pages,
            "failed": [{"path": str(path), "error": error} for path, error in self.failed],
        }


def backfill_thumbnails(
    settings: Settings,
    *,
    on_progress: RenderProgress | None = None,
) -> RenderAssetsReport:
    """Render missing assets for already-indexed current deck versions."""

    def emit(event: dict[str, object]) -> None:
        if on_progress is not None:
            on_progress(event)

    if not settings.database_path.is_file():
        return RenderAssetsReport(0, 0, 0, 0, ())
    connection = connect(settings.database_path)
    try:
        rows = connection.execute(
            """
            SELECT d.canonical_path, v.id, v.slide_count, v.source_format, v.sha256
            FROM decks d
            JOIN deck_versions v ON v.id = d.current_version_id
            WHERE v.status = 'parsed'
            ORDER BY d.canonical_path
            """
        ).fetchall()
    finally:
        connection.close()

    total = len(rows)
    repaired = 0
    skipped = 0
    pages = 0
    failed: list[tuple[Path, str]] = []
    emit({"stage": "scan", "total": total})
    for index, row in enumerate(rows, 1):
        source_path = Path(str(row[0]))
        version_id = str(row[1])
        slide_count = int(row[2])
        source_format = str(row[3])

        def page_progress(
            page: int,
            total_pages: int,
            *,
            file_index: int = index,
            file_name: str = source_path.name,
        ) -> None:
            emit(
                {
                    "stage": "render",
                    "index": file_index,
                    "total": total,
                    "name": file_name,
                    "page": page,
                    "pages": total_pages,
                }
            )

        emit(
            {
                "stage": "file",
                "index": index,
                "total": total,
                "name": source_path.name,
                "slides": slide_count,
            }
        )
        try:
            if source_format == "render_deck_html":
                if settings.html_enabled:
                    from pptlib.application.html_assets import render_html_version

                    render_html_version(
                        settings,
                        source_path,
                        version_id,
                        str(row[4]),
                        on_page=page_progress,
                    )
                    repaired += 1
                    pages += slide_count
                else:
                    skipped += 1
                emit(
                    {
                        "stage": "file_done",
                        "index": index,
                        "total": total,
                        "name": source_path.name,
                        "action": "repaired" if settings.html_enabled else "skipped",
                        "slides": slide_count,
                    }
                )
                continue
            if render_cache_is_complete(
                settings.assets_dir,
                version_id,
                slide_count,
            ):
                skipped += 1
                emit(
                    {
                        "stage": "file_done",
                        "index": index,
                        "total": total,
                        "name": source_path.name,
                        "action": "skipped",
                        "slides": slide_count,
                    }
                )
                continue
            render_deck_thumbnails(
                source_path,
                version_id,
                slide_count,
                assets_dir=settings.assets_dir,
                temp_dir=settings.temp_dir,
                renderer=settings.renderer,
                thumbnail_long_edge=settings.thumbnail_long_edge,
                preview_long_edge=settings.preview_long_edge,
                generate_previews=True,
                on_page=page_progress,
            )
            repaired += 1
            pages += slide_count
            emit(
                {
                    "stage": "file_done",
                    "index": index,
                    "total": total,
                    "name": source_path.name,
                    "action": "repaired",
                    "slides": slide_count,
                }
            )
        except (ThumbnailError, AppError, OSError, ValueError) as error:
            failed.append((source_path, str(error)))
            logger.warning(
                "asset repair failed",
                extra={"path": str(source_path), "error": str(error)},
            )
            emit(
                {
                    "stage": "error",
                    "index": index,
                    "total": total,
                    "name": source_path.name,
                    "error": str(error),
                }
            )
    return RenderAssetsReport(
        total,
        repaired,
        skipped,
        pages,
        tuple(failed),
    )
