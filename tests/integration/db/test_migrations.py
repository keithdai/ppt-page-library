from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import copyfile

from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.migrations import migrate


def test_connect_configures_concurrent_access_pragmas(tmp_path: Path) -> None:
    connection = connect(tmp_path / "pages.db")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_migrate_creates_foundation_schema_and_is_idempotent(tmp_path: Path) -> None:
    connection = connect(tmp_path / "pages.db")
    migrations_dir = Path(__file__).resolve().parents[3] / "src" / "pptlib" / "migrations"

    first = migrate(connection, migrations_dir)
    second = migrate(connection, migrations_dir)
    table_names = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    assert first == [
        "0001_foundation.sql",
        "0002_ingestion.sql",
        "0003_selection.sql",
        "0003_taxonomy.sql",
        "0004_hierarchical_taxonomy.sql",
        "0005_slide_fingerprints.sql",
        "0006_source_file_identity.sql",
        "0007_deck_version_sha256_index.sql",
        "0008_html_assets.sql",
        "0009_scan_plans.sql",
        "0010_scan_run_schedule.sql",
    ]
    assert second == []
    assert {
        "schema_migrations",
        "jobs",
        "job_stages",
        "scan_plans",
        "scan_plan_roots",
        "scan_runs",
        "scan_run_items",
    }.issubset(table_names)
    assert "slide_taxonomy" in table_names
    assert "slide_fingerprints" in table_names
    taxonomy_indexes = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'slide_taxonomy'"
        ).fetchall()
    }
    assert {
        "idx_slide_taxonomy_topic",
        "idx_slide_taxonomy_page_type",
        "idx_slide_taxonomy_subtopic",
    }.issubset(taxonomy_indexes)
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(slide_taxonomy)").fetchall()
    }
    assert {
        "subtopic",
        "confidence",
        "classification_source",
        "classifier_version",
    }.issubset(columns)
    version_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(deck_versions)").fetchall()
    }
    assert {
        "ctime_ns",
        "source_format",
        "canonical_format",
        "dependencies_json",
        "capabilities_json",
        "warnings_json",
        "renderer_version",
    }.issubset(version_columns)
    slide_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(slides)").fetchall()
    }
    assert {
        "page_key",
        "page_kind",
        "composition_ref_json",
        "capabilities_json",
    }.issubset(slide_columns)
    version_indexes = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'deck_versions'"
        ).fetchall()
    }
    assert "idx_deck_versions_sha256" in version_indexes
    assert "idx_deck_versions_source_format" in version_indexes


def test_concurrent_migrate_calls_apply_once_and_preserve_jobs_table(tmp_path: Path) -> None:
    database = tmp_path / "concurrent.db"
    migrations_dir = Path(__file__).resolve().parents[3] / "src" / "pptlib" / "migrations"

    def run() -> list[str]:
        connection = connect(database)
        try:
            return migrate(connection, migrations_dir)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: run(), range(2)))
    connection = connect(database)
    assert sorted(results) == [
        [],
        [
            "0001_foundation.sql",
            "0002_ingestion.sql",
            "0003_selection.sql",
            "0003_taxonomy.sql",
            "0004_hierarchical_taxonomy.sql",
            "0005_slide_fingerprints.sql",
            "0006_source_file_identity.sql",
            "0007_deck_version_sha256_index.sql",
            "0008_html_assets.sql",
            "0009_scan_plans.sql",
            "0010_scan_run_schedule.sql",
        ],
    ]
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 11
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
    ).fetchone()


def test_html_migration_preserves_existing_pptx_rows_and_selection(tmp_path: Path) -> None:
    migrations_dir = Path(__file__).resolve().parents[3] / "src" / "pptlib" / "migrations"
    legacy_dir = tmp_path / "legacy_migrations"
    legacy_dir.mkdir()
    for path in migrations_dir.glob("*.sql"):
        if path.name < "0008_html_assets.sql":
            copyfile(path, legacy_dir / path.name)
    connection = connect(tmp_path / "legacy.db")
    try:
        migrate(connection, legacy_dir)
        connection.execute(
            """
            INSERT INTO decks(id, canonical_path, display_name, current_version_id,
                              created_at, updated_at)
            VALUES ('deck_old', '/source.pptx', 'Source', 'ver_old', 'then', 'then')
            """
        )
        connection.execute(
            """
            INSERT INTO deck_versions(id, deck_id, sha256, size_bytes, mtime_ns,
                                      parser_version, slide_count, status, created_at, updated_at)
            VALUES ('ver_old', 'deck_old', 'digest', 123, 456,
                    'parser', 1, 'parsed', 'then', 'then')
            """
        )
        connection.execute(
            """
            INSERT INTO slides(id, deck_version_id, slide_number, title, created_at)
            VALUES ('slide_old', 'ver_old', 1, 'Legacy', 'then')
            """
        )
        connection.execute("INSERT INTO slide_fts(slide_id, title) VALUES ('slide_old', 'Legacy')")
        connection.execute(
            "INSERT INTO selections(id, name, created_at, updated_at) "
            "VALUES ('sel_old', 'Selection', 'then', 'then')"
        )
        connection.execute(
            """
            INSERT INTO selection_items(id, selection_id, slide_id, deck_version_id,
                                        source_page_number, sort_order, created_at)
            VALUES ('item_old', 'sel_old', 'slide_old', 'ver_old', 1, 1, 'then')
            """
        )
        tables = ("decks", "deck_versions", "slides", "slide_fts", "selections", "selection_items")
        before = {
            table: dict(connection.execute(f"SELECT * FROM {table}").fetchone()) for table in tables
        }

        assert migrate(connection, migrations_dir) == [
            "0008_html_assets.sql",
            "0009_scan_plans.sql",
            "0010_scan_run_schedule.sql",
        ]
        assert migrate(connection, migrations_dir) == []
        for table, old_row in before.items():
            row = dict(connection.execute(f"SELECT * FROM {table}").fetchone())
            assert {key: row[key] for key in old_row} == old_row

        version = connection.execute("SELECT * FROM deck_versions").fetchone()
        assert version["source_format"] == "pptx"
        assert version["canonical_format"] == "pptx_package"
        assert version["dependencies_json"] == "[]"
        assert version["capabilities_json"] == "{}"
        assert version["warnings_json"] == "[]"
        assert version["renderer_version"] == ""
        slide = connection.execute("SELECT * FROM slides").fetchone()
        assert slide["page_key"] == ""
        assert slide["page_kind"] == "ooxml"
        assert slide["composition_ref_json"] == "{}"
        assert slide["capabilities_json"] == "{}"
        assert (
            connection.execute(
                "SELECT slide_id FROM slide_fts WHERE slide_fts MATCH 'Legacy'"
            ).fetchone()[0]
            == "slide_old"
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
