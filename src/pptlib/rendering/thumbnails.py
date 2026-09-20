from __future__ import annotations

import contextlib
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4


class ThumbnailError(RuntimeError):
    """Raised when a slide deck cannot be rendered to image assets."""


_LO_APP_BINARY = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")


def render_deck_thumbnails(
    source_path: Path,
    version_id: str,
    slide_count: int,
    *,
    assets_dir: Path,
    temp_dir: Path,
    renderer: str = "auto",
    thumbnail_long_edge: int = 640,
    preview_long_edge: int = 1440,
    generate_previews: bool = True,
    executable_finder: Callable[[str], str | None] = shutil.which,
) -> int:
    """Render a deck to cached JPEG thumbnails and preview images.

    Two backends are supported:

    - ``officecli`` renders per page through a headless browser and keeps the
      deck resident, so large media-heavy files no longer force a full deck
      LibreOffice PDF conversion (which produced multi-GB intermediates and
      all-or-nothing timeouts).
    - ``libreoffice`` is the original PPTX → PDF → ``pdftoppm`` pipeline, kept
      as a fallback for environments without a browser (e.g. some containers).

    ``renderer="auto"`` prefers officecli and falls back to LibreOffice.
    When ``generate_previews`` is False only thumbnails are produced (used by
    startup backfill to avoid re-rendering large existing files).
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
    if thumbs_exist and (previews_exist or not want_previews):
        return slide_count

    if not source_path.exists():
        raise ThumbnailError(f"source file not found: {source_path}")

    officecli = executable_finder("officecli")
    order = _backend_order(renderer, officecli)
    last_error: ThumbnailError | None = None
    for backend in order:
        try:
            if backend == "officecli":
                _render_with_officecli(
                    officecli or "officecli",
                    source_path,
                    slide_count,
                    expected_thumbs=expected_thumbs if not thumbs_exist else [],
                    expected_previews=(
                        expected_previews if want_previews and not previews_exist else []
                    ),
                    thumbnail_long_edge=thumbnail_long_edge,
                    preview_long_edge=preview_long_edge,
                )
            else:
                _render_with_libreoffice(
                    source_path,
                    slide_count,
                    temp_dir=temp_dir,
                    expected_thumbs=expected_thumbs if not thumbs_exist else [],
                    expected_previews=(
                        expected_previews if want_previews and not previews_exist else []
                    ),
                    thumbnail_long_edge=thumbnail_long_edge,
                    preview_long_edge=preview_long_edge,
                    executable_finder=executable_finder,
                )
            return slide_count
        except ThumbnailError as error:
            last_error = error
            continue
    raise last_error or ThumbnailError("no renderer produced images")


def _backend_order(renderer: str, officecli: str | None) -> list[str]:
    if renderer == "officecli":
        return ["officecli"]
    if renderer == "libreoffice":
        return ["libreoffice"]
    # auto: prefer officecli when present, always keep LibreOffice as fallback.
    return (["officecli"] if officecli else []) + ["libreoffice"]


def _render_with_officecli(
    officecli: str,
    source_path: Path,
    slide_count: int,
    *,
    expected_thumbs: list[Path],
    expected_previews: list[Path],
    thumbnail_long_edge: int,
    preview_long_edge: int,
) -> None:
    """Render per page via officecli, keeping the deck resident."""
    if not expected_thumbs and not expected_previews:
        return
    file_size_mb = source_path.stat().st_size // (1024 * 1024)
    # Cold open of a large deck can take a while; scale the ceiling with size.
    open_timeout = max(120, min(600, file_size_mb // 4 + 120))
    page_timeout = max(60, min(300, file_size_mb // 8 + 60))
    opened = False
    try:
        _run([officecli, "open", str(source_path)], timeout=open_timeout)
        opened = True
        _officecli_pages(
            officecli, source_path, expected_thumbs, thumbnail_long_edge, page_timeout
        )
        _officecli_pages(
            officecli, source_path, expected_previews, preview_long_edge, page_timeout
        )
    finally:
        if opened:
            with contextlib.suppress(ThumbnailError):
                _run([officecli, "close", str(source_path)], timeout=60)


def _officecli_pages(
    officecli: str,
    source_path: Path,
    expected: list[Path],
    long_edge: int,
    timeout: int,
) -> None:
    for index, target in enumerate(expected, start=1):
        if target.is_file():
            continue
        # officecli infers the image format from the output extension, so the
        # staging file must keep a .jpg suffix (a bare .tmp is rejected).
        tmp = target.with_name(f".{target.stem}.tmp.jpg")
        _run(
            [
                officecli,
                "view",
                str(source_path),
                "screenshot",
                "-o",
                str(tmp),
                "--page",
                str(index),
                "--screenshot-width",
                str(long_edge),
            ],
            timeout=timeout,
        )
        if not tmp.is_file():
            raise ThumbnailError(f"officecli produced no image for page {index}")
        tmp.replace(target)


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
) -> None:
    """PPTX → PDF → pdftoppm fallback (whole-deck conversion)."""
    if not expected_thumbs and not expected_previews:
        return
    soffice = executable_finder("soffice")
    if soffice is None and executable_finder is shutil.which and _LO_APP_BINARY.exists():
        soffice = str(_LO_APP_BINARY)
    pdftoppm = executable_finder("pdftoppm")
    if not soffice or not pdftoppm:
        missing = ", ".join(
            name for name, value in (("soffice", soffice), ("pdftoppm", pdftoppm)) if not value
        )
        raise ThumbnailError(f"thumbnail renderer unavailable: {missing}")

    work_dir = temp_dir / f"thumbnail-{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    file_size_mb = source_path.stat().st_size // (1024 * 1024)
    lo_timeout = max(180, min(900, file_size_mb // 2 + 180))
    render_timeout = max(120, min(600, file_size_mb // 4 + 120))
    try:
        profile_dir = work_dir / "lo-profile"
        profile_dir.mkdir()
        _run(
            [
                soffice,
                f"-env:UserInstallation={profile_dir.as_uri()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(work_dir),
                str(source_path),
            ],
            timeout=lo_timeout,
        )
        pdfs = list(work_dir.glob("*.pdf"))
        if not pdfs:
            raise ThumbnailError("LibreOffice did not produce a PDF")
        pdf_path = pdfs[0]

        if expected_thumbs:
            _pdftoppm_images(
                pdftoppm,
                pdf_path,
                work_dir / "thumb",
                expected_thumbs,
                long_edge=thumbnail_long_edge,
                quality=85,
                slide_count=slide_count,
                timeout=render_timeout,
                prefix_name="thumbnail",
            )
        if expected_previews:
            _pdftoppm_images(
                pdftoppm,
                pdf_path,
                work_dir / "preview",
                expected_previews,
                long_edge=preview_long_edge,
                quality=92,
                slide_count=slide_count,
                timeout=render_timeout,
                prefix_name="preview",
            )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ThumbnailError(str(error)) from error
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _pdftoppm_images(
    pdftoppm: str,
    pdf_path: Path,
    prefix: Path,
    expected: list[Path],
    *,
    long_edge: int,
    quality: int,
    slide_count: int,
    timeout: int,
    prefix_name: str,
) -> None:
    _run(
        [
            pdftoppm,
            "-jpeg",
            "-jpegopt",
            f"quality={quality}",
            "-scale-to",
            str(long_edge),
            str(pdf_path),
            str(prefix),
        ],
        timeout=timeout,
    )
    rendered = sorted(
        prefix.parent.glob(f"{prefix.name}-*.jpg"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )
    if not rendered:
        raise ThumbnailError(f"pdftoppm did not produce images for {prefix_name}")
    for index, rendered_path in enumerate(rendered[:slide_count], start=1):
        shutil.copyfile(rendered_path, expected[index - 1])


def _run(command: list[str], *, timeout: int) -> None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ThumbnailError(str(error)) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "render command failed").strip()
        raise ThumbnailError(detail[-500:])
