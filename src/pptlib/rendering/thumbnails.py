from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import posixpath
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image
from playwright.sync_api import Browser, Page, sync_playwright


class ThumbnailError(RuntimeError):
    """Raised when a slide deck cannot be rendered to image assets."""


_LO_APP_BINARY = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
_RENDER_CACHE_VERSION = 5
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICECLI_CHUNK_SIZE = 4
_LIBREOFFICE_PAGE_CHUNK_SIZE = 16
_FONT_EXTENSIONS = {".dfont", ".otf", ".ttc", ".ttf"}
_CHROME_ARGS = [
    "--password-store=basic",
    "--use-mock-keychain",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--disable-extensions",
    "--disable-sync",
    "--no-first-run",
]


def _render_marker_path(assets_dir: Path, version_id: str) -> Path:
    return assets_dir / "render-meta" / f"{version_id}.json"


def _render_cache_is_current(assets_dir: Path, version_id: str) -> bool:
    payload = _read_render_marker(assets_dir, version_id)
    return bool(payload.get("cache_version") == _RENDER_CACHE_VERSION)


def _read_render_marker(assets_dir: Path, version_id: str) -> dict[str, object]:
    try:
        payload = json.loads(_render_marker_path(assets_dir, version_id).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_render_marker(
    assets_dir: Path,
    version_id: str,
    backend: str,
    *,
    complete: bool = True,
) -> None:
    marker = _render_marker_path(assets_dir, version_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    staging = marker.with_name(f".{marker.name}.{uuid4().hex}.tmp")
    staging.write_text(
        json.dumps(
            {
                "cache_version": _RENDER_CACHE_VERSION,
                "backend": backend,
                "complete": complete,
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    staging.replace(marker)


def _staging_targets(staging_root: Path, targets: list[Path]) -> list[Path]:
    staged: list[Path] = []
    for target in targets:
        path = staging_root / target.parent.name / target.name
        path.parent.mkdir(parents=True, exist_ok=True)
        staged.append(path)
    return staged


def _commit_rendered_assets(
    staged_targets: list[Path],
    final_targets: list[Path],
    staging_root: Path,
) -> None:
    missing = [path for path in staged_targets if not path.is_file()]
    if missing:
        raise ThumbnailError(f"renderer produced {len(missing)} incomplete image assets")

    backup_dir = staging_root / "previous"
    replaced: list[tuple[Path, Path | None]] = []
    try:
        for staged, final in zip(staged_targets, final_targets, strict=True):
            backup = None
            if final.exists():
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / final.parent.name / final.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                final.replace(backup)
            replaced.append((final, backup))
            staged.replace(final)
    except OSError as error:
        for final, backup in reversed(replaced):
            with contextlib.suppress(OSError):
                final.unlink()
            if backup is not None:
                with contextlib.suppress(OSError):
                    backup.replace(final)
        raise ThumbnailError(f"could not replace rendered image cache: {error}") from error


def render_cache_is_complete(
    assets_dir: Path,
    version_id: str,
    slide_count: int,
    *,
    require_previews: bool = True,
) -> bool:
    marker = _read_render_marker(assets_dir, version_id)
    if (
        slide_count < 1
        or marker.get("cache_version") != _RENDER_CACHE_VERSION
        or marker.get("complete") is not True
    ):
        return slide_count == 0
    kinds = ("thumbnails", "previews") if require_previews else ("thumbnails",)
    return all(
        (assets_dir / kind / f"{version_id}_s{index:05d}.jpg").is_file()
        for kind in kinds
        for index in range(1, slide_count + 1)
    )


def render_deck_thumbnails(
    source_path: Path,
    version_id: str,
    slide_count: int,
    *,
    assets_dir: Path,
    temp_dir: Path,
    renderer: str = "auto",
    thumbnail_long_edge: int = 640,
    preview_long_edge: int = 1920,
    generate_previews: bool = True,
    force: bool = False,
    executable_finder: Callable[[str], str | None] = shutil.which,
    on_page: Callable[[int, int], None] | None = None,
) -> int:
    """Render a deck to cached JPEG thumbnails and preview images.

    Two backends are supported:

    - ``libreoffice`` exports bounded page ranges to PDF before rasterization,
      preserving PowerPoint colors, gradients, layout, and embedded images.
    - ``officecli`` renders per page through an isolated headless browser and
      remains available as a fallback when LibreOffice cannot render a deck.

    ``renderer="auto"`` prefers LibreOffice for fidelity and falls back to the
    isolated officecli browser path when LibreOffice is unavailable or fails.
    When ``generate_previews`` is False only thumbnails are produced (used by
    startup backfill to avoid re-rendering large existing files).

    ``on_page(done, total)`` is called after each thumbnail page is rendered so
    callers can surface fine-grained progress ("到第 3/20 页"). Officecli emits
    progress page by page; LibreOffice emits it after each bounded range has
    been rasterized.
    """
    if slide_count < 1:
        return 0
    thumb_dir = assets_dir / "thumbnails"
    preview_dir = assets_dir / "previews"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    expected_thumbs = [
        thumb_dir / f"{version_id}_s{index:05d}.jpg" for index in range(1, slide_count + 1)
    ]
    expected_previews = [
        preview_dir / f"{version_id}_s{index:05d}.jpg" for index in range(1, slide_count + 1)
    ]
    thumbs_exist = all(path.is_file() for path in expected_thumbs)
    previews_exist = all(path.is_file() for path in expected_previews)
    want_previews = generate_previews
    refresh_existing = force or not _render_cache_is_current(assets_dir, version_id)
    if not refresh_existing and thumbs_exist and (previews_exist or not want_previews):
        return slide_count

    if not source_path.exists():
        raise ThumbnailError(f"source file not found: {source_path}")
    _reject_external_relationships(source_path)

    officecli_path = executable_finder("officecli")
    officecli = str(Path(officecli_path).resolve()) if officecli_path else None
    order = _backend_order(renderer, officecli)
    thumb_targets = expected_thumbs if refresh_existing or not thumbs_exist else []
    preview_targets = (
        expected_previews if want_previews and (refresh_existing or not previews_exist) else []
    )
    staging_root = assets_dir / ".render-staging" / f"{version_id}-{uuid4().hex}"
    staged_thumbs = _staging_targets(staging_root, thumb_targets)
    staged_previews = _staging_targets(staging_root, preview_targets)
    last_error: ThumbnailError | None = None
    try:
        for backend_index, backend in enumerate(order):
            try:
                if backend == "officecli":
                    _render_with_officecli(
                        officecli or "officecli",
                        source_path,
                        slide_count,
                        temp_dir=temp_dir,
                        expected_thumbs=staged_thumbs,
                        expected_previews=staged_previews,
                        thumbnail_long_edge=thumbnail_long_edge,
                        preview_long_edge=preview_long_edge,
                        on_page=on_page,
                    )
                else:
                    _render_with_libreoffice(
                        source_path,
                        slide_count,
                        temp_dir=temp_dir,
                        expected_thumbs=staged_thumbs,
                        expected_previews=staged_previews,
                        thumbnail_long_edge=thumbnail_long_edge,
                        preview_long_edge=preview_long_edge,
                        executable_finder=executable_finder,
                        on_page=on_page,
                    )
                _commit_rendered_assets(
                    staged_thumbs + staged_previews,
                    thumb_targets + preview_targets,
                    staging_root,
                )
                _write_render_marker(assets_dir, version_id, backend)
                return slide_count
            except ThumbnailError as error:
                last_error = error
                if backend_index + 1 < len(order):
                    for target in staged_thumbs + staged_previews:
                        with contextlib.suppress(OSError):
                            target.unlink()
                continue
        raise last_error or ThumbnailError("no renderer produced images")
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _backend_order(
    renderer: str,
    officecli: str | None,
) -> list[str]:
    if renderer == "officecli":
        return ["officecli"]
    if renderer == "libreoffice":
        return ["libreoffice"]
    return ["libreoffice"] + (["officecli"] if officecli else [])


def _reject_external_relationships(source_path: Path) -> None:
    try:
        with ZipFile(source_path) as package:
            for part in package.namelist():
                if not part.endswith(".rels"):
                    continue
                relationships = ET.fromstring(package.read(part))
                for relationship in relationships.findall(f"{{{_REL_NS}}}Relationship"):
                    if relationship.get("TargetMode", "").casefold() != "external":
                        continue
                    relation_type = relationship.get("Type", "")
                    if relation_type.endswith("/hyperlink"):
                        continue
                    if (
                        relation_type.endswith("/video")
                        and relationship.get("Target", "").strip().casefold() == "null"
                    ):
                        continue
                    raise ThumbnailError(
                        "external linked media is not rendered; embed the asset and retry"
                    )
    except ThumbnailError:
        raise
    except (OSError, ValueError, ET.ParseError, BadZipFile) as error:
        raise ThumbnailError(f"invalid PPTX relationships: {error}") from error


def _render_with_officecli(
    officecli: str,
    source_path: Path,
    slide_count: int,
    *,
    temp_dir: Path,
    expected_thumbs: list[Path],
    expected_previews: list[Path],
    thumbnail_long_edge: int,
    preview_long_edge: int,
    on_page: Callable[[int, int], None] | None = None,
) -> None:
    """Render officecli HTML chunks with an isolated Playwright browser."""
    if not expected_thumbs and not expected_previews:
        return
    file_size_mb = source_path.stat().st_size // (1024 * 1024)
    open_timeout = max(120, min(600, file_size_mb // 4 + 120))
    chunk_timeout = max(120, min(600, file_size_mb // 2 + 120))
    pending = [
        page_number
        for page_number in range(1, slide_count + 1)
        if (expected_thumbs and not expected_thumbs[page_number - 1].is_file())
        or (expected_previews and not expected_previews[page_number - 1].is_file())
    ]
    if not pending:
        return

    work_dir = (temp_dir / f"officecli-render-{uuid4().hex}").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    opened = False
    try:
        _run([officecli, "open", str(source_path)], timeout=open_timeout)
        opened = True
        with sync_playwright() as playwright:
            browser_home = work_dir / "browser-home"
            browser_home.mkdir()
            browser = playwright.chromium.launch(
                channel="chrome",
                headless=True,
                chromium_sandbox=True,
                args=_CHROME_ARGS,
                timeout=60_000,
                env={**os.environ, "HOME": str(browser_home)},
            )
            try:
                for pages in _officecli_page_chunks(pending):
                    html_path = work_dir / f"pages-{pages[0]}-{pages[-1]}.html"
                    page_filter = str(pages[0]) if len(pages) == 1 else f"{pages[0]}-{pages[-1]}"
                    _run(
                        [
                            officecli,
                            "view",
                            str(source_path),
                            "html",
                            "-o",
                            str(html_path),
                            "--page",
                            page_filter,
                        ],
                        timeout=chunk_timeout,
                    )
                    if not html_path.is_file():
                        raise ThumbnailError(
                            f"officecli produced no HTML for pages {pages[0]}-{pages[-1]}"
                        )
                    _screenshot_officecli_html(
                        browser,
                        html_path,
                        pages,
                        expected_thumbs=expected_thumbs,
                        expected_previews=expected_previews,
                        thumbnail_long_edge=thumbnail_long_edge,
                        preview_long_edge=preview_long_edge,
                        slide_count=slide_count,
                        on_page=on_page,
                    )
                    html_path.unlink(missing_ok=True)
            finally:
                browser.close()
    except ThumbnailError:
        raise
    except Exception as error:
        raise ThumbnailError(f"officecli HTML rendering failed: {type(error).__name__}") from error
    finally:
        if opened:
            with contextlib.suppress(ThumbnailError):
                _run([officecli, "close", str(source_path)], timeout=60)
        shutil.rmtree(work_dir, ignore_errors=True)


def _officecli_page_chunks(page_numbers: list[int]) -> list[list[int]]:
    chunks: list[list[int]] = []
    current: list[int] = []
    for page_number in page_numbers:
        if current and (page_number != current[-1] + 1 or len(current) >= _OFFICECLI_CHUNK_SIZE):
            chunks.append(current)
            current = []
        current.append(page_number)
    if current:
        chunks.append(current)
    return chunks


def _screenshot_officecli_html(
    browser: Browser,
    html_path: Path,
    page_numbers: list[int],
    *,
    expected_thumbs: list[Path],
    expected_previews: list[Path],
    thumbnail_long_edge: int,
    preview_long_edge: int,
    slide_count: int,
    on_page: Callable[[int, int], None] | None = None,
) -> None:
    context = browser.new_context(
        viewport={"width": preview_long_edge, "height": preview_long_edge},
        device_scale_factor=1,
        service_workers="block",
        accept_downloads=False,
        reduced_motion="reduce",
    )
    try:
        context.route(
            "http://**/*",
            lambda route: route.abort("blockedbyclient"),
        )
        context.route(
            "https://**/*",
            lambda route: route.abort("blockedbyclient"),
        )
        page = context.new_page()
        page.set_default_timeout(30_000)
        response = page.goto(
            html_path.as_uri() + "#screenshot",
            wait_until="load",
            timeout=60_000,
        )
        if response is not None and response.status >= 400:
            raise ThumbnailError("officecli HTML could not be loaded")
        page.evaluate(
            """async () => {
                await document.fonts.ready;
                document.querySelectorAll('video,audio').forEach(media => media.pause());
                document.getAnimations().forEach(animation => animation.cancel());
                await new Promise(resolve =>
                    requestAnimationFrame(() => requestAnimationFrame(resolve))
                );
            }"""
        )
        for page_number in page_numbers:
            thumb = expected_thumbs[page_number - 1] if expected_thumbs else None
            preview = expected_previews[page_number - 1] if expected_previews else None
            if (thumb is None or thumb.is_file()) and (preview is None or preview.is_file()):
                if on_page is not None:
                    on_page(page_number, slide_count)
                continue
            screenshot = _capture_officecli_slide(
                page,
                page_number,
                max(
                    preview_long_edge if preview is not None else 0,
                    thumbnail_long_edge if thumb is not None else 0,
                ),
            )
            _write_officecli_images(
                screenshot,
                thumb,
                preview,
                thumbnail_long_edge,
                preview_long_edge,
            )
            if on_page is not None:
                on_page(page_number, slide_count)
    finally:
        context.close()


def _capture_officecli_slide(page: Page, page_number: int, long_edge: int) -> bytes:
    selector = f'.main > .slide-container[data-slide="{page_number}"] .slide'
    slide = page.locator(selector)
    if slide.count() != 1:
        raise ThumbnailError(f"officecli HTML is missing page {page_number}")
    page.evaluate(
        """async ({pageNumber, longEdge}) => {
            const containers = Array.from(
                document.querySelectorAll('.main > .slide-container')
            );
            const active = containers.find(
                container => Number(container.dataset.slide) === pageNumber
            );
            if (!active) throw new Error(`missing slide ${pageNumber}`);
            containers.forEach(container => {
                container.style.display = container === active ? 'flex' : 'none';
            });
            document.documentElement.style.overflow = 'hidden';
            document.body.style.overflow = 'hidden';
            document.body.style.margin = '0';
            const main = document.querySelector('.main');
            main.style.padding = '0';
            main.style.gap = '0';
            main.style.overflow = 'hidden';
            const element = active.querySelector('.slide');
            const wrapper = element.parentElement;
            element.style.transform = '';
            const width = element.offsetWidth;
            const height = element.offsetHeight;
            const scale = longEdge / Math.max(width, height);
            element.style.transform = `scale(${scale})`;
            element.style.transformOrigin = '0 0';
            wrapper.style.width = `${width * scale}px`;
            wrapper.style.height = `${height * scale}px`;
            await Promise.race([
                Promise.all(
                    Array.from(active.querySelectorAll('img'), image =>
                        image.decode().catch(() => undefined)
                    )
                ),
                new Promise(resolve => setTimeout(resolve, 15000))
            ]);
            await new Promise(resolve =>
                requestAnimationFrame(() => requestAnimationFrame(resolve))
            );
        }""",
        {"pageNumber": page_number, "longEdge": long_edge},
    )
    page.wait_for_timeout(50)
    return slide.screenshot(
        type="jpeg",
        quality=92,
        animations="disabled",
        caret="hide",
        timeout=30_000,
    )


def _write_officecli_images(
    screenshot: bytes,
    thumb: Path | None,
    preview: Path | None,
    thumbnail_long_edge: int,
    preview_long_edge: int,
) -> None:
    try:
        with Image.open(io.BytesIO(screenshot)) as original:
            image = original.convert("RGB")
            try:
                for target, edge, quality in (
                    (thumb, thumbnail_long_edge, 85),
                    (preview, preview_long_edge, 92),
                ):
                    if target is None or target.is_file():
                        continue
                    scale = edge / max(image.size)
                    dimensions = (
                        max(1, round(image.width * scale)),
                        max(1, round(image.height * scale)),
                    )
                    with image.resize(dimensions, Image.Resampling.LANCZOS) as resized:
                        staging = target.with_name(f".{target.stem}.tmp.jpg")
                        resized.save(staging, format="JPEG", quality=quality)
                        staging.replace(target)
            finally:
                image.close()
    except (OSError, ValueError) as error:
        raise ThumbnailError(f"invalid officecli screenshot: {error}") from error


def _presentation_visibility(
    source_path: Path,
    slide_count: int,
) -> tuple[list[int], list[int]]:
    presentation_part = "ppt/presentation.xml"
    with ZipFile(source_path) as package:
        presentation = ET.fromstring(package.read(presentation_part))
        relationships = ET.fromstring(package.read("ppt/_rels/presentation.xml.rels"))
        targets = {
            relation.get("Id", ""): relation.get("Target", "")
            for relation in relationships.findall(f"{{{_REL_NS}}}Relationship")
            if relation.get("Type", "").endswith("/slide")
        }
        visible: list[int] = []
        hidden: list[int] = []
        slide_ids = presentation.findall(f".//{{{_P_NS}}}sldId")
        if len(slide_ids) != slide_count:
            raise ThumbnailError(
                f"presentation references {len(slide_ids)} of {slide_count} slides"
            )
        for position, slide_id in enumerate(slide_ids, start=1):
            relationship_id = slide_id.get(f"{{{_R_NS}}}id", "")
            target = targets.get(relationship_id)
            if not target:
                raise ThumbnailError(f"slide relationship is missing: {relationship_id}")
            slide_part = posixpath.normpath(
                posixpath.join(posixpath.dirname(presentation_part), target)
            ).lstrip("/")
            slide = ET.fromstring(package.read(slide_part))
            if slide.get("show", "1").lower() in {"0", "false"}:
                hidden.append(position)
            else:
                visible.append(position)
    return visible, hidden


def _render_with_libreoffice(
    source_path: Path,
    slide_count: int,
    *,
    temp_dir: Path,
    expected_thumbs: list[Path],
    expected_previews: list[Path],
    thumbnail_long_edge: int,
    preview_long_edge: int,
    executable_finder: Callable[[str], str | None],
    on_page: Callable[[int, int], None] | None = None,
) -> None:
    """Render bounded LibreOffice PDF ranges, then rasterize with bundled PDFium."""
    if not expected_thumbs and not expected_previews:
        return
    visible_pages, hidden_pages = _presentation_visibility(source_path, slide_count)
    soffice = executable_finder("soffice")
    if soffice is None and executable_finder is shutil.which and _LO_APP_BINARY.exists():
        soffice = str(_LO_APP_BINARY)
    if not soffice:
        raise ThumbnailError("thumbnail renderer unavailable: soffice")

    work_dir = temp_dir / f"thumbnail-{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    file_size_mb = source_path.stat().st_size // (1024 * 1024)
    lo_timeout = max(180, min(900, file_size_mb // 2 + 180))
    try:
        profile_dir = work_dir / "lo-profile"
        profile_dir.mkdir()
        libreoffice_env = _libreoffice_environment(work_dir, temp_dir)
        pdf_path = work_dir / f"{source_path.stem}.pdf"
        for page_numbers in _page_chunks(
            visible_pages,
            _libreoffice_page_chunk_size(file_size_mb),
        ):
            with contextlib.suppress(OSError):
                pdf_path.unlink()
            _run(
                [
                    soffice,
                    f"-env:UserInstallation={profile_dir.as_uri()}",
                    "--headless",
                    "--invisible",
                    "--nologo",
                    "--nodefault",
                    "--nolockcheck",
                    "--norestore",
                    "--nofirststartwizard",
                    "--convert-to",
                    _libreoffice_pdf_filter(page_numbers),
                    "--outdir",
                    str(work_dir),
                    str(source_path),
                ],
                timeout=lo_timeout,
                env=libreoffice_env,
            )
            if not pdf_path.is_file():
                raise ThumbnailError("LibreOffice did not produce a PDF")

            if expected_thumbs:
                _pdfium_images(
                    pdf_path,
                    expected_thumbs,
                    long_edge=thumbnail_long_edge,
                    quality=85,
                    page_numbers=page_numbers,
                    prefix_name="thumbnail",
                )
            if expected_previews:
                _pdfium_images(
                    pdf_path,
                    expected_previews,
                    long_edge=preview_long_edge,
                    quality=92,
                    page_numbers=page_numbers,
                    prefix_name="preview",
                )
            with contextlib.suppress(OSError):
                pdf_path.unlink()
            if on_page is not None:
                for page_number in page_numbers:
                    on_page(page_number, slide_count)
        if hidden_pages:
            _render_hidden_slides(
                source_path,
                hidden_pages,
                slide_count=slide_count,
                temp_dir=work_dir,
                expected_thumbs=expected_thumbs,
                expected_previews=expected_previews,
                thumbnail_long_edge=thumbnail_long_edge,
                preview_long_edge=preview_long_edge,
                executable_finder=executable_finder,
                on_page=on_page,
            )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ThumbnailError(str(error)) from error
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _page_chunks(page_numbers: list[int], chunk_size: int) -> list[list[int]]:
    return [
        page_numbers[index : index + chunk_size]
        for index in range(0, len(page_numbers), chunk_size)
    ]


def _libreoffice_page_chunk_size(file_size_mb: int) -> int:
    if file_size_mb < 256:
        return 64
    if file_size_mb < 512:
        return 32
    return _LIBREOFFICE_PAGE_CHUNK_SIZE


def _libreoffice_pdf_filter(page_numbers: list[int]) -> str:
    options = {
        "PageRange": {
            "type": "string",
            "value": ",".join(str(page_number) for page_number in page_numbers),
        }
    }
    return "pdf:impress_pdf_Export:" + json.dumps(options, separators=(",", ":"))


def _libreoffice_environment(work_dir: Path, temp_dir: Path) -> dict[str, str]:
    font_config, font_dir, font_cache = _prepare_libreoffice_fonts(temp_dir)
    cache_dir = work_dir / "cache"
    cache_dir.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "FONTCONFIG_FILE": str(font_config),
            "FONTCONFIG_PATH": str(font_config.parent),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SAL_USE_VCLPLUGIN": "svp",
            "SAL_FONTPATH_PRIVATE": str(font_dir),
            "XDG_CACHE_HOME": str(cache_dir),
            "FC_CACHEDIR": str(font_cache),
        }
    )
    return environment


def _prepare_libreoffice_fonts(temp_dir: Path) -> tuple[Path, Path, Path]:
    root = temp_dir / "libreoffice-fontconfig-v2"
    font_dir = root / "fonts"
    cache_dir = root / "cache"
    config_path = root / "fonts.conf"
    marker_path = root / "sources.sha256"
    sources = _libreoffice_font_sources()
    identity = hashlib.sha256(
        "\n".join(
            f"{source}:{source.stat().st_size}:{source.stat().st_mtime_ns}" for source in sources
        ).encode()
    ).hexdigest()
    try:
        current_identity = marker_path.read_text("ascii").strip()
    except OSError:
        current_identity = ""
    if (
        current_identity == identity
        and config_path.is_file()
        and font_dir.is_dir()
        and cache_dir.is_dir()
    ):
        return config_path, font_dir, cache_dir

    shutil.rmtree(root, ignore_errors=True)
    font_dir.mkdir(parents=True)
    cache_dir.mkdir()
    for index, source in enumerate(sources):
        (font_dir / f"{index:05d}{source.suffix.lower()}").symlink_to(source)
    config = ET.Element("fontconfig")
    ET.SubElement(config, "dir").text = str(font_dir)
    ET.SubElement(config, "cachedir").text = str(cache_dir)
    for family, comparison in (
        ("FZLanTingHeiPro", "contains"),
        ("方正兰亭黑Pro_GB18030", "eq"),
        ("等线", "contains"),
        ("微软雅黑", "eq"),
        ("SimHei", "eq"),
    ):
        _add_fontconfig_substitution(
            config,
            family,
            "PingFang SC",
            comparison=comparison,
        )
    ET.ElementTree(config).write(config_path, encoding="utf-8", xml_declaration=True)
    marker_path.write_text(identity + "\n", encoding="ascii")
    return config_path, font_dir, cache_dir


def _add_fontconfig_substitution(
    config: ET.Element,
    requested_family: str,
    replacement_family: str,
    *,
    comparison: str,
) -> None:
    match = ET.SubElement(config, "match", {"target": "pattern"})
    test = ET.SubElement(match, "test", {"name": "family", "compare": comparison})
    ET.SubElement(test, "string").text = requested_family
    edit = ET.SubElement(
        match,
        "edit",
        {"name": "family", "mode": "prepend", "binding": "strong"},
    )
    ET.SubElement(edit, "string").text = replacement_family


def _libreoffice_font_sources() -> list[Path]:
    resources = _LO_APP_BINARY.parents[1] / "Resources"
    roots = [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library" / "Fonts",
        resources / "fonts",
        resources / "resource" / "common" / "fonts",
    ]
    assets = Path("/System/Library/AssetsV2")
    if assets.is_dir():
        roots.extend(assets.glob("com_apple_MobileAsset_Font*/*.asset/AssetData"))
    sources: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        sources.update(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() in _FONT_EXTENSIONS
        )
    return sorted(sources, key=lambda path: str(path).casefold())


def _render_hidden_slides(
    source_path: Path,
    hidden_pages: list[int],
    *,
    slide_count: int,
    temp_dir: Path,
    expected_thumbs: list[Path],
    expected_previews: list[Path],
    thumbnail_long_edge: int,
    preview_long_edge: int,
    executable_finder: Callable[[str], str | None],
    on_page: Callable[[int, int], None] | None = None,
) -> None:
    from pptlib.export.ooxml import ExportError, SlideRef, export_slides

    hidden_pptx = temp_dir / "hidden-slides.pptx"
    hidden_assets = temp_dir / "hidden-assets"
    try:
        export_slides(
            [SlideRef(f"hidden-{page}", source_path, page) for page in hidden_pages],
            hidden_pptx,
        )
    except ExportError as error:
        raise ThumbnailError(str(error)) from error
    _, still_hidden = _presentation_visibility(hidden_pptx, len(hidden_pages))
    if still_hidden:
        raise ThumbnailError("temporary hidden-slide export remained hidden")
    render_deck_thumbnails(
        hidden_pptx,
        "hidden",
        len(hidden_pages),
        assets_dir=hidden_assets,
        temp_dir=temp_dir / "hidden-work",
        renderer="libreoffice",
        thumbnail_long_edge=thumbnail_long_edge,
        preview_long_edge=preview_long_edge,
        generate_previews=bool(expected_previews),
        force=True,
        executable_finder=executable_finder,
    )
    for index, page_number in enumerate(hidden_pages, start=1):
        if expected_thumbs:
            shutil.copyfile(
                hidden_assets / "thumbnails" / f"hidden_s{index:05d}.jpg",
                expected_thumbs[page_number - 1],
            )
        if expected_previews:
            shutil.copyfile(
                hidden_assets / "previews" / f"hidden_s{index:05d}.jpg",
                expected_previews[page_number - 1],
            )
        if on_page is not None:
            on_page(page_number, slide_count)


def _pdfium_images(
    pdf_path: Path,
    expected: list[Path],
    *,
    long_edge: int,
    quality: int,
    page_numbers: list[int],
    prefix_name: str,
) -> None:
    try:
        document = pdfium.PdfDocument(pdf_path)
        try:
            if len(document) != len(page_numbers):
                raise ThumbnailError(
                    f"{prefix_name} renderer returned {len(document)} "
                    f"of {len(page_numbers)} visible pages"
                )
            for pdf_index, page_number in enumerate(page_numbers):
                page = document[pdf_index]
                try:
                    width, height = page.get_size()
                    scale = long_edge / max(width, height)
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil().convert("RGB")
                        try:
                            staging = expected[page_number - 1].with_name(
                                f".{expected[page_number - 1].stem}.tmp.jpg"
                            )
                            if max(image.size) == long_edge:
                                image.save(staging, format="JPEG", quality=quality)
                            else:
                                ratio = long_edge / max(image.size)
                                dimensions = (
                                    max(1, round(image.width * ratio)),
                                    max(1, round(image.height * ratio)),
                                )
                                with image.resize(
                                    dimensions,
                                    Image.Resampling.LANCZOS,
                                ) as resized:
                                    resized.save(staging, format="JPEG", quality=quality)
                            staging.replace(expected[page_number - 1])
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            document.close()
    except ThumbnailError:
        raise
    except Exception as error:
        raise ThumbnailError(f"{prefix_name} PDF rendering failed: {error}") from error


def _run(
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ThumbnailError(str(error)) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "render command failed").strip()
        raise ThumbnailError(detail[-500:])
