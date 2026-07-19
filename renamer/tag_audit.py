"""Filename-to-tag audit records used by both CLI and GUI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .media import read_media
from .regular_parser import format_title, parse_regular_stem
from .review_models import FileSnapshot, TagProposal, path_key, proposal_id
from .tag_writer import supports_tag_writing


def expected_tags_from_filename(
    path: str,
    current: dict[str, str],
) -> tuple[dict[str, str], str]:
    """Return canonical tag values represented by a supported filename."""
    if not supports_tag_writing(path):
        extension = Path(path).suffix.lower() or "this file type"
        raise ValueError(f"Tag writing is not supported for {extension} files")
    stem = Path(path).stem
    from .tag_writer import parse_stem

    parsed = parse_stem(stem)
    if parsed is not None and parsed["is_ocremix"]:
        expected = dict(current)
        expected.update(
            {
                "artist": parsed["game"],
                "title": parsed["title"],
                "album": parsed["game"],
                "album_artist": "OverClocked ReMix",
                "grouping": parsed["game"],
                "subtitle": ", ".join(parsed["remixers"]),
            }
        )
        return expected, "Filename provides the source-specific display fields."

    regular = parse_regular_stem(stem)
    if regular is not None:
        expected = dict(current)
        expected.update(
            {
                "artist": regular.artist,
                "title": format_title(regular),
            }
        )
        return expected, "Filename provides explicit artist, title, and feature metadata."
    if parsed is None:
        raise ValueError("Filename is not in a supported music naming format")
    expected = dict(current)
    expected.update({"artist": parsed["artist"], "title": parsed["full_title"]})
    return expected, "Filename provides explicit artist and title metadata."


def audit_tag_file(path: str) -> TagProposal | None:
    """Create a field-level proposal, or ``None`` when tags already match."""
    media = read_media(path)
    if not media.usable:
        raise OSError(f"{media.status}: {media.error or 'cannot read media'}")
    current = dict(media.tags)
    expected, reason = expected_tags_from_filename(path, current)
    relevant = set(current) | set(expected)
    before = {key: current.get(key, "") for key in sorted(relevant)}
    after = {key: expected.get(key, "") for key in sorted(relevant)}
    if before == after:
        return None

    snapshot = FileSnapshot.capture(path, tags=current, include_hash=True)
    digest = {
        "before": before,
        "after": after,
    }
    return TagProposal(
        id=proposal_id("tag", path, digest),
        decision_group_id=path_key(path),
        snapshot=snapshot,
        path=snapshot.path,
        before=before,
        after=after,
        confidence="high",
        reason=reason,
    )


def audit_tags_for_folder(
    folder_path: str,
    recursive: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
) -> tuple[list[TagProposal], list[dict]]:
    from .extractor import scan_folder

    proposals: list[TagProposal] = []
    issues: list[dict] = []
    paths = scan_folder(folder_path, recursive=recursive)
    for index, path in enumerate(paths, start=1):
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            proposal = audit_tag_file(path)
            if proposal is not None:
                proposals.append(proposal)
        except (OSError, ValueError) as exc:
            issues.append(
                {
                    "path": os.path.abspath(path),
                    "category": "tag-audit",
                    "message": str(exc),
                }
            )
        if progress:
            progress(index, len(paths), path)
    return proposals, issues


__all__ = [
    "audit_tag_file",
    "audit_tags_for_folder",
    "expected_tags_from_filename",
]
