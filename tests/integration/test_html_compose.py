from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import html as lh
from PIL import Image

from pptlib.application.compose import (
    compose_from_manifest,
    compose_from_slide_ids,
    preflight_compose_from_manifest,
)
from pptlib.config import Settings, load_settings
from pptlib.discovery.scanner import ScannedFile
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.html.source import HtmlSource
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.migrations import migrate
from pptlib.infrastructure.db.repositories import DeckRepository
from pptlib.ingestion.parser import ParsedDeck, ParsedSlide

MIGRATIONS = Path(__file__).resolve().parents[2] / "src/pptlib/migrations"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "library"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "cache"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "output"),
            "PPTLIB_ENABLE_HTML": "1",
        }
    )
    connection = connect(settings.database_path)
    try:
        migrate(connection, MIGRATIONS)
    finally:
        connection.close()
    return settings


def _html(path: Path, *, color: str = "red", hostile: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "styles").mkdir(exist_ok=True)
    (path.parent / "media").mkdir(exist_ok=True)
    (path.parent / "styles" / "common.css").write_text(
        '@import "fonts.css"; .shared {color:' + color + ";}"
        '.slide {background:url("../media/shape.svg")} '
        ".motion{animation:spin 2s infinite}@keyframes spin{to{opacity:.5}}"
    )
    (path.parent / "styles" / "fonts.css").write_text(
        '@font-face{font-family:Test;src:url("../media/test.woff2") format("woff2")}'
    )
    (path.parent / "media" / "test.woff2").write_bytes(b"wOF2-font-fixture")
    (path.parent / "media" / "nested.png").write_bytes(b"PNG nested dependency")
    (path.parent / "media" / "shape.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="nested.png"/>'
        '<script>SVG_ATTACK()</script><rect width="2" height="2"/></svg>'
    )
    (path.parent / "media" / "motion.gif").write_bytes(
        base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
    )
    (path.parent / "media" / "movie.mp4").write_bytes(b"\0\0\0\x18ftypmp42-native-video")
    attack = (
        '<script>window.TOP_ATTACK = "</iframe><script>BREAK_OUT()</script>";</script>'
        '<style>.x::after{content:"\\3c /style>\\3c script>STYLE_ATTACK()";}</style>'
        '<img src="https://example.test/private?token=DO_NOT_LEAK" onerror="EVENT_ATTACK()">'
        '<a href="javascript:URL_ATTACK()">unsafe link</a>'
        '<iframe srcdoc="&lt;script&gt;FRAME_ATTACK()&lt;/script&gt;"></iframe>'
        "<svg><script>INLINE_SVG_ATTACK()</script><foreignObject>blocked</foreignObject></svg>"
        if hostile
        else ""
    )
    path.write_text(
        '<!doctype html><html><head><meta name="fs-deck-generator" content="render-deck">'
        '<link rel="stylesheet" href="styles/common.css"></head><body class="deck-body">'
        '<div class="deck">'
        '<section class="slide-frame"><div class="slide shared" data-slide-key="first">'
        "<h1>First " + color + '</h1><img src="media/motion.gif">'
        '<video src="media/movie.mp4" autoplay></video>' + attack + "</div></section>"
        '<section class="slide-frame"><div class="slide shared" data-slide-key="second">'
        "<h1>Second " + color + "</h1></div></section></div></body></html>"
    )
    return path


def _import(settings: Settings, path: Path, *, pptx: bool = False) -> list[str]:
    source = None if pptx else HtmlSource(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if pptx else source.fingerprint
    keys = [""] if pptx else source.page_keys
    parsed = ParsedDeck(
        tuple(
            ParsedSlide(
                number,
                f"Title {key}",
                "body",
                f"Notes {key} </script><script>NOTES_ATTACK()</script>",
                page_key=key,
                page_kind="ooxml" if pptx else "h5_raw",
            )
            for number, key in enumerate(keys, 1)
        ),
        source_format="pptx" if pptx else "render_deck_html",
        dependencies_json=json.dumps(
            []
            if pptx
            else [
                {"kind": "local", "reference": ref, **metadata}
                for ref, metadata in source.resources.items()
            ]
        ),
    )
    connection = connect(settings.database_path)
    try:
        imported = DeckRepository(connection).import_parsed(
            ScannedFile(path, digest, path.stat().st_size, path.stat().st_mtime_ns),
            parsed,
        )
    finally:
        connection.close()
    ids = [f"{imported.version_id}_s{number:05d}" for number in range(1, len(keys) + 1)]
    if pptx:
        preview = settings.assets_dir / "previews" / f"{ids[0]}.jpg"
        preview.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1920, 1080), "navy").save(preview, format="JPEG")
    return ids


def _selection(tmp_path: Path, slide_ids: list[str]) -> Path:
    path = tmp_path / "selection.json"
    path.write_text(json.dumps({"slide_ids": slide_ids}))
    return path


def _read(output: Path):
    if output.suffix.lower() == ".zip":
        with ZipFile(output) as archive:
            raw = archive.read("index.html")
            files = {name: archive.read(name) for name in archive.namelist()}
    else:
        raw = output.read_bytes()
        files = {}
    document = lh.document_fromstring(raw)
    frames = document.xpath("//iframe")
    pages = [lh.document_fromstring(frame.get("srcdoc")) for frame in frames]
    manifest = json.loads(document.get_element_by_id("source-manifest").text)
    return document, pages, manifest, files


@pytest.mark.parametrize("extension", [".html", ".zip", ".HTML", ".ZIP"])
def test_order_duplicates_shared_css_notes_and_native_media(settings, tmp_path, extension):
    first = _import(settings, _html(tmp_path / "red" / "index.html"))
    second = _import(settings, _html(tmp_path / "blue" / "index.html", color="blue"))
    ids = [second[1], first[0], second[0], first[0]]
    selection = _selection(tmp_path, ids)
    output = tmp_path / ("deck" + extension)
    preflight = preflight_compose_from_manifest(settings, selection, output)
    assert preflight.ok, preflight.blockers
    assert preflight.page_count == 4
    assert preflight.source_count == 2
    assert preflight.estimated_fidelity == "sanitized-html"
    assert preflight.estimated_output_bytes > 1000
    assert not output.exists()
    result = compose_from_manifest(
        settings,
        selection,
        output,
        preflight_token=preflight.preflight_token,
    )
    assert result.page_count == 4
    outer, pages, manifest, files = _read(output)
    assert [page.xpath("//h1")[0].text for page in pages] == [
        "Second blue",
        "First red",
        "First blue",
        "First red",
    ]
    assert len(outer.xpath("//script")) == 1
    assert [item["slide_id"] for item in manifest["items"]] == ids
    assert json.loads(result.manifest_path.read_text()) == manifest
    assert all("source_path" not in item for item in manifest["items"])
    assert all("Notes " in item["notes"] for item in manifest["items"])
    assert all("allow-scripts" not in frame.get("sandbox") for frame in outer.xpath("//iframe"))
    assert all(not page.xpath("//script") for page in pages)
    assert all(len(page.xpath("//*[@data-slide-key]")) == 1 for page in pages)
    red_css = pages[1].xpath("//link")[0].get("href")
    blue_css = pages[0].xpath("//link")[0].get("href")
    assert b"color:red" in base64.b64decode(red_css.split(",", 1)[1])
    assert b"color:blue" in base64.b64decode(blue_css.split(",", 1)[1])
    assert not pages[1].xpath("//video")[0].get("autoplay")
    assert "controls" in pages[1].xpath("//video")[0].attrib
    for tag, mime in (("img", "image/gif"), ("video", "video/mp4")):
        value = pages[1].xpath(f"//{tag}")[0].get("src")
        if extension.lower() == ".zip":
            assert value in files
        else:
            assert value.startswith(f"data:{mime};base64,")
    if files:
        assert "index.html" in files and "manifest.json" in files
        assert all(not name.startswith(("/", "../")) for name in files)
        assert not any("common.css" in name or "index.html/" in name for name in files)


@pytest.mark.parametrize("extension", [".html", ".zip"])
def test_dependency_closure_svg_fonts_and_sanitization(settings, tmp_path, extension):
    ids = _import(settings, _html(tmp_path / "source" / "index.html", hostile=True))
    output = tmp_path / ("deck" + extension)
    result = compose_from_slide_ids(settings, ids[:1], output)
    outer, pages, manifest, files = _read(output)
    assert "HTML_UNSAFE_OR_UNSUPPORTED_CONTENT_BLOCKED" in result.warnings
    assert "HTML_EXTERNAL_RESOURCES_BLOCKED" in result.warnings
    assert "TOP_ATTACK" not in output.read_bytes().decode("utf-8", errors="ignore")
    assert "DO_NOT_LEAK" not in output.read_bytes().decode("utf-8", errors="ignore")
    page = pages[0]
    assert not page.xpath("//script|//iframe|//foreignObject")
    assert not page.xpath("//*[@onerror]")
    assert not page.xpath("//a[@href]")
    assert "NOTES_ATTACK" in manifest["items"][0]["notes"]
    assert len(outer.xpath("//script")) == 1
    css = base64.b64decode(page.xpath("//link")[0].get("href").split(",", 1)[1])
    assert b"styles/" not in css and b"../" not in css
    nested = re.findall(rb'data:([^;"]+);base64,([A-Za-z0-9+/=]+)', css)
    decoded = {mime: base64.b64decode(payload) for mime, payload in nested}
    assert b"text/css" in decoded
    assert b"data:font/woff2;base64," in decoded[b"text/css"]
    assert b"image/svg+xml" in decoded
    assert b"SVG_ATTACK" not in decoded[b"image/svg+xml"]
    assert b"data:image/png;base64," in decoded[b"image/svg+xml"]
    assert b"nested.png" not in decoded[b"image/svg+xml"]
    assert not any(b"DO_NOT_LEAK" in value for value in files.values())


def test_mixed_pptx_uses_high_resolution_preview_with_flattening_warning(settings, tmp_path):
    html_ids = _import(settings, _html(tmp_path / "source" / "index.html"))
    pptx = tmp_path / "source.pptx"
    pptx.write_bytes(b"PPTX source already indexed")
    pptx_ids = _import(settings, pptx, pptx=True)
    ids = [html_ids[1], pptx_ids[0], html_ids[0], pptx_ids[0]]
    selection = _selection(tmp_path, ids)
    output = tmp_path / "mixed.zip"
    preflight = preflight_compose_from_manifest(settings, selection, output)
    assert preflight.ok, preflight.blockers
    assert preflight.estimated_fidelity == "mixed-html-and-images"
    assert any("PPTX_FLATTENED_TO_IMAGE" in warning for warning in preflight.warnings)
    result = compose_from_manifest(
        settings, selection, output, preflight_token=preflight.preflight_token
    )
    _, pages, manifest, files = _read(output)
    assert [item["slide_id"] for item in manifest["items"]] == ids
    image = pages[1].xpath("//img")[0].get("src")
    assert files[image].startswith(b"\xff\xd8")
    assert result.warnings == preflight.warnings
    assert manifest["items"][1]["fidelity"] == "flattened-image"


@pytest.mark.parametrize("verify", [True, False])
@pytest.mark.parametrize(
    "mutation", ["html", "css", "svg-child", "output", "sidecar", "selection", "notes"]
)
def test_preflight_token_rejects_mutation(settings, tmp_path, verify, mutation):
    path = _html(tmp_path / "source" / "index.html")
    ids = _import(settings, path)
    selection = _selection(tmp_path, ids)
    output = tmp_path / "out.html"
    preflight = preflight_compose_from_manifest(
        settings,
        selection,
        output,
        verify_source_hash=verify,
    )
    assert preflight.ok, preflight.blockers
    if mutation == "notes":
        connection = connect(settings.database_path)
        try:
            connection.execute("UPDATE slides SET notes_text='changed' WHERE id=?", (ids[0],))
            connection.commit()
        finally:
            connection.close()
    else:
        target = {
            "html": path,
            "css": path.parent / "styles" / "common.css",
            "svg-child": path.parent / "media" / "nested.png",
            "output": output,
            "sidecar": output.with_suffix(".manifest.json"),
            "selection": selection,
        }[mutation]
        if mutation == "selection":
            target.write_text(json.dumps(ids[::-1]))
        else:
            target.write_bytes((target.read_bytes() if target.exists() else b"") + b" changed")
    with pytest.raises(AppError):
        compose_from_manifest(
            settings,
            selection,
            output,
            preflight_token=preflight.preflight_token,
            verify_source_hash=verify,
        )
    assert output.exists() == (mutation == "output")
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    "target_kind",
    [
        "source",
        "unselected-source",
        "resource",
        "unselected-resource",
        "database",
        "database-hardlink",
        "source-hardlink",
        "resource-hardlink",
        "symlink",
        "selection",
    ],
)
def test_output_protects_sources_resources_db_and_manifest(settings, tmp_path, target_kind):
    path = _html(tmp_path / "source" / "index.html")
    ids = _import(settings, path)
    other = _html(tmp_path / "other" / "index.html")
    _import(settings, other)
    selection = _selection(tmp_path, ids)
    protected = {
        "source": path,
        "unselected-source": other,
        "resource": path.parent / "styles" / "common.css",
        "unselected-resource": other.parent / "styles" / "common.css",
        "database": settings.database_path,
        "database-hardlink": settings.database_path,
        "source-hardlink": path,
        "resource-hardlink": other.parent / "styles" / "common.css",
        "symlink": path,
        "selection": selection,
    }[target_kind]
    before = protected.read_bytes()
    if "hardlink" in target_kind:
        output = tmp_path / "alias.html"
        output.hardlink_to(protected)
        kwargs = {}
    elif target_kind == "symlink":
        output = tmp_path / "alias.html"
        output.symlink_to(protected)
        kwargs = {}
    else:
        # Test both output and arbitrary companion-manifest protections.
        output = protected if protected.suffix == ".html" else tmp_path / "out.html"
        kwargs = {} if output == protected else {"manifest_path": protected}
    with pytest.raises(AppError):
        _export_protected(settings, ids, output, selection, kwargs, target_kind)
    assert protected.read_bytes() == before


def _export_protected(settings, ids, output, selection, kwargs, target_kind):
    if target_kind == "selection":
        from pptlib.application.html_compose import compose_html_from_slide_ids

        return compose_html_from_slide_ids(
            settings,
            ids,
            output,
            manifest_path=selection,
            input_manifest=selection,
        )
    return compose_from_slide_ids(settings, ids, output, **kwargs)


@pytest.mark.parametrize("existing", [True, False])
def test_second_publish_failure_rolls_back_both_destinations(
    settings, tmp_path, monkeypatch, existing
):
    ids = _import(settings, _html(tmp_path / "source" / "index.html"))
    output = tmp_path / "deck.html"
    manifest = output.with_suffix(".manifest.json")
    if existing:
        output.write_bytes(b"previous deck")
        manifest.write_bytes(b"previous manifest")
    original = Path.replace

    def fail_manifest(self, target):
        if Path(target) == manifest and self.suffix == ".tmp":
            raise OSError("injected failure")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail_manifest)
    with pytest.raises(AppError):
        compose_from_slide_ids(settings, ids, output)
    assert output.exists() == existing
    assert manifest.exists() == existing
    if existing:
        assert output.read_bytes() == b"previous deck"
        assert manifest.read_bytes() == b"previous manifest"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.backup"))


def test_source_changes_during_build_leave_existing_output_untouched(
    settings, tmp_path, monkeypatch
):
    from pptlib.application import html_compose

    path = _html(tmp_path / "source" / "index.html")
    ids = _import(settings, path)
    output = tmp_path / "out.html"
    output.write_bytes(b"old output")
    original = html_compose.build_html_artifact

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text(path.read_text() + "<!-- changed -->")
        return result

    monkeypatch.setattr(html_compose, "build_html_artifact", mutate)
    with pytest.raises(AppError) as error:
        compose_from_slide_ids(settings, ids, output, verify_source_hash=False)
    assert error.value.code == ErrorCode.SOURCE_CHANGED
    assert output.read_bytes() == b"old output"
    assert not output.with_suffix(".manifest.json").exists()


def test_size_limit_blocks_preflight_and_execution(settings, tmp_path, monkeypatch):
    from pptlib.application import html_compose

    ids = _import(settings, _html(tmp_path / "source" / "index.html"))
    selection = _selection(tmp_path, ids)
    output = tmp_path / "out.zip"
    monkeypatch.setattr(html_compose, "MAX_OUTPUT_BYTES", 2000)
    preflight = preflight_compose_from_manifest(settings, selection, output)
    assert not preflight.ok and preflight.preflight_token is None
    with pytest.raises(AppError):
        compose_from_manifest(settings, selection, output)
    assert not output.exists()


def test_source_origin_does_not_disclose_bearer_urls(settings, tmp_path):
    ids = _import(settings, _html(tmp_path / "source" / "index.html"))
    version = ids[0].rsplit("_s", 1)[0]
    metadata = settings.assets_dir / "html-manifests" / f"{version}.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(
        json.dumps(
            {
                "pptlib": {
                    "source_url": "https://user:SECRET@example.test/private/BEARER_TOKEN?token=SECRET#SECRET"
                }
            }
        )
    )
    output = tmp_path / "out.html"
    compose_from_slide_ids(settings, ids, output)
    manifest = _read(output)[2]
    assert manifest["items"][0]["source_url"] == "https://example.test/"
    assert "SECRET" not in output.read_text()
    assert "BEARER_TOKEN" not in output.read_text()


def test_html_to_pptx_remains_rejected(settings, tmp_path):
    ids = _import(settings, _html(tmp_path / "source" / "index.html"))
    with pytest.raises(AppError) as error:
        compose_from_slide_ids(settings, ids, tmp_path / "out.pptx")
    assert error.value.code == ErrorCode.REQUEST_INVALID
    assert not (tmp_path / "out.pptx").exists()


def test_nested_zip_source_is_read_in_place_with_offline_dependencies(settings, tmp_path):
    source = _html(tmp_path / "source" / "nested" / "index.html")
    package = tmp_path / "source.zip"
    with ZipFile(package, "w") as archive:
        for path in source.parent.rglob("*"):
            if path.is_file():
                archive.write(path, "nested/" + path.relative_to(source.parent).as_posix())
    ids = _import(settings, package)
    before = package.read_bytes()
    output = tmp_path / "result.zip"
    compose_from_slide_ids(settings, ids[::-1], output)
    _, pages, manifest, files = _read(output)
    assert pages[0].xpath("//h1")[0].text == "Second red"
    assert len(manifest["items"]) == 2
    assert files[pages[1].xpath("//video")[0].get("src")].endswith(b"native-video")
    assert package.read_bytes() == before
    assert not (tmp_path / "nested").exists()


def test_generic_page_dimensions_are_retained(settings, tmp_path):
    source = tmp_path / "generic.html"
    source.write_text(
        "<!doctype html><html><head><style>.slide{width:960px;height:720px}</style>"
        '</head><body><section class="slide"><h1>Portrait-ish</h1></section></body></html>'
    )
    ids = _import(settings, source)
    output = tmp_path / "result.html"
    compose_from_slide_ids(settings, ids, output)
    outer, pages, _, _ = _read(output)
    frame = outer.xpath("//iframe")[0]
    assert frame.get("data-width") == "960"
    assert frame.get("data-height") == "720"
    assert "width:960px!important" in pages[0].xpath("//style")[-1].text


@pytest.mark.parametrize("mutation", ["source", "selection", "output", "sidecar", "new-source"])
def test_rechecks_after_staging_before_publish(settings, tmp_path, monkeypatch, mutation):
    from pptlib.export import html as exporter

    path = _html(tmp_path / "source" / "index.html")
    ids = _import(settings, path)
    selection = _selection(tmp_path, ids)
    output = tmp_path / "result.html"
    manifest = output.with_suffix(".manifest.json")
    preflight = preflight_compose_from_manifest(settings, selection, output)
    assert preflight.ok
    original = exporter._stage
    calls = 0

    def stage_and_mutate(*args, **kwargs):
        nonlocal calls
        staging = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            if mutation == "new-source":
                _html(output)
                _import(settings, output)
            else:
                target = {
                    "source": path,
                    "selection": selection,
                    "output": output,
                    "sidecar": manifest,
                }[mutation]
                target.write_bytes(b"modified during staging")
        return staging

    monkeypatch.setattr(exporter, "_stage", stage_and_mutate)
    with pytest.raises(AppError):
        compose_from_manifest(
            settings, selection, output, preflight_token=preflight.preflight_token
        )
    if mutation == "output":
        assert output.read_bytes() == b"modified during staging"
    elif mutation != "new-source":
        assert not output.exists()
    if mutation == "sidecar":
        assert manifest.read_bytes() == b"modified during staging"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.backup"))


@pytest.mark.parametrize(
    "change", ["missing", "corrupt", "low-resolution", "changed-after-preflight"]
)
def test_pptx_preview_must_be_available_high_resolution_and_unchanged(settings, tmp_path, change):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"indexed PPTX")
    ids = _import(settings, source, pptx=True)
    preview = settings.assets_dir / "previews" / f"{ids[0]}.jpg"
    output = tmp_path / "result.html"
    selection = _selection(tmp_path, ids)
    original_preflight = preflight_compose_from_manifest(settings, selection, output)
    assert original_preflight.ok
    if change == "missing":
        preview.unlink()
    elif change == "corrupt":
        preview.write_bytes(b"not a JPEG")
    else:
        size = (640, 360) if change == "low-resolution" else (1920, 1080)
        Image.new("RGB", size, "red").save(preview, format="JPEG")
    if change != "changed-after-preflight":
        assert not preflight_compose_from_manifest(settings, selection, output).ok
    with pytest.raises(AppError):
        compose_from_manifest(
            settings,
            selection,
            output,
            preflight_token=original_preflight.preflight_token,
        )
    assert not output.exists()


def test_archive_metadata_origin_is_in_manifest_without_bearer_paths(settings, tmp_path):
    path = _html(settings.assets_dir / "html-archives" / "capture" / "index.html")
    ids = _import(settings, path)
    (path.parent / "archive.json").write_text(
        json.dumps(
            {
                "source_url": "https://example.test/capability/SECRET?token=SECRET",
            }
        )
    )
    output = tmp_path / "out.html"
    selection = _selection(tmp_path, ids)
    preflight = preflight_compose_from_manifest(settings, selection, output)
    assert preflight.ok, preflight.blockers
    compose_from_manifest(settings, selection, output, preflight_token=preflight.preflight_token)
    assert _read(output)[2]["items"][0]["source_url"] == "https://example.test/"
    assert "SECRET" not in output.read_text()


def test_companion_alias_and_directory_are_blocked_in_preflight(settings, tmp_path):
    ids = _import(settings, _html(tmp_path / "source" / "index.html"))
    selection = _selection(tmp_path, ids)
    output = tmp_path / "out.html"
    output.mkdir()
    preflight = preflight_compose_from_manifest(settings, selection, output)
    assert not preflight.ok and preflight.preflight_token is None
    with pytest.raises(AppError):
        compose_from_slide_ids(
            settings, ids, tmp_path / "other.html", manifest_path=tmp_path / "other.html"
        )


def test_cyclic_css_imports_are_bounded_and_reported(settings, tmp_path):
    path = _html(tmp_path / "source" / "index.html")
    css = path.parent / "styles" / "fonts.css"
    css.write_text(css.read_text() + '@import "common.css";')
    ids = _import(settings, path)
    output = tmp_path / "out.html"
    result = compose_from_slide_ids(settings, ids, output)
    assert "HTML_CYCLIC_RESOURCE_BLOCKED" in result.warnings
    assert output.stat().st_size < 100_000
