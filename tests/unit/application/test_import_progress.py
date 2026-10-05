from __future__ import annotations

import os
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest

from pptlib.application import import_decks
from pptlib.application.import_decks import MissingPolicy, scan_and_import
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect
from pptlib.rendering import thumbnails

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
        assets_dir = Path(_kwargs["assets_dir"])
        for page in range(1, slide_count + 1):
            for kind in ("thumbnails", "previews"):
                target = assets_dir / kind / f"{version_id}_s{page:05d}.jpg"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"jpeg")
            if on_page is not None:
                on_page(page, slide_count)
        thumbnails._write_render_marker(assets_dir, version_id, "officecli")
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
        report = scan_and_import(connection, [src], settings=settings, on_progress=events.append)
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
        second = scan_and_import(connection, [src], settings=settings, on_progress=events.append)
    finally:
        connection.close()

    assert first.imported[0].created is True
    # Re-importing the unchanged file is detected as a duplicate.
    assert second.imported[0].created is False
    assert second.skipped == 1
    done = [e for e in events if e["stage"] == "file_done"]
    assert done and done[0]["created"] is False


def test_pptx_render_failure_is_reported_and_retried_without_reparse(
    monkeypatch, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    deck = src / "a.pptx"
    _write_pptx(deck, "文件甲")
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    calls = 0

    def flaky_render(source_path, version_id, slide_count, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise thumbnails.ThumbnailError("browser failed")
        assets_dir = Path(kwargs["assets_dir"])
        for page in range(1, slide_count + 1):
            for kind in ("thumbnails", "previews"):
                target = assets_dir / kind / f"{version_id}_s{page:05d}.jpg"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"jpeg")
        thumbnails._write_render_marker(assets_dir, version_id, "officecli")
        return slide_count

    monkeypatch.setattr(import_decks, "render_deck_thumbnails", flaky_render)
    connection = connect(settings.database_path)
    try:
        first_events: list[dict] = []
        first = scan_and_import(
            connection, [src], settings=settings, on_progress=first_events.append
        )

        def unexpected_parse(*_args, **_kwargs):
            pytest.fail("retrying a missing preview must not reparse the PPTX")

        monkeypatch.setattr(import_decks, "parse_pptx", unexpected_parse)
        second = scan_and_import(connection, [src], settings=settings)
    finally:
        connection.close()

    assert len(first.imported) == 1
    assert first.failed == ((deck.resolve(), "browser failed"),)
    assert [event["stage"] for event in first_events][-1] == "error"
    assert not any(event["stage"] == "file_done" for event in first_events)
    assert second.failed == ()
    assert second.imported[0].action == "unchanged"
    assert calls == 2


def test_unchanged_file_skips_hash_parse_and_render(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    deck = src / "a.pptx"
    _write_pptx(deck, "文件甲")
    render_calls: list[str] = []

    def fake_render(source_path, version_id, slide_count, **kwargs):
        render_calls.append(version_id)
        assets_dir = Path(kwargs["assets_dir"])
        for page in range(1, slide_count + 1):
            for kind in ("thumbnails", "previews"):
                target = assets_dir / kind / f"{version_id}_s{page:05d}.jpg"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"jpeg")
        thumbnails._write_render_marker(assets_dir, version_id, "officecli")
        return slide_count

    monkeypatch.setattr(import_decks, "render_deck_thumbnails", fake_render)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    connection = connect(settings.database_path)
    try:
        first = scan_and_import(connection, [src], settings=settings)

        def unexpected(*_args, **_kwargs):
            pytest.fail("unchanged file must not be hashed, parsed, or rendered")

        monkeypatch.setattr(import_decks, "hash_discovered_file", unexpected)
        monkeypatch.setattr(import_decks, "parse_pptx", unexpected)
        second = scan_and_import(connection, [src], settings=settings)
    finally:
        connection.close()

    assert first.imported[0].action == "created"
    assert second.imported[0].action == "unchanged"
    assert second.skipped == 1
    assert len(render_calls) == 1


def test_mtime_only_change_hashes_but_skips_parse(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    deck = src / "a.pptx"
    _write_pptx(deck, "文件甲")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    connection = connect(settings.database_path)
    try:
        first = scan_and_import(connection, [src], settings=settings)
        original = deck.stat().st_mtime_ns
        deck.touch()
        assert deck.stat().st_mtime_ns != original

        def unexpected_parse(*_args, **_kwargs):
            pytest.fail("same content hash must not be parsed again")

        monkeypatch.setattr(import_decks, "parse_pptx", unexpected_parse)
        second = scan_and_import(connection, [src], settings=settings)
        row = connection.execute(
            "SELECT mtime_ns FROM deck_versions WHERE id = ?",
            (first.imported[0].version_id,),
        ).fetchone()
    finally:
        connection.close()

    assert second.imported[0].action == "unchanged"
    assert int(row[0]) == deck.stat().st_mtime_ns


def test_directory_sync_relocates_identical_file_without_reparse(
    monkeypatch, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    old_dir = src / "old"
    new_dir = src / "new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir()
    old_path = old_dir / "a.pptx"
    new_path = new_dir / "a.pptx"
    _write_pptx(old_path, "文件甲")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)

    connection = connect(settings.database_path)
    try:
        original = scan_and_import(connection, [src], settings=settings).imported[0]
        slide_id = f"{original.version_id}_s00001"
        connection.execute(
            """
            INSERT INTO selections(id, name, created_at, updated_at)
            VALUES ('selection-moved', '移动后仍可用', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO selection_items(
                id, selection_id, slide_id, deck_version_id,
                source_page_number, sort_order, created_at
            ) VALUES ('item-moved', 'selection-moved', ?, ?, 1, 1, 'now')
            """,
            (slide_id, original.version_id),
        )
        old_path.rename(new_path)

        def unexpected(*_args, **_kwargs):
            pytest.fail("relocated identical file must not be parsed or rendered")

        monkeypatch.setattr(import_decks, "parse_pptx", unexpected)
        monkeypatch.setattr(import_decks, "render_deck_thumbnails", unexpected)
        relocated = scan_and_import(connection, [src], settings=settings)
        row = connection.execute(
            """
            SELECT d.id, d.canonical_path, d.display_name, v.id, COUNT(s.id)
            FROM decks d
            JOIN deck_versions v ON v.id = d.current_version_id
            JOIN slides s ON s.deck_version_id = v.id
            GROUP BY d.id, d.canonical_path, d.display_name, v.id
            """
        ).fetchone()
        selection = connection.execute(
            """
            SELECT i.slide_id, i.deck_version_id, d.canonical_path
            FROM selection_items i
            JOIN deck_versions v ON v.id = i.deck_version_id
            JOIN decks d ON d.id = v.deck_id
            WHERE i.id = 'item-moved'
            """
        ).fetchone()
    finally:
        connection.close()

    moved = relocated.imported[0]
    assert moved.action == "moved"
    assert moved.deck_id == original.deck_id
    assert moved.version_id == original.version_id
    assert moved.path == new_path.resolve()
    assert relocated.skipped == 1
    assert relocated.removed == ()
    assert tuple(row) == (
        original.deck_id,
        str(new_path.resolve()),
        "a",
        original.version_id,
        1,
    )
    assert tuple(selection) == (
        slide_id,
        original.version_id,
        str(new_path.resolve()),
    )


def test_identical_copy_is_new_when_original_source_still_exists(
    monkeypatch, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    original_path = src / "a.pptx"
    copied_path = src / "b.pptx"
    _write_pptx(original_path, "文件甲")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)

    connection = connect(settings.database_path)
    try:
        original = scan_and_import(connection, [src], settings=settings).imported[0]
        shutil.copy2(original_path, copied_path)
        copied = scan_and_import(connection, [src], settings=settings)
        rows = connection.execute(
            "SELECT id, canonical_path, current_version_id FROM decks ORDER BY canonical_path"
        ).fetchall()
    finally:
        connection.close()

    assert [item.action for item in copied.imported] == ["unchanged", "created"]
    assert len(rows) == 2
    assert str(rows[0][0]) == original.deck_id
    assert str(rows[0][1]) == str(original_path.resolve())
    assert str(rows[0][2]) == original.version_id
    assert str(rows[1][1]) == str(copied_path.resolve())
    assert str(rows[1][0]) != original.deck_id
    assert str(rows[1][2]) != original.version_id


def test_relocation_reactivates_missing_deck_preserved_by_selection(
    monkeypatch, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    old_dir = src / "old"
    new_dir = src / "new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir()
    old_path = old_dir / "a.pptx"
    backup = tmp_path / "backup.pptx"
    new_path = new_dir / "a.pptx"
    _write_pptx(old_path, "文件甲")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)

    connection = connect(settings.database_path)
    try:
        original = scan_and_import(connection, [src], settings=settings).imported[0]
        slide_id = f"{original.version_id}_s00001"
        connection.execute(
            """
            INSERT INTO selections(id, name, created_at, updated_at)
            VALUES ('selection-missing', '缺失后恢复', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO selection_items(
                id, selection_id, slide_id, deck_version_id,
                source_page_number, sort_order, created_at
            ) VALUES ('item-missing', 'selection-missing', ?, ?, 1, 1, 'now')
            """,
            (slide_id, original.version_id),
        )
        shutil.copy2(old_path, backup)
        old_path.unlink()
        missing = scan_and_import(connection, [src], settings=settings)
        hidden = connection.execute(
            "SELECT current_version_id FROM decks WHERE id = ?",
            (original.deck_id,),
        ).fetchone()
        backup.rename(new_path)

        def unexpected(*_args, **_kwargs):
            pytest.fail("reactivated identical file must not be parsed or rendered")

        monkeypatch.setattr(import_decks, "parse_pptx", unexpected)
        monkeypatch.setattr(import_decks, "render_deck_thumbnails", unexpected)
        restored = scan_and_import(connection, [src], settings=settings)
        row = connection.execute(
            "SELECT canonical_path, current_version_id FROM decks WHERE id = ?",
            (original.deck_id,),
        ).fetchone()
    finally:
        connection.close()

    assert missing.removed == (old_path.resolve(),)
    assert hidden[0] is None
    assert restored.imported[0].action == "moved"
    assert restored.imported[0].deck_id == original.deck_id
    assert restored.imported[0].version_id == original.version_id
    assert tuple(row) == (str(new_path.resolve()), original.version_id)


def test_missing_policy_keep_preserves_visible_library_content(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    deck = src / "a.pptx"
    _write_pptx(deck, "文件甲")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)

    connection = connect(settings.database_path)
    try:
        original = scan_and_import(connection, [src], settings=settings).imported[0]
        deck.unlink()
        report = scan_and_import(
            connection,
            [src],
            settings=settings,
            missing_policy=MissingPolicy.KEEP,
        )
        current = connection.execute(
            "SELECT current_version_id FROM decks WHERE id = ?",
            (original.deck_id,),
        ).fetchone()
    finally:
        connection.close()

    assert report.removed == ()
    assert current[0] == original.version_id


def test_repair_existing_assets_can_be_disabled(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    deck = src / "a.pptx"
    _write_pptx(deck, "文件甲")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    connection = connect(settings.database_path)
    try:
        imported = scan_and_import(connection, [src], settings=settings).imported[0]
        for kind in ("thumbnails", "previews"):
            (settings.assets_dir / kind / f"{imported.version_id}_s00001.jpg").unlink()

        def unexpected_render(*_args, **_kwargs):
            pytest.fail("disabled preview repair must not invoke the renderer")

        monkeypatch.setattr(import_decks, "render_deck_thumbnails", unexpected_render)
        report = scan_and_import(
            connection,
            [src],
            settings=settings,
            repair_existing_assets=False,
        )
    finally:
        connection.close()

    assert report.imported[0].action == "unchanged"
    assert report.failed == ()


def test_multiple_new_copies_do_not_claim_one_missing_deck(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    old_dir = src / "old"
    first_dir = src / "first"
    second_dir = src / "second"
    old_dir.mkdir(parents=True)
    first_dir.mkdir()
    second_dir.mkdir()
    old_path = old_dir / "a.pptx"
    _write_pptx(old_path, "文件甲")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)

    connection = connect(settings.database_path)
    try:
        original = scan_and_import(connection, [src], settings=settings).imported[0]
        payload = old_path.read_bytes()
        old_path.unlink()
        (first_dir / "a.pptx").write_bytes(payload)
        (second_dir / "a.pptx").write_bytes(payload)
        report = scan_and_import(connection, [src], settings=settings)
        deck_ids = {str(row[0]) for row in connection.execute("SELECT id FROM decks").fetchall()}
    finally:
        connection.close()

    assert [item.action for item in report.imported] == ["created", "created"]
    assert report.removed == (old_path.resolve(),)
    assert len(deck_ids) == 2
    assert original.deck_id not in deck_ids


def test_relocated_deck_keeps_identity_when_content_changes(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    old_dir = src / "old"
    new_dir = src / "new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir()
    old_path = old_dir / "a.pptx"
    new_path = new_dir / "a.pptx"
    _write_pptx(old_path, "旧标题")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)

    connection = connect(settings.database_path)
    try:
        original = scan_and_import(connection, [src], settings=settings).imported[0]
        old_path.rename(new_path)
        moved = scan_and_import(connection, [src], settings=settings).imported[0]
        _write_pptx(new_path, "新标题")
        updated = scan_and_import(connection, [src], settings=settings).imported[0]
        rows = connection.execute(
            "SELECT id, canonical_path, current_version_id FROM decks"
        ).fetchall()
    finally:
        connection.close()

    assert moved.action == "moved"
    assert updated.action == "updated"
    assert updated.deck_id == original.deck_id
    assert updated.version_id != original.version_id
    assert [tuple(row) for row in rows] == [
        (original.deck_id, str(new_path.resolve()), updated.version_id)
    ]


def test_same_size_and_mtime_replacement_is_detected_by_ctime(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    deck = src / "a.pptx"
    _write_pptx(deck, "AAAA")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    connection = connect(settings.database_path)
    try:
        first = scan_and_import(connection, [src], settings=settings).imported[0]
        original = deck.stat()
        _write_pptx(deck, "BBBB")
        os.utime(deck, ns=(original.st_atime_ns, original.st_mtime_ns))
        replaced = deck.stat()
        assert replaced.st_size == original.st_size
        assert replaced.st_mtime_ns == original.st_mtime_ns
        assert replaced.st_ctime_ns != original.st_ctime_ns

        second = scan_and_import(connection, [src], settings=settings).imported[0]
    finally:
        connection.close()

    assert second.action == "updated"
    assert second.version_id != first.version_id


def test_changed_file_replaces_old_version_and_cached_assets(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    deck = src / "a.pptx"
    _write_pptx(deck, "旧标题")
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)

    def fake_render(source_path, version_id, slide_count, *, assets_dir, **_kwargs):
        for kind in ("thumbnails", "previews"):
            target = assets_dir / kind / f"{version_id}_s00001.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"jpeg")
        marker = assets_dir / "render-meta" / f"{version_id}.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}")
        return slide_count

    monkeypatch.setattr(import_decks, "render_deck_thumbnails", fake_render)
    connection = connect(settings.database_path)
    try:
        first = scan_and_import(connection, [src], settings=settings)
        old = first.imported[0]
        _write_pptx(deck, "新标题")
        second = scan_and_import(connection, [src], settings=settings)
        versions = connection.execute(
            "SELECT id FROM deck_versions WHERE deck_id = ?",
            (old.deck_id,),
        ).fetchall()
        title = connection.execute("SELECT title FROM slides").fetchone()
    finally:
        connection.close()

    assert second.imported[0].action == "updated"
    assert [str(row[0]) for row in versions] == [second.imported[0].version_id]
    assert str(title[0]) == "新标题"
    assert not (settings.assets_dir / "thumbnails" / f"{old.version_id}_s00001.jpg").exists()
    assert not (settings.assets_dir / "previews" / f"{old.version_id}_s00001.jpg").exists()
    assert not (settings.assets_dir / "render-meta" / f"{old.version_id}.json").exists()


def test_directory_sync_removes_missing_file_but_explicit_file_does_not(
    monkeypatch, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    first_path = src / "a.pptx"
    second_path = src / "b.pptx"
    _write_pptx(first_path, "文件甲")
    _write_pptx(second_path, "文件乙")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    connection = connect(settings.database_path)
    try:
        scan_and_import(connection, [src], settings=settings)
        second_path.unlink()

        explicit = scan_and_import(connection, [first_path], settings=settings)
        count_after_explicit = connection.execute("SELECT COUNT(*) FROM decks").fetchone()[0]

        synced = scan_and_import(connection, [src], settings=settings)
        remaining = connection.execute(
            "SELECT canonical_path FROM decks WHERE current_version_id IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()

    assert explicit.removed == ()
    assert count_after_explicit == 2
    assert synced.removed == (second_path.resolve(),)
    assert [str(row[0]) for row in remaining] == [str(first_path.resolve())]


def test_sync_preserves_versions_referenced_by_saved_selection(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    deck = src / "a.pptx"
    _write_pptx(deck, "旧标题")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    connection = connect(settings.database_path)
    try:
        first = scan_and_import(connection, [src], settings=settings).imported[0]
        old_slide_id = f"{first.version_id}_s00001"
        connection.execute(
            """
            INSERT INTO selections(id, name, created_at, updated_at)
            VALUES ('selection-1', '保留旧页', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO selection_items(
                id, selection_id, slide_id, deck_version_id,
                source_page_number, sort_order, created_at
            ) VALUES ('item-1', 'selection-1', ?, ?, 1, 1, 'now')
            """,
            (old_slide_id, first.version_id),
        )

        _write_pptx(deck, "新标题")
        updated = scan_and_import(connection, [src], settings=settings).imported[0]
        version_ids = {
            str(row[0])
            for row in connection.execute(
                "SELECT id FROM deck_versions WHERE deck_id = ?",
                (first.deck_id,),
            ).fetchall()
        }

        deck.unlink()
        report = scan_and_import(connection, [src], settings=settings)
        current = connection.execute(
            "SELECT current_version_id FROM decks WHERE id = ?",
            (first.deck_id,),
        ).fetchone()
    finally:
        connection.close()

    assert version_ids == {first.version_id, updated.version_id}
    assert report.removed == (deck.resolve(),)
    assert current is not None and current[0] is None
