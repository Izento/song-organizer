"""Shared command-layer resolution without business logic."""

from __future__ import annotations

from argparse import Namespace

from cli.config import FolderConfig, resolve_folders
from cli.output import Output
from renamer.runtime import resolve_acoustid_key, resolve_fpcalc


def command_folders(
    args: Namespace,
    output: Output,
    *,
    include_strategy: bool = False,
    include_lookup: bool = False,
) -> list[FolderConfig] | None:
    try:
        folders = resolve_folders(
            args.folder,
            args.config,
            strategy=args.strategy if include_strategy else None,
            lookup=args.lookup if include_lookup else False,
        )
    except ValueError as exc:
        output.print(f"[red]Configuration error:[/red] {exc}")
        return None
    if not folders:
        output.print("[red]No folders configured.[/red] Use --folder or config.yaml.")
        return None
    return folders


def online_key(enabled: bool, output: Output) -> str | None:
    if not enabled:
        return None
    key = resolve_acoustid_key()
    if not key:
        output.print(
            "[yellow]AcoustID key is not configured; "
            "continuing without online identification.[/yellow]"
        )
        return None
    if resolve_fpcalc() is None:
        output.print(
            "[yellow]fpcalc is unavailable; online identification "
            "will fall back to local metadata.[/yellow]"
        )
    return key


__all__ = ["command_folders", "online_key"]
