# ruff: noqa: E501
from pathlib import Path
from zipfile import ZipFile

from pptlib.application.import_decks import scan_and_import
from pptlib.application.search_slides import find_slides
from pptlib.discovery.scanner import ScannedFile
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.migrations import migrate
from pptlib.infrastructure.db.repositories import DeckRepository
from pptlib.ingestion.parser import ParsedDeck, ParsedSlide


def _write_fixture(path: Path) -> None:
    presentation = """<p:presentation xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'><p:sldIdLst><p:sldId id='1' r:id='rId1'/></p:sldIdLst></p:presentation>"""
    rels = """<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'><Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide' Target='slides/slide1.xml'/></Relationships>"""
    slide = """<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type='title'/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Annual Review</a:t></a:r></a:p></p:txBody></p:sp><p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody><a:p><a:r><a:t>收入增长与利润</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"""
    with ZipFile(path, "w") as package:
        package.writestr("ppt/presentation.xml", presentation)
        package.writestr("ppt/_rels/presentation.xml.rels", rels)
        package.writestr("ppt/slides/slide1.xml", slide)


def test_scan_import_is_idempotent_and_supports_latin_and_cjk_search(tmp_path: Path) -> None:
    deck = tmp_path / "annual.pptx"
    _write_fixture(deck)
    connection = connect(tmp_path / "pages.db")
    migrate(connection, Path(__file__).resolve().parents[3] / "src" / "pptlib" / "migrations")

    first = scan_and_import(connection, [tmp_path])
    second = scan_and_import(connection, [tmp_path])
    assert first.discovered == 1
    assert len(first.imported) == 1 and first.imported[0].created
    assert second.imported[0].created is False
    assert len(find_slides(connection, "Annual")) == 1
    cjk = find_slides(connection, "利润")
    assert len(cjk) == 1 and cjk[0].title == "Annual Review"
    taxonomy = connection.execute(
        "SELECT topic, page_type FROM slide_taxonomy WHERE slide_id = ?",
        (f"{first.imported[0].version_id}_s00001",),
    ).fetchone()
    assert taxonomy is not None
    assert tuple(taxonomy) == ("数据与经营", "数据图表")


def test_import_propagates_short_title_only_section_context(tmp_path: Path) -> None:
    connection = connect(tmp_path / "pages.db")
    migrate(connection, Path(__file__).resolve().parents[3] / "src" / "pptlib" / "migrations")
    scanned = ScannedFile(tmp_path / "org.pptx", "section-test", 1, 1)
    parsed = ParsedDeck(
        (
            ParsedSlide(1, "绩效与激励", "", ""),
            ParsedSlide(2, "阶段一", "达成情况", ""),
        )
    )

    imported = DeckRepository(connection).import_parsed(scanned, parsed)
    row = connection.execute(
        "SELECT subtopic, classification_source FROM slide_taxonomy "
        "WHERE slide_id = ?",
        (f"{imported.version_id}_s00002",),
    ).fetchone()
    assert row is not None
    assert row[0] == "绩效与激励"
    assert row[1] == "auto"
