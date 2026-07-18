from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.migrations import migrate


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
    ]
    assert second == []
    assert {"schema_migrations", "jobs", "job_stages"}.issubset(table_names)
    assert "slide_taxonomy" in table_names
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
    }.issubset(
        taxonomy_indexes
    )
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(slide_taxonomy)").fetchall()
    }
    assert {
        "subtopic",
        "confidence",
        "classification_source",
        "classifier_version",
    }.issubset(columns)


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
        ],
    ]
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 5
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
    ).fetchone()
