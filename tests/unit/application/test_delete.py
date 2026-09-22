from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pptlib.application.delete import delete_decks, delete_slides
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.domain.errors import AppError
from pptlib.infrastructure.db.connection import connect


def _seed(settings, *, with_selection: bool = False) -> None:
    """Seed one deck with two slides, thumbnails, taxonomy and FTS rows."""
    initialize(settings)
    now = datetime.now(UTC).isoformat()
    thumbs = settings.assets_dir / "thumbnails"
    previews = settings.assets_dir / "previews"
    thumbs.mkdir(parents=True, exist_ok=True)
    previews.mkdir(parents=True, exist_ok=True)
    connection = connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO decks(id, canonical_path, display_name, current_version_id,"
            " created_at, updated_at) VALUES ('deck_a', '/tmp/a.pptx', 'A', 'ver_a', ?, ?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO deck_versions(id, deck_id, sha256, size_bytes, mtime_ns,"
            " parser_version, slide_count, status, created_at, updated_at)"
            " VALUES ('ver_a', 'deck_a', 'sha', 1, 1, 'p', 2, 'parsed', ?, ?)",
            (now, now),
        )
        for n in (1, 2):
            sid = f"ver_a_s{n:05d}"
            connection.execute(
                "INSERT INTO slides(id, deck_version_id, slide_number, title, body_text,"
                " notes_text, content_text, created_at)"
                " VALUES (?, 'ver_a', ?, ?, '正文', '', '标题 正文', ?)",
                (sid, n, f"标题{n}", now),
            )
            connection.execute(
                "INSERT INTO slide_fts(slide_id, title, body_text, notes_text)"
                " VALUES (?, ?, '正文', '')",
                (sid, f"标题{n}"),
            )
            connection.execute(
                "INSERT INTO slide_taxonomy(slide_id, topic, subtopic, page_type,"
                " confidence, classification_source, classifier_version, classified_at)"
                " VALUES (?, '战略与增长', '业务规划与增长', '观点与结论', 'high', 'auto', 'v', ?)",
                (sid, now),
            )
            (thumbs / f"{sid}.jpg").write_bytes(b"jpeg")
            (previews / f"{sid}.jpg").write_bytes(b"jpeg")
        if with_selection:
            connection.execute(
                "INSERT INTO selections(id, name, created_at, updated_at)"
                " VALUES ('sel_1', 'S', ?, ?)",
                (now, now),
            )
            connection.execute(
                "INSERT INTO selection_items(id, selection_id, slide_id, deck_version_id,"
                " source_page_number, sort_order, created_at)"
                " VALUES ('it_1', 'sel_1', 'ver_a_s00001', 'ver_a', 1, 0, ?)",
                (now,),
            )
    finally:
        connection.close()


def _counts(settings) -> dict[str, int]:
    connection = connect(settings.database_path)
    try:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("decks", "deck_versions", "slides", "slide_taxonomy", "slide_fts")
        }
    finally:
        connection.close()


def test_delete_deck_cascades_and_clears_assets(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    _seed(settings)

    result = delete_decks(settings, ["deck_a"])

    assert result.decks_removed == 1
    assert result.slides_removed == 2
    assert result.thumbnails_removed == 4  # 2 thumbnails + 2 previews
    assert _counts(settings) == {
        "decks": 0,
        "deck_versions": 0,
        "slides": 0,
        "slide_taxonomy": 0,
        "slide_fts": 0,
    }
    # cached image files are gone
    assert not list((settings.assets_dir / "thumbnails").glob("*.jpg"))
    assert not list((settings.assets_dir / "previews").glob("*.jpg"))


def test_delete_single_slide_keeps_the_rest(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    _seed(settings)

    result = delete_slides(settings, ["ver_a_s00001"])

    assert result.slides_removed == 1
    counts = _counts(settings)
    # one slide (and its fts/taxonomy) gone; the deck + version survive
    assert counts["slides"] == 1
    assert counts["slide_fts"] == 1
    assert counts["slide_taxonomy"] == 1
    assert counts["decks"] == 1
    assert counts["deck_versions"] == 1
    assert (settings.assets_dir / "thumbnails" / "ver_a_s00002.jpg").is_file()
    assert not (settings.assets_dir / "thumbnails" / "ver_a_s00001.jpg").exists()


def test_deleting_last_slide_prunes_empty_deck(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    _seed(settings)

    delete_slides(settings, ["ver_a_s00001", "ver_a_s00002"])

    counts = _counts(settings)
    assert counts == {
        "decks": 0,
        "deck_versions": 0,
        "slides": 0,
        "slide_taxonomy": 0,
        "slide_fts": 0,
    }


def test_delete_blocked_when_slide_in_selection(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    _seed(settings, with_selection=True)

    with pytest.raises(AppError):
        delete_decks(settings, ["deck_a"])
    # nothing was removed
    assert _counts(settings)["slides"] == 2


def test_delete_requires_ids(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    _seed(settings)
    with pytest.raises(AppError):
        delete_decks(settings, [])
    with pytest.raises(AppError):
        delete_slides(settings, ["  "])
