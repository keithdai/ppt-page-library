from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import html as lh  # type: ignore[import-untyped]

from pptlib.application import html_assets
from pptlib.application.import_decks import scan_and_import
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.html.adapter import ingest_html
from pptlib.html.normalize import normalize_document
from pptlib.html.preview import _Sanitizer
from pptlib.html.source import HtmlSource, parse_document
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.repositories import search_slides


def _source(tmp_path: Path, body: str, head: str = "") -> HtmlSource:
    path = tmp_path / "index.html"
    path.write_text(
        f"<!doctype html><html><head>{head}</head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return HtmlSource(path)


def test_render_deck_preserves_raw_html_dom_and_hash(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        '<div class="slide-frame"><div class="slide" data-slide-key="original">'
        "<h1>Original</h1></div></div>",
        '<meta name="fs-deck-generator" content="render-deck">',
    )
    before = source.path.read_bytes()
    assert source.html == before.decode()
    assert source.dom_adapter == "render-deck"
    assert source.page_keys == ["original"]
    assert source.page_sizes == {"original": (1920, 1080)}
    assert lh.tostring(source.document) == lh.tostring(parse_document(source.html))
    assert source.entry_sha256 == source.fingerprint == hashlib.sha256(before).hexdigest()
    ingest_html(source, temp_dir=tmp_path / "unused")
    assert source.path.read_bytes() == before
    assert lh.tostring(source.document) == lh.tostring(parse_document(source.html))


def test_reveal_leaf_pages_keep_order_and_source_ancestor_context(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        '<div class="reveal"><div class="slides">'
        '<section id="start"><h1>Start</h1></section>'
        '<section class="vertical"><section id="detail"><h2>Detail</h2></section>'
        "<section><h2>Next</h2></section></section>"
        '<section id="end"><h2>End</h2></section></div></div>',
    )
    assert source.dom_adapter == "reveal"
    assert source.page_keys == ["start", "detail", "page-0003", "end"]
    assert len(source.document.xpath('//*[@class="slide-frame"]')) == 4
    assert len(source.document.xpath('//*[@class="vertical"]//*[@class="slide-frame"]')) == 2
    assert source.document.xpath('//*[@data-slide-key="detail"]')[0].tag == "section"
    assert "slide-frame" not in source.html
    assert source.page_sizes == dict.fromkeys(source.page_keys, (1280, 720))
    assert source.fingerprint == hashlib.sha256(source.path.read_bytes()).hexdigest()


def test_generic_stable_unique_bounded_keys_and_normalization_idempotence(tmp_path: Path) -> None:
    long_key = "a" * 160
    source = _source(
        tmp_path,
        '<section data-slide="intro"><h1>Intro</h1></section>'
        '<div class="slide" data-slide-key="intro"><h2>Duplicate</h2></div>'
        '<section data-slide="intro-2"><p>Reserved</p></section>'
        '<div class="slide" data-slide-key="bad key"><p>Missing ID</p></div>'
        f'<div class="slide" data-slide-key="{long_key}"><p>Long</p></div>'
        f'<div class="slide" data-slide-key="{long_key}"><p>Duplicate long</p></div>',
    )
    assert source.page_keys == [
        "intro",
        "intro-3",
        "intro-2",
        "page-0004",
        long_key,
        "a" * 158 + "-2",
    ]
    assert len(set(source.page_keys)) == len(source.page_keys)
    assert HtmlSource(source.path).page_keys == source.page_keys
    tree_before = lh.tostring(source.document)
    assert normalize_document(source.document).page_keys == source.page_keys
    assert lh.tostring(source.document) == tree_before


@pytest.mark.parametrize(
    ("head", "attributes", "expected"),
    [
        ("", 'data-width="1440" data-height="900"', (1440, 900)),
        ("", 'width="1080" height="1920"', (1080, 1920)),
        ("", 'style="width:1024px;height:768px"', (1024, 768)),
        ("<style>.slide {width:1600px;height:900px}</style>", "", (1600, 900)),
        (
            "<style>:root{--slide-width:1200px;--slide-height:800px}"
            ".slide{width:var(--slide-width);height:var(--slide-height)}</style>",
            "",
            (1200, 800),
        ),
        ("", 'data-pptlib-width="999999" data-pptlib-height="-1"', (1280, 720)),
        ("", 'style="width:100vw;height:100vh"', (1280, 720)),
    ],
)
def test_generic_geometry_is_bounded_and_available_to_preview(
    tmp_path: Path, head: str, attributes: str, expected: tuple[int, int]
) -> None:
    source = _source(
        tmp_path, f'<section class="slide" {attributes}><h1>Title</h1></section>', head
    )
    key = source.page_keys[0]
    assert source.page_sizes[key] == expected
    page = source.document.xpath("//*[@data-slide-key=$key]", key=key)[0]
    for node in (page, page.getparent()):
        assert (int(node.get("data-pptlib-width")), int(node.get("data-pptlib-height"))) == expected
    result = ingest_html(source, temp_dir=tmp_path / "unused")
    reference = json.loads(result.parsed.slides[0].composition_ref_json)
    assert (reference["width"], reference["height"]) == expected


def test_generic_page_is_consumable_by_existing_preview_sanitizer(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        '<div class="slides"><section id="one"><h1>First</h1></section>'
        '<section id="two"><h1>Second</h1></section></div>',
    )
    document = _Sanitizer(source, "/test/").document("two", static=True)
    tree = parse_document(document.decode())
    assert tree.xpath("//*[@data-slide-key]/@data-slide-key") == ["two"]
    assert tree.xpath("//*[@data-slide-key]/@data-pptlib-width") == ["1280"]
    assert tree.xpath("//*[@data-slide-key]/@data-pptlib-height") == ["720"]
    assert "Second" in tree.text_content() and "First" not in tree.text_content()


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("<main><h1>Website</h1><section>News</section></main>", "Unrecognized"),
        ('<div id="app"></div><script>renderApp()</script>', "Unrecognized"),
        ('<div class="slide"><script>renderSlide()</script></div>', "no static content"),
        ('<template><section class="slide"><h1>Template</h1></section></template>', "Unrecognized"),
        ('<div class="slides"><section data-markdown="remote.md"></section></div>', "no static"),
        ('<div class="slide"><div class="slide">Nested</div></div>', "nested static slides"),
    ],
)
def test_websites_and_js_only_presentations_fail_explicitly(
    tmp_path: Path, body: str, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        _source(tmp_path, body)


def test_page_limit_and_malformed_render_deck_do_not_fall_back(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="2000"):
        _source(tmp_path, "<section data-slide><h1>Page</h1></section>" * 2001)
    with pytest.raises(ValueError, match="empty"):
        _source(
            tmp_path,
            '<section class="slide"><h1>No frame</h1></section>',
            '<meta name="fs-deck-generator" content="render-deck">',
        )


def test_empty_source_has_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.html"
    path.write_bytes(b" \n")
    with pytest.raises(ValueError, match="empty"):
        HtmlSource(path)


def test_native_notes_body_references_and_capabilities(tmp_path: Path) -> None:
    notes = "First line\nSecond line with whitespace.  "
    source = _source(
        tmp_path,
        '<div class="slides"><section id="first"><h1>First <em>title</em></h1>'
        '<p>Indexed <b>body</b></p><aside class="notes">Reveal notes'
        "<style>.note {color:red}</style><script>Never notes</script></aside>"
        "<script>Never indexed</script><style>.red {color:red}</style></section>"
        '<section id="second"><h2>Second</h2></section></div>',
        '<script type="application/json" id="fs-deck-notes">'
        + json.dumps({"second": notes})
        + "</script>",
    )
    result = ingest_html(source, temp_dir=tmp_path / "unused")
    first, second = result.parsed.slides
    assert first.title == "First title"
    assert first.body_text == "First title Indexed body"
    assert first.notes_text == "Reveal notes"
    assert second.notes_text == notes
    assert result.canonical["slides"][1]["notes"] == notes
    assert json.loads(result.parsed.capabilities_json) == {
        "html_export": True,
        "pptx_export": False,
    }
    assert all(
        json.loads(slide.capabilities_json)["html_export"]
        and not json.loads(slide.capabilities_json)["pptx_export"]
        for slide in result.parsed.slides
    )
    assert result.parsed.source_format == "render_deck_html"
    assert result.parsed.canonical_format == "html_dom_reference"
    assert result.canonical["pptlib"]["page_keys"] == source.page_keys
    reference = json.loads(first.composition_ref_json)
    assert reference["entry"] == "index.html"
    assert reference["source_sha256"] == source.fingerprint
    assert reference["entry_sha256"] == source.entry_sha256
    assert reference["dom_adapter"] == "reveal"
    assert source.document.xpath("//script[@id='fs-deck-notes']")
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("notes", ["not json", '["wrong shape"]', '{"first":42}'])
def test_malformed_notes_warn_without_discarding_pages(tmp_path: Path, notes: str) -> None:
    source = _source(
        tmp_path,
        '<section class="slide"><h1>Still indexed</h1></section>',
        f'<script id="fs-deck-notes" type="application/json">{notes}</script>',
    )
    result = ingest_html(source, temp_dir=tmp_path)
    assert result.parsed.slides[0].title == "Still indexed"
    assert any("fs-deck-notes" in warning for warning in json.loads(result.parsed.warnings_json))


def test_notes_survive_normalized_invalid_key(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        '<section class="slide" data-slide-key="invalid key"><h1>Title</h1></section>',
        '<script id="fs-deck-notes" type="application/json">{"invalid key":"Notes"}</script>',
    )
    result = ingest_html(source, temp_dir=tmp_path)
    assert source.page_keys == ["page-0001"]
    assert result.parsed.slides[0].notes_text == "Notes"


def test_shared_inline_dependencies_track_normalized_page_keys(tmp_path: Path) -> None:
    uri = "data:image/png;base64,aW1hZ2U="
    source = _source(
        tmp_path,
        f'<section data-slide="one"><img src="{uri}"></section>'
        f'<section data-slide="two"><img src="{uri}"></section>',
    )
    result = ingest_html(source, temp_dir=tmp_path)
    dependencies = json.loads(result.parsed.dependencies_json)
    assert len(dependencies) == 1
    assert dependencies[0]["page_keys"] == ["one", "two"]
    assert dependencies[0]["sha256"] == hashlib.sha256(b"image").hexdigest()
    assert uri not in json.dumps(result.canonical)
    assert uri in source.html


def test_generic_zip_dependencies_geometry_and_read_only_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "deck.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "deck/index.html",
            '<html><head><link rel="stylesheet" href="assets/main.css"></head><body>'
            '<section class="slide"><h1>ZIP</h1><img src="assets/a.png"></section></body></html>',
        )
        archive.writestr("deck/assets/main.css", ".slide {width:1024px;height:768px}")
        archive.writestr("deck/assets/a.png", b"image")
        archive.writestr("deck/private.txt", b"private")
    before = path.read_bytes()
    source = HtmlSource(path)
    assert source.page_sizes == {"page-0001": (1024, 768)}
    assert source.fingerprint == hashlib.sha256(before).hexdigest()
    assert source.read_resource("assets/a.png") == b"image"
    with pytest.raises(ValueError, match="声明"):
        source.read_resource("private.txt")
    result = ingest_html(source, temp_dir=tmp_path / "unused")
    assert len(json.loads(result.parsed.dependencies_json)) == 2
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("reference", ["../outside.png", "%2e%2e/outside.png", "/private/a.png"])
def test_generic_dependencies_cannot_escape(tmp_path: Path, reference: str) -> None:
    with pytest.raises(ValueError):
        _source(tmp_path, f'<section class="slide"><img src="{reference}"></section>')


def test_generic_dependency_and_package_limits_still_apply(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"image")
    source = _source(tmp_path, '<section data-slide><img src="a.png"></section>')
    with pytest.raises(ValueError, match="数量"):
        HtmlSource(source.path, max_parts=0)
    with pytest.raises(ValueError, match="体积"):
        HtmlSource(source.path, max_package_bytes=4)
    archive_path = tmp_path / "limited.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("index.html", source.html)
        archive.writestr("a.png", b"image")
    with pytest.raises(ValueError, match="成员数量"):
        HtmlSource(archive_path, max_parts=1)
    with pytest.raises(ValueError, match="解压体积"):
        HtmlSource(archive_path, max_package_bytes=4)


def test_generic_same_size_dependency_changes_are_detected(tmp_path: Path) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(b"first")
    source = _source(tmp_path, '<section data-slide><h1>Page</h1><img src="a.png"></section>')
    image.write_bytes(b"other")
    changed = HtmlSource(source.path)
    assert changed.entry_sha256 == source.entry_sha256
    assert changed.fingerprint != source.fingerprint
    with pytest.raises(ValueError, match="变化"):
        source.read_resource("a.png")
    image.unlink()
    (tmp_path / "target.png").write_bytes(b"other")
    image.symlink_to(tmp_path / "target.png")
    with pytest.raises(ValueError, match="符号链接"):
        HtmlSource(source.path)


def test_native_generic_scan_indexes_notes_and_writes_reference_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(
        tmp_path,
        '<section data-slide="overview"><h1>Overview</h1><p>Forecast</p></section>',
        '<script id="fs-deck-notes" type="application/json">{"overview":"Speakerkeyword"}</script>',
    )
    monkeypatch.setenv("PPTLIB_HTML_ENGINE", str(tmp_path / "nonexistent"))
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home"), "PPTLIB_ENABLE_HTML": "1"})
    initialize(settings)
    monkeypatch.setattr(html_assets, "render_html_version", lambda *_args, **_kwargs: 1)
    connection = connect(settings.database_path)
    try:
        report = scan_and_import(connection, [source.path], settings=settings)
        assert not report.failed
        imported = report.imported[0]
        hits = search_slides(connection, "Speakerkeyword")
        assert len(hits) == 1 and hits[0].page_key == "overview"
        row = connection.execute(
            "SELECT composition_ref_json FROM slides WHERE deck_version_id=?",
            (imported.version_id,),
        ).fetchone()
        assert json.loads(row[0])["source_version_id"] == imported.version_id
        manifest = settings.assets_dir / "html-manifests" / f"{imported.version_id}.json"
        canonical = json.loads(manifest.read_text())
        assert canonical["format"] == "pptlib-html-reference"
        assert canonical["pptlib"]["source_sha256"] == source.fingerprint
        assert scan_and_import(connection, [source.path], settings=settings).skipped == 1
    finally:
        connection.close()
