from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sqlite3
import threading
import uuid
import warnings
import webbrowser
from dataclasses import replace
from pathlib import Path

import uvicorn

from pptlib.application.catalog import (
    MiaodaSync,
    MiaodaSyncError,
    build_catalog,
    write_catalog_bundle,
)
from pptlib.application.compose import compose_from_manifest, preflight_compose_from_manifest
from pptlib.application.deduplicate import find_duplicate_slides
from pptlib.application.delete import delete_decks, delete_slides
from pptlib.application.doctor import run_doctor
from pptlib.application.import_decks import scan_and_import
from pptlib.application.scan_plans import (
    current_scan_run,
    delete_scan_plan,
    list_scan_plans,
    preview_scan_plan,
    record_missed_scan_run,
    recover_interrupted_runs,
    retry_scan_run,
    run_scan_plan,
    save_scan_plan,
    scan_run_history,
    set_scan_plan_enabled,
)
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.domain.errors import AppError
from pptlib.infrastructure.db.connection import connect
from pptlib.web.app import create_app
from pptlib.worker.main import run_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pptlib")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor")
    subparsers.add_parser("init")
    import_command = subparsers.add_parser(
        "import", help="scan and index PPTX files or source directories (indexed in place)"
    )
    import_command.add_argument(
        "root", type=Path, nargs="+", help="one or more PPTX files or directories"
    )
    import_command.add_argument(
        "--html",
        action="store_true",
        help="also accept standard render-deck HTML / ZIP bundles",
    )
    scan_plan = subparsers.add_parser(
        "scan-plan", help="manage and execute desktop automatic-update plans"
    )
    scan_actions = scan_plan.add_subparsers(dest="scan_action", required=True)
    scan_actions.add_parser("list")
    scan_save = scan_actions.add_parser("save")
    scan_save.add_argument("--payload", required=True, help="JSON plan payload")
    scan_preview = scan_actions.add_parser("preview")
    scan_preview.add_argument("--payload", required=True, help="JSON plan payload")
    scan_delete = scan_actions.add_parser("delete")
    scan_delete.add_argument("plan_id")
    scan_enabled = scan_actions.add_parser("set-enabled")
    scan_enabled.add_argument("plan_id")
    scan_enabled.add_argument("enabled", choices=("0", "1"))
    scan_run = scan_actions.add_parser("run")
    scan_run.add_argument("plan_id")
    scan_run.add_argument("--root-id")
    scan_run.add_argument("--trigger", choices=("manual", "scheduled", "retry"), default="manual")
    scan_run.add_argument("--scheduled-for")
    scan_history = scan_actions.add_parser("history")
    scan_history.add_argument("--limit", type=int, default=50)
    scan_missed = scan_actions.add_parser("missed")
    scan_missed.add_argument("plan_id")
    scan_missed.add_argument("--scheduled-for")
    scan_retry = scan_actions.add_parser("retry")
    scan_retry.add_argument("run_id")
    scan_actions.add_parser("current")
    scan_actions.add_parser("recover")
    html_preview = subparsers.add_parser("html-preview", help="serve an isolated HTML page preview")
    html_preview.add_argument("slide_id")
    subparsers.add_parser("render-html", help="retry missing HTML thumbnail and preview images")
    subparsers.add_parser(
        "render-missing",
        help="retry missing thumbnail and preview images without reparsing source files",
    )
    remove = subparsers.add_parser(
        "remove",
        help="remove decks or slides from the local index (source PPTX files are never touched)",
    )
    remove.add_argument(
        "--deck", action="append", default=[], help="deck_id to remove (repeatable)"
    )
    remove.add_argument(
        "--slide", action="append", default=[], help="slide_id to remove (repeatable)"
    )
    duplicates = subparsers.add_parser(
        "duplicates",
        help="find exact and near-duplicate slides in the local library",
    )
    duplicates.add_argument(
        "--refresh",
        action="store_true",
        help="recompute every page fingerprint instead of using cached values",
    )
    compose = subparsers.add_parser(
        "compose", help="compose a PPTX from a Miaoda selection manifest.json"
    )
    compose.add_argument("manifest", type=Path, help="path to the selection manifest.json")
    compose.add_argument("output", type=Path, help="output .pptx path")
    compose.add_argument(
        "--no-verify-hash",
        action="store_true",
        help="skip source SHA-256 verification (allows composing after a source file changed)",
    )
    compose.add_argument(
        "--preflight-token",
        help="bind export to a previously validated manifest and output state",
    )
    compose_preflight = subparsers.add_parser(
        "compose-preflight", help="validate a selection before writing a PPTX"
    )
    compose_preflight.add_argument("manifest", type=Path, help="path to selection manifest.json")
    compose_preflight.add_argument("output", type=Path, help="planned output .pptx path")
    compose_preflight.add_argument(
        "--no-verify-hash",
        action="store_true",
        help="skip source SHA-256 verification",
    )
    catalog = subparsers.add_parser(
        "catalog", help="export the local slide catalog as catalog.json"
    )
    catalog.add_argument("output_dir", type=Path, help="directory to write catalog.json into")
    catalog.add_argument(
        "--include-local-fields",
        action="store_true",
        help="include absolute source paths and full slide text for the desktop client",
    )
    sync = subparsers.add_parser(
        "sync", help="publish thumbnails + metadata to a Miaoda app via lark-cli"
    )
    sync.add_argument("--app-id", required=True, help="Miaoda app id (app_...)")
    sync.add_argument(
        "--environment", default="online", choices=["dev", "online"], help="target db environment"
    )
    sync.add_argument("--lark-cli", default="lark-cli", help="path to the lark-cli binary")
    sync.add_argument(
        "--dry-run", action="store_true", help="print lark-cli commands without executing"
    )
    serve = subparsers.add_parser("serve")
    serve.add_argument("--no-open", action="store_true")
    worker = subparsers.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    return parser


def _available_port(host: str, preferred: int) -> int:
    with socket.socket() as probe:
        try:
            probe.bind((host, preferred))
            return preferred
        except OSError:
            probe.bind((host, 0))
            return int(probe.getsockname()[1])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    if args.command == "doctor":
        print(json.dumps(run_doctor(settings).to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "init":
        applied = initialize(settings)
        print(json.dumps({"applied_migrations": applied}, ensure_ascii=False))
        return 0
    if args.command == "scan-plan":
        initialize(settings)

        def _emit_scan_progress(event: dict[str, object]) -> None:
            import sys

            sys.stdout.write("@@PPTLIB_PROGRESS " + json.dumps(event, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        try:
            if args.scan_action == "list":
                result = list_scan_plans(settings)
            elif args.scan_action == "save":
                result = save_scan_plan(settings, json.loads(args.payload))
            elif args.scan_action == "preview":
                result = preview_scan_plan(settings, json.loads(args.payload))
            elif args.scan_action == "delete":
                result = delete_scan_plan(settings, args.plan_id)
            elif args.scan_action == "set-enabled":
                result = set_scan_plan_enabled(settings, args.plan_id, enabled=args.enabled == "1")
            elif args.scan_action == "history":
                result = scan_run_history(settings, limit=args.limit)
            elif args.scan_action == "missed":
                result = record_missed_scan_run(
                    settings,
                    args.plan_id,
                    scheduled_for=args.scheduled_for,
                )
            elif args.scan_action == "current":
                result = current_scan_run(settings)
            elif args.scan_action == "recover":
                result = {"ok": True, "recovered": recover_interrupted_runs(settings)}
            elif args.scan_action == "retry":
                cancelled = threading.Event()
                signal.signal(signal.SIGTERM, lambda *_args: cancelled.set())
                signal.signal(signal.SIGINT, lambda *_args: cancelled.set())
                result = retry_scan_run(
                    settings,
                    args.run_id,
                    on_progress=_emit_scan_progress,
                    should_cancel=cancelled.is_set,
                )
            elif args.scan_action == "run":
                cancelled = threading.Event()
                signal.signal(signal.SIGTERM, lambda *_args: cancelled.set())
                signal.signal(signal.SIGINT, lambda *_args: cancelled.set())
                result = run_scan_plan(
                    settings,
                    args.plan_id,
                    root_id=args.root_id,
                    trigger=args.trigger,
                    scheduled_for=args.scheduled_for,
                    on_progress=_emit_scan_progress,
                    should_cancel=cancelled.is_set,
                )
            else:
                raise AssertionError("unreachable scan-plan action")
        except (ValueError, OSError, sqlite3.Error, AppError, json.JSONDecodeError) as error:
            print(
                json.dumps(
                    {"ok": False, "message": str(error)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 1
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        return 0
    if args.command == "import":
        if args.html:
            settings = replace(settings, html_enabled=True)
        initialize(settings)
        roots = [p.expanduser().resolve() for p in args.root]
        missing = [str(p) for p in roots if not p.exists()]
        if missing:
            raise SystemExit("source path not found: " + ", ".join(missing))

        def _emit_progress(event: dict[str, object]) -> None:
            # A single-line, prefixed marker on stdout. The desktop client peels
            # these off the stream to drive the progress UI; the final result
            # JSON below is unaffected because it is multi-line (indent=2).
            import sys

            sys.stdout.write("@@PPTLIB_PROGRESS " + json.dumps(event, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        connection = connect(settings.database_path)
        try:
            report = scan_and_import(
                connection, roots, settings=settings, on_progress=_emit_progress
            )
        finally:
            connection.close()
        print(
            json.dumps(
                {
                    "discovered": report.discovered,
                    "imported": [
                        {
                            "deck_id": item.deck_id,
                            "version_id": item.version_id,
                            "path": str(item.path),
                            "name": item.path.name,
                            "slide_count": item.slide_count,
                            "created": item.created,
                            "action": item.action,
                        }
                        for item in report.imported
                    ],
                    "skipped": report.skipped,
                    "removed": [str(path) for path in report.removed],
                    "failed": [
                        {"path": str(path), "error": error} for path, error in report.failed
                    ],
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    if args.command in {"html-preview", "render-html"}:
        initialize(settings)
        try:
            if args.command == "render-html":
                from pptlib.application.html_assets import retry_html_previews

                preview_result = retry_html_previews(settings)
                print(json.dumps(preview_result, ensure_ascii=False), flush=True)
                return 0 if preview_result["ok"] else 1
            from pptlib.application.html_assets import preview_source
            from pptlib.html.preview import PreviewWarning, start_preview

            source, metadata = preview_source(settings, args.slide_id)
            stopped = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_args: stopped.set())
            signal.signal(signal.SIGINT, lambda *_args: stopped.set())
            parent_pid = os.getppid()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", PreviewWarning)
                with start_preview(source, metadata["page_key"]) as url:
                    print(
                        json.dumps({"ok": True, "url": url, **metadata}, ensure_ascii=False),
                        flush=True,
                    )
                    while not stopped.wait(1):
                        if os.getppid() != parent_pid:
                            break
            return 0
        except (AppError, OSError, ValueError) as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": error.code.value if isinstance(error, AppError) else "HTML_ERROR",
                        "message": error.message if isinstance(error, AppError) else str(error),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 1
    if args.command == "render-missing":
        from pptlib.application.render_assets import backfill_thumbnails

        initialize(settings)

        def _emit_render_progress(event: dict[str, object]) -> None:
            import sys

            sys.stdout.write("@@PPTLIB_PROGRESS " + json.dumps(event, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        repair = backfill_thumbnails(settings, on_progress=_emit_render_progress)
        print(json.dumps(repair.to_dict(), ensure_ascii=False), flush=True)
        return 0 if not repair.failed else 1
    if args.command == "remove":
        initialize(settings)
        if not args.deck and not args.slide:
            raise SystemExit("remove requires at least one --deck or --slide")
        try:
            deck_result = delete_decks(settings, args.deck) if args.deck else None
            slide_result = delete_slides(settings, args.slide) if args.slide else None
        except AppError as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": error.code.value,
                        "message": error.message,
                        "details": dict(error.details),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        decks_removed = deck_result.decks_removed if deck_result else 0
        slides_removed = (deck_result.slides_removed if deck_result else 0) + (
            slide_result.slides_removed if slide_result else 0
        )
        thumbnails_removed = (deck_result.thumbnails_removed if deck_result else 0) + (
            slide_result.thumbnails_removed if slide_result else 0
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "decks_removed": decks_removed,
                    "slides_removed": slides_removed,
                    "thumbnails_removed": thumbnails_removed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "duplicates":
        initialize(settings)
        duplicate_report = find_duplicate_slides(settings, refresh=args.refresh)
        print(json.dumps(duplicate_report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "compose-preflight":
        initialize(settings)
        preflight_result = preflight_compose_from_manifest(
            settings,
            args.manifest.expanduser().resolve(),
            args.output.expanduser().resolve(),
            verify_source_hash=not args.no_verify_hash,
        )
        print(json.dumps(preflight_result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "compose":
        initialize(settings)
        manifest = args.manifest.expanduser().resolve()
        if not manifest.is_file():
            raise SystemExit(f"manifest not found: {manifest}")
        output = args.output.expanduser().resolve()
        try:
            compose_result = compose_from_manifest(
                settings,
                manifest,
                output,
                verify_source_hash=not args.no_verify_hash,
                preflight_token=args.preflight_token,
            )
        except AppError as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": error.code.value,
                        "message": error.message,
                        "details": dict(error.details),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "export_id": compose_result.export_id,
                    "output_path": str(compose_result.output_path),
                    "manifest_path": str(compose_result.manifest_path),
                    "page_count": compose_result.page_count,
                    "fidelity_level": compose_result.fidelity_level,
                    "warnings": list(compose_result.warnings),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "catalog":
        initialize(settings)
        output_dir = args.output_dir.expanduser().resolve()
        catalog_path = write_catalog_bundle(
            settings,
            output_dir,
            include_local_fields=args.include_local_fields,
        )
        catalog = build_catalog(settings)
        print(
            json.dumps(
                {
                    "ok": True,
                    "catalog_path": str(catalog_path),
                    "slide_count": catalog.slide_count,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "sync":
        initialize(settings)
        syncer = MiaodaSync(
            args.app_id,
            lark_cli=args.lark_cli,
            environment=args.environment,
            dry_run=args.dry_run,
        )
        try:
            sync_result = syncer.sync(settings)
        except (AppError, MiaodaSyncError) as error:
            print(json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False, indent=2))
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "app_id": sync_result.app_id,
                    "dry_run": sync_result.dry_run,
                    "slide_count": sync_result.slide_count,
                    "thumbnails_uploaded": sync_result.thumbnails_uploaded,
                    "rows_upserted": sync_result.rows_upserted,
                    "commands": sync_result.commands if sync_result.dry_run else [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "worker":
        if not args.once:
            raise SystemExit("M0.0 worker requires --once")
        initialize(settings)
        run_once(settings, worker_id=f"worker-{uuid.uuid4().hex[:8]}")
        return 0
    if args.command == "serve":
        initialize(settings)
        port = _available_port(settings.host, settings.port)
        if not args.no_open:
            webbrowser.open(f"http://{settings.host}:{port}")
        uvicorn.run(create_app(settings), host=settings.host, port=port)
        return 0
    raise AssertionError("unreachable command")
