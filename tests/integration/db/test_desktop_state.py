from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pptlib.application.desktop_state import (
    desktop_bootstrap,
    desktop_search,
    recover_desktop_task,
    save_desktop_selection,
    set_desktop_task,
)
from pptlib.application.library import LibraryFilters
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect


def _settings(tmp_path: Path):
    return replace(
        load_settings({}),
        home=tmp_path,
        database_path=tmp_path / "pages.db",
        assets_dir=tmp_path / "assets",
        temp_dir=tmp_path / "tmp",
        log_dir=tmp_path / "logs",
        output_root=tmp_path / "exports",
    )


def _insert_deck(settings, *, deck_id: str, version_id: str, name: str, pages: int) -> list[str]:
    slide_ids = []
    with connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO decks(
                id, canonical_path, display_name, current_version_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (deck_id, str(settings.home / f"{name}.pptx"), name, version_id),
        )
        connection.execute(
            """
            INSERT INTO deck_versions(
                id, deck_id, sha256, size_bytes, mtime_ns, parser_version,
                slide_count, status, created_at, updated_at
            ) VALUES (?, ?, ?, 1, 1, 'test', ?, 'parsed', datetime('now'), datetime('now'))
            """,
            (version_id, deck_id, version_id, pages),
        )
        for number in range(1, pages + 1):
            slide_id = f"{version_id}_s{number:05d}"
            slide_ids.append(slide_id)
            connection.execute(
                """
                INSERT INTO slides(
                    id, deck_version_id, slide_number, title, body_text,
                    notes_text, content_text, created_at
                ) VALUES (?, ?, ?, ?, ?, '', ?, datetime('now'))
                """,
                (
                    slide_id,
                    version_id,
                    number,
                    f"Page {number}",
                    f"body {number}",
                    f"body {number}",
                ),
            )
            connection.execute(
                """
                INSERT INTO slide_fts(slide_id, title, body_text, notes_text)
                VALUES (?, ?, ?, '')
                """,
                (slide_id, f"Page {number}", f"body {number}"),
            )
    return slide_ids


def test_desktop_bootstrap_and_search_are_paginated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    initialize(settings)
    slide_ids = _insert_deck(
        settings,
        deck_id="deck_a",
        version_id="ver_a",
        name="Source",
        pages=3,
    )

    bootstrap = desktop_bootstrap(settings)
    assert bootstrap["slideCount"] == 3
    assert bootstrap["decks"][0]["source_path"].endswith("Source.pptx")
    assert bootstrap["selection"] == {"revision": 0, "items": []}

    page = desktop_search(
        settings,
        query="",
        page=2,
        page_size=2,
        filters=LibraryFilters(deck_id="deck_a"),
    )
    assert page["total"] == 3
    assert [item["slide_id"] for item in page["items"]] == [slide_ids[2]]
    assert page["deckIds"] == ["deck_a"]
    by_filename = desktop_search(
        settings,
        query="Source.pptx",
        page=1,
        page_size=10,
        filters=LibraryFilters(),
    )
    assert by_filename["total"] == 3

    unchanged = desktop_bootstrap(
        settings,
        since_revision=str(bootstrap["libraryRevision"]),
    )
    assert unchanged["unchanged"] is True
    assert unchanged["decks"] == []
    assert unchanged["slideCount"] is None


def test_desktop_selection_and_task_state_survive_new_connections(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    initialize(settings)
    slide_ids = _insert_deck(
        settings,
        deck_id="deck_a",
        version_id="ver_a",
        name="Source",
        pages=2,
    )

    saved = save_desktop_selection(
        settings,
        {"slideIds": list(reversed(slide_ids)), "expectedRevision": 0},
    )
    assert saved["revision"] == 1
    assert [item["slide_id"] for item in saved["items"]] == list(reversed(slide_ids))
    restored = desktop_bootstrap(settings)
    assert restored["selection"]["revision"] == 1
    assert [item["slide_id"] for item in restored["selection"]["items"]] == list(
        reversed(slide_ids)
    )

    task = set_desktop_task(
        settings,
        {
            "taskId": "task-1",
            "kind": "导入并渲染",
            "state": "running",
            "startedAt": "2026-10-06T00:00:00+00:00",
            "progress": {"current": 2, "total": 10},
        },
    )
    assert task["task"]["progress"] == {"current": 2, "total": 10}
    recovered = recover_desktop_task(settings)
    assert recovered["recovered"] is True
    assert recovered["task"]["state"] == "interrupted"
