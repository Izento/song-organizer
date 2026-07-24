"""Read-only review report command."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from cli.commands.shared import command_folders, online_key
from cli.output import Output
from renamer.review_api import analyze_folder


def run(args: Namespace, output: Output) -> int:
    folders = command_folders(args, output, include_lookup=True)
    if folders is None:
        return 2
    acoustid_key = online_key(args.fingerprint, output)
    failures = 0

    for folder in folders:
        if not Path(folder.path).is_dir():
            output.print(f"[yellow]Skipping missing folder:[/yellow] {folder.path}")
            failures += 1
            continue
        plan = analyze_folder(
            folder.path,
            strategy=folder.strategy,
            recursive=folder.recursive_or(True),
            lookup=args.lookup or folder.lookup,
            acoustid_key=acoustid_key,
        )
        output.print(
            f"[cyan]{folder.path}[/cyan]: "
            f"{len(plan.rename_proposals)} renames, "
            f"{len(plan.tag_proposals)} tag repairs, "
            f"{len(plan.duplicate_findings)} duplicate findings, "
            f"{len(plan.issues)} issues"
        )
    return 1 if failures == len(folders) else 0


__all__ = ["run"]
