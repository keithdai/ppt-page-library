from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from pptlib.application.import_decks import MissingPolicy, ProgressCallback, scan_and_import
from pptlib.bootstrap import initialize
from pptlib.config import Settings
from pptlib.discovery.scanner import ScanRules, discover_paths_with_diagnostics
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.repositories import DeckRepository, library_revision
from pptlib.infrastructure.operation_lock import heavy_operation_lock

IMPORT_EXTENSIONS = (".pptx", ".html", ".htm", ".zip")
MAX_IMPORT_FILES = 100


def import_agent_files(
    settings: Settings,
    paths: Sequence[Path],
    *,
    allow_write: bool,
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, object]:
    if not allow_write:
        raise AppError(ErrorCode.REQUEST_INVALID, "import requires explicit --allow-write")
    if not 1 <= len(paths) <= MAX_IMPORT_FILES:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            f"import requires between 1 and {MAX_IMPORT_FILES} local files",
        )
    # File arguments explicitly opt into the local HTML adapter, not URL archival.
    import_settings = replace(settings, html_enabled=True)
    rules = ScanRules(
        formats=frozenset({"pptx", "html", "html_zip"}),
        max_file_bytes=settings.max_file_bytes,
        recursive=False,
    )
    expanded = [path.expanduser() for path in paths]
    for path in expanded:
        if path.is_symlink():
            raise AppError(ErrorCode.PATH_NOT_ALLOWED, "symlink sources cannot be imported")
        if not path.exists():
            raise AppError(
                ErrorCode.NOT_FOUND, "source file not found", details={"path": str(path)}
            )
        if not path.is_file():
            raise AppError(ErrorCode.REQUEST_INVALID, "import accepts files, not directories")
    discovery = discover_paths_with_diagnostics(expanded, rules=rules)
    if discovery.rejected:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "one or more sources cannot be imported",
            details={
                "rejected": [
                    {"path": str(issue.path), "reason": issue.reason, "message": issue.detail}
                    for issue in discovery.rejected
                ]
            },
        )
    sources = [item.path for item in discovery.accepted]
    with heavy_operation_lock(settings.home):
        initialize(import_settings)
        connection = connect(settings.database_path)
        try:
            report = scan_and_import(
                connection,
                sources,
                settings=import_settings,
                scan_rules=rules,
                missing_policy=MissingPolicy.KEEP,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
            repository = DeckRepository(connection)
            reported_paths = (
                {item.path for item in report.imported}
                | {path for path, _ in report.failed}
                | set(report.cancelled)
            )
            failures = list(report.failed)
            failures.extend(
                (path, "source was rejected or changed before import; retry this file")
                for path in sources if path not in reported_paths
            )
            indexed_by_path = {
                path: current
                for path, _ in failures
                if (current := repository.current_for_path(path)) is not None
            }
            failed_paths = {path for path, _ in report.failed}
            result: dict[str, object] = {
                "status": "completed",
                "discovered": report.discovered,
                "imported": [
                    {
                        "deck_id": item.deck_id,
                        "version_id": item.version_id,
                        "path": str(item.path),
                        "name": item.path.name,
                        "slide_count": item.slide_count,
                        "created": item.created,
                        "action": item.action,
                        "searchable": True,
                        "preview_ready": item.path not in failed_paths,
                    }
                    for item in report.imported
                ],
                "skipped": report.skipped,
                "failed": [
                    {
                        "path": str(path),
                        "message": message,
                        "searchable": path in indexed_by_path,
                        "deck_id": (
                            indexed_by_path[path].deck_id if path in indexed_by_path else None
                        ),
                    }
                    for path, message in failures
                ],
                "cancelled": [str(path) for path in report.cancelled],
                "cancellation_requested": bool(should_cancel and should_cancel()),
                "library_revision": library_revision(connection),
                "sources_retained_in_place": True,
            }
        finally:
            connection.close()
    if failures or report.cancelled or result["cancellation_requested"]:
        result["status"] = "incomplete"
        raise AppError(
            ErrorCode.IMPORT_INCOMPLETE,
            "import did not complete for every file; inspect details.result",
            details={"result": result},
        )
    return result
