#!/usr/bin/env python3
"""Fail closed when repository boundaries or current documentation drift."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PREFIXES = (
    ".trae-html-share-packages/",
    ".worktrees/",
    "build/",
    "desktop/dist/",
    "desktop/node_modules/",
    "dist-app/",
    "sources/",
    "交付/",
)
FORBIDDEN_SUFFIXES = (".bak", ".old", ".orig", ".tmp", ".log")
REQUIRED_DOCUMENTS = (
    "AGENTS.md",
    "documentation/README.md",
    "documentation/architecture.md",
    "documentation/flows.md",
    "documentation/permissions.md",
    "documentation/variables.md",
    "documentation/cron.md",
    "documentation/automation.md",
    "documentation/seo.md",
    "documentation/tests.md",
    "documentation/html-archive.md",
    "documentation/agent-cli.md",
    "docs/README.md",
    "docs/acceptance/README.md",
    "docs/_archive/README.md",
)
CURRENT_DOC_PREFIXES = ("documentation/", "docs/acceptance/")
LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def current_markdown(files: list[str]) -> list[Path]:
    selected: set[Path] = set()
    for name in files:
        if not name.endswith(".md"):
            continue
        if name in {"README.md", "AGENTS.md", "docs/README.md"}:
            selected.add(ROOT / name)
            continue
        if name.startswith(CURRENT_DOC_PREFIXES):
            selected.add(ROOT / name)
    for name in REQUIRED_DOCUMENTS:
        if name.endswith(".md") and (ROOT / name).is_file():
            selected.add(ROOT / name)
    return sorted(selected)


def local_link_target(raw: str) -> str | None:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        value = value[1 : value.index(">")]
    elif " " in value:
        value = value.split(" ", 1)[0]
    if not value or value.startswith(("#", "http://", "https://", "mailto:", "file:")):
        return None
    return unquote(value.split("#", 1)[0].split("?", 1)[0])


def check_links(documents: list[Path]) -> list[str]:
    errors: list[str] = []
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(text):
            target = local_link_target(match.group(1))
            if target is None:
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                relative = document.relative_to(ROOT)
                errors.append(f"{relative}: broken link -> {target}")
    return errors


def main() -> int:
    files = tracked_files()
    errors: list[str] = []

    for name in files:
        if name != "var/.gitkeep" and (
            name.startswith(FORBIDDEN_PREFIXES) or name.endswith(FORBIDDEN_SUFFIXES)
        ):
            errors.append(f"tracked generated/private artifact: {name}")
        basename = Path(name).name
        if basename.startswith(".env") and basename != ".env.example":
            errors.append(f"tracked environment file: {name}")

    for required in REQUIRED_DOCUMENTS:
        if not (ROOT / required).is_file():
            errors.append(f"missing required governance document: {required}")

    errors.extend(check_links(current_markdown(files)))

    if errors:
        print("Repository checks failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"Repository checks passed ({len(files)} tracked files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
