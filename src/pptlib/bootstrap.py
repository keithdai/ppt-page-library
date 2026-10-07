from __future__ import annotations

import contextlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pptlib.config import Settings
from pptlib.domain.taxonomy import CLASSIFIER_VERSION, classify_slide_result
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.migrations import migrate, pending_migrations
from pptlib.infrastructure.db.safety import (
    check_database_integrity,
    create_database_backup,
    database_maintenance_lock,
    restore_database_backup,
)
from pptlib.logging import configure_logging


def initialize(settings: Settings) -> list[str]:
    for directory in (
        settings.home,
        settings.assets_dir,
        settings.temp_dir,
        settings.log_dir,
        settings.output_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    configure_logging(settings.log_dir)
    with database_maintenance_lock(settings.home):
        return _initialize_database(settings)


def _initialize_database(settings: Settings) -> list[str]:
    database_existed = settings.database_path.is_file()
    connection = connect(settings.database_path)
    backup_path: Path | None = None
    try:
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        check_database_integrity(connection)
        pending = pending_migrations(connection, migrations_dir)
        if database_existed and pending:
            backup_path = create_database_backup(
                connection,
                settings.database_path,
                settings.home / "backups",
            )
        try:
            completed = migrate(connection, migrations_dir)
            check_database_integrity(connection)
        except Exception:
            connection.close()
            if backup_path is not None:
                restore_database_backup(backup_path, settings.database_path)
            raise
        _backfill_taxonomy(connection)
        return completed
    finally:
        with contextlib.suppress(sqlite3.ProgrammingError):
            connection.close()


def _backfill_taxonomy(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT s.id, d.display_name, s.title, s.body_text, s.notes_text
        FROM slides s
        JOIN deck_versions v ON v.id = s.deck_version_id
        JOIN decks d ON d.id = v.deck_id
        LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
        WHERE v.status = 'parsed' AND d.current_version_id = v.id
          AND (
              t.slide_id IS NULL
              OR (
                  COALESCE(t.classification_source, 'auto') != 'manual'
                  AND (
                      COALESCE(t.classifier_version, '') != ?
                      OR COALESCE(t.subtopic, '') = ''
                  )
              )
          )
        """,
        (CLASSIFIER_VERSION,),
    ).fetchall()
    if not rows:
        return
    now = datetime.now(UTC).isoformat()
    connection.execute("BEGIN")
    try:
        for row in rows:
            classification = classify_slide_result(
                row[1], row[2], " ".join((row[3], row[4]))
            )
            connection.execute(
                """
                INSERT INTO slide_taxonomy(
                    slide_id, topic, subtopic, page_type, confidence,
                    classification_source, classifier_version, classified_at
                ) VALUES (?, ?, ?, ?, ?, 'auto', ?, ?)
                ON CONFLICT(slide_id) DO UPDATE SET
                    topic = excluded.topic,
                    subtopic = excluded.subtopic,
                    page_type = excluded.page_type,
                    confidence = excluded.confidence,
                    classification_source = excluded.classification_source,
                    classifier_version = excluded.classifier_version,
                    classified_at = excluded.classified_at
                WHERE slide_taxonomy.classification_source != 'manual'
                """,
                (
                    row[0],
                    classification.topic,
                    classification.subtopic,
                    classification.page_type,
                    classification.confidence,
                    classification.classifier_version,
                    now,
                ),
            )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
