from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4


class ThumbnailError(RuntimeError):
    """Raised when a slide deck cannot be rendered to image assets."""


def render_deck_thumbnails(
    source_path: Path,
    version_id: str,
    slide_count: int,
    *,
    assets_dir: Path,
    temp_dir: Path,
    executable_finder: Callable[[str], str | None] = shutil.which,
) -> int:
    """Render a deck to cached JPEG thumbnails and return the page count.

    LibreOffice handles PPTX layout and ``pdftoppm`` rasterizes the resulting
    PDF. Rendering is best-effort during import: callers may catch
    :class:`ThumbnailError` and keep text search available when either tool is
    missing or a particular deck is malformed.
    """
    if slide_count < 1:
        return 0
    target_dir = assets_dir / "thumbnails"
    target_dir.mkdir(parents=True, exist_ok=True)
    expected = [
        target_dir / f"{version_id}_s{index:05d}.jpg"
        for index in range(1, slide_count + 1)
    ]
    if all(path.is_file() for path in expected):
        return slide_count

    soffice = executable_finder("soffice")
    if soffice is None and executable_finder is shutil.which:
        app_binary = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        soffice = str(app_binary) if app_binary.exists() else None
    pdftoppm = executable_finder("pdftoppm")
    if not soffice or not pdftoppm:
        missing = ", ".join(
            name
            for name, value in (("soffice", soffice), ("pdftoppm", pdftoppm))
            if not value
        )
        raise ThumbnailError(f"thumbnail renderer unavailable: {missing}")

    work_dir = temp_dir / f"thumbnail-{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
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
            timeout=180,
        )
        pdfs = list(work_dir.glob("*.pdf"))
        if not pdfs:
            raise ThumbnailError("LibreOffice did not produce a PDF")
        prefix = work_dir / "slide"
        _run(
            [
                pdftoppm,
                "-jpeg",
                "-scale-to",
                "640",
                str(pdfs[0]),
                str(prefix),
            ],
            timeout=180,
        )
        rendered = sorted(
            work_dir.glob("slide-*.jpg"),
            key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
        )
        if not rendered:
            raise ThumbnailError("pdftoppm did not produce thumbnails")
        for index, rendered_path in enumerate(rendered[:slide_count], start=1):
            shutil.copyfile(rendered_path, expected[index - 1])
        return min(len(rendered), slide_count)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ThumbnailError(str(error)) from error
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


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
