import base64
from pathlib import Path
from zipfile import ZipFile

import pytest

from pptlib.export.html import HtmlPage, build_html_artifact
from pptlib.html.adapter import ingest_html
from pptlib.html.preview import _Sanitizer
from pptlib.html.source import HtmlSource


@pytest.mark.parametrize("bundle", [False, True])
def test_export_can_be_archived_again_with_isolated_styles_and_notes(
    tmp_path: Path, bundle: bool,
) -> None:
    pages = []
    for index, (title, color) in enumerate([("Alpha", "red"), ("Beta", "blue")], 1):
        directory = tmp_path / title
        directory.mkdir()
        (directory / "theme.css").write_text(f".shared{{color:{color}}}")
        entry = directory / "index.html"
        entry.write_text(
            '<html><head><link rel="stylesheet" href="theme.css"></head><body>'
            f'<section class="slide"><h1 class="shared">{title}</h1></section></body></html>'
        )
        source = HtmlSource(entry)
        pages.append(HtmlPage(
            f"s{index}", f"v{index}", "render_deck_html", source.fingerprint, 1,
            source.page_keys[0], title, f"Notes for {title}", source,
        ))
    artifact = build_html_artifact(pages, offline_zip=bundle)
    target = tmp_path / ("roundtrip.zip" if bundle else "roundtrip.html")
    if bundle:
        with ZipFile(target, "w") as archive:
            archive.writestr("index.html", artifact.document)
            for name, content in artifact.resources.items():
                archive.writestr(name, content)
    else:
        target.write_bytes(artifact.document)
    imported = HtmlSource(target)
    parsed = ingest_html(imported, temp_dir=tmp_path / "tmp").parsed
    assert [s.title for s in parsed.slides] == ["Alpha", "Beta"]
    assert [s.notes_text for s in parsed.slides] == ["Notes for Alpha", "Notes for Beta"]
    assert imported.page_keys == ["page-0001", "page-0002"]
    first = _Sanitizer(imported, "/test/").document("page-0001", static=True).decode()
    second = _Sanitizer(imported, "/test/").document("page-0002", static=True).decode()
    assert "color:red" in first and "color:blue" not in first
    assert "color:blue" in second and "color:red" not in second


def test_large_embedded_media_is_not_silently_truncated(tmp_path: Path) -> None:
    # Valid tiny GIF plus trailing padding models a large binary data URI.
    gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
    content = gif + b"\0" * (8 * 1024 * 1024)
    uri = "data:image/gif;base64," + base64.b64encode(content).decode()
    entry = tmp_path / "large.html"
    entry.write_text(f'<section class="slide"><h1>Large media</h1><img src="{uri}"></section>')
    source = HtmlSource(entry)
    page = HtmlPage(
        "s1", "v1", "render_deck_html", source.fingerprint, 1, source.page_keys[0],
        "Large media", "", source,
    )
    artifact = build_html_artifact([page], offline_zip=False)
    output = tmp_path / "out.html"
    output.write_bytes(artifact.document)
    recovered = HtmlSource(output)
    image_uri = recovered.document.xpath("//img/@src")[0]
    assert base64.b64decode(image_uri.partition(",")[2], validate=True) == content
    assert len(ingest_html(recovered, temp_dir=tmp_path).parsed.slides) == 1
