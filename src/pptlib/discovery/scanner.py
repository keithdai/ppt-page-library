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
        if path.is_symlink() or not path.is_file() or path.name.startswith("~$"):
            continue
        stat = path.stat()
        if stat.st_size > max_file_bytes:
            continue
        found.append(
            ScannedFile(
                path=path,
                sha256=_hash_file(path),
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
        )
    return found
