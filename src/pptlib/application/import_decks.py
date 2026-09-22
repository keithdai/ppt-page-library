from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pptlib.config import Settings
from pptlib.discovery.scanner import scan_paths
from pptlib.infrastructure.db.repositories import DeckRepository, ImportedDeck
from pptlib.ingestion.parser import parse_pptx
from pptlib.rendering.thumbnails import ThumbnailError, render_deck_thumbnails

logger = logging.getLogger("pptlib.import")

# A progress event is a small JSON-friendly dict. ``stage`` is one of:
#   "scan"      total files discovered            -> {stage, total}
#   "file"      starting a file                   -> {stage, index, total, name}
#   "parse"     text extracted for the file       -> {stage, index, total, name, slides}
#   "render"    a thumbnail page finished          -> {stage, index, total, name, page, pages}
#   "file_done" a file finished                   -> {stage, index, total, name, created, slides}
#   "error"     a file failed                     -> {stage, index, total, name, error}
ProgressEvent = dict[str, object]
ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True, slots=True)
class ImportReport:
    discovered: int
    imported: tuple[ImportedDeck, ...]
    skipped: int
    failed: tuple[tuple[Path, str], ...]


def scan_and_import(
    connection: sqlite3.Connection,
    roots: list[Path] | tuple[Path, ...],
    *,
    settings: Settings | None = None,
    on_progress: ProgressCallback | None = None,
) -> ImportReport:
    def emit(event: ProgressEvent) -> None:
        if on_progress is not None:
            on_progress(event)

    max_file_bytes = settings.max_file_bytes if settings else 500 * 1024 * 1024
    max_package_bytes = (
        settings.max_uncompressed_package_bytes if settings else 2 * 1024 * 1024 * 1024
    )
    max_parts = settings.max_parts_per_package if settings else 20_000
    # ``roots`` may mix explicit PPTX files and directories. Files are indexed in
    # place (no copy) — their real path is recorded as the canonical source.
    scanned = scan_paths(list(roots), max_file_bytes=max_file_bytes)
    total = len(scanned)
    emit({"stage": "scan", "total": total})
    imported: list[ImportedDeck] = []
    failed: list[tuple[Path, str]] = []
    repository = DeckRepository(connection)
    for index, item in enumerate(scanned, start=1):
        name = item.path.name
        emit({"stage": "file", "index": index, "total": total, "name": name})
        try:
            parsed = parse_pptx(
                item.path,
                max_uncompressed_package_bytes=max_package_bytes,
                max_parts=max_parts,
            )
            imported_deck = repository.import_parsed(item, parsed)
            imported.append(imported_deck)
            emit(
                {
                    "stage": "parse",
                    "index": index,
                    "total": total,
                    "name": name,
                    "slides": imported_deck.slide_count,
                }
            )
            if settings is not None:
                try:
                    render_deck_thumbnails(
                        imported_deck.path,
                        imported_deck.version_id,
                        imported_deck.slide_count,
                        assets_dir=settings.assets_dir,
                        temp_dir=settings.temp_dir,
                        renderer=settings.renderer,
                        thumbnail_long_edge=settings.thumbnail_long_edge,
                        preview_long_edge=settings.preview_long_edge,
                        on_page=lambda page, pages, _i=index, _n=name: emit(
                            {
                                "stage": "render",
                                "index": _i,
                                "total": total,
                                "name": _n,
                                "page": page,
                                "pages": pages,
                            }
                        ),
                    )
                except ThumbnailError as error:
                    logger.warning(
                        "thumbnail rendering skipped",
                        extra={"path": str(imported_deck.path), "error": str(error)},
                    )
            emit(
                {
                    "stage": "file_done",
                    "index": index,
                    "total": total,
                    "name": name,
                    "created": imported_deck.created,
                    "slides": imported_deck.slide_count,
                }
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            failed.append((item.path, str(error)))
            emit(
                {
                    "stage": "error",
                    "index": index,
                    "total": total,
                    "name": name,
                    "error": str(error),
                }
            )
    created = sum(1 for item in imported if item.created)
    return ImportReport(len(scanned), tuple(imported), len(imported) - created, tuple(failed))
