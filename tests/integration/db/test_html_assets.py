from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from pptlib.application.catalog import build_catalog
from pptlib.application.compose import compose_from_manifest, compose_from_slide_ids
from pptlib.application.library import (
    InMemorySelectionStore,
    InMemorySlideCatalog,
    LibraryFilters,
    PageLibraryService,
    SlideSummary,
)
from pptlib.config import Settings, load_settings
from pptlib.discovery.scanner import ScannedFile
from pptlib.domain.errors import AppError, ErrorCode
from pptlib.export.ooxml import ExportResult
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.library import (
    SqliteSelectionStore,
    SqliteSlideCatalog,
    selection_slide_refs,
)
from pptlib.infrastructure.db.migrations import migrate
from pptlib.infrastructure.db.repositories import (
    DeckRepository,
    ImportedDeck,
    count_search_slides,
    deck_id_for,
    search_slides,
    version_id_for,
)
from pptlib.ingestion.parser import PARSER_VERSION, ParsedDeck, ParsedSlide

MIGRATIONS = Path(__file__).resolve().parents[3] / "src" / "pptlib" / "migrations"
HTML_EXPORT_MESSAGE = (
    "HTML \u9875\u9762\u5f53\u524d\u652f\u6301\u5165\u5e93\u548c\u9884\u89c8\uff0c"
    "\u7ec4\u5408\u5bfc\u51fa\u5c06\u5728\u540e\u7eed\u9636\u6bb5\u5f00\u653e"
)
HTML_CAPABILITIES = {
    "dynamic_preview": True,
    "video": True,
    "iframe": False,
    "requires_network": False,
    "html_export": False,
    "pptx_export": False,
}
HTML_WARNINGS = [{"code": "EXPORT_UNSUPPORTED", "message": "Preview only"}]
DEPENDENCIES = [
    {
        "kind": "data_uri",
        "sha256": "a" * 64,
        "locator": {"uri_sha256": "b" * 64},
        "mime": "video/mp4",
        "size_bytes": 10_000_000,
        "page_keys": ["overview"],
    }
]


def _html() -> ParsedDeck:
    return ParsedDeck(
        slides=(
            ParsedSlide(
                1,
                "Shared overview",
                "Shared body",
                "Notes",
                page_key="overview",
                page_kind="h5_raw",
                composition_ref_json=json.dumps(
                    {
                        "adapter": "test",
                        "page_key": "overview",
                        "source_sha256": "adapter-fingerprint",
                        "entry": "index.html",
                        "source_version_id": "must-be-replaced",
                    }
                ),
                capabilities_json=json.dumps(HTML_CAPABILITIES),
            ),
            ParsedSlide(
                2, "Shared ending", "Shared body", "", page_key="ending", page_kind="h5_schema"
            ),
        ),
        parser_version="html-test-v1",
        source_format="render_deck_html",
        canonical_format="deckjson_reference",
        dependencies_json=json.dumps(DEPENDENCIES),
        capabilities_json='{"preview":true}',
        warnings_json=json.dumps(HTML_WARNINGS),
        renderer_version="renderer-test-v1",
    )


def _import(settings: Settings, name: str, parsed: ParsedDeck) -> ImportedDeck:
    path = settings.home / name
    path.write_text(f"Source fixture: {name}", encoding="utf-8")
    scanned = ScannedFile(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, 1
    )
    connection = connect(settings.database_path)
    try:
        return DeckRepository(connection).import_parsed(scanned, parsed)
    finally:
        connection.close()


@pytest.fixture
def library(tmp_path: Path) -> tuple[Settings, ImportedDeck, ImportedDeck]:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "temp"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    connection = connect(settings.database_path)
    try:
        migrate(connection, MIGRATIONS)
    finally:
        connection.close()
    pptx = _import(settings, "a.pptx", ParsedDeck((ParsedSlide(1, "Shared PPTX", "Body", ""),)))
    html = _import(settings, "b.html", _html())
    return settings, pptx, html


def _slide_id(deck: ImportedDeck, number: int = 1) -> str:
    return f"{deck.version_id}_s{number:05d}"


def test_parsed_models_keep_pptx_defaults_and_positional_constructors() -> None:
    slide = ParsedSlide(1, "Title", "Body", "Notes")
    deck = ParsedDeck((slide,))
    assert slide.content_text == "Title\nBody\nNotes"
    assert (slide.page_key, slide.page_kind) == ("", "ooxml")
    assert slide.composition_ref_json == slide.capabilities_json == "{}"
    assert deck.parser_version == PARSER_VERSION
    assert (deck.source_format, deck.canonical_format) == ("pptx", "pptx_package")
    assert deck.dependencies_json == deck.warnings_json == "[]"
    assert deck.capabilities_json == "{}"
    assert deck.renderer_version == ""
    first = SlideSummary("one", "deck", "Deck", 1, "Title")
    second = SlideSummary("two", "deck", "Deck", 2, "Title")
    first.capabilities["dynamic"] = True
    first.warnings.append("warning")
    assert second.capabilities == {}
    assert second.warnings == []


def test_metadata_round_trip_keeps_ids_and_only_lightweight_dependencies(library) -> None:
    settings, pptx, html = library
    parsed = _html()
    connection = connect(settings.database_path)
    try:
        version = connection.execute(
            "SELECT * FROM deck_versions WHERE id = ?", (html.version_id,)
        ).fetchone()
        for key in (
            "source_format",
            "canonical_format",
            "dependencies_json",
            "capabilities_json",
            "warnings_json",
            "renderer_version",
        ):
            assert version[key] == getattr(parsed, key)
        assert json.loads(version["dependencies_json"]) == DEPENDENCIES
        assert len(version["dependencies_json"]) < 1_000
        slide = connection.execute(
            "SELECT * FROM slides WHERE id = ?", (_slide_id(html),)
        ).fetchone()
        for key in ("page_key", "page_kind", "capabilities_json"):
            assert slide[key] == getattr(parsed.slides[0], key)
        assert json.loads(slide["composition_ref_json"]) == {
            **json.loads(parsed.slides[0].composition_ref_json),
            "source_version_id": html.version_id,
        }
        assert html.deck_id == deck_id_for(html.path)
        assert html.version_id == version_id_for(html.deck_id, version["sha256"])
        before = [dict(row) for row in connection.execute("SELECT * FROM slides ORDER BY id")]
        scanned = ScannedFile(html.path, version["sha256"], version["size_bytes"], 1)
        again = DeckRepository(connection).import_parsed(scanned, parsed)
        assert not again.created
        assert again.version_id == html.version_id
        assert before == [
            dict(row) for row in connection.execute("SELECT * FROM slides ORDER BY id")
        ]
        assert connection.execute("SELECT COUNT(*) FROM deck_versions").fetchone()[0] == 2
        pptx_version = connection.execute(
            "SELECT * FROM deck_versions WHERE id = ?", (pptx.version_id,)
        ).fetchone()
        assert pptx_version["source_format"] == "pptx"
        assert pptx_version["canonical_format"] == "pptx_package"
    finally:
        connection.close()
    # Sidecar production belongs to the import coordinator, not the repository.
    assert not (settings.assets_dir / "html-manifests").exists()


def test_adapter_fingerprint_drives_version_and_composition_reference(library) -> None:
    settings, _, html = library
    catalog = SqliteSlideCatalog(settings.database_path)
    old_slide = catalog.get(_slide_id(html))
    assert old_slide is not None
    store = SqliteSelectionStore(settings.database_path)
    store.add(old_slide)
    fingerprint = hashlib.sha256(b"entry plus changed local dependency").hexdigest()
    parsed = _html()
    pages = tuple(
        replace(
            page,
            composition_ref_json=json.dumps(
                {
                    **json.loads(page.composition_ref_json),
                    "source_sha256": fingerprint,
                }
            ),
        )
        for page in parsed.slides
    )
    connection = connect(settings.database_path)
    try:
        updated = DeckRepository(connection).import_parsed(
            ScannedFile(html.path, fingerprint, html.path.stat().st_size, 2),
            replace(parsed, slides=pages),
        )
        assert updated.deck_id == html.deck_id
        assert updated.version_id == version_id_for(html.deck_id, fingerprint)
        assert updated.version_id != html.version_id
        refs = connection.execute(
            "SELECT composition_ref_json FROM slides WHERE deck_version_id = ?",
            (updated.version_id,),
        ).fetchall()
        assert len(refs) == 2
        for row in refs:
            reference = json.loads(row[0])
            assert reference["source_version_id"] == updated.version_id
            assert reference["source_sha256"] == fingerprint
        assert (
            count_search_slides(
                connection, "Shared", LibraryFilters(source_format="render_deck_html")
            )
            == 2
        )
    finally:
        connection.close()
    assert catalog.get(old_slide.id) is None
    assert [item.id for item in store.list()] == [old_slide.id]
    with pytest.raises(AppError, match=HTML_EXPORT_MESSAGE):
        selection_slide_refs(settings.database_path)


def test_invalid_html_composition_reference_rolls_back_import(library) -> None:
    settings, _, _ = library
    connection = connect(settings.database_path)
    try:
        parsed = _html()
        invalid = replace(parsed.slides[0], composition_ref_json="[]")
        with pytest.raises(ValueError, match="composition_ref_json must be an object"):
            DeckRepository(connection).import_parsed(
                ScannedFile(settings.home / "invalid.html", "invalid", 1, 1),
                replace(parsed, slides=(invalid,)),
            )
        assert not connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM slides").fetchone()[0] == 3
    finally:
        connection.close()


@pytest.mark.parametrize(
    "dependencies",
    [
        {},
        ["not-metadata"],
        [{"payload": "encoded-video"}],
        [{"locator": {"base64": "encoded-video"}}],
        [{"locator": "data:video/mp4;base64,AAAA"}],
    ],
)
def test_dependency_payloads_are_rejected_before_any_database_write(library, dependencies) -> None:
    settings, _, _ = library
    connection = connect(settings.database_path)
    try:
        scanned = ScannedFile(settings.home / "rejected.html", "rejected", 1, 1)
        with pytest.raises(ValueError, match="dependencies_json"):
            DeckRepository(connection).import_parsed(
                scanned, replace(_html(), dependencies_json=json.dumps(dependencies))
            )
        assert not connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.parametrize("query", ["", "Shared"])
@pytest.mark.parametrize(
    "source_format,expected",
    [
        ("", 3),
        ("pptx", 1),
        ("render_deck_html", 2),
        ("missing", 0),
        ("render_deck_html' OR 1=1 --", 0),
    ],
)
def test_format_filter_matches_search_count_and_memory(library, query, source_format, expected):
    settings, _, html = library
    filters = LibraryFilters(source_format=source_format)
    connection = connect(settings.database_path)
    try:
        results = search_slides(connection, query, filters=filters)
        assert len(results) == count_search_slides(connection, query, filters) == expected
        for result in results:
            assert isinstance(result.capabilities, dict)
            assert isinstance(result.warnings, list)
        combined = LibraryFilters(source_format=source_format, deck_id=html.deck_id)
        assert count_search_slides(connection, query, combined) == (
            2 if source_format in ("", "render_deck_html") else 0
        )
    finally:
        connection.close()
    catalog = SqliteSlideCatalog(settings.database_path)
    all_items = catalog.search("", page=1, page_size=20).items
    memory = InMemorySlideCatalog(all_items)
    for adapter in (catalog, memory):
        page = adapter.search(query, page=1, page_size=1, filters=filters)
        assert page.total == expected
        assert len(page.items) == min(1, expected)
        if adapter is catalog:
            assert [item.id for item in page.items] == [item.slide_id for item in results[:1]]
        all_matches = adapter.search(query, page=1, page_size=20, filters=filters)
        assert {item.id for item in all_matches.items} == {item.slide_id for item in results}
        assert len(adapter.search(query, page=2, page_size=1, filters=filters).items) == (
            1 if expected > 1 else 0
        )
    assert LibraryFilters().to_dict() == {"topic": "", "page_type": "", "deck_id": ""}
    if source_format:
        assert filters.to_dict()["source_format"] == source_format


def test_catalog_search_detail_and_selection_expose_parsed_metadata(library) -> None:
    settings, pptx, html = library
    expected = {
        "source_format": "render_deck_html",
        "page_key": "overview",
        "page_kind": "h5_raw",
        "capabilities": HTML_CAPABILITIES,
        "warnings": HTML_WARNINGS,
    }
    catalog = SqliteSlideCatalog(settings.database_path)
    slide = catalog.get(_slide_id(html))
    assert slide is not None
    exported = build_catalog(settings)
    catalog_row = next(row.to_row() for row in exported.slides if row.slide_id == slide.id)
    assert "source_path" not in catalog_row
    assert "search_text" not in catalog_row
    assert "composition_ref_json" not in catalog_row
    payloads = [slide.to_dict(), catalog_row]
    connection = connect(settings.database_path)
    try:
        payloads.append(asdict(search_slides(connection, "overview")[0]))
    finally:
        connection.close()
    for store in (SqliteSelectionStore(settings.database_path), InMemorySelectionStore()):
        service = PageLibraryService(catalog, store)
        service.add(_slide_id(pptx))
        service.add(slide.id)
        service.reorder([slide.id, _slide_id(pptx)])
        assert [item.id for item in store.list()] == [slide.id, _slide_id(pptx)]
        payloads.append(service.selection_dict()["items"][0])
    for payload in payloads:
        assert {key: payload[key] for key in expected} == expected
    pptx_summary = catalog.get(_slide_id(pptx))
    assert pptx_summary is not None
    assert pptx_summary.source_format == "pptx"
    assert pptx_summary.page_key == ""
    assert pptx_summary.page_kind == "ooxml"
    assert pptx_summary.capabilities == {}
    assert pptx_summary.warnings == []


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("source_exists", [False, True])
def test_compose_rejects_html_before_ooxml_or_source_checks(
    library, monkeypatch, mixed, source_exists
) -> None:
    settings, pptx, html = library
    if not source_exists:
        html.path.unlink()
        pptx.path.unlink()
    refs = Mock(side_effect=AssertionError("OOXML reference must not be constructed"))
    exporter = Mock(side_effect=AssertionError("OOXML exporter must not run"))
    monkeypatch.setattr("pptlib.application.compose.SlideRef", refs)
    monkeypatch.setattr("pptlib.application.compose.export_slides", exporter)
    slide_ids = [_slide_id(pptx), _slide_id(html)] if mixed else [_slide_id(html)]
    output = settings.home / "out.pptx"
    with pytest.raises(AppError) as error:
        compose_from_slide_ids(settings, slide_ids, output, verify_source_hash=False)
    assert error.value.code == ErrorCode.REQUEST_INVALID
    assert str(error.value) == HTML_EXPORT_MESSAGE
    manifest = settings.home / "selection.json"
    manifest.write_text(json.dumps({"slide_ids": slide_ids}), encoding="utf-8")
    with pytest.raises(AppError, match=HTML_EXPORT_MESSAGE):
        compose_from_manifest(settings, manifest, output)
    refs.assert_not_called()
    exporter.assert_not_called()
    assert not output.exists()
    assert not output.with_suffix(".manifest.json").exists()


def test_pptx_compose_still_passes_locked_hash_and_order(library, monkeypatch) -> None:
    settings, pptx, _ = library
    output = settings.home / "out.pptx"
    manifest = settings.home / "out.manifest.json"
    expected = ExportResult("export", output, manifest, 2, (), "native")
    exporter = Mock(return_value=expected)
    monkeypatch.setattr("pptlib.application.compose.export_slides", exporter)
    assert (
        compose_from_slide_ids(
            settings, [_slide_id(pptx), _slide_id(pptx)], output, manifest_path=manifest
        )
        == expected
    )
    refs, actual_output, actual_manifest = exporter.call_args.args
    assert (actual_output, actual_manifest) == (output, manifest)
    assert len(refs) == 2
    assert all(ref.file_version_id == pptx.version_id and ref.page_number == 1 for ref in refs)
    assert all(ref.source_path == pptx.path for ref in refs)
    assert all(
        ref.expected_sha256 == hashlib.sha256(pptx.path.read_bytes()).hexdigest() for ref in refs
    )


def test_direct_export_guard_uses_selected_html_version(library, monkeypatch):
    settings, pptx, html = library
    catalog = SqliteSlideCatalog(settings.database_path)
    item = catalog.search("overview", page=1, page_size=10).items[0]
    assert item.source_format == "render_deck_html"
    assert item.page_key == "overview"
    assert item.page_kind == "h5_raw"
    assert item.capabilities == HTML_CAPABILITIES
    assert item.warnings == HTML_WARNINGS

    selection = SqliteSelectionStore(settings.database_path)
    pptx_item = catalog.get(_slide_id(pptx))
    assert pptx_item is not None
    selection.add(pptx_item)
    selection.add(item)
    assert selection.list()[1] == item

    # A new current PPTX version must not bypass the selected HTML version's guard.
    connection = connect(settings.database_path)
    try:
        replacement = ParsedDeck((ParsedSlide(1, "Replacement", "", ""),))
        DeckRepository(connection).import_parsed(
            ScannedFile(html.path, "new-pptx-digest", 1, 2), replacement
        )
    finally:
        connection.close()
    refs = Mock(side_effect=AssertionError("OOXML reference must not be constructed"))
    monkeypatch.setattr("pptlib.export.ooxml.SlideRef", refs)
    with pytest.raises(AppError, match=HTML_EXPORT_MESSAGE):
        selection_slide_refs(settings.database_path)
    refs.assert_not_called()
    assert not list(settings.output_root.glob("*.pptx"))
