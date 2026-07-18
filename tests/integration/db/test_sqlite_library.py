from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pptlib.application.library import LibraryFilters, SlideSummary
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.library import (
    SqliteSelectionStore,
    SqliteSlideCatalog,
    selection_slide_refs,
)
from pptlib.infrastructure.db.migrations import migrate
from pptlib.infrastructure.db.repositories import count_search_slides, search_slides

MIGRATIONS = Path(__file__).resolve().parents[3] / "src" / "pptlib" / "migrations"


def _database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "pages.db")
    migrate(connection, MIGRATIONS)
    return connection


def _insert_version(
    connection: sqlite3.Connection,
    *,
    deck_id: str,
    version_id: str,
    path: Path,
    title: str,
    body: str,
) -> str:
    connection.execute(
        """
        INSERT INTO decks(id, canonical_path, display_name, created_at, updated_at)
        VALUES (?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(id) DO NOTHING
        """,
        (deck_id, str(path), path.stem),
    )
    connection.execute(
        """
        INSERT INTO deck_versions(
            id, deck_id, sha256, size_bytes, mtime_ns, parser_version,
            slide_count, status, created_at, updated_at
        ) VALUES (?, ?, ?, 1, 1, 'test', 1, 'parsed', datetime('now'), datetime('now'))
        """,
        (version_id, deck_id, version_id),
    )
    slide_id = f"{version_id}_s00001"
    connection.execute(
        """
        INSERT INTO slides(
            id, deck_version_id, slide_number, title, body_text, notes_text,
            content_text, created_at
        ) VALUES (?, ?, 1, ?, ?, '', ?, datetime('now'))
        """,
        (slide_id, version_id, title, body, body),
    )
    grams = " ".join(body[index : index + 2] for index in range(len(body) - 1))
    connection.execute(
        "INSERT INTO slide_fts(slide_id, title, body_text, notes_text) VALUES (?, ?, ?, '')",
        (slide_id, title, f"{body} {grams}"),
    )
    return slide_id


def test_selection_add_is_idempotent_for_same_slide(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        slide_id = _insert_version(
            connection,
            deck_id="deck_a",
            version_id="ver_a",
            path=tmp_path / "a.pptx",
            title="A",
            body="body",
        )
    finally:
        connection.close()

    store = SqliteSelectionStore(tmp_path / "pages.db")
    summary = SlideSummary(slide_id, "deck_a", "a.pptx", 1, "A", "body")
    assert [item.id for item in store.add(summary)] == [slide_id]
    assert [item.id for item in store.add(summary)] == [slide_id]
    assert store.reorder([slide_id])[0].id == slide_id

    connection = connect(tmp_path / "pages.db")
    try:
        assert connection.execute("SELECT COUNT(*) FROM selection_items").fetchone()[0] == 1
    finally:
        connection.close()


def test_search_count_and_get_only_use_current_deck_version(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        old_slide = _insert_version(
            connection,
            deck_id="deck_a",
            version_id="ver_old",
            path=tmp_path / "a.pptx",
            title="Old title",
            body="legacy-only",
        )
        new_slide = _insert_version(
            connection,
            deck_id="deck_a",
            version_id="ver_new",
            path=tmp_path / "a.pptx",
            title="New title",
            body="current-only",
        )
        connection.execute(
            "UPDATE decks SET current_version_id = 'ver_new' WHERE id = 'deck_a'"
        )

        assert search_slides(connection, "legacy-only") == []
        assert count_search_slides(connection, "legacy-only") == 0
        current = search_slides(connection, "current-only")
        assert [result.slide_id for result in current] == [new_slide]
        assert count_search_slides(connection, "current-only") == 1
    finally:
        connection.close()

    catalog = SqliteSlideCatalog(tmp_path / "pages.db")
    assert catalog.get(old_slide) is None
    assert catalog.get(new_slide) is not None


def test_empty_search_browses_current_slides_with_pagination(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        first = _insert_version(
            connection,
            deck_id="deck_a",
            version_id="ver_a",
            path=tmp_path / "a.pptx",
            title="A",
            body="first",
        )
        second = _insert_version(
            connection,
            deck_id="deck_b",
            version_id="ver_b",
            path=tmp_path / "b.pptx",
            title="B",
            body="second",
        )
        connection.execute(
            "UPDATE decks SET current_version_id = CASE id "
            "WHEN 'deck_a' THEN 'ver_a' WHEN 'deck_b' THEN 'ver_b' END "
            "WHERE id IN ('deck_a', 'deck_b')"
        )
    finally:
        connection.close()

    catalog = SqliteSlideCatalog(tmp_path / "pages.db")
    page = catalog.search("", page=1, page_size=1)
    assert page.total == 2
    assert [item.id for item in page.items] == [first]
    assert catalog.search("", page=2, page_size=1).items[0].id == second


def test_catalog_exposes_existing_slide_thumbnail_url(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        slide_id = _insert_version(
            connection,
            deck_id="deck_a",
            version_id="ver_a",
            path=tmp_path / "a.pptx",
            title="A",
            body="body",
        )
        connection.execute("UPDATE decks SET current_version_id = 'ver_a' WHERE id = 'deck_a'")
    finally:
        connection.close()

    thumbnail = tmp_path / "assets" / "thumbnails" / f"{slide_id}.jpg"
    thumbnail.parent.mkdir(parents=True)
    thumbnail.write_bytes(b"jpg")

    catalog = SqliteSlideCatalog(tmp_path / "pages.db", assets_dir=tmp_path / "assets")
    slide = catalog.get(slide_id)

    assert slide is not None
    assert slide.thumbnail_url == f"/assets/thumbnails/{slide_id}.jpg"
    assert slide.preview_url == slide.thumbnail_url


def test_selection_refs_keep_selected_version_after_source_changes(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        old_slide = _insert_version(
            connection,
            deck_id="deck_a",
            version_id="ver_old",
            path=tmp_path / "a.pptx",
            title="Old title",
            body="legacy-only",
        )
        _insert_version(
            connection,
            deck_id="deck_a",
            version_id="ver_new",
            path=tmp_path / "a.pptx",
            title="New title",
            body="current-only",
        )
        connection.execute(
            "UPDATE decks SET current_version_id = 'ver_new' WHERE id = 'deck_a'"
        )
        connection.execute(
            "INSERT INTO selections(id, name, created_at, updated_at) "
            "VALUES ('selection_default', 'default', datetime('now'), datetime('now'))"
        )
        connection.execute(
            """
            INSERT INTO selection_items(
                id, selection_id, slide_id, deck_version_id, source_page_number,
                sort_order, created_at
            ) VALUES ('item_old', 'selection_default', ?, 'ver_old', 1, 1, datetime('now'))
            """,
            (old_slide,),
        )
    finally:
        connection.close()

    refs = selection_slide_refs(tmp_path / "pages.db")
    assert len(refs) == 1
    assert refs[0].file_version_id == "ver_old"
    assert refs[0].expected_sha256 == "ver_old"
    assert refs[0].source_path == (tmp_path / "a.pptx")


def test_cjk_query_does_not_build_bigrams_across_whitespace(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        first = _insert_version(
            connection,
            deck_id="deck_a",
            version_id="ver_a",
            path=tmp_path / "a.pptx",
            title="A",
            body="收入增长",
        )
        second = _insert_version(
            connection,
            deck_id="deck_b",
            version_id="ver_b",
            path=tmp_path / "b.pptx",
            title="B",
            body="利润增长",
        )
        connection.execute(
            "UPDATE decks SET current_version_id = CASE id "
            "WHEN 'deck_a' THEN 'ver_a' WHEN 'deck_b' THEN 'ver_b' END "
            "WHERE id IN ('deck_a', 'deck_b')"
        )
        results = search_slides(connection, "收入 利润")
        assert [result.slide_id for result in results] == []
        assert search_slides(connection, "收入 增长")[0].slide_id == first
        assert search_slides(connection, "利润 增长")[0].slide_id == second
    finally:
        connection.close()


def test_sqlite_search_filters_taxonomy_and_deck_facets(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        org = _insert_version(
            connection,
            deck_id="deck_org",
            version_id="ver_org",
            path=tmp_path / "org.pptx",
            title="组织与人才",
            body="团队",
        )
        metrics = _insert_version(
            connection,
            deck_id="deck_metrics",
            version_id="ver_metrics",
            path=tmp_path / "metrics.pptx",
            title="收入增长",
            body="数据",
        )
        connection.execute(
            "UPDATE decks SET current_version_id = CASE id "
            "WHEN 'deck_org' THEN 'ver_org' WHEN 'deck_metrics' THEN 'ver_metrics' END "
            "WHERE id IN ('deck_org', 'deck_metrics')"
        )
        connection.execute(
            "INSERT INTO slide_taxonomy(slide_id, topic, page_type, classified_at) "
            "VALUES (?, '组织与人才', '观点与结论', datetime('now'))",
            (org,),
        )
        connection.execute(
            "INSERT INTO slide_taxonomy(slide_id, topic, page_type, classified_at) "
            "VALUES (?, '数据与业绩', '数据图表', datetime('now'))",
            (metrics,),
        )
    finally:
        connection.close()

    catalog = SqliteSlideCatalog(tmp_path / "pages.db")
    page = catalog.search(
        "", page=1, page_size=20, filters=LibraryFilters(topic="组织与人才")
    )
    assert page.total == 1
    assert [item.id for item in page.items] == [org]
    assert catalog.search(
        "", page=1, page_size=20, filters=LibraryFilters(deck_id="deck_metrics")
    ).items[0].id == metrics
    facets = catalog.facets()
    assert [item.id for item in facets.topics]
    assert next(item for item in facets.decks if item.id == "deck_org").count == 1


def test_sqlite_catalog_rejects_unknown_taxonomy_filters(tmp_path: Path) -> None:
    catalog = SqliteSlideCatalog(tmp_path / "pages.db")

    with pytest.raises(AppError) as error:
        catalog.search(
            "", page=1, page_size=20, filters=LibraryFilters(topic="不存在的主题")
        )

    assert error.value.code == ErrorCode.REQUEST_INVALID


def test_sqlite_search_persists_hierarchical_metadata_and_filters_subtopic(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    try:
        slide_id = _insert_version(
            connection,
            deck_id="deck_org",
            version_id="ver_org",
            path=tmp_path / "org.pptx",
            title="绩效指标",
            body="奖金与考核",
        )
        connection.execute("UPDATE decks SET current_version_id = 'ver_org' WHERE id = 'deck_org'")
        connection.execute(
            """
            INSERT INTO slide_taxonomy(
                slide_id, topic, subtopic, page_type, confidence,
                classification_source, classifier_version, classified_at
            ) VALUES (
                ?, '组织与人才', '绩效与激励', '数据图表', 'high', 'manual', 'test', datetime('now')
            )
            """,
            (slide_id,),
        )
    finally:
        connection.close()

    catalog = SqliteSlideCatalog(tmp_path / "pages.db")
    page = catalog.search(
        "", page=1, page_size=20, filters=LibraryFilters(subtopic="绩效与激励")
    )
    assert page.total == 1
    item = page.items[0]
    assert item.subtopic == "绩效与激励"
    assert item.confidence == "high"
    assert item.classification_source == "manual"
    assert catalog.search(
        "", page=1, page_size=20, filters=LibraryFilters(subtopic="客户案例")
    ).total == 0

    facets = catalog.facets()
    org = next(option for option in facets.topics if option.id == "组织与人才")
    assert next(child for child in org.children if child.id == "绩效与激励").count == 1
