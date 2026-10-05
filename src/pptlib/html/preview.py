"""A capability-scoped, read-only preview of one untrusted HTML slide."""

from __future__ import annotations

import base64
import hashlib
import hmac
import posixpath
import re
import secrets
import socket
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, unquote, urlsplit

import tinycss2  # type: ignore[import-untyped]
from lxml import etree, html  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from pptlib.html.source import HtmlSource

_MAX_BYTES = 128 * 1024 * 1024
_MAX_PARTS = 20_000
_MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_NODES = 200_000
_MAX_CSS_DEPTH = 64
_HTML_TAGS = set(
    [
        "html",
        "head",
        "body",
        "title",
        "style",
        "link",
        "div",
        "span",
        "section",
        "article",
        "main",
        "header",
        "footer",
        "aside",
        "nav",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "a",
        "b",
        "i",
        "u",
        "s",
        "strong",
        "em",
        "small",
        "sub",
        "sup",
        "br",
        "hr",
        "pre",
        "code",
        "blockquote",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "col",
        "colgroup",
        "caption",
        "figure",
        "figcaption",
        "img",
        "picture",
        "video",
        "audio",
        "source",
        "track",
        "wbr",
    ]
)
_SVG_TAGS = {
    name.lower(): name
    for name in [
        "svg",
        "g",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "defs",
        "symbol",
        "use",
        "image",
        "title",
        "desc",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "mask",
        "pattern",
        "filter",
        "feBlend",
        "feColorMatrix",
        "feComponentTransfer",
        "feComposite",
        "feConvolveMatrix",
        "feDiffuseLighting",
        "feDisplacementMap",
        "feDistantLight",
        "feDropShadow",
        "feFlood",
        "feFuncA",
        "feFuncB",
        "feFuncG",
        "feFuncR",
        "feGaussianBlur",
        "feMerge",
        "feMergeNode",
        "feMorphology",
        "feOffset",
        "fePointLight",
        "feSpecularLighting",
        "feSpotLight",
        "feTile",
        "feTurbulence",
    ]
}
_ATTRS = set(
    [
        "id",
        "class",
        "style",
        "title",
        "lang",
        "dir",
        "role",
        "width",
        "height",
        "alt",
        "colspan",
        "rowspan",
        "span",
        "scope",
        "start",
        "reversed",
        "value",
        "type",
        "media",
        "sizes",
        "kind",
        "label",
        "srclang",
        "default",
        "controls",
        "muted",
        "preload",
        "playsinline",
        "rel",
    ]
)
_SVG_ATTRS = {
    name.lower(): name
    for name in [
        "x",
        "y",
        "x1",
        "x2",
        "y1",
        "y2",
        "dx",
        "dy",
        "r",
        "rx",
        "ry",
        "cx",
        "cy",
        "d",
        "points",
        "viewBox",
        "preserveAspectRatio",
        "transform",
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "opacity",
        "color",
        "offset",
        "stop-color",
        "stop-opacity",
        "gradientUnits",
        "gradientTransform",
        "spreadMethod",
        "patternUnits",
        "patternContentUnits",
        "patternTransform",
        "clip-path",
        "clip-rule",
        "clipPathUnits",
        "mask",
        "maskUnits",
        "maskContentUnits",
        "filter",
        "filterUnits",
        "primitiveUnits",
        "in",
        "in2",
        "result",
        "stdDeviation",
        "mode",
        "values",
        "operator",
        "k1",
        "k2",
        "k3",
        "k4",
        "radius",
        "scale",
        "seed",
        "baseFrequency",
        "numOctaves",
        "stitchTiles",
        "flood-color",
        "flood-opacity",
        "lighting-color",
        "surfaceScale",
        "diffuseConstant",
        "specularConstant",
        "specularExponent",
        "limitingConeAngle",
        "azimuth",
        "elevation",
        "targetX",
        "targetY",
        "order",
        "kernelMatrix",
        "divisor",
        "bias",
        "edgeMode",
        "preserveAlpha",
        "text-anchor",
        "dominant-baseline",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "letter-spacing",
        "textLength",
        "lengthAdjust",
        "vector-effect",
    ]
}
_CSS_FUNCTIONS = set(
    [
        "var",
        "env",
        "calc",
        "min",
        "max",
        "clamp",
        "rgb",
        "rgba",
        "hsl",
        "hsla",
        "hwb",
        "lab",
        "lch",
        "oklab",
        "oklch",
        "color",
        "color-mix",
        "light-dark",
        "linear-gradient",
        "radial-gradient",
        "conic-gradient",
        "repeating-linear-gradient",
        "repeating-radial-gradient",
        "repeating-conic-gradient",
        "matrix",
        "matrix3d",
        "translate",
        "translatex",
        "translatey",
        "translatez",
        "translate3d",
        "scale",
        "scalex",
        "scaley",
        "scalez",
        "scale3d",
        "rotate",
        "rotatex",
        "rotatey",
        "rotatez",
        "rotate3d",
        "skew",
        "skewx",
        "skewy",
        "perspective",
        "cubic-bezier",
        "steps",
        "linear",
        "blur",
        "brightness",
        "contrast",
        "drop-shadow",
        "grayscale",
        "hue-rotate",
        "invert",
        "opacity",
        "saturate",
        "sepia",
        "circle",
        "ellipse",
        "inset",
        "polygon",
        "path",
        "rect",
        "xywh",
        "counter",
        "counters",
        "symbols",
        "repeat",
        "minmax",
        "fit-content",
        "format",
        "tech",
        "local",
        "not",
        "is",
        "where",
        "has",
        "nth-child",
        "nth-last-child",
        "nth-of-type",
        "nth-last-of-type",
        "selector",
        "supports",
        "font-tech",
        "font-format",
    ]
)
_MIMES = {
    "text/css",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "image/svg+xml",
    "image/bmp",
    "image/x-icon",
    "video/mp4",
    "video/webm",
    "video/ogg",
    "video/quicktime",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-wav",
    "font/woff",
    "font/woff2",
    "font/ttf",
    "font/otf",
    "application/font-woff",
    "application/vnd.ms-fontobject",
    "text/vtt",
}
_INLINE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "video/mp4",
    "video/webm",
    "video/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "font/woff",
    "font/woff2",
    "font/ttf",
    "font/otf",
}
_RUNTIME = """(() => {
  const root = document.documentElement;
  const frame = document.querySelector('.slide-frame');
  const slide = frame && frame.querySelector('.slide');
  const fit = () => {
    const scale = Math.min(innerWidth / 1920, innerHeight / 1080);
    root.style.setProperty('--fs-scale', String(scale));
    if (frame) {
      frame.style.setProperty('width', (1920 * scale) + 'px', 'important');
      frame.style.setProperty('height', (1080 * scale) + 'px', 'important');
    }
    if (slide) slide.style.setProperty('transform', 'scale(' + scale + ')', 'important');
  };
  document.querySelectorAll('video,audio').forEach(media => {
    media.autoplay = false;
    media.controls = true;
    media.preload = 'metadata';
    media.pause();
  });
  fit();
  addEventListener('resize', fit);
  root.dataset.pptlibReady = 'true';
})();"""
_RUNTIME_HASH = base64.b64encode(hashlib.sha256(_RUNTIME.encode()).digest()).decode()
_LAYOUT = """
html, body { margin:0!important; padding:0!important; width:100%!important;
  height:100%!important; overflow:hidden!important; }
body { display:flex!important; align-items:center!important; justify-content:center!important; }
[data-pptlib-wrapper] { display:contents!important; }
.slide-frame { display:block!important; position:relative!important; margin:0!important;
  padding:0!important; border:0!important; overflow:hidden!important;
  opacity:1!important; visibility:visible!important; flex:none!important; }
.slide-frame > .slide { display:block!important; position:absolute!important;
  left:0!important; top:0!important; width:1920px!important; height:1080px!important;
  margin:0!important; transform-origin:0 0!important; opacity:1!important;
  visibility:visible!important; }
"""
_STATIC = """
*, *::before, *::after { animation:none!important; transition:none!important;
  caret-color:transparent!important; scroll-behavior:auto!important; }
"""


class PreviewWarning(UserWarning):
    """A source feature was blocked by the preview's security policy."""


def _tag(node: Any) -> str:
    return str(node.tag).rsplit("}", 1)[-1].lower() if isinstance(node.tag, str) else ""


def _safe_ref(value: str, base: str = "") -> tuple[str, str] | None:
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value.strip())
        path = unquote(parsed.path, errors="strict")
    except (ValueError, UnicodeError):
        return None
    if (
        parsed.scheme
        or parsed.netloc
        or path.startswith(("/", "\\"))
        or "\\" in path
        or "\x00" in path
        or ":" in path
        or "%" in path
        or parsed.query
    ):
        return None
    if not path:
        return ("", parsed.fragment) if parsed.fragment else None
    ref = posixpath.normpath(posixpath.join(base, path))
    if ref in {".", ".."} or ref.startswith("../"):
        return None
    return ref, parsed.fragment


class _Sanitizer:
    def __init__(self, source: HtmlSource, prefix: str) -> None:
        self.source = source
        self.prefix = prefix
        self.blocked: set[str] = set()
        self.resources: dict[str, tuple[str, int, str]] = {}
        self.inline_resources: dict[str, tuple[bytes, str]] = {}
        self.inline_bytes = 0
        if len(source.html.encode("utf-8")) > _MAX_BYTES:
            raise ValueError("HTML preview exceeds size limit")
        if len(source.resources) > _MAX_PARTS:
            raise ValueError("HTML preview exceeds resource count limit")
        total = 0
        for ref, metadata in source.resources.items():
            parsed = _safe_ref(ref)
            size = metadata["size_bytes"]
            digest = metadata["sha256"]
            mime = metadata["mime"]
            if (
                parsed != (ref, "")
                or not isinstance(size, int)
                or not 0 <= size <= _MAX_BYTES
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(mime, str)
            ):
                raise ValueError("Invalid preview resource manifest")
            total += size
            if total > _MAX_PACKAGE_BYTES:
                raise ValueError("HTML preview exceeds package size limit")
            if mime in _MIMES:
                self.resources[ref] = (digest, size, mime)
            else:
                self.blocked.add("unsupported resource MIME type")

    def _inline_url(self, value: str) -> str:
        header, separator, payload = value.partition(",")
        if not separator or ";base64" not in header.lower():
            self.blocked.add("unsupported inline resource")
            return ""
        mime = header[5:].split(";", 1)[0].lower()
        if mime not in _INLINE_MIMES:
            self.blocked.add("unsupported inline resource MIME type")
            return ""
        try:
            content = base64.b64decode(payload, validate=True)
        except ValueError:
            self.blocked.add("invalid inline resource")
            return ""
        digest = hashlib.sha256(content).hexdigest()
        ref = f"inline/{digest}"
        if ref not in self.inline_resources:
            if len(content) > _MAX_BYTES or self.inline_bytes + len(content) > _MAX_BYTES:
                raise ValueError("Selected HTML page exceeds inline resource limit")
            self.inline_resources[ref] = (content, mime)
            self.inline_bytes += len(content)
            self.resources[ref] = (digest, len(content), mime)
        return self.prefix + "resource/" + quote(ref, safe="")

    def url(self, value: str, base: str = "", *, fragment_only: bool = False) -> str:
        if value.strip().lower().startswith("data:") and not fragment_only:
            return self._inline_url(value.strip())
        resolved = _safe_ref(value, base)
        if resolved:
            ref, fragment = resolved
            if not ref:
                return "#" + quote(fragment, safe="")
            if not fragment_only and ref in self.resources:
                suffix = "#" + quote(fragment, safe="") if fragment else ""
                return self.prefix + "resource/" + quote(ref, safe="") + suffix
        self.blocked.add("remote, unsafe, or unknown resource URL")
        return ""

    def _tokens(self, tokens: list[Any], base: str, depth: int = 0) -> str:
        if depth > _MAX_CSS_DEPTH:
            raise ValueError("CSS nesting exceeds preview limit")
        result: list[str] = []
        for token in tokens:
            kind = token.type
            if kind in {"error", "bad-url", "bad-string"}:
                self.blocked.add("invalid CSS")
                continue
            if kind == "url":
                result.append('url("' + self.url(str(token.value), base) + '")')
            elif kind == "function":
                name = str(token.lower_name)
                args = token.arguments
                if name == "url":
                    args = [arg for arg in args if arg.type not in {"whitespace", "comment"}]
                    value = (
                        str(args[0].value) if len(args) == 1 and args[0].type == "string" else ""
                    )
                    result.append('url("' + self.url(value, base) + '")')
                elif name in {"image-set", "-webkit-image-set"}:
                    parts = [
                        'url("' + self.url(str(arg.value), base) + '")'
                        if arg.type == "string"
                        else self._tokens([arg], base, depth + 1)
                        for arg in args
                    ]
                    result.append(name + "(" + "".join(parts) + ")")
                elif name in _CSS_FUNCTIONS:
                    result.append(name + "(" + self._tokens(args, base, depth + 1) + ")")
                else:
                    self.blocked.add("unsupported CSS function")
            elif kind in {"{} block", "[] block", "() block"}:
                delimiters = {
                    "{} block": ("{", "}"),
                    "[] block": ("[", "]"),
                    "() block": ("(", ")"),
                }
                left, right = delimiters[kind]
                result.append(left + self._tokens(token.content, base, depth + 1) + right)
            else:
                result.append(str(token.serialize()))
        return "".join(result).replace("<", "\\3c ")

    def css(self, text: str, base: str = "", *, inline: bool = False, depth: int = 0) -> str:
        if depth > _MAX_CSS_DEPTH or len(text.encode("utf-8")) > _MAX_BYTES:
            raise ValueError("CSS exceeds preview limit")
        rules = (
            tinycss2.parse_declaration_list(text, skip_comments=True, skip_whitespace=True)
            if inline
            else tinycss2.parse_stylesheet(text, skip_comments=True, skip_whitespace=True)
        )
        result: list[str] = []
        for rule in rules:
            if rule.type == "declaration":
                if str(rule.lower_name) in {"behavior", "-moz-binding"}:
                    self.blocked.add("executable CSS")
                    continue
                value = self._tokens(rule.value, base)
                important = "!important" if rule.important else ""
                name = tinycss2.serialize_identifier(rule.name)
                result.append(f"{name}:{value}{important};")
            elif rule.type == "qualified-rule":
                selector = self._tokens(rule.prelude, base)
                body = self.css(
                    tinycss2.serialize(rule.content), base, inline=True, depth=depth + 1
                )
                result.append(selector + "{" + body + "}")
            elif rule.type == "at-rule" and not inline:
                name = str(rule.lower_at_keyword)
                if name == "import" and rule.content is None:
                    args = list(rule.prelude)
                    while args and args[0].type in {"whitespace", "comment"}:
                        args.pop(0)
                    if args:
                        first = args[0]
                        if first.type == "string":
                            first_url = 'url("' + self.url(str(first.value), base) + '")'
                        else:
                            first_url = self._tokens([first], base)
                        result.append(
                            "@import " + first_url + " " + self._tokens(args[1:], base) + ";"
                        )
                elif rule.content is not None and name in {
                    "media",
                    "supports",
                    "layer",
                    "container",
                    "keyframes",
                    "-webkit-keyframes",
                    "font-face",
                    "page",
                    "property",
                }:
                    prelude = self._tokens(rule.prelude, base).strip()
                    body = self.css(
                        tinycss2.serialize(rule.content),
                        base,
                        inline=name in {"font-face", "page", "property"},
                        depth=depth + 1,
                    )
                    result.append("@" + name + " " + prelude + "{" + body + "}")
                else:
                    self.blocked.add("unsupported CSS at-rule")
        return "".join(result).replace("<", "\\3c ")

    def clean_tree(self, root: Any, base: str = "", *, svg: bool = False) -> None:
        nodes = list(root.iter())
        if len(nodes) > _MAX_NODES:
            raise ValueError("HTML preview exceeds node count limit")
        for node in nodes:
            tag = _tag(node)
            in_svg = svg or tag == "svg" or any(_tag(p) == "svg" for p in node.iterancestors())
            allowed = tag in _SVG_TAGS if in_svg else tag in _HTML_TAGS
            if tag == "link" and str(node.get("rel", "")).lower() != "stylesheet":
                allowed = False
            if not allowed:
                parent = node.getparent()
                if parent is not None:
                    if not in_svg and tag == "embed":
                        # libxml's HTML4 parser treats this HTML5 void tag as a
                        # container. Keep following content, never its attributes.
                        node.drop_tag()
                    else:
                        tail = str(node.tail or "")
                        previous = node.getprevious()
                        if previous is None:
                            parent.text = str(parent.text or "") + tail
                        else:
                            previous.tail = str(previous.tail or "") + tail
                        parent.remove(node)
                self.blocked.add("active or unsupported HTML/SVG element")
                continue
            if in_svg:
                node.tag = _SVG_TAGS[tag]
            for attr, value in list(node.attrib.items()):
                name = str(attr).lower()
                del node.attrib[attr]
                if name == "style":
                    node.set("style", self.css(value, base, inline=True))
                elif name.startswith("aria-") or (
                    name.startswith("data-")
                    and re.fullmatch(r"data-[a-z0-9_-]{1,80}", name)
                    and len(value) <= 2048
                ):
                    node.set(name, value)
                elif name in {
                    "href",
                    "xlink:href",
                    "{http://www.w3.org/1999/xlink}href",
                    "src",
                    "poster",
                }:
                    is_resource = (
                        (name == "src" and tag in {"img", "video", "audio", "source", "track"})
                        or (name == "poster" and tag == "video")
                        or (name.endswith("href") and tag in {"image", "link"})
                    )
                    if is_resource or (name.endswith("href") and tag in {"a", "use"}):
                        url = self.url(value, base, fragment_only=not is_resource)
                        if url:
                            node.set("href" if name.endswith("href") else name, url)
                elif in_svg and name in _SVG_ATTRS:
                    node.set(
                        _SVG_ATTRS[name],
                        self._tokens(tinycss2.parse_component_value_list(value), base),
                    )
                elif name in _ATTRS and not name.startswith("on"):
                    node.set(name, value)
            if tag == "style":
                node.text = self.css(str(node.text or ""), base)
            elif tag in {"video", "audio"}:
                node.set("controls", "")
                node.set("preload", "metadata")
            elif tag == "img":
                node.set("loading", "eager")

    def document(self, page_key: str, *, static: bool) -> bytes:
        original = deepcopy(self.source.document)
        if sum(1 for _ in original.iter()) > _MAX_NODES:
            raise ValueError("HTML preview exceeds node count limit")
        frames = [
            node for node in original.iter() if "slide-frame" in str(node.get("class", "")).split()
        ]
        matches = [
            frame
            for frame in frames
            if any(
                "slide" in str(node.get("class", "")).split()
                and node.get("data-slide-key") == page_key
                for node in frame.iter()
            )
        ]
        if len(matches) != 1:
            raise ValueError("Preview page_key must identify exactly one slide-frame")
        selected = matches[0]
        if any(parent in frames for parent in selected.iterancestors()):
            raise ValueError("Nested slide frames are not supported")
        for frame in frames:
            if frame is not selected:
                parent = frame.getparent()
                if parent is not None:
                    parent.remove(frame)
        document = html.Element("html")
        document.attrib.update(original.attrib)
        head = html.Element("head")
        body = html.Element("body")
        original_body = original.find("body")
        if original_body is not None:
            body.attrib.update(original_body.attrib)
        document.extend([head, body])
        for node in original.iter():
            tag = _tag(node)
            if tag == "style" or tag == "link" and str(node.get("rel", "")).lower() == "stylesheet":
                head.append(deepcopy(node))
        branch = deepcopy(selected)
        for style in list(branch.iter("style")):
            parent = style.getparent()
            if parent is not None:
                parent.remove(style)
        wrappers: list[Any] = []
        for ancestor in selected.iterancestors():
            if _tag(ancestor) in {"body", "html"}:
                break
            wrapper = html.Element(str(ancestor.tag))
            wrapper.attrib.update(ancestor.attrib)
            wrapper.append(branch)
            branch = wrapper
            wrappers.append(wrapper)
        body.append(branch)
        self.clean_tree(document)
        clean_frames = [
            node for node in body.iter() if "slide-frame" in str(node.get("class", "")).split()
        ]
        if len(clean_frames) != 1:
            raise ValueError("Selected slide was inside an unsafe element")
        frame = clean_frames[0]
        frame.set(
            "class",
            str(frame.get("class", "")) + " active is-active is-current",
        )
        frame.set("aria-hidden", "false")
        for deck in body.iter():
            if "deck" in str(deck.get("class", "")).split():
                deck.set("data-mode", "present")
                deck.set("data-js-ready", "true")
        for wrapper in wrappers:
            wrapper.set("data-pptlib-wrapper", "")
        for slide in frame.iter():
            if "slide" in str(slide.get("class", "")).split():
                slide.set("class", str(slide.get("class", "")) + " active is-active")
        meta = html.Element("meta", charset="utf-8")
        head.insert(0, meta)
        style = html.Element("style")
        style.text = _LAYOUT + (_STATIC if static else "")
        head.append(style)
        script = html.Element("script")
        script.text = _RUNTIME
        body.append(script)
        self.report_warnings()
        return b"<!doctype html>\n" + bytes(html.tostring(document, encoding="utf-8"))

    def report_warnings(self) -> None:
        if self.blocked:
            warnings.warn(
                "HTML preview blocked: " + ", ".join(sorted(self.blocked)),
                PreviewWarning,
                stacklevel=3,
            )
            self.blocked.clear()

    def resource(self, ref: str) -> tuple[bytes, str]:
        digest, size, mime = self.resources[ref]
        inline = self.inline_resources.get(ref)
        content = inline[0] if inline is not None else self.source.read_resource(ref)
        if len(content) != size or not hmac.compare_digest(
            hashlib.sha256(content).hexdigest(), digest
        ):
            raise ValueError("HTML preview resource changed since import")
        base = posixpath.dirname(ref)
        if mime == "text/css":
            rules, _encoding = tinycss2.parse_stylesheet_bytes(content)
            content = self.css(str(tinycss2.serialize(rules)), base).encode("utf-8")
        elif mime == "image/svg+xml":
            parser = etree.XMLParser(
                resolve_entities=False,
                no_network=True,
                load_dtd=False,
                huge_tree=False,
                remove_comments=True,
                remove_pis=True,
            )
            svg = etree.fromstring(content, parser=parser)
            if _tag(svg) != "svg":
                raise ValueError("Invalid SVG resource")
            self.clean_tree(svg, base, svg=True)
            content = bytes(etree.tostring(svg, encoding="utf-8"))
        self.report_warnings()
        return content, mime


def _range(value: str, size: int) -> tuple[int, int]:
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", value)
    if not match or size == 0:
        raise ValueError("Invalid byte range")
    first, last = match.groups()
    if not first:
        if not last or int(last) == 0:
            raise ValueError("Invalid suffix range")
        return max(0, size - int(last)), size - 1
    start = int(first)
    end = min(size - 1, int(last)) if last else size - 1
    if start > end:
        raise ValueError("Unsatisfiable byte range")
    return start, end


class _PreviewServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    request_queue_size = 8

    def __init__(self, sanitizer: _Sanitizer) -> None:
        self.sanitizer = sanitizer
        self.document = b""
        self.slots = threading.BoundedSemaphore(8)
        self.connections: set[socket.socket] = set()
        self.connections_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _PreviewHandler)
        self.host = f"127.0.0.1:{self.server_port}"
        resource_origin = f"http://{self.host}{sanitizer.prefix}resource/"
        self.csp = (
            "default-src 'none'; "
            f"script-src 'sha256-{_RUNTIME_HASH}'; script-src-attr 'none'; "
            f"style-src 'unsafe-inline' {resource_origin}; "
            f"img-src {resource_origin}; media-src {resource_origin}; font-src {resource_origin}; "
            "connect-src 'none'; worker-src 'none'; child-src 'none'; frame-src 'none'; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
            "sandbox allow-scripts allow-same-origin"
        )

    def process_request(self, request: Any, client_address: Any) -> None:
        connection = cast(socket.socket, request)
        connection.settimeout(5)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(connection)
            return
        with self.connections_lock:
            self.connections.add(connection)
        try:
            super().process_request(connection, client_address)
        except BaseException:
            with self.connections_lock:
                self.connections.discard(connection)
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        connection = cast(socket.socket, request)
        try:
            super().process_request_thread(connection, client_address)
        finally:
            with self.connections_lock:
                self.connections.discard(connection)
            self.slots.release()

    def server_close(self) -> None:
        with self.connections_lock:
            for connection in self.connections:
                with suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
        super().server_close()


class _PreviewHandler(BaseHTTPRequestHandler):
    server: _PreviewServer

    def log_message(self, format: str, *args: object) -> None:
        # The request path contains a bearer capability. Never put it in logs.
        pass

    def _reply(
        self,
        status: int,
        body: bytes = b"",
        mime: str = "text/plain",
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", self.server.csp)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Connection", "close")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    def do_GET(self) -> None:
        if self.requestline.split()[1] != self.path:
            self._reply(404)
            return
        if self.headers.get_all("Host") != [self.server.host]:
            self._reply(403)
            return
        origin = self.headers.get("Origin")
        if (origin is not None and origin != f"http://{self.server.host}") or self.headers.get(
            "Sec-Fetch-Site"
        ) == "cross-site":
            self._reply(403)
            return
        sanitizer = self.server.sanitizer
        if self.path == sanitizer.prefix:
            self._reply(200, self.server.document, "text/html; charset=utf-8")
            return
        prefix = sanitizer.prefix + "resource/"
        if not self.path.startswith(prefix):
            self._reply(404)
            return
        encoded = self.path[len(prefix) :]
        try:
            ref = unquote(encoded, errors="strict")
        except UnicodeError:
            self._reply(404)
            return
        if quote(ref, safe="") != encoded or ref not in sanitizer.resources:
            self._reply(404)
            return
        try:
            body, mime = sanitizer.resource(ref)
        except (OSError, ValueError, KeyError, etree.LxmlError):
            self._reply(409)
            return
        size = len(body)
        extra = {"Accept-Ranges": "bytes"}
        range_header = self.headers.get("Range")
        if range_header is not None:
            try:
                if len(self.headers.get_all("Range", [])) != 1:
                    raise ValueError("Multiple ranges")
                start, end = _range(range_header, size)
            except ValueError:
                self._reply(416, extra={"Content-Range": f"bytes */{size}"})
                return
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._reply(206, body[start : end + 1], mime, extra)
        else:
            self._reply(200, body, mime, extra)

    def do_HEAD(self) -> None:
        self.do_GET()

    def _method_not_allowed(self) -> None:
        self._reply(405, extra={"Allow": "GET, HEAD"})

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_TRACE = _method_not_allowed
    do_CONNECT = _method_not_allowed


@contextmanager
def start_preview(
    source: HtmlSource,
    page_key: str,
    static: bool = False,
    *,
    expected_sha256: str | None = None,
) -> Iterator[str]:
    """Serve one sanitized page until context exit.

    Resource keys/read_resource arguments are relative to the entry directory,
    including for nested ZIP entries. Callers must validate their stored source
    fingerprint first, or pass it as ``expected_sha256`` for validation here.
    No source directory is mounted, extracted, or copied.
    """
    if expected_sha256 is not None and not hmac.compare_digest(source.fingerprint, expected_sha256):
        raise ValueError("HTML source fingerprint changed since import")
    sanitizer = _Sanitizer(source, "/" + secrets.token_urlsafe(32) + "/")
    server = _PreviewServer(sanitizer)
    thread: threading.Thread | None = None
    try:
        server.document = sanitizer.document(page_key, static=static)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        yield f"http://{server.host}{sanitizer.prefix}"
    finally:
        if thread is not None and thread.is_alive():
            server.shutdown()
            thread.join()
        server.server_close()
