from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pptlib.application.library import (
    DEFAULT_LIBRARY_FILTERS,
    LEGACY_TOPIC_ALIASES,
    FacetOption,
    LibraryFacets,
    LibraryFilters,
)
from pptlib.discovery.scanner import ScannedFile
from pptlib.domain.taxonomy import (
    PAGE_TYPES,
    TOPIC_TREE,
    TOPICS,
    classify_slide_result,
)
from pptlib.ingestion.parser import ParsedDeck


def _now() -> str:
    return datetime.now(UTC).isoformat()


def deck_id_for(path: Path) -> str:
    return "deck_" + hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]


def version_id_for(deck_id: str, sha256: str) -> str:
    return "ver_" + hashlib.sha256(f"{deck_id}:{sha256}".encode("ascii")).hexdigest()[:32]


def _analyze(value: str) -> str:
    """Make FTS5 useful for CJK while retaining normal Unicode word search."""
    if not value:
        return ""
    chars = [char for char in value if not char.isspace()]
    grams = [
        "".join(chars[index : index + 2])
        for index in range(len(chars) - 1)
        if all(_is_cjk(c) for c in chars[index : index + 2])
    ]
    return f"{value} {' '.join(grams)}" if grams else value


def _is_cjk(value: str) -> bool:
    return "\u3400" <= value <= "\u9fff" or "\uf900" <= value <= "\ufaff"


def _is_section_marker(title: str, body: str, notes: str) -> bool:
    """Recognize a short title-only slide as a section context marker.

    This intentionally errs on the conservative side: a title with body or
    notes is always treated as a normal content slide, and sentence-like
    punctuation prevents accidental context propagation.
    """

    normalized = " ".join(title.split())
    if not normalized or body.strip() or notes.strip() or len(normalized) > 32:
        return False
    punctuation = ("。", "，", ",", "；", ";", "：", ":", "！", "!", "？", "?")
    return not any(mark in normalized for mark in punctuation)


@dataclass(frozen=True, slots=True)
class ImportedDeck:
    deck_id: str
    version_id: str
    path: Path
    slide_count: int
    created: bool
    action: str = "created"
    obsolete_slide_ids: tuple[str, ...] = ()
    obsolete_version_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CurrentDeck:
    deck_id: str
    version_id: str
    path: Path
    sha256: str
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    slide_count: int


@dataclass(frozen=True, slots=True)
class SearchResult:
    slide_id: str
    deck_id: str
    version_id: str
    path: Path
    slide_number: int
    title: str
    body_text: str
    notes_text: str
    rank: float
    topic: str = ""
    page_type: str = ""
    subtopic: str = ""
    confidence: str = ""
    classification_source: str = ""
    classifier_version: str = ""
    source_format: str = "pptx"
    page_key: str = ""
    page_kind: str = "ooxml"
    capabilities: dict[str, object] = field(default_factory=dict)
    warnings: list[object] = field(default_factory=list)


def _validate_dependencies(raw: str) -> None:
    """Keep source locators and digests in SQLite, never inline media or HTML."""
    dependencies = json.loads(raw)
    if not isinstance(dependencies, list) or any(
        not isinstance(dependency, dict) for dependency in dependencies
    ):
        raise ValueError("dependencies_json must be a list of metadata objects")
    payload_keys = {"base64", "data", "payload", "html", "content", "bytes", "media_payload"}
    pending: list[object] = list(dependencies)
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if payload_keys.intersection(key.casefold() for key in value):
                raise ValueError("dependencies_json must contain metadata only, not media payloads")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and value.lstrip().casefold().startswith("data:"):
            raise ValueError("dependencies_json must contain locators, not inline data URIs")


def _prune_unreferenced_versions(
    connection: sqlite3.Connection,
    deck_id: str,
    current_version_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    version_ids = tuple(
        str(row[0])
        for row in connection.execute(
            """
            SELECT v.id
            FROM deck_versions v
            WHERE v.deck_id = ?
              AND v.id <> ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM selection_items i
                  JOIN slides s ON s.id = i.slide_id
                  WHERE s.deck_version_id = v.id
              )
            ORDER BY v.id
            """,
            (deck_id, current_version_id),
        ).fetchall()
    )
    if not version_ids:
        return (), ()
    placeholders = ",".join("?" * len(version_ids))
    slide_ids = tuple(
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT id FROM slides
            WHERE deck_version_id IN ({placeholders})
            ORDER BY id
            """,
            version_ids,
        ).fetchall()
    )
    for slide_id in slide_ids:
        connection.execute("DELETE FROM slide_fts WHERE slide_id = ?", (slide_id,))
    connection.execute(
        f"DELETE FROM deck_versions WHERE id IN ({placeholders})",
        version_ids,
    )
    return slide_ids, version_ids


class DeckRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _current_deck(row: sqlite3.Row) -> CurrentDeck:
        return CurrentDeck(
            deck_id=str(row[0]),
            version_id=str(row[1]),
            path=Path(str(row[2])),
            sha256=str(row[3]),
            size_bytes=int(row[4]),
            mtime_ns=int(row[5]),
            ctime_ns=int(row[6]),
            slide_count=int(row[7]),
        )

    def current_for_path(self, path: Path) -> CurrentDeck | None:
        row = self.connection.execute(
            """
            SELECT d.id, v.id, d.canonical_path, v.sha256, v.size_bytes,
                   v.mtime_ns, v.ctime_ns, v.slide_count
            FROM decks d
            JOIN deck_versions v ON v.id = d.current_version_id
            WHERE d.canonical_path = ?
            """,
            (str(path.resolve()),),
        ).fetchone()
        if row is None:
            return None
        return self._current_deck(row)

    def current_for_sha256(self, digest: str) -> tuple[CurrentDeck, ...]:
        rows = self.connection.execute(
            """
            SELECT d.id, v.id, d.canonical_path, v.sha256, v.size_bytes,
                   v.mtime_ns, v.ctime_ns, v.slide_count
            FROM decks d
            JOIN deck_versions v ON v.deck_id = d.id
            WHERE v.sha256 = ?
              AND (v.id = d.current_version_id OR d.current_version_id IS NULL)
            ORDER BY d.canonical_path
            """,
            (digest,),
        ).fetchall()
        return tuple(self._current_deck(row) for row in rows)

    def relocate_current(
        self,
        current: CurrentDeck,
        scanned: ScannedFile,
    ) -> ImportedDeck:
        path = scanned.path.resolve()
        now = _now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                """
                UPDATE decks
                SET canonical_path = ?, display_name = ?,
                    current_version_id = ?, updated_at = ?
                WHERE id = ?
                  AND canonical_path = ?
                  AND (current_version_id = ? OR current_version_id IS NULL)
                """,
                (
                    str(path),
                    path.stem,
                    current.version_id,
                    now,
                    current.deck_id,
                    str(current.path),
                    current.version_id,
                ),
            )
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError("relocation source changed during sync")
            self.connection.execute(
                """
                UPDATE deck_versions
                SET size_bytes = ?, mtime_ns = ?, ctime_ns = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    scanned.size_bytes,
                    scanned.mtime_ns,
                    scanned.ctime_ns,
                    now,
                    current.version_id,
                ),
            )
            self.connection.execute("COMMIT")
        except Exception:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        return ImportedDeck(
            current.deck_id,
            current.version_id,
            path,
            current.slide_count,
            False,
            "moved",
        )

    def update_source_metadata(
        self,
        version_id: str,
        *,
        size_bytes: int,
        mtime_ns: int,
        ctime_ns: int,
    ) -> None:
        self.connection.execute(
            """
            UPDATE deck_versions
            SET size_bytes = ?, mtime_ns = ?, ctime_ns = ?, updated_at = ?
            WHERE id = ?
            """,
            (size_bytes, mtime_ns, ctime_ns, _now(), version_id),
        )

    def import_parsed(self, scanned: ScannedFile, parsed: ParsedDeck) -> ImportedDeck:
        _validate_dependencies(parsed.dependencies_json)
        path = scanned.path.resolve()
        prior = self.current_for_path(path)
        deck_id = prior.deck_id if prior is not None else deck_id_for(path)
        version_id = version_id_for(deck_id, scanned.sha256)
        now = _now()
        connection = self.connection
        connection.execute("BEGIN")
        try:
            connection.execute(
                """
                INSERT INTO decks(id, canonical_path, display_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(canonical_path) DO UPDATE SET display_name=excluded.display_name,
                    updated_at=excluded.updated_at
                """,
                (deck_id, str(path), path.stem, now, now),
            )
            existing = connection.execute(
                "SELECT id, slide_count FROM deck_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE deck_versions
                    SET size_bytes = ?, mtime_ns = ?, ctime_ns = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        scanned.size_bytes,
                        scanned.mtime_ns,
                        scanned.ctime_ns,
                        now,
                        version_id,
                    ),
                )
                connection.execute(
                    "UPDATE decks SET current_version_id = ?, updated_at = ? WHERE id = ?",
                    (version_id, now, deck_id),
                )
                obsolete_slides, obsolete_versions = _prune_unreferenced_versions(
                    connection, deck_id, version_id
                )
                connection.execute("COMMIT")
                return ImportedDeck(
                    deck_id,
                    version_id,
                    path,
                    int(existing[1]),
                    prior is None,
                    "created" if prior is None else "updated",
                    obsolete_slides,
                    obsolete_versions,
                )

            connection.execute(
                """
                INSERT INTO deck_versions(
                    id, deck_id, sha256, size_bytes, mtime_ns, ctime_ns, parser_version,
                    slide_count, status, created_at, updated_at,
                    source_format, canonical_format, dependencies_json,
                    capabilities_json, warnings_json, renderer_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'parsed', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    deck_id,
                    scanned.sha256,
                    scanned.size_bytes,
                    scanned.mtime_ns,
                    scanned.ctime_ns,
                    parsed.parser_version,
                    len(parsed.slides),
                    now,
                    now,
                    parsed.source_format,
                    parsed.canonical_format,
                    parsed.dependencies_json,
                    parsed.capabilities_json,
                    parsed.warnings_json,
                    parsed.renderer_version,
                ),
            )
            section_title = ""
            for slide in parsed.slides:
                slide_id = f"{version_id}_s{slide.slide_number:05d}"
                composition_ref_json = slide.composition_ref_json
                if parsed.source_format == "render_deck_html":
                    composition_ref = json.loads(composition_ref_json)
                    if not isinstance(composition_ref, dict):
                        raise ValueError("composition_ref_json must be an object")
                    composition_ref["source_version_id"] = version_id
                    composition_ref_json = json.dumps(composition_ref, ensure_ascii=False)
                connection.execute(
                    """
                    INSERT INTO slides(
                        id, deck_version_id, slide_number, title, body_text, notes_text,
                        content_text, created_at, page_key, page_kind,
                        composition_ref_json, capabilities_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        slide_id,
                        version_id,
                        slide.slide_number,
                        slide.title,
                        slide.body_text,
                        slide.notes_text,
                        slide.content_text,
                        now,
                        slide.page_key,
                        slide.page_kind,
                        composition_ref_json,
                        slide.capabilities_json,
                    ),
                )
                connection.execute(
                    "INSERT INTO slide_fts(slide_id, title, body_text, notes_text) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        slide_id,
                        _analyze(slide.title),
                        _analyze(slide.body_text),
                        _analyze(slide.notes_text),
                    ),
                )
                if _is_section_marker(slide.title, slide.body_text, slide.notes_text):
                    section_title = slide.title
                classification = classify_slide_result(
                    path.name,
                    slide.title,
                    " ".join((slide.body_text, slide.notes_text)),
                    section_title=section_title,
                    notes=slide.notes_text,
                )
                connection.execute(
                    """
                    INSERT INTO slide_taxonomy(
                        slide_id, topic, subtopic, page_type, confidence,
                        classification_source, classifier_version, classified_at
                    ) VALUES (?, ?, ?, ?, ?, 'auto', ?, ?)
                    """,
                    (
                        slide_id,
                        classification.topic,
                        classification.subtopic,
                        classification.page_type,
                        classification.confidence,
                        classification.classifier_version,
                        now,
                    ),
                )
            connection.execute(
                "UPDATE decks SET current_version_id = ?, updated_at = ? WHERE id = ?",
                (version_id, now, deck_id),
            )
            obsolete_slides, obsolete_versions = _prune_unreferenced_versions(
                connection, deck_id, version_id
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return ImportedDeck(
            deck_id,
            version_id,
            path,
            len(parsed.slides),
            prior is None,
            "created" if prior is None else "updated",
            obsolete_slides,
            obsolete_versions,
        )


def _match_query(query: str) -> str:
    cleaned = " ".join(query.split())
    if not cleaned:
        return ""
    # FTS5 phrase syntax handles punctuation safely and is deterministic. For
    # longer CJK queries, require each bigram so substring searches work.
    tokens = cleaned.split()
    if len(tokens) > 1 and any(_is_cjk(char) for char in cleaned):
        clauses: list[str] = []
        for token in tokens:
            runs: list[str] = []
            current = ""
            for char in token:
                if _is_cjk(char):
                    current += char
                elif current:
                    runs.append(current)
                    current = ""
            if current:
                runs.append(current)
            cjk_runs = [run for run in runs if len(run) >= 3]
            if cjk_runs:
                for run in cjk_runs:
                    clauses.extend(
                        "".join(run[index : index + 2]) for index in range(len(run) - 1)
                    )
            else:
                clauses.append(token)
        return " AND ".join(
            f'"{clause.replace(chr(34), chr(34) * 2)}"' for clause in clauses
        )
    cjk = [char for char in cleaned if _is_cjk(char)]
    if len(cjk) >= 3:
        grams = ["".join(cjk[i : i + 2]) for i in range(len(cjk) - 1)]
        return " AND ".join(f'"{gram.replace(chr(34), chr(34) * 2)}"' for gram in grams)
    return f'"{cleaned.replace(chr(34), chr(34) * 2)}"'


def search_slides(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 50,
    offset: int = 0,
    filters: LibraryFilters = DEFAULT_LIBRARY_FILTERS,
) -> list[SearchResult]:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    if offset < 0:
        raise ValueError("offset cannot be negative")
    match = _match_query(query)
    filter_sql = ""
    filter_values: list[str | int] = []
    if filters.topic:
        canonical = LEGACY_TOPIC_ALIASES.get(filters.topic, filters.topic)
        if canonical == filters.topic:
            filter_sql += " AND t.topic = ?"
            filter_values.append(filters.topic)
        else:
            filter_sql += " AND t.topic IN (?, ?)"
            filter_values.extend((filters.topic, canonical))
    if filters.subtopic:
        filter_sql += " AND t.subtopic = ?"
        filter_values.append(filters.subtopic)
    if filters.page_type:
        filter_sql += " AND t.page_type = ?"
        filter_values.append(filters.page_type)
    if filters.deck_id:
        filter_sql += " AND d.id = ?"
        filter_values.append(filters.deck_id)
    if filters.source_format:
        filter_sql += " AND v.source_format = ?"
        filter_values.append(filters.source_format)
    if not match:
        rows = connection.execute(
            f"""
            SELECT s.id, d.id, v.id, d.canonical_path, s.slide_number,
                   s.title, s.body_text, s.notes_text, t.topic, t.page_type,
                   t.subtopic, t.confidence, t.classification_source, t.classifier_version,
                   v.source_format, s.page_key, s.page_kind,
                   s.capabilities_json, v.warnings_json
            FROM slides s
            JOIN deck_versions v ON v.id = s.deck_version_id
            JOIN decks d ON d.id = v.deck_id
            LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
            WHERE v.status = 'parsed' AND d.current_version_id = v.id{filter_sql}
            ORDER BY d.canonical_path, s.slide_number
            LIMIT ? OFFSET ?
            """,
            (*filter_values, limit, offset),
        ).fetchall()
        return [
            SearchResult(
                slide_id=row[0],
                deck_id=row[1],
                version_id=row[2],
                path=Path(row[3]),
                slide_number=int(row[4]),
                title=row[5],
                body_text=row[6],
                notes_text=row[7],
                rank=0.0,
                topic=str(row[8] or ""),
                page_type=str(row[9] or ""),
                subtopic=str(row[10] or ""),
                confidence=str(row[11] or ""),
                classification_source=str(row[12] or ""),
                classifier_version=str(row[13] or ""),
                source_format=str(row[14]),
                page_key=str(row[15]),
                page_kind=str(row[16]),
                capabilities=json.loads(row[17]),
                warnings=json.loads(row[18]),
            )
            for row in rows
        ]
    rows = connection.execute(
        f"""
        SELECT s.id, d.id, v.id, d.canonical_path, s.slide_number,
               s.title, s.body_text, s.notes_text, bm25(slide_fts) AS rank,
               t.topic, t.page_type, t.subtopic, t.confidence,
               t.classification_source, t.classifier_version,
               v.source_format, s.page_key, s.page_kind,
               s.capabilities_json, v.warnings_json
        FROM slide_fts f
        JOIN slides s ON s.id = f.slide_id
        JOIN deck_versions v ON v.id = s.deck_version_id
        JOIN decks d ON d.id = v.deck_id
        LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
        WHERE slide_fts MATCH ? AND v.status = 'parsed' AND d.current_version_id = v.id{filter_sql}
        ORDER BY rank, d.canonical_path, s.slide_number
        LIMIT ? OFFSET ?
        """,
        (match, *filter_values, limit, offset),
    ).fetchall()
    return [
        SearchResult(
            slide_id=row[0],
            deck_id=row[1],
            version_id=row[2],
            path=Path(row[3]),
            slide_number=int(row[4]),
            title=row[5],
            body_text=row[6],
            notes_text=row[7],
            rank=float(row[8]),
            topic=str(row[9] or ""),
            page_type=str(row[10] or ""),
            subtopic=str(row[11] or ""),
            confidence=str(row[12] or ""),
            classification_source=str(row[13] or ""),
            classifier_version=str(row[14] or ""),
            source_format=str(row[15]),
            page_key=str(row[16]),
            page_kind=str(row[17]),
            capabilities=json.loads(row[18]),
            warnings=json.loads(row[19]),
        )
        for row in rows
    ]


def count_search_slides(
    connection: sqlite3.Connection,
    query: str,
    filters: LibraryFilters = DEFAULT_LIBRARY_FILTERS,
) -> int:
    match = _match_query(query)
    filter_sql = ""
    filter_values: list[str] = []
    if filters.topic:
        canonical = LEGACY_TOPIC_ALIASES.get(filters.topic, filters.topic)
        if canonical == filters.topic:
            filter_sql += " AND t.topic = ?"
            filter_values.append(filters.topic)
        else:
            filter_sql += " AND t.topic IN (?, ?)"
            filter_values.extend((filters.topic, canonical))
    if filters.subtopic:
        filter_sql += " AND t.subtopic = ?"
        filter_values.append(filters.subtopic)
    if filters.page_type:
        filter_sql += " AND t.page_type = ?"
        filter_values.append(filters.page_type)
    if filters.deck_id:
        filter_sql += " AND d.id = ?"
        filter_values.append(filters.deck_id)
    if filters.source_format:
        filter_sql += " AND v.source_format = ?"
        filter_values.append(filters.source_format)
    if not match:
        row = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM slides s
            JOIN deck_versions v ON v.id = s.deck_version_id
            JOIN decks d ON d.id = v.deck_id
            LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
            WHERE v.status = 'parsed' AND d.current_version_id = v.id{filter_sql}
            """
            , filter_values,
        ).fetchone()
        return int(row[0]) if row is not None else 0
    row = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM slide_fts f
        JOIN slides s ON s.id = f.slide_id
        JOIN deck_versions v ON v.id = s.deck_version_id
        JOIN decks d ON d.id = v.deck_id
        LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
        WHERE slide_fts MATCH ? AND v.status = 'parsed' AND d.current_version_id = v.id{filter_sql}
        """,
        (match, *filter_values),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def library_facets(connection: sqlite3.Connection) -> LibraryFacets:
    """Return fixed taxonomy options and current-source deck counts."""
    current = """
        FROM slides s
        JOIN deck_versions v ON v.id = s.deck_version_id
        JOIN decks d ON d.id = v.deck_id
        LEFT JOIN slide_taxonomy t ON t.slide_id = s.id
        WHERE v.status = 'parsed' AND d.current_version_id = v.id
    """
    topic_rows = connection.execute(
        f"SELECT t.topic, COUNT(*) {current} GROUP BY t.topic"
    ).fetchall()
    topic_counts = {str(row[0]): int(row[1]) for row in topic_rows if row[0]}
    page_type_rows = connection.execute(
        f"SELECT t.page_type, COUNT(*) {current} GROUP BY t.page_type"
    ).fetchall()
    page_type_counts = {str(row[0]): int(row[1]) for row in page_type_rows if row[0]}
    deck_rows = connection.execute(
        f"SELECT d.id, d.display_name, COUNT(*) {current} "
        "GROUP BY d.id, d.display_name ORDER BY d.display_name"
    ).fetchall()
    hierarchy_rows = connection.execute(
        f"SELECT t.topic, t.subtopic, COUNT(*) {current} GROUP BY t.topic, t.subtopic"
    ).fetchall()
    hierarchy_counts: dict[tuple[str, str], int] = {
        (str(row[0]), str(row[1])): int(row[2])
        for row in hierarchy_rows
        if row[0] and row[1]
    }
    topics = tuple(
        FacetOption(
            topic,
            topic,
            sum(
                topic_counts.get(alias, 0)
                for alias, canonical in ((topic, topic), *LEGACY_TOPIC_ALIASES.items())
                if canonical == topic
            ),
            tuple(
                FacetOption(
                    subtopic,
                    subtopic,
                    sum(
                        hierarchy_counts.get((alias, subtopic), 0)
                        for alias, canonical in ((topic, topic), *LEGACY_TOPIC_ALIASES.items())
                        if canonical == topic
                    ),
                )
                for subtopic in TOPIC_TREE[topic]
            ),
        )
        for topic in TOPICS
    )
    return LibraryFacets(
        topics=topics,
        page_types=tuple(
            FacetOption(value, value, page_type_counts.get(value, 0)) for value in PAGE_TYPES
        ),
        decks=tuple(FacetOption(str(row[0]), str(row[1]), int(row[2])) for row in deck_rows),
    )
