from __future__ import annotations

import json
from pathlib import Path

from pptlib.application.catalog import (
    CATALOG_TABLE,
    Catalog,
    CatalogSlide,
    MiaodaSync,
    _sql_str,
    build_catalog,
)
from pptlib.config import load_settings


def _slide(slide_id: str = "ver_x_s00001") -> CatalogSlide:
    return CatalogSlide(
        slide_id=slide_id,
        deck_id="deck_x",
        deck_name="Deck O'Brien",
        source_path="/tmp/Deck O'Brien.pptx",
        slide_number=1,
        title="标题",
        summary="正文摘要",
        search_text="正文摘要和完整备注",
        topic="战略与增长",
        subtopic="业务规划与增长",
        page_type="观点与结论",
        confidence="high",
        classification_source="auto",
        thumbnail_file=f"thumbnails/{slide_id}.jpg",
        source_sha256="abc123",
    )


def test_sql_str_escapes_single_quotes() -> None:
    assert _sql_str("O'Brien") == "'O''Brien'"


def test_catalog_to_dict_round_trips() -> None:
    catalog = Catalog(
        schema_version="catalog-v1",
        generated_at="2026-09-19T00:00:00+00:00",
        slide_count=1,
        slides=[_slide()],
    )
    payload = catalog.to_dict(include_local_fields=True)
    assert payload["slide_count"] == 1
    assert payload["slides"][0]["slide_id"] == "ver_x_s00001"
    assert payload["slides"][0]["source_path"] == "/tmp/Deck O'Brien.pptx"
    assert payload["slides"][0]["search_text"] == "正文摘要和完整备注"
    # portable: survives a JSON round trip
    assert json.loads(json.dumps(payload, ensure_ascii=False))["slides"][0]["title"] == "标题"


def test_catalog_excludes_local_fields_by_default() -> None:
    slide = Catalog(
        schema_version="catalog-v1",
        generated_at="2026-09-19T00:00:00+00:00",
        slide_count=1,
        slides=[_slide()],
    ).to_dict()["slides"][0]

    assert "source_path" not in slide
    assert "search_text" not in slide


def test_build_catalog_keeps_full_text_for_local_search(tmp_path: Path) -> None:
    from pptlib.infrastructure.db.connection import connect

    home = tmp_path / "home"
    settings = load_settings({"PPTLIB_HOME": str(home)})
    _seed_minimal_db(settings)
    full_text = "前段" * 220 + "后半段唯一关键词"
    connection = connect(settings.database_path)
    try:
        connection.execute(
            "UPDATE slides SET body_text = ?, content_text = ? WHERE id = 'ver_x_s00001'",
            (full_text, full_text),
        )
    finally:
        connection.close()

    slide = build_catalog(settings).slides[0]
    assert len(slide.summary) == 400
    assert slide.search_text == full_text
    assert "后半段唯一关键词" in slide.search_text


def test_dry_run_sync_records_commands_without_executing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assets = home / "assets" / "thumbnails"
    assets.mkdir(parents=True)
    (assets / "ver_x_s00001.jpg").write_bytes(b"jpeg")

    # Minimal DB with one parsed slide so build_catalog succeeds.
    settings = load_settings({"PPTLIB_HOME": str(home)})
    _seed_minimal_db(settings)

    syncer = MiaodaSync("app_test", dry_run=True)
    result = syncer.sync(settings)

    assert result.dry_run is True
    assert result.app_id == "app_test"
    assert result.slide_count == 1
    assert result.thumbnails_uploaded == 1
    assert result.rows_upserted == 1
    joined = "\n".join(result.commands)
    assert "+file-upload" in joined
    assert "+db-execute" in joined
    assert CATALOG_TABLE in joined
    # Online is the default; DDL is not attempted there (Miaoda forbids it).
    assert "CREATE TABLE IF NOT EXISTS" not in joined


def test_dev_dry_run_emits_ddl(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "assets" / "thumbnails").mkdir(parents=True)
    settings = load_settings({"PPTLIB_HOME": str(home)})
    _seed_minimal_db(settings)

    syncer = MiaodaSync("app_test", environment="dev", dry_run=True)
    result = syncer.sync(settings)
    joined = "\n".join(result.commands)
    assert "CREATE TABLE IF NOT EXISTS" in joined


def _seed_minimal_db(settings) -> None:
    from datetime import UTC, datetime

    from pptlib.bootstrap import initialize
    from pptlib.infrastructure.db.connection import connect

    initialize(settings)
    now = datetime.now(UTC).isoformat()
    connection = connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO decks(id, canonical_path, display_name, current_version_id,"
            " created_at, updated_at) VALUES ('deck_x', '/tmp/x.pptx', 'Deck', 'ver_x', ?, ?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO deck_versions(id, deck_id, sha256, size_bytes, mtime_ns,"
            " parser_version, slide_count, status, created_at, updated_at)"
            " VALUES ('ver_x', 'deck_x', 'abc123', 1, 1, 'p', 1, 'parsed', ?, ?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO slides(id, deck_version_id, slide_number, title, body_text,"
            " notes_text, content_text, created_at)"
            " VALUES ('ver_x_s00001', 'ver_x', 1, '标题', '正文', '', '标题 正文', ?)",
            (now,),
        )
    finally:
        connection.close()
