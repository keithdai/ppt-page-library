from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from pptlib.application import import_decks
from pptlib.application.import_decks import scan_and_import
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect

_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _write_pptx(path: Path, title: str) -> None:
    presentation = (
        f"<p:presentation xmlns:p='{_NS_P}' xmlns:r='{_NS_R}'>"
        "<p:sldIdLst><p:sldId id='1' r:id='rId1'/></p:sldIdLst></p:presentation>"
    )
    rels = (
        f"<Relationships xmlns='{_NS_REL}'><Relationship Id='rId1' "
        f"Type='{_NS_R}/slide' Target='slides/slide1.xml'/></Relationships>"
    )
    slide = (
        f"<p:sld xmlns:p='{_NS_P}' xmlns:a='{_NS_A}'><p:cSld><p:spTree><p:sp>"
        "<p:nvSpPr><p:nvPr><p:ph type='title'/></p:nvPr></p:nvSpPr>"
        f"<p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody>"
        "</p:sp></p:spTree></p:cSld></p:sld>"
    )
    with ZipFile(path, "w") as package:
        package.writestr("ppt/presentation.xml", presentation)
        package.writestr("ppt/_rels/presentation.xml.rels", rels)
        package.writestr("ppt/slides/slide1.xml", slide)


def _stub_render(monkeypatch) -> None:
    """Render one thumbnail page per deck without invoking officecli."""

    def fake_render(source_path, version_id, slide_count, *, on_page=None, **_kwargs):
        for page in range(1, slide_count + 1):
            if on_page is not None:
                on_page(page, slide_count)
        return slide_count

    monkeypatch.setattr(import_decks, "render_deck_thumbnails", fake_render)


def test_folder_scan_imports_all_and_emits_progress(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _write_pptx(src / "a.pptx", "文件甲")
    _write_pptx(src / "b.pptx", "文件乙")
    _stub_render(monkeypatch)

    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    events: list[dict] = []
    connection = connect(settings.database_path)
    try:
        # A single directory root must be scanned recursively into both files.
        report = scan_and_import(
            connection, [src], settings=settings, on_progress=events.append
        )
    finally:
        connection.close()

    assert report.discovered == 2
    assert len(report.imported) == 2
    assert all(deck.created for deck in report.imported)

    stages = [e["stage"] for e in events]
    assert stages[0] == "scan"
    assert events[0]["total"] == 2
    assert "render" in stages  # per-page progress fired
    render_events = [e for e in events if e["stage"] == "render"]
    assert render_events and render_events[0]["page"] == 1
    file_done = [e for e in events if e["stage"] == "file_done"]
    assert len(file_done) == 2
    assert all(e["created"] is True for e in file_done)


def test_reimport_marks_duplicates_skipped(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _write_pptx(src / "a.pptx", "文件甲")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)

    connection = connect(settings.database_path)
    try:
        first = scan_and_import(connection, [src], settings=settings)
        events: list[dict] = []
        second = scan_and_import(
            connection, [src], settings=settings, on_progress=events.append
        )
    finally:
        connection.close()

    assert first.imported[0].created is True
    # Re-importing the unchanged file is detected as a duplicate.
    assert second.imported[0].created is False
    assert second.skipped == 1
    done = [e for e in events if e["stage"] == "file_done"]
    assert done and done[0]["created"] is False
