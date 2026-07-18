# ruff: noqa: E501
from pathlib import Path
from zipfile import ZipFile

from pptlib.ingestion.parser import parse_pptx


def _write_fixture(path: Path) -> None:
    presentation = """<p:presentation xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'><p:sldIdLst><p:sldId id='1' r:id='rId1'/><p:sldId id='2' r:id='rId2'/></p:sldIdLst></p:presentation>"""
    presentation_rels = """<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'><Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide' Target='slides/slide1.xml'/><Relationship Id='rId2' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide' Target='slides/slide2.xml'/></Relationships>"""
    def slide(title: str, body: str) -> str:
        return f"""<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type='title'/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp><p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody><a:p><a:r><a:t>{body}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"""
    with ZipFile(path, "w") as package:
        package.writestr("ppt/presentation.xml", presentation)
        package.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        package.writestr("ppt/slides/slide1.xml", slide("季度复盘", "收入增长"))
        package.writestr("ppt/slides/slide2.xml", slide("Roadmap", "Launch plan"))


def test_parse_pptx_extracts_ordered_title_and_body(tmp_path: Path) -> None:
    path = tmp_path / "deck.pptx"
    _write_fixture(path)
    parsed = parse_pptx(path)
    assert len(parsed.slides) == 2
    assert parsed.slides[0].title == "季度复盘"
    assert parsed.slides[0].body_text == "收入增长"
    assert parsed.slides[1].title == "Roadmap"


def test_parse_pptx_rejects_non_zip(tmp_path: Path) -> None:
    path = tmp_path / "bad.pptx"
    path.write_bytes(b"not a zip")
    try:
        parse_pptx(path)
    except ValueError as error:
        assert "valid PPTX" in str(error)
    else:
        raise AssertionError("expected invalid package failure")
