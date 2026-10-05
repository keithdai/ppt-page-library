from __future__ import annotations

import io
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from pptlib.rendering import thumbnails
from pptlib.rendering.thumbnails import ThumbnailError, render_deck_thumbnails


def _make_pptx(path: Path) -> Path:
    # A minimal valid package is enough; renderers are stubbed in these tests.
    with ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
    return path


def _make_visibility_pptx(path: Path) -> Path:
    with ZipFile(path, "w") as package:
        package.writestr(
            "ppt/presentation.xml",
            (
                '<p:presentation xmlns:p="http://schemas.openxmlformats.org/'
                'presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships"><p:sldIdLst>'
                '<p:sldId id="1" r:id="rId1"/><p:sldId id="2" r:id="rId2"/>'
                '<p:sldId id="3" r:id="rId3"/></p:sldIdLst></p:presentation>'
            ),
        )
        package.writestr(
            "ppt/_rels/presentation.xml.rels",
            (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships"><Relationship Id="rId1" Type="x/slide" '
                'Target="slides/slide1.xml"/><Relationship Id="rId2" Type="x/slide" '
                'Target="slides/slide2.xml"/><Relationship Id="rId3" Type="x/slide" '
                'Target="slides/slide3.xml"/></Relationships>'
            ),
        )
        package.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        )
        package.writestr(
            "ppt/slides/slide2.xml",
            (
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
                'presentationml/2006/main" show="0"/>'
            ),
        )
        package.writestr(
            "ppt/slides/slide3.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        )
    return path


def test_backend_order_prefers_libreoffice_with_isolated_officecli_fallback() -> None:
    assert thumbnails._backend_order("auto", "/usr/bin/officecli") == [
        "libreoffice",
        "officecli",
    ]
    assert thumbnails._backend_order("auto", None) == ["libreoffice"]
    assert thumbnails._backend_order("officecli", None) == ["officecli"]
    assert thumbnails._backend_order("libreoffice", "/usr/bin/officecli") == ["libreoffice"]


def test_external_media_relationship_is_rejected_before_rendering(
    tmp_path: Path,
) -> None:
    source = tmp_path / "external-media.pptx"
    with ZipFile(source, "w") as package:
        package.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships"><Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/image" Target="http://127.0.0.1/private.png" '
                'TargetMode="External"/></Relationships>'
            ),
        )

    with pytest.raises(ThumbnailError, match="external linked media"):
        render_deck_thumbnails(
            source,
            "ver_external",
            1,
            assets_dir=tmp_path / "assets",
            temp_dir=tmp_path / "tmp",
            renderer="officecli",
            executable_finder=lambda _: "/usr/bin/officecli",
        )


def test_external_hyperlink_relationship_remains_renderable(tmp_path: Path) -> None:
    source = tmp_path / "external-hyperlink.pptx"
    with ZipFile(source, "w") as package:
        package.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships"><Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/hyperlink" Target="https://example.com/" '
                'TargetMode="External"/></Relationships>'
            ),
        )

    thumbnails._reject_external_relationships(source)


def test_null_external_video_placeholder_remains_renderable(tmp_path: Path) -> None:
    source = tmp_path / "null-video.pptx"
    with ZipFile(source, "w") as package:
        package.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships"><Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/video" Target="NULL" '
                'TargetMode="External"/></Relationships>'
            ),
        )

    thumbnails._reject_external_relationships(source)


def test_zero_slides_short_circuits(tmp_path: Path) -> None:
    assert (
        render_deck_thumbnails(
            tmp_path / "missing.pptx",
            "ver_x",
            0,
            assets_dir=tmp_path / "assets",
            temp_dir=tmp_path / "tmp",
        )
        == 0
    )


def test_presentation_visibility_tracks_hidden_slide_positions(tmp_path: Path) -> None:
    source = _make_visibility_pptx(tmp_path / "deck.pptx")
    assert thumbnails._presentation_visibility(source, 3) == ([1, 3], [2])


def test_auto_cleans_partial_libreoffice_output_before_officecli_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _make_pptx(tmp_path / "deck.pptx")
    assets = tmp_path / "assets"
    calls: list[str] = []
    officecli_target = tmp_path / "cellar" / "officecli"
    officecli_target.parent.mkdir()
    officecli_target.write_text("")
    officecli_link = tmp_path / "bin" / "officecli"
    officecli_link.parent.mkdir()
    officecli_link.symlink_to(officecli_target)

    def fake_libreoffice(
        source_path: Path,
        slide_count: int,
        *,
        expected_thumbs: list[Path],
        expected_previews: list[Path],
        **_: object,
    ) -> None:
        calls.append("libreoffice")
        expected_thumbs[0].write_bytes(b"partial")
        expected_previews[0].write_bytes(b"partial")
        raise ThumbnailError("conversion failed")

    def fake_officecli(
        officecli: str,
        source_path: Path,
        slide_count: int,
        *,
        expected_thumbs: list[Path],
        expected_previews: list[Path],
        **_: object,
    ) -> None:
        calls.append("officecli")
        assert officecli == str(officecli_target.resolve())
        assert not any(
            target.exists() for target in list(expected_thumbs) + list(expected_previews)
        )
        for target in list(expected_thumbs) + list(expected_previews):
            target.write_bytes(b"jpeg")

    monkeypatch.setattr(thumbnails, "_render_with_officecli", fake_officecli)
    monkeypatch.setattr(thumbnails, "_render_with_libreoffice", fake_libreoffice)

    count = render_deck_thumbnails(
        source,
        "ver_deck",
        2,
        assets_dir=assets,
        temp_dir=tmp_path / "tmp",
        renderer="auto",
        executable_finder=lambda name: str(officecli_link) if name == "officecli" else None,
    )

    assert count == 2
    assert calls == ["libreoffice", "officecli"]
    assert (assets / "thumbnails" / "ver_deck_s00001.jpg").is_file()
    assert (assets / "previews" / "ver_deck_s00002.jpg").is_file()


def test_existing_assets_skip_rendering(tmp_path: Path) -> None:
    source = _make_pptx(tmp_path / "deck.pptx")
    assets = tmp_path / "assets"
    (assets / "thumbnails").mkdir(parents=True)
    (assets / "previews").mkdir(parents=True)
    for index in (1, 2):
        (assets / "thumbnails" / f"ver_deck_s{index:05d}.jpg").write_bytes(b"jpeg")
        (assets / "previews" / f"ver_deck_s{index:05d}.jpg").write_bytes(b"jpeg")
    thumbnails._write_render_marker(assets, "ver_deck", "libreoffice")

    # No renderer should be invoked; officecli finder returning a path would
    # otherwise trigger a real subprocess.
    count = render_deck_thumbnails(
        source,
        "ver_deck",
        2,
        assets_dir=assets,
        temp_dir=tmp_path / "tmp",
        renderer="auto",
        executable_finder=lambda _: None,
    )
    assert count == 2


def test_in_progress_marker_is_not_complete_even_when_assets_exist(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    (assets / "thumbnails").mkdir(parents=True)
    (assets / "previews").mkdir(parents=True)
    (assets / "thumbnails" / "ver_deck_s00001.jpg").write_bytes(b"jpeg")
    (assets / "previews" / "ver_deck_s00001.jpg").write_bytes(b"jpeg")
    thumbnails._write_render_marker(
        assets,
        "ver_deck",
        "libreoffice",
        complete=False,
    )

    assert not thumbnails.render_cache_is_complete(assets, "ver_deck", 1)


def test_in_progress_marker_rebuilds_in_staging_before_replacing_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _make_pptx(tmp_path / "deck.pptx")
    assets = tmp_path / "assets"
    (assets / "thumbnails").mkdir(parents=True)
    (assets / "previews").mkdir(parents=True)
    first_thumb = assets / "thumbnails" / "ver_deck_s00001.jpg"
    first_preview = assets / "previews" / "ver_deck_s00001.jpg"
    first_thumb.write_bytes(b"first-thumb")
    first_preview.write_bytes(b"first-preview")
    thumbnails._write_render_marker(
        assets,
        "ver_deck",
        "officecli",
        complete=False,
    )

    def fake_officecli(
        officecli: str,
        source_path: Path,
        slide_count: int,
        *,
        expected_thumbs: list[Path],
        expected_previews: list[Path],
        **_: object,
    ) -> None:
        assert first_thumb.read_bytes() == b"first-thumb"
        assert first_preview.read_bytes() == b"first-preview"
        assert not any(target.exists() for target in expected_thumbs + expected_previews)
        for target in list(expected_thumbs) + list(expected_previews):
            target.write_bytes(b"rebuilt")

    monkeypatch.setattr(thumbnails, "_render_with_officecli", fake_officecli)
    render_deck_thumbnails(
        source,
        "ver_deck",
        2,
        assets_dir=assets,
        temp_dir=tmp_path / "tmp",
        renderer="officecli",
        executable_finder=lambda _: "/usr/bin/officecli",
    )

    assert first_thumb.read_bytes() == b"rebuilt"
    assert first_preview.read_bytes() == b"rebuilt"
    assert (assets / "thumbnails" / "ver_deck_s00002.jpg").is_file()
    assert (assets / "previews" / "ver_deck_s00002.jpg").is_file()


def test_failed_force_render_preserves_last_complete_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _make_pptx(tmp_path / "deck.pptx")
    assets = tmp_path / "assets"
    (assets / "thumbnails").mkdir(parents=True)
    (assets / "previews").mkdir(parents=True)
    thumb = assets / "thumbnails" / "ver_deck_s00001.jpg"
    preview = assets / "previews" / "ver_deck_s00001.jpg"
    thumb.write_bytes(b"old-thumb")
    preview.write_bytes(b"old-preview")
    thumbnails._write_render_marker(assets, "ver_deck", "libreoffice")

    def failing_render(
        source_path: Path,
        slide_count: int,
        *,
        expected_thumbs: list[Path],
        expected_previews: list[Path],
        **_: object,
    ) -> None:
        expected_thumbs[0].write_bytes(b"partial-thumb")
        expected_previews[0].write_bytes(b"partial-preview")
        raise ThumbnailError("conversion failed")

    monkeypatch.setattr(thumbnails, "_render_with_libreoffice", failing_render)
    with pytest.raises(ThumbnailError, match="conversion failed"):
        render_deck_thumbnails(
            source,
            "ver_deck",
            1,
            assets_dir=assets,
            temp_dir=tmp_path / "tmp",
            renderer="libreoffice",
            force=True,
        )

    assert thumb.read_bytes() == b"old-thumb"
    assert preview.read_bytes() == b"old-preview"
    assert thumbnails.render_cache_is_complete(assets, "ver_deck", 1)
    assert list((assets / ".render-staging").glob("*")) == []


def test_legacy_assets_without_marker_are_refreshed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _make_pptx(tmp_path / "deck.pptx")
    assets = tmp_path / "assets"
    (assets / "thumbnails").mkdir(parents=True)
    (assets / "previews").mkdir(parents=True)
    targets = [assets / kind / "ver_deck_s00001.jpg" for kind in ("thumbnails", "previews")]
    for target in targets:
        target.write_bytes(b"old")

    def fake_libreoffice(
        source_path: Path,
        slide_count: int,
        *,
        expected_thumbs: list[Path],
        expected_previews: list[Path],
        **_: object,
    ) -> None:
        for target in list(expected_thumbs) + list(expected_previews):
            target.write_bytes(b"new")

    monkeypatch.setattr(thumbnails, "_render_with_libreoffice", fake_libreoffice)
    render_deck_thumbnails(
        source,
        "ver_deck",
        1,
        assets_dir=assets,
        temp_dir=tmp_path / "tmp",
        renderer="libreoffice",
    )

    assert all(target.read_bytes() == b"new" for target in targets)
    assert thumbnails._render_cache_is_current(assets, "ver_deck")


def test_force_renders_over_existing_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _make_pptx(tmp_path / "deck.pptx")
    assets = tmp_path / "assets"
    (assets / "thumbnails").mkdir(parents=True)
    (assets / "previews").mkdir(parents=True)
    targets = [
        assets / kind / f"ver_deck_s{index:05d}.jpg"
        for kind in ("thumbnails", "previews")
        for index in (1, 2)
    ]
    for target in targets:
        target.write_bytes(b"old")
    thumbnails._write_render_marker(assets, "ver_deck", "libreoffice")

    def fake_libreoffice(
        source_path: Path,
        slide_count: int,
        *,
        expected_thumbs: list[Path],
        expected_previews: list[Path],
        **_: object,
    ) -> None:
        for target in list(expected_thumbs) + list(expected_previews):
            target.write_bytes(b"new")

    monkeypatch.setattr(thumbnails, "_render_with_libreoffice", fake_libreoffice)
    render_deck_thumbnails(
        source,
        "ver_deck",
        2,
        assets_dir=assets,
        temp_dir=tmp_path / "tmp",
        renderer="libreoffice",
        force=True,
    )

    assert all(target.read_bytes() == b"new" for target in targets)


def test_officecli_html_backend_batches_only_missing_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _make_pptx(tmp_path / "deck.pptx")
    expected_thumbs = [tmp_path / f"thumb-{index}.jpg" for index in range(1, 11)]
    expected_previews = [tmp_path / f"preview-{index}.jpg" for index in range(1, 11)]
    expected_thumbs[1].write_bytes(b"existing")
    expected_previews[1].write_bytes(b"existing")
    commands: list[list[str]] = []
    batches: list[list[int]] = []

    class FakeBrowser:
        def close(self) -> None:
            pass

    class FakeChromium:
        def launch(self, **_kwargs: object) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeContext:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, *_args: object) -> None:
            pass

    def fake_run(command: list[str], *, timeout: int) -> None:
        commands.append(command)
        if "html" in command:
            Path(command[command.index("-o") + 1]).write_text("<html></html>")

    def fake_screenshot(
        browser: object,
        html_path: Path,
        page_numbers: list[int],
        **_kwargs: object,
    ) -> None:
        batches.append(page_numbers)
        for page_number in page_numbers:
            expected_thumbs[page_number - 1].write_bytes(b"thumb")
            expected_previews[page_number - 1].write_bytes(b"preview")

    monkeypatch.setattr(thumbnails, "_run", fake_run)
    monkeypatch.setattr(thumbnails, "sync_playwright", FakeContext)
    monkeypatch.setattr(thumbnails, "_screenshot_officecli_html", fake_screenshot)

    thumbnails._render_with_officecli(
        "/usr/bin/officecli",
        source,
        10,
        temp_dir=tmp_path / "work",
        expected_thumbs=expected_thumbs,
        expected_previews=expected_previews,
        thumbnail_long_edge=640,
        preview_long_edge=1440,
    )

    assert batches == [[1], [3, 4, 5, 6], [7, 8, 9, 10]]
    html_commands = [command for command in commands if "html" in command]
    assert [command[-1] for command in html_commands] == [
        "1",
        "3-6",
        "7-10",
    ]
    assert commands[0][1] == "open"
    assert commands[-1][1] == "close"


def test_officecli_images_preserve_slide_aspect_ratio(tmp_path: Path) -> None:
    original = Image.new("RGB", (1200, 900), "navy")
    screenshot = io.BytesIO()
    original.save(screenshot, format="JPEG", quality=92)
    thumb = tmp_path / "thumb.jpg"
    preview = tmp_path / "preview.jpg"

    thumbnails._write_officecli_images(
        screenshot.getvalue(),
        thumb,
        preview,
        640,
        1440,
    )

    with Image.open(thumb) as image:
        assert image.size == (640, 480)
    with Image.open(preview) as image:
        assert image.size == (1440, 1080)


def test_libreoffice_chunk_size_decreases_for_large_decks() -> None:
    assert thumbnails._libreoffice_page_chunk_size(255) == 64
    assert thumbnails._libreoffice_page_chunk_size(256) == 32
    assert thumbnails._libreoffice_page_chunk_size(511) == 32
    assert thumbnails._libreoffice_page_chunk_size(512) == 16


def test_libreoffice_exports_bounded_page_ranges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _make_pptx(tmp_path / "deck.pptx")
    expected_thumbs = [tmp_path / f"thumb-{index}.jpg" for index in range(1, 4)]
    expected_previews = [tmp_path / f"preview-{index}.jpg" for index in range(1, 4)]
    commands: list[list[str]] = []
    environments: list[dict[str, str] | None] = []
    progress: list[int] = []

    def fake_run(
        command: list[str],
        *,
        timeout: int,
        env: dict[str, str] | None = None,
    ) -> None:
        commands.append(command)
        environments.append(env)
        if command[0] == "/opt/soffice":
            output_dir = Path(command[command.index("--outdir") + 1])
            (output_dir / "deck.pdf").write_bytes(b"pdf")

    def fake_pdfium(
        pdf_path: Path,
        expected: list[Path],
        *,
        page_numbers: list[int],
        **_kwargs: object,
    ) -> None:
        for page_number in page_numbers:
            expected[page_number - 1].write_bytes(b"jpeg")

    monkeypatch.setattr(thumbnails, "_run", fake_run)
    monkeypatch.setattr(thumbnails, "_pdfium_images", fake_pdfium)
    monkeypatch.setattr(
        thumbnails,
        "_presentation_visibility",
        lambda _source, _count: ([1, 2, 3], []),
    )
    monkeypatch.setattr(thumbnails, "_libreoffice_page_chunk_size", lambda _size: 2)

    thumbnails._render_with_libreoffice(
        source,
        3,
        temp_dir=tmp_path / "work",
        expected_thumbs=expected_thumbs,
        expected_previews=expected_previews,
        thumbnail_long_edge=640,
        preview_long_edge=1440,
        executable_finder=lambda name: f"/opt/{name}",
        on_page=lambda page, _total: progress.append(page),
    )

    filters = [
        command[command.index("--convert-to") + 1]
        for command in commands
        if command[0] == "/opt/soffice"
    ]
    assert filters == [
        'pdf:impress_pdf_Export:{"PageRange":{"type":"string","value":"1,2"}}',
        'pdf:impress_pdf_Export:{"PageRange":{"type":"string","value":"3"}}',
    ]
    libreoffice_commands = [command for command in commands if command[0] == "/opt/soffice"]
    assert all(
        "--headless" in command and "--invisible" in command for command in libreoffice_commands
    )
    assert all(path.is_file() for path in expected_thumbs + expected_previews)
    assert progress == [1, 2, 3]
    libreoffice_envs = [
        env
        for command, env in zip(commands, environments, strict=True)
        if command[0] == "/opt/soffice"
    ]
    assert all(env and env["PYTHONDONTWRITEBYTECODE"] == "1" for env in libreoffice_envs)
    assert all(env and env["SAL_USE_VCLPLUGIN"] == "svp" for env in libreoffice_envs)


def test_pdfium_render_maps_pages_without_external_binary(tmp_path: Path) -> None:
    pdf_path = tmp_path / "deck.pdf"
    first = Image.new("RGB", (400, 300), "white")
    second = Image.new("RGB", (400, 300), "navy")
    first.save(pdf_path, format="PDF", save_all=True, append_images=[second])
    first.close()
    second.close()
    expected = [tmp_path / f"page-{index}.jpg" for index in range(1, 4)]

    thumbnails._pdfium_images(
        pdf_path,
        expected,
        long_edge=640,
        quality=85,
        page_numbers=[1, 3],
        prefix_name="thumbnail",
    )

    assert expected[0].is_file()
    assert not expected[1].exists()
    assert expected[2].is_file()
    with Image.open(expected[0]) as image:
        assert max(image.size) == 640
