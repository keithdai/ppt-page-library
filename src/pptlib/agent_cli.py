from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
import threading
import uuid
from collections.abc import Sequence
from contextlib import redirect_stdout
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from pptlib.application.agent import (
    AGENT_PROTOCOL,
    describe_capabilities,
    get_slide,
    preflight_agent_compose,
    run_agent_compose,
    search_library,
)
from pptlib.application.agent_import import import_agent_files
from pptlib.application.library import LibraryFilters
from pptlib.config import load_settings
from pptlib.domain.errors import AppError, ErrorCode

if TYPE_CHECKING:
    from _typeshed import SupportsWrite


class ExitCode(IntEnum):
    OK = 0
    INVALID_REQUEST = 2
    NOT_FOUND = 3
    PRECONDITION_FAILED = 4
    BUSY = 5
    CAPABILITY_UNAVAILABLE = 6
    IMPORT_INCOMPLETE = 7
    INTERNAL_ERROR = 10


class UsageError(ValueError):
    pass


class HelpRequested(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


class AgentArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)

    def print_help(self, file: SupportsWrite[str] | None = None) -> None:
        raise HelpRequested(self.format_help())


def build_parser() -> argparse.ArgumentParser:
    parser = AgentArgumentParser(
        prog="deckatlas",
        description="Stable local CLI for AI agents using a DeckAtlas slide library.",
    )
    parser.add_argument("--request-id", help="caller-provided request identifier")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", help="describe commands, permissions, and schemas")
    import_command = commands.add_parser(
        "import", help="index local PPTX/HTML/ZIP files in place and generate previews"
    )
    import_command.add_argument("paths", type=Path, nargs="+")
    import_command.add_argument("--allow-write", action="store_true", required=True)

    search = commands.add_parser("search", help="search the local slide library")
    search.add_argument("--query", default="")
    search.add_argument("--page", type=int, default=1)
    search.add_argument("--page-size", type=int, default=20)
    search.add_argument("--expected-revision")
    search.add_argument("--topic", default="")
    search.add_argument("--subtopic", default="")
    search.add_argument("--page-type", default="")
    search.add_argument("--deck-id", default="")
    search.add_argument("--source-format", default="")

    slide = commands.add_parser("slide", help="read one slide")
    slide_actions = slide.add_subparsers(dest="slide_action", required=True)
    slide_get = slide_actions.add_parser("get")
    slide_get.add_argument("slide_id")

    compose = commands.add_parser("compose", help="preflight or export an ordered selection")
    compose_actions = compose.add_subparsers(dest="compose_action", required=True)
    compose_preflight = compose_actions.add_parser("preflight")
    compose_preflight.add_argument("manifest", type=Path)
    compose_preflight.add_argument("output", type=Path)
    compose_run = compose_actions.add_parser("run")
    compose_run.add_argument("manifest", type=Path)
    compose_run.add_argument("output", type=Path)
    compose_run.add_argument("--preflight-token", required=True)
    compose_run.add_argument("--allow-write", action="store_true", required=True)
    compose_run.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    request_id = _request_id_from(raw_argv)
    command = _command_from(raw_argv)
    try:
        args = build_parser().parse_args(raw_argv)
        supplied_request_id = args.request_id if args.request_id is not None else request_id
        request_id = _validate_request_id(supplied_request_id)
        command, result = _dispatch(args)
    except HelpRequested as help_request:
        command = "help"
        result = {"help": help_request.text}
    except UsageError as error:
        return _emit_error(
            request_id,
            command,
            ErrorCode.REQUEST_INVALID.value,
            str(error),
            ExitCode.INVALID_REQUEST,
        )
    except AppError as error:
        return _emit_error(
            request_id,
            command,
            error.code.value,
            error.message,
            _app_error_exit_code(error.code),
            retryable=error.retryable,
            details=dict(error.details),
        )
    except FileNotFoundError:
        return _emit_error(
            request_id,
            command,
            ErrorCode.NOT_FOUND.value,
            "required local file was not found",
            ExitCode.NOT_FOUND,
        )
    except sqlite3.Error as error:
        retryable = "locked" in str(error).casefold() or "busy" in str(error).casefold()
        code = ErrorCode.DATABASE_BUSY if retryable else ErrorCode.INTERNAL_ERROR
        return _emit_error(
            request_id,
            command,
            code.value,
            "DeckAtlas database is busy" if retryable else "DeckAtlas database error",
            ExitCode.BUSY if retryable else ExitCode.INTERNAL_ERROR,
            retryable=retryable,
        )
    except (OSError, ValueError) as error:
        return _emit_error(
            request_id,
            command,
            ErrorCode.REQUEST_INVALID.value,
            str(error),
            ExitCode.INVALID_REQUEST,
        )
    except Exception:
        return _emit_error(
            request_id,
            command,
            ErrorCode.INTERNAL_ERROR.value,
            "unexpected internal error",
            ExitCode.INTERNAL_ERROR,
        )
    _emit(
        {
            "protocol": AGENT_PROTOCOL,
            "type": "response",
            "id": request_id,
            "command": command,
            "ok": True,
            "result": result,
        }
    )
    return ExitCode.OK


def _dispatch(args: argparse.Namespace) -> tuple[str, dict[str, object]]:
    settings = load_settings()
    if args.command == "capabilities":
        return "capabilities", describe_capabilities(settings)
    if args.command == "import":
        cancelled = threading.Event()
        previous = {
            sig: signal.signal(sig, lambda *_args: cancelled.set())
            for sig in (signal.SIGTERM, signal.SIGINT)
        }
        try:
            with redirect_stdout(sys.stderr):
                result = import_agent_files(
                    settings,
                    args.paths,
                    allow_write=args.allow_write,
                    should_cancel=cancelled.is_set,
                )
            return "import", result
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    if args.command == "search":
        filters = LibraryFilters(
            topic=args.topic,
            subtopic=args.subtopic,
            page_type=args.page_type,
            deck_id=args.deck_id,
            source_format=args.source_format,
        )
        return (
            "search",
            search_library(
                settings,
                query=args.query,
                page=args.page,
                page_size=args.page_size,
                filters=filters,
                expected_revision=args.expected_revision,
            ),
        )
    if args.command == "slide" and args.slide_action == "get":
        return "slide.get", get_slide(settings, args.slide_id)
    if args.command == "compose" and args.compose_action == "preflight":
        return (
            "compose.preflight",
            preflight_agent_compose(settings, args.manifest, args.output),
        )
    if args.command == "compose" and args.compose_action == "run":
        return (
            "compose.run",
            run_agent_compose(
                settings,
                args.manifest,
                args.output,
                preflight_token=args.preflight_token,
                allow_write=args.allow_write,
                overwrite=args.overwrite,
            ),
        )
    raise UsageError("unsupported command")


def _emit_error(
    request_id: str,
    command: str,
    code: str,
    message: str,
    exit_code: ExitCode,
    *,
    retryable: bool = False,
    details: dict[str, object] | None = None,
) -> int:
    _emit(
        {
            "protocol": AGENT_PROTOCOL,
            "type": "response",
            "id": request_id,
            "command": command,
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            },
        }
    )
    return exit_code


def _app_error_exit_code(code: ErrorCode) -> ExitCode:
    if code == ErrorCode.REQUEST_INVALID:
        return ExitCode.INVALID_REQUEST
    if code == ErrorCode.NOT_FOUND:
        return ExitCode.NOT_FOUND
    if code in {
        ErrorCode.PATH_NOT_ALLOWED,
        ErrorCode.SOURCE_CHANGED,
        ErrorCode.INVALID_STATE_TRANSITION,
    }:
        return ExitCode.PRECONDITION_FAILED
    if code == ErrorCode.DATABASE_BUSY:
        return ExitCode.BUSY
    if code == ErrorCode.LIBREOFFICE_NOT_FOUND:
        return ExitCode.CAPABILITY_UNAVAILABLE
    if code == ErrorCode.IMPORT_INCOMPLETE:
        return ExitCode.IMPORT_INCOMPLETE
    return ExitCode.INTERNAL_ERROR


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def _validate_request_id(request_id: str) -> str:
    if not request_id or len(request_id) > 128 or not request_id.isprintable():
        raise UsageError("request-id must contain 1 to 128 printable characters")
    return request_id


def _request_id_from(argv: Sequence[str]) -> str:
    for index, value in enumerate(argv):
        if value.startswith("--request-id="):
            request_id = value.partition("=")[2]
            if request_id:
                return request_id
        if value == "--request-id" and index + 1 < len(argv) and argv[index + 1]:
            return argv[index + 1]
    return f"req_{uuid.uuid4().hex}"


def _command_from(argv: Sequence[str]) -> str:
    positional: list[str] = []
    skip_next = False
    for value in argv:
        if skip_next:
            skip_next = False
            continue
        if value == "--request-id":
            skip_next = True
            continue
        if value.startswith("-"):
            continue
        positional.append(value)
    if not positional:
        return "unknown"
    if positional[0] == "slide" and len(positional) > 1:
        return f"slide.{positional[1]}"
    if positional[0] == "compose" and len(positional) > 1:
        return f"compose.{positional[1]}"
    return positional[0]
