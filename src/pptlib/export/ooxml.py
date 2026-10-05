"""Minimal, package-level PPTX slide transplant.

The exporter intentionally works on the OOXML package rather than using a
high-level presentation library.  A selection is represented by ``SlideRef``
objects, which is the stable boundary used by ingestion/search layers.

M0 supports static slides (text, shapes, images, charts and tables as long as
their package relationships are internal).  Macros, OLE, media and unknown
relationship types are preserved as warnings and are not promised to have A
level fidelity.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, cast
from xml.etree import ElementTree as ET

_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_REL_NS = _NS["rel"]
_R_NS = _NS["r"]
_P_NS = _NS["p"]
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_SLIDE_MASTER_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"
)
ET.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
ET.register_namespace("p", _P_NS)
ET.register_namespace("r", _R_NS)

MAX_ZIP_MEMBERS = 20_000
MAX_ZIP_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class SlideRef:
    """An immutable page reference produced by ingestion/search."""

    file_version_id: str
    source_path: Path
    page_number: int
    expected_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number must be >= 1")


@dataclass(frozen=True)
class ExportResult:
    export_id: str
    output_path: Path
    manifest_path: Path
    page_count: int
    warnings: tuple[str, ...]
    fidelity_level: str


@dataclass(frozen=True)
class ExportPreflight:
    page_count: int
    source_count: int
    estimated_output_bytes: int
    warnings: tuple[str, ...]
    fidelity_level: str
    slide_size: tuple[str | None, str | None]


class ExportError(RuntimeError):
    """Structured, user-safe export failure."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class _Package:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.archive = zipfile.ZipFile(path)
            infos = self.archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS:
                raise ExportError(
                    "INVALID_SOURCE_PACKAGE",
                    "源文件部件数量超过安全上限",
                    {"max_members": MAX_ZIP_MEMBERS},
                )
            total_size = 0
            names: set[str] = set()
            for info in infos:
                _validate_zip_member_name(info.filename)
                if info.filename in names:
                    raise ExportError("INVALID_SOURCE_PACKAGE", "源文件包含重复的 ZIP 部件名")
                names.add(info.filename)
                total_size += info.file_size
                if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise ExportError(
                        "INVALID_SOURCE_PACKAGE",
                        "源文件解压后大小超过安全上限",
                        {"max_uncompressed_bytes": MAX_ZIP_UNCOMPRESSED_BYTES},
                    )
            self.parts = {info.filename: self.archive.read(info) for info in infos}
        except ExportError:
            self.archive.close()
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            raise ExportError(
                "INVALID_SOURCE_PACKAGE", "源文件不是有效的 PPTX 包", {"path": str(path)}
            ) from exc
        if "ppt/presentation.xml" not in self.parts:
            self.archive.close()
            raise ExportError(
                "INVALID_SOURCE_PACKAGE", "PPTX 缺少 presentation.xml", {"path": str(path)}
            )
        try:
            self.content_types = ET.fromstring(self.parts["[Content_Types].xml"])
            ET.fromstring(self.parts["ppt/presentation.xml"])
        except (ET.ParseError, KeyError) as exc:
            self.archive.close()
            raise ExportError(
                "INVALID_SOURCE_PACKAGE",
                "PPTX 必需的 OOXML 部件缺失或损坏",
                {"path": str(path)},
            ) from exc

    def close(self) -> None:
        self.archive.close()

    def relationships(self, part: str) -> list[ET.Element]:
        rel_path = _rels_path(part)
        data = self.parts.get(rel_path)
        if data is None:
            return []
        try:
            return list(ET.fromstring(data))
        except ET.ParseError as exc:
            raise ExportError(
                "INVALID_SOURCE_PACKAGE",
                "PPTX 关系部件损坏",
                {"path": str(self.path), "part": rel_path},
            ) from exc

    def content_type(self, part: str) -> str | None:
        absolute = "/" + part
        for child in self.content_types.findall(f"{{{_NS['ct']}}}Override"):
            if child.get("PartName") == absolute:
                return child.get("ContentType")
        ext = Path(part).suffix.removeprefix(".").lower()
        for child in self.content_types.findall(f"{{{_NS['ct']}}}Default"):
            if child.get("Extension", "").lower() == ext:
                return child.get("ContentType")
        return None


def _rels_path(part: str) -> str:
    directory, filename = posixpath.split(part)
    return posixpath.join(directory, "_rels", filename + ".rels")


def _validate_zip_member_name(name: str) -> None:
    normalized = name.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or PureWindowsPath(normalized).is_absolute()
        or any(part == ".." for part in normalized.split("/"))
    ):
        raise ExportError(
            "INVALID_SOURCE_PACKAGE",
            "源文件包含不安全的 ZIP 部件路径",
            {"member": name},
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output_fingerprint(path: Path) -> str:
    resolved = path.expanduser().resolve()
    return f"sha256:{_sha256_file(resolved)}" if resolved.is_file() else "missing"


def _resolve(source_part: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target)).lstrip("/")


def _relative(source_part: str, target_part: str) -> str:
    return posixpath.relpath(target_part, posixpath.dirname(source_part))


def _slide_part(package: _Package, page_number: int) -> str:
    root = ET.fromstring(package.parts["ppt/presentation.xml"])
    slide_ids = root.find(f"{{{_P_NS}}}sldIdLst")
    if slide_ids is None:
        raise ExportError(
            "INVALID_SOURCE_PACKAGE", "PPTX 缺少页面列表", {"path": str(package.path)}
        )
    slides = []
    rels = {r.get("Id"): r for r in package.relationships("ppt/presentation.xml")}
    for item in slide_ids:
        rid = item.get(f"{{{_R_NS}}}id")
        rel = rels.get(rid)
        if rel is not None and rel.get("Type", "").endswith("/slide"):
            slides.append(_resolve("ppt/presentation.xml", rel.get("Target", "")))
    try:
        return slides[page_number - 1]
    except IndexError as exc:
        raise ExportError(
            "SLIDE_NOT_FOUND",
            "页面编号超出源文件页数",
            {"path": str(package.path), "page_number": page_number},
        ) from exc


def _slide_size(package: _Package) -> tuple[str | None, str | None]:
    root = ET.fromstring(package.parts["ppt/presentation.xml"])
    size = root.find(f"{{{_P_NS}}}sldSz")
    return (size.get("cx"), size.get("cy")) if size is not None else (None, None)


def _slide_count(package: _Package) -> int:
    root = ET.fromstring(package.parts["ppt/presentation.xml"])
    slide_ids = root.find(f"{{{_P_NS}}}sldIdLst")
    return len(slide_ids) if slide_ids is not None else 0


def _inspect_relationships(
    package: _Package,
    source_part: str,
    warnings: set[str],
    visited: set[str] | None = None,
) -> None:
    if visited is None:
        visited = set()
    if source_part in visited:
        return
    visited.add(source_part)
    for relationship in package.relationships(source_part):
        target = relationship.get("Target", "")
        mode = relationship.get("TargetMode")
        relation_type = relationship.get("Type", "")
        if mode == "External" or target.startswith(("http://", "https://", "mailto:")):
            warnings.add("EXTERNAL_LINK_PRESERVED")
            continue
        target_part = _resolve(source_part, target)
        if target_part not in package.parts:
            raise ExportError(
                "MISSING_RELATIONSHIP_TARGET",
                "页面依赖缺失，无法安全导出",
                {"part": source_part, "target": target_part},
            )
        if relation_type.endswith(("/oleObject", "/audio", "/video", "/control")):
            warnings.add("UNSUPPORTED_OBJECT_PRESERVED")
        _inspect_relationships(package, target_part, warnings, visited)


def preflight_slides(
    slides: list[SlideRef],
    *,
    verify_source_hash: bool = True,
) -> ExportPreflight:
    if not slides:
        raise ExportError("EMPTY_SELECTION", "至少选择一个页面")
    packages: dict[Path, _Package] = {}
    warnings: set[str] = set()
    try:
        for ref in slides:
            source = ref.source_path.expanduser().resolve()
            if not source.is_file():
                raise ExportError(
                    "SOURCE_NOT_FOUND",
                    "部分页面的源 PPTX 在本地不存在",
                    {"path": str(source), "file_version_id": ref.file_version_id},
                )
            if verify_source_hash and ref.expected_sha256 is not None:
                actual_sha256 = _sha256_file(source)
                if actual_sha256 != ref.expected_sha256:
                    raise ExportError(
                        "SOURCE_CHANGED",
                        "源文件已发生变化，无法安全导出选片内容",
                        {
                            "file_version_id": ref.file_version_id,
                            "path": str(source),
                            "expected_sha256": ref.expected_sha256,
                            "actual_sha256": actual_sha256,
                        },
                    )
            if source not in packages:
                packages[source] = _Package(source)

        first = packages[slides[0].source_path.expanduser().resolve()]
        expected_size = _slide_size(first)
        for ref in slides:
            package = packages[ref.source_path.expanduser().resolve()]
            actual_size = _slide_size(package)
            if actual_size != expected_size:
                raise ExportError(
                    "INCOMPATIBLE_SLIDE_SIZE",
                    "所选页面尺寸不一致",
                    {
                        "expected": expected_size,
                        "path": str(package.path),
                        "actual": actual_size,
                    },
                )
            source_slide = _slide_part(package, ref.page_number)
            _inspect_relationships(package, source_slide, warnings)

        selections_by_source: dict[Path, int] = {}
        for ref in slides:
            source = ref.source_path.expanduser().resolve()
            selections_by_source[source] = selections_by_source.get(source, 0) + 1
        estimated_bytes = sum(
            round(
                source.stat().st_size
                * min(
                    1.0,
                    0.05
                    + 0.95
                    * selections_by_source[source]
                    / max(1, _slide_count(package)),
                )
            )
            for source, package in packages.items()
        )
        return ExportPreflight(
            page_count=len(slides),
            source_count=len(packages),
            estimated_output_bytes=estimated_bytes,
            warnings=tuple(sorted(warnings)),
            fidelity_level="B" if warnings else "A",
            slide_size=expected_size,
        )
    finally:
        for package in packages.values():
            package.close()


def _clone_relationships(
    package: _Package,
    source_part: str,
    target_part: str,
    mapping: dict[str, str],
    output_parts: dict[str, bytes],
    warnings: set[str],
    recursive: bool,
    namespace_prefix: str | None = None,
    visited: set[tuple[str, str]] | None = None,
) -> None:
    """Copy a relationship closure and rewrite target paths."""
    if visited is None:
        visited = set()
    visit_key = (source_part, target_part)
    if visit_key in visited:
        return
    visited.add(visit_key)
    rel_source = _rels_path(source_part)
    source_data = package.parts.get(rel_source)
    if source_data is None:
        return
    root = ET.fromstring(source_data)
    for rel in root:
        target = rel.get("Target", "")
        mode = rel.get("TargetMode")
        rel_type = rel.get("Type", "")
        if mode == "External" or target.startswith(("http://", "https://", "mailto:")):
            # Keep the relationship element and its original r:id in the
            # destination .rels file.  The slide XML can therefore continue
            # to resolve hyperlinks and other external targets.
            warnings.add("EXTERNAL_LINK_PRESERVED")
            continue
        target_source = _resolve(source_part, target)
        if target_source not in package.parts:
            raise ExportError(
                "MISSING_RELATIONSHIP_TARGET",
                "页面依赖缺失，无法安全导出",
                {"part": source_part, "target": target_source},
            )
        mapped_target = mapping.get(target_source)
        if mapped_target is None:
            mapped_target = (
                f"{namespace_prefix}/{target_source.removeprefix('ppt/')}"
                if namespace_prefix is not None
                else target_source
            )
            mapping[target_source] = mapped_target
        if recursive:
            output_parts[mapped_target] = package.parts[target_source]
            _clone_relationships(
                package,
                target_source,
                mapped_target,
                mapping,
                output_parts,
                warnings,
                True,
                namespace_prefix,
                visited,
            )
        rel.set("Target", _relative(target_part, mapped_target))
        if rel_type.endswith(("/oleObject", "/audio", "/video", "/control")):
            warnings.add("UNSUPPORTED_OBJECT_PRESERVED")
    output_parts[_rels_path(target_part)] = _serialize_opc(root, _REL_NS)


def _prune_unreachable_parts(parts: dict[str, bytes]) -> None:
    """Remove package parts not reachable from the root relationships."""
    reachable = {"[Content_Types].xml"}
    pending: list[tuple[str, str]] = []
    root_rels = parts.get("_rels/.rels")
    if root_rels is not None:
        reachable.add("_rels/.rels")
        for relationship in ET.fromstring(root_rels):
            if relationship.get("TargetMode") == "External":
                continue
            target = posixpath.normpath(relationship.get("Target", "")).lstrip("/")
            if target:
                pending.append(("", target))
    if "ppt/presentation.xml" in parts:
        pending.append(("", "ppt/presentation.xml"))

    while pending:
        _parent, part = pending.pop()
        if part in reachable or part not in parts:
            continue
        reachable.add(part)
        rel_path = _rels_path(part)
        rel_data = parts.get(rel_path)
        if rel_data is None:
            continue
        reachable.add(rel_path)
        for relationship in ET.fromstring(rel_data):
            if relationship.get("TargetMode") == "External":
                continue
            target = _resolve(part, relationship.get("Target", ""))
            if target:
                pending.append((part, target))

    for name in tuple(parts):
        if name not in reachable:
            del parts[name]


def _prune_content_types(content_types: ET.Element, parts: dict[str, bytes]) -> None:
    for override in list(content_types.findall(f"{{{_NS['ct']}}}Override")):
        part_name = override.get("PartName", "").lstrip("/")
        if part_name not in parts:
            content_types.remove(override)


def _serialize_opc(root: ET.Element, namespace: str) -> bytes:
    """Serialize an OPC part whose single namespace must be the default xmlns.

    ``[Content_Types].xml`` and ``.rels`` parts declare their namespace with a
    bare ``xmlns=``; PowerPoint and LibreOffice reject prefixed roots such as
    ``<ns0:Types>``. ElementTree cannot emit an unprefixed default namespace
    here (``default_namespace`` rejects the unqualified attributes these parts
    use), so we rewrite the qualified element tags to local names and declare
    the namespace explicitly on the root. These parts only contain elements and
    attributes from ``namespace`` (attributes are unqualified), so this is safe.
    """
    clark_prefix = f"{{{namespace}}}"
    localized = _localize_element(root, clark_prefix)
    localized.set("xmlns", namespace)
    return bytes(ET.tostring(localized, encoding="utf-8", xml_declaration=True))


def _freeze_system_colors(data: bytes) -> bytes:
    """Freeze system colors and make explicitly selected hidden slides visible."""
    root = ET.fromstring(data)
    changed = root.attrib.pop("show", None) in {"0", "false", "False"}
    for color in root.iter(f"{{{_A_NS}}}sysClr"):
        fallback = color.get("lastClr")
        if not fallback:
            continue
        color.tag = f"{{{_A_NS}}}srgbClr"
        color.attrib.clear()
        color.set("val", fallback)
        changed = True
    if not changed:
        return data
    return cast(bytes, ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _localize_element(element: ET.Element, clark_prefix: str) -> ET.Element:
    tag = element.tag
    if isinstance(tag, str) and tag.startswith(clark_prefix):
        tag = tag[len(clark_prefix) :]
    clone = ET.Element(tag, dict(element.attrib))
    clone.text = element.text
    clone.tail = element.tail
    for child in element:
        clone.append(_localize_element(child, clark_prefix))
    return clone


def _add_content_types(
    package: _Package, mapping: dict[str, str], content_types: ET.Element
) -> None:
    existing_overrides = {
        child.get("PartName") for child in content_types.findall(f"{{{_NS['ct']}}}Override")
    }
    existing_defaults = {
        child.get("Extension", "").lower()
        for child in content_types.findall(f"{{{_NS['ct']}}}Default")
    }
    for source_part, target_part in mapping.items():
        if source_part.startswith("ppt/presentation") or source_part.startswith("ppt/slides/_rels"):
            continue
        content_type = package.content_type(source_part)
        if content_type is None:
            continue
        ext = Path(target_part).suffix.removeprefix(".").lower()
        if "/" + target_part not in existing_overrides and ext not in existing_defaults:
            content_types.append(
                ET.Element(
                    f"{{{_NS['ct']}}}Default", {"Extension": ext, "ContentType": content_type}
                )
            )
            existing_defaults.add(ext)
        elif "/" + target_part not in existing_overrides:
            content_types.append(
                ET.Element(
                    f"{{{_NS['ct']}}}Override",
                    {"PartName": "/" + target_part, "ContentType": content_type},
                )
            )
            existing_overrides.add("/" + target_part)


def _validate_output(path: Path, expected_pages: int) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            required = {
                "[Content_Types].xml",
                "ppt/presentation.xml",
                "ppt/_rels/presentation.xml.rels",
            }
            if not required.issubset(names):
                raise ExportError("OUTPUT_INVALID", "导出包缺少必需的 OOXML 部件")
            root = ET.fromstring(archive.read("ppt/presentation.xml"))
            slide_ids = root.find(f"{{{_P_NS}}}sldIdLst")
            actual = len(slide_ids) if slide_ids is not None else 0
            if actual != expected_pages:
                raise ExportError(
                    "OUTPUT_PAGE_COUNT_MISMATCH",
                    "导出页数校验失败",
                    {"expected": expected_pages, "actual": actual},
                )
            for slide in range(1, expected_pages + 1):
                if f"ppt/slides/slide{slide}.xml" not in names:
                    raise ExportError("OUTPUT_INVALID", "导出包缺少页面部件", {"slide": slide})
    except zipfile.BadZipFile as exc:
        raise ExportError("OUTPUT_INVALID", "生成的文件不是有效 PPTX") from exc


def _validate_output_parts(parts: dict[str, bytes]) -> None:
    if len(parts) > MAX_ZIP_MEMBERS:
        raise ExportError(
            "EXPORT_FAILED",
            "导出包部件数量超过安全上限",
            {"max_members": MAX_ZIP_MEMBERS},
        )
    total_size = 0
    for name, data in parts.items():
        _validate_zip_member_name(name)
        total_size += len(data)
        if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ExportError(
                "EXPORT_FAILED",
                "导出包解压后大小超过安全上限",
                {"max_uncompressed_bytes": MAX_ZIP_UNCOMPRESSED_BYTES},
            )


def export_slides(
    slides: list[SlideRef],
    output_path: Path,
    manifest_path: Path | None = None,
    *,
    expected_output_fingerprint: str | None = None,
    expected_manifest_fingerprint: str | None = None,
) -> ExportResult:
    """Copy selected pages into a new PPTX and emit a source manifest.

    The write is transactional: output is assembled next to the requested path
    and atomically renamed only after package and page-count validation.
    """
    if not slides:
        raise ExportError("EMPTY_SELECTION", "至少选择一个页面")
    output_path = output_path.expanduser().resolve()
    manifest_path = (
        (manifest_path or output_path.with_suffix(".manifest.json")).expanduser().resolve()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_suffix(output_path.suffix + ".tmp")
    packages: dict[Path, _Package] = {}
    try:
        for ref in slides:
            source = ref.source_path.expanduser().resolve()
            if ref.expected_sha256 is not None:
                actual_sha256 = _sha256_file(source)
                if actual_sha256 != ref.expected_sha256:
                    raise ExportError(
                        "SOURCE_CHANGED",
                        "源文件已发生变化，无法安全导出选片内容",
                        {
                            "file_version_id": ref.file_version_id,
                            "path": str(source),
                            "expected_sha256": ref.expected_sha256,
                            "actual_sha256": actual_sha256,
                        },
                    )
            if source not in packages:
                packages[source] = _Package(source)
        first = packages[slides[0].source_path.expanduser().resolve()]
        expected_size = _slide_size(first)
        for ref in slides:
            package = packages[ref.source_path.expanduser().resolve()]
            if _slide_size(package) != expected_size:
                raise ExportError(
                    "INCOMPATIBLE_SLIDE_SIZE",
                    "所选页面尺寸不一致",
                    {
                        "expected": expected_size,
                        "path": str(package.path),
                        "actual": _slide_size(package),
                    },
                )

        output_parts = dict(first.parts)
        presentation = ET.fromstring(output_parts["ppt/presentation.xml"])
        slide_ids = presentation.find(f"{{{_P_NS}}}sldIdLst")
        if slide_ids is None:
            slide_ids = ET.SubElement(presentation, f"{{{_P_NS}}}sldIdLst")
        master_ids = presentation.find(f"{{{_P_NS}}}sldMasterIdLst")
        if master_ids is None:
            master_ids = ET.Element(f"{{{_P_NS}}}sldMasterIdLst")
            presentation.insert(list(presentation).index(slide_ids), master_ids)
        for child in list(slide_ids):
            slide_ids.remove(child)
        presentation_rels = ET.fromstring(output_parts["ppt/_rels/presentation.xml.rels"])
        for relationship in list(presentation_rels):
            if relationship.get("Type", "").endswith("/slide"):
                presentation_rels.remove(relationship)
        used_rids = {rel.get("Id", "") for rel in presentation_rels}
        numeric_ids = [int(r[3:]) for r in used_rids if r.startswith("rId") and r[3:].isdigit()]
        next_rid = max(numeric_ids, default=0) + 1
        numeric_master_ids = [
            int(value) for item in master_ids if (value := item.get("id", "")).isdigit()
        ]
        next_master_id = max(numeric_master_ids + [2_147_483_647]) + 1
        registered_masters = {
            _resolve("ppt/presentation.xml", rel.get("Target", ""))
            for rel in presentation_rels
            if rel.get("Type", "").endswith("/slideMaster")
        }
        warnings: set[str] = set()
        manifest_items: list[dict[str, Any]] = []
        for output_order, ref in enumerate(slides, start=1):
            package = packages[ref.source_path.expanduser().resolve()]
            source_slide = _slide_part(package, ref.page_number)
            target_slide = f"ppt/slides/slide{output_order}.xml"
            mapping: dict[str, str] = {source_slide: target_slide}
            output_parts[target_slide] = _freeze_system_colors(package.parts[source_slide])
            recursive = package is not first
            if recursive:
                foreign_names = {
                    value.split("/")[1]
                    for value in output_parts
                    if value.startswith("ppt/foreign") and "/" in value
                }
                prefix = f"ppt/foreign{len(foreign_names) + 1}"
                mapping = {source_slide: target_slide}

                # Relationship closure uses a stable, collision-free prefix.
                for rel in package.relationships(source_slide):
                    if rel.get("TargetMode") == "External":
                        continue
                    target_source = _resolve(source_slide, rel.get("Target", ""))
                    if target_source in package.parts:
                        relative = target_source.removeprefix("ppt/")
                        mapping[target_source] = f"{prefix}/{relative}"
                _clone_relationships(
                    package,
                    source_slide,
                    target_slide,
                    mapping,
                    output_parts,
                    warnings,
                    True,
                    prefix,
                )
                for source_part, target_part in list(mapping.items()):
                    if source_part == source_slide or target_part in output_parts:
                        continue
                    output_parts[target_part] = package.parts[source_part]
                for source_part, target_part in mapping.items():
                    if (
                        package.content_type(source_part) != _SLIDE_MASTER_CONTENT_TYPE
                        or target_part in registered_masters
                    ):
                        continue
                    rel_id = f"rId{next_rid}"
                    next_rid += 1
                    ET.SubElement(
                        presentation_rels,
                        f"{{{_REL_NS}}}Relationship",
                        {
                            "Id": rel_id,
                            "Type": (
                                "http://schemas.openxmlformats.org/officeDocument/2006/"
                                "relationships/slideMaster"
                            ),
                            "Target": _relative("ppt/presentation.xml", target_part),
                        },
                    )
                    ET.SubElement(
                        master_ids,
                        f"{{{_P_NS}}}sldMasterId",
                        {"id": str(next_master_id), f"{{{_R_NS}}}id": rel_id},
                    )
                    next_master_id += 1
                    registered_masters.add(target_part)
            else:
                _clone_relationships(
                    package, source_slide, target_slide, mapping, output_parts, warnings, True
                )
            rel_id = f"rId{next_rid}"
            next_rid += 1
            ET.SubElement(
                presentation_rels,
                f"{{{_REL_NS}}}Relationship",
                {
                    "Id": rel_id,
                    "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
                    "Target": _relative("ppt/presentation.xml", target_slide),
                },
            )
            ET.SubElement(
                slide_ids,
                f"{{{_P_NS}}}sldId",
                {"id": str(255 + output_order), f"{{{_R_NS}}}id": rel_id},
            )
            manifest_items.append(
                {
                    "output_order": output_order,
                    "file_version_id": ref.file_version_id,
                    "source_path": str(ref.source_path),
                    "source_page_number": ref.page_number,
                    "source_part": source_slide,
                    "source_sha256": hashlib.sha256(package.parts[source_slide]).hexdigest(),
                }
            )
            _add_content_types(package, mapping, first.content_types)
        output_parts["ppt/presentation.xml"] = ET.tostring(
            presentation, encoding="utf-8", xml_declaration=True
        )
        output_parts["ppt/_rels/presentation.xml.rels"] = _serialize_opc(presentation_rels, _REL_NS)
        _prune_unreachable_parts(output_parts)
        _prune_content_types(first.content_types, output_parts)
        output_parts["[Content_Types].xml"] = _serialize_opc(first.content_types, _NS["ct"])
        _validate_output_parts(output_parts)
        export_id = f"export_{uuid.uuid4().hex}"
        fidelity = "B" if warnings else "A"
        manifest = {
            "export_id": export_id,
            "backend": "ooxml-static",
            "status": "published",
            "fidelity_level": fidelity,
            "page_count": len(slides),
            "warnings": sorted(warnings),
            "items": manifest_items,
        }
        with zipfile.ZipFile(temp_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in output_parts.items():
                archive.writestr(name, data)
        _validate_output(temp_output, len(slides))
        if (
            expected_output_fingerprint is not None
            and output_fingerprint(output_path) != expected_output_fingerprint
        ):
            raise ExportError(
                "OUTPUT_CHANGED",
                "输出位置在检查后发生变化，请重新确认导出",
                {"path": str(output_path)},
            )
        if (
            expected_manifest_fingerprint is not None
            and output_fingerprint(manifest_path) != expected_manifest_fingerprint
        ):
            raise ExportError(
                "OUTPUT_CHANGED",
                "来源清单在检查后发生变化，请重新确认导出",
                {"path": str(manifest_path)},
            )
        temp_output.replace(output_path)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return ExportResult(
            export_id, output_path, manifest_path, len(slides), tuple(sorted(warnings)), fidelity
        )
    except ExportError:
        temp_output.unlink(missing_ok=True)
        raise
    except (OSError, ET.ParseError, KeyError, zipfile.BadZipFile) as exc:
        temp_output.unlink(missing_ok=True)
        raise ExportError("EXPORT_FAILED", "PPTX 导出失败", {"reason": type(exc).__name__}) from exc
    finally:
        for package in packages.values():
            package.close()
