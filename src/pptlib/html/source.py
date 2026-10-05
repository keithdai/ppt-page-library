"""Read bounded HTML sources in place, including archive dependency closures."""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

import tinycss2  # type: ignore[import-untyped]
from lxml import html as lh  # type: ignore[import-untyped]

HTML_LIMIT = 128 * 1024 * 1024
_MEDIA_TYPES = {
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".avif",
    ".ico",
    ".mp4",
    ".webm",
    ".mov",
    ".mp3",
    ".wav",
    ".ogg",
    ".m4a",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
}


def css_urls(css: str) -> list[str]:
    urls: list[str] = []

    def visit(tokens: list[Any]) -> None:
        importing = False
        for token in tokens:
            if token.type in {"comment", "whitespace"}:
                continue
            if token.type == "url":
                urls.append(str(token.value))
            elif token.type == "at-keyword" and token.lower_value == "import":
                importing = True
                continue
            elif token.type == "string" and importing:
                urls.append(str(token.value))
            elif token.type == "function":
                if token.lower_name == "url":
                    value = [item for item in token.arguments if item.type != "whitespace"]
                    if len(value) == 1 and value[0].type == "string":
                        urls.append(str(value[0].value))
                else:
                    visit(token.arguments)
            elif hasattr(token, "content"):
                visit(token.content)
            importing = False

    visit(tinycss2.parse_component_value_list(css))
    return urls


def local_reference(value: str, base: str = "") -> str | None:
    """Resolve a resource relative to the entry; reject escaping and protocol paths."""
    value = value.strip()
    parts = urlsplit(value)
    if not value or value.startswith("#") or parts.scheme or parts.netloc:
        return None
    path = unquote(parts.path)
    if "\\" in path or "\x00" in path or path.startswith("/"):
        raise ValueError("HTML 资源路径不允许绝对路径或反斜杠")
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
    if joined == ".." or joined.startswith("../"):
        raise ValueError("HTML 资源越出了演示文件所在目录")
    return joined


def parse_document(text: str) -> Any:
    parser = lh.HTMLParser(encoding="utf-8", no_network=True, huge_tree=True)
    return lh.document_fromstring(text.encode("utf-8"), parser=parser)


class HtmlSource:
    """Immutable-by-fingerprint source; only dependency-listed resources are exposed.

    A directory bundle is indexed by its HTML entry. ZIPs are read in place and
    never extracted. Resource keys are relative to the entry's own directory.
    """

    def __init__(
        self,
        source_path: Path,
        max_bytes: int = HTML_LIMIT,
        max_parts: int = 20_000,
        max_package_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        self.path = source_path.resolve()
        self.max_bytes = min(max_bytes, HTML_LIMIT)
        self.max_parts = max_parts
        self.max_package_bytes = max_package_bytes
        self.is_zip = self.path.suffix.lower() == ".zip"
        self.entry = self.path.name
        self.resources: dict[str, dict[str, Any]] = {}
        self.external_urls: set[str] = set()
        self._members: set[str] = set()
        if self.path.stat().st_size > max_bytes:
            raise ValueError("HTML 来源文件超过大小限制")
        if self.is_zip:
            self._inspect_archive()
            with ZipFile(self.path) as archive:
                info = archive.getinfo(self.entry)
                if info.file_size > self.max_bytes:
                    raise ValueError("HTML 入口超过 128 MB 限制")
                raw = archive.read(info)
        else:
            if self.path.stat().st_size > self.max_bytes:
                raise ValueError("HTML 入口超过 128 MB 限制")
            raw = self.path.read_bytes()
        self.html = raw.decode("utf-8-sig")
        self.document = parse_document(self.html)
        self.page_keys = self._page_keys()
        self._discover_resources()
        self.entry_sha256 = hashlib.sha256(raw).hexdigest()
        if self.is_zip:
            with self.path.open("rb") as handle:
                self.fingerprint = hashlib.file_digest(handle, "sha256").hexdigest()
        elif self.resources:
            digest = hashlib.sha256(raw)
            for key, resource in sorted(self.resources.items()):
                digest.update(b"\x00" + key.encode() + b"\x00")
                digest.update(resource["sha256"].encode("ascii"))
            self.fingerprint = digest.hexdigest()
        else:
            self.fingerprint = self.entry_sha256

    def _inspect_archive(self) -> None:
        try:
            with ZipFile(self.path) as archive:
                infos = archive.infolist()
                if len(infos) > self.max_parts:
                    raise ValueError("HTML ZIP 文件成员数量超限")
                if sum(info.file_size for info in infos) > self.max_package_bytes:
                    raise ValueError("HTML ZIP 解压体积超限")
                entries: list[str] = []
                for info in infos:
                    name = info.filename
                    if (
                        name.startswith(("/", "\\"))
                        or "\\" in name
                        or ".." in PurePosixPath(name).parts
                        or ":" in name
                        or stat.S_ISLNK(info.external_attr >> 16)
                        or info.flag_bits & 1
                    ):
                        raise ValueError("HTML ZIP 包含不安全路径、符号链接或加密成员")
                    if name in self._members:
                        raise ValueError("HTML ZIP 包含重复成员")
                    self._members.add(name)
                    if not info.is_dir() and PurePosixPath(name).name.lower() == "index.html":
                        entries.append(name)
                if len(entries) != 1:
                    raise ValueError("HTML ZIP 必须包含唯一的 index.html 入口")
                self.entry = entries[0]
        except BadZipFile as error:
            raise ValueError("文件不是有效的 HTML ZIP 包") from error

    def _page_keys(self) -> list[str]:
        generators = self.document.xpath(
            "//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz')='fs-deck-generator']/@content"
        )
        if generators != ["render-deck"]:
            raise ValueError("首版仅支持带 render-deck 标记的标准 HTML 演示")
        frames = self.document.xpath(
            "//*[contains(concat(' ',normalize-space(@class),' '),' slide-frame ')]"
        )
        keys: list[str] = []
        for frame in frames:
            slides = frame.xpath(
                ".//*[contains(concat(' ',normalize-space(@class),' '),' slide ')]"
            )
            if len(slides) != 1 or not slides[0].get("data-slide-key"):
                raise ValueError("每个 slide-frame 必须包含一个带稳定 key 的 slide")
            key = str(slides[0].get("data-slide-key"))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", key):
                raise ValueError("HTML 页面 key 格式不受支持")
            keys.append(key)
        if not keys or len(keys) > 2000 or len(set(keys)) != len(keys):
            raise ValueError("HTML 页面为空、过多或存在重复 key")
        return keys

    def _read_raw_resource(self, reference: str, limit: int | None = None) -> bytes:
        allowed = min(
            self.max_package_bytes,
            self.max_bytes,
            limit if limit is not None else self.max_package_bytes,
        )
        if self.is_zip:
            member = posixpath.join(posixpath.dirname(self.entry), reference)
            if member not in self._members:
                raise ValueError(f"HTML ZIP 缺少依赖资源：{reference}")
            with ZipFile(self.path) as archive:
                info = archive.getinfo(member)
                if info.file_size > allowed:
                    raise ValueError("HTML 资源体积超限")
                with archive.open(info) as handle:
                    raw = handle.read(allowed + 1)
                if len(raw) > allowed:
                    raise ValueError("HTML 资源体积超限")
                return raw
        target = self.path.parent / reference
        if not target.resolve().is_relative_to(self.path.parent):
            raise ValueError("HTML 资源不能越出来源目录")
        for part in [target, *target.parents]:
            if part == self.path.parent:
                break
            if part.is_symlink():
                raise ValueError("HTML 资源不能使用符号链接")
        if not target.is_file():
            raise ValueError(f"HTML 缺少依赖资源：{reference}")
        if target.stat().st_size > allowed:
            raise ValueError("HTML 资源体积超限")
        with target.open("rb") as handle:
            raw = handle.read(allowed + 1)
        if len(raw) > allowed:
            raise ValueError("HTML 资源体积超限")
        return raw

    def _discover_resources(self) -> None:
        pending: list[tuple[str, str]] = []
        for node in self.document.iter():
            if not isinstance(node.tag, str):
                continue
            tag = node.tag.lower()
            if tag in {"img", "video", "audio", "source", "image", "use"}:
                for attr in ("src", "poster", "href", "xlink:href"):
                    if node.get(attr):
                        pending.append((node.get(attr), ""))
                if node.get("srcset") and "data:" not in node.get("srcset"):
                    pending.extend(
                        (item.strip().split()[0], "")
                        for item in node.get("srcset").split(",")
                        if item.strip()
                    )
            elif tag == "link" and "stylesheet" in node.get("rel", "").lower().split():
                pending.append((node.get("href", ""), ""))
            elif tag in {"script", "iframe"}:
                value = node.get("src", "")
                if value and urlsplit(value).scheme in {"http", "https"}:
                    self.external_urls.add(value)
            if node.get("style"):
                pending.extend((url, "") for url in css_urls(node.get("style")))
            if tag == "style":
                pending.extend((url, "") for url in css_urls(node.text or ""))
        total = 0
        while pending:
            value, base = pending.pop()
            ref = local_reference(value, base)
            if ref is None:
                if urlsplit(value.strip()).scheme in {"http", "https"} or value.startswith("//"):
                    self.external_urls.add(value)
                continue
            if ref in self.resources:
                continue
            if Path(ref).suffix.lower() not in _MEDIA_TYPES:
                raise ValueError(f"HTML 依赖类型不受支持：{ref}")
            if len(self.resources) >= self.max_parts:
                raise ValueError("HTML 依赖数量超限")
            raw = self._read_raw_resource(ref, self.max_package_bytes - total)
            total += len(raw)
            if total > self.max_package_bytes:
                raise ValueError("HTML 依赖总大小超限")
            self.resources[ref] = {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "mime": mimetypes.guess_type(ref)[0] or "application/octet-stream",
            }
            if ref.lower().endswith(".css"):
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("HTML 样式文件超过 4 MB 限制")
                pending.extend((url, ref) for url in css_urls(raw.decode("utf-8-sig")))

    def read_resource(self, relative: str) -> bytes:
        if relative not in self.resources:
            raise ValueError("HTML 资源未在依赖清单中声明")
        expected_size = int(self.resources[relative]["size_bytes"])
        if self.is_zip:
            with ZipFile(self.path) as archive:
                member = posixpath.join(posixpath.dirname(self.entry), relative)
                member_changed = (
                    member not in self._members
                    or archive.getinfo(member).file_size != expected_size
                )
                if member_changed:
                    raise ValueError("HTML 来源依赖已变化，请重新入库")
        else:
            target = self.path.parent / relative
            if not target.is_file() or target.stat().st_size != expected_size:
                raise ValueError("HTML 来源依赖已变化，请重新入库")
        raw = self._read_raw_resource(relative, expected_size)
        if hashlib.sha256(raw).hexdigest() != self.resources[relative]["sha256"]:
            raise ValueError("来源依赖已变化，请重新入库")
        return raw
