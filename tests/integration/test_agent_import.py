from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from pptlib.agent_cli import ExitCode, main
from pptlib.application import agent_import, import_decks
from pptlib.application.agent_import import import_agent_files
from pptlib.bootstrap import initialize
from pptlib.cli import main as private_main
from pptlib.config import Settings, load_settings
from pptlib.domain.errors import AppError
from pptlib.infrastructure.db.connection import connect_readonly
from pptlib.infrastructure.operation_lock import heavy_operation_lock
from pptlib.rendering.thumbnails import ThumbnailError, _write_render_marker


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for key, directory in (
        ("PPTLIB_HOME", "library"),
        ("PPTLIB_OUTPUT_ROOT", "exports"),
        ("PPTLIB_TEMP_DIR", "tmp"),
        ("PPTLIB_LOG_DIR", "logs"),
    ):
        monkeypatch.setenv(key, str(tmp_path / directory))
    monkeypatch.setenv("PPTLIB_ENABLE_HTML", "0")
    return load_settings()


def write_pptx(path: Path, title: str = "Agent Import Evidence") -> None:
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{p}" xmlns:r="{r}">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="{rel}"><Relationship Id="rId1" '
            f'Type="{r}/slide" Target="slides/slide1.xml"/></Relationships>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree><p:sp>'
            f'<p:txBody><a:p><a:r><a:t>{escape(title)}</a:t></a:r></a:p></p:txBody>'
            '</p:sp></p:spTree></p:cSld></p:sld>',
        )


@pytest.fixture
def render_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    def render(source, version_id, slide_count, **kwargs):
        assets = kwargs["assets_dir"]
        for number in range(1, slide_count + 1):
            for kind in ("thumbnails", "previews"):
                image = assets / kind / f"{version_id}_s{number:05d}.jpg"
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b"test-jpeg")
        _write_render_marker(assets, version_id, "libreoffice")
        return slide_count

    monkeypatch.setattr("pptlib.application.import_decks.render_deck_thumbnails", render)
    monkeypatch.setattr(
        "pptlib.application.html_assets.render_html_version",
        lambda *_args, **_kwargs: None,
    )


def response(capsys: pytest.CaptureFixture[str]) -> dict:
    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1
    return json.loads(captured.out)


def test_import_pptx_search_reimport_and_update(
    settings: Settings, render_stub: None, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "AI output with spaces.pptx"
    write_pptx(source)
    original = source.read_bytes()
    assert main(["import", str(source), "--allow-write"]) == ExitCode.OK
    first = response(capsys)["result"]
    assert first["status"] == "completed"
    deck = first["imported"][0]
    assert deck["action"] == "created" and deck["preview_ready"]
    assert main(["search", "--query", "Agent Import Evidence"]) == ExitCode.OK
    found = response(capsys)["result"]
    assert found["total"] == 1
    assert found["items"][0]["deck_id"] == deck["deck_id"]
    assert source.read_bytes() == original

    assert main(["import", str(source), "--allow-write"]) == ExitCode.OK
    repeated = response(capsys)["result"]
    assert repeated["skipped"] == 1
    assert repeated["imported"][0]["version_id"] == deck["version_id"]

    write_pptx(source, "Updated Agent Evidence")
    assert main(["import", str(source), "--allow-write"]) == ExitCode.OK
    updated = response(capsys)["result"]["imported"][0]
    assert updated["deck_id"] == deck["deck_id"] and updated["action"] == "updated"
    assert main(["search", "--query", "Updated Agent Evidence"]) == ExitCode.OK
    assert response(capsys)["result"]["total"] == 1


@pytest.mark.parametrize("extension", [".html", ".zip"])
def test_import_html_and_zip_auto_enable_local_adapter(
    settings: Settings, render_stub: None, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    extension: str,
) -> None:
    html = '<section class="slide"><h1>Agent HTML Evidence</h1></section>'
    source = tmp_path / f"output{extension}"
    if extension == ".zip":
        with ZipFile(source, "w") as archive:
            archive.writestr("index.html", html)
    else:
        source.write_text(html, encoding="utf-8")
    original = source.read_bytes()
    assert main(["import", str(source), "--allow-write"]) == ExitCode.OK
    imported = response(capsys)["result"]["imported"][0]
    assert imported["slide_count"] == 1
    assert main(["search", "--query", "Agent HTML Evidence"]) == ExitCode.OK
    assert response(capsys)["result"]["items"][0]["source_format"] == "render_deck_html"
    assert source.read_bytes() == original


def test_import_denies_without_write_and_rejects_invalid_files_before_initialization(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "generated.pptx"
    write_pptx(source)
    assert main(["import", str(source)]) == ExitCode.INVALID_REQUEST
    response(capsys)
    assert not settings.home.exists()
    bad = tmp_path / "not-a-deck.txt"
    bad.write_text("text")
    assert main(["import", str(source), str(bad), "--allow-write"]) == ExitCode.INVALID_REQUEST
    assert response(capsys)["error"]["details"]["rejected"][0]["reason"] == "unsupported"
    assert not settings.home.exists()
    assert main(["import", str(tmp_path), "--allow-write"]) == ExitCode.INVALID_REQUEST
    response(capsys)
    link = tmp_path / "linked.pptx"
    link.symlink_to(source)
    assert main(["import", str(link), "--allow-write"]) == ExitCode.PRECONDITION_FAILED
    response(capsys)
    assert main(["import", str(tmp_path / "absent.pptx"), "--allow-write"]) == ExitCode.NOT_FOUND
    response(capsys)
    assert not settings.home.exists()


def test_render_failure_reports_searchable_pages_and_can_be_retried(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch, render_stub: None,
) -> None:
    source = tmp_path / "retry.pptx"
    write_pptx(source)
    original_render = import_decks.render_deck_thumbnails

    def fail(*_args, **_kwargs):
        raise ThumbnailError("renderer unavailable")

    monkeypatch.setattr("pptlib.application.import_decks.render_deck_thumbnails", fail)
    assert main(["import", str(source), "--allow-write"]) == ExitCode.IMPORT_INCOMPLETE
    result = response(capsys)["error"]["details"]["result"]
    assert result["status"] == "incomplete"
    assert result["imported"][0]["searchable"] and not result["imported"][0]["preview_ready"]
    assert main(["search", "--query", "Agent Import Evidence"]) == ExitCode.OK
    assert response(capsys)["result"]["total"] == 1
    assert main(["import", str(source), "--allow-write"]) == ExitCode.IMPORT_INCOMPLETE
    repeated_failure = response(capsys)["error"]["details"]["result"]
    assert repeated_failure["failed"][0]["searchable"]
    monkeypatch.setattr("pptlib.application.import_decks.render_deck_thumbnails", original_render)
    assert main(["import", str(source), "--allow-write"]) == ExitCode.OK
    assert response(capsys)["result"]["imported"][0]["action"] == "unchanged"


def test_import_batch_preserves_other_records_and_reports_parse_failures(
    settings: Settings, render_stub: None, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    good = tmp_path / "good.pptx"
    write_pptx(good)
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a package")
    assert main(["import", str(good), str(bad), "--allow-write"]) == ExitCode.IMPORT_INCOMPLETE
    result = response(capsys)["error"]["details"]["result"]
    assert len(result["imported"]) == 1 and len(result["failed"]) == 1
    assert not result["failed"][0]["searchable"]
    good.unlink()
    another = tmp_path / "another.pptx"
    write_pptx(another, "Another Agent Evidence")
    assert main(["import", str(another), "--allow-write"]) == ExitCode.OK
    response(capsys)
    with connect_readonly(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 2


def test_heavy_lock_blocks_both_public_and_private_cli_and_releases(
    settings: Settings, render_stub: None, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "concurrent.pptx"
    write_pptx(source)
    with heavy_operation_lock(settings.home):
        assert main(["import", str(source), "--allow-write"]) == ExitCode.BUSY
        assert response(capsys)["error"]["retryable"]
        assert private_main(["import", str(source)]) == 1
        assert response(capsys)["error"] == "DATABASE_BUSY"
        assert not settings.database_path.exists()
    assert main(["import", str(source), "--allow-write"]) == ExitCode.OK
    response(capsys)


def test_cancel_import_before_first_file_releases_lock(
    settings: Settings, tmp_path: Path, render_stub: None,
) -> None:
    source = tmp_path / "cancel.pptx"
    write_pptx(source)
    with pytest.raises(AppError) as error:
        import_agent_files(settings, [source], allow_write=True, should_cancel=lambda: True)
    assert error.value.code.value == "IMPORT_INCOMPLETE"
    assert error.value.details["result"]["cancelled"] == [str(source)]
    with heavy_operation_lock(settings.home):
        pass


def test_startup_recovery_waits_for_an_import_lock(
    settings: Settings, capsys: pytest.CaptureFixture[str],
) -> None:
    started = threading.Event()

    def recover() -> int:
        started.set()
        return private_main(["scan-plan", "recover"])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with heavy_operation_lock(settings.home):
            future = pool.submit(recover)
            assert started.wait(timeout=2)
            assert not future.done()
            assert not settings.database_path.exists()
        assert future.result(timeout=10) == 0
    assert response(capsys)["ok"]


def test_desktop_search_and_progress_persistence_do_not_wait_for_heavy_work(
    settings: Settings, capsys: pytest.CaptureFixture[str],
) -> None:
    initialize(settings)
    with heavy_operation_lock(settings.home):
        assert private_main(["desktop", "search"]) == 0
        assert response(capsys)["total"] == 0
        task = json.dumps({"taskId": "task-import", "kind": "import", "state": "running"})
        assert private_main(["desktop", "task-set", "--payload", task]) == 0
        assert response(capsys)["task"]["state"] == "running"


def test_cancel_during_last_file_reports_completed_pages_and_cancel_request(
    settings: Settings, render_stub: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "last.pptx"
    write_pptx(source)
    cancelled = threading.Event()
    original = import_decks.render_deck_thumbnails

    def render_then_cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        cancelled.set()
        return result

    monkeypatch.setattr(import_decks, "render_deck_thumbnails", render_then_cancel)
    with pytest.raises(AppError) as error:
        import_agent_files(
            settings, [source], allow_write=True, should_cancel=cancelled.is_set
        )
    result = error.value.details["result"]
    assert result["cancellation_requested"]
    assert result["imported"][0]["searchable"] and result["imported"][0]["preview_ready"]


def test_second_scan_dropped_file_has_a_failure_entry(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "disappears.pptx"
    write_pptx(source)
    original = agent_import.initialize

    def initialize_then_remove(import_settings):
        applied = original(import_settings)
        source.unlink()
        return applied

    monkeypatch.setattr(agent_import, "initialize", initialize_then_remove)
    with pytest.raises(AppError) as error:
        import_agent_files(settings, [source], allow_write=True)
    result = error.value.details["result"]
    assert result["discovered"] == 0
    assert result["failed"][0]["path"] == str(source)
    assert not result["failed"][0]["searchable"]


@pytest.mark.parametrize("missing_relationships", [False, True])
def test_malformed_package_does_not_hide_successful_batch_files(
    settings: Settings, render_stub: None, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    missing_relationships: bool,
) -> None:
    good = tmp_path / "a-good.pptx"
    write_pptx(good)
    bad = tmp_path / "z-malformed.pptx"
    with ZipFile(bad, "w") as archive:
        if missing_relationships:
            archive.writestr("ppt/presentation.xml", "<presentation/>")
        else:
            archive.writestr("ppt/presentation.xml", "<broken")
            archive.writestr("ppt/_rels/presentation.xml.rels", "<relationships/>")
    assert main(["import", str(good), str(bad), "--allow-write"]) == ExitCode.IMPORT_INCOMPLETE
    result = response(capsys)["error"]["details"]["result"]
    assert result["imported"][0]["path"] == str(good)
    assert result["failed"][0]["path"] == str(bad)
