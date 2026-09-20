from __future__ import annotations

from pathlib import Path

import pytest

from pptlib.rendering import thumbnails
from pptlib.rendering.thumbnails import ThumbnailError, render_deck_thumbnails


def _make_pptx(path: Path) -> Path:
    # A minimal non-empty file is enough; renderers are stubbed in these tests.
    path.write_bytes(b"PK\x03\x04 fake pptx")
    return path


def test_backend_order_prefers_officecli_when_available() -> None:
    assert thumbnails._backend_order("auto", "/usr/bin/officecli") == [
        "officecli",
        "libreoffice",
    ]
    assert thumbnails._backend_order("auto", None) == ["libreoffice"]
    assert thumbnails._backend_order("officecli", None) == ["officecli"]
    assert thumbnails._backend_order("libreoffice", "/usr/bin/officecli") == ["libreoffice"]


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


def test_auto_falls_back_to_libreoffice_when_officecli_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _make_pptx(tmp_path / "deck.pptx")
    assets = tmp_path / "assets"
    calls: list[str] = []

    def fake_officecli(*args: object, **kwargs: object) -> None:
        calls.append("officecli")
        raise ThumbnailError("no headless browser")

    def fake_libreoffice(
        source_path: Path,
        slide_count: int,
        *,
        expected_thumbs: list[Path],
        expected_previews: list[Path],
        **_: object,
    ) -> None:
        calls.append("libreoffice")
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
        executable_finder=lambda name: "/usr/bin/officecli" if name == "officecli" else None,
    )

    assert count == 2
    assert calls == ["officecli", "libreoffice"]
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
