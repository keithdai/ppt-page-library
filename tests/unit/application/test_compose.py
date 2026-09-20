from __future__ import annotations

import json
from pathlib import Path

import pytest

from pptlib.application.compose import (
    _remap_source_path,
    load_manifest_slide_ids,
)
from pptlib.domain.errors import AppError, ErrorCode


def test_load_manifest_accepts_bare_list(tmp_path: Path) -> None:
    manifest = tmp_path / "sel.json"
    manifest.write_text(json.dumps(["a", "b", "c"]), encoding="utf-8")
    assert load_manifest_slide_ids(manifest) == ["a", "b", "c"]


def test_load_manifest_accepts_slide_ids_object(tmp_path: Path) -> None:
    manifest = tmp_path / "sel.json"
    manifest.write_text(json.dumps({"slide_ids": ["x", "y"]}), encoding="utf-8")
    assert load_manifest_slide_ids(manifest) == ["x", "y"]


def test_load_manifest_accepts_camelcase_slide_ids(tmp_path: Path) -> None:
    # The Miaoda web app exports {"schemaVersion", "generatedAt", "slideIds"}.
    manifest = tmp_path / "sel.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "ppt-selection-v1",
                "generatedAt": "2026-09-20T00:00:00Z",
                "slideIds": ["b", "a"],
            }
        ),
        encoding="utf-8",
    )
    assert load_manifest_slide_ids(manifest) == ["b", "a"]


def test_load_manifest_accepts_items_with_order(tmp_path: Path) -> None:
    manifest = tmp_path / "sel.json"
    manifest.write_text(
        json.dumps(
            {
                "items": [
                    {"slide_id": "second", "order": 2},
                    {"slide_id": "first", "order": 1},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_manifest_slide_ids(manifest) == ["first", "second"]


def test_load_manifest_rejects_empty(tmp_path: Path) -> None:
    manifest = tmp_path / "sel.json"
    manifest.write_text(json.dumps([]), encoding="utf-8")
    with pytest.raises(AppError) as excinfo:
        load_manifest_slide_ids(manifest)
    assert excinfo.value.code == ErrorCode.REQUEST_INVALID


def test_load_manifest_rejects_bad_json(tmp_path: Path) -> None:
    manifest = tmp_path / "sel.json"
    manifest.write_text("{not json", encoding="utf-8")
    with pytest.raises(AppError) as excinfo:
        load_manifest_slide_ids(manifest)
    assert excinfo.value.code == ErrorCode.REQUEST_INVALID


def test_remap_source_path_prefers_existing(tmp_path: Path) -> None:
    real = tmp_path / "deck.pptx"
    real.write_bytes(b"pptx")
    assert _remap_source_path(str(real), tmp_path / "home") == real


def test_remap_source_path_rewrites_container_data_prefix(tmp_path: Path) -> None:
    home = tmp_path / "home"
    nested = home / "uploaded_sources" / "abc" / "deck.pptx"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"pptx")
    remapped = _remap_source_path("/data/uploaded_sources/abc/deck.pptx", home)
    assert remapped == nested
