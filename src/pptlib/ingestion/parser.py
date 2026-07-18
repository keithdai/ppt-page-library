from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

try:  # defusedxml is shipped in production; fallback keeps the core importable in minimal envs.
    from defusedxml.ElementTree import Element, fromstring  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised only before optional dependency install
    import xml.etree.ElementTree as _stdlib_et

    Element = _stdlib_et.Element

    def fromstring(data: bytes) -> Element:
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("unsafe XML declaration")
        return _stdlib_et.fromstring(data)

PARSER_VERSION = "pptx-xml-v1"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


@dataclass(frozen=True, slots=True)
class ParsedSlide:
    slide_number: int
    title: str
    body_text: str
    notes_text: str

    @property
    def content_text(self) -> str:
        return "\n".join(part for part in (self.title, self.body_text, self.notes_text) if part)


@dataclass(frozen=True, slots=True)
class ParsedDeck:
    slides: tuple[ParsedSlide, ...]
    parser_version: str = PARSER_VERSION


def _text_nodes(root: Element) -> list[str]:
    return [" ".join((node.text or "").split()) for node in root.iter(f"{{{_A_NS}}}t") if node.text]


def _join_text(values: list[str]) -> str:
    return " ".join(value for value in values if value).strip()


def _relationship_targets(xml: bytes) -> dict[str, str]:
    root = fromstring(xml)
    return {
        relation.attrib["Id"]: relation.attrib["Target"]
        for relation in root.findall(f"{{{_PKG_REL_NS}}}Relationship")
        if relation.attrib.get("Type", "").endswith("/slide")
        or relation.attrib.get("Type", "").endswith("/notesSlide")
    }


def _resolve_part(base: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), target)).lstrip("/")


def _slide_order(package: ZipFile) -> list[str]:
    presentation = "ppt/presentation.xml"
    root = fromstring(package.read(presentation))
    relationships = _relationship_targets(package.read("ppt/_rels/presentation.xml.rels"))
    ordered: list[str] = []
    for slide_id in root.findall(f".//{{{_P_NS}}}sldId"):
        relation_id = slide_id.attrib.get(f"{{{_R_NS}}}id")
        target = relationships.get(relation_id or "")
        if target:
            ordered.append(_resolve_part(presentation, target))
    return ordered


def _notes_for_slide(package: ZipFile, slide_part: str) -> str:
    rel_part = posixpath.join(
        posixpath.dirname(slide_part), "_rels", posixpath.basename(slide_part) + ".rels"
    )
    if rel_part not in package.namelist():
        return ""
    relationships = _relationship_targets(package.read(rel_part))
    for relation_id, target in relationships.items():
        # Relationship targets are mapped by Id; only notesSlide relations are accepted.
        rel_root = fromstring(package.read(rel_part))
        relation = next(
            (
                item
                for item in rel_root.findall(f"{{{_PKG_REL_NS}}}Relationship")
                if item.attrib.get("Id") == relation_id
            ),
            None,
        )
        if relation is not None and relation.attrib.get("Type", "").endswith("/notesSlide"):
            part = _resolve_part(slide_part, target)
            if part in package.namelist():
                return _join_text(_text_nodes(fromstring(package.read(part))))
    return ""


def _slide_text(root: Element) -> tuple[str, str]:
    title_values: list[str] = []
    body_values: list[str] = []
    for shape in root.findall(f".//{{{_P_NS}}}sp"):
        texts = _text_nodes(shape)
        if not texts:
            continue
        placeholder = shape.find(f"./{{{_P_NS}}}nvSpPr/{{{_P_NS}}}nvPr/{{{_P_NS}}}ph")
        placeholder_type = placeholder.attrib.get("type", "") if placeholder is not None else ""
        if placeholder_type in {"title", "ctrTitle"}:
            title_values.extend(texts)
        else:
            body_values.extend(texts)
    if not title_values:
        all_values = _text_nodes(root)
        title_values = all_values[:1]
        if title_values and body_values[:1] == title_values:
            body_values = body_values[1:]
    return _join_text(title_values), _join_text(body_values)


def parse_pptx(
    path: Path,
    *,
    max_uncompressed_package_bytes: int = 2 * 1024 * 1024 * 1024,
    max_parts: int = 20_000,
) -> ParsedDeck:
    """Extract slide text without materializing arbitrary package members."""
    try:
        with ZipFile(path) as package:
            infos = package.infolist()
            if len(infos) > max_parts:
                raise ValueError("PPTX package contains too many parts")
            if sum(info.file_size for info in infos) > max_uncompressed_package_bytes:
                raise ValueError("PPTX package exceeds the uncompressed size limit")
            if "ppt/presentation.xml" not in package.namelist():
                raise ValueError("PPTX package is missing ppt/presentation.xml")
            slides: list[ParsedSlide] = []
            for number, part in enumerate(_slide_order(package), start=1):
                if part not in package.namelist():
                    raise ValueError(f"PPTX slide part is missing: {part}")
                title, body = _slide_text(fromstring(package.read(part)))
                slides.append(
                    ParsedSlide(number, title, body, _notes_for_slide(package, part))
                )
            return ParsedDeck(tuple(slides))
    except BadZipFile as error:
        raise ValueError("file is not a valid PPTX zip package") from error
