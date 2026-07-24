"""Undo the latest recoverable apply batch."""

from __future__ import annotations

from argparse import Namespace

from cli.output import Output
from renamer.apply import latest_undoable_batch, undo_batch


def run(_args: Namespace, output: Output) -> int:
    batch = latest_undoable_batch()
    if batch is None:
        output.print("No recoverable batch is available.")
        return 1
    results = undo_batch(batch["batch_id"])
    succeeded = sum(result.status == "succeeded" for result in results)
    failed = sum(result.status == "failed" for result in results)
    output.print(f"Undo complete: {succeeded} restored, {failed} failed.")
    return 1 if failed else 0


__all__ = ["run"]
