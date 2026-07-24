"""Argument definitions for Ballad's command-line interface."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def _add_folder_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--folder",
        metavar="PATH",
        help="Process one folder instead of the local config.",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Use a specific config.yaml instead of the per-user default.",
    )


def _add_online_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--lookup",
        action="store_true",
        help="Enable MusicBrainz enrichment where needed.",
    )
    parser.add_argument(
        "--fingerprint",
        action="store_true",
        help="Use AcoustID identification when a private key is configured.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ballad",
        description="Review-first music library organizer.",
    )
    commands = parser.add_subparsers(dest="command", title="commands")

    commands.add_parser("gui", help="Open the desktop application.")

    rename = commands.add_parser("rename", help="Review or apply filename repairs.")
    _add_folder_options(rename)
    _add_online_options(rename)
    rename.add_argument(
        "--strategy",
        choices=("regular", "filename_norm", "musicbrainz"),
        help="Force an extraction strategy for an explicit folder.",
    )
    rename.add_argument(
        "--apply",
        action="store_true",
        help="Apply reviewed-safe renames instead of previewing them.",
    )
    rename.add_argument(
        "--interactive",
        action="store_true",
        help="Confirm each proposed rename.",
    )

    audit = commands.add_parser("audit", help="Build a read-only review report.")
    _add_folder_options(audit)
    _add_online_options(audit)

    tags = commands.add_parser("tags", help="Review or apply filename-derived tags.")
    _add_folder_options(tags)
    tags.add_argument(
        "--apply",
        action="store_true",
        help="Apply reviewed-safe tag repairs instead of previewing them.",
    )

    dedup = commands.add_parser("dedup", help="Audit duplicate candidates.")
    _add_folder_options(dedup)

    auto_detect = commands.add_parser(
        "auto-detect",
        help="Recommend an extraction strategy for a folder.",
    )
    auto_detect.add_argument("--folder", required=True, metavar="PATH")

    commands.add_parser("undo", help="Undo the latest recoverable batch.")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


__all__ = ["build_parser", "parse_args"]
