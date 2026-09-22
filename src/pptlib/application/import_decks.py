from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pptlib.config import Settings
from pptlib.discovery.scanner import scan_paths
from pptlib.infrastructure.db.repositories import DeckRepository, ImportedDeck
from pptlib.ingestion.parser import parse_pptx
from pptlib.rendering.thumbnails import ThumbnailError, render_deck_thumbnails

logger = logging.getLogger("pptlib.import")


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
) -> ImportReport:
    max_file_bytes = settings.max_file_bytes if settings else 500 * 1024 * 1024
    max_package_bytes = (
        settings.max_uncompressed_package_bytes if settings else 2 * 1024 * 1024 * 1024
    )
    max_parts = settings.max_parts_per_package if settings else 20_000
    # ``roots`` may mix explicit PPTX files and directories. Files are indexed in
    # place (no copy) — their real path is recorded as the canonical source.
    scanned = scan_paths(list(roots), max_file_bytes=max_file_bytes)
    imported: list[ImportedDeck] = []
    failed: list[tuple[Path, str]] = []
    repository = DeckRepository(connection)
    for item in scanned:
        try:
            parsed = parse_pptx(
                item.path,
                max_uncompressed_package_bytes=max_package_bytes,
                max_parts=max_parts,
            )
            imported_deck = repository.import_parsed(item, parsed)
            imported.append(imported_deck)
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
                    )
                except ThumbnailError as error:
                    logger.warning(
                        "thumbnail rendering skipped",
                        extra={"path": str(imported_deck.path), "error": str(error)},
                    )
        except (OSError, ValueError, sqlite3.Error) as error:
            failed.append((item.path, str(error)))
    created = sum(1 for item in imported if item.created)
    return ImportReport(len(scanned), tuple(imported), len(imported) - created, tuple(failed))
