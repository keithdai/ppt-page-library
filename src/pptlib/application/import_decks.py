from __future__ import annotations

import contextlib
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pptlib.config import Settings
from pptlib.discovery.scanner import (
    ScannedFile,
    ScanRules,
    discover_paths,
    discover_paths_with_diagnostics,
    hash_discovered_file,
)
from pptlib.domain.errors import AppError
from pptlib.infrastructure.db.repositories import (
    CurrentDeck,
    DeckRepository,
    ImportedDeck,
)
from pptlib.ingestion.parser import parse_pptx
from pptlib.rendering.thumbnails import (
    ThumbnailError,
    render_cache_is_complete,
    render_deck_thumbnails,
)

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
    removed: tuple[Path, ...] = ()
    cancelled: tuple[Path, ...] = ()


class MissingPolicy(StrEnum):
    KEEP = "keep"
    REMOVE = "remove"


def _unchanged_import(current: CurrentDeck) -> ImportedDeck:
    return ImportedDeck(
        current.deck_id,
        current.version_id,
        current.path,
        current.slide_count,
        False,
        "unchanged",
    )


def _ensure_pptx_assets(
    settings: Settings,
    path: Path,
    version_id: str,
    slide_count: int,
    *,
    on_page: Callable[[int, int], None] | None = None,
) -> None:
    if render_cache_is_complete(settings.assets_dir, version_id, slide_count):
        return
    render_deck_thumbnails(
        path,
        version_id,
        slide_count,
        assets_dir=settings.assets_dir,
        temp_dir=settings.temp_dir,
        renderer=settings.renderer,
        thumbnail_long_edge=settings.thumbnail_long_edge,
        preview_long_edge=settings.preview_long_edge,
        on_page=on_page,
    )


def _remove_cached_assets(
    settings: Settings,
    slide_ids: tuple[str, ...],
    version_ids: tuple[str, ...],
) -> None:
    for slide_id in slide_ids:
        for kind in ("thumbnails", "previews"):
            with contextlib.suppress(OSError):
                (settings.assets_dir / kind / f"{slide_id}.jpg").unlink()
    for version_id in version_ids:
        with contextlib.suppress(OSError):
            (settings.assets_dir / "render-meta" / f"{version_id}.json").unlink()
        for kind in ("html-manifests", "html-render-meta"):
            with contextlib.suppress(OSError):
                (settings.assets_dir / kind / f"{version_id}.json").unlink()


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _directory_roots(roots: list[Path] | tuple[Path, ...]) -> tuple[Path, ...]:
    resolved = sorted(
        {root.expanduser().resolve() for root in roots if root.expanduser().is_dir()},
        key=lambda path: (len(path.parts), str(path).casefold()),
    )
    collapsed: list[Path] = []
    for root in resolved:
        if not any(_is_below(root, parent) for parent in collapsed):
            collapsed.append(root)
    return tuple(collapsed)


def _relocation_candidate(
    repository: DeckRepository,
    scanned: ScannedFile,
    roots: tuple[Path, ...],
    new_hash_counts: Counter[str],
) -> CurrentDeck | None:
    if not roots or new_hash_counts[scanned.sha256] != 1:
        return None
    candidates = [
        current
        for current in repository.current_for_sha256(scanned.sha256)
        if any(_is_below(current.path, root) for root in roots)
        and _path_was_relocated(current.path, scanned.path)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _path_was_relocated(old_path: Path, new_path: Path) -> bool:
    if not old_path.is_file():
        return True
    try:
        return old_path.samefile(new_path)
    except OSError:
        return False


def _source_matches_scan(scanned: ScannedFile) -> bool:
    try:
        current = scanned.path.stat()
    except OSError:
        return False
    return (
        current.st_size,
        current.st_mtime_ns,
        current.st_ctime_ns,
    ) == (
        scanned.size_bytes,
        scanned.mtime_ns,
        scanned.ctime_ns,
    )


def _register_source_roots(
    connection: sqlite3.Connection,
    roots: tuple[Path, ...],
) -> None:
    now = datetime.now(UTC).isoformat()
    tracked = {
        Path(str(row[0])): str(row[1])
        for row in connection.execute("SELECT path, id FROM source_roots").fetchall()
    }
    for root in roots:
        covering = next((path for path in tracked if _is_below(root, path)), None)
        if covering is not None:
            connection.execute(
                "UPDATE source_roots SET enabled = 1, updated_at = ? WHERE id = ?",
                (now, tracked[covering]),
            )
            continue
        descendants = [path for path in tracked if _is_below(path, root)]
        for descendant in descendants:
            connection.execute(
                "DELETE FROM source_roots WHERE id = ?",
                (tracked.pop(descendant),),
            )
        root_id = "root_" + sha256(str(root).encode("utf-8")).hexdigest()[:32]
        connection.execute(
            """
            INSERT INTO source_roots(id, path, enabled, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(path) DO UPDATE SET enabled = 1, updated_at = excluded.updated_at
            """,
            (root_id, str(root), now, now),
        )
        tracked[root] = root_id


def _sync_removed_sources(
    connection: sqlite3.Connection,
    roots: tuple[Path, ...],
    settings: Settings | None,
    *,
    candidate_paths: set[Path] | None = None,
) -> tuple[Path, ...]:
    if not roots and candidate_paths is None:
        return ()
    candidates = []
    for row in connection.execute(
        "SELECT id, canonical_path, current_version_id FROM decks"
    ).fetchall():
        path = Path(str(row[1]))
        in_scope = (
            path in candidate_paths
            if candidate_paths is not None
            else any(_is_below(path, root) for root in roots)
        )
        if in_scope and not path.is_file():
            candidates.append((str(row[0]), path, row[2] is not None))
    if not candidates:
        return ()

    removed_paths: list[Path] = []
    assets_to_remove: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    connection.execute("BEGIN IMMEDIATE")
    try:
        for deck_id, path, _was_visible in candidates:
            latest = connection.execute(
                "SELECT canonical_path, current_version_id FROM decks WHERE id = ?",
                (deck_id,),
            ).fetchone()
            if latest is None or Path(str(latest[0])) != path or path.is_file():
                continue
            was_visible = latest[1] is not None
            slide_ids = tuple(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT s.id
                    FROM slides s
                    JOIN deck_versions v ON v.id = s.deck_version_id
                    WHERE v.deck_id = ?
                    """,
                    (deck_id,),
                ).fetchall()
            )
            version_ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM deck_versions WHERE deck_id = ?",
                    (deck_id,),
                ).fetchall()
            )
            referenced = False
            if slide_ids:
                placeholders = ",".join("?" * len(slide_ids))
                referenced = (
                    connection.execute(
                        f"""
                        SELECT 1 FROM selection_items
                        WHERE slide_id IN ({placeholders})
                        LIMIT 1
                        """,
                        slide_ids,
                    ).fetchone()
                    is not None
                )
            if referenced:
                if was_visible:
                    connection.execute(
                        """
                        UPDATE decks
                        SET current_version_id = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (datetime.now(UTC).isoformat(), deck_id),
                    )
            else:
                for slide_id in slide_ids:
                    connection.execute(
                        "DELETE FROM slide_fts WHERE slide_id = ?",
                        (slide_id,),
                    )
                connection.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
                assets_to_remove.append((slide_ids, version_ids))
            if was_visible:
                removed_paths.append(path)
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise

    if settings is not None:
        for slide_ids, version_ids in assets_to_remove:
            _remove_cached_assets(settings, slide_ids, version_ids)
    return tuple(sorted(removed_paths, key=lambda path: str(path).casefold()))


def sync_removed_sources(
    connection: sqlite3.Connection,
    roots: list[Path] | tuple[Path, ...],
    *,
    settings: Settings | None = None,
) -> tuple[Path, ...]:
    return _sync_removed_sources(connection, _directory_roots(roots), settings)


def sync_removed_source_paths(
    connection: sqlite3.Connection,
    paths: list[Path] | tuple[Path, ...],
    *,
    settings: Settings | None = None,
) -> tuple[Path, ...]:
    candidates = {path.expanduser().resolve() for path in paths}
    return _sync_removed_sources(
        connection,
        (),
        settings,
        candidate_paths=candidates,
    )


def scan_and_import(
    connection: sqlite3.Connection,
    roots: list[Path] | tuple[Path, ...],
    *,
    settings: Settings | None = None,
    on_progress: ProgressCallback | None = None,
    scan_rules: ScanRules | None = None,
    missing_policy: MissingPolicy = MissingPolicy.REMOVE,
    repair_existing_assets: bool = True,
    relocation_roots: list[Path] | tuple[Path, ...] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ImportReport:
    def emit(event: ProgressEvent) -> None:
        if on_progress is not None:
            on_progress(event)

    def page_progress(file_index: int, file_name: str) -> Callable[[int, int], None]:
        def report(page: int, pages: int) -> None:
            emit(
                {
                    "stage": "render",
                    "index": file_index,
                    "total": total,
                    "name": file_name,
                    "page": page,
                    "pages": pages,
                }
            )

        return report

    max_file_bytes = settings.max_file_bytes if settings else 500 * 1024 * 1024
    max_package_bytes = (
        settings.max_uncompressed_package_bytes if settings else 2 * 1024 * 1024 * 1024
    )
    max_parts = settings.max_parts_per_package if settings else 20_000
    directory_roots = _directory_roots(roots)
    relocation_scope = (
        _directory_roots(relocation_roots) if relocation_roots is not None else directory_roots
    )
    _register_source_roots(connection, directory_roots)
    # Discovery only reads path metadata. Hashing and parsing happen below
    # after the current indexed version has been checked.
    if scan_rules is None:
        discovered = discover_paths(
            list(roots),
            max_file_bytes=max_file_bytes,
            include_html=bool(settings and settings.html_enabled),
        )
    else:
        discovered = list(discover_paths_with_diagnostics(list(roots), rules=scan_rules).accepted)
    total = len(discovered)
    emit({"stage": "scan", "total": total})
    imported: list[ImportedDeck] = []
    failed: list[tuple[Path, str]] = []
    repository = DeckRepository(connection)
    relocation_scans: dict[Path, ScannedFile] = {}
    relocation_errors: dict[Path, OSError | ValueError] = {}
    relocation_hash_counts: Counter[str] = Counter()
    cancelled: list[Path] = []
    if directory_roots:
        for item in discovered:
            if should_cancel is not None and should_cancel():
                break
            if repository.current_for_path(item.path) is not None:
                continue
            try:
                relocation_scans[item.path] = hash_discovered_file(item)
            except (OSError, ValueError) as error:
                relocation_errors[item.path] = error
        relocation_hash_counts.update(scanned.sha256 for scanned in relocation_scans.values())

    for index, item in enumerate(discovered, start=1):
        if should_cancel is not None and should_cancel():
            cancelled.extend(candidate.path for candidate in discovered[index - 1 :])
            emit(
                {
                    "stage": "cancelled",
                    "index": index - 1,
                    "total": total,
                    "remaining": len(cancelled),
                }
            )
            break
        name = item.path.name
        is_html = item.path.suffix.lower() != ".pptx"
        emit({"stage": "file", "index": index, "total": total, "name": name})
        try:
            current = repository.current_for_path(item.path)
            if (
                current is not None
                and not is_html
                and current.size_bytes == item.size_bytes
                and current.mtime_ns == item.mtime_ns
                and current.ctime_ns == item.ctime_ns
            ):
                if settings is not None and repair_existing_assets:
                    _ensure_pptx_assets(
                        settings,
                        current.path,
                        current.version_id,
                        current.slide_count,
                        on_page=page_progress(index, name),
                    )
                imported_deck = _unchanged_import(current)
                imported.append(imported_deck)
                emit(
                    {
                        "stage": "file_done",
                        "index": index,
                        "total": total,
                        "name": name,
                        "created": False,
                        "action": "unchanged",
                        "slides": current.slide_count,
                    }
                )
                continue

            if current is None and item.path in relocation_errors:
                raise relocation_errors[item.path]
            scanned = relocation_scans.get(item.path)
            if scanned is None:
                scanned = hash_discovered_file(item)
            if not _source_matches_scan(scanned):
                raise OSError(f"source changed after hashing: {scanned.path}")
            if current is not None and scanned.sha256 == current.sha256:
                if is_html and settings is not None and repair_existing_assets:
                    from pptlib.application.html_assets import render_html_version

                    render_html_version(
                        settings,
                        scanned.path,
                        current.version_id,
                        scanned.sha256,
                        on_page=page_progress(index, name),
                    )
                elif settings is not None and repair_existing_assets:
                    _ensure_pptx_assets(
                        settings,
                        current.path,
                        current.version_id,
                        current.slide_count,
                        on_page=page_progress(index, name),
                    )
                repository.update_source_metadata(
                    current.version_id,
                    size_bytes=scanned.size_bytes,
                    mtime_ns=scanned.mtime_ns,
                    ctime_ns=scanned.ctime_ns,
                )
                imported_deck = _unchanged_import(current)
                imported.append(imported_deck)
                emit(
                    {
                        "stage": "file_done",
                        "index": index,
                        "total": total,
                        "name": name,
                        "created": False,
                        "action": "unchanged",
                        "slides": current.slide_count,
                    }
                )
                continue

            if current is None:
                relocation = _relocation_candidate(
                    repository,
                    scanned,
                    relocation_scope,
                    relocation_hash_counts,
                )
                if relocation is not None:
                    imported_deck = repository.relocate_current(relocation, scanned)
                    if is_html and settings is not None:
                        from pptlib.application.html_assets import render_html_version

                        render_html_version(
                            settings,
                            scanned.path,
                            imported_deck.version_id,
                            scanned.sha256,
                        )
                    elif settings is not None:
                        _ensure_pptx_assets(
                            settings,
                            imported_deck.path,
                            imported_deck.version_id,
                            imported_deck.slide_count,
                            on_page=page_progress(index, name),
                        )
                    imported.append(imported_deck)
                    emit(
                        {
                            "stage": "file_done",
                            "index": index,
                            "total": total,
                            "name": name,
                            "created": False,
                            "action": "moved",
                            "slides": imported_deck.slide_count,
                        }
                    )
                    continue

            html_result = None
            if is_html and settings is not None:
                from pptlib.application.html_assets import open_html_source
                from pptlib.html.adapter import ingest_html, write_canonical

                html_source = open_html_source(settings, scanned.path)
                if html_source.fingerprint != scanned.sha256:
                    raise ValueError("HTML 来源或依赖在导入过程中变化，请重试")
                html_result = ingest_html(html_source, temp_dir=settings.temp_dir)
                # Hash dependencies again before committing the version.
                if open_html_source(settings, scanned.path).fingerprint != scanned.sha256:
                    raise ValueError("HTML 来源或依赖在解析过程中变化，请重试")
                parsed = html_result.parsed
            else:
                parsed = parse_pptx(
                    scanned.path,
                    max_uncompressed_package_bytes=max_package_bytes,
                    max_parts=max_parts,
                )
            if not _source_matches_scan(scanned):
                raise OSError(f"source changed while parsing: {scanned.path}")
            imported_deck = repository.import_parsed(scanned, parsed)
            imported.append(imported_deck)
            if settings is not None:
                if html_result is not None:
                    write_canonical(html_result, settings.assets_dir, imported_deck.version_id)
                _remove_cached_assets(
                    settings,
                    imported_deck.obsolete_slide_ids,
                    imported_deck.obsolete_version_ids,
                )
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
                if is_html:
                    from pptlib.application.html_assets import render_html_version

                    render_html_version(
                        settings,
                        scanned.path,
                        imported_deck.version_id,
                        scanned.sha256,
                        on_page=page_progress(index, name),
                    )
                else:
                    _ensure_pptx_assets(
                        settings,
                        imported_deck.path,
                        imported_deck.version_id,
                        imported_deck.slide_count,
                        on_page=page_progress(index, name),
                    )
            emit(
                {
                    "stage": "file_done",
                    "index": index,
                    "total": total,
                    "name": name,
                    "created": imported_deck.created,
                    "action": imported_deck.action,
                    "slides": imported_deck.slide_count,
                }
            )
        except (OSError, ValueError, sqlite3.Error, ThumbnailError, AppError) as error:
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
    removed: tuple[Path, ...] = ()
    if missing_policy is MissingPolicy.REMOVE and not failed and not cancelled:
        removed = _sync_removed_sources(connection, directory_roots, settings)
    skipped = sum(1 for item in imported if item.action in {"unchanged", "moved"})
    return ImportReport(
        len(discovered),
        tuple(imported),
        skipped,
        tuple(failed),
        removed,
        tuple(cancelled),
    )
