from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
import zipfile
from pathlib import Path

from pptlib.agent_cli import ExitCode, main
from pptlib.application.agent import AGENT_PROTOCOL, SELECTION_SCHEMA
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect


def _make_pptx_fixture(path: Path) -> None:
    parts = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
</Types>""",
        "_rels/.rels": """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
        "ppt/presentation.xml": """<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldMasterIdLst><p:sldMasterId id="1" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>
  <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>""",
        "ppt/_rels/presentation.xml.rels": """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>""",
        "ppt/slides/slide1.xml": """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>""",
        "ppt/slides/_rels/slide1.xml.rels": """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>""",
        "ppt/slideLayouts/slideLayout1.xml": """<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>""",
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>""",
        "ppt/slideMasters/slideMaster1.xml": """<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:clrMap accent1="accent1" accent2="accent2" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/></p:sldMaster>""",
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>""",
        "ppt/theme/theme1.xml": """<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="fixture"/>""",
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)


def _configure(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    output = tmp_path / "exports"
    monkeypatch.setenv("PPTLIB_HOME", str(home))
    monkeypatch.setenv("PPTLIB_OUTPUT_ROOT", str(output))
    monkeypatch.setenv("PPTLIB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PPTLIB_TEMP_DIR", str(tmp_path / "tmp"))
    return load_settings()


def _insert_slide(settings, source_path: Path | None = None) -> str:
    slide_id = "ver_agent_s00001"
    source = source_path or Path("/tmp/agent-source.pptx")
    source_sha256 = (
        hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else "sha"
    )
    with connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO decks(
                id, canonical_path, display_name, current_version_id, created_at, updated_at
            ) VALUES (
                'deck_agent', ?, 'AI Agent Deck',
                'ver_agent', datetime('now'), datetime('now')
            )
            """,
            (str(source),),
        )
        connection.execute(
            """
            INSERT INTO deck_versions(
                id, deck_id, sha256, size_bytes, mtime_ns, parser_version,
                slide_count, status, created_at, updated_at
            ) VALUES (
                'ver_agent', 'deck_agent', ?, 1, 1, 'test',
                1, 'parsed', datetime('now'), datetime('now')
            )
            """,
            (source_sha256,),
        )
        connection.execute(
            """
            INSERT INTO slides(
                id, deck_version_id, slide_number, title, body_text, notes_text,
                content_text, created_at
            ) VALUES (
                ?, 'ver_agent', 1, 'AI Native 组织', '协作即沉淀',
                'Context over Control', '协作即沉淀 Context over Control', datetime('now')
            )
            """,
            (slide_id,),
        )
        connection.execute(
            """
            INSERT INTO slide_fts(slide_id, title, body_text, notes_text)
            VALUES (?, 'AI Native 组织', '协作即沉淀 作即 即沉 沉淀', 'Context over Control')
            """,
            (slide_id,),
        )
        connection.execute(
            """
            INSERT INTO slide_taxonomy(
                slide_id, topic, subtopic, page_type, confidence,
                classification_source, classifier_version, classified_at
            ) VALUES (
                ?, '组织与人才', '组织发展', '观点与结论',
                'high', 'auto', 'test', datetime('now')
            )
            """,
            (slide_id,),
        )
    preview = settings.assets_dir / "previews" / f"{slide_id}.jpg"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"preview")
    return slide_id


def _response(capsys) -> dict[str, object]:
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    return json.loads(captured.out)


def test_capabilities_and_parse_errors_use_one_json_response(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _configure(monkeypatch, tmp_path)

    assert main(["--request-id", "req-test", "capabilities"]) == ExitCode.OK
    capabilities = _response(capsys)
    assert capabilities["protocol"] == AGENT_PROTOCOL
    assert capabilities["id"] == "req-test"
    assert capabilities["result"]["permissions"]["default"] == "read_only"
    slide_ids_schema = capabilities["result"]["selection_schema"]["properties"]["slide_ids"]
    assert slide_ids_schema["minItems"] == 1
    assert slide_ids_schema["maxItems"] == 2_000

    assert main(["--help"]) == ExitCode.OK
    help_response = _response(capsys)
    assert help_response["command"] == "help"
    assert "usage: deckatlas" in help_response["result"]["help"]

    assert main(["unknown"]) == ExitCode.INVALID_REQUEST
    error = _response(capsys)
    assert error["ok"] is False
    assert error["error"]["code"] == "REQUEST_INVALID"

    assert main(["--request-id=req-bad", "slide", "get"]) == ExitCode.INVALID_REQUEST
    error = _response(capsys)
    assert error["id"] == "req-bad"
    assert error["command"] == "slide.get"

    assert main(["--request-id=", "capabilities"]) == ExitCode.INVALID_REQUEST
    error = _response(capsys)
    assert error["error"]["code"] == "REQUEST_INVALID"


def test_search_and_slide_get_return_agent_ready_paths(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = _configure(monkeypatch, tmp_path)
    initialize(settings)
    slide_id = _insert_slide(settings)

    assert main(["search", "--query", "AI Native"]) == ExitCode.OK
    search = _response(capsys)["result"]
    assert search["total"] == 1
    assert search["items"][0]["slide_id"] == slide_id
    assert search["items"][0]["can_compose"] is True
    assert search["items"][0]["preview_path"].endswith(f"{slide_id}.jpg")

    revision = search["library_revision"]
    assert (
        main(["search", "--query", "AI Native", "--expected-revision", revision])
        == ExitCode.OK
    )
    _response(capsys)

    assert main(["slide", "get", slide_id]) == ExitCode.OK
    slide = _response(capsys)["result"]
    assert slide["title"] == "AI Native 组织"
    assert slide["text"] == "协作即沉淀\nContext over Control"

    assert main(["search", "--page", "1000001"]) == ExitCode.INVALID_REQUEST
    error = _response(capsys)
    assert error["error"]["code"] == "REQUEST_INVALID"


def test_search_does_not_create_a_missing_library(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = _configure(monkeypatch, tmp_path)

    assert main(["search"]) == ExitCode.NOT_FOUND
    response = _response(capsys)

    assert response["error"]["code"] == "NOT_FOUND"
    assert not settings.database_path.exists()


def test_compose_preflight_is_read_only_and_run_requires_explicit_write(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = _configure(monkeypatch, tmp_path)
    initialize(settings)
    manifest = tmp_path / "selection.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": SELECTION_SCHEMA,
                "slide_ids": ["missing_slide"],
            }
        ),
        encoding="utf-8",
    )
    output = settings.output_root / "agent-deck.pptx"

    assert main(["compose", "preflight", str(manifest), str(output)]) == ExitCode.OK
    preflight = _response(capsys)["result"]
    assert preflight["ready"] is False
    assert preflight["preflight_token"] is None
    assert not output.exists()

    assert (
        main(
            [
                "compose",
                "run",
                str(manifest),
                str(output),
                "--preflight-token",
                "invalid",
            ]
        )
        == ExitCode.INVALID_REQUEST
    )
    error = _response(capsys)
    assert error["error"]["code"] == "REQUEST_INVALID"
    assert not output.exists()


def test_compose_preflight_token_allows_one_controlled_export(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = _configure(monkeypatch, tmp_path)
    initialize(settings)
    source = tmp_path / "source.pptx"
    _make_pptx_fixture(source)
    slide_id = _insert_slide(settings, source)
    manifest = tmp_path / "selection.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": SELECTION_SCHEMA,
                "slide_ids": [slide_id],
            }
        ),
        encoding="utf-8",
    )
    output = settings.output_root / "agent-deck.pptx"

    assert main(["compose", "preflight", str(manifest), str(output)]) == ExitCode.OK
    preflight = _response(capsys)["result"]
    assert preflight["ready"] is True

    assert (
        main(
            [
                "compose",
                "run",
                str(manifest),
                str(output),
                "--preflight-token",
                preflight["preflight_token"],
                "--allow-write",
            ]
        )
        == ExitCode.OK
    )
    result = _response(capsys)["result"]
    assert result["output_path"] == str(output)
    assert result["page_count"] == 1
    assert output.is_file()
    assert output.with_suffix(".manifest.json").is_file()
