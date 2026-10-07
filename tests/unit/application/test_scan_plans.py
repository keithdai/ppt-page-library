from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from pptlib.application import import_decks, scan_plans
from pptlib.application.import_decks import ImportReport
from pptlib.application.scan_plans import (
    delete_scan_plan,
    list_scan_plans,
    preview_scan_plan,
    preview_token,
    record_missed_scan_run,
    retry_scan_run,
    run_scan_plan,
    save_scan_plan,
    scan_run_history,
)
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.repositories import ImportedDeck
from pptlib.rendering import thumbnails

_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _write_pptx(path: Path, title: str) -> None:
    presentation = (
        f"<p:presentation xmlns:p='{_NS_P}' xmlns:r='{_NS_R}'>"
        "<p:sldIdLst><p:sldId id='1' r:id='rId1'/></p:sldIdLst></p:presentation>"
    )
    relationships = (
        f"<Relationships xmlns='{_NS_REL}'><Relationship Id='rId1' "
        f"Type='{_NS_R}/slide' Target='slides/slide1.xml'/></Relationships>"
    )
    slide = (
        f"<p:sld xmlns:p='{_NS_P}' xmlns:a='{_NS_A}'><p:cSld><p:spTree><p:sp>"
        "<p:nvSpPr><p:nvPr><p:ph type='title'/></p:nvPr></p:nvSpPr>"
        f"<p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody>"
        "</p:sp></p:spTree></p:cSld></p:sld>"
    )
    with ZipFile(path, "w") as package:
        package.writestr("ppt/presentation.xml", presentation)
        package.writestr("ppt/_rels/presentation.xml.rels", relationships)
        package.writestr("ppt/slides/slide1.xml", slide)


def _stub_render(monkeypatch) -> None:
    def fake_render(_source, version_id, slide_count, **kwargs):
        assets = Path(kwargs["assets_dir"])
        for page in range(1, slide_count + 1):
            for kind in ("thumbnails", "previews"):
                target = assets / kind / f"{version_id}_s{page:05d}.jpg"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"jpeg")
        thumbnails._write_render_marker(assets, version_id, "libreoffice")
        return slide_count

    monkeypatch.setattr(import_decks, "render_deck_thumbnails", fake_render)


def _payload(root: Path) -> dict[str, object]:
    return {
        "name": "核心素材库",
        "enabled": True,
        "scheduleKind": "daily",
        "scheduleTime": "02:30",
        "windowStart": "02:00",
        "windowEnd": "05:00",
        "formats": ["pptx"],
        "minFileBytes": 2,
        "maxFileBytes": 10,
        "stabilitySeconds": 0,
        "missingPolicy": "keep",
        "repairPreviews": True,
        "roots": [
            {
                "name": "核心素材",
                "path": str(root),
                "recursive": True,
            }
        ],
    }


def test_preview_and_crud_preserve_multi_root_rules(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "deck.pptx").write_bytes(b"deck")
    (first / "too-large.pptx").write_bytes(b"x" * 20)
    (first / "notes.txt").write_text("notes", encoding="utf-8")
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    payload = _payload(first)
    payload["roots"].append({"name": "客户案例", "path": str(second), "recursive": False})

    preview = preview_scan_plan(settings, payload)

    assert preview["ok"] is True
    assert preview["counts"]["created"] == 1
    assert preview["counts"]["above_maximum"] == 1
    assert preview["counts"]["unsupported"] == 1
    payload["previewToken"] = preview["previewToken"]
    saved = save_scan_plan(settings, payload)["plan"]
    assert saved["name"] == "核心素材库"
    assert len(saved["roots"]) == 2
    assert saved["missingPolicy"] == "keep"
    assert list_scan_plans(settings)["plans"][0]["id"] == saved["id"]
    assert delete_scan_plan(settings, saved["id"]) == {"ok": True, "deleted": True}
    assert list_scan_plans(settings)["plans"] == []


def test_save_rejects_stale_preview_and_overlapping_roots(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    payload = _payload(root)
    payload["previewToken"] = preview_token(payload)
    payload["name"] = "配置已改"

    with pytest.raises(ValueError, match="重新预检"):
        save_scan_plan(settings, payload)

    payload = _payload(root)
    payload["roots"].append({"name": "重叠目录", "path": str(nested), "recursive": True})
    with pytest.raises(ValueError, match="范围重叠"):
        preview_scan_plan(settings, payload)


def test_missing_root_is_a_preview_blocker(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    preview = preview_scan_plan(settings, _payload(tmp_path / "missing"))

    assert preview["ok"] is False
    assert preview["blockers"]
    assert preview["roots"][0]["root"]["authorizationStatus"] == "missing"


def test_schedule_time_must_be_inside_window(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    payload = _payload(root)
    payload["scheduleTime"] = "10:00"

    with pytest.raises(ValueError, match="执行窗口"):
        preview_scan_plan(settings, payload)


def test_missing_root_pauses_plan_during_run(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    payload = _payload(root)
    payload["previewToken"] = preview_token(payload)
    plan = save_scan_plan(settings, payload)["plan"]
    root.rmdir()

    result = run_scan_plan(settings, plan["id"])
    refreshed = list_scan_plans(settings)["plans"][0]

    assert result["status"] == "failed"
    assert refreshed["enabled"] is False
    assert refreshed["roots"][0]["authorizationStatus"] == "missing"


def test_run_persists_file_results_and_history(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "deck.pptx"
    source.write_bytes(b"deck")
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    payload = _payload(root)
    payload["previewToken"] = preview_token(payload)
    plan = save_scan_plan(settings, payload)["plan"]

    captured = {}

    def fake_import(*_args, **kwargs):
        captured.update(kwargs)
        return ImportReport(
            discovered=1,
            imported=(
                ImportedDeck(
                    "deck_id",
                    "version_id",
                    source.resolve(),
                    3,
                    True,
                    "created",
                ),
            ),
            skipped=0,
            failed=(),
        )

    monkeypatch.setattr(scan_plans, "scan_and_import", fake_import)
    result = run_scan_plan(settings, plan["id"])
    history = scan_run_history(settings)["runs"]

    assert result["status"] == "completed"
    assert result["stats"]["created"] == 1
    assert history[0]["id"] == result["runId"]
    assert history[0]["items"][0]["path"] == str(source.resolve())
    assert history[0]["items"][0]["action"] == "created"
    assert captured["missing_policy"].value == "keep"
    assert captured["repair_existing_assets"] is True


def test_missed_run_uses_scheduled_occurrence_for_deduplication(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    payload = _payload(root)
    payload["previewToken"] = preview_token(payload)
    plan = save_scan_plan(settings, payload)["plan"]

    result = record_missed_scan_run(
        settings,
        plan["id"],
        scheduled_for="2026-10-05T02:30:00+08:00",
    )
    run = scan_run_history(settings)["runs"][0]

    assert result["status"] == "missed"
    assert run["status"] == "missed"
    assert run["startedAt"] == "2026-10-05T02:30:00+08:00"
    assert run["scheduledFor"] == "2026-10-05T02:30:00+08:00"
    assert "白天补跑" in run["errorMessage"]


def test_retry_scan_run_only_passes_failed_paths(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    failed_source = root / "failed.pptx"
    successful_source = root / "successful.pptx"
    failed_source.write_bytes(b"failed")
    successful_source.write_bytes(b"successful")
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    payload = _payload(root)
    payload["previewToken"] = preview_token(payload)
    plan = save_scan_plan(settings, payload)["plan"]
    original_run = "scan_run_original"
    connection = connect(settings.database_path)
    try:
        connection.execute(
            """
            INSERT INTO scan_runs(
                id, plan_id, plan_name, trigger_type, scope_type, status,
                rules_json, stats_json, started_at, finished_at
            ) VALUES (?, ?, ?, 'manual', 'plan', 'partial', '{}', '{}', 'then', 'then')
            """,
            (original_run, plan["id"], plan["name"]),
        )
        for decision, source in (("failed", failed_source), ("accepted", successful_source)):
            connection.execute(
                """
                INSERT INTO scan_run_items(
                    id, run_id, root_id, path, decision, action, reason,
                    size_bytes, slide_count, elapsed_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', 1, 0, 0, 'then')
                """,
                (
                    f"item_{decision}",
                    original_run,
                    plan["roots"][0]["id"],
                    str(source),
                    decision,
                    "failed" if decision == "failed" else "created",
                ),
            )
    finally:
        connection.close()
    captured_roots = []

    def fake_import(_connection, roots, **_kwargs):
        captured_roots.extend(roots)
        return ImportReport(
            discovered=1,
            imported=(
                ImportedDeck(
                    "deck_id",
                    "version_id",
                    failed_source.resolve(),
                    1,
                    True,
                    "created",
                ),
            ),
            skipped=0,
            failed=(),
        )

    monkeypatch.setattr(scan_plans, "scan_and_import", fake_import)
    result = retry_scan_run(settings, original_run)

    assert result["status"] == "completed"
    assert captured_roots == [failed_source]


def test_remove_policy_preserves_identity_for_moves_between_plan_roots(
    monkeypatch, tmp_path: Path
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    old_path = first / "deck.pptx"
    new_path = second / "deck.pptx"
    _write_pptx(old_path, "跨目录移动")
    _stub_render(monkeypatch)
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    payload = _payload(first)
    payload["maxFileBytes"] = 1024 * 1024
    payload["missingPolicy"] = "remove"
    payload["roots"].append({"name": "第二目录", "path": str(second), "recursive": True})
    payload["previewToken"] = preview_token(payload)
    plan = save_scan_plan(settings, payload)["plan"]

    first_run = run_scan_plan(settings, plan["id"])
    old_path.rename(new_path)
    second_run = run_scan_plan(settings, plan["id"])
    connection = connect(settings.database_path)
    try:
        rows = connection.execute("SELECT id, canonical_path FROM decks").fetchall()
    finally:
        connection.close()

    assert first_run["status"] == "completed"
    assert second_run["status"] == "completed"
    assert second_run["stats"]["moved"] == 1
    assert len(rows) == 1
    assert rows[0]["canonical_path"] == str(new_path.resolve())


def test_preview_hash_failure_marks_run_failed(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "deck.pptx").write_bytes(b"deck")
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    initialize(settings)
    payload = _payload(root)
    payload["previewToken"] = preview_token(payload)
    plan = save_scan_plan(settings, payload)["plan"]

    def fail_hash(*_args, **_kwargs):
        raise OSError("cannot read source")

    monkeypatch.setattr(scan_plans, "hash_discovered_file", fail_hash)
    monkeypatch.setattr(
        scan_plans,
        "scan_and_import",
        lambda *_args, **_kwargs: ImportReport(0, (), 0, ()),
    )
    result = run_scan_plan(settings, plan["id"])
    run = scan_run_history(settings)["runs"][0]

    assert result["status"] == "failed"
    assert result["stats"]["failed"] == 1
    assert run["items"][0]["decision"] == "failed"
