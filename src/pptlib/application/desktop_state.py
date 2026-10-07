from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from pptlib.application.library import LibraryFilters, SlideSummary
from pptlib.config import Settings
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.library import SqliteSelectionStore, SqliteSlideCatalog
from pptlib.infrastructure.db.repositories import search_deck_ids

DEFAULT_SELECTION_ID = "selection_default"
MAX_PAGE_SIZE = 200
MAX_SELECTION_SIZE = 20_000
MAX_TASK_PROGRESS_BYTES = 64 * 1024
TASK_STATUSES = {"running", "stopping", "finished", "failed", "interrupted"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _desktop_slide(slide: SlideSummary) -> dict[str, object]:
    return {
        "slide_id": slide.id,
        "deck_id": slide.deck_id,
        "deck_name": slide.deck_name,
        "slide_number": slide.slide_number,
        "title": slide.title,
        "summary": slide.text,
        "topic": slide.topic,
        "subtopic": slide.subtopic,
        "page_type": slide.page_type,
        "source_format": slide.source_format,
        "page_key": slide.page_key,
        "page_kind": slide.page_kind,
        "capabilities": dict(slide.capabilities),
        "warnings": list(slide.warnings),
        "thumbnail_url": slide.thumbnail_url or "",
        "preview_url": slide.preview_url or "",
    }


def _library_revision(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT d.id, d.current_version_id, d.updated_at, v.updated_at,
               COUNT(s.id), COALESCE(MAX(t.classified_at), '')
        FROM decks d
        JOIN deck_versions v ON v.id = d.current_version_id
        LEFT JOIN slides s ON s.deck_version_id = v.id
        LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
        WHERE v.status = 'parsed'
        GROUP BY d.id, d.current_version_id, d.updated_at, v.updated_at
        ORDER BY d.id
        """
    ).fetchall()
    payload = json.dumps([tuple(row) for row in rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _task_row(connection: sqlite3.Connection) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT task_id, kind, status, started_at, updated_at, finished_at,
               progress_json, error_message
        FROM desktop_task_state WHERE slot = 'active'
        """
    ).fetchone()
    if row is None:
        return None
    return {
        "taskId": str(row["task_id"]),
        "kind": str(row["kind"]),
        "state": str(row["status"]),
        "startedAt": str(row["started_at"]),
        "updatedAt": str(row["updated_at"]),
        "finishedAt": str(row["finished_at"]) if row["finished_at"] else None,
        "progress": json.loads(str(row["progress_json"])),
        "error": str(row["error_message"]) if row["error_message"] else None,
    }


def desktop_bootstrap(
    settings: Settings,
    *,
    since_revision: str | None = None,
) -> dict[str, object]:
    catalog = SqliteSlideCatalog(settings.database_path, settings.assets_dir)
    selection = SqliteSelectionStore(
        settings.database_path,
        selection_id=DEFAULT_SELECTION_ID,
        assets_dir=settings.assets_dir,
    )
    with connect(settings.database_path) as connection:
        revision = _library_revision(connection)
        task = _task_row(connection)
    selection_items, selection_revision = selection.snapshot()
    unchanged = bool(since_revision and since_revision == revision)
    decks = () if unchanged else catalog.decks()
    facets = None if unchanged else catalog.facets().to_dict()
    return {
        "ok": True,
        "libraryRevision": revision,
        "unchanged": unchanged,
        "slideCount": sum(deck.slide_count for deck in decks) if not unchanged else None,
        "decks": [deck.to_dict() for deck in decks],
        "facets": facets,
        "selection": {
            "revision": selection_revision,
            "items": [_desktop_slide(item) for item in selection_items],
        },
        "task": task,
    }


def desktop_search(
    settings: Settings,
    *,
    query: str,
    page: int,
    page_size: int,
    filters: LibraryFilters,
) -> dict[str, object]:
    if page < 1:
        raise AppError(ErrorCode.REQUEST_INVALID, "page must be at least 1")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            f"page_size must be between 1 and {MAX_PAGE_SIZE}",
        )
    catalog = SqliteSlideCatalog(settings.database_path, settings.assets_dir)
    result = catalog.search(
        query.strip(),
        page=page,
        page_size=page_size,
        filters=filters,
    )
    with connect(settings.database_path) as connection:
        deck_ids = search_deck_ids(connection, query.strip(), filters)
    return {
        "ok": True,
        "items": [_desktop_slide(item) for item in result.items],
        "total": result.total,
        "page": result.page,
        "pageSize": result.page_size,
        "query": result.query,
        "filters": result.filters.to_dict(),
        "deckIds": list(deck_ids),
    }


def save_desktop_selection(
    settings: Settings,
    payload: dict[str, Any],
) -> dict[str, object]:
    raw_ids = payload.get("slideIds")
    if not isinstance(raw_ids, list) or len(raw_ids) > MAX_SELECTION_SIZE:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            f"slideIds must be a list with at most {MAX_SELECTION_SIZE} items",
        )
    if any(not isinstance(slide_id, str) for slide_id in raw_ids):
        raise AppError(ErrorCode.REQUEST_INVALID, "slideIds must contain strings")
    expected = payload.get("expectedRevision")
    if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool)):
        raise AppError(ErrorCode.REQUEST_INVALID, "expectedRevision must be an integer")
    store = SqliteSelectionStore(
        settings.database_path,
        selection_id=DEFAULT_SELECTION_ID,
        assets_dir=settings.assets_dir,
    )
    items, revision = store.replace(raw_ids, expected_revision=expected)
    return {
        "ok": True,
        "revision": revision,
        "items": [_desktop_slide(item) for item in items],
    }


def set_desktop_task(settings: Settings, payload: dict[str, Any]) -> dict[str, object]:
    task_id = payload.get("taskId")
    kind = payload.get("kind")
    status = payload.get("state")
    progress = payload.get("progress")
    error_message = payload.get("error")
    started_at = payload.get("startedAt")
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise AppError(ErrorCode.REQUEST_INVALID, "invalid taskId")
    if not isinstance(kind, str) or not kind or len(kind) > 120:
        raise AppError(ErrorCode.REQUEST_INVALID, "invalid task kind")
    if status not in TASK_STATUSES:
        raise AppError(ErrorCode.REQUEST_INVALID, "invalid task state")
    if progress is not None and not isinstance(progress, dict):
        raise AppError(ErrorCode.REQUEST_INVALID, "task progress must be an object")
    if error_message is not None and not isinstance(error_message, str):
        raise AppError(ErrorCode.REQUEST_INVALID, "task error must be text")
    progress_json = json.dumps(progress or {}, ensure_ascii=False, separators=(",", ":"))
    if len(progress_json.encode("utf-8")) > MAX_TASK_PROGRESS_BYTES:
        raise AppError(ErrorCode.REQUEST_INVALID, "task progress is too large")
    now = _now()
    start = started_at if isinstance(started_at, str) and started_at else now
    finished_at = now if status in {"finished", "failed", "interrupted"} else None
    with connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO desktop_task_state(
                slot, task_id, kind, status, started_at, updated_at,
                finished_at, progress_json, error_message
            ) VALUES ('active', ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slot) DO UPDATE SET
                task_id = excluded.task_id,
                kind = excluded.kind,
                status = excluded.status,
                started_at = excluded.started_at,
                updated_at = excluded.updated_at,
                finished_at = excluded.finished_at,
                progress_json = excluded.progress_json,
                error_message = excluded.error_message
            """,
            (
                task_id,
                kind,
                status,
                start,
                now,
                finished_at,
                progress_json,
                error_message[:2000] if error_message else None,
            ),
        )
        task = _task_row(connection)
    return {"ok": True, "task": task}


def recover_desktop_task(settings: Settings) -> dict[str, object]:
    now = _now()
    with connect(settings.database_path) as connection:
        changed = connection.execute(
            """
            UPDATE desktop_task_state
            SET status = 'interrupted', updated_at = ?, finished_at = ?,
                error_message = COALESCE(error_message, '应用退出前任务未正常结束')
            WHERE slot = 'active' AND status IN ('running', 'stopping')
            """,
            (now, now),
        ).rowcount
        task = _task_row(connection)
    return {"ok": True, "recovered": changed == 1, "task": task}
