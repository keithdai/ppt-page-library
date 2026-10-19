"""Selection resolution and checked publication for HTML/ZIP composition."""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lxml import etree  # type: ignore[import-untyped]
from PIL import Image

from pptlib.application.compose import (
    ComposePreflightResult,
    _remap_source_path,
    load_manifest_slide_ids,
)
from pptlib.config import Settings
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.export.html import (
    MAX_OUTPUT_BYTES,
    MAX_PAGES,
    HtmlArtifact,
    HtmlPage,
    build_html_artifact,
    publish_html_artifact,
)
from pptlib.export.ooxml import ExportError, ExportResult, output_fingerprint
from pptlib.html.source import HtmlSource, css_urls, local_reference
from pptlib.infrastructure.db.connection import connect_readonly


@dataclass
class _Prepared:
    artifact: HtmlArtifact
    files: dict[Path, str]
    rows: list[dict[str, Any]]
    output: Path
    manifest: Path
    output_state: str
    manifest_state: str
    destinations: tuple[Path, Path]

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "artifact": self.artifact.fingerprint,
                    "files": sorted((str(path), state) for path, state in self.files.items()),
                    "rows": self.rows,
                    "destinations": [str(path) for path in self.destinations],
                },
                sort_keys=True,
                ensure_ascii=True,
            ).encode()
        ).hexdigest()


def _error(error: Exception) -> AppError:
    if isinstance(error, AppError):
        return error
    if isinstance(error, ExportError):
        code = {
            "OUTPUT_CHANGED": ErrorCode.INVALID_STATE_TRANSITION,
            "SOURCE_CHANGED": ErrorCode.SOURCE_CHANGED,
        }.get(error.code, ErrorCode.REQUEST_INVALID)
        return AppError(code, error.message, details={"export_code": error.code})
    # Parser/filesystem errors may contain signed URLs or source script strings.
    return AppError(
        ErrorCode.REQUEST_INVALID,
        "HTML export could not validate its source, resources or destination",
        details={"error_type": type(error).__name__},
    )


def _rows(settings: Settings, slide_ids: list[str]) -> list[dict[str, Any]]:
    if not slide_ids or len(slide_ids) > MAX_PAGES:
        raise AppError(ErrorCode.REQUEST_INVALID, "Select between 1 and 2000 pages")
    if not settings.database_path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "Local library database does not exist")
    connection = connect_readonly(settings.database_path)
    try:
        result = []
        for slide_id in slide_ids:
            row = connection.execute(
                """
                SELECT s.id AS slide_id, s.slide_number, s.page_key, s.title, s.notes_text,
                       v.id AS version_id, v.sha256, v.source_format, d.canonical_path
                FROM slides s
                JOIN deck_versions v ON v.id = s.deck_version_id
                JOIN decks d ON d.id = v.deck_id
                WHERE s.id = ? AND v.status = 'parsed' AND d.current_version_id = v.id
                """,
                (slide_id,),
            ).fetchone()
            if row is None:
                raise AppError(
                    ErrorCode.NOT_FOUND, "Selected page is missing or its version expired"
                )
            if row["source_format"] not in {"render_deck_html", "pptx"}:
                raise AppError(ErrorCode.REQUEST_INVALID, "Unsupported source format")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", str(row["version_id"])):
                raise AppError(ErrorCode.REQUEST_INVALID, "Invalid source version identifier")
            result.append(dict(row))
        return result
    finally:
        connection.close()


def _open_source(settings: Settings, path: Path) -> HtmlSource:
    return HtmlSource(
        path,
        max_bytes=min(settings.max_file_bytes, MAX_OUTPUT_BYTES),
        max_parts=settings.max_parts_per_package,
        max_package_bytes=min(settings.max_uncompressed_package_bytes, MAX_OUTPUT_BYTES),
    )


def _complete_dependencies(source: HtmlSource) -> None:
    """Close SVG dependencies for older source adapters that only traversed CSS.

    Reads stay within HtmlSource's bounded, non-symlink dependency reader. No
    remote URL is fetched and only references discovered in CSS/SVG are added.
    """
    pending = list(source.resources)
    inspected: set[str] = set()
    total = sum(int(resource["size_bytes"]) for resource in source.resources.values())
    while pending:
        ref = pending.pop()
        if ref in inspected:
            continue
        inspected.add(ref)
        mime = source.resources[ref]["mime"]
        if mime not in {"image/svg+xml", "text/css"}:
            continue
        raw = source.read_resource(ref)
        values: list[str] = []
        if mime == "text/css":
            values = css_urls(raw.decode("utf-8-sig"))
        else:
            tree = etree.fromstring(
                raw,
                parser=etree.XMLParser(
                    resolve_entities=False,
                    no_network=True,
                    load_dtd=False,
                    huge_tree=False,
                    remove_comments=True,
                    remove_pis=True,
                ),
            )
            for node in tree.iter():
                if not isinstance(node.tag, str):
                    continue
                tag = str(node.tag).rsplit("}", 1)[-1].lower()
                for name, value in node.attrib.items():
                    attr = str(name).rsplit("}", 1)[-1].lower()
                    if tag in {"image", "use"} and attr in {"href", "src"}:
                        values.append(value)
                    elif attr == "style" or "url(" in value.lower():
                        values.extend(css_urls(value))
                if tag == "style":
                    values.extend(css_urls(node.text or ""))
        for value in values:
            child = local_reference(value, ref)
            if child is None:
                if urlsplit(value.strip()).scheme in {"http", "https"}:
                    source.external_urls.add(value)
                continue
            if child in source.resources:
                pending.append(child)
                continue
            mime = mimetypes.guess_type(child)[0] or ""
            if not mime.startswith(("image/", "font/", "audio/", "video/")) and mime != "text/css":
                raise ValueError("Unsupported SVG dependency")
            if len(source.resources) >= source.max_parts:
                raise ValueError("Too many SVG dependencies")
            content = source._read_raw_resource(child, source.max_package_bytes - total)
            total += len(content)
            source.resources[child] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "mime": mime,
            }
            pending.append(child)


def _file_state(path: Path) -> str:
    if not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "A source or required preview is unavailable")
    return output_fingerprint(path)


def _same_path(first: Path, second: Path) -> bool:
    return first.resolve() == second.resolve() or (
        first.exists() and second.exists() and first.samefile(second)
    )


def _protect(
    settings: Settings,
    targets: tuple[Path, Path],
    extra: set[Path],
) -> None:
    if not settings.database_path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "Local library database does not exist")
    if _same_path(*targets):
        raise AppError(ErrorCode.REQUEST_INVALID, "Output and manifest must be different files")
    protected = {
        settings.database_path,
        *(Path(str(settings.database_path) + suffix) for suffix in ("-wal", "-shm", "-journal")),
        *extra,
    }
    connection = connect_readonly(settings.database_path)
    try:
        rows = connection.execute(
            """SELECT d.canonical_path, v.source_format, v.dependencies_json
               FROM decks d LEFT JOIN deck_versions v ON v.deck_id = d.id"""
        ).fetchall()
    finally:
        connection.close()
    html_paths: set[Path] = set()
    for row in rows:
        path = _remap_source_path(str(row["canonical_path"]), settings.home).resolve()
        protected.add(path)
        if row["source_format"] != "render_deck_html" or path.suffix.lower() == ".zip":
            continue
        html_paths.add(path)
        dependencies = json.loads(row["dependencies_json"] or "[]")
        for dependency in dependencies:
            reference = dependency.get("reference") if isinstance(dependency, dict) else None
            if isinstance(reference, str):
                local = local_reference(reference)
                if local:
                    protected.add(path.parent / local)
    for path in html_paths:
        # Existing targets may be hardlinks to resources outside this directory.
        if path.is_file() and any(
            target.exists() or target.resolve().is_relative_to(path.parent) for target in targets
        ):
            try:
                source = _open_source(settings, path)
                _complete_dependencies(source)
                protected.update(path.parent / ref for ref in source.resources)
            except (OSError, ValueError, etree.LxmlError):
                # A broken unselected source must not make its resources writable.
                raise AppError(
                    ErrorCode.REQUEST_INVALID,
                    "Cannot safely export beside an unreadable indexed HTML source",
                ) from None
    for target in targets:
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise AppError(ErrorCode.REQUEST_INVALID, "Export destination must be a regular file")
        if target.resolve().is_relative_to(settings.assets_dir.resolve()):
            raise AppError(ErrorCode.REQUEST_INVALID, "Export cannot overwrite library assets")
        if any(_same_path(target, path) for path in protected):
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                "Export cannot overwrite a source, source resource, database or input manifest",
            )


def _source_origin(canonical: dict[str, Any]) -> str | None:
    if not isinstance(canonical, dict):
        raise ValueError("Source metadata must be an object")
    metadata = canonical.get("pptlib", canonical)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("source_url")
    archive = metadata.get("archive", {})
    if not value and isinstance(archive, dict):
        value = archive.get("source_url")
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname
    if host in {"localhost", "127.0.0.1", "::1"}:
        return None
    # Bearer credentials can occur in paths as well as queries. Export only the
    # origin; the exact archived URL remains in the local canonical metadata.
    host = f"[{host}]" if ":" in host else host
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}/"


def _prepare(
    settings: Settings,
    slide_ids: list[str],
    output: Path,
    manifest: Path,
    *,
    verify_source_hash: bool,
    input_manifest: Path | None = None,
) -> _Prepared:
    output = output.expanduser().absolute()
    manifest = manifest.expanduser().absolute()
    if output.suffix.lower() not in {".html", ".zip"}:
        raise AppError(ErrorCode.REQUEST_INVALID, "HTML export requires .html or .zip")
    files: dict[Path, str] = {}
    if input_manifest is not None:
        files[input_manifest] = _file_state(input_manifest)
    _protect(settings, (output, manifest), set(files))
    destinations = (output.resolve(), manifest.resolve())
    output_state, manifest_state = output_fingerprint(output), output_fingerprint(manifest)
    rows = _rows(settings, slide_ids)
    pages: list[HtmlPage] = []
    sources: dict[Path, HtmlSource] = {}
    origins: dict[str, str | None] = {}
    previews: dict[Path, bytes] = {}
    input_bytes = 0
    for row in rows:
        path = _remap_source_path(row["canonical_path"], settings.home).resolve()
        if path not in files:
            files[path] = _file_state(path)
        expected = str(row["sha256"])
        source_format = str(row["source_format"])
        source: HtmlSource | None = None
        preview: bytes | None = None
        fingerprint = files[path].removeprefix("sha256:")
        version = str(row["version_id"])
        if source_format == "render_deck_html":
            if path not in sources:
                if path.stat().st_size + input_bytes > MAX_OUTPUT_BYTES:
                    raise ExportError("OUTPUT_TOO_LARGE", "Selected sources exceed export limit")
                sources[path] = _open_source(settings, path)
                _complete_dependencies(sources[path])
                input_bytes += len(sources[path].html.encode()) + sum(
                    int(resource["size_bytes"]) for resource in sources[path].resources.values()
                )
                if input_bytes > MAX_OUTPUT_BYTES:
                    raise ExportError("OUTPUT_TOO_LARGE", "Selected sources exceed export limit")
            source = sources[path]
            fingerprint = source.fingerprint
            if str(row["page_key"]) not in source.page_keys:
                raise AppError(ErrorCode.SOURCE_CHANGED, "Selected HTML page key is missing")
            if not source.is_zip:
                for ref, resource in source.resources.items():
                    files[path.parent / ref] = "sha256:" + resource["sha256"]
            if version not in origins:
                canonical_path = settings.assets_dir / "html-manifests" / f"{version}.json"
                metadata_paths = [canonical_path]
                if path.is_relative_to(settings.assets_dir / "html-archives"):
                    metadata_paths.append(path.parent / "archive.json")
                origins[version] = None
                for metadata_path in metadata_paths:
                    files[metadata_path] = output_fingerprint(metadata_path)
                    if metadata_path.is_file():
                        if metadata_path.stat().st_size > 8 * 1024 * 1024:
                            raise ValueError("Canonical source metadata exceeds size limit")
                        origin = _source_origin(json.loads(metadata_path.read_text("utf-8")))
                        origins[version] = origins[version] or origin
        else:
            preview_path = (
                settings.assets_dir
                / "previews"
                / (f"{version}_s{int(row['slide_number']):05d}.jpg")
            )
            if preview_path not in previews:
                files[preview_path] = _file_state(preview_path)
                if preview_path.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("PPTX preview exceeds size limit")
                preview = preview_path.read_bytes()
                input_bytes += len(preview)
                if input_bytes > MAX_OUTPUT_BYTES:
                    raise ExportError("OUTPUT_TOO_LARGE", "Selected previews exceed export limit")
                with Image.open(io.BytesIO(preview)) as image:
                    if (
                        image.format != "JPEG"
                        or max(image.size) < min(settings.preview_long_edge, 1920)
                        or max(image.size) > 16384
                    ):
                        raise ExportError(
                            "PREVIEW_MISSING", "A high-resolution JPEG preview is required"
                        )
                    image.verify()
                previews[preview_path] = preview
            preview = previews[preview_path]
        if verify_source_hash and expected != fingerprint:
            raise AppError(ErrorCode.SOURCE_CHANGED, "Source or resource changed since import")
        pages.append(
            HtmlPage(
                slide_id=str(row["slide_id"]),
                version_id=version,
                source_format=source_format,
                source_fingerprint=fingerprint,
                page_number=int(row["slide_number"]),
                page_key=str(row["page_key"]),
                title=str(row["title"]),
                notes=str(row["notes_text"]),
                source=source,
                preview=preview,
                source_url=origins.get(version),
            )
        )
    artifact = build_html_artifact(
        pages,
        offline_zip=output.suffix.lower() == ".zip",
        max_bytes=MAX_OUTPUT_BYTES,
    )
    prepared = _Prepared(
        artifact,
        files,
        rows,
        output,
        manifest,
        output_state,
        manifest_state,
        destinations,
    )
    _recheck(settings, slide_ids, prepared)
    return prepared


def _recheck(settings: Settings, slide_ids: list[str], prepared: _Prepared) -> None:
    if (prepared.output.resolve(), prepared.manifest.resolve()) != prepared.destinations:
        raise ExportError("OUTPUT_CHANGED", "Export destination changed after preflight")
    _protect(settings, (prepared.output, prepared.manifest), set(prepared.files))
    if _rows(settings, slide_ids) != prepared.rows:
        raise ExportError("SOURCE_CHANGED", "Selected library metadata changed after preflight")
    for path, state in prepared.files.items():
        if output_fingerprint(path) != state:
            raise ExportError(
                "SOURCE_CHANGED", "Source, preview or manifest changed after preflight"
            )
    for path, state in (
        (prepared.output, prepared.output_state),
        (prepared.manifest, prepared.manifest_state),
    ):
        if output_fingerprint(path) != state:
            raise ExportError("OUTPUT_CHANGED", "Output changed after preflight")


def preflight_html_from_manifest(
    settings: Settings,
    manifest_path: Path,
    output_path: Path,
    *,
    verify_source_hash: bool = True,
) -> ComposePreflightResult:
    output = output_path.expanduser().absolute()
    manifest = output.with_suffix(".manifest.json")
    slide_ids: list[str] = []
    source_state = output_state = manifest_state = "missing"
    try:
        source_state = output_fingerprint(manifest_path)
        output_state = output_fingerprint(output)
        manifest_state = output_fingerprint(manifest)
        slide_ids = load_manifest_slide_ids(manifest_path)
        prepared = _prepare(
            settings,
            slide_ids,
            output,
            manifest,
            verify_source_hash=verify_source_hash,
            input_manifest=manifest_path,
        )
        if prepared.files[manifest_path] != source_state:
            raise ExportError("SOURCE_CHANGED", "Selection manifest changed during preflight")
        token = hashlib.sha256(
            json.dumps(
                {
                    "prepared": prepared.fingerprint,
                    "selection": source_state,
                    "output": output_state,
                    "manifest": manifest_state,
                    "verify_source_hash": verify_source_hash,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return ComposePreflightResult(
            ok=True,
            page_count=len(slide_ids),
            source_count=len({row["version_id"] for row in prepared.rows}),
            estimated_output_bytes=prepared.artifact.size_bytes,
            estimated_fidelity=prepared.artifact.fidelity_level,
            warnings=prepared.artifact.warnings,
            blockers=(),
            output_path=output,
            will_replace_output=output.exists(),
            preflight_token=token,
            manifest_fingerprint=source_state,
            output_fingerprint=output_state,
            result_manifest_fingerprint=manifest_state,
        )
    except (AppError, ExportError, OSError, ValueError, TypeError, etree.LxmlError) as error:
        failure = _error(error)
        return ComposePreflightResult(
            ok=False,
            page_count=len(slide_ids),
            source_count=0,
            estimated_output_bytes=0,
            estimated_fidelity="unavailable",
            warnings=(),
            blockers=(
                {"code": failure.code.value, "message": str(failure), "details": failure.details},
            ),
            output_path=output,
            will_replace_output=output.exists(),
            preflight_token=None,
            manifest_fingerprint=source_state,
            output_fingerprint=output_state,
            result_manifest_fingerprint=manifest_state,
        )


def compose_html_from_slide_ids(
    settings: Settings,
    slide_ids: list[str],
    output_path: Path,
    *,
    manifest_path: Path | None = None,
    verify_source_hash: bool = True,
    expected_output_fingerprint: str | None = None,
    expected_manifest_fingerprint: str | None = None,
    input_manifest: Path | None = None,
    preflight_token: str | None = None,
) -> ExportResult:
    try:
        # Snapshot before reading the selection, then bind it to the resolved IDs.
        selection_state = output_fingerprint(input_manifest) if input_manifest else None
        if input_manifest is not None and load_manifest_slide_ids(input_manifest) != slide_ids:
            raise ExportError("SOURCE_CHANGED", "Selection manifest changed before export")
        prepared = _prepare(
            settings,
            slide_ids,
            output_path,
            manifest_path or output_path.with_suffix(".manifest.json"),
            verify_source_hash=verify_source_hash,
            input_manifest=input_manifest,
        )
        if input_manifest and prepared.files[input_manifest] != selection_state:
            raise ExportError("SOURCE_CHANGED", "Selection manifest changed during export")
        if preflight_token is not None:
            token = hashlib.sha256(
                json.dumps(
                    {
                        "prepared": prepared.fingerprint,
                        "selection": selection_state,
                        "output": prepared.output_state,
                        "manifest": prepared.manifest_state,
                        "verify_source_hash": verify_source_hash,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            if token != preflight_token:
                raise AppError(
                    ErrorCode.INVALID_STATE_TRANSITION, "Export conditions changed; preflight again"
                )
        if (
            expected_output_fingerprint is not None
            and expected_output_fingerprint != prepared.output_state
        ) or (
            expected_manifest_fingerprint is not None
            and expected_manifest_fingerprint != prepared.manifest_state
        ):
            raise ExportError("OUTPUT_CHANGED", "Output changed after preflight")
        return publish_html_artifact(
            prepared.artifact,
            prepared.output,
            prepared.manifest,
            recheck=lambda: _recheck(settings, slide_ids, prepared),
            expected_output=prepared.output_state,
            expected_manifest=prepared.manifest_state,
        )
    except (AppError, ExportError, OSError, ValueError, TypeError, etree.LxmlError) as error:
        raise _error(error) from None
