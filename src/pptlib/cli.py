from __future__ import annotations

import argparse
import json
import socket
import uuid
import webbrowser
from pathlib import Path

import uvicorn

from pptlib.application.doctor import run_doctor
from pptlib.application.import_decks import scan_and_import
from pptlib.bootstrap import initialize
from pptlib.config import load_settings
from pptlib.infrastructure.db.connection import connect
from pptlib.web.app import create_app
from pptlib.worker.main import run_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pptlib")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor")
    subparsers.add_parser("init")
    import_command = subparsers.add_parser("import", help="scan and index a PPTX source directory")
    import_command.add_argument("root", type=Path)
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
        root = args.root.expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"source directory not found: {root}")
        connection = connect(settings.database_path)
        try:
            report = scan_and_import(connection, [root], settings=settings)
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
