"""Render sanitized HTML pages without executing any source JavaScript."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import Browser, Request, Response, Route, sync_playwright

from pptlib.html.preview import start_preview
from pptlib.html.source import HtmlSource
from pptlib.rendering.thumbnails import ThumbnailError

_CACHE_VERSION = 2
_WIDTH = 1920
_HEIGHT = 1080
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
_SETTLE = """async () => {
  await document.fonts.ready;
  await Promise.all(Array.from(document.images, image =>
    image.decode().catch(() => undefined)));
  document.querySelectorAll('video,audio').forEach(media => media.pause());
  document.getAnimations().forEach(animation => animation.cancel());
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}"""


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            staging = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        staging.replace(path)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def _dimensions(edge: int) -> tuple[int, int]:
    return edge, max(1, round(edge * _HEIGHT / _WIDTH))


def _valid_jpeg(path: Path, edge: int, digest: object) -> bool:
    try:
        if (
            not isinstance(digest, str)
            or not path.is_file()
            or path.stat().st_size > 16 * 1024 * 1024
        ):
            return False
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            return False
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "JPEG" or image.size != _dimensions(edge):
                return False
            image.load()
        return True
    except (OSError, ValueError):
        return False


def _save_images(
    screenshot: bytes,
    thumb: Path,
    preview: Path,
    thumb_edge: int,
    preview_edge: int,
) -> dict[str, str]:
    digests: dict[str, str] = {}
    with Image.open(io.BytesIO(screenshot)) as original:
        if original.size != (_WIDTH, _HEIGHT):
            raise ValueError("HTML screenshot has unexpected dimensions")
        image = original.convert("RGB")
        try:
            for kind, target, edge, quality in (
                ("thumbnail", thumb, thumb_edge, 85),
                ("preview", preview, preview_edge, 92),
            ):
                with image.resize(_dimensions(edge), Image.Resampling.LANCZOS) as resized:
                    output = io.BytesIO()
                    resized.save(output, format="JPEG", quality=quality)
                    content = output.getvalue()
                _atomic_write(target, content)
                digests[kind] = hashlib.sha256(content).hexdigest()
        finally:
            image.close()
    return digests


def _screenshot_page(
    browser: Browser,
    source: HtmlSource,
    page_key: str,
    expected_sha256: str,
) -> bytes:
    with ExitStack() as stack:
        url = stack.enter_context(
            start_preview(source, page_key, static=True, expected_sha256=expected_sha256)
        )
        context = browser.new_context(
            viewport={"width": _WIDTH, "height": _HEIGHT},
            device_scale_factor=1,
            service_workers="block",
            accept_downloads=False,
            reduced_motion="reduce",
        )
        stack.callback(context.close)
        endpoint = urlsplit(url)

        def isolate(route: Route) -> None:
            request = route.request
            target = urlsplit(request.url)
            if (
                request.method in {"GET", "HEAD"}
                and target.scheme == endpoint.scheme
                and target.netloc == endpoint.netloc
                and not target.query
                and (
                    target.path == endpoint.path
                    or target.path.startswith(endpoint.path + "resource/")
                )
            ):
                route.continue_()
            else:
                route.abort("blockedbyclient")

        context.route("**/*", isolate)
        page = context.new_page()
        page.set_default_timeout(30_000)
        failed_resources: list[int] = []
        failed_requests: list[str] = []

        def check_response(response: Response) -> None:
            if response.status >= 400:
                failed_resources.append(response.status)

        def check_failure(request: Request) -> None:
            if request.url.startswith(url) and not (
                request.resource_type == "media" and request.failure == "net::ERR_ABORTED"
            ):
                failed_requests.append(request.resource_type)

        page.on("response", check_response)
        page.on("requestfailed", check_failure)
        response = page.goto(url, wait_until="load", timeout=60_000)
        if response is None or response.status != 200:
            raise ValueError("HTML preview page could not be loaded")
        page.wait_for_function("document.documentElement.dataset.pptlibReady === 'true'")
        # A bounded race is needed because evaluate() has no per-call timeout.
        page.evaluate(
            "() => Promise.race([(" + _SETTLE + ")(), new Promise((_, reject) => "
            "setTimeout(() => reject(new Error('HTML assets timed out')), 30000))])"
        )
        screenshot = page.screenshot(
            type="png",
            full_page=False,
            animations="disabled",
            caret="hide",
            timeout=30_000,
        )
        if failed_resources or failed_requests:
            raise ValueError("HTML preview resource failed integrity or loading checks")
        return screenshot


def _load_completed(marker: Path, identity: dict[str, object], force: bool) -> dict[str, Any]:
    if force:
        return {}
    try:
        payload = json.loads(marker.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("identity") != identity:
        return {}
    completed = payload.get("completed")
    return completed if isinstance(completed, dict) else {}


def _write_marker(
    marker: Path,
    identity: dict[str, object],
    completed: dict[str, Any],
) -> None:
    _atomic_write(
        marker,
        (
            json.dumps({"identity": identity, "completed": completed}, sort_keys=True) + "\n"
        ).encode(),
    )


def render_html_thumbnails(
    source_path: Path,
    version_id: str,
    slide_count: int,
    *,
    assets_dir: Path,
    temp_dir: Path,
    expected_sha256: str,
    page_keys: list[str],
    thumbnail_long_edge: int = 640,
    preview_long_edge: int = 1920,
    on_page: Callable[[int, int], None] | None = None,
    force: bool = False,
) -> int:
    """Render selected pages, resuming only fingerprint-matched, verified JPEGs.

    ``page_keys`` order determines deterministic 1-based filenames. The source
    and media stay in place; even the full-size PNG is held only in memory.
    ``temp_dir`` is retained for compatibility with the rendering application API.
    """
    try:
        if (
            slide_count != len(page_keys)
            or slide_count < 0
            or len(set(page_keys)) != len(page_keys)
        ):
            raise ValueError("HTML slide count and unique page_keys must agree")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", version_id):
            raise ValueError("Invalid HTML version_id")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("Invalid expected HTML fingerprint")
        if not all(1 <= edge <= _WIDTH for edge in (thumbnail_long_edge, preview_long_edge)):
            raise ValueError("HTML image long edges must be between 1 and 1920")
        if not page_keys:
            return 0
        source = HtmlSource(source_path)
        if not hmac.compare_digest(source.fingerprint, expected_sha256):
            raise ValueError("HTML source fingerprint changed since import")
        if not set(page_keys).issubset(source.page_keys):
            raise ValueError("HTML page_keys are missing from the source")
        identity: dict[str, object] = {
            "cache_version": _CACHE_VERSION,
            "backend": "sanitized-html-chrome",
            "fingerprint": expected_sha256,
            "page_keys": page_keys,
            "thumbnail_long_edge": thumbnail_long_edge,
            "preview_long_edge": preview_long_edge,
        }
        marker = assets_dir / "html-render-meta" / f"{version_id}.json"
        completed = _load_completed(marker, identity, force)
        with ExitStack() as stack:
            browser: Browser | None = None
            for index, key in enumerate(page_keys, 1):
                name = f"{version_id}_s{index:05d}.jpg"
                thumb = assets_dir / "thumbnails" / name
                preview = assets_dir / "previews" / name
                record = completed.get(str(index))
                valid = (
                    isinstance(record, dict)
                    and _valid_jpeg(thumb, thumbnail_long_edge, record.get("thumbnail"))
                    and _valid_jpeg(preview, preview_long_edge, record.get("preview"))
                )
                if not valid:
                    if browser is None:
                        playwright = stack.enter_context(sync_playwright())
                        browser_path = os.environ.get("PPTLIB_HTML_BROWSER")
                        browser_home = temp_dir / "html-browser-home"
                        browser_home.mkdir(parents=True, exist_ok=True)
                        browser_env: dict[str, str | float | bool] = {
                            **os.environ,
                            "HOME": str(browser_home),
                        }
                        if browser_path:
                            executable = Path(browser_path).expanduser().resolve()
                            if not executable.is_file():
                                raise ValueError("Configured HTML browser does not exist")
                            browser = playwright.chromium.launch(
                                executable_path=str(executable),
                                headless=True,
                                chromium_sandbox=True,
                                args=_CHROME_ARGS,
                                timeout=60_000,
                                env=browser_env,
                            )
                        else:
                            browser = playwright.chromium.launch(
                                channel="chrome",
                                headless=True,
                                chromium_sandbox=True,
                                args=_CHROME_ARGS,
                                timeout=60_000,
                                env=browser_env,
                            )
                        stack.callback(browser.close)
                    screenshot = _screenshot_page(browser, source, key, expected_sha256)
                    completed[str(index)] = _save_images(
                        screenshot,
                        thumb,
                        preview,
                        thumbnail_long_edge,
                        preview_long_edge,
                    )
                    _write_marker(marker, identity, completed)
                if on_page is not None:
                    on_page(index, slide_count)
        return slide_count
    except ThumbnailError:
        raise
    except Exception as error:
        # Browser exceptions contain the preview's bearer URL; don't surface it.
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        raise ThumbnailError(f"HTML rendering failed: {detail}") from error
