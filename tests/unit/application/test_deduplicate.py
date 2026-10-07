from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageDraw

from pptlib.application.deduplicate import find_duplicate_slides
from pptlib.application.delete import delete_slides
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect


def _write_pptx(path: Path, text: str) -> None:
    presentation = (
        "<p:presentation xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' "
        "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
        "<p:sldIdLst><p:sldId id='256' r:id='rId1'/></p:sldIdLst></p:presentation>"
    )
    relationships = (
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        "<Relationship Id='rId1' "
        "Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide' "
        "Target='slides/slide1.xml'/></Relationships>"
    )
    slide = (
        "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' "
        "xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'>"
        "<p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id='2' name='Text'/></p:nvSpPr>"
        "<p:spPr><a:xfrm><a:off x='100' y='100'/><a:ext cx='800' cy='300'/>"
        f"</a:xfrm></p:spPr><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    with ZipFile(path, "w") as package:
        package.writestr("ppt/presentation.xml", presentation)
        package.writestr("ppt/_rels/presentation.xml.rels", relationships)
        package.writestr("ppt/slides/slide1.xml", slide)


def _write_preview(path: Path, *, variant: int) -> None:
    image = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((28, 30, 292, 62), fill=(42, 86, 190))
    draw.rectangle((28, 82, 210, 142), fill=(220, 225, 235))
    if variant:
        draw.rectangle((212, 118, 220, 126), fill=(220, 225, 235 + variant))
    image.save(path, "JPEG", quality=92)


def _seed_slide(
    settings,
    source: Path,
    *,
    deck_index: int,
    text: str,
    preview_variant: int,
) -> str:
    deck_id = f"deck_{deck_index}"
    version_id = f"ver_{deck_index}"
    slide_id = f"{version_id}_s00001"
    _write_pptx(source, text)
    now = datetime.now(UTC).isoformat()
    connection = connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO decks(id, canonical_path, display_name, current_version_id,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (deck_id, str(source), source.stem, version_id, now, now),
        )
        connection.execute(
            "INSERT INTO deck_versions(id, deck_id, sha256, size_bytes, mtime_ns,"
            " parser_version, slide_count, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'test', 1, 'parsed', ?, ?)",
            (
                version_id,
                deck_id,
                f"sha-{deck_index}",
                source.stat().st_size,
                source.stat().st_mtime_ns,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO slides(id, deck_version_id, slide_number, title, body_text,"
            " notes_text, content_text, created_at)"
            " VALUES (?, ?, 1, ?, '', '', ?, ?)",
            (slide_id, version_id, text, text, now),
        )
    finally:
        connection.close()
    previews = settings.assets_dir / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    _write_preview(previews / f"{slide_id}.jpg", variant=preview_variant)
    return slide_id


def test_duplicate_scan_separates_exact_near_and_different_text(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    exact_text = "这是一个完全相同的标准页面正文，用于跨文件重复检测"
    near_first = "本季度业务收入同比增长31%，主要来自重点客户"
    near_second = "本季度业务收入同比增长35%，主要来自重点客户"

    exact_a = _seed_slide(
        settings,
        tmp_path / "exact-a.pptx",
        deck_index=1,
        text=exact_text,
        preview_variant=0,
    )
    exact_b = _seed_slide(
        settings,
        tmp_path / "exact-b.pptx",
        deck_index=2,
        text=exact_text,
        preview_variant=0,
    )
    _seed_slide(
        settings,
        tmp_path / "near-a.pptx",
        deck_index=3,
        text=near_first,
        preview_variant=0,
    )
    _seed_slide(
        settings,
        tmp_path / "near-b.pptx",
        deck_index=4,
        text=near_second,
        preview_variant=1,
    )
    _seed_slide(
        settings,
        tmp_path / "template-a.pptx",
        deck_index=5,
        text="完全不同的产品战略与市场判断",
        preview_variant=0,
    )
    _seed_slide(
        settings,
        tmp_path / "template-b.pptx",
        deck_index=6,
        text="另一套毫不相关的财务预算与人员规划",
        preview_variant=0,
    )

    report = find_duplicate_slides(settings)

    exact_group = next(group for group in report.groups if group.kind == "exact")
    assert {member.slide_id for member in exact_group.members} == {exact_a, exact_b}
    assert sum(member.selected_by_default for member in exact_group.members) == 1
    assert any(group.kind == "similar" for group in report.groups)
    grouped_ids = {
        member.slide_id
        for group in report.groups
        for member in group.members
    }
    assert "ver_5_s00001" not in grouped_ids
    assert "ver_6_s00001" not in grouped_ids


def test_duplicate_scan_reuses_cached_fingerprints(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    _seed_slide(
        settings,
        tmp_path / "first.pptx",
        deck_index=1,
        text="缓存指纹测试页面正文内容",
        preview_variant=0,
    )

    first = find_duplicate_slides(settings)
    preview = settings.assets_dir / "previews" / "ver_1_s00001.jpg"
    preview.unlink()
    second = find_duplicate_slides(settings)

    assert first.analyzed_slides == 1
    assert second.analyzed_slides == 1


def test_delete_recommended_duplicate_then_rescan(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    text = "重复页面删除后重新扫描测试正文"
    sources = [tmp_path / "first.pptx", tmp_path / "second.pptx"]
    for index, source in enumerate(sources, start=1):
        _seed_slide(
            settings,
            source,
            deck_index=index,
            text=text,
            preview_variant=0,
        )

    first = find_duplicate_slides(settings)
    recommended = [
        member.slide_id
        for group in first.groups
        for member in group.members
        if member.selected_by_default
    ]
    assert len(recommended) == 1

    result = delete_slides(settings, recommended)
    second = find_duplicate_slides(settings)

    assert result.slides_removed == 1
    assert second.groups == ()
    assert all(source.is_file() for source in sources)
