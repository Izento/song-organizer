"""Read-only universal duplicate audit command."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from cli.commands.shared import command_folders
from cli.output import Output
from renamer.universal_dedup import dedup_folder


def run(args: Namespace, output: Output) -> int:
    folders = command_folders(args, output)
    if folders is None:
        return 2
    totals = {
        "groups": 0,
        "auto_safe_groups": 0,
        "review_groups": 0,
        "unsafe_groups": 0,
        "errors": 0,
    }
    valid_folders = 0

    for folder in folders:
        path = Path(folder.path)
        if not path.is_dir():
            output.print(f"[yellow]Skipping missing folder:[/yellow] {path}")
            totals["errors"] += 1
            continue
        valid_folders += 1
        summary = dedup_folder(
            folder_path=str(path),
            dry_run=True,
            recursive=folder.recursive_or(False),
        )
        for key in totals:
            totals[key] += summary.get(key, 0)
        for finding in summary.get("findings", [])[:10]:
            output.print(
                f"  [{finding.classification}] "
                f"{finding.paths[0]} ({len(finding.paths)} files)"
            )

    output.print(
        f"\nAudit — [cyan]{totals['groups']}[/cyan] duplicate groups: "
        f"[green]{totals['auto_safe_groups']}[/green] exact-content, "
        f"[yellow]{totals['review_groups']}[/yellow] review, "
        f"[red]{totals['unsafe_groups']}[/red] keep-both. "
        "No files were deleted."
    )
    return 1 if valid_folders == 0 or totals["errors"] else 0


__all__ = ["run"]
