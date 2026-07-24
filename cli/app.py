"""Bootstrap and dispatch for Ballad's command-line interface."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from cli.output import ConsoleOutput, Output
from cli.parser import parse_args
from renamer.runtime import ensure_app_dirs, resource_path


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _load_local_environment() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(resource_path(".env"))


def _run_gui() -> int:
    from gui.app import run

    run()
    return 0


def _dispatch(command: str, args, output: Output) -> int:
    if command == "gui":
        return _run_gui()
    if command == "rename":
        from cli.commands.rename import run
    elif command == "audit":
        from cli.commands.audit import run
    elif command == "tags":
        from cli.commands.tags import run
    elif command == "dedup":
        from cli.commands.dedup import run
    elif command == "auto-detect":
        from cli.commands.auto_detect import run
    elif command == "undo":
        from cli.commands.undo import run
    else:
        raise ValueError(f"Unknown command: {command}")
    return run(args, output)


def main(
    argv: Sequence[str] | None = None,
    *,
    output: Output | None = None,
) -> int:
    _configure_utf8_console()
    _load_local_environment()
    ensure_app_dirs()
    args = parse_args(argv)
    if args.command is None:
        return _run_gui()
    return _dispatch(args.command, args, output or ConsoleOutput())


__all__ = ["main"]
