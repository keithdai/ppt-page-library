from __future__ import annotations

import json
from pathlib import Path

from pptlib import __version__
from pptlib.application.compose import (
    compose_from_manifest,
    preflight_compose_from_manifest,
)
from pptlib.application.library import LibraryFilters, validate_filters
from pptlib.config import Settings
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.infrastructure.db.connection import connect_readonly
from pptlib.infrastructure.db.repositories import (
    SearchResult,
    count_search_slides,
    get_current_slide,
    library_revision,
    search_slides,
)

AGENT_PROTOCOL = "deckatlas.agent/1.0"
SELECTION_SCHEMA = "deckatlas.selection/1.0"
MAX_QUERY_LENGTH = 500
MAX_PAGE = 1_000_000
MAX_PAGE_SIZE = 100
MAX_SELECTION_BYTES = 1024 * 1024
MAX_SELECTION_SIZE = 2_000


def describe_capabilities(settings: Settings) -> dict[str, object]:
    return {
        "product": "DeckAtlas",
        "version": __version__,
        "protocol": AGENT_PROTOCOL,
        "library_available": settings.database_path.is_file(),
        "export_formats": ["pptx", "html", "zip"] if settings.html_enabled else ["pptx"],
        "permissions": {
            "default": "read_only",
            "write_operations": ["compose.run"],
            "output_root": str(settings.output_root),
        },
        "commands": {
            "capabilities": {"mutates": False},
            "search": {
                "mutates": False,
                "filters": ["topic", "subtopic", "page_type", "deck_id", "source_format"],
                "max_page": MAX_PAGE,
                "max_page_size": MAX_PAGE_SIZE,
            },
            "slide.get": {"mutates": False},
            "compose.preflight": {
                "mutates": False,
                "selection_schema": SELECTION_SCHEMA,
            },
            "compose.run": {
                "mutates": True,
                "requires": ["preflight_token", "allow_write"],
                "selection_schema": SELECTION_SCHEMA,
            },
        },
        "selection_schema": {
            "type": "object",
            "required": ["schema_version", "slide_ids"],
            "properties": {
                "schema_version": {"const": SELECTION_SCHEMA},
                "slide_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_SELECTION_SIZE,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
            },
        },
    }


def search_library(
    settings: Settings,
    *,
    query: str,
    page: int,
    page_size: int,
    filters: LibraryFilters,
    expected_revision: str | None = None,
) -> dict[str, object]:
    _require_library(settings)
    normalized_query = query.strip()
    if len(normalized_query) > MAX_QUERY_LENGTH:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            f"query must be at most {MAX_QUERY_LENGTH} characters",
        )
    if page < 1 or page > MAX_PAGE:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            f"page must be between 1 and {MAX_PAGE}",
        )
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            f"page_size must be between 1 and {MAX_PAGE_SIZE}",
        )
    validate_filters(filters)

    with connect_readonly(settings.database_path) as connection:
        connection.execute("BEGIN")
        revision = library_revision(connection)
        if expected_revision and expected_revision != revision:
            raise AppError(
                ErrorCode.INVALID_STATE_TRANSITION,
                "library changed since the previous request",
                retryable=True,
                details={"expected_revision": expected_revision, "revision": revision},
            )
        offset = (page - 1) * page_size
        matches = search_slides(
            connection,
            normalized_query,
            limit=page_size,
            offset=offset,
            filters=filters,
        )
        total = count_search_slides(connection, normalized_query, filters=filters)

    return {
        "items": [_search_item(settings, item) for item in matches],
        "total": total,
        "page": page,
        "page_size": page_size,
        "next_page": page + 1 if offset + len(matches) < total else None,
        "query": normalized_query,
        "filters": filters.to_dict(),
        "library_revision": revision,
    }


def get_slide(settings: Settings, slide_id: str) -> dict[str, object]:
    _require_library(settings)
    normalized_id = _validate_slide_id(slide_id)
    with connect_readonly(settings.database_path) as connection:
        connection.execute("BEGIN")
        slide = get_current_slide(connection, normalized_id)
        revision = library_revision(connection)
    if slide is None:
        raise AppError(
            ErrorCode.NOT_FOUND,
            "slide not found in the current library",
            details={"slide_id": normalized_id},
        )
    result = _search_item(settings, slide, include_full_text=True)
    result["library_revision"] = revision
    return result


def preflight_agent_compose(
    settings: Settings,
    manifest_path: Path,
    output_path: Path,
) -> dict[str, object]:
    manifest = validate_selection_manifest(manifest_path)
    output = validate_output_path(settings, output_path)
    result = preflight_compose_from_manifest(settings, manifest, output)
    payload = result.to_dict()
    payload["ready"] = bool(payload.pop("ok"))
    return payload


def run_agent_compose(
    settings: Settings,
    manifest_path: Path,
    output_path: Path,
    *,
    preflight_token: str,
    allow_write: bool,
    overwrite: bool,
) -> dict[str, object]:
    if not allow_write:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "compose.run requires explicit --allow-write",
        )
    manifest = validate_selection_manifest(manifest_path)
    output = validate_output_path(settings, output_path)
    if output.exists() and not overwrite:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "output already exists; pass --overwrite after reviewing preflight",
            details={"output_path": str(output)},
        )
    result = compose_from_manifest(
        settings,
        manifest,
        output,
        preflight_token=preflight_token,
    )
    return {
        "export_id": result.export_id,
        "output_path": str(result.output_path),
        "manifest_path": str(result.manifest_path),
        "page_count": result.page_count,
        "fidelity_level": result.fidelity_level,
        "warnings": list(result.warnings),
    }


def validate_selection_manifest(manifest_path: Path) -> Path:
    manifest = manifest_path.expanduser().resolve()
    if not manifest.is_file():
        raise AppError(
            ErrorCode.NOT_FOUND,
            "selection manifest not found",
            details={"manifest_path": str(manifest)},
        )
    try:
        with manifest.open("rb") as handle:
            raw = handle.read(MAX_SELECTION_BYTES + 1)
    except OSError as error:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "selection manifest is not valid UTF-8 JSON",
            details={"manifest_path": str(manifest)},
        ) from error
    if len(raw) > MAX_SELECTION_BYTES:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "selection manifest exceeds the 1 MiB limit",
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "selection manifest is not valid UTF-8 JSON",
            details={"manifest_path": str(manifest)},
        ) from error
    if not isinstance(payload, dict):
        raise AppError(ErrorCode.REQUEST_INVALID, "selection manifest must be a JSON object")
    if payload.get("schema_version") != SELECTION_SCHEMA:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            f"selection manifest schema_version must be {SELECTION_SCHEMA}",
        )
    slide_ids = payload.get("slide_ids")
    if not isinstance(slide_ids, list) or not 1 <= len(slide_ids) <= MAX_SELECTION_SIZE:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            f"slide_ids must contain between 1 and {MAX_SELECTION_SIZE} items",
        )
    if any(
        not isinstance(slide_id, str)
        or not slide_id.strip()
        or len(slide_id) > 128
        for slide_id in slide_ids
    ):
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "each slide_id must be a non-empty string of at most 128 characters",
        )
    return manifest


def validate_output_path(settings: Settings, output_path: Path) -> Path:
    output = output_path.expanduser().resolve()
    output_root = settings.output_root.expanduser().resolve()
    extensions = {".pptx", ".html", ".zip"} if settings.html_enabled else {".pptx"}
    if output.suffix.lower() not in extensions:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            "output extension must be one of: " + ", ".join(sorted(extensions)),
        )
    related_paths = (
        output,
        output.with_suffix(".manifest.json").resolve(),
    )
    for path in related_paths:
        if path == output_root or output_root not in path.parents:
            raise AppError(
                ErrorCode.PATH_NOT_ALLOWED,
                "output and its companion files must stay inside PPTLIB_OUTPUT_ROOT",
                details={"output_root": str(output_root)},
            )
        if path.exists() and not path.is_file():
            raise AppError(
                ErrorCode.PATH_NOT_ALLOWED,
                "output and its companion paths must be regular files",
                details={"path": str(path)},
            )
    return output


def _require_library(settings: Settings) -> None:
    if not settings.database_path.is_file():
        raise AppError(
            ErrorCode.NOT_FOUND,
            "DeckAtlas library not found; open DeckAtlas and import slides first",
        )


def _validate_slide_id(slide_id: str) -> str:
    normalized = slide_id.strip()
    if not normalized or len(normalized) > 128:
        raise AppError(ErrorCode.REQUEST_INVALID, "invalid slide_id")
    return normalized


def _search_item(
    settings: Settings,
    slide: SearchResult,
    *,
    include_full_text: bool = False,
) -> dict[str, object]:
    text = "\n".join(value for value in (slide.body_text, slide.notes_text) if value)
    if not include_full_text and len(text) > 1_000:
        text = text[:997] + "..."
    return {
        "slide_id": slide.slide_id,
        "deck_id": slide.deck_id,
        "deck_name": slide.path.stem,
        "slide_number": slide.slide_number,
        "title": slide.title,
        "text": text,
        "topic": slide.topic,
        "subtopic": slide.subtopic,
        "page_type": slide.page_type,
        "source_format": slide.source_format,
        "page_kind": slide.page_kind,
        "can_compose": slide.source_format == "pptx" or (
            settings.html_enabled and slide.source_format == "render_deck_html"
        ),
        "export_formats": (
            (["pptx"] if slide.source_format == "pptx" else [])
            + (["html", "zip"] if settings.html_enabled else [])
        ),
        "thumbnail_path": _asset_path(settings, "thumbnails", slide.slide_id),
        "preview_path": _preview_path(settings, slide.slide_id),
        "capabilities": dict(slide.capabilities),
        "warnings": list(slide.warnings),
    }


def _preview_path(settings: Settings, slide_id: str) -> str | None:
    return _asset_path(settings, "previews", slide_id) or _asset_path(
        settings, "thumbnails", slide_id
    )


def _asset_path(settings: Settings, kind: str, slide_id: str) -> str | None:
    assets_root = settings.assets_dir.resolve()
    candidate = (assets_root / kind / f"{slide_id}.jpg").resolve()
    if assets_root not in candidate.parents or not candidate.is_file():
        return None
    return str(candidate)
