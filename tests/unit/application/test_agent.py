from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pptlib.application.agent import (
    MAX_SELECTION_BYTES,
    SELECTION_SCHEMA,
    validate_output_path,
    validate_selection_manifest,
)
from pptlib.config import load_settings
from pptlib.domain.errors import AppError, ErrorCode


def _settings(tmp_path: Path):
    return replace(
        load_settings({}),
        home=tmp_path / "home",
        database_path=tmp_path / "home" / "pages.db",
        assets_dir=tmp_path / "home" / "assets",
        temp_dir=tmp_path / "tmp",
        log_dir=tmp_path / "logs",
        output_root=tmp_path / "exports",
    )


def test_agent_manifest_requires_versioned_strict_shape(tmp_path: Path) -> None:
    manifest = tmp_path / "selection.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": SELECTION_SCHEMA,
                "slide_ids": ["slide_a", "slide_b"],
            }
        ),
        encoding="utf-8",
    )

    assert validate_selection_manifest(manifest) == manifest.resolve()


@pytest.mark.parametrize(
    "payload",
    [
        ["slide_a"],
        {"slide_ids": ["slide_a"]},
        {"schema_version": "unknown", "slide_ids": ["slide_a"]},
        {"schema_version": SELECTION_SCHEMA, "slide_ids": []},
        {"schema_version": SELECTION_SCHEMA, "slide_ids": [1]},
    ],
)
def test_agent_manifest_rejects_ambiguous_or_invalid_payloads(
    tmp_path: Path,
    payload: object,
) -> None:
    manifest = tmp_path / "selection.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AppError) as excinfo:
        validate_selection_manifest(manifest)

    assert excinfo.value.code == ErrorCode.REQUEST_INVALID


def test_agent_output_must_stay_inside_configured_root(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert validate_output_path(settings, settings.output_root / "deck.pptx") == (
        settings.output_root / "deck.pptx"
    ).resolve()

    with pytest.raises(AppError) as excinfo:
        validate_output_path(settings, tmp_path / "outside.pptx")

    assert excinfo.value.code == ErrorCode.PATH_NOT_ALLOWED


def test_agent_manifest_enforces_actual_read_limit(tmp_path: Path) -> None:
    manifest = tmp_path / "selection.json"
    manifest.write_bytes(b" " * (MAX_SELECTION_BYTES + 1))

    with pytest.raises(AppError) as excinfo:
        validate_selection_manifest(manifest)

    assert excinfo.value.code == ErrorCode.REQUEST_INVALID


@pytest.mark.parametrize("companion_suffix", [None, ".manifest.json"])
def test_agent_output_rejects_symlink_escape(
    tmp_path: Path,
    companion_suffix: str | None,
) -> None:
    settings = _settings(tmp_path)
    settings.output_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    if companion_suffix is None:
        (settings.output_root / "linked").symlink_to(outside, target_is_directory=True)
        output = settings.output_root / "linked" / "deck.pptx"
    else:
        output = settings.output_root / "deck.pptx"
        output.with_suffix(companion_suffix).symlink_to(outside / "escaped")

    with pytest.raises(AppError) as excinfo:
        validate_output_path(settings, output)

    assert excinfo.value.code == ErrorCode.PATH_NOT_ALLOWED


@pytest.mark.parametrize("companion_suffix", [".manifest.json"])
def test_agent_output_rejects_companion_directories(
    tmp_path: Path,
    companion_suffix: str,
) -> None:
    settings = _settings(tmp_path)
    settings.output_root.mkdir(parents=True)
    output = settings.output_root / "deck.pptx"
    output.with_suffix(companion_suffix).mkdir()

    with pytest.raises(AppError) as excinfo:
        validate_output_path(settings, output)

    assert excinfo.value.code == ErrorCode.PATH_NOT_ALLOWED
