"""The only application boundary that knows the external render-deck CLI layout."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import unquote_to_bytes

from lxml import html as lh  # type: ignore[import-untyped]

from pptlib.html.source import HtmlSource, css_urls, parse_document
from pptlib.ingestion.parser import ParsedDeck, ParsedSlide

ADAPTER_VERSION = "render-deck-adapter-v1"
_DATA_URI = re.compile(r"""data:[^\s"'<>)]*""", re.I)


@dataclass(frozen=True)
class HtmlIngest:
    parsed: ParsedDeck
    canonical: dict[str, Any]
    fingerprint: str


def engine_cli() -> Path:
    configured = os.environ.get("PPTLIB_HTML_ENGINE")
    root = Path(configured) if configured else Path.home() / "Projects" / "feishu-deck-h5"
    path = (
        root if root.is_file() else (root / "skills/feishu-deck-h5/deck-json/sync-index-to-deck.py")
    )
    if not path.is_file():
        raise ValueError("未找到 HTML 格式引擎，请设置 PPTLIB_HTML_ENGINE 为 feishu-deck-h5 路径")
    return path.resolve()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ingest_html(
    source: HtmlSource,
    *,
    temp_dir: Path,
) -> HtmlIngest:
    """Replace media with references *before* invoking exact backfill.

    The subprocess sees a lightweight structural document. Neither temporary
    files nor the DB receive the source's embedded videos/images.
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

    # Work on a fresh tree: preview continues to use the untouched source.
    document = parse_document(source.html)
    script_count = len(document.xpath("//script"))
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
    cli = engine_cli()
    engine_version = hashlib.sha256(cli.read_bytes()).hexdigest()
    temp_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="html-backfill-", dir=temp_dir) as directory:
        job = Path(directory)
        index = job / "index.html"
        output = job / "deck.json"
        index.write_text(structural, encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, "-B", str(cli), str(index), str(output), "--backfill"],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(f"HTML 格式引擎运行失败：{type(error).__name__}") from error
        if result.returncode or not output.is_file():
            raise ValueError("HTML 无法精确恢复：" + (result.stderr or result.stdout)[-600:])
        canonical = json.loads(output.read_text("utf-8"))
    pages = canonical.get("slides", [])
    if [page.get("key") for page in pages] != source.page_keys:
        raise ValueError("HTML 引擎恢复的页面顺序或 key 与来源不一致")
    warnings = []
    if script_count:
        warnings.append("隔离预览保留 CSS 动效和原生媒体播放，不执行来源脚本或自定义播放器。")
    if iframe_count:
        warnings.append("来源包含 iframe；隔离预览不加载嵌入网页。")
    if source.external_urls:
        warnings.append("来源包含联网依赖；离线预览已阻断外部网络，部分内容可能缺失。")
    slides = []
    for number, page in enumerate(pages, 1):
        key = page["key"]
        frame = source.document.xpath("//*[@data-slide-key=$key]", key=key)[0]
        text_node = parse_document(lh.tostring(frame, encoding="unicode"))
        for excluded in text_node.xpath("//script|//style"):
            excluded.drop_tree()
        title_nodes = text_node.xpath(
            "//*[contains(concat(' ',normalize-space(@class),' '),' title-zh ')]|//h1|//h2"
        )
        title = (
            " ".join(title_nodes[0].text_content().split())
            if title_nodes
            else str(page.get("screen_label") or key)
        )
        text = " ".join(text_node.text_content().split())
        capability = {
            "dynamic_preview": True,
            "video": bool(frame.xpath(".//video")),
            "iframe": bool(frame.xpath(".//iframe")),
            "requires_network": bool(source.external_urls),
            "html_export": False,
            "pptx_export": False,
        }
        slides.append(
            ParsedSlide(
                number,
                title[:1000],
                text[:100_000],
                "",
                page_key=key,
                page_kind="h5_raw",
                capabilities_json=_json(capability),
                composition_ref_json=_json(
                    {
                        "adapter": ADAPTER_VERSION,
                        "adapter_version": engine_version,
                        "source_sha256": source.fingerprint,
                        "page_key": key,
                        "entry": source.entry,
                    }
                ),
            )
        )
    canonical["pptlib"] = {
        "adapter": ADAPTER_VERSION,
        "engine_sha256": engine_version,
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
            canonical_format="deckjson_reference",
            dependencies_json=_json(list(dependencies.values())),
            capabilities_json=_json({"html_export": False, "pptx_export": False}),
            warnings_json=_json(warnings),
            renderer_version=engine_version,
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
