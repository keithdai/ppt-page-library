"""Read DeckAtlas's isolated offline pages back into the library without script execution."""

from __future__ import annotations

import base64
import json
import re
from copy import deepcopy
from typing import Any

from lxml import html as lh  # type: ignore[import-untyped]

from pptlib.html.normalize import normalize_document


def _expand_css(text: str, depth: int = 0) -> str:
    if depth > 16:
        raise ValueError("离线样式依赖层级过深")

    def inline(match: re.Match[str]) -> str:
        raw = base64.b64decode(match[1], validate=True).decode("utf-8")
        content = _expand_css(raw, depth + 1)
        media = match[2].strip()
        return "@media " + media + "{" + content + "}" if media else content

    return re.sub(
        r'@import\s+url\("data:text/css;base64,([A-Za-z0-9+/=]+)"\)\s*([^;]*);',
        inline, text,
    )


def unpack_offline_deck(document: Any) -> tuple[Any, dict[str, Any]]:
    markers = document.xpath("//meta[@name='deckatlas-format']/@content")
    if not markers:
        return document, {}
    if markers != ["pptlib-offline-html-v1"]:
        raise ValueError("不支持此 DeckAtlas HTML 版本")
    containers = document.xpath("//section[@class='page']")
    if not 1 <= len(containers) <= 2000:
        raise ValueError("离线演示的页面数量不正确")
    combined = lh.document_fromstring(
        '<html><head><meta name="fs-deck-generator" content="render-deck"></head>'
        "<body></body></html>"
    )
    title = document.find(".//title")
    if title is not None:
        combined.find("head").append(deepcopy(title))
    notes: dict[str, str] = {}
    page_documents: dict[str, Any] = {}
    for number, container in enumerate(containers, 1):
        frames = container.xpath("./iframe[@srcdoc]")
        if len(frames) != 1:
            raise ValueError("离线演示页面缺少独立内容")
        frame = frames[0]
        raw = frame.get("srcdoc")
        parser = lh.HTMLParser(encoding="utf-8", no_network=True, huge_tree=True)
        child = lh.document_fromstring(raw.encode("utf-8"), parser=parser)
        for link in child.xpath("//link[@rel='stylesheet']"):
            uri = str(link.get("href", ""))
            if uri.startswith("data:text/css;base64,"):
                style = lh.Element("style")
                style.text = _expand_css(
                    base64.b64decode(uri.partition(",")[2], validate=True).decode("utf-8")
                )
                link.getparent().replace(link, style)
        key = f"page-{number:04d}"
        slides = child.xpath(
            "//*[contains(concat(' ',normalize-space(@class),' '),' slide ')]"
        )
        if not slides:
            # Mixed PPTX pages are deliberately re-imported as their exported image.
            body = child.find("body")
            slide = lh.Element("div", {"class": "slide"})
            slide.text, body.text = body.text, None
            for node in list(body):
                slide.append(node)
            body.append(slide)
            slides = [slide]
        if len(slides) != 1:
            raise ValueError("离线演示的独立页面结构不正确")
        slide = slides[0]
        slide.set("data-slide-key", key)
        slide.set("data-title", frame.get("title", key))
        for axis, fallback in (("width", "1920"), ("height", "1080")):
            size = frame.get(f"data-{axis}", fallback)
            if not size.isdigit() or not 64 <= int(size) <= 8192:
                raise ValueError("离线演示的画布尺寸不正确")
            slide.set(f"data-pptlib-{axis}", size)
        normalize_document(child)
        page_documents[key] = child
        branch = lh.Element("div")
        for node in child.xpath("//style|//link[@rel='stylesheet']"):
            branch.append(deepcopy(node))
        selected = child.xpath(
            "//*[contains(concat(' ',normalize-space(@class),' '),' slide-frame ')]"
        )
        if len(selected) != 1:
            raise ValueError("离线演示的页面容器不正确")
        branch.append(deepcopy(selected[0]))
        combined.find("body").append(branch)
        note = container.xpath("./pre[@class='page-notes']")
        if note:
            notes[key] = note[0].text_content()
    note_node = lh.Element("script", type="application/json", id="fs-deck-notes")
    note_node.text = json.dumps(notes, ensure_ascii=False)
    combined.find("body").append(note_node)
    return combined, page_documents
