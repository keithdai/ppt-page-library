"""Offline HTML packaging. Untrusted pages never execute in the outer document."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import tempfile
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import html as lh  # type: ignore[import-untyped]

from pptlib.export.ooxml import ExportError, ExportResult, output_fingerprint
from pptlib.html.preview import PreviewWarning, _Sanitizer
from pptlib.html.source import HtmlSource, parse_document

MAX_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_PAGES = 2000
MAX_RESOURCE_DEPTH = 64
_VERSION = "pptlib-offline-html-v1"
_MIME_SUFFIX = {
    "image/svg+xml": ".svg",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "text/css": ".css",
    "font/woff": ".woff",
    "font/woff2": ".woff2",
    "font/ttf": ".ttf",
    "font/otf": ".otf",
}
_PAGE_CSP = (
    "default-src 'none'; script-src 'none'; script-src-attr 'none'; "
    "style-src 'unsafe-inline' data: 'self' file:; "
    "img-src data: 'self' file:; media-src data: 'self' file:; font-src data:; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)
_STYLE = """
html,body{margin:0;width:100%;height:100%;background:#17191e;color:#fff;
font:16px system-ui,sans-serif;overflow:hidden}
*{box-sizing:border-box} #viewport{position:absolute;inset:0 0 56px;overflow:hidden}
.page{position:absolute;inset:0;overflow:hidden} .page[hidden]{display:none}
iframe{position:absolute;width:1920px;height:1080px;border:0;transform-origin:0 0;
background:white}nav{position:absolute;bottom:0;height:56px;width:100%;display:flex;
align-items:center;justify-content:center;gap:16px}button{font:inherit;color:inherit;
background:#30343e;border:1px solid #606574;border-radius:4px;padding:6px 12px;
cursor:pointer}button:disabled{opacity:.4;cursor:default}
#notes{position:absolute;bottom:56px;right:0;max-width:640px;max-height:40vh;
overflow:auto;background:#242832;padding:20px;white-space:pre-wrap}
"""
_RUNTIME = """(() => {
  'use strict';
  const pages = Array.from(document.querySelectorAll('.page'));
  const viewport = document.getElementById('viewport');
  const notes = document.getElementById('notes');
  const counter = document.getElementById('counter');
  let current = -1;
  function fit() {
    const w = viewport.clientWidth, h = viewport.clientHeight;
    pages.forEach(page => {
      const frame = page.querySelector('iframe');
      const width = Number(frame.dataset.width), height = Number(frame.dataset.height);
      const scale = Math.min(w / width, h / height);
      frame.style.width = width + 'px';
      frame.style.height = height + 'px';
      frame.style.transform = 'scale(' + scale + ')';
      frame.style.left = ((w - width * scale) / 2) + 'px';
      frame.style.top = ((h - height * scale) / 2) + 'px';
    });
  }
  function show() {
    const value = /^#([0-9]+)$/.exec(location.hash);
    const next = Math.max(0, Math.min(pages.length - 1, value ? Number(value[1]) - 1 : 0));
    if (next !== current && current >= 0) {
      const doc = pages[current].querySelector('iframe').contentDocument;
      if (doc) doc.querySelectorAll('video,audio').forEach(media => media.pause());
    }
    current = next;
    pages.forEach((page, index) => { page.hidden = index !== current; });
    notes.textContent = pages[current].querySelector('.page-notes').textContent;
    counter.textContent = (current + 1) + ' / ' + pages.length;
    document.getElementById('previous').disabled = current === 0;
    document.getElementById('next').disabled = current === pages.length - 1;
    fit();
  }
  function move(delta) { location.hash = String(Math.max(1,
    Math.min(pages.length, current + 1 + delta))); }
  function fullscreen() {
    const task = document.fullscreenElement ? document.exitFullscreen()
      : document.documentElement.requestFullscreen();
    if (task) task.catch(() => {});
  }
  function key(event) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'ArrowRight' || event.key === 'PageDown') move(1);
    else if (event.key === 'ArrowLeft' || event.key === 'PageUp') move(-1);
    else if (event.key === 'Home') location.hash = '1';
    else if (event.key === 'End') location.hash = String(pages.length);
    else if (event.key.toLowerCase() === 'f') fullscreen();
    else return;
    event.preventDefault();
  }
  document.getElementById('previous').addEventListener('click', () => move(-1));
  document.getElementById('next').addEventListener('click', () => move(1));
  document.getElementById('fullscreen').addEventListener('click', fullscreen);
  document.getElementById('toggle-notes').addEventListener('click', () => {
    notes.hidden = !notes.hidden;
  });
  pages.forEach(page => {
    const frame = page.querySelector('iframe');
    const bind = () => {
      if (frame.contentDocument) frame.contentDocument.addEventListener('keydown', key);
    };
    frame.addEventListener('load', bind);
    bind();
  });
  addEventListener('keydown', key);
  addEventListener('hashchange', show);
  addEventListener('resize', fit);
  show();
})();"""


@dataclass(frozen=True)
class HtmlPage:
    slide_id: str
    version_id: str
    source_format: str
    source_fingerprint: str
    page_number: int
    page_key: str
    title: str
    notes: str
    source: HtmlSource | None = None
    preview: bytes | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class HtmlArtifact:
    document: bytes
    resources: dict[str, bytes]
    manifest: bytes
    export_id: str
    warnings: tuple[str, ...]
    fidelity_level: str
    page_count: int

    @property
    def size_bytes(self) -> int:
        return len(self.document) + len(self.manifest) + sum(map(len, self.resources.values()))

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.document + self.manifest)
        for name, data in sorted(self.resources.items()):
            digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
        return digest.hexdigest()


class _Resources:
    def __init__(self, offline_zip: bool, limit: int) -> None:
        self.offline_zip = offline_zip
        self.limit = limit
        self.files: dict[str, bytes] = {}
        self.total = 0
        self.emitted_bytes = 0
        self.seen: set[str] = set()

    def url(self, content: bytes, mime: str, *, embed: bool = False) -> str:
        digest = hashlib.sha256(mime.encode() + b"\0" + content).hexdigest()
        if digest not in self.seen:
            self.total += len(content)
            if self.total > self.limit:
                raise ExportError("OUTPUT_TOO_LARGE", "HTML resources exceed export size limit")
            self.seen.add(digest)
        if not self.offline_zip or embed:
            if (len(content) + 2) // 3 * 4 > self.limit:
                raise ExportError("OUTPUT_TOO_LARGE", "Embedded resource exceeds export size limit")
            return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")
        name = f"resources/{digest}{_MIME_SUFFIX.get(mime, '.bin')}"
        self.files[name] = content
        return name


class _OfflineSanitizer(_Sanitizer):
    """Reuse the preview policy; replace its capability URLs with offline URLs."""

    def __init__(self, source: HtmlSource, resources: _Resources) -> None:
        super().__init__(source, "")
        self.store = resources
        self.resolved: dict[str, str] = {}
        self.resolving: set[str] = set()
        self.export_warnings: set[str] = set()
        self.in_resource = 0

    def report_warnings(self) -> None:
        if self.blocked:
            self.export_warnings.add("HTML_UNSAFE_OR_UNSUPPORTED_CONTENT_BLOCKED")
            self.blocked.clear()

    def url(self, value: str, base: str = "", *, fragment_only: bool = False) -> str:
        # The base policy validates every reference, including inline MIME types.
        reference = super().url(value, base, fragment_only=fragment_only)
        if not reference or reference.startswith("#"):
            return reference
        encoded, separator, fragment = reference[len("resource/") :].partition("#")
        ref = unquote(encoded)
        if ref in self.resolving:
            self.export_warnings.add("HTML_CYCLIC_RESOURCE_BLOCKED")
            return ""
        if len(self.resolving) >= MAX_RESOURCE_DEPTH:
            raise ExportError("OUTPUT_TOO_LARGE", "HTML resource nesting exceeds export limit")
        if ref not in self.resolved:
            self.resolving.add(ref)
            self.in_resource += 1
            try:
                content, mime = self.resource(ref)
                # CSS is embedded so its relative URLs cannot escape resources/.
                # SVG dependencies and fonts are data URLs: local font CORS and
                # SVG image-document restrictions otherwise break offline decks.
                self.resolved[ref] = self.store.url(
                    content,
                    mime,
                    embed=mime == "text/css"
                    or mime.startswith("font/")
                    or mime.startswith("application/")
                    or self.in_resource > 1,
                )
            finally:
                self.in_resource -= 1
                self.resolving.remove(ref)
        result = self.resolved[ref]
        if self.in_resource and result.startswith("resources/"):
            # A previously top-level resource can also be referenced by SVG/CSS.
            content, mime = self.resource(ref)
            result = self.store.url(content, mime, embed=True)
        result += "#" + fragment if separator else ""
        self.store.emitted_bytes += len(result)
        if self.store.emitted_bytes > self.store.limit:
            raise ExportError("OUTPUT_TOO_LARGE", "Expanded HTML dependencies exceed export limit")
        return result

    def standalone(self, page_key: str) -> str:
        document = parse_document(self.document(page_key, static=False).decode("utf-8"))
        for node in document.xpath("//script"):
            node.drop_tree()
        head = document.find("head")
        assert head is not None
        head.insert(
            0,
            lh.Element(
                "meta",
                attrib={
                    "http-equiv": "Content-Security-Policy",
                    "content": _PAGE_CSP,
                },
            ),
        )
        head.insert(1, lh.Element("meta", attrib={"name": "referrer", "content": "no-referrer"}))
        style = lh.Element("style")
        width, height = self.source.page_sizes[page_key]
        style.text = (
            "html { --fs-scale:1!important; }"
            f".slide-frame {{ width:{width}px!important; height:{height}px!important; }}"
            f".slide-frame > .slide {{ width:{width}px!important; height:{height}px!important;"
            " transform:none!important; }"
        )
        head.append(style)
        self.report_warnings()
        return "<!doctype html>\n" + str(lh.tostring(document, encoding="unicode"))


def build_html_artifact(
    pages: Sequence[HtmlPage],
    *,
    offline_zip: bool,
    max_bytes: int = MAX_OUTPUT_BYTES,
) -> HtmlArtifact:
    """Build a bounded, deterministic package without starting a browser/server."""
    if not pages or len(pages) > MAX_PAGES:
        raise ExportError("EMPTY_SELECTION", "Select between 1 and 2000 pages")
    resources = _Resources(offline_zip, max_bytes)
    sanitizers: dict[int, _OfflineSanitizer] = {}
    documents: dict[tuple[str, str], str] = {}
    sections: list[str] = []
    items: list[dict[str, Any]] = []
    notices: set[str] = set()
    total = 0
    for number, page in enumerate(pages, 1):
        width, height = (1920, 1080)
        if page.source is not None:
            width, height = page.source.page_sizes[page.page_key]
            key = (page.version_id, page.page_key)
            sanitizer = sanitizers.get(id(page.source))
            if sanitizer is None:
                sanitizer = _OfflineSanitizer(page.source, resources)
                sanitizers[id(page.source)] = sanitizer
            if key not in documents:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", PreviewWarning)
                    documents[key] = sanitizer.standalone(page.page_key)
            doc = documents[key]
            notices.update(sanitizer.export_warnings)
            if page.source.external_urls:
                notices.add("HTML_EXTERNAL_RESOURCES_BLOCKED")
            fidelity = "sanitized-html"
        elif page.preview is not None:
            url = resources.url(page.preview, "image/jpeg")
            doc = (
                '<!doctype html><html><head><meta charset="utf-8">'
                '<meta http-equiv="Content-Security-Policy" content="'
                + html.escape(_PAGE_CSP, quote=True)
                + '"></head><body style="margin:0;background:white">'
                '<img alt="'
                + html.escape(page.title, quote=True)
                + '" src="'
                + url
                + '" style="width:1920px;height:1080px;object-fit:contain">'
                "</body></html>"
            )
            notices.add(
                "PPTX_FLATTENED_TO_IMAGE: PPTX pages use high-resolution previews; "
                "text, animations and media are not editable or interactive."
            )
            fidelity = "flattened-image"
        else:
            raise ExportError("PREVIEW_MISSING", "PPTX high-resolution preview is unavailable")
        # Attribute encoding is intentional: never interpolate a document or
        # source metadata into a script/template raw-text context.
        if total + len(doc.encode()) > max_bytes:
            raise ExportError("OUTPUT_TOO_LARGE", "HTML pages exceed export size limit")
        section = (
            f'<section class="page" data-page="{number}"'
            + (" hidden" if number != 1 else "")
            + f'><iframe data-width="{width}" data-height="{height}"'
            + ' sandbox="allow-same-origin" referrerpolicy="no-referrer" title="'
            + html.escape(page.title, quote=True)
            + '" srcdoc="'
            + html.escape(doc, quote=True)
            + '"></iframe><pre class="page-notes" hidden>'
            + html.escape(page.notes)
            + "</pre></section>"
        )
        total += len(section.encode())
        if total + resources.total > max_bytes:
            raise ExportError("OUTPUT_TOO_LARGE", "HTML pages exceed export size limit")
        sections.append(section)
        item: dict[str, Any] = {
            "output_page_number": number,
            "slide_id": page.slide_id,
            "file_version_id": page.version_id,
            "source_format": page.source_format,
            "source_sha256": page.source_fingerprint,
            "source_page_number": page.page_number,
            "page_key": page.page_key,
            "title": page.title,
            "notes": page.notes,
            "fidelity": fidelity,
        }
        if page.source_url:
            item["source_url"] = page.source_url
        items.append(item)
    fidelity_level = (
        "mixed-html-and-images" if any(p.source is None for p in pages) else "sanitized-html"
    )
    identity = json.dumps(items, sort_keys=True, ensure_ascii=True).encode()
    export_id = hashlib.sha256(_VERSION.encode() + identity).hexdigest()[:32]
    manifest = json.dumps(
        {
            "schema_version": _VERSION,
            "export_id": export_id,
            "page_count": len(pages),
            "fidelity_level": fidelity_level,
            "warnings": sorted(notices),
            "items": items,
        },
        ensure_ascii=True,
        indent=2,
    ).encode()
    runtime_hash = base64.b64encode(hashlib.sha256(_RUNTIME.encode()).digest()).decode()
    csp = (
        "default-src 'none'; "
        f"script-src 'sha256-{runtime_hash}'; script-src-attr 'none'; "
        "style-src 'unsafe-inline' data: 'self' file:; frame-src 'self' about:; "
        "img-src data: 'self' file:; media-src data: 'self' file:; font-src data:; "
        "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    )
    document = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="'
        + html.escape(csp, quote=True)
        + '"><meta name="referrer" content="no-referrer">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="deckatlas-format" content="pptlib-offline-html-v1">'
        "<title>DeckAtlas · 组合演示</title><style>" + _STYLE + "</style></head><body>"
        '<main id="viewport">' + "".join(sections) + "</main>"
        '<nav aria-label="Slide navigation"><button id="previous" aria-label="Previous slide">'
        '&#8592;</button><span id="counter" aria-live="polite"></span>'
        '<button id="next" aria-label="Next slide">&#8594;</button>'
        '<button id="fullscreen">全屏</button><button id="toggle-notes">讲稿</button>'
        '</nav><aside id="notes" hidden></aside><pre id="source-manifest" hidden>'
        + html.escape(manifest.decode())
        + "</pre><script>"
        + _RUNTIME
        + "</script></body></html>"
    ).encode()
    artifact = HtmlArtifact(
        document,
        resources.files,
        manifest,
        export_id,
        tuple(sorted(notices)),
        fidelity_level,
        len(pages),
    )
    if artifact.size_bytes > max_bytes:
        raise ExportError("OUTPUT_TOO_LARGE", "HTML package exceeds export size limit")
    return artifact


def _stage(path: Path, write: Callable[[Any], None]) -> Path:
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            staging = Path(handle.name)
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        return staging
    except BaseException:
        if staging is not None:
            staging.unlink(missing_ok=True)
        raise


def publish_html_artifact(
    artifact: HtmlArtifact,
    output: Path,
    manifest: Path,
    *,
    recheck: Callable[[], None],
    expected_output: str,
    expected_manifest: str,
) -> ExportResult:
    """Stage both files, revalidate, then replace with rollback on failure.

    Each rename is atomic. The pair is not a filesystem transaction; backups
    restore both destinations if either publication step fails.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    staging: list[Path] = []
    backups: dict[Path, Path] = {}
    published: list[Path] = []

    def write_output(handle: Any) -> None:
        if output.suffix.lower() == ".zip":
            with ZipFile(handle, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("index.html", artifact.document)
                archive.writestr("manifest.json", artifact.manifest)
                for name, content in sorted(artifact.resources.items()):
                    archive.writestr(name, content)
        else:
            handle.write(artifact.document)

    try:
        staging.append(_stage(output, write_output))
        staging.append(_stage(manifest, lambda handle: handle.write(artifact.manifest)))
        recheck()
        for path, expected in ((output, expected_output), (manifest, expected_manifest)):
            if output_fingerprint(path) != expected:
                raise ExportError("OUTPUT_CHANGED", "Output changed after export preflight")
            if path.exists():
                backup = path.parent / f".{path.name}.{uuid4().hex}.backup"
                os.link(path, backup, follow_symlinks=False)
                backups[path] = backup
        recheck()
        for temporary, path in zip(staging, (output, manifest), strict=True):
            temporary.replace(path)
            published.append(path)
    except BaseException:
        for path in reversed(published):
            previous = backups.pop(path, None)
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(previous, path)
        raise
    finally:
        for temporary in staging:
            temporary.unlink(missing_ok=True)
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    return ExportResult(
        artifact.export_id,
        output,
        manifest,
        artifact.page_count,
        artifact.warnings,
        artifact.fidelity_level,
    )
