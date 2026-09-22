from __future__ import annotations

import argparse
import json
import socket
import uuid
import webbrowser
from pathlib import Path

import uvicorn

from pptlib.application.catalog import (
    MiaodaSync,
    MiaodaSyncError,
    build_catalog,
    write_catalog_bundle,
)
from pptlib.application.compose import compose_from_manifest
from pptlib.application.doctor import run_doctor
from pptlib.application.import_decks import scan_and_import
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
    catalog = subparsers.add_parser(
        "catalog", help="export the local slide catalog as catalog.json"
    )
    catalog.add_argument("output_dir", type=Path, help="directory to write catalog.json into")
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
    if args.command == "import":
        initialize(settings)
        roots = [p.expanduser().resolve() for p in args.root]
        missing = [str(p) for p in roots if not p.exists()]
        if missing:
            raise SystemExit("source path not found: " + ", ".join(missing))
        connection = connect(settings.database_path)
        try:
            report = scan_and_import(connection, roots, settings=settings)
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
                            "slide_count": item.slide_count,
                            "created": item.created,
                        }
                        for item in report.imported
                    ],
                    "skipped": report.skipped,
                    "failed": [
                        {"path": str(path), "error": error} for path, error in report.failed
                    ],
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    if args.command == "compose":
        initialize(settings)
        manifest = args.manifest.expanduser().resolve()
        if not manifest.is_file():
            raise SystemExit(f"manifest not found: {manifest}")
        output = args.output.expanduser().resolve()
        try:
            result = compose_from_manifest(
                settings,
                manifest,
                output,
                verify_source_hash=not args.no_verify_hash,
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
                    "export_id": result.export_id,
                    "output_path": str(result.output_path),
                    "manifest_path": str(result.manifest_path),
                    "page_count": result.page_count,
                    "fidelity_level": result.fidelity_level,
                    "warnings": list(result.warnings),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "catalog":
        initialize(settings)
        output_dir = args.output_dir.expanduser().resolve()
        catalog_path = write_catalog_bundle(settings, output_dir)
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
            print(
                json.dumps(
                    {"ok": False, "message": str(error)}, ensure_ascii=False, indent=2
                )
            )
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
