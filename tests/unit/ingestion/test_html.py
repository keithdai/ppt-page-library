from __future__ import annotations

import base64
import json
from pathlib import Path
from subprocess import CompletedProcess
from zipfile import ZipFile

import pytest

from pptlib.application import html_assets
from pptlib.application.import_decks import scan_and_import
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.html import adapter
from pptlib.html.adapter import ingest_html
from pptlib.html.source import HtmlSource, css_urls, parse_document
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.repositories import search_slides
from pptlib.rendering.thumbnails import ThumbnailError


def document(body: str = "<h1>财务分析</h1>", key: str = "finance") -> str:
    return (
        "<!doctype html><html><head>"
        '<meta name="fs-deck-generator" content="render-deck">'
        '<title>演示测试</title></head><body><div class="deck">'
        f'<div class="slide-frame"><div class="slide" data-slide-key="{key}">'
        f"{body}</div></div></div></body></html>"
    )


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    script = tmp_path / "engine.py"
    script.write_text("# deterministic test engine")
    monkeypatch.setattr(adapter, "engine_cli", lambda: script)
    inputs: list[str] = []

    def run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        text = Path(command[3]).read_text()
        inputs.append(text)
        tree = parse_document(text)
        pages = [
            {
                "key": key,
                "layout": "raw",
                "data": {"html": "<h1>财务分析</h1>"},
            }
            for key in tree.xpath("//*[@data-slide-key]/@data-slide-key")
        ]
        Path(command[4]).write_text(json.dumps({"slides": pages}))
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(adapter.subprocess, "run", run)
    return inputs


def test_media_removed_before_external_engine_and_no_payload_in_metadata(
    tmp_path: Path,
    fake_engine: list[str],
) -> None:
    encoded = base64.b64encode(b"original-video" * 100_000).decode()
    path = tmp_path / "deck.html"
    original = document(f'<h1>财务分析</h1><video src="data:video/mp4;base64,{encoded}"/>')
    path.write_text(original)
    result = ingest_html(HtmlSource(path), temp_dir=tmp_path / "jobs")
    assert len(fake_engine) == 1
    assert encoded not in fake_engine[0]
    assert "pptlib-dependency:" in fake_engine[0]
    assert encoded not in result.parsed.dependencies_json
    assert len(result.parsed.dependencies_json) < 1000
    assert result.parsed.slides[0].title == "财务分析"
    assert json.loads(result.parsed.slides[0].capabilities_json)["video"]
    assert path.read_text() == original
    assert list((tmp_path / "jobs").iterdir()) == []


def test_bundle_dependency_changes_fingerprint_without_entry_change(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    image = tmp_path / "assets/picture.png"
    image.write_bytes(b"first-image")
    (tmp_path / "assets/style.css").write_text(
        '.slide { background: url("picture.png"); } /* url(../not-a-resource) */'
    )
    path = tmp_path / "index.html"
    path.write_text(
        document().replace(
            "</head>",
            '<link rel="stylesheet" href="assets/style.css"></head>',
        )
    )
    first = HtmlSource(path)
    image.write_bytes(b"second-image")
    second = HtmlSource(path)
    assert first.entry_sha256 == second.entry_sha256
    assert first.fingerprint != second.fingerprint
    assert set(first.resources) == {"assets/style.css", "assets/picture.png"}
    with pytest.raises(ValueError, match="变化"):
        first.read_resource("assets/picture.png")
    with pytest.raises(ValueError, match="声明"):
        first.read_resource("../private.txt")


def test_css_dependency_scanner_ignores_comments_and_decodes_escapes() -> None:
    assert css_urls("/* url(...) data: */ p {color:red}") == []
    assert css_urls('@import "base.css"; p{background:u\\72l("hero.png")}') == [
        "base.css",
        "hero.png",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        "<html><h1>ordinary website</h1></html>",
        document().replace('data-slide-key="finance"', 'data-slide-key="bad key"'),
        document().replace(
            "</body>",
            '<div class="slide-frame"><div class="slide" '
            'data-slide-key="finance"></div></div></body>',
        ),
        document('<img src="../outside.png">'),
    ],
)
def test_unsupported_or_unsafe_html_is_rejected(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "deck.html"
    path.write_text(payload)
    with pytest.raises(ValueError):
        HtmlSource(path)


def test_zip_is_read_in_place_and_rejects_ambiguity_and_traversal(tmp_path: Path) -> None:
    source = tmp_path / "deck.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("deck/index.html", document('<img src="assets/cover.png">'))
        archive.writestr("deck/assets/cover.png", b"image")
    before = source.read_bytes()
    parsed = HtmlSource(source)
    assert parsed.page_keys == ["finance"]
    assert parsed.read_resource("assets/cover.png") == b"image"
    assert source.read_bytes() == before
    assert list(tmp_path.iterdir()) == [source]
    with ZipFile(source, "a") as archive:
        archive.writestr("other/index.html", document())
    with pytest.raises(ValueError, match="唯一"):
        HtmlSource(source)
    with ZipFile(source, "w") as archive:
        archive.writestr("index.html", document())
        archive.writestr("../outside.png", b"bad")
    with pytest.raises(ValueError, match="不安全"):
        HtmlSource(source)


def test_html_sync_reuses_versions_moves_paths_and_updates_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_engine: list[str],
) -> None:
    roots = tmp_path / "sources"
    roots.mkdir()
    path = roots / "deck.html"
    path.write_text(document())
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_ENABLE_HTML": "1",
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
        }
    )
    initialize(settings)
    monkeypatch.setattr(html_assets, "render_html_version", lambda *_args, **_kwargs: 1)
    connection = connect(settings.database_path)
    try:
        first = scan_and_import(connection, [roots], settings=settings)
        assert not first.failed
        original = first.imported[0]
        assert search_slides(connection, "财务分析")[0].source_format == "render_deck_html"
        second = scan_and_import(connection, [roots], settings=settings)
        assert second.skipped == 1
        assert len(fake_engine) == 1
        moved = roots / "renamed.html"
        path.rename(moved)
        third = scan_and_import(connection, [roots], settings=settings)
        assert third.imported[0].action == "moved"
        assert third.imported[0].version_id == original.version_id
        assert len(fake_engine) == 1
        moved.write_text(document("<h1>经营分析</h1>"))
        fourth = scan_and_import(connection, [roots], settings=settings)
        assert fourth.imported[0].deck_id == original.deck_id
        assert fourth.imported[0].version_id != original.version_id
        assert len(fake_engine) == 2
    finally:
        connection.close()


def test_failed_preview_is_reported_and_reimport_retries_without_backfill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_engine: list[str],
) -> None:
    path = tmp_path / "deck.html"
    path.write_text(document())
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_ENABLE_HTML": "1",
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
        }
    )
    initialize(settings)

    def fail(*args: object, **kwargs: object) -> int:
        raise ThumbnailError("browser failed")

    monkeypatch.setattr(html_assets, "render_html_version", fail)
    connection = connect(settings.database_path)
    try:
        first = scan_and_import(connection, [path], settings=settings)
        assert first.failed and "browser failed" in first.failed[0][1]
        assert connection.execute("SELECT count(*) FROM slides").fetchone()[0] == 1
        monkeypatch.setattr(html_assets, "render_html_version", lambda *_args, **_kwargs: 1)
        recovered = scan_and_import(connection, [path], settings=settings)
        assert not recovered.failed
        assert recovered.skipped == 1
        assert len(fake_engine) == 1
    finally:
        connection.close()


def test_feature_disabled_keeps_html_out_of_import(
    tmp_path: Path,
    fake_engine: list[str],
) -> None:
    (tmp_path / "deck.html").write_text(document())
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    connection = connect(settings.database_path)
    try:
        report = scan_and_import(connection, [tmp_path], settings=settings)
        assert report.discovered == 0
        assert not fake_engine
    finally:
        connection.close()
