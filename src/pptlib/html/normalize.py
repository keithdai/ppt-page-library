"""Deterministic static DOM adapters; never evaluate source JavaScript."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import tinycss2  # type: ignore[import-untyped]
from lxml import html as lh  # type: ignore[import-untyped]

NORMALIZER_VERSION = "static-dom-v1"
_KEY = re.compile(r"[A-Za-z0-9_-]{1,160}")
_INERT = {"script", "style", "template", "noscript"}


def _class(node: Any, name: str) -> bool:
    return name in str(node.get("class", "")).split()


@dataclass(frozen=True)
class Normalization:
    adapter: str
    page_keys: list[str]
    page_sizes: dict[str, tuple[int, int]]


def _render_pages(document: Any) -> list[Any]:
    frames = [node for node in document.iter() if _class(node, "slide-frame")]
    pages = []
    for frame in frames:
        slides = [node for node in frame.iterdescendants() if _class(node, "slide")]
        if len(slides) != 1 or not slides[0].get("data-slide-key"):
            raise ValueError("Each slide-frame must contain one slide with a stable key")
        if not _KEY.fullmatch(slides[0].get("data-slide-key")):
            raise ValueError("Unsupported HTML page key")
        pages.append(slides[0])
    keys = [page.get("data-slide-key") for page in pages]
    if not keys or len(keys) > 2000 or len(set(keys)) != len(keys):
        raise ValueError("HTML pages are empty, too numerous, or have duplicate keys")
    return pages


def _static_pages(document: Any) -> tuple[str, list[Any]]:
    nodes = [
        node
        for node in document.iter()
        if isinstance(node.tag, str)
        and node.tag not in _INERT
        and not any(parent.tag in _INERT for parent in node.iterancestors())
    ]
    roots = [
        node
        for node in nodes
        if node.tag == "section"
        and node.getparent() is not None
        and _class(node.getparent(), "slides")
    ]
    if roots:
        pages: list[Any] = []

        def leaves(section: Any) -> None:
            children = list(section.iterchildren("section"))
            if children:
                for child in children:
                    leaves(child)
            else:
                pages.append(section)

        for root in roots:
            leaves(root)
        return "reveal", pages
    pages = [
        node
        for node in nodes
        if _class(node, "slide") or node.tag == "section" and "data-slide" in node.attrib
    ]
    selected = set(pages)
    if any(any(parent in selected for parent in page.iterancestors()) for page in pages):
        raise ValueError("Ambiguous nested static slides; use separate slide sections")
    return "static-slides", pages


def _has_content(page: Any) -> bool:
    text = page.xpath(
        ".//text()[not(ancestor::script or ancestor::style or ancestor::template "
        "or ancestor::noscript)]"
    )
    return bool(
        any(str(part).strip() for part in text)
        or page.xpath(".//img[@src or @srcset]|.//video|.//audio|.//svg")
        or re.search(r"background(?:-image)?\s*:", page.get("style", ""), re.I)
    )


def _dimension(value: str) -> int | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(?:px)?", value.strip(), re.I)
    if not match:
        return None
    number = float(match[1])
    return round(number) if 64 <= number <= 8192 else None


def _declarations(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in tinycss2.parse_declaration_list(text, skip_comments=True, skip_whitespace=True):
        if item.type == "declaration":
            result[item.lower_name] = tinycss2.serialize(item.value).strip()
    return result


def _selector_matches(selector: str, node: Any) -> bool:
    # Only unconditional simple selectors are inferred, not a browser CSS cascade.
    if selector == ":root":
        return bool(node.tag == "html")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", selector):
        return bool(node.tag == selector.lower())
    if re.fullmatch(r"\.[A-Za-z_][A-Za-z0-9_-]*", selector):
        return _class(node, selector[1:])
    if re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_-]*", selector):
        return bool(node.get("id") == selector[1:])
    return False


def _page_sizes(
    document: Any, pages: list[Any], stylesheets: list[str], *, legacy: bool
) -> list[tuple[int, int]]:
    rules = []
    for css in [*stylesheets, *(node.text or "" for node in document.iter("style"))]:
        for rule in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
            if rule.type == "qualified-rule":
                declarations = _declarations(tinycss2.serialize(rule.content))
                selectors = tinycss2.serialize(rule.prelude).strip().split(",")
                rules.append((selectors, declarations))
    sizes = []
    for page in pages:
        inherited: dict[str, str] = {}
        dimensions: dict[str, int] = {}
        for node in [*reversed(list(page.iterancestors())), page]:
            values: dict[str, str] = {}
            for selectors, declarations in rules:
                if any(_selector_matches(selector.strip(), node) for selector in selectors):
                    values.update(declarations)
            values.update(_declarations(node.get("style", "")))
            inherited.update({key: value for key, value in values.items() if key.startswith("--")})
            for axis in ("width", "height"):
                candidates = [
                    node.get(f"data-pptlib-{axis}", ""),
                    node.get(f"data-slide-{axis}", ""),
                    node.get(f"data-{axis}", ""),
                    node.get(axis, ""),
                    values.get(axis, ""),
                    inherited.get(f"--slide-{axis}", ""),
                ]
                for value in candidates:
                    variable = re.fullmatch(r"var\((--[A-Za-z0-9_-]+)\)", value.strip())
                    if variable:
                        value = inherited.get(variable[1], "")
                    dimension = _dimension(value)
                    if dimension is not None:
                        dimensions[axis] = dimension
                        break
        default = (1920, 1080) if legacy else (1280, 720)
        sizes.append((dimensions.get("width", default[0]), dimensions.get("height", default[1])))
    return sizes


def normalize_document(document: Any, *, stylesheets: list[str] | None = None) -> Normalization:
    """Normalize generic pages in place; leave validated render-deck DOM untouched.

    Generic geometry is exposed on both frame and slide through data-pptlib-width
    and data-pptlib-height. Numeric attributes, inline CSS and simple stylesheet
    selectors are supported; script configuration is never executed or inferred.
    """
    generators = document.xpath(
        "//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
        "'abcdefghijklmnopqrstuvwxyz')='fs-deck-generator']/@content"
    )
    legacy = "render-deck" in generators
    if legacy:
        if generators != ["render-deck"]:
            raise ValueError("Conflicting render-deck generator markers")
        pages = _render_pages(document)
        adapter = "render-deck"
    else:
        adapter, pages = _static_pages(document)
        if not pages:
            raise ValueError(
                "Unrecognized HTML presentation: expected render-deck, Reveal .slides > section, "
                "or .slide / section[data-slide]. "
                "Ordinary websites and JS-only apps are unsupported."
            )
        if len(pages) > 2000:
            raise ValueError("HTML page count exceeds 2000")
        if not any(_has_content(page) for page in pages):
            raise ValueError(
                "HTML slides have no static content; JS-only presentations are unsupported"
            )
    sizes = _page_sizes(document, pages, stylesheets or [], legacy=legacy)
    keys: list[str] = []
    if legacy:
        keys = [str(page.get("data-slide-key")) for page in pages]
        return Normalization(adapter, keys, dict(zip(keys, sizes, strict=True)))

    candidates = [
        next(
            (
                value
                for attr in ("data-slide-key", "id", "data-slide")
                if _KEY.fullmatch(value := page.get(attr, ""))
            ),
            f"page-{number:04d}",
        )
        for number, page in enumerate(pages, 1)
    ]
    reserved = set(candidates)
    used: set[str] = set()
    for page, candidate, (width, height) in zip(pages, candidates, sizes, strict=True):
        key = candidate
        suffix = 2
        while key in used or (key != candidate and key in reserved):
            ending = f"-{suffix}"
            key = candidate[: 160 - len(ending)] + ending
            suffix += 1
        used.add(key)
        keys.append(key)
        original_key = page.get("data-slide-key")
        if original_key and original_key != key:
            page.set("data-pptlib-original-key", original_key)
        page.set("data-slide-key", key)
        if not _class(page, "slide"):
            page.set("class", (page.get("class", "") + " slide").strip())
        parent = page.getparent()
        if parent is None or page.tag in {"html", "body"}:
            raise ValueError("HTML document root cannot be a slide")
        if _class(parent, "slide-frame") and len(parent) == 1:
            frame = parent
        else:
            frame = lh.Element("div", {"class": "slide-frame"})
            parent.replace(page, frame)
            frame.append(page)
            frame.tail, page.tail = page.tail, None
        if any(_class(ancestor, "slide-frame") for ancestor in frame.iterancestors()):
            raise ValueError("Ambiguous nested slide frames")
        for node in (page, frame):
            node.set("data-pptlib-width", str(width))
            node.set("data-pptlib-height", str(height))
    return Normalization(adapter, keys, dict(zip(keys, sizes, strict=True)))
