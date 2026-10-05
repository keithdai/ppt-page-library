from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient

from pptlib.application import import_decks
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect
from pptlib.rendering import thumbnails
from pptlib.web.app import create_app


def _write_fixture(path: Path, *, title: str = "年度复盘", body: str = "收入增长") -> None:
    presentation = (
        "<p:presentation xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' "
        "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
        "<p:sldSz cx='12192000' cy='6858000'/>"
        "<p:sldIdLst><p:sldId id='1' r:id='rId1'/></p:sldIdLst></p:presentation>"
    )
    rels = (
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        "<Relationship Id='rId1' "
        "Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide' "
        "Target='slides/slide1.xml'/></Relationships>"
    )
    slide = (
        "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' "
        "xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'><p:cSld>"
        "<p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type='title'/></p:nvPr></p:nvSpPr>"
        f"<p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp>"
        f"<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody><a:p><a:r><a:t>{body}</a:t>"
        "</a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    content_types = (
        "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
        "<Override PartName='/ppt/presentation.xml' "
        "ContentType='application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml'/>"
        "<Override PartName='/ppt/slides/slide1.xml' "
        "ContentType='application/vnd.openxmlformats-officedocument.presentationml.slide+xml'/>"
        "</Types>"
    )
    with ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("ppt/presentation.xml", presentation)
        package.writestr("ppt/_rels/presentation.xml.rels", rels)
        package.writestr("ppt/slides/slide1.xml", slide)


def _stub_render(monkeypatch) -> None:
    def fake_render(_source_path, version_id, slide_count, **kwargs):
        assets_dir = Path(kwargs["assets_dir"])
        for page in range(1, slide_count + 1):
            for kind in ("thumbnails", "previews"):
                target = assets_dir / kind / f"{version_id}_s{page:05d}.jpg"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"jpeg")
        thumbnails._write_render_marker(assets_dir, version_id, "test")
        return slide_count

    monkeypatch.setattr(import_decks, "render_deck_thumbnails", fake_render)


def test_real_import_search_and_persistent_selection(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    _write_fixture(source / "annual.pptx")
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    initialize(settings)
    _stub_render(monkeypatch)
    client = TestClient(create_app(settings))

    imported = client.post("/api/v1/imports", json={"root": str(source)})
    assert imported.status_code == 200
    assert imported.json()["data"]["imported"][0]["slide_count"] == 1

    search = client.get("/api/v1/slides", params={"q": "收入"})
    assert search.status_code == 200
    slide_id = search.json()["data"]["items"][0]["id"]
    added = client.post("/api/v1/selection/items", json={"slide_id": slide_id})
    assert added.json()["data"]["count"] == 1

    exported = client.post("/api/v1/exports", json={})
    assert exported.status_code == 200
    export_data = exported.json()["data"]
    assert export_data["page_count"] == 1
    assert Path(export_data["output_path"]).exists()
    assert Path(export_data["manifest_path"]).exists()
    assert export_data["download_url"].startswith("/api/v1/exports/")
    downloaded = client.get(export_data["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    restarted = TestClient(create_app(settings))
    persisted = restarted.get("/api/v1/selection")
    assert persisted.json()["data"]["count"] == 1


def test_upload_import_accepts_selected_pptx_files(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    fixture = source / "annual.pptx"
    _write_fixture(fixture)
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    initialize(settings)

    _stub_render(monkeypatch)
    client = TestClient(create_app(settings))

    uploaded = client.post(
        "/api/v1/imports/files",
        files={
            "files": (
                "annual.pptx",
                fixture.read_bytes(),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )

    assert uploaded.status_code == 200
    data = uploaded.json()["data"]
    assert data["imported"][0]["slide_count"] == 1
    assert client.get("/api/v1/slides", params={"q": "收入"}).json()["data"]["total"] == 1

    uploaded_again = client.post(
        "/api/v1/imports/files",
        files={
            "files": (
                "annual.pptx",
                fixture.read_bytes(),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )
    assert uploaded_again.status_code == 200
    duplicate_data = uploaded_again.json()["data"]
    assert duplicate_data["skipped"] == 1
    assert client.get("/api/v1/slides", params={"q": "收入"}).json()["data"]["total"] == 1


def test_upload_import_rejects_file_over_configured_limit(tmp_path: Path) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    fixture = source / "annual.pptx"
    _write_fixture(fixture)
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
            "PPTLIB_MAX_FILE_BYTES": "10",
        }
    )
    initialize(settings)
    client = TestClient(create_app(settings))

    uploaded = client.post(
        "/api/v1/imports/files",
        files={"files": ("annual.pptx", fixture.read_bytes(), "application/octet-stream")},
    )

    assert uploaded.status_code == 409
    assert uploaded.json()["error"]["message"] == "文件超过大小限制：annual.pptx"


def test_export_rejects_source_changed_after_selection(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    deck = source / "annual.pptx"
    _write_fixture(deck)
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    initialize(settings)
    _stub_render(monkeypatch)
    client = TestClient(create_app(settings))

    assert client.post("/api/v1/imports", json={"root": str(source)}).status_code == 200
    slide_id = client.get("/api/v1/slides", params={"q": "收入"}).json()["data"]["items"][0]["id"]
    assert client.post("/api/v1/selection/items", json={"slide_id": slide_id}).status_code == 200
    deck.write_bytes(deck.read_bytes() + b"changed")

    exported = client.post("/api/v1/exports", json={})
    assert exported.status_code == 409
    assert exported.json()["error"]["code"] == "SOURCE_CHANGED"


def test_real_library_supports_taxonomy_and_source_filters(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    _write_fixture(source / "org.pptx", title="组织与人才", body="团队招聘")
    _write_fixture(source / "metrics.pptx", title="收入增长", body="数据指标")
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    initialize(settings)
    _stub_render(monkeypatch)
    client = TestClient(create_app(settings))
    imported = client.post("/api/v1/imports", json={"root": str(source)})
    assert imported.status_code == 200

    org = client.get("/api/v1/slides", params={"topic": "组织与人才"})
    assert org.status_code == 200
    assert org.json()["data"]["total"] == 1
    org_item = org.json()["data"]["items"][0]
    assert org_item["deck_name"] == "org"

    by_deck = client.get("/api/v1/slides", params={"deck_id": org_item["deck_id"]})
    assert by_deck.status_code == 200
    assert by_deck.json()["data"]["total"] == 1

    facets = client.get("/api/v1/library/facets")
    assert facets.status_code == 200
    deck_facets = facets.json()["data"]["decks"]
    assert len(deck_facets) == 2
    assert all(item["count"] == 1 for item in deck_facets)


def test_real_library_source_view_lists_decks_then_scopes_pages(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    _write_fixture(source / "org.pptx", title="组织与人才", body="团队招聘")
    _write_fixture(source / "metrics.pptx", title="收入增长", body="数据指标")
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    initialize(settings)
    _stub_render(monkeypatch)
    client = TestClient(create_app(settings))
    assert client.post("/api/v1/imports", json={"root": str(source)}).status_code == 200

    index = client.get("/library", params={"view": "source"})
    assert index.status_code == 200
    assert 'class="deck-index"' in index.text
    assert "org" in index.text
    assert "metrics" in index.text
    assert index.text.count('class="deck-card"') == 2

    deck_id = client.get("/api/v1/slides", params={"q": "团队"}).json()["data"]["items"][0][
        "deck_id"
    ]
    detail = client.get("/library", params={"view": "source", "deck_id": deck_id})
    assert detail.status_code == 200
    assert 'class="deck-detail"' in detail.text
    assert 'class="slide-grid"' in detail.text
    assert "组织与人才" in detail.text
    assert "收入增长" not in detail.text


def test_initialize_backfills_hierarchical_taxonomy_idempotently(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    _write_fixture(source / "metrics.pptx", title="收入增长", body="数据指标")
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    initialize(settings)
    _stub_render(monkeypatch)
    client = TestClient(create_app(settings))
    assert client.post("/api/v1/imports", json={"root": str(source)}).status_code == 200

    connection = connect(settings.database_path)
    try:
        slide_id = connection.execute("SELECT id FROM slides LIMIT 1").fetchone()[0]
        connection.execute(
            """
            UPDATE slide_taxonomy
            SET topic = '数据与业绩', subtopic = '', confidence = 'low',
                classification_source = 'auto', classifier_version = ''
            WHERE slide_id = ?
            """,
            (slide_id,),
        )
    finally:
        connection.close()

    initialize(settings)
    initialize(settings)
    connection = connect(settings.database_path)
    try:
        row = connection.execute(
            "SELECT topic, subtopic, confidence, classification_source, classifier_version "
            "FROM slide_taxonomy WHERE slide_id = ?",
            (slide_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row[0] == "数据与经营"
    assert row[1]
    assert row[2] in {"low", "medium", "high"}
    assert row[3] == "auto"
    assert row[4]
