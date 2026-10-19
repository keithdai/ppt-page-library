"""Bounded public-web download and dependency capture, without executing source scripts."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import mimetypes
import socket
import ssl
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import tinycss2  # type: ignore[import-untyped]
from lxml import etree, html  # type: ignore[import-untyped]

from pptlib.html.source import HTML_LIMIT, parse_document

_MIME_EXTENSIONS = {
    "text/css": ".css", "image/png": ".png", "image/jpeg": ".jpg",
    "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg",
    "image/avif": ".avif", "image/x-icon": ".ico", "video/mp4": ".mp4",
    "video/webm": ".webm", "video/quicktime": ".mov", "audio/mpeg": ".mp3",
    "audio/wav": ".wav", "audio/ogg": ".ogg", "audio/mp4": ".m4a",
    "font/woff": ".woff", "font/woff2": ".woff2", "font/ttf": ".ttf",
    "font/otf": ".otf", "application/font-woff": ".woff", "text/vtt": ".vtt",
}
_EXTENSIONS = set(_MIME_EXTENSIONS.values())


def normalize_url(value: str) -> str:
    """Fragments identify the initially visible page, not a different deck."""
    value = value.strip()
    if len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("演示链接格式不正确")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as error:
        raise ValueError("演示链接格式不正确") from error
    if (
        parts.scheme.lower() not in {"http", "https"} or not parts.hostname
        or parts.username is not None or parts.password is not None
        or port not in {None, 80, 443}
    ):
        raise ValueError("请输入公开的 HTTP/HTTPS 演示链接，不支持账号密码或自定义端口")
    host = parts.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = f"[{host}]"
    scheme = parts.scheme.lower()
    default = 443 if scheme == "https" else 80
    authority = host + (f":{port}" if port is not None and port != default else "")
    return urlunsplit((scheme, authority, parts.path or "/", parts.query, ""))


def public_addresses(host: str, port: int) -> list[str]:
    """Validate *all* resolved addresses and pin the connection to one of them."""
    try:
        addresses = list(dict.fromkeys(
            str(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ))
    except OSError as error:
        raise ValueError("无法解析演示资源域名") from error
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("在线归档不访问本机、局域网或私有网络地址")
    return addresses


@dataclass(frozen=True)
class Download:
    content: bytes
    url: str
    mime: str


class ResourceUnavailable(ValueError):
    """A valid public URL returned a fetch error, distinct from a policy/size rejection."""


def download(url: str, limit: int, *, deadline: float) -> Download:
    current = normalize_url(url)
    for _ in range(6):
        if time.monotonic() >= deadline:
            raise ValueError("在线归档超时，请稍后重试")
        parts = urlsplit(current)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = public_addresses(str(parts.hostname), port)
        timeout = max(0.1, min(30.0, deadline - time.monotonic()))
        connection = http.client.HTTPConnection(str(parts.hostname), port, timeout=timeout)
        watchdog: threading.Timer | None = None
        try:
            sock = socket.create_connection((addresses[0], port), timeout=timeout)
            connection.sock = sock

            def abort_transfer(active: http.client.HTTPConnection = connection) -> None:
                current_socket = active.sock
                if current_socket is not None:
                    with suppress(OSError):
                        current_socket.shutdown(socket.SHUT_RDWR)

            # A socket timeout measures inactivity, not total time. Interrupt
            # headers and reads even when the server keeps trickling bytes.
            watchdog = threading.Timer(max(0.01, deadline - time.monotonic()), abort_transfer)
            watchdog.daemon = True
            watchdog.start()
            if parts.scheme == "https":
                try:
                    sock = ssl.create_default_context().wrap_socket(
                        sock, server_hostname=parts.hostname
                    )
                except BaseException:
                    sock.close()
                    raise
            connection.sock = sock
            connection.request(
                "GET", urlunsplit(("", "", parts.path or "/", parts.query, "")),
                headers={
                    "Host": parts.netloc, "User-Agent": "DeckAtlas-Archive/1.0",
                    "Accept-Encoding": "identity", "Accept": "*/*",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("在线来源返回了无效重定向")
                current = normalize_url(urljoin(current, location))
                continue
            if response.status != 200:
                raise ResourceUnavailable(f"在线资源无法下载（HTTP {response.status}）")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("在线资源返回了不支持的压缩传输")
            length = response.getheader("Content-Length")
            if length and int(length) > limit:
                raise ValueError("在线资源超过归档大小限制")
            chunks = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("在线归档超时，请稍后重试")
                sock.settimeout(max(0.1, min(30.0, remaining)))
                chunk = response.read1(min(256 * 1024, limit - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ValueError("在线资源超过归档大小限制")
                chunks.append(chunk)
            if time.monotonic() >= deadline:
                raise ValueError("在线归档超时，请稍后重试")
            if length is not None and size != int(length):
                raise ResourceUnavailable("在线资源下载不完整，请重试")
            return Download(
                b"".join(chunks), current,
                response.getheader("Content-Type", "").split(";")[0].strip().lower(),
            )
        except (OSError, http.client.HTTPException) as error:
            raise ResourceUnavailable(
                f"在线资源下载失败（{type(error).__name__}），请重试"
            ) from error
        finally:
            if watchdog is not None:
                watchdog.cancel()
                watchdog.join()
            connection.close()
    raise ValueError("在线来源重定向次数过多")


class RemoteCapture:
    """Build a local dependency closure in a new, private staging directory."""

    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        max_parts: int = 2000,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self.max_parts = min(max_parts, 2000)
        self.total_bytes = 0
        self.deadline = time.monotonic() + 900
        self.saved: dict[str, str] = {}
        self.resources: list[dict[str, object]] = []
        self.missing: dict[str, str] = {}
        self.on_progress = on_progress

    def fetch(self, url: str, *, entry: bool = False) -> Download:
        limit = min(HTML_LIMIT, self.max_bytes - self.total_bytes)
        if limit <= 0:
            raise ValueError("在线归档总大小超过限制")
        result = download(url, limit, deadline=self.deadline)
        self.total_bytes += len(result.content)
        if entry and result.mime not in {"text/html", "application/xhtml+xml", ""}:
            raise ValueError("链接未返回 HTML 演示文稿")
        return result

    def resource(self, value: str, base: str, *, css: bool = False, depth: int = 0) -> str:
        value = value.strip()
        if not value or value.startswith("#") or value.lower().startswith("data:"):
            return value
        if depth > 32:
            raise ValueError("在线资源依赖层级过深")
        absolute, fragment = urldefrag(urljoin(base, value))
        absolute = normalize_url(absolute)
        if absolute in self.missing:
            return absolute + ("#" + fragment if fragment else "")
        if absolute not in self.saved:
            if len(self.saved) >= self.max_parts:
                raise ValueError("在线资源数量超过限制")
            try:
                result = self.fetch(absolute)
            except ResourceUnavailable as error:
                self.missing[absolute] = str(error)
                if self.on_progress:
                    name = urlsplit(absolute).path.rsplit("/", 1)[-1]
                    self.on_progress({
                        "stage": "download_warning",
                        "message": f"部分来源资源无法下载：{name}",
                    })
                # Keep the provenance URL. The offline sanitizer blocks it and
                # the source parser reports requires_network instead of success.
                return absolute + ("#" + fragment if fragment else "")
            suffix = ".css" if css else _MIME_EXTENSIONS.get(result.mime)
            if suffix is None:
                guessed = Path(urlsplit(result.url).path).suffix.lower()
                if guessed not in _EXTENSIONS or result.mime in {
                    "text/html", "application/javascript", "text/javascript",
                }:
                    raise ValueError("在线演示包含无法归档的资源类型")
                suffix = guessed
            name = hashlib.sha256(absolute.encode()).hexdigest() + suffix
            relative = "resources/" + name
            self.saved[absolute] = relative
            content = result.content
            if suffix == ".css":
                content = self.css(content.decode("utf-8-sig"), result.url, depth + 1).encode()
            elif suffix == ".svg":
                parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
                root = etree.fromstring(content, parser)
                if str(root.tag).rsplit("}", 1)[-1] != "svg":
                    raise ValueError("在线 SVG 资源格式不正确")
                self.rewrite_tree(root, result.url, nested=True, depth=depth + 1)
                content = bytes(etree.tostring(root, encoding="utf-8"))
            target = self.directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            self.resources.append({
                "url": absolute, "path": relative, "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mime": result.mime or mimetypes.guess_type(name)[0],
            })
            if self.on_progress:
                self.on_progress({
                    "stage": "download_resource", "resource_count": len(self.resources),
                    "bytes": self.total_bytes,
                })
        reference = self.saved[absolute]
        return reference + ("#" + fragment if fragment else "")

    def css(self, text: str, base: str, depth: int = 0, *, nested: bool = True) -> str:
        def ref(value: str, is_css: bool = False) -> str:
            result = self.resource(value, base, css=is_css, depth=depth)
            return result.removeprefix("resources/") if nested else result

        def visit(tokens: list[Any]) -> list[Any]:
            output = []
            importing = False
            for token in tokens:
                kind = token.type
                if kind in {"whitespace", "comment"}:
                    output.append(token)
                    continue
                if kind == "at-keyword" and token.lower_value == "import":
                    importing = True
                    output.append(token)
                    continue
                replacement = None
                if kind == "url":
                    replacement = ref(str(token.value), importing)
                elif kind == "string" and importing:
                    replacement = ref(str(token.value), True)
                elif kind == "function":
                    if token.lower_name == "url":
                        args = [
                            t for t in token.arguments if t.type not in {"whitespace", "comment"}
                        ]
                        if len(args) == 1 and args[0].type == "string":
                            replacement = ref(str(args[0].value), importing)
                    else:
                        if token.lower_name in {"image-set", "-webkit-image-set"}:
                            token.arguments = [
                                tinycss2.parse_component_value_list(
                                    "url(" + json.dumps(ref(t.value)) + ")"
                                )[0] if t.type == "string" else visit([t])[0]
                                for t in token.arguments
                            ]
                        else:
                            token.arguments = visit(token.arguments)
                elif hasattr(token, "content"):
                    token.content = visit(token.content)
                if replacement is not None:
                    output.extend(tinycss2.parse_component_value_list(
                        "url(" + json.dumps(replacement, ensure_ascii=True) + ")"
                    ))
                else:
                    output.append(token)
                importing = False
            return output

        return str(tinycss2.serialize(visit(tinycss2.parse_component_value_list(text))))

    def rewrite_tree(
        self, root: Any, base: str, *, nested: bool = False, depth: int = 0
    ) -> None:
        for node in list(root.iter()):
            if not isinstance(node.tag, str):
                continue
            tag = node.tag.rsplit("}", 1)[-1].lower()
            if tag == "script":
                # Notes are inert JSON; all executable source code stays out of the archive.
                if node.get("type") != "application/json" or node.get("id") != "fs-deck-notes":
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)
                continue
            if tag in {"base", "iframe", "object", "embed"}:
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
                continue
            for attr in list(node.attrib):
                if attr.lower().startswith("on") or attr.lower() in {"srcdoc", "integrity"}:
                    del node.attrib[attr]
            attrs: tuple[str, ...] = ()
            if tag in {"img", "video", "audio", "source", "track"}:
                attrs = ("src", "poster", "data-fs-src")
            elif tag in {"image", "use"}:
                attrs = ("href", "xlink:href", "{http://www.w3.org/1999/xlink}href")
            elif tag == "link":
                if "stylesheet" in node.get("rel", "").lower().split():
                    attrs = ("href",)
                else:
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)
                    continue
            for attr in attrs:
                value = node.get(attr)
                if value:
                    local = self.resource(value, base, css=tag == "link", depth=depth)
                    node.set(attr, local.removeprefix("resources/") if nested else local)
            if node.get("srcset"):
                # Preserve all ordinary density candidates; data URIs already work offline.
                srcset = node.get("srcset")
                if "data:" not in srcset.lower():
                    candidates = []
                    for item in srcset.split(","):
                        parts = item.strip().split()
                        if parts:
                            local = self.resource(parts[0], base, depth=depth)
                            parts[0] = local.removeprefix("resources/") if nested else local
                            candidates.append(" ".join(parts))
                    node.set("srcset", ", ".join(candidates))
                    if tag == "img" and not node.get("src") and candidates:
                        node.set("src", candidates[-1].split()[0])
            if node.get("style"):
                node.set("style", self.css(node.get("style"), base, depth, nested=nested))
            if tag == "style" and node.text:
                node.text = self.css(node.text, base, depth, nested=nested)

    def capture(self, url: str) -> tuple[Path, dict[str, object]]:
        url = normalize_url(url)
        if self.on_progress:
            self.on_progress({"stage": "download_start", "path": url})
        result = self.fetch(url, entry=True)
        try:
            text = result.content.decode("utf-8-sig")
        except UnicodeError as error:
            raise ValueError("在线演示需要使用 UTF-8 编码") from error
        document = parse_document(text)
        titles = document.xpath("//title/text()")
        title = " ".join(str(titles[0]).split())[:200] if titles else "在线演示"
        base_nodes = document.xpath("//base[@href]/@href")
        base = urljoin(result.url, str(base_nodes[0])) if base_nodes else result.url
        self.rewrite_tree(document, base)
        entry = self.directory / "index.html"
        entry.write_bytes(b"<!doctype html>\n" + html.tostring(document, encoding="utf-8"))
        # This metadata is deliberately separate from the content fingerprint:
        # checking the same URL again must not create a new deck just due to time.
        metadata: dict[str, object] = {
            "source_url": url, "resolved_url": result.url, "title": title,
            "entry_sha256": hashlib.sha256(result.content).hexdigest(),
            "resources": self.resources, "download_bytes": self.total_bytes,
            "missing_resources": [
                {"url": url, "error": error} for url, error in self.missing.items()
            ],
            "capture_profile": "static-dom-native-media-v1",
        }
        return entry, metadata
