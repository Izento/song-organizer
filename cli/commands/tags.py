"""Filename-derived tag review and apply command."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from cli.commands.shared import command_folders
from cli.output import Output
from renamer.apply import apply_review_plan
from renamer.extractor import scan_folder
from renamer.review_api import plan_tag_updates
from renamer.review_models import ReviewPlan


def run(args: Namespace, output: Output) -> int:
    folders = command_folders(args, output)
    if folders is None:
        return 2

    proposed = 0
    succeeded = 0
    problems = 0
    total_files = 0
    valid_folders = 0

    for folder in folders:
        path = Path(folder.path)
        if not path.is_dir():
            output.print(f"[yellow]Skipping missing folder:[/yellow] {path}")
            problems += 1
            continue
        valid_folders += 1
        recursive = folder.recursive_or(True)
        file_count = len(scan_folder(str(path), recursive=recursive))
        proposals, issues = plan_tag_updates(str(path), recursive=recursive)
        total_files += file_count
        proposed += len(proposals)
        problems += len(issues)
        results = []
        if args.apply and proposals:
            review = ReviewPlan.create(
                root=str(path),
                recursive=recursive,
                tag_proposals=proposals,
                issues=issues,
            )
            results = apply_review_plan(review, [item.id for item in proposals])
            succeeded += sum(result.status == "succeeded" for result in results)
            problems += sum(result.status in {"blocked", "failed"} for result in results)

        count = (
            sum(result.status == "succeeded" for result in results)
            if args.apply
            else len(proposals)
        )
        action = "Updated" if args.apply else "Would update"
        output.print(
            f"\n[bold cyan]{path}[/bold cyan]\n"
            f"  {action}: [green]{count}[/green] / {file_count} files; "
            f"issues: [red]{len(issues)}[/red]"
        )
        for item in proposals[:4]:
            output.print(
                f"  [dim]{item.path}[/dim] "
                f'{item.before.get("title", "")} → {item.after.get("title", "")}'
            )
        for issue in issues[:3]:
            output.print(f'  [red]ERROR[/red] {issue["path"]}: {issue["message"]}')

    action = "Updated" if args.apply else "Would update"
    changed = succeeded if args.apply else proposed
    output.print(
        f"\n{action} tags for [green]{changed}[/green] of {total_files} files. "
        f"Problems: {problems}."
    )
    return 1 if valid_folders == 0 or problems else 0


__all__ = ["run"]
