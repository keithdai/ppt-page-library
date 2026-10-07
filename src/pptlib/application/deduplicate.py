from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import cast
from xml.etree.ElementTree import Element
from zipfile import BadZipFile, ZipFile

from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]
from PIL import Image, ImageOps, UnidentifiedImageError

from pptlib.config import Settings
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.infrastructure.db.connection import connect

FINGERPRINT_VERSION = "dedup-v2"
PHASH_DISTANCE_LIMIT = 12
VISUAL_SIMILARITY_MIN = 0.94
TEXT_SIMILARITY_MIN = 0.92
MIN_TEXT_LENGTH = 12
MAX_XML_PART_BYTES = 64 * 1024 * 1024

_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_TEXT_CLEAN_RE = re.compile(r"[\W_]+", re.UNICODE)
_VOLATILE_ATTRIBUTES = {"id", "name", "creationId", "modId"}
_OBJECT_TAGS = {"sp", "pic", "graphicFrame", "cxnSp", "grpSp", "contentPart"}
_VISUAL_VECTOR_SIZE = 32


@dataclass(frozen=True, slots=True)
class DuplicateSlide:
    slide_id: str
    deck_id: str
    deck_name: str
    slide_number: int
    title: str
    match_kind: str
    confidence: float
    visual_similarity: float
    text_similarity: float
    structure_equal: bool
    is_canonical: bool
    selected_by_default: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    group_id: str
    kind: str
    confidence: float
    canonical_slide_id: str
    members: tuple[DuplicateSlide, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "kind": self.kind,
            "confidence": self.confidence,
            "canonical_slide_id": self.canonical_slide_id,
            "members": [member.to_dict() for member in self.members],
        }


@dataclass(frozen=True, slots=True)
class DuplicateReport:
    total_slides: int
    analyzed_slides: int
    exact_groups: int
    similar_groups: int
    removable_pages: int
    groups: tuple[DuplicateGroup, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_slides": self.total_slides,
            "analyzed_slides": self.analyzed_slides,
            "exact_groups": self.exact_groups,
            "similar_groups": self.similar_groups,
            "removable_pages": self.removable_pages,
            "groups": [group.to_dict() for group in self.groups],
        }


@dataclass(frozen=True, slots=True)
class _SlideRow:
    slide_id: str
    deck_id: str
    deck_name: str
    source_path: Path
    source_mtime_ns: int
    slide_number: int
    title: str
    content_text: str


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    slide: _SlideRow
    pixel_sha256: str
    perceptual_hash: str
    visual_vector: bytes | None
    text_hash: str
    structure_hash: str
    content_hash: str
    image_width: int
    image_height: int


@dataclass(frozen=True, slots=True)
class _PairEvidence:
    kind: str
    confidence: float
    visual_similarity: float
    text_similarity: float
    structure_equal: bool


class _DisjointSet:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            parent = self.parent[value]
            self.parent[value] = root
            value = parent
        return root

    def union(self, first: str, second: str) -> None:
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root != second_root:
            self.parent[second_root] = first_root


def find_duplicate_slides(
    settings: Settings,
    *,
    refresh: bool = False,
) -> DuplicateReport:
    if not settings.database_path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "本地索引数据库不存在，请先导入 PPTX")
    connection = connect(settings.database_path)
    try:
        rows = _current_slides(connection)
        _ensure_fingerprints(connection, settings, rows, refresh=refresh)
        fingerprints = _load_fingerprints(connection, rows)
    finally:
        connection.close()
    groups = _build_groups(fingerprints)
    return DuplicateReport(
        total_slides=len(rows),
        analyzed_slides=len(fingerprints),
        exact_groups=sum(group.kind == "exact" for group in groups),
        similar_groups=sum(group.kind == "similar" for group in groups),
        removable_pages=sum(
            member.selected_by_default
            for group in groups
            for member in group.members
        ),
        groups=groups,
    )


def _current_slides(connection: sqlite3.Connection) -> list[_SlideRow]:
    rows = connection.execute(
        """
        SELECT s.id, d.id, d.display_name, d.canonical_path, v.mtime_ns,
               s.slide_number, s.title, s.content_text
        FROM slides s
        JOIN deck_versions v ON v.id = s.deck_version_id
        JOIN decks d ON d.id = v.deck_id
        WHERE v.status = 'parsed' AND d.current_version_id = v.id
        ORDER BY d.display_name, s.slide_number
        """
    ).fetchall()
    return [
        _SlideRow(
            slide_id=str(row[0]),
            deck_id=str(row[1]),
            deck_name=str(row[2]),
            source_path=Path(str(row[3])),
            source_mtime_ns=int(row[4]),
            slide_number=int(row[5]),
            title=str(row[6] or ""),
            content_text=str(row[7] or ""),
        )
        for row in rows
    ]


def _ensure_fingerprints(
    connection: sqlite3.Connection,
    settings: Settings,
    rows: list[_SlideRow],
    *,
    refresh: bool,
) -> None:
    existing = {
        str(row[0])
        for row in connection.execute(
            "SELECT slide_id FROM slide_fingerprints WHERE fingerprint_version = ?",
            (FINGERPRINT_VERSION,),
        ).fetchall()
    }
    pending = rows if refresh else [row for row in rows if row.slide_id not in existing]
    if not pending:
        return
    by_source: dict[Path, list[_SlideRow]] = defaultdict(list)
    for row in pending:
        by_source[row.source_path].append(row)

    now = datetime.now(UTC).isoformat()
    records: list[tuple[object, ...]] = []
    for source_path, source_rows in by_source.items():
        package_data = _slide_package_data(source_path, settings)
        for row in source_rows:
            structure_hash, content_hash = package_data.get(
                row.slide_number, _fallback_package_hashes(row)
            )
            image_path = _slide_image_path(settings, row.slide_id)
            pixel_hash, perceptual_hash, visual_vector, width, height = (
                _image_fingerprint(image_path)
            )
            records.append(
                (
                    row.slide_id,
                    pixel_hash,
                    perceptual_hash,
                    visual_vector,
                    _sha256_text(_normalize_text(row.content_text)),
                    structure_hash,
                    content_hash,
                    width,
                    height,
                    FINGERPRINT_VERSION,
                    now,
                )
            )

    connection.execute("BEGIN")
    try:
        connection.executemany(
            """
            INSERT INTO slide_fingerprints(
                slide_id, pixel_sha256, perceptual_hash, visual_vector,
                text_hash, structure_hash, content_hash,
                image_width, image_height, fingerprint_version, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slide_id) DO UPDATE SET
                pixel_sha256 = excluded.pixel_sha256,
                perceptual_hash = excluded.perceptual_hash,
                visual_vector = excluded.visual_vector,
                text_hash = excluded.text_hash,
                structure_hash = excluded.structure_hash,
                content_hash = excluded.content_hash,
                image_width = excluded.image_width,
                image_height = excluded.image_height,
                fingerprint_version = excluded.fingerprint_version,
                computed_at = excluded.computed_at
            """,
            records,
        )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _slide_package_data(
    source_path: Path,
    settings: Settings,
) -> dict[int, tuple[str, str]]:
    if not source_path.is_file():
        return {}
    try:
        with ZipFile(source_path) as package:
            infos = package.infolist()
            if len(infos) > settings.max_parts_per_package:
                return {}
            if sum(info.file_size for info in infos) > settings.max_uncompressed_package_bytes:
                return {}
            parts = _slide_order(package)
            result: dict[int, tuple[str, str]] = {}
            for number, part in enumerate(parts, start=1):
                if part not in package.namelist():
                    continue
                root = _parse_xml_part(package, part)
                relationships = _relationship_tokens(package, part)
                structure = _structure_descriptor(root)
                content = {
                    "slide": _canonical_node(root, relationships),
                    "relationships": sorted(relationships.values()),
                }
                result[number] = (
                    _sha256_json(structure),
                    _sha256_json(content),
                )
            return result
    except (BadZipFile, KeyError, OSError, ValueError):
        return {}


def _slide_order(package: ZipFile) -> list[str]:
    presentation = "ppt/presentation.xml"
    if presentation not in package.namelist():
        return []
    root = _parse_xml_part(package, presentation)
    rel_path = "ppt/_rels/presentation.xml.rels"
    relations = _relationships(package, presentation, rel_path)
    ordered: list[str] = []
    for slide_id in root.findall(f".//{{{_P_NS}}}sldId"):
        relation_id = slide_id.attrib.get(f"{{{_R_NS}}}id", "")
        target = relations.get(relation_id)
        if target is not None:
            ordered.append(target[0])
    return ordered


def _relationships(
    package: ZipFile,
    source_part: str,
    rel_path: str | None = None,
) -> dict[str, tuple[str, str]]:
    if rel_path is None:
        directory, filename = posixpath.split(source_part)
        rel_path = posixpath.join(directory, "_rels", filename + ".rels")
    if rel_path not in package.namelist():
        return {}
    root = _parse_xml_part(package, rel_path)
    result: dict[str, tuple[str, str]] = {}
    for relation in root.findall(f"{{{_PKG_REL_NS}}}Relationship"):
        if relation.attrib.get("TargetMode") == "External":
            continue
        relation_id = relation.attrib.get("Id")
        target = relation.attrib.get("Target")
        if not relation_id or not target:
            continue
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_part), target)
        ).lstrip("/")
        result[relation_id] = (resolved, relation.attrib.get("Type", ""))
    return result


def _relationship_tokens(package: ZipFile, slide_part: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for relation_id, (target, relation_type) in _relationships(package, slide_part).items():
        if target in package.namelist():
            tokens[relation_id] = relation_type.rsplit("/", 1)[-1]
    return tokens


def _parse_xml_part(package: ZipFile, part: str) -> Element:
    if package.getinfo(part).file_size > MAX_XML_PART_BYTES:
        raise ValueError(f"PPTX XML part exceeds the size limit: {part}")
    return cast(Element, fromstring(package.read(part)))


def _structure_descriptor(root: Element) -> list[dict[str, object]]:
    tree = root.find(f".//{{{_P_NS}}}spTree")
    if tree is None:
        return []
    descriptor: list[dict[str, object]] = []
    for child in tree:
        kind = _local_name(child.tag)
        if kind not in _OBJECT_TAGS:
            continue
        placeholder = child.find(f".//{{{_P_NS}}}ph")
        tags = Counter(_local_name(node.tag) for node in child.iter())
        descriptor.append(
            {
                "kind": kind,
                "bbox": _bounding_box(child),
                "placeholder": (
                    {
                        "type": placeholder.attrib.get("type", ""),
                        "idx": placeholder.attrib.get("idx", ""),
                    }
                    if placeholder is not None
                    else None
                ),
                "tags": sorted(tags.items()),
            }
        )
    return descriptor


def _bounding_box(element: Element) -> list[int] | None:
    for transform in element.iter():
        if _local_name(transform.tag) not in {"xfrm", "xfrm2D"}:
            continue
        offset = next(
            (child for child in transform if _local_name(child.tag) == "off"),
            None,
        )
        extent = next(
            (child for child in transform if _local_name(child.tag) == "ext"),
            None,
        )
        if offset is not None and extent is not None:
            return [
                int(offset.attrib.get("x", 0)),
                int(offset.attrib.get("y", 0)),
                int(extent.attrib.get("cx", 0)),
                int(extent.attrib.get("cy", 0)),
            ]
    return None


def _canonical_node(element: Element, relationship_hashes: dict[str, str]) -> object:
    attrs: list[tuple[str, str]] = []
    for raw_name, raw_value in element.attrib.items():
        name = _local_name(raw_name)
        if name in _VOLATILE_ATTRIBUTES:
            continue
        value = relationship_hashes.get(raw_value, raw_value)
        attrs.append((name, value))
    text = _normalize_text(element.text or "") if _local_name(element.tag) == "t" else ""
    return [
        _local_name(element.tag),
        sorted(attrs),
        text,
        [_canonical_node(child, relationship_hashes) for child in element],
    ]


def _fallback_package_hashes(_row: _SlideRow) -> tuple[str, str]:
    return "", ""


def _slide_image_path(settings: Settings, slide_id: str) -> Path | None:
    preview = settings.assets_dir / "previews" / f"{slide_id}.jpg"
    if preview.is_file():
        return preview
    thumbnail = settings.assets_dir / "thumbnails" / f"{slide_id}.jpg"
    return thumbnail if thumbnail.is_file() else None


def _image_fingerprint(
    image_path: Path | None,
) -> tuple[str, str, bytes | None, int, int]:
    if image_path is None:
        return "", "", None, 0, 0
    try:
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            width, height = image.size
            pixel_digest = hashlib.sha256()
            pixel_digest.update(f"{width}x{height}:RGB:".encode("ascii"))
            pixel_digest.update(image.tobytes())
            gray = ImageOps.fit(
                image.convert("L"),
                (_VISUAL_VECTOR_SIZE, _VISUAL_VECTOR_SIZE),
                method=Image.Resampling.LANCZOS,
            )
            vector = gray.tobytes()
            hash_image = ImageOps.fit(
                image.convert("L"),
                (9, 8),
                method=Image.Resampling.LANCZOS,
            )
            return (
                pixel_digest.hexdigest(),
                _perceptual_hash(hash_image.tobytes()),
                vector,
                width,
                height,
            )
    except (OSError, UnidentifiedImageError, ValueError):
        return "", "", None, 0, 0


def _perceptual_hash(vector: bytes) -> str:
    bits = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            bits = (bits << 1) | int(vector[row + x] > vector[row + x + 1])
    return f"{bits:016x}"


def _load_fingerprints(
    connection: sqlite3.Connection,
    slides: list[_SlideRow],
) -> list[_Fingerprint]:
    by_id = {slide.slide_id: slide for slide in slides}
    rows = connection.execute(
        """
        SELECT slide_id, pixel_sha256, perceptual_hash, visual_vector,
               text_hash, structure_hash, content_hash, image_width, image_height
        FROM slide_fingerprints
        WHERE fingerprint_version = ?
        """,
        (FINGERPRINT_VERSION,),
    ).fetchall()
    return [
        _Fingerprint(
            slide=by_id[str(row[0])],
            pixel_sha256=str(row[1] or ""),
            perceptual_hash=str(row[2] or ""),
            visual_vector=bytes(row[3]) if row[3] is not None else None,
            text_hash=str(row[4]),
            structure_hash=str(row[5] or ""),
            content_hash=str(row[6] or ""),
            image_width=int(row[7]),
            image_height=int(row[8]),
        )
        for row in rows
        if str(row[0]) in by_id
    ]


def _build_groups(fingerprints: list[_Fingerprint]) -> tuple[DuplicateGroup, ...]:
    if len(fingerprints) < 2:
        return ()
    by_id = {item.slide.slide_id: item for item in fingerprints}
    disjoint = _DisjointSet(list(by_id))
    exact_buckets: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for item in fingerprints:
        if item.pixel_sha256:
            exact_buckets[("visual", item.pixel_sha256, item.text_hash)].append(
                item.slide.slide_id
            )
        if item.content_hash and item.pixel_sha256:
            exact_buckets[
                ("content", item.content_hash, item.text_hash, item.pixel_sha256)
            ].append(item.slide.slide_id)
    for ids in exact_buckets.values():
        if len(ids) > 1:
            first_id = ids[0]
            for second_id in ids[1:]:
                disjoint.union(first_id, second_id)

    for first_id, second_id in _near_candidates(fingerprints):
        evidence = _compare(by_id[first_id], by_id[second_id])
        if evidence.kind == "near":
            disjoint.union(first_id, second_id)

    components: dict[str, list[_Fingerprint]] = defaultdict(list)
    for item in fingerprints:
        components[disjoint.find(item.slide.slide_id)].append(item)

    groups = [
        _make_group(members)
        for members in components.values()
        if len(members) > 1
    ]
    groups.sort(
        key=lambda group: (
            group.kind != "exact",
            -len(group.members),
            group.group_id,
        )
    )
    return tuple(groups)


def _near_candidates(fingerprints: list[_Fingerprint]) -> set[tuple[str, str]]:
    visual_buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
    text_buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
    by_text: dict[str, list[str]] = defaultdict(list)
    for item in fingerprints:
        if item.perceptual_hash:
            value = int(item.perceptual_hash, 16)
            for band in range(8):
                visual_buckets[(band, (value >> (band * 8)) & 0xFF)].append(
                    item.slide.slide_id
                )
        normalized_text = _normalize_text(item.slide.content_text)
        if len(normalized_text) >= MIN_TEXT_LENGTH:
            by_text[item.text_hash].append(item.slide.slide_id)
            text_hash = _text_simhash(normalized_text)
            for band in range(8):
                text_buckets[(band, (text_hash >> (band * 8)) & 0xFF)].append(
                    item.slide.slide_id
                )

    candidates: set[tuple[str, str]] = set()
    for ids in by_text.values():
        for index, first in enumerate(ids):
            for second in ids[index + 1 :]:
                candidates.add(_pair_key(first, second))
    by_id = {item.slide.slide_id: item for item in fingerprints}
    for slide_id, item in by_id.items():
        if not item.perceptual_hash:
            continue
        normalized_text = _normalize_text(item.slide.content_text)
        if len(normalized_text) < MIN_TEXT_LENGTH:
            continue
        visual_hash = int(item.perceptual_hash, 16)
        text_hash = _text_simhash(normalized_text)
        visual_matches: set[str] = set()
        text_matches: set[str] = set()
        for band in range(8):
            visual_matches.update(
                visual_buckets[(band, (visual_hash >> (band * 8)) & 0xFF)]
            )
            text_matches.update(text_buckets[(band, (text_hash >> (band * 8)) & 0xFF)])
        for other_id in visual_matches & text_matches:
            if other_id != slide_id:
                candidates.add(_pair_key(slide_id, other_id))
    return candidates


def _make_group(members: list[_Fingerprint]) -> DuplicateGroup:
    canonical = max(
        members,
        key=lambda item: (
            bool(item.pixel_sha256),
            len(_normalize_text(item.slide.content_text)),
            item.slide.source_mtime_ns,
            -item.slide.slide_number,
            item.slide.slide_id,
        ),
    )
    evidence_by_id = {
        item.slide.slide_id: _compare(canonical, item)
        for item in members
        if item.slide.slide_id != canonical.slide.slide_id
    }
    exact = all(evidence.kind.startswith("exact") for evidence in evidence_by_id.values())
    group_kind = "exact" if exact else "similar"
    member_rows: list[DuplicateSlide] = []
    for item in sorted(
        members,
        key=lambda value: (
            value.slide.slide_id != canonical.slide.slide_id,
            value.slide.deck_name,
            value.slide.slide_number,
        ),
    ):
        is_canonical = item.slide.slide_id == canonical.slide.slide_id
        evidence = (
            _PairEvidence("canonical", 1.0, 1.0, 1.0, True)
            if is_canonical
            else evidence_by_id[item.slide.slide_id]
        )
        member_rows.append(
            DuplicateSlide(
                slide_id=item.slide.slide_id,
                deck_id=item.slide.deck_id,
                deck_name=item.slide.deck_name,
                slide_number=item.slide.slide_number,
                title=item.slide.title,
                match_kind=evidence.kind,
                confidence=round(evidence.confidence, 4),
                visual_similarity=round(evidence.visual_similarity, 4),
                text_similarity=round(evidence.text_similarity, 4),
                structure_equal=evidence.structure_equal,
                is_canonical=is_canonical,
                selected_by_default=exact and not is_canonical,
            )
        )
    group_id = "dup_" + hashlib.sha256(
        "\n".join(sorted(item.slide.slide_id for item in members)).encode("utf-8")
    ).hexdigest()[:16]
    confidence = min(
        (member.confidence for member in member_rows if not member.is_canonical),
        default=1.0,
    )
    return DuplicateGroup(
        group_id=group_id,
        kind=group_kind,
        confidence=round(confidence, 4),
        canonical_slide_id=canonical.slide.slide_id,
        members=tuple(member_rows),
    )


def _compare(first: _Fingerprint, second: _Fingerprint) -> _PairEvidence:
    structure_equal = bool(
        first.structure_hash
        and second.structure_hash
        and first.structure_hash == second.structure_hash
    )
    text_similarity = _text_similarity(
        first.slide.content_text, second.slide.content_text
    )
    visual_similarity = _visual_similarity(first, second)
    text_equal = first.text_hash == second.text_hash
    pixels_available = bool(first.pixel_sha256 and second.pixel_sha256)
    pixels_equal = pixels_available and first.pixel_sha256 == second.pixel_sha256
    if (
        first.content_hash
        and first.content_hash == second.content_hash
        and text_equal
        and pixels_equal
    ):
        return _PairEvidence(
            "exact_structure", 1.0, visual_similarity, text_similarity, structure_equal
        )
    if pixels_equal and text_equal:
        return _PairEvidence(
            "exact_visual", 1.0, 1.0, text_similarity, structure_equal
        )
    if not first.perceptual_hash or not second.perceptual_hash:
        return _PairEvidence(
            "different", 0.0, visual_similarity, text_similarity, structure_equal
        )
    distance = _hamming_distance(first.perceptual_hash, second.perceptual_hash)
    first_text = _normalize_text(first.slide.content_text)
    second_text = _normalize_text(second.slide.content_text)
    enough_text = min(len(first_text), len(second_text)) >= MIN_TEXT_LENGTH
    is_near = (
        distance <= PHASH_DISTANCE_LIMIT
        and visual_similarity >= VISUAL_SIMILARITY_MIN
        and enough_text
        and text_similarity >= TEXT_SIMILARITY_MIN
    )
    confidence = (
        visual_similarity * 0.5
        + text_similarity * 0.3
        + float(structure_equal) * 0.2
    )
    return _PairEvidence(
        "near" if is_near else "different",
        confidence if is_near else 0.0,
        visual_similarity,
        text_similarity,
        structure_equal,
    )


def _visual_similarity(first: _Fingerprint, second: _Fingerprint) -> float:
    if first.pixel_sha256 and first.pixel_sha256 == second.pixel_sha256:
        return 1.0
    if first.visual_vector is None or second.visual_vector is None:
        return 0.0
    if len(first.visual_vector) != len(second.visual_vector):
        return 0.0
    difference = sum(
        abs(left - right)
        for left, right in zip(first.visual_vector, second.visual_vector, strict=True)
    )
    return max(0.0, 1.0 - difference / (255 * len(first.visual_vector)))


def _text_similarity(first: str, second: str) -> float:
    first_normalized = _normalize_text(first)
    second_normalized = _normalize_text(second)
    if not first_normalized and not second_normalized:
        return 1.0
    if not first_normalized or not second_normalized:
        return 0.0
    return SequenceMatcher(None, first_normalized, second_normalized).ratio()


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _TEXT_CLEAN_RE.sub("", normalized)


def _text_simhash(value: str) -> int:
    grams = {value[index : index + 3] for index in range(max(1, len(value) - 2))}
    weights = [0] * 64
    for gram in grams:
        hashed = int.from_bytes(hashlib.sha256(gram.encode("utf-8")).digest()[:8])
        for bit in range(64):
            weights[bit] += 1 if hashed & (1 << bit) else -1
    result = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            result |= 1 << bit
    return result


def _hamming_distance(first: str, second: str) -> int:
    return (int(first, 16) ^ int(second, 16)).bit_count()


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _pair_key(first: str, second: str) -> tuple[str, str]:
    return (first, second) if first < second else (second, first)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
