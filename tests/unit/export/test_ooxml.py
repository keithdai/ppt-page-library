from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from pptlib.export import ExportError, SlideRef, export_slides, ooxml

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _xml(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _make_fixture(
    path: Path,
    *,
    slide_count: int = 1,
    size: tuple[int, int] = (12192000, 6858000),
    external: bool = False,
) -> None:
    parts: dict[str, bytes] = {}
    types = ET.Element(f"{{{CT}}}Types")
    ET.SubElement(
        types,
        f"{{{CT}}}Default",
        {
            "Extension": "rels",
            "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
        },
    )
    ET.SubElement(types, f"{{{CT}}}Default", {"Extension": "xml", "ContentType": "application/xml"})
    ET.SubElement(
        types,
        f"{{{CT}}}Override",
        {
            "PartName": "/ppt/presentation.xml",
            "ContentType": (
                "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
            ),
        },
    )
    ET.SubElement(
        types,
        f"{{{CT}}}Override",
        {
            "PartName": "/ppt/theme/theme1.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.theme+xml",
        },
    )
    ET.SubElement(
        types,
        f"{{{CT}}}Override",
        {
            "PartName": "/ppt/slideLayouts/slideLayout1.xml",
            "ContentType": (
                "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"
            ),
        },
    )
    for n in range(1, slide_count + 1):
        ET.SubElement(
            types,
            f"{{{CT}}}Override",
            {
                "PartName": f"/ppt/slides/slide{n}.xml",
                "ContentType": (
                    "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
                ),
            },
        )
    parts["[Content_Types].xml"] = _xml(types)
    root_rels = ET.Element(f"{{{REL}}}Relationships")
    ET.SubElement(
        root_rels,
        f"{{{REL}}}Relationship",
        {
            "Id": "rId1",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
            "Target": "ppt/presentation.xml",
        },
    )
    parts["_rels/.rels"] = _xml(root_rels)
    presentation = ET.Element(f"{{{P}}}presentation")
    ET.SubElement(presentation, f"{{{P}}}sldSz", {"cx": str(size[0]), "cy": str(size[1])})
    masters = ET.SubElement(presentation, f"{{{P}}}sldMasterIdLst")
    ET.SubElement(masters, f"{{{P}}}sldMasterId", {"id": "1", f"{{{R}}}id": "rId1"})
    slide_ids = ET.SubElement(presentation, f"{{{P}}}sldIdLst")
    for n in range(1, slide_count + 1):
        ET.SubElement(slide_ids, f"{{{P}}}sldId", {"id": str(255 + n), f"{{{R}}}id": f"rId{n + 1}"})
    parts["ppt/presentation.xml"] = _xml(presentation)
    presentation_rels = ET.Element(f"{{{REL}}}Relationships")
    ET.SubElement(
        presentation_rels,
        f"{{{REL}}}Relationship",
        {
            "Id": "rId1",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster",
            "Target": "slideMasters/slideMaster1.xml",
        },
    )
    for n in range(1, slide_count + 1):
        ET.SubElement(
            presentation_rels,
            f"{{{REL}}}Relationship",
            {
                "Id": f"rId{n + 1}",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
                "Target": f"slides/slide{n}.xml",
            },
        )
    parts["ppt/_rels/presentation.xml.rels"] = _xml(presentation_rels)
    parts["ppt/slideMasters/slideMaster1.xml"] = (
        b'<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
    )
    for n in range(1, slide_count + 1):
        slide = ET.Element(f"{{{P}}}sld")
        c_sld = ET.SubElement(slide, f"{{{P}}}cSld")
        tree = ET.SubElement(c_sld, f"{{{P}}}spTree")
        shape = ET.SubElement(tree, f"{{{P}}}sp")
        nv = ET.SubElement(shape, f"{{{P}}}nvSpPr")
        ET.SubElement(nv, f"{{{P}}}cNvPr", {"id": "1", "name": f"fixture-{n}"})
        parts[f"ppt/slides/slide{n}.xml"] = _xml(slide)
        if external or n == 1:
            rels = ET.Element(f"{{{REL}}}Relationships")
            ET.SubElement(
                rels,
                f"{{{REL}}}Relationship",
                {
                    "Id": "rId1",
                    "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout",
                    "Target": "../slideLayouts/slideLayout1.xml",
                },
            )
            if external:
                ET.SubElement(
                    rels,
                    f"{{{REL}}}Relationship",
                    {
                        "Id": "rId2",
                        "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                        "Target": "https://example.com",
                        "TargetMode": "External",
                    },
                )
            parts[f"ppt/slides/_rels/slide{n}.xml.rels"] = _xml(rels)
    parts["ppt/slideLayouts/slideLayout1.xml"] = (
        b'<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
    )
    parts["ppt/theme/theme1.xml"] = (
        b'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="fixture"/>'
    )
    layout_rels = ET.Element(f"{{{REL}}}Relationships")
    ET.SubElement(
        layout_rels,
        f"{{{REL}}}Relationship",
        {
            "Id": "rId1",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme",
            "Target": "../theme/theme1.xml",
        },
    )
    parts["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = _xml(layout_rels)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def test_export_single_source_order_and_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.pptx"
    _make_fixture(source, slide_count=2)
    output = tmp_path / "out.pptx"
    result = export_slides([SlideRef("fv_a", source, 2), SlideRef("fv_a", source, 1)], output)
    assert result.page_count == 2
    assert result.fidelity_level == "A"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert [item["source_page_number"] for item in manifest["items"]] == [2, 1]
    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("ppt/presentation.xml"))
        assert len(root.find(f"{{{P}}}sldIdLst")) == 2  # type: ignore[arg-type]


def test_export_rejects_changed_source_when_expected_hash_is_set(tmp_path: Path) -> None:
    source = tmp_path / "source.pptx"
    _make_fixture(source)
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_bytes(source.read_bytes() + b"changed")

    with pytest.raises(ExportError, match="源文件已发生变化") as error:
        export_slides([SlideRef("fv", source, 1, expected)], tmp_path / "out.pptx")
    assert error.value.code == "SOURCE_CHANGED"
    assert not (tmp_path / "out.pptx").exists()


def test_export_cross_source_rewrites_relationships_and_warns(tmp_path: Path) -> None:
    first = tmp_path / "first.pptx"
    second = tmp_path / "second.pptx"
    _make_fixture(first, external=False)
    _make_fixture(second, external=True)
    output = tmp_path / "merged.pptx"
    result = export_slides([SlideRef("fv1", first, 1), SlideRef("fv2", second, 1)], output)
    assert "EXTERNAL_LINK_PRESERVED" in result.warnings
    with zipfile.ZipFile(output) as archive:
        rels = ET.fromstring(archive.read("ppt/slides/_rels/slide2.xml.rels"))
        targets = [rel.get("Target") for rel in rels]
        assert any(target and target.startswith("../foreign1/") for target in targets)
        assert any(target == "https://example.com" for target in targets)
        assert "ppt/foreign1/slideLayouts/slideLayout1.xml" in archive.namelist()
        assert "ppt/foreign1/theme/theme1.xml" in archive.namelist()


def test_export_rejects_unsafe_zip_member_path(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.pptx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../outside.xml", b"not safe")
    with pytest.raises(ExportError, match="不安全") as error:
        export_slides([SlideRef("fv", source, 1)], tmp_path / "out.pptx")
    assert error.value.code == "INVALID_SOURCE_PACKAGE"


def test_export_rejects_zip_member_count_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "many-members.pptx"
    _make_fixture(source)
    monkeypatch.setattr(ooxml, "MAX_ZIP_MEMBERS", 1)
    with pytest.raises(ExportError, match="部件数量") as error:
        export_slides([SlideRef("fv", source, 1)], tmp_path / "out.pptx")
    assert error.value.code == "INVALID_SOURCE_PACKAGE"


def test_export_rejects_zip_uncompressed_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "large-members.pptx"
    _make_fixture(source)
    monkeypatch.setattr(ooxml, "MAX_ZIP_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(ExportError, match="解压后大小") as error:
        export_slides([SlideRef("fv", source, 1)], tmp_path / "out.pptx")
    assert error.value.code == "INVALID_SOURCE_PACKAGE"


def test_export_rejects_incompatible_slide_size(tmp_path: Path) -> None:
    first = tmp_path / "first.pptx"
    second = tmp_path / "second.pptx"
    _make_fixture(first)
    _make_fixture(second, size=(100, 100))
    with pytest.raises(ExportError, match="尺寸") as error:
        export_slides([SlideRef("fv1", first, 1), SlideRef("fv2", second, 1)], tmp_path / "x.pptx")
    assert error.value.code == "INCOMPATIBLE_SLIDE_SIZE"


def test_export_rejects_empty_selection(tmp_path: Path) -> None:
    with pytest.raises(ExportError) as error:
        export_slides([], tmp_path / "x.pptx")
    assert error.value.code == "EMPTY_SELECTION"
