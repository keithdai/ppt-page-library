"""Native indexing and lightweight references for static HTML presentations."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes

from lxml import html as lh  # type: ignore[import-untyped]

from pptlib.html.source import HtmlSource, css_urls
from pptlib.ingestion.parser import ParsedDeck, ParsedSlide

ADAPTER_VERSION = "native-html-adapter-v1"
_DATA_URI = re.compile(r"""data:[^\s"'<>)]*""", re.I)


@dataclass(frozen=True)
class HtmlIngest:
    parsed: ParsedDeck
    canonical: dict[str, Any]
    fingerprint: str


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _notes(document: Any, warnings: list[str]) -> dict[str, str]:
    notes: dict[str, str] = {}
    for node in document.xpath("//script[@id='fs-deck-notes']"):
        try:
            payload = json.loads(node.text or "")
        except (ValueError, RecursionError):
            warnings.append("Invalid fs-deck-notes JSON; embedded speaker notes were not indexed.")
            continue
        if not isinstance(payload, dict) or any(
            not isinstance(value, str) for value in payload.values()
        ):
            warnings.append("Unsupported fs-deck-notes format; expected a page-key to text map.")
            continue
        notes.update(payload)
    return notes


def _text(node: Any) -> str:
    return " ".join(" ".join(node.itertext()).split())


def ingest_html(
    source: HtmlSource,
    *,
    temp_dir: Path,
) -> HtmlIngest:
    """Index normalized DOM without an external engine or source execution.

    ``temp_dir`` remains for caller compatibility; native ingest needs no files.
    Neither the canonical manifest nor the DB receives source media payloads.
    """
    dependencies: dict[str, dict[str, Any]] = {}
    current_key = ""

    def replace_uri(uri: str) -> str:
        header, separator, payload = uri.partition(",")
        if not separator:
            raise ValueError("HTML 包含无效的 data URI")
        mime = header[5:].split(";")[0].lower() or "text/plain"
        if not (
            mime.startswith(("image/", "video/", "audio/", "font/"))
            or mime in {"application/font-woff", "application/vnd.ms-fontobject"}
        ):
            raise ValueError(f"不支持的内嵌资源类型：{mime}")
        try:
            raw = (
                base64.b64decode(payload, validate=True)
                if ";base64" in header.lower()
                else unquote_to_bytes(payload)
            )
        except ValueError as error:
            raise ValueError("HTML 包含无效的 Base64 资源") from error
        digest = hashlib.sha256(raw).hexdigest()
        item = dependencies.setdefault(
            digest,
            {
                "kind": "data_uri",
                "sha256": digest,
                "mime": mime,
                "size_bytes": len(raw),
                "page_keys": [],
                "locator": {"uri_sha256": hashlib.sha256(uri.encode()).hexdigest()},
            },
        )
        if current_key not in item["page_keys"]:
            item["page_keys"].append(current_key)
        return "pptlib-dependency:" + digest

    # Work on a fresh normalized tree: preview keeps its original source view.
    document = deepcopy(source.document)
    warnings: list[str] = []
    notes = _notes(document, warnings)
    script_count = len(document.xpath("//script[not(@type='application/json')]"))
    iframe_count = len(document.xpath("//iframe"))
    for node in document.xpath("//script"):
        node.drop_tree()
    for node in document.iter():
        if not isinstance(node.tag, str):
            continue
        owners = node.xpath("ancestor-or-self::*[@data-slide-key]/@data-slide-key")
        current_key = str(owners[-1]) if owners else "*"
        for name, value in list(node.attrib.items()):
            if name.lower().startswith("on") or name.lower() == "srcdoc":
                del node.attrib[name]
            elif "data:" in value.lower():
                node.set(name, _DATA_URI.sub(lambda match: replace_uri(match.group()), value))
        if node.tag.lower() == "style" and node.text:
            for uri in css_urls(node.text):
                if uri.lower().startswith("data:"):
                    node.text = node.text.replace(uri, replace_uri(uri))
    for ref, resource in source.resources.items():
        dependencies["local:" + ref] = {
            "kind": "local",
            "reference": ref,
            **resource,
            "page_keys": ["*"],
        }
    for url in sorted(source.external_urls):
        dependencies["external:" + url] = {
            "kind": "external",
            "url": url,
            "page_keys": ["*"],
            "available": False,
        }
    structural = lh.tostring(document, encoding="unicode", method="html")
    if len(structural.encode()) > 8 * 1024 * 1024:
        raise ValueError("去除媒体后的 HTML 结构超过 8 MB 限制")
    if script_count:
        warnings.append("隔离预览保留 CSS 动效和原生媒体播放，不执行来源脚本或自定义播放器。")
    if iframe_count:
        warnings.append("来源包含 iframe；隔离预览不加载嵌入网页。")
    if source.external_urls:
        warnings.append("来源包含联网依赖；离线预览已阻断外部网络，部分内容可能缺失。")
    slides = []
    pages = []
    page_nodes = {
        node.get("data-slide-key"): node
        for node in document.xpath(
            "//*[contains(concat(' ',normalize-space(@class),' '),' slide ')][@data-slide-key]"
        )
    }
    for number, key in enumerate(source.page_keys, 1):
        frame = page_nodes[key]
        text_node = deepcopy(frame)
        for excluded in text_node.xpath(".//script|.//style|.//template|.//noscript"):
            excluded.drop_tree()
        page_notes = ""
        for alias in (
            key,
            frame.get("data-pptlib-original-key"),
            frame.get("id"),
            frame.get("data-slide"),
        ):
            if alias in notes:
                page_notes = notes[alias]
                break
        for excluded in text_node.xpath(
            ".//*[contains(concat(' ',normalize-space(@class),' '),' notes ')]"
        ):
            inline_notes = _text(excluded)
            if inline_notes and inline_notes != page_notes:
                page_notes = "\n\n".join(part for part in (page_notes, inline_notes) if part)
            excluded.drop_tree()
        title_nodes = text_node.xpath(
            ".//*[contains(concat(' ',normalize-space(@class),' '),' title-zh ')]|.//h1|.//h2|.//h3"
        )
        title = next((text for node in title_nodes if (text := _text(node))), "") or str(
            frame.get("data-screen-label")
            or frame.get("data-title")
            or frame.get("aria-label")
            or key
        )
        text = _text(text_node)
        capability = {
            "dynamic_preview": True,
            "video": bool(frame.xpath(".//video")),
            "iframe": bool(frame.xpath(".//iframe")),
            "requires_network": bool(source.external_urls),
            "html_export": True,
            "pptx_export": False,
        }
        width, height = source.page_sizes[key]
        reference = {
            "adapter": ADAPTER_VERSION,
            "adapter_version": source.normalizer_version,
            "dom_adapter": source.dom_adapter,
            "source_sha256": source.fingerprint,
            "entry_sha256": source.entry_sha256,
            "page_key": key,
            "entry": source.entry,
            "width": width,
            "height": height,
        }
        slides.append(
            ParsedSlide(
                number,
                title[:1000],
                text[:100_000],
                page_notes,
                page_key=key,
                page_kind="h5_raw",
                capabilities_json=_json(capability),
                composition_ref_json=_json(reference),
            )
        )
        pages.append(
            {
                "key": key,
                "layout": "raw",
                "screen_label": title[:1000],
                "notes": page_notes,
                "width": width,
                "height": height,
                "composition_ref": reference,
            }
        )
    canonical: dict[str, Any] = {
        "format": "pptlib-html-reference",
        "version": 1,
        "slides": pages,
    }
    canonical["pptlib"] = {
        "adapter": ADAPTER_VERSION,
        "normalizer_version": source.normalizer_version,
        "dom_adapter": source.dom_adapter,
        "source_sha256": source.fingerprint,
        "entry_sha256": source.entry_sha256,
        "entry": source.entry,
        "page_keys": source.page_keys,
        "dependencies": list(dependencies.values()),
        "warnings": warnings,
        "context": "Rehydrate media and global styles from the locked source before composing.",
    }
    return HtmlIngest(
        ParsedDeck(
            tuple(slides),
            parser_version=ADAPTER_VERSION,
            source_format="render_deck_html",
            canonical_format="html_dom_reference",
            dependencies_json=_json(list(dependencies.values())),
            capabilities_json=_json({"html_export": True, "pptx_export": False}),
            warnings_json=_json(warnings),
            renderer_version=source.normalizer_version,
        ),
        canonical,
        source.fingerprint,
    )


def write_canonical(result: HtmlIngest, assets_dir: Path, version_id: str) -> None:
    target = assets_dir / "html-manifests" / f"{version_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(_json(result.canonical), encoding="utf-8")
    temporary.replace(target)
