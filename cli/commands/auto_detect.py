"""Strategy recommendation command."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from cli.output import Output
from renamer.extractor import extract_track, scan_folder
from renamer.strategy import StrategySample, infer_strategy


def run(args: Namespace, output: Output) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        output.print(f"[red]Folder not found:[/red] {folder}")
        return 2

    paths = scan_folder(str(folder), recursive=False)[:20]
    if not paths:
        output.print(f"No audio files found in: {folder}")
        return 1

    samples = [
        StrategySample(
            filename=Path(path).stem,
            extraction_strategy=extract_track(path).strategy or "",
        )
        for path in paths
    ]
    recommendation = infer_strategy(samples)

    output.print(f"Sampling {recommendation.sample_size} files from:\n  {folder}")
    output.print("\nStrategy detection:")
    for name, count in recommendation.counts.items():
        output.print(f"  {name}: {count}/{recommendation.sample_size} files")
    output.print(
        f"\nRecommended strategy: {recommendation.strategy or 'auto'}\n"
        f"Note: {recommendation.note}"
    )
    output.print("\nSuggested config.yaml entry:")
    output.print(f'  - path: "{folder}"')
    if recommendation.strategy:
        output.print(f"    strategy: {recommendation.strategy}")
    if recommendation.strategy == "musicbrainz":
        output.print("    lookup: true")
    output.print("    recursive: false")
    return 0


__all__ = ["run"]
