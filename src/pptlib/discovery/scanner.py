from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    path: Path
    size_bytes: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class ScannedFile:
    path: Path
    sha256: str
    size_bytes: int
    mtime_ns: int
    ctime_ns: int = 0


@dataclass(frozen=True, slots=True)
class ScanRules:
    formats: frozenset[str] = frozenset({"pptx"})
    min_file_bytes: int = 0
    max_file_bytes: int = 500 * 1024 * 1024
    recursive: bool = True
    stability_seconds: int = 0

    def __post_init__(self) -> None:
        supported = {"pptx", "html", "html_zip"}
        if not self.formats or not self.formats.issubset(supported):
            raise ValueError("formats must contain pptx, html, or html_zip")
        if self.min_file_bytes < 0:
            raise ValueError("min_file_bytes cannot be negative")
        if self.max_file_bytes <= 0 or self.min_file_bytes > self.max_file_bytes:
            raise ValueError("invalid file size range")
        if self.stability_seconds < 0:
            raise ValueError("stability_seconds cannot be negative")


@dataclass(frozen=True, slots=True)
class DiscoveryIssue:
    path: Path
    reason: str
    size_bytes: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    accepted: tuple[DiscoveredFile, ...]
    rejected: tuple[DiscoveryIssue, ...]


_FORMAT_BY_SUFFIX = {
    ".pptx": "pptx",
    ".html": "html",
    ".htm": "html",
    ".zip": "html_zip",
}
_INCOMPLETE_SUFFIXES = {".tmp", ".part", ".download", ".crdownload"}


def _hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_file(
    path: Path,
    *,
    rules: ScanRules,
    now_ns: int,
) -> DiscoveredFile | DiscoveryIssue:
    if path.is_symlink():
        return DiscoveryIssue(path, "symlink", detail="符号链接不会被扫描")
    if not path.is_file():
        return DiscoveryIssue(path, "not_file", detail="路径不是可读取文件")
    if path.name.startswith("~$"):
        return DiscoveryIssue(path, "temporary", detail="Office 临时文件")
    if path.name.startswith("."):
        return DiscoveryIssue(path, "hidden", detail="隐藏文件")
    suffix = path.suffix.lower()
    if suffix in _INCOMPLETE_SUFFIXES:
        return DiscoveryIssue(path, "temporary", detail="文件仍在下载或写入")
    source_format = _FORMAT_BY_SUFFIX.get(suffix)
    if source_format is None or source_format not in rules.formats:
        return DiscoveryIssue(path, "unsupported", detail="文件格式不在计划范围内")
    try:
        stat = path.stat()
    except OSError as error:
        return DiscoveryIssue(path, "unreadable", detail=str(error))
    if stat.st_size < rules.min_file_bytes:
        return DiscoveryIssue(
            path,
            "below_minimum",
            size_bytes=stat.st_size,
            detail="文件小于计划设置的最小值",
        )
    if stat.st_size > rules.max_file_bytes:
        return DiscoveryIssue(
            path,
            "above_maximum",
            size_bytes=stat.st_size,
            detail="文件超过计划设置的最大值",
        )
    stable_ns = rules.stability_seconds * 1_000_000_000
    if stable_ns and now_ns - stat.st_mtime_ns < stable_ns:
        return DiscoveryIssue(
            path,
            "unstable",
            size_bytes=stat.st_size,
            detail=f"文件需保持 {rules.stability_seconds} 秒未变化",
        )
    return DiscoveredFile(
        path=path.resolve(),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        ctime_ns=stat.st_ctime_ns,
    )


def _discover_file(
    path: Path,
    *,
    max_file_bytes: int,
    include_html: bool = False,
) -> DiscoveredFile | None:
    """Read cheap file metadata for one usable PPTX without hashing its contents."""
    formats = frozenset({"pptx", "html", "html_zip"} if include_html else {"pptx"})
    result = _inspect_file(
        path,
        rules=ScanRules(formats=formats, max_file_bytes=max_file_bytes),
        now_ns=time.time_ns(),
    )
    return result if isinstance(result, DiscoveredFile) else None


def discover_paths_with_diagnostics(
    paths: list[Path] | tuple[Path, ...],
    *,
    rules: ScanRules,
) -> DiscoveryReport:
    """Discover source files and retain a reason for every skipped candidate."""
    accepted: dict[str, DiscoveredFile] = {}
    rejected: dict[str, DiscoveryIssue] = {}
    now_ns = time.time_ns()
    for raw in paths:
        expanded = raw.expanduser()
        if expanded.is_symlink():
            rejected.setdefault(str(expanded), DiscoveryIssue(expanded, "symlink"))
            continue
        resolved = expanded.resolve()
        if not resolved.exists():
            rejected.setdefault(
                str(resolved),
                DiscoveryIssue(resolved, "missing", detail="路径不存在或需要重新授权"),
            )
            continue
        if resolved.is_dir():
            iterator = resolved.rglob("*") if rules.recursive else resolved.iterdir()
            for candidate in sorted(iterator, key=lambda item: str(item).casefold()):
                if candidate.is_dir():
                    continue
                result = _inspect_file(candidate, rules=rules, now_ns=now_ns)
                key = str(candidate.resolve()) if candidate.exists() else str(candidate)
                if isinstance(result, DiscoveredFile):
                    accepted.setdefault(key, result)
                    rejected.pop(key, None)
                elif key not in accepted:
                    rejected.setdefault(key, result)
            continue
        result = _inspect_file(resolved, rules=rules, now_ns=now_ns)
        key = str(resolved)
        if isinstance(result, DiscoveredFile):
            accepted.setdefault(key, result)
            rejected.pop(key, None)
        elif key not in accepted:
            rejected.setdefault(key, result)
    return DiscoveryReport(
        tuple(accepted[key] for key in sorted(accepted, key=str.casefold)),
        tuple(rejected[key] for key in sorted(rejected, key=str.casefold)),
    )


def hash_discovered_file(discovered: DiscoveredFile) -> ScannedFile:
    before = discovered.path.stat()
    expected = (discovered.size_bytes, discovered.mtime_ns, discovered.ctime_ns)
    observed = (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    if observed != expected:
        raise OSError(f"source changed before hashing: {discovered.path}")
    if discovered.path.suffix.lower() in {".html", ".htm"}:
        from pptlib.html.source import HtmlSource

        digest = HtmlSource(discovered.path).fingerprint
    else:
        digest = _hash_file(discovered.path)
    after = discovered.path.stat()
    final = (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if final != observed:
        raise OSError(f"source changed while hashing: {discovered.path}")
    return ScannedFile(
        path=discovered.path,
        sha256=digest,
        size_bytes=after.st_size,
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
    )


def discover_source_root(
    root: Path,
    *,
    max_file_bytes: int = 500 * 1024 * 1024,
    include_html: bool = False,
) -> list[DiscoveredFile]:
    """Find readable PPTX files below *root* without reading their contents.

    Temporary Office lock files (``~$foo.pptx``), symlinks, and files over the
    configured limit are skipped. The returned order is deterministic.
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        return []
    found: list[DiscoveredFile] = []
    pattern = "*" if include_html else "*.pptx"
    for path in sorted(root.rglob(pattern), key=lambda item: str(item).casefold()):
        discovered = _discover_file(
            path,
            max_file_bytes=max_file_bytes,
            include_html=include_html,
        )
        if discovered is not None:
            found.append(discovered)
    return found


def discover_paths(
    paths: list[Path] | tuple[Path, ...],
    *,
    max_file_bytes: int = 500 * 1024 * 1024,
    include_html: bool = False,
) -> list[DiscoveredFile]:
    """Discover a mix of explicit PPTX files and directories.

    Files are indexed *in place* (their real path becomes the canonical path,
    so nothing is copied); directories are walked recursively.
    De-duplicated by resolved path, deterministic order.
    """
    formats = frozenset({"pptx", "html", "html_zip"} if include_html else {"pptx"})
    report = discover_paths_with_diagnostics(
        paths,
        rules=ScanRules(formats=formats, max_file_bytes=max_file_bytes),
    )
    return list(report.accepted)


def scan_source_root(
    root: Path,
    *,
    max_file_bytes: int = 500 * 1024 * 1024,
) -> list[ScannedFile]:
    return [
        hash_discovered_file(item)
        for item in discover_source_root(root, max_file_bytes=max_file_bytes)
    ]


def scan_paths(
    paths: list[Path] | tuple[Path, ...],
    *,
    max_file_bytes: int = 500 * 1024 * 1024,
) -> list[ScannedFile]:
    return [
        hash_discovered_file(item) for item in discover_paths(paths, max_file_bytes=max_file_bytes)
    ]
