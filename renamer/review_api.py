"""UI-neutral analysis APIs for CLI and tkinter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .extractor import extract_track, scan_folder
from .formatter import build_filename, split_feat
from .media import read_media
from .musicbrainz import enrich_track
from .universal_dedup import analyze_duplicates
from .review_models import (
    FileSnapshot,
    RenameProposal,
    ReviewPlan,
    canonical_path,
    path_key,
    proposal_id,
)
from .regular_parser import (
    normalize_text,
    normalize_title_text,
    parse_regular_filename,
    split_feature_names,
)
from .tag_audit import audit_tags_for_folder


ProgressCallback = Callable[[str, int, int, str], None]


def _emit(
    callback: ProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    path: str,
) -> None:
    if callback:
        callback(stage, current, total, path)


def _track_values(track) -> dict[str, str]:
    if track.is_ocremix:
        return {
            "artist": normalize_title_text(track.game),
            "title": normalize_title_text(track.title),
            "contributors": ", ".join(
                normalize_title_text(remixer) for remixer in track.remixers
            ),
        }
    return {
        "artist": track.artist,
        "title": normalize_title_text(track.title),
        "contributors": ", ".join(
            normalize_title_text(feature) for feature in track.feat_artists
        ),
    }


def _filename_identity_hint(path: str) -> tuple[str, str] | None:
    """Return a conservative identity hint from a filename.

    A filename can still provide useful evidence when its title is a
    placeholder such as ``Unknown Title``.  Track-number-style names are
    excluded because they are not independent identity evidence.
    """
    parsed = parse_regular_filename(Path(path).name)
    if parsed is not None:
        return parsed.artist, parsed.title

    stem = Path(path).stem.replace("_", " ")
    if " - " not in stem:
        return None
    artist, title = (part.strip() for part in stem.split(" - ", 1))
    artist_key = normalize_text(artist)
    if (
        not artist
        or not title
        or artist_key in {"track", "unknown artist", "various artists", "va"}
        or artist_key.isdigit()
    ):
        return None
    return artist, title


def _tag_filename_conflict(path: str, track) -> str | None:
    if track.strategy != "tag_based":
        return None
    hint = _filename_identity_hint(path)
    if hint is None or not track.artist or not track.title:
        return None
    filename_artist, filename_title = hint
    if (
        normalize_text(filename_artist) == normalize_text(track.artist)
        and normalize_text(filename_title) == normalize_text(track.title)
    ):
        return None
    return (
        "Filename identity "
        f"{filename_artist!r} - {filename_title!r} conflicts with embedded "
        f"tags {track.artist!r} - {track.title!r}; automatic rename blocked."
    )


def _proposal_identity(values: dict[str, str]) -> tuple[str, str, tuple[str, ...]]:
    """Return a comparable artist/title/features identity for review proposals."""
    title, title_features = split_feat(values.get("title", ""))
    features = list(title_features)
    for key in ("contributors", "subtitle"):
        features.extend(split_feature_names(values.get(key, "")))
    return (
        normalize_text(values.get("artist", "")),
        normalize_text(title),
        tuple(sorted(normalize_text(feature) for feature in features)),
    )


def plan_renames(
    folder_path: str,
    strategy: str | None = None,
    recursive: bool = True,
    lookup: bool = False,
    acoustid_key: str | None = None,
    progress: ProgressCallback | None = None,
    cancel_event=None,
) -> tuple[list[RenameProposal], list[dict]]:
    """Analyze a folder and return typed rename proposals without mutation."""
    paths = scan_folder(folder_path, recursive=recursive)
    proposals: list[RenameProposal] = []
    issues: list[dict] = []
    destinations: dict[str, list[int]] = {}

    for index, path in enumerate(paths, start=1):
        if cancel_event is not None and cancel_event.is_set():
            break
        _emit(progress, "extract", index, len(paths), path)
        try:
            track = extract_track(
                path,
                strategy=strategy,
                acoustid_key=acoustid_key,
            )
            online_conflict = False
            conflict = _tag_filename_conflict(path, track)
            if conflict:
                if acoustid_key:
                    identified = extract_track(
                        path,
                        strategy=strategy,
                        acoustid_key=acoustid_key,
                        prefer_acoustid=True,
                    )
                    if identified.strategy == "acoustid":
                        track = identified
                        online_conflict = True
                        conflict = None
                if conflict:
                    issues.append(
                        {
                            "path": canonical_path(path),
                            "category": "identity-conflict",
                            "message": conflict,
                        }
                    )
                    continue
            if lookup and track.needs_lookup:
                enrich_track(track)
            if track.skip_reason:
                issues.append(
                    {
                        "path": canonical_path(path),
                        "category": "rename",
                        "message": track.skip_reason,
                    }
                )
                continue
            if not any((track.artist, track.title, track.game)):
                issues.append(
                    {
                        "path": canonical_path(path),
                        "category": "rename",
                        "message": "No extractable identity",
                    }
                )
                continue

            media = read_media(path)
            snapshot = FileSnapshot.capture(path, tags=media.tags, include_hash=True)
            new_name = build_filename(track)
            new_path = os.path.join(os.path.dirname(path), new_name)
            current_name = os.path.basename(path)
            if current_name == new_name:
                continue
            values = _track_values(track)
            confidence = {
                "tag_based": "high",
                "filename_norm": "medium",
                "acoustid": "medium",
                "musicbrainz": "medium",
            }.get(track.strategy, "low")
            warnings = []
            if track.strategy in {"acoustid", "musicbrainz"}:
                warnings.append(f"Identity came from {track.strategy}.")
            if track.strategy == "acoustid" and track.acoustid_score is not None:
                warnings.append(f"Audio match score: {track.acoustid_score:.3f}.")
            if online_conflict:
                warnings.append(
                    "Embedded tags conflicted with the filename and were not used."
                )
            reason = f"Normalized using {track.strategy or 'automatic'} evidence."
            if track.strategy == "acoustid" and track.acoustid_recording_id:
                reason += (
                    f" AcoustID recording {track.acoustid_recording_id} "
                    "was retained as evidence."
                )
            item = RenameProposal(
                id=proposal_id("rename", path, new_path),
                decision_group_id=path_key(path),
                snapshot=snapshot,
                old_path=snapshot.path,
                new_path=canonical_path(new_path),
                current_values={"filename": current_name, **values},
                proposed_values={"filename": new_name, **values},
                confidence=confidence,
                reason=reason,
                warnings=tuple(warnings),
            )
            destinations.setdefault(path_key(new_path), []).append(len(proposals))
            proposals.append(item)
        except (OSError, ValueError) as exc:
            issues.append(
                {
                    "path": canonical_path(path),
                    "category": "rename",
                    "message": str(exc),
                }
            )

    for indexes in destinations.values():
        if len(indexes) < 2:
            continue
        for index in indexes:
            item = proposals[index]
            warnings = tuple(item.warnings) + (
                "Destination collides with another proposal.",
            )
            proposals[index] = RenameProposal(
                **{
                    **item.to_dict(),
                    "snapshot": item.snapshot,
                    "warnings": warnings,
                }
            )
    return proposals, issues


def plan_tag_updates(
    folder_path: str,
    recursive: bool = True,
    progress: ProgressCallback | None = None,
    cancel_event=None,
):
    return audit_tags_for_folder(
        folder_path,
        recursive=recursive,
        progress=(
            (lambda current, total, path: _emit(
                progress, "tag-audit", current, total, path
            ))
            if progress
            else None
        ),
        cancel_event=cancel_event,
    )


def analyze_folder(
    folder_path: str,
    strategy: str | None = None,
    recursive: bool = True,
    lookup: bool = False,
    acoustid_key: str | None = None,
    include_duplicates: bool = True,
    fingerprint: bool = False,
    progress: ProgressCallback | None = None,
    cancel_event=None,
) -> ReviewPlan:
    """Build one immutable review plan for the selected root."""
    rename_proposals, rename_issues = plan_renames(
        folder_path,
        strategy=strategy,
        recursive=recursive,
        lookup=lookup,
        acoustid_key=acoustid_key,
        progress=progress,
        cancel_event=cancel_event,
    )
    tag_proposals, tag_issues = plan_tag_updates(
        folder_path,
        recursive=recursive,
        progress=progress,
        cancel_event=cancel_event,
    )
    tag_by_group = {item.decision_group_id: item for item in tag_proposals}
    for index, rename in enumerate(rename_proposals):
        tag = tag_by_group.get(rename.decision_group_id)
        if tag is None:
            continue
        if _proposal_identity(rename.proposed_values) == _proposal_identity(
            tag.after
        ):
            continue
        rename_proposals[index] = RenameProposal(
            **{
                **rename.to_dict(),
                "snapshot": rename.snapshot,
                "warnings": tuple(rename.warnings)
                + ("Conflicts with filename-derived tag repair.",),
            }
        )
        tag_proposals[tag_proposals.index(tag)] = type(tag)(
            **{
                **tag.to_dict(),
                "snapshot": tag.snapshot,
                "warnings": tuple(tag.warnings)
                + ("Conflicts with tag-derived rename.",),
            }
        )
    duplicate_findings = []
    duplicate_issues = []
    if include_duplicates and not (
        cancel_event is not None and cancel_event.is_set()
    ):
        try:
            duplicate_findings = analyze_duplicates(
                folder_path,
                recursive=recursive,
                progress=(
                    lambda current, total, path: _emit(
                        progress, "duplicate-audit", current, total, path
                    )
                    if progress
                    else None
                ),
                cancel_event=cancel_event,
                fingerprint=fingerprint,
            )
        except (OSError, ValueError) as exc:
            duplicate_issues.append(
                {
                    "path": canonical_path(folder_path),
                    "category": "duplicate-audit",
                    "message": str(exc),
                }
            )
    return ReviewPlan.create(
        root=folder_path,
        recursive=recursive,
        rename_proposals=rename_proposals,
        tag_proposals=tag_proposals,
        duplicate_findings=duplicate_findings,
        issues=rename_issues + tag_issues + duplicate_issues,
    )


__all__ = ["analyze_folder", "plan_renames", "plan_tag_updates"]
