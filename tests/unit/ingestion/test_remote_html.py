from __future__ import annotations

import hashlib
import socket
import threading
import time
from pathlib import Path

import pytest

from pptlib.html import remote
from pptlib.html.remote import Download, RemoteCapture, normalize_url, public_addresses
from pptlib.html.source import HtmlSource

ROOT = "https://slides.example.test/deck"
HTML = """<!doctype html><html><head>
<meta name="fs-deck-generator" content="render-deck"><title>演示标题</title>
<link rel="stylesheet" href="/styles/main.css"></head><body>
<div class="slide-frame"><section class="slide" data-slide-key="one">
<h1>第一张</h1><img src="/images/chart.png"><video src="/movie.mp4"></video>
<input type="radio" name="tabs" id="a" checked><label for="a">说明</label>
</section></div><script src="/do-not-fetch.js"></script>
<script id="fs-deck-notes" type="application/json">{"one":"讲稿保留"}</script>
</body></html>"""


def fake_download(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    resources = {
        ROOT: (HTML.encode(), "text/html"),
        "https://slides.example.test/styles/main.css": (
            b'@import "extra.css";.slide{background:url("../images/bg.png")} '
            b'@font-face{font-family:x;src:url("../fonts/a.woff2")}', "text/css"
        ),
        "https://slides.example.test/styles/extra.css": (b"h1{color:blue}", "text/css"),
        "https://slides.example.test/images/bg.png": (b"background", "image/png"),
        "https://slides.example.test/images/chart.png": (b"chart", "image/png"),
        "https://slides.example.test/fonts/a.woff2": (b"font", "font/woff2"),
        "https://slides.example.test/movie.mp4": (b"video", "video/mp4"),
    }

    def fetch(url: str, limit: int, *, deadline: float) -> Download:
        calls.append(url)
        content, mime = resources[url]
        if len(content) > limit:
            raise ValueError("在线资源超过归档大小限制")
        return Download(content, url, mime)

    monkeypatch.setattr(remote, "download", fetch)
    return calls


def test_captures_relative_css_fonts_and_media_without_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fake_download(monkeypatch)
    capture = RemoteCapture(tmp_path)
    path, metadata = capture.capture(ROOT + "#32")
    source = HtmlSource(path)
    assert source.page_keys == ["one"]
    assert len(source.resources) == 6
    assert source.external_urls == set()
    assert metadata["source_url"] == ROOT
    assert metadata["title"] == "演示标题"
    assert all("do-not-fetch" not in call for call in calls)
    assert source.document.xpath('//*[@id="fs-deck-notes"]/text()') == ['{"one":"讲稿保留"}']
    assert b"video" in [
        source.read_resource(ref) for ref in source.resources if ref.endswith(".mp4")
    ]
    for ref, item in source.resources.items():
        assert hashlib.sha256(source.read_resource(ref)).hexdigest() == item["sha256"]


def test_repeat_capture_has_stable_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_download(monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    a, _ = RemoteCapture(first).capture(ROOT + "#1")
    b, _ = RemoteCapture(second).capture(ROOT + "#32")
    assert HtmlSource(a).fingerprint == HtmlSource(b).fingerprint


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "https://user:pass@example.com/", "https://example.com:8123/",
    "https://example.com\\@localhost/", "https://example.com/\x00x",
])
def test_rejects_unsafe_url_forms(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "10.2.3.4", "169.254.169.254"])
def test_rejects_private_or_mixed_dns_answers(
    address: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)),
    ])
    with pytest.raises(ValueError, match="私有网络"):
        public_addresses("slides.example", 443)


def test_checks_total_resource_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_download(monkeypatch)
    with pytest.raises(ValueError, match="大小限制"):
        RemoteCapture(tmp_path, max_bytes=len(HTML.encode()) + 1).capture(ROOT)


def test_deadline_is_enforced_before_opening_connection() -> None:
    with pytest.raises(ValueError, match="超时"):
        remote.download(ROOT, 1024, deadline=time.monotonic() - 1)


@pytest.mark.parametrize("trickle", [False, True])
def test_truncated_and_trickling_bodies_are_not_successful(
    monkeypatch: pytest.MonkeyPatch, trickle: bool,
) -> None:
    client, server = socket.socketpair()
    monkeypatch.setattr(remote, "public_addresses", lambda *a: ["8.8.8.8"])
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: client)

    def reply() -> None:
        try:
            server.recv(8192)
            server.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
            if trickle:
                for _ in range(30):
                    server.sendall(b"x")
                    time.sleep(0.04)
            else:
                server.sendall(b"abc")
        except OSError:
            pass
        finally:
            server.close()

    worker = threading.Thread(target=reply)
    worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(ValueError):
            remote.download("http://slides.example.test/", 2048, deadline=started + 0.2)
        assert time.monotonic() - started < 0.8
    finally:
        client.close()
        worker.join(timeout=2)


def test_string_image_set_is_downloaded_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fake_download(monkeypatch)
    css = RemoteCapture(tmp_path).css(
        '.slide{background:image-set("/images/chart.png" 1x)}', ROOT, nested=False
    )
    assert calls == ["https://slides.example.test/images/chart.png"]
    assert "resources/" in css
    assert "https:" not in css


def test_missing_resource_is_reported_without_losing_other_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_download(monkeypatch)
    fetch = remote.download

    def unavailable(url: str, limit: int, *, deadline: float) -> Download:
        if url.endswith("movie.mp4"):
            raise remote.ResourceUnavailable("HTTP 404")
        return fetch(url, limit, deadline=deadline)

    monkeypatch.setattr(remote, "download", unavailable)
    entry, metadata = RemoteCapture(tmp_path).capture(ROOT)
    source = HtmlSource(entry)
    assert source.page_keys == ["one"]
    assert source.external_urls == {"https://slides.example.test/movie.mp4"}
    assert len(metadata["missing_resources"]) == 1


def test_svg_children_and_subtitles_are_fingerprinted(tmp_path: Path) -> None:
    (tmp_path / "child.png").write_bytes(b"first")
    (tmp_path / "chart.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="child.png"/></svg>'
    )
    (tmp_path / "captions.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nHello")
    (tmp_path / "index.html").write_text(
        '<section class="slide"><img src="chart.svg">'
        '<video><track src="captions.vtt"></video></section>'
    )
    source = HtmlSource(tmp_path / "index.html")
    assert set(source.resources) == {"chart.svg", "child.png", "captions.vtt"}
    (tmp_path / "child.png").write_bytes(b"other")
    assert HtmlSource(tmp_path / "index.html").fingerprint != source.fingerprint


def test_unrecognized_page_never_enters_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptlib.application.import_url import import_url
    from pptlib.bootstrap import initialize
    from pptlib.config import load_settings
    from pptlib.infrastructure.db.connection import connect

    monkeypatch.setattr(remote, "download", lambda url, limit, **kw:
                        Download(b"<html><body>Please sign in</body></html>", url, "text/html"))
    settings = load_settings(env={"PPTLIB_HOME": str(tmp_path), "PPTLIB_ENABLE_HTML": "1"})
    initialize(settings)
    with connect(settings.database_path) as connection:
        with pytest.raises(ValueError):
            import_url(connection, ROOT, settings=settings)
        assert connection.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 0
    assert list((settings.assets_dir / "html-archives").iterdir()) == []


def test_url_import_reuses_snapshot_and_preserves_origins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptlib.application import html_assets
    from pptlib.application.import_url import import_url
    from pptlib.bootstrap import initialize
    from pptlib.config import load_settings
    from pptlib.infrastructure.db.connection import connect
    from pptlib.infrastructure.db.library import SqliteSlideCatalog

    fake_download(monkeypatch)
    monkeypatch.setattr(html_assets, "render_html_version", lambda *a, **k: 1)
    settings = load_settings(env={"PPTLIB_HOME": str(tmp_path), "PPTLIB_ENABLE_HTML": "1"})
    initialize(settings)
    with connect(settings.database_path) as connection:
        first = import_url(connection, ROOT + "#1", settings=settings)
        second = import_url(connection, ROOT + "#32", settings=settings)
        assert first["ok"] and second["ok"]
        assert first["imported"][0]["deck_id"] == second["imported"][0]["deck_id"]
        assert second["imported"][0]["action"] == "unchanged"
        assert connection.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 1
    deck = SqliteSlideCatalog(settings.database_path).decks()[0]
    assert deck.source_url == ROOT
    assert deck.captured_at
    assert "演示标题" in deck.name
