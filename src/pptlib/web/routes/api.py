from __future__ import annotations

import hashlib
from pathlib import Path
from shutil import rmtree
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pptlib import __version__
from pptlib.application.doctor import run_doctor
from pptlib.application.import_decks import ImportReport, scan_and_import
from pptlib.application.library import LibraryFilters
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.export.ooxml import ExportError, export_slides
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.library import selection_slide_refs

router = APIRouter(prefix="/api/v1")


class SelectionItemRequest(BaseModel):
    slide_id: str = Field(min_length=1, max_length=200)


class SelectionReorderRequest(BaseModel):
    slide_ids: list[str] = Field(max_length=500)


class ImportRequest(BaseModel):
    root: str = Field(min_length=1, max_length=2000)


class ExportRequest(BaseModel):
    filename: str | None = Field(default=None, max_length=120)


def _success(request: Request, data: object) -> dict[str, object]:
    return {
        "ok": True,
        "data": data,
        "request_id": request.state.request_id,
    }


def _import_payload(report: ImportReport) -> dict[str, object]:
    # Keep the response shape identical for directory scans and file uploads.
    return {
        "discovered": report.discovered,
        "imported": [
            {
                "deck_id": item.deck_id,
                "version_id": item.version_id,
                "path": str(item.path),
                "slide_count": item.slide_count,
                "created": item.created,
            }
            for item in report.imported
        ],
        "skipped": report.skipped,
        "failed": [{"path": str(path), "error": error} for path, error in report.failed],
    }


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    return _success(request, {"status": "ok", "version": __version__})


@router.get("/doctor")
def doctor(request: Request) -> dict[str, object]:
    report = run_doctor(request.app.state.settings)
    return _success(request, report.to_dict())


@router.post("/imports")
def import_decks(request: Request, payload: ImportRequest) -> dict[str, object]:
    root = Path(payload.root).expanduser().resolve()
    if not root.is_dir():
        raise AppError(
            ErrorCode.PATH_NOT_ALLOWED,
            "来源目录不存在或不可读取",
            details={"root": str(root)},
        )
    settings = request.app.state.settings
    connection = connect(settings.database_path)
    try:
        report = scan_and_import(connection, [root], settings=settings)
    finally:
        connection.close()
    return _success(request, _import_payload(report))


@router.post("/imports/files")
async def import_uploaded_files(
    request: Request,
    files: Annotated[list[UploadFile], File(description="一个或多个 PPTX 文件")],
) -> dict[str, object]:
    """Persist browser-selected PPTX files, then run the normal importer."""
    settings = request.app.state.settings
    upload_root = settings.home / "uploaded_sources"
    staging_root = upload_root / f".staging-{uuid4().hex}"
    staging_root.mkdir(parents=True, exist_ok=False)
    seen_names: set[str] = set()
    staged: list[tuple[Path, str, str]] = []
    try:
        for upload in files:
            filename = Path(upload.filename or "").name
            if (
                not filename
                or filename != (upload.filename or "")
                or Path(filename).suffix.casefold() != ".pptx"
            ):
                raise AppError(ErrorCode.REQUEST_INVALID, "只能选择 .pptx 文件")
            if filename in seen_names:
                raise AppError(ErrorCode.REQUEST_INVALID, f"文件名重复：{filename}")
            seen_names.add(filename)
            target = staging_root / filename
            size = 0
            digest = hashlib.sha256()
            with target.open("wb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_file_bytes:
                        raise AppError(ErrorCode.REQUEST_INVALID, f"文件超过大小限制：{filename}")
                    digest.update(chunk)
                    handle.write(chunk)
            await upload.close()
            staged.append((target, digest.hexdigest(), filename))
    except AppError:
        rmtree(staging_root, ignore_errors=True)
        raise

    roots: set[Path] = set()
    for target, content_hash, filename in staged:
        stable_root = upload_root / content_hash
        stable_root.mkdir(parents=True, exist_ok=True)
        stable_target = stable_root / filename
        if stable_target.exists():
            target.unlink()
        else:
            target.replace(stable_target)
        roots.add(stable_root)
    rmtree(staging_root, ignore_errors=True)

    connection = connect(settings.database_path)
    try:
        report = scan_and_import(connection, sorted(roots), settings=settings)
    finally:
        connection.close()
    return _success(request, _import_payload(report))


@router.post("/exports")
def export_selection(request: Request, payload: ExportRequest | None = None) -> dict[str, object]:
    settings = request.app.state.settings
    refs = selection_slide_refs(settings.database_path)
    if not refs:
        raise AppError(ErrorCode.REQUEST_INVALID, "选片单为空，无法导出")
    requested = payload.filename if payload is not None else None
    filename = requested or f"selection-{uuid4().hex[:12]}.pptx"
    if Path(filename).name != filename or not filename.lower().endswith(".pptx"):
        raise AppError(ErrorCode.REQUEST_INVALID, "导出文件名必须是当前目录下的 .pptx 文件")
    output_path = settings.output_root / filename
    if output_path.exists():
        raise AppError(ErrorCode.REQUEST_INVALID, "导出文件已存在，不会覆盖已有文件")
    try:
        result = export_slides(list(refs), output_path)
    except ExportError as error:
        if error.code == ErrorCode.SOURCE_CHANGED.value:
            raise AppError(
                ErrorCode.SOURCE_CHANGED, error.message, details=error.details
            ) from error
        raise AppError(ErrorCode.INTERNAL_ERROR, error.message, details=error.to_dict()) from error
    return _success(
        request,
        {
            "export_id": result.export_id,
            "status": "published",
            "output_path": str(result.output_path),
            "manifest_path": str(result.manifest_path),
            "download_url": f"/api/v1/exports/{filename}",
            "page_count": result.page_count,
            "warnings": list(result.warnings),
            "fidelity_level": result.fidelity_level,
        },
    )


@router.get("/exports/{filename}")
def download_export(request: Request, filename: str) -> FileResponse:
    """Download a published export without exposing the output directory."""
    if Path(filename).name != filename or not filename.lower().endswith(".pptx"):
        raise AppError(ErrorCode.REQUEST_INVALID, "导出文件名必须是当前目录下的 .pptx 文件")
    output_path = request.app.state.settings.output_root / filename
    if not output_path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "导出文件不存在", details={"filename": filename})
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=filename,
    )


@router.get("/slides")
def slides(
    request: Request,
    q: str = Query(default="", max_length=500),
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=24, ge=1, le=100),
    topic: str = Query(default="", max_length=100),
    subtopic: str = Query(default="", max_length=100),
    page_type: str = Query(default="", max_length=100),
    deck_id: str = Query(default="", max_length=200),
) -> dict[str, object]:
    result = request.app.state.library.search(
        q,
        page,
        page_size,
        LibraryFilters(topic=topic, subtopic=subtopic, page_type=page_type, deck_id=deck_id),
    )
    return _success(request, result.to_dict())


@router.get("/library/facets")
def library_facets(request: Request) -> dict[str, object]:
    return _success(request, request.app.state.library.facets().to_dict())


@router.get("/slides/{slide_id}")
def slide_detail(request: Request, slide_id: str) -> dict[str, object]:
    return _success(request, request.app.state.library.get(slide_id).to_dict())


@router.get("/selection")
def selection(request: Request) -> dict[str, object]:
    return _success(request, request.app.state.library.selection_dict())


@router.post("/selection/items")
def add_selection_item(
    request: Request, payload: SelectionItemRequest
) -> dict[str, object]:
    items = request.app.state.library.add(payload.slide_id)
    return _success(request, {"items": [item.to_dict() for item in items], "count": len(items)})


@router.delete("/selection/items/{slide_id}")
def remove_selection_item(request: Request, slide_id: str) -> dict[str, object]:
    items = request.app.state.library.remove(slide_id)
    return _success(request, {"items": [item.to_dict() for item in items], "count": len(items)})


@router.post("/selection/reorder")
def reorder_selection(
    request: Request, payload: SelectionReorderRequest
) -> dict[str, object]:
    items = request.app.state.library.reorder(payload.slide_ids)
    return _success(request, {"items": [item.to_dict() for item in items], "count": len(items)})
