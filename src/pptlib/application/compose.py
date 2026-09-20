from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pptlib.config import Settings
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.export.ooxml import ExportError, ExportResult, SlideRef, export_slides
from pptlib.infrastructure.db.connection import connect


@dataclass(frozen=True, slots=True)
class ComposePlanItem:
    slide_id: str
    version_id: str
    source_path: Path
    page_number: int
    sha256: str | None


def _remap_source_path(canonical: str, home: Path) -> Path:
    """Resolve a stored source path onto the local filesystem.

    Historically the DB was populated inside a container where the data
    directory was mounted at ``/data``. Locally that maps to ``PPTLIB_HOME``.
    We accept the stored path verbatim when it exists, otherwise remap a
    leading ``/data/`` segment onto the current home before giving up.
    """
    direct = Path(canonical)
    if direct.exists():
        return direct
    if canonical.startswith("/data/"):
        remapped = home / canonical[len("/data/") :]
        if remapped.exists():
            return remapped
    return direct


def _lookup_slide(
    connection: sqlite3.Connection, slide_id: str, home: Path
) -> ComposePlanItem:
    row = connection.execute(
        """
        SELECT s.id AS slide_id, s.slide_number, v.id AS version_id, v.sha256,
               d.canonical_path
        FROM slides s
        JOIN deck_versions v ON v.id = s.deck_version_id
        JOIN decks d ON d.id = v.deck_id
        WHERE s.id = ? AND v.status = 'parsed' AND d.current_version_id = v.id
        """,
        (slide_id,),
    ).fetchone()
    if row is None:
        raise AppError(
            ErrorCode.NOT_FOUND,
            "选片单引用的页面不存在或来源版本已过期",
            details={"slide_id": slide_id},
        )
    source = _remap_source_path(str(row["canonical_path"]), home)
    return ComposePlanItem(
        slide_id=str(row["slide_id"]),
        version_id=str(row["version_id"]),
        source_path=source,
        page_number=int(row["slide_number"]),
        sha256=str(row["sha256"]) if row["sha256"] else None,
    )


def build_compose_plan(settings: Settings, slide_ids: list[str]) -> list[ComposePlanItem]:
    """Resolve an ordered list of slide ids into concrete source references."""
    if not slide_ids:
        raise AppError(ErrorCode.REQUEST_INVALID, "manifest 未包含任何页面")
    if not settings.database_path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "本地索引数据库不存在，请先导入 PPTX")
    connection = connect(settings.database_path)
    try:
        return [_lookup_slide(connection, slide_id, settings.home) for slide_id in slide_ids]
    finally:
        connection.close()


def load_manifest_slide_ids(manifest_path: Path) -> list[str]:
    """Read an ordered slide-id list from a Miaoda selection manifest.

    Accepted shapes (all resolve to an ordered list of slide ids):
    - ``["slide_id", ...]``
    - ``{"slide_ids": ["slide_id", ...]}`` or ``{"slideIds": [...]}`` (the web
      app emits camelCase; both are accepted)
    - ``{"items": [{"slide_id": "..."}, ...]}`` (sorted by ``order`` when present)
    """
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "无法读取或解析 manifest.json",
            details={"path": str(manifest_path), "error": str(error)},
        ) from error

    def _id_list(value: object) -> list[object] | None:
        return value if isinstance(value, list) else None

    if isinstance(raw, list):
        candidates: list[object] = raw
    elif isinstance(raw, dict) and _id_list(raw.get("slide_ids")) is not None:
        candidates = raw["slide_ids"]
    elif isinstance(raw, dict) and _id_list(raw.get("slideIds")) is not None:
        candidates = raw["slideIds"]
    elif isinstance(raw, dict) and isinstance(raw.get("items"), list):
        items = raw["items"]
        if all(isinstance(entry, dict) for entry in items):
            items = sorted(items, key=lambda entry: entry.get("order", 0))
            candidates = [entry.get("slide_id", entry.get("slideId")) for entry in items]
        else:
            candidates = items
    else:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "manifest.json 结构无法识别"
            "（应为 slide_id 列表，或含 slide_ids/slideIds/items 的对象）",
            details={"path": str(manifest_path)},
        )

    slide_ids = [str(value) for value in candidates if isinstance(value, str) and value]
    if not slide_ids:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "manifest.json 未包含有效的 slide_id",
            details={"path": str(manifest_path)},
        )
    return slide_ids


def compose_from_slide_ids(
    settings: Settings,
    slide_ids: list[str],
    output_path: Path,
    *,
    manifest_path: Path | None = None,
    verify_source_hash: bool = True,
) -> ExportResult:
    """Compose selected pages into a new PPTX using the OOXML native exporter."""
    plan = build_compose_plan(settings, slide_ids)
    missing = [item.slide_id for item in plan if not item.source_path.exists()]
    if missing:
        raise AppError(
            ErrorCode.NOT_FOUND,
            "部分页面的源 PPTX 在本地不存在，无法组合",
            details={
                "missing_slide_ids": missing,
                "hint": "源文件应保留在本地（var/dev/uploaded_sources 或原始导入目录）",
            },
        )
    refs = [
        SlideRef(
            file_version_id=item.version_id,
            source_path=item.source_path,
            page_number=item.page_number,
            expected_sha256=item.sha256 if verify_source_hash else None,
        )
        for item in plan
    ]
    try:
        return export_slides(refs, output_path, manifest_path)
    except ExportError as error:
        raise AppError(
            _export_error_code(error.code), error.message, details=error.to_dict()
        ) from error


# Map the exporter's structured codes onto app-level error codes so callers
# (CLI/desktop/web) can distinguish user-actionable problems (bad selection,
# changed source) from genuine internal failures.
_REQUEST_ERROR_CODES = frozenset(
    {
        "EMPTY_SELECTION",
        "INCOMPATIBLE_SLIDE_SIZE",
        "SLIDE_NOT_FOUND",
        "INVALID_SOURCE_PACKAGE",
    }
)


def _export_error_code(export_code: str) -> ErrorCode:
    if export_code == ErrorCode.SOURCE_CHANGED.value:
        return ErrorCode.SOURCE_CHANGED
    if export_code in _REQUEST_ERROR_CODES:
        return ErrorCode.REQUEST_INVALID
    return ErrorCode.INTERNAL_ERROR


def compose_from_manifest(
    settings: Settings,
    manifest_path: Path,
    output_path: Path,
    *,
    verify_source_hash: bool = True,
) -> ExportResult:
    slide_ids = load_manifest_slide_ids(manifest_path)
    return compose_from_slide_ids(
        settings,
        slide_ids,
        output_path,
        manifest_path=output_path.with_suffix(".manifest.json"),
        verify_source_hash=verify_source_hash,
    )
