"""Archive a public HTML presentation and index its immutable local snapshot."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

from pptlib.application.html_assets import open_html_source
from pptlib.application.import_decks import scan_and_import
from pptlib.config import Settings
from pptlib.html.remote import RemoteCapture, normalize_url


def import_url(
    connection: sqlite3.Connection,
    url: str,
    *,
    settings: Settings,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, Any]:
    if not settings.html_enabled:
        raise ValueError("HTML 功能未启用，请设置 PPTLIB_ENABLE_HTML=1")
    url = normalize_url(url)
    archive_root = settings.assets_dir / "html-archives"
    archive_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".capture-", dir=archive_root) as directory:
        staging = Path(directory)
        capture = RemoteCapture(
            staging, max_bytes=settings.max_uncompressed_package_bytes,
            max_parts=settings.max_parts_per_package, on_progress=on_progress,
        )
        entry, metadata = capture.capture(url)
        # Validate the full page/dependency structure before making any source visible.
        source = open_html_source(settings, entry)
        fingerprint = source.fingerprint
        safe_title = re.sub(r'[^\w\u4e00-\u9fff .-]', "_", str(metadata["title"]))[:80].strip(" .")
        filename = (safe_title or "在线演示") + ".html"
        # File name is not part of the content identity; all asset URLs remain relative.
        entry.rename(staging / filename)
        metadata["captured_at"] = datetime.now(UTC).isoformat()
        metadata["fingerprint"] = fingerprint
        metadata["requested_url"] = url
        (staging / "archive.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        destination = archive_root / hashlib.sha256(url.encode()).hexdigest() / fingerprint
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            staging.rename(destination)
        else:
            # Reuse the previous snapshot only if it still passes integrity checks.
            existing = list(destination.glob("*.html"))
            if (
                len(existing) != 1
                or open_html_source(settings, existing[0]).fingerprint != fingerprint
            ):
                raise ValueError("已有在线归档文件已变化，请先检查本地归档")
            filename = existing[0].name
        archived_entry = destination / filename
    report = scan_and_import(
        connection, [archived_entry], settings=settings, on_progress=on_progress
    )
    for item in report.imported:
        connection.execute(
            """
            INSERT INTO html_origins(
                deck_id, source_url, resolved_url, captured_at, capture_profile,
                resource_count, download_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(deck_id) DO NOTHING
            """,
            (
                item.deck_id, url, metadata["resolved_url"], metadata["captured_at"],
                metadata["capture_profile"], len(capture.resources), capture.total_bytes,
            ),
        )
    return {
        "ok": not report.failed,
        "discovered": report.discovered,
        "source_url": url,
        "archive_path": str(archived_entry),
        "resource_count": len(capture.resources),
        "download_bytes": capture.total_bytes,
        "archive_complete": not capture.missing,
        "warnings": [
            f"归档有缺失：{urlsplit(url).path.rsplit('/', 1)[-1]} · {error}"
            for url, error in capture.missing.items()
        ],
        "imported": [
            {
                "deck_id": item.deck_id, "version_id": item.version_id,
                "path": str(item.path), "name": item.path.name,
                "slide_count": item.slide_count, "created": item.created, "action": item.action,
            } for item in report.imported
        ],
        "skipped": report.skipped,
        "removed": [str(path) for path in report.removed],
        "failed": [{"path": str(path), "error": error} for path, error in report.failed],
    }
