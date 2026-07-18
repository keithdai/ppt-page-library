from __future__ import annotations

import sqlite3
from pathlib import Path


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def migrate(connection: sqlite3.Connection, migrations_dir: Path) -> list[str]:
    completed: list[str] = []
    connection.execute("BEGIN IMMEDIATE")
    try:
        _ensure_migration_table(connection)
        for path in sorted(migrations_dir.glob("*.sql")):
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE name = ?", (path.name,)
            ).fetchone()
            if applied is not None:
                continue
            statement = ""
            for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    if statement.strip():
                        connection.execute(statement)
                    statement = ""
            if statement.strip():
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(name, applied_at) VALUES (?, datetime('now'))",
                (path.name,),
            )
            completed.append(path.name)
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    return completed
