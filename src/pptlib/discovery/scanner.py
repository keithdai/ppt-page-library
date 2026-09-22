from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ScannedFile:
    path: Path
    sha256: str
    size_bytes: int
    mtime_ns: int


def _hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_file(path: Path, *, max_file_bytes: int) -> ScannedFile | None:
    """Build a :class:`ScannedFile` for a single pptx, or ``None`` if unusable."""
    if path.is_symlink() or not path.is_file() or path.name.startswith("~$"):
        return None
    if path.suffix.lower() != ".pptx":
        return None
    stat = path.stat()
    if stat.st_size > max_file_bytes:
        return None
    return ScannedFile(
        path=path,
        sha256=_hash_file(path),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def scan_source_root(
    root: Path,
    *,
    max_file_bytes: int = 500 * 1024 * 1024,
) -> list[ScannedFile]:
    """Find readable PPTX files below *root* and calculate stable identities.

    Temporary Office lock files (``~$foo.pptx``), symlinks, and files over the
    configured limit are skipped. The returned order is deterministic.
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        return []
    found: list[ScannedFile] = []
    for path in sorted(root.rglob("*.pptx"), key=lambda item: str(item).casefold()):
        scanned = _scan_file(path, max_file_bytes=max_file_bytes)
        if scanned is not None:
            found.append(scanned)
    return found


def scan_paths(
    paths: list[Path] | tuple[Path, ...],
    *,
    max_file_bytes: int = 500 * 1024 * 1024,
) -> list[ScannedFile]:
    """Scan a mix of explicit PPTX files and directories.

    Files are indexed *in place* (their real path becomes the canonical path,
    so nothing is copied); directories are walked like :func:`scan_source_root`.
    De-duplicated by resolved path, deterministic order.
    """
    seen: dict[str, ScannedFile] = {}
    for raw in paths:
        p = raw.expanduser().resolve()
        if p.is_dir():
            for scanned in scan_source_root(p, max_file_bytes=max_file_bytes):
                seen.setdefault(str(scanned.path), scanned)
        elif p.is_file():
            scanned = _scan_file(p, max_file_bytes=max_file_bytes)
            if scanned is not None:
                seen.setdefault(str(scanned.path), scanned)
    return [seen[key] for key in sorted(seen, key=str.casefold)]
