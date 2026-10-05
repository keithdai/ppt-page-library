"""Export the local slide catalog and sync it to a Miaoda (妙搭) app.

The desktop client and CLI use this to publish thumbnails + slide metadata so
that page selection can happen in the Miaoda web app while the heavy source
PPTX files stay local. Two concerns are separated:

- :func:`build_catalog` reads the local SQLite index into a plain, portable
  dict (``catalog.json``) with a stable schema. It never touches the network.
- :class:`MiaodaSync` drives ``lark-cli apps`` to upload thumbnails to the
  app's file storage and upsert metadata rows into the app database. It shells
  out to the already-authenticated local ``lark-cli`` and supports ``dry_run``.

The composition bridge is deliberate: Miaoda holds only ``slide_id`` +
metadata + a thumbnail. Selection in the web app produces an ordered list of
``slide_id`` (a manifest) which :mod:`pptlib.application.compose` resolves back
to local source PPTX pages.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pptlib.config import Settings
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.infrastructure.db.connection import connect

CATALOG_SCHEMA_VERSION = "catalog-v1"
CATALOG_TABLE = "slides_catalog"


@dataclass(frozen=True, slots=True)
class CatalogSlide:
    slide_id: str
    deck_id: str
    deck_name: str
    source_path: str
    slide_number: int
    title: str
    summary: str
    search_text: str
    topic: str
    subtopic: str
    page_type: str
    confidence: str
    classification_source: str
    thumbnail_file: str  # relative path within the catalog bundle
    source_sha256: str
    source_format: str = "pptx"
    page_key: str = ""
    page_kind: str = "ooxml"
    capabilities: dict[str, object] = field(default_factory=dict)
    warnings: list[object] = field(default_factory=list)

    def to_row(self, *, include_local_fields: bool = False) -> dict[str, object]:
        row = asdict(self)
        if not include_local_fields:
            row.pop("source_path")
            row.pop("search_text")
        return row


@dataclass(frozen=True, slots=True)
class Catalog:
    schema_version: str
    generated_at: str
    slide_count: int
    slides: list[CatalogSlide] = field(default_factory=list)

    def to_dict(self, *, include_local_fields: bool = False) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "slide_count": self.slide_count,
            "slides": [
                slide.to_row(include_local_fields=include_local_fields)
                for slide in self.slides
            ],
        }


def _summary_text(body: str, notes: str, limit: int = 400) -> str:
    text = "\n".join(part for part in (body, notes) if part).strip()
    return text[:limit]


def build_catalog(settings: Settings) -> Catalog:
    """Read the local index into a portable catalog of current parsed slides."""
    from datetime import UTC, datetime

    if not settings.database_path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "本地索引数据库不存在，请先导入 PPTX")
    connection = connect(settings.database_path)
    try:
        rows = connection.execute(
            """
            SELECT s.id AS slide_id, d.id AS deck_id, d.display_name, d.canonical_path,
                   s.slide_number, s.title, s.body_text, s.notes_text,
                   v.sha256,
                   COALESCE(t.topic, '') AS topic,
                   COALESCE(t.subtopic, '') AS subtopic,
                   COALESCE(t.page_type, '') AS page_type,
                   COALESCE(t.confidence, '') AS confidence,
                   COALESCE(t.classification_source, '') AS classification_source,
                   v.source_format, s.page_key, s.page_kind,
                   s.capabilities_json, v.warnings_json
            FROM slides s
            JOIN deck_versions v ON v.id = s.deck_version_id
            JOIN decks d ON d.id = v.deck_id
            LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
            WHERE v.status = 'parsed' AND d.current_version_id = v.id
            ORDER BY d.display_name, s.slide_number
            """
        ).fetchall()
    finally:
        connection.close()

    slides: list[CatalogSlide] = []
    for row in rows:
        slide_id = str(row["slide_id"])
        body_text = str(row["body_text"] or "")
        notes_text = str(row["notes_text"] or "")
        slides.append(
            CatalogSlide(
                slide_id=slide_id,
                deck_id=str(row["deck_id"]),
                deck_name=str(row["display_name"]),
                source_path=str(row["canonical_path"]),
                slide_number=int(row["slide_number"]),
                title=str(row["title"] or ""),
                summary=_summary_text(body_text, notes_text),
                search_text="\n".join(part for part in (body_text, notes_text) if part),
                topic=str(row["topic"]),
                subtopic=str(row["subtopic"]),
                page_type=str(row["page_type"]),
                confidence=str(row["confidence"]),
                classification_source=str(row["classification_source"]),
                thumbnail_file=f"thumbnails/{slide_id}.jpg",
                source_sha256=str(row["sha256"] or ""),
                source_format=str(row["source_format"]),
                page_key=str(row["page_key"]),
                page_kind=str(row["page_kind"]),
                capabilities=json.loads(row["capabilities_json"]),
                warnings=json.loads(row["warnings_json"]),
            )
        )
    return Catalog(
        schema_version=CATALOG_SCHEMA_VERSION,
        generated_at=datetime.now(UTC).isoformat(),
        slide_count=len(slides),
        slides=slides,
    )


def write_catalog_bundle(
    settings: Settings,
    output_dir: Path,
    *,
    include_local_fields: bool = False,
) -> Path:
    """Write catalog.json next to the thumbnails it references.

    Thumbnails are not copied; ``catalog.json`` records a relative path and the
    caller (sync/desktop) reads them from ``settings.assets_dir``. Returns the
    catalog.json path.
    """
    catalog = build_catalog(settings)
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = output_dir / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            catalog.to_dict(include_local_fields=include_local_fields),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return catalog_path


# --------------------------------------------------------------------------- #
# Miaoda sync driver (lark-cli apps)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SyncResult:
    app_id: str
    dry_run: bool
    slide_count: int
    thumbnails_uploaded: int
    rows_upserted: int
    commands: list[str] = field(default_factory=list)


class MiaodaSyncError(RuntimeError):
    pass


class MiaodaSync:
    """Publish the catalog to a Miaoda full_stack app via the local lark-cli.

    Assumes the local ``lark-cli`` is already authenticated as the correct
    user/tenant. Thumbnails go to app file storage; metadata rows are upserted
    into ``slides_catalog`` via ``apps +db-execute``.
    """

    def __init__(
        self,
        app_id: str,
        *,
        lark_cli: str = "lark-cli",
        environment: str = "online",
        dry_run: bool = False,
    ) -> None:
        self.app_id = app_id
        self.lark_cli = lark_cli
        self.environment = environment
        self.dry_run = dry_run
        self._log: list[str] = []

    def sync(self, settings: Settings) -> SyncResult:
        catalog = build_catalog(settings)
        self._log = []
        self.ensure_table()
        existing = {} if self.dry_run else self._fetch_existing()
        uploaded = 0
        pending: list[tuple[CatalogSlide, str]] = []
        for slide in catalog.slides:
            prior = existing.get(slide.slide_id)
            thumb = settings.assets_dir / "thumbnails" / f"{slide.slide_id}.jpg"
            # Skip work when the source is unchanged and a thumbnail is already
            # published: re-sync stays cheap and storage does not accumulate
            # duplicate uploads.
            if (
                prior is not None
                and prior.get("source_sha256") == slide.source_sha256
                and prior.get("thumbnail_path")
            ):
                continue
            thumbnail_url = ""
            if thumb.is_file():
                thumbnail_url = self.upload_thumbnail(thumb)
                uploaded += 1
            pending.append((slide, thumbnail_url))
        upserted = self._upsert_rows(pending)
        return SyncResult(
            app_id=self.app_id,
            dry_run=self.dry_run,
            slide_count=catalog.slide_count,
            thumbnails_uploaded=uploaded,
            rows_upserted=upserted,
            commands=list(self._log),
        )

    def _fetch_existing(self) -> dict[str, dict[str, str]]:
        """Read published rows so an incremental sync can skip unchanged slides."""
        data = self._db_query(
            f"SELECT slide_id, source_sha256, thumbnail_path FROM {CATALOG_TABLE};"
        )
        result: dict[str, dict[str, str]] = {}
        for row in data:
            if isinstance(row, dict) and row.get("slide_id"):
                result[str(row["slide_id"])] = {
                    "source_sha256": str(row.get("source_sha256") or ""),
                    "thumbnail_path": str(row.get("thumbnail_path") or ""),
                }
        return result

    def ensure_table(self) -> None:
        # Miaoda forbids DDL in the online environment: the table must be
        # provisioned in dev and published via `+db-env-migrate`. Only attempt
        # CREATE TABLE when syncing against dev.
        if self.environment != "dev":
            return
        ddl = (
            f"CREATE TABLE IF NOT EXISTS {CATALOG_TABLE} ("
            "slide_id TEXT PRIMARY KEY,"
            "deck_id TEXT NOT NULL,"
            "deck_name TEXT NOT NULL,"
            "slide_number INTEGER NOT NULL,"
            "title TEXT,"
            "summary TEXT,"
            "topic TEXT,"
            "subtopic TEXT,"
            "page_type TEXT,"
            "confidence TEXT,"
            "classification_source TEXT,"
            "thumbnail_path TEXT,"
            "source_sha256 TEXT,"
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ");"
        )
        self._db_execute(ddl)

    def upload_thumbnail(self, thumbnail: Path) -> str:
        """Upload a thumbnail; return the app-storage download URL (or '').

        ``lark-cli apps +file-upload`` rejects absolute ``--file`` paths, so we
        run it from the thumbnail's directory and pass the bare filename.
        """
        out = self._run(
            [
                self.lark_cli,
                "apps",
                "+file-upload",
                "--app-id",
                self.app_id,
                "--file",
                thumbnail.name,
                "--as",
                "user",
            ],
            cwd=thumbnail.parent,
        )
        if self.dry_run or not out:
            return ""
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            return ""
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        if isinstance(data, dict):
            return str(data.get("download_url") or data.get("path") or "")
        return ""

    def _upsert_rows(self, rows: list[tuple[CatalogSlide, str]]) -> int:
        """Upsert slides in batches; returns the number of rows written.

        ``thumbnail_path`` stores the Miaoda storage URL when the thumbnail was
        uploaded this run, otherwise the catalog-relative path so a re-sync can
        still resolve it.
        """
        if not rows:
            return 0
        statements = [self._upsert_sql(slide, url) for slide, url in rows]
        # Chunk to keep each db-execute payload small and resilient.
        batch = 50
        for start in range(0, len(statements), batch):
            self._db_execute("\n".join(statements[start : start + batch]))
        return len(rows)

    def _upsert_sql(self, slide: CatalogSlide, thumbnail_url: str) -> str:
        thumbnail_path = thumbnail_url or slide.thumbnail_file
        return (
            f"INSERT INTO {CATALOG_TABLE} "
            "(slide_id, deck_id, deck_name, slide_number, title, summary, topic, "
            "subtopic, page_type, confidence, classification_source, thumbnail_path, "
            "source_sha256) VALUES ("
            f"{_sql_str(slide.slide_id)}, {_sql_str(slide.deck_id)}, "
            f"{_sql_str(slide.deck_name)}, {slide.slide_number}, {_sql_str(slide.title)}, "
            f"{_sql_str(slide.summary)}, {_sql_str(slide.topic)}, {_sql_str(slide.subtopic)}, "
            f"{_sql_str(slide.page_type)}, {_sql_str(slide.confidence)}, "
            f"{_sql_str(slide.classification_source)}, {_sql_str(thumbnail_path)}, "
            f"{_sql_str(slide.source_sha256)}) "
            "ON CONFLICT(slide_id) DO UPDATE SET "
            "deck_id=excluded.deck_id, deck_name=excluded.deck_name, "
            "slide_number=excluded.slide_number, title=excluded.title, "
            "summary=excluded.summary, topic=excluded.topic, subtopic=excluded.subtopic, "
            "page_type=excluded.page_type, confidence=excluded.confidence, "
            "classification_source=excluded.classification_source, "
            "thumbnail_path=excluded.thumbnail_path, source_sha256=excluded.source_sha256;"
        )

    def _db_query(self, sql: str) -> list[dict[str, object]]:
        out = self._run(
            [
                self.lark_cli,
                "apps",
                "+db-execute",
                "--app-id",
                self.app_id,
                "--environment",
                self.environment,
                "--sql",
                sql,
                "--as",
                "user",
                "--yes",
            ]
        )
        if self.dry_run or not out:
            return []
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            return []
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return data if isinstance(data, list) else []

    def _db_execute(self, sql: str) -> None:
        self._run(
            [
                self.lark_cli,
                "apps",
                "+db-execute",
                "--app-id",
                self.app_id,
                "--environment",
                self.environment,
                "--sql",
                sql,
                "--as",
                "user",
                "--yes",
            ]
        )

    def _run(self, command: list[str], *, cwd: Path | None = None) -> str:
        self._log.append(" ".join(shlex.quote(part) for part in command))
        if self.dry_run:
            return ""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(cwd) if cwd is not None else None,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise MiaodaSyncError(str(error)) from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "lark-cli command failed").strip()
            raise MiaodaSyncError(detail[-500:])
        return result.stdout


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
