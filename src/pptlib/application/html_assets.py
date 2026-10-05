"""Application services for HTML preview and retryable preview generation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pptlib.config import Settings
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.html.adapter import ingest_html, write_canonical
from pptlib.html.source import HtmlSource
from pptlib.infrastructure.db.connection import connect


def open_html_source(settings: Settings, path: Path) -> HtmlSource:
    return HtmlSource(
        path,
        max_bytes=settings.max_file_bytes,
        max_parts=settings.max_parts_per_package,
        max_package_bytes=settings.max_uncompressed_package_bytes,
    )


def verify_html_source(settings: Settings, path: Path, expected: str) -> HtmlSource:
    source = open_html_source(settings, path)
    if source.fingerprint != expected:
        raise AppError(ErrorCode.SOURCE_CHANGED, "HTML 来源或依赖已变化，请重新入库")
    return source


def render_html_version(
    settings: Settings,
    path: Path,
    version_id: str,
    expected: str,
    *,
    on_page: Callable[[int, int], None] | None = None,
) -> int:
    from pptlib.rendering.html import render_html_thumbnails

    manifest = settings.assets_dir / "html-manifests" / f"{version_id}.json"
    if not manifest.is_file():
        source = verify_html_source(settings, path, expected)
        result = ingest_html(source, temp_dir=settings.temp_dir)
        write_canonical(result, settings.assets_dir, version_id)
    canonical = json.loads(manifest.read_text("utf-8"))
    keys = canonical["pptlib"]["page_keys"]
    return render_html_thumbnails(
        path,
        version_id,
        len(keys),
        assets_dir=settings.assets_dir,
        temp_dir=settings.temp_dir,
        expected_sha256=expected,
        page_keys=keys,
        thumbnail_long_edge=settings.thumbnail_long_edge,
        preview_long_edge=settings.preview_long_edge,
        on_page=on_page,
    )


def preview_source(settings: Settings, slide_id: str) -> tuple[HtmlSource, dict[str, Any]]:
    if not settings.html_enabled:
        raise AppError(ErrorCode.REQUEST_INVALID, "HTML 功能未启用，请设置 PPTLIB_ENABLE_HTML=1")
    connection = connect(settings.database_path)
    try:
        row = connection.execute(
            """
            SELECT d.canonical_path, v.sha256, v.source_format, v.warnings_json,
                   s.page_key, s.slide_number, s.capabilities_json
            FROM slides s
            JOIN deck_versions v ON v.id = s.deck_version_id
            JOIN decks d ON d.id = v.deck_id
            WHERE s.id = ? AND d.current_version_id = v.id
            """,
            (slide_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None or row["source_format"] != "render_deck_html":
        raise AppError(ErrorCode.NOT_FOUND, "HTML 页面不存在或来源版本已过期")
    source = verify_html_source(settings, Path(row["canonical_path"]), str(row["sha256"]))
    return source, {
        "slide_id": slide_id,
        "page_key": str(row["page_key"]),
        "slide_number": int(row["slide_number"]),
        "preview_profile": "sanitized-v1",
        "warnings": json.loads(row["warnings_json"]),
    }


def retry_html_previews(settings: Settings) -> dict[str, Any]:
    if not settings.html_enabled:
        raise AppError(ErrorCode.REQUEST_INVALID, "HTML 功能未启用")
    connection = connect(settings.database_path)
    try:
        rows = connection.execute(
            """
            SELECT d.canonical_path, v.id, v.sha256
            FROM decks d JOIN deck_versions v ON v.id = d.current_version_id
            WHERE v.source_format = 'render_deck_html' ORDER BY d.canonical_path
            """
        ).fetchall()
    finally:
        connection.close()
    rendered = 0
    failures = []
    from pptlib.rendering.thumbnails import ThumbnailError

    for row in rows:
        try:
            rendered += render_html_version(
                settings,
                Path(row[0]),
                str(row[1]),
                str(row[2]),
            )
        except (AppError, OSError, ValueError, ThumbnailError) as error:
            failures.append({"path": str(row[0]), "error": str(error)})
    return {"ok": not failures, "page_count": rendered, "failed": failures}
