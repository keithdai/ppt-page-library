from __future__ import annotations

import hashlib
import http.client
import io
import json
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import quote, urlsplit
from zipfile import ZipFile

import pytest
from lxml import html  # type: ignore[import-untyped]
from PIL import Image

from pptlib.html import preview
from pptlib.html.preview import PreviewWarning, start_preview
from pptlib.html.source import HtmlSource
from pptlib.rendering import html as renderer
from pptlib.rendering.thumbnails import ThumbnailError


def _deck(content: str = "", *, head: str = "", pages: int = 2) -> str:
    frames = "".join(
        f'<section class="slide-frame"><div class="slide" data-layout="raw" '
        f'data-slide-key="page-{index}">'
        f"<h1>Page {index}</h1>{content if index == 1 else ''}</div></section>"
        for index in range(1, pages + 1)
    )
    return (
        '<!doctype html><html><head><meta name="fs-deck-generator" content="render-deck">'
        f"{head}</head><body><nav>Unrelated navigation</nav>"
        f'<main class="deck">{frames}</main><footer>Unrelated footer</footer></body></html>'
    )


def _source(
    tmp_path: Path,
    content: str = "",
    *,
    head: str = "",
    pages: int = 2,
    resources: dict[str, bytes] | None = None,
) -> HtmlSource:
    for ref, data in (resources or {}).items():
        target = tmp_path / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    path = tmp_path / "deck.html"
    path.write_text(_deck(content, head=head, pages=pages), encoding="utf-8")
    return HtmlSource(path)


def _request(
    url: str,
    *,
    method: str = "GET",
    path: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(url)
    connection = http.client.HTTPConnection(str(parsed.hostname), parsed.port, timeout=5)
    try:
        connection.request(method, path if path is not None else parsed.path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def _resource_url(url: str, ref: str) -> str:
    return url + "resource/" + quote(ref, safe="")


def _assert_closed(url: str) -> None:
    parsed = urlsplit(url)
    with (
        pytest.raises(OSError),
        socket.create_connection((str(parsed.hostname), int(parsed.port or 0)), timeout=0.5),
    ):
        pass


def test_selected_page_and_layout_styles_only(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        head="<style>.slide{background:tomato} @keyframes pulse{to{opacity:.5}}</style>",
    )
    with start_preview(source, "page-1") as url:
        status, headers, body = _request(url)
        tree = html.fromstring(body)
        assert status == 200
        assert len(tree.xpath('//*[contains(@class,"slide-frame")]')) == 1
        assert "Page 1" in tree.text_content()
        assert "Page 2" not in tree.text_content()
        assert "Unrelated" not in tree.text_content()
        assert "@keyframes pulse" in body.decode()
        assert "background:tomato" in body.decode()
        assert "animation:none" not in body.decode()
        assert "active" in tree.xpath('//*[@data-slide-key="page-1"]')[0].get("class")
        assert tree.xpath('//*[@data-slide-key="page-1"]')[0].get("data-layout") == "raw"
        assert "--fs-scale" in body.decode()
        assert headers["Cache-Control"] == "no-store, max-age=0"
    _assert_closed(url)


def test_removes_active_html_svg_and_unsafe_attributes(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        """<script>window.pwned=1</script><iframe src="https://evil.invalid/"></iframe>
        <object data="https://evil.invalid/"></object><embed src="https://evil.invalid/">
        <form action="https://evil.invalid/"><input autofocus onfocus="alert(1)"></form>
        <div onclick="alert(1)" srcdoc="bad" contenteditable="true">Keep me</div>
        <a href="javascript:alert(1)" ping="https://evil.invalid/" target="_blank">link</a>
        <svg viewBox="0 0 10 10" onload="alert(1)">
          <script>alert(2)</script><foreignObject><iframe></iframe></foreignObject>
          <rect fill="red" width="10" height="10"/>
          <animate attributeName="href" values="javascript:alert(1)"/>
          <use href="https://evil.invalid/a.svg#x"/>
        </svg>""",
        head='<base href="https://evil.invalid/"><meta http-equiv="refresh" content="0;url=x">',
    )
    with pytest.warns(PreviewWarning), start_preview(source, "page-1") as url:
        _, headers, body = _request(url)
    tree = html.fromstring(body)
    assert len(tree.xpath("//script")) == 1
    assert tree.xpath("//script")[0].text == preview._RUNTIME
    assert not tree.xpath("//iframe|//object|//embed|//form|//input|//base")
    assert not tree.xpath("//foreignobject|//animate")
    assert "pwned" not in body.decode()
    assert "evil.invalid" not in body.decode()
    assert "srcdoc=" not in body.decode()
    assert "onclick=" not in body.decode()
    assert "onload=" not in body.decode()
    assert "viewBox=" in body.decode()
    assert "Keep me" in body.decode()
    csp = headers["Content-Security-Policy"]
    assert f"script-src 'sha256-{preview._RUNTIME_HASH}'" in csp
    for directive in ("connect-src", "worker-src", "frame-src", "object-src", "form-action"):
        assert f"{directive} 'none'" in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.invalid/i.png",
        "//evil.invalid/a",
        "javascript:alert(1)",
        "data:image/svg+xml,bad",
        "file:///etc/passwd",
        "../secret.png",
        "%2e%2e/secret.png",
        "%252e%252e/secret.png",
        "/etc/passwd",
        r"a\..\b",
        "a.png?leak=secret",
        "a.png%00",
        "a.png\n",
    ],
)
def test_unsafe_urls_fail_closed(tmp_path: Path, value: str) -> None:
    sanitizer = preview._Sanitizer(_source(tmp_path), "/token/")
    assert sanitizer.url(value) == ""


def test_css_parser_rewrites_escaped_urls_nested_rules_and_image_set(tmp_path: Path) -> None:
    source = _source(tmp_path, '<img src="assets/a.png">', resources={"assets/a.png": b"image"})
    sanitizer = preview._Sanitizer(source, "/token/")
    css = r"""
    @import "https://evil.invalid/a.css";
    @media (min-width:1px) {
      .slide { background:u\72l("https://evil.invalid/a.png");
        mask: url(assets/a.png);
        background-image:image-set("assets/a.png" 1x, "//evil.invalid/b" 2x);
        width:calc(10px + var(--size)); animation:pulse 1s infinite; }
    }
    @keyframes pulse { 0% {opacity:.5} 100% {opacity:1} }
    .bad { behavior:url(assets/a.png); -moz-binding:url(assets/a.png);
      --escape:"</style><script>oops</script>"; }
    """
    cleaned = sanitizer.css(css)
    assert "evil.invalid" not in cleaned
    assert "/token/resource/assets%2Fa.png" in cleaned
    assert "behavior:" not in cleaned and "-moz-binding:" not in cleaned
    assert "</style>" not in cleaned
    assert "calc(10px + var(--size))" in cleaned
    assert "@media" in cleaned and "@keyframes" in cleaned and "pulse 1s infinite" in cleaned
    assert sanitizer.blocked


def test_stylesheet_dependencies_are_entry_relative_for_nested_zip(tmp_path: Path) -> None:
    archive = tmp_path / "deck.zip"
    with ZipFile(archive, "w") as package:
        package.writestr(
            "nested/deck/index.html",
            _deck(head='<link rel="stylesheet" href="css/main.css">'),
        )
        package.writestr("nested/deck/css/main.css", '.slide{background:url("../img/a.png")}')
        package.writestr("nested/deck/img/a.png", b"image")
        package.writestr("unrelated.txt", b"secret")
    source = HtmlSource(archive)
    with start_preview(source, "page-1") as url:
        _, _, document = _request(url)
        assert _resource_url("", "css/main.css") in document.decode()
        status, _, css = _request(_resource_url(url, "css/main.css"))
        assert status == 200
        assert "resource/img%2Fa.png" in css.decode()
        assert _request(_resource_url(url, "img/a.png"))[2] == b"image"
        assert _request(_resource_url(url, "unrelated.txt"))[0] == 404
        assert sorted(tmp_path.iterdir()) == [archive]


def test_svg_resource_is_sanitized_not_served_raw(tmp_path: Path) -> None:
    svg = b"""<!DOCTYPE svg [<!ENTITY leak SYSTEM "file:///etc/passwd">]>
    <svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)" viewBox="0 0 10 10">
    <script>alert(1)</script><text>&leak;</text><rect width="10" height="10"/>
    <foreignObject><div>bad</div></foreignObject>
    <image href="https://evil.invalid/image.png"/></svg>"""
    source = _source(tmp_path, '<img src="image.svg">', resources={"image.svg": svg})
    with start_preview(source, "page-1") as url, pytest.warns(PreviewWarning):
        status, _, content = _request(_resource_url(url, "image.svg"))
    assert status == 200
    assert b"script" not in content and b"onload" not in content
    assert b"ENTITY" not in content and b"&leak;" not in content
    assert b"evil.invalid" not in content and b"foreignObject" not in content
    assert b"rect" in content and b"viewBox" in content


def test_video_is_native_metadata_only_and_static_mode_disables_animation(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        '<video src="clip.mp4" autoplay loop onplay="bad()"></video>',
        resources={"clip.mp4": b"0123456789"},
    )
    with start_preview(source, "page-1", static=True) as url:
        _, _, content = _request(url)
    tree = html.fromstring(content)
    video = tree.xpath("//video")[0]
    assert "controls" in video.attrib
    assert video.get("preload") == "metadata"
    assert not ({"autoplay", "loop", "onplay"} & set(video.attrib))
    assert b"animation:none!important" in content


def test_inline_poster_and_video_are_served_as_bounded_resources(tmp_path: Path) -> None:
    poster = "data:image/png;base64,aW1hZ2U="
    video = "data:video/mp4;base64,MDEyMzQ1Njc4OQ=="
    source = _source(
        tmp_path,
        f'<video poster="{poster}"><source src="{video}" type="video/mp4"></video>',
    )
    with start_preview(source, "page-1") as url:
        _, _, content = _request(url)
        tree = html.fromstring(content)
        poster_url = tree.xpath("//video")[0].get("poster")
        video_url = tree.xpath("//source")[0].get("src")
        assert poster_url and video_url
        assert "/resource/inline%2F" in poster_url
        assert _request(url, path=poster_url)[2] == b"image"
        status, headers, body = _request(
            url,
            path=video_url,
            headers={"Range": "bytes=2-5"},
        )
        assert status == 206 and body == b"2345"
        assert headers["Content-Range"] == "bytes 2-5/10"


@pytest.mark.parametrize(
    ("range_value", "status", "content_range", "expected"),
    [
        ("bytes=2-5", 206, "bytes 2-5/10", b"2345"),
        ("bytes=7-", 206, "bytes 7-9/10", b"789"),
        ("bytes=-3", 206, "bytes 7-9/10", b"789"),
        ("bytes=0-100", 206, "bytes 0-9/10", b"0123456789"),
        ("bytes=10-", 416, "bytes */10", b""),
        ("bytes=5-1", 416, "bytes */10", b""),
        ("bytes=-0", 416, "bytes */10", b""),
        ("bytes=0-1,3-4", 416, "bytes */10", b""),
        ("garbage", 416, "bytes */10", b""),
    ],
)
def test_media_ranges(
    tmp_path: Path,
    range_value: str,
    status: int,
    content_range: str,
    expected: bytes,
) -> None:
    source = _source(tmp_path, '<video src="clip.mp4">', resources={"clip.mp4": b"0123456789"})
    with start_preview(source, "page-1") as url:
        actual, headers, body = _request(
            _resource_url(url, "clip.mp4"), headers={"Range": range_value}
        )
        assert actual == status and body == expected
        assert headers["Content-Range"] == content_range
        head_status, head_headers, head_body = _request(
            _resource_url(url, "clip.mp4"), method="HEAD", headers={"Range": range_value}
        )
        assert head_status == status and head_body == b""
        assert head_headers["Content-Length"] == str(len(expected))


def test_host_origin_token_route_and_methods_are_restricted(tmp_path: Path) -> None:
    source = _source(tmp_path, '<img src="a.png">', resources={"a.png": b"image"})
    with start_preview(source, "page-1") as url:
        assert len(urlsplit(url).path.strip("/")) >= 40
        for host in ("localhost", "evil.invalid", "127.0.0.1"):
            assert _request(url, headers={"Host": host})[0] == 403
        assert _request(url, headers={"Origin": "https://evil.invalid"})[0] == 403
        assert _request(url, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
        for path in (
            "/",
            "/wrong-token/",
            "/" + urlsplit(url).path,
            urlsplit(url).path + "?x",
            urlsplit(url).path + "resource/../deck.html",
            urlsplit(url).path + "resource/%2e%2e%2Fdeck.html",
            urlsplit(url).path + "resource/%252e%252e%252Fdeck.html",
            urlsplit(url).path + "resource/%61.png",
        ):
            assert _request(url, path=path)[0] == 404
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"):
            status, headers, _ = _request(url, method=method)
            assert status == 405 and headers["Allow"] == "GET, HEAD"
        status, headers, body = _request(url, method="HEAD")
        assert status == 200 and not body and int(headers["Content-Length"]) > 0


def test_duplicate_host_is_rejected(tmp_path: Path) -> None:
    with start_preview(_source(tmp_path), "page-1") as url:
        parsed = urlsplit(url)
        connection = http.client.HTTPConnection(str(parsed.hostname), parsed.port)
        try:
            connection.putrequest("GET", parsed.path, skip_host=True)
            connection.putheader("Host", parsed.netloc)
            connection.putheader("Host", parsed.netloc)
            connection.endheaders()
            response = connection.getresponse()
            assert response.status == 403
            response.read()
        finally:
            connection.close()


def test_resource_mutation_and_symlink_replacement_fail_closed(tmp_path: Path) -> None:
    source = _source(tmp_path, '<img src="a.png">', resources={"a.png": b"image"})
    with start_preview(source, "page-1") as url:
        (tmp_path / "a.png").write_bytes(b"changed")
        assert _request(_resource_url(url, "a.png"))[0] == 409
        (tmp_path / "secret.png").write_bytes(b"image")
        (tmp_path / "a.png").unlink()
        (tmp_path / "a.png").symlink_to(tmp_path / "secret.png")
        assert _request(_resource_url(url, "a.png"))[0] == 409


def test_wrong_fingerprint_prevents_server_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = MagicMock()
    monkeypatch.setattr(preview, "_PreviewServer", server)
    with (
        pytest.raises(ValueError, match="fingerprint"),
        start_preview(_source(tmp_path), "page-1", expected_sha256="0" * 64),
    ):
        pytest.fail("must not yield")
    server.assert_not_called()


def test_cleanup_on_body_exception_and_invalid_page(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(RuntimeError), start_preview(source, "page-1") as url:
        raise RuntimeError("consumer failed")
    _assert_closed(url)
    with pytest.raises(ValueError, match="page_key"), start_preview(source, "missing"):
        pytest.fail("must not yield")


def test_shutdown_interrupts_clients_with_incomplete_headers(tmp_path: Path) -> None:
    source = _source(tmp_path)
    started = time.monotonic()
    with start_preview(source, "page-1") as url:
        parsed = urlsplit(url)
        client = socket.create_connection((str(parsed.hostname), int(parsed.port or 0)), timeout=1)
        client.sendall(b"GET / HTTP/1.1\r\nHost: ")
        assert _request(url)[0] == 200
    client.close()
    assert time.monotonic() - started < 2
    _assert_closed(url)


def test_manifest_and_css_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path, '<img src="a.png">', resources={"a.png": b"image"})
    source.resources["../secret.png"] = source.resources["a.png"]
    with pytest.raises(ValueError, match="manifest"):
        preview._Sanitizer(source, "/token/")
    del source.resources["../secret.png"]
    monkeypatch.setattr(preview, "_MAX_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        preview._Sanitizer(source, "/token/")


@pytest.fixture
def png() -> bytes:
    with Image.new("RGB", (1920, 1080), "#c04020") as image:
        output = io.BytesIO()
        image.save(output, "PNG")
    return output.getvalue()


def _mock_browser(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    engine = MagicMock()

    @contextmanager
    def fake_playwright() -> Iterator[MagicMock]:
        try:
            yield engine
        finally:
            engine.stop()

    monkeypatch.setattr(renderer, "sync_playwright", fake_playwright)
    return engine


def _render(source: HtmlSource, tmp_path: Path, **options: Any) -> int:
    return renderer.render_html_thumbnails(
        source.path,
        "ver_html",
        len(source.page_keys),
        assets_dir=tmp_path / "assets",
        temp_dir=tmp_path / "temp",
        expected_sha256=source.fingerprint,
        page_keys=source.page_keys,
        **options,
    )


def test_renderer_atomic_jpegs_cache_marker_and_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    png: bytes,
) -> None:
    monkeypatch.delenv("PPTLIB_HTML_BROWSER", raising=False)
    source = _source(tmp_path)
    engine = _mock_browser(monkeypatch)
    screenshot = MagicMock(return_value=png)
    monkeypatch.setattr(renderer, "_screenshot_page", screenshot)
    progress: list[tuple[int, int]] = []
    assert _render(source, tmp_path, on_page=lambda a, b: progress.append((a, b))) == 2
    assert progress == [(1, 2), (2, 2)]
    assert screenshot.call_count == 2
    launch = engine.chromium.launch.call_args.kwargs
    assert launch.pop("env")["HOME"] == str((tmp_path / "temp/html-browser-home").resolve())
    assert launch == {
        "channel": "chrome",
        "headless": True,
        "chromium_sandbox": True,
        "args": renderer._CHROME_ARGS,
        "timeout": 60_000,
    }
    engine.chromium.launch.return_value.close.assert_called_once()
    engine.stop.assert_called_once()
    for kind, size in (("thumbnails", (640, 360)), ("previews", (1920, 1080))):
        for index in (1, 2):
            with Image.open(tmp_path / "assets" / kind / f"ver_html_s{index:05d}.jpg") as image:
                assert image.format == "JPEG" and image.size == size
    assert (tmp_path / "temp/html-browser-home").is_dir()
    assert not list((tmp_path / "assets").rglob("*.tmp"))
    marker = tmp_path / "assets/html-render-meta/ver_html.json"
    assert json.loads(marker.read_text())["identity"]["fingerprint"] == source.fingerprint
    screenshot.reset_mock()
    assert _render(source, tmp_path) == 2
    screenshot.assert_not_called()
    assert _render(source, tmp_path, force=True) == 2
    assert screenshot.call_count == 2


def test_partial_failure_resumes_only_valid_completed_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    png: bytes,
) -> None:
    source = _source(tmp_path)
    engine = _mock_browser(monkeypatch)
    screenshot = MagicMock(side_effect=[png, RuntimeError("browser crashed")])
    monkeypatch.setattr(renderer, "_screenshot_page", screenshot)
    with pytest.raises(ThumbnailError, match="RuntimeError"):
        _render(source, tmp_path)
    engine.chromium.launch.return_value.close.assert_called_once()
    engine.stop.assert_called_once()
    marker = json.loads((tmp_path / "assets/html-render-meta/ver_html.json").read_text())
    assert set(marker["completed"]) == {"1"}
    screenshot = MagicMock(return_value=png)
    monkeypatch.setattr(renderer, "_screenshot_page", screenshot)
    assert _render(source, tmp_path) == 2
    assert screenshot.call_count == 1 and screenshot.call_args.args[2] == "page-2"
    (tmp_path / "assets/thumbnails/ver_html_s00001.jpg").write_bytes(b"corrupt")
    screenshot.reset_mock()
    assert _render(source, tmp_path) == 2
    assert screenshot.call_count == 1 and screenshot.call_args.args[2] == "page-1"


def test_renderer_stale_marker_and_changed_source_never_reuse_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    png: bytes,
) -> None:
    source = _source(tmp_path)
    engine = _mock_browser(monkeypatch)
    screenshot = MagicMock(return_value=png)
    monkeypatch.setattr(renderer, "_screenshot_page", screenshot)
    assert _render(source, tmp_path) == 2
    marker = tmp_path / "assets/html-render-meta/ver_html.json"
    marker.write_text("{}")
    screenshot.reset_mock()
    assert _render(source, tmp_path) == 2
    assert screenshot.call_count == 2
    source.path.write_text(source.html.replace("Page 1", "Changed"))
    engine.reset_mock()
    with pytest.raises(ThumbnailError, match="fingerprint"):
        _render(source, tmp_path)
    engine.chromium.launch.assert_not_called()


def test_renderer_context_and_server_cleanup_after_navigation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    browser = MagicMock()
    context = browser.new_context.return_value
    context.new_page.return_value.goto.side_effect = RuntimeError("navigation failed")
    urls: list[str] = []

    @contextmanager
    def tracked_preview(*args: Any, **kwargs: Any) -> Iterator[str]:
        with start_preview(*args, **kwargs) as url:
            urls.append(url)
            yield url

    monkeypatch.setattr(renderer, "start_preview", tracked_preview)
    with pytest.raises(RuntimeError, match="navigation"):
        renderer._screenshot_page(browser, source, "page-1", source.fingerprint)
    context.close.assert_called_once()
    assert len(urls) == 1
    _assert_closed(urls[0])
    route_handler = context.route.call_args.args[1]
    for target in ("https://evil.invalid/", "file:///etc/passwd", "http://127.0.0.1:1/"):
        route = MagicMock()
        route.request.url = target
        route.request.method = "GET"
        route_handler(route)
        route.abort.assert_called_once_with("blockedbyclient")
        route.continue_.assert_not_called()


def test_atomic_write_removes_staging_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "image.jpg"
    target.write_bytes(b"existing")
    monkeypatch.setattr(Path, "replace", MagicMock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        renderer._atomic_write(target, b"replacement")
    assert target.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [target]


def test_launch_failure_stops_playwright(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    engine = _mock_browser(monkeypatch)
    engine.chromium.launch.side_effect = RuntimeError("Chrome missing")
    with pytest.raises(ThumbnailError, match="RuntimeError"):
        _render(source, tmp_path)
    engine.stop.assert_called_once()
    assert not (tmp_path / "assets/html-render-meta/ver_html.json").exists()


@pytest.mark.skipif(
    not Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").is_file(),
    reason="installed Chrome channel is required",
)
def test_installed_chrome_renders_sanitized_slide(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        '<script>document.body.textContent="unsafe";</script>',
        head="<style>.slide{background:rgb(200,40,20);color:white}</style>",
        pages=1,
    )
    with pytest.warns(PreviewWarning):
        assert _render(source, tmp_path) == 1
    target = tmp_path / "assets/previews/ver_html_s00001.jpg"
    with Image.open(target) as image:
        assert image.size == (1920, 1080)
        red, green, blue = image.getpixel((960, 540))
        assert red > 180 and green < 60 and blue < 40
    assert hashlib.sha256(source.path.read_bytes()).hexdigest() == source.fingerprint
